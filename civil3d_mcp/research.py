"""
Sản phẩm phục vụ báo cáo nghiên cứu
====================================
Các hàm ở đây gộp nhiều lời gọi Civil 3D thành một số liệu hoặc một file có thể
đưa thẳng vào báo cáo: bảng sai lệch cao độ, khối lượng đào đắp, trắc dọc, trắc
ngang, và bản ghi môi trường phần mềm.

Nguyên tắc chung: mỗi con số đi kèm điều kiện sinh ra nó (bước lưới, vùng loại
biên, số điểm rơi ngoài phạm vi). Một RMSE không kèm cỡ mẫu và phạm vi lấy mẫu
thì không kiểm chứng lại được, nên không dùng được trong báo cáo.
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Dict, List, Optional, Sequence

from . import alignments as al_mod
from . import corridors as cor_mod
from . import surfaces as surf_mod
from .client import Civil3DClient
from .com import C3DError, discover_install_paths, file_version, reraise_if_transient
from .geometry import deviation_stats, grid_points, point_in_polygon, read_xyz, write_csv


def environment_report(client: Civil3DClient) -> Dict[str, object]:
    """Bản ghi môi trường phần mềm - phần bắt buộc của mục thực nghiệm.

    Kết quả thực nghiệm chỉ tái lập được khi biết chính xác phiên bản đã chạy, nên
    số hiệu lấy từ registry và từ chính file .exe, không phải từ trí nhớ.
    """
    status = client.get_status()
    installs = discover_install_paths()
    for item in installs:
        exe = os.path.join(item.get("location", ""), "acad.exe")
        item["executable_version"] = file_version(exe)
    return {
        "captured_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "civil3d_installations": installs,
        "com_versions": status.get("com_versions_installed"),
        "com_version_active": status.get("com_version_active"),
        "autocad_version": status.get("autocad_version"),
        "application_caption": status.get("application"),
        "connected": status.get("ok"),
    }


# --------------------------------------------------------------------------
# Nạp đám mây điểm vào bề mặt
# --------------------------------------------------------------------------

def surface_from_point_file(client: Civil3DClient, surface_name: str, xyz_path: str,
                            thin_every: int = 1, max_points: Optional[int] = None,
                            style: Optional[str] = None,
                            max_triangle_length: Optional[float] = None) -> Dict[str, object]:
    """Dựng TIN surface từ file điểm XYZ/CSV (đầu ra của bước lọc đám mây điểm).

    `thin_every` giữ lại mỗi điểm thứ n - cách giảm mật độ rẻ và có thể mô tả lại
    trong báo cáo. Nó KHÔNG phải phép giảm mẫu theo lưới (voxel): lấy mẫu đều theo
    thứ tự file giữ nguyên phân bố không gian chỉ khi file chưa được sắp xếp lại;
    nếu file đã sắp theo toạ độ thì cách này sẽ bỏ sót cả vùng, hãy giảm mẫu bằng
    công cụ point cloud trước rồi mới đưa vào đây.
    """
    if thin_every < 1:
        raise C3DError("thin_every phải >= 1.")
    all_points = read_xyz(xyz_path, max_points=None)
    if not all_points:
        raise C3DError(f"Không đọc được điểm nào từ {xyz_path}.")
    points = all_points[::thin_every]
    if max_points and len(points) > max_points:
        points = points[:max_points]

    created = surf_mod.create_tin_surface(client, surface_name, style=style,
                                          description=f"Dựng từ {os.path.basename(xyz_path)}")
    added = surf_mod.add_points(client, surface_name, points)
    result: Dict[str, object] = {
        "surface": surface_name,
        "source_file": os.path.abspath(xyz_path),
        "points_in_file": len(all_points),
        "thin_every": thin_every,
        "points_sent": len(points),
        "creation": created,
        "point_load": added,
    }
    if max_triangle_length is not None:
        result["build_options"] = surf_mod.set_build_options(
            client, surface_name, max_triangle_length=max_triangle_length)
    result["statistics"] = surf_mod.surface_info(client, surface_name)["statistics"]
    result["verified"] = bool(added.get("verified"))
    return result


# --------------------------------------------------------------------------
# So sánh hai bề mặt trên lưới
# --------------------------------------------------------------------------

def surface_deviation_grid(client: Civil3DClient, surface_a: str, surface_b: str,
                           spacing: float = 1.0, edge_inset: float = 0.0,
                           boundary: Optional[Sequence[Sequence[float]]] = None,
                           tolerances: Sequence[float] = (0.02, 0.05, 0.10),
                           max_samples: int = 20000) -> Dict[str, object]:
    """Chênh cao độ giữa hai bề mặt, lấy mẫu trên lưới đều trong vùng chồng lấn.

    dz = z(A) - z(B). Chỉ những nút lưới có cao độ trên CẢ HAI bề mặt mới vào
    thống kê; số nút rơi ra ngoài được đếm riêng vì đó là thước đo độ phủ, và trộn
    chúng vào sẽ làm sai lệch trông nhỏ đi một cách giả tạo.

    `edge_inset` co vùng lấy mẫu vào trong: mép TIN là nơi nội suy kém tin cậy
    nhất, để nguyên sẽ kéo lệch RMSE bằng những tam giác dài ở biên.
    """
    a = surf_mod.get_surface(client, surface_a)
    b = surf_mod.get_surface(client, surface_b)
    sa = surf_mod._statistics(a)
    sb = surf_mod._statistics(b)
    for name, st in ((surface_a, sa), (surface_b, sb)):
        if "min_x" not in st:
            raise C3DError(f"Không đọc được hộp bao của bề mặt {name!r} - bề mặt có thể rỗng.")

    overlap = {
        "min_x": max(sa["min_x"], sb["min_x"]),
        "min_y": max(sa["min_y"], sb["min_y"]),
        "max_x": min(sa["max_x"], sb["max_x"]),
        "max_y": min(sa["max_y"], sb["max_y"]),
    }
    if overlap["max_x"] <= overlap["min_x"] or overlap["max_y"] <= overlap["min_y"]:
        raise C3DError(
            f"Hai bề mặt không chồng lấn nhau trên mặt bằng. {surface_a!r}: "
            f"X[{sa['min_x']:.3f}, {sa['max_x']:.3f}] Y[{sa['min_y']:.3f}, {sa['max_y']:.3f}]; "
            f"{surface_b!r}: X[{sb['min_x']:.3f}, {sb['max_x']:.3f}] "
            f"Y[{sb['min_y']:.3f}, {sb['max_y']:.3f}]. Thường là do hai bề mặt ở hai hệ "
            "toạ độ khác nhau chứ không phải do dữ liệu sai."
        )

    nodes = grid_points(overlap, float(spacing), inset=float(edge_inset))
    if boundary:
        nodes = [p for p in nodes if point_in_polygon(p[0], p[1], boundary)]
    truncated = False
    if len(nodes) > max_samples:
        step = len(nodes) // max_samples + 1
        nodes = nodes[::step]
        truncated = True

    deltas: List[float] = []
    rows: List[Dict[str, object]] = []
    missing_a = missing_b = 0
    for x, y in nodes:
        za = _elev(a, x, y)
        zb = _elev(b, x, y)
        if za is None:
            missing_a += 1
        if zb is None:
            missing_b += 1
        dz = round(za - zb, 6) if (za is not None and zb is not None) else None
        if dz is not None:
            deltas.append(dz)
        rows.append({"x": round(x, 4), "y": round(y, 4),
                     "z_a": za, "z_b": zb, "dz": dz})

    return {
        "surface_a": surface_a,
        "surface_b": surface_b,
        "convention": f"dz = cao độ {surface_a!r} - cao độ {surface_b!r}",
        "overlap_bounds": {k: round(v, 4) for k, v in overlap.items()},
        "grid_spacing": float(spacing),
        "edge_inset": float(edge_inset),
        "boundary_applied": bool(boundary),
        "nodes_sampled": len(nodes),
        "nodes_compared": len(deltas),
        "nodes_outside_a": missing_a,
        "nodes_outside_b": missing_b,
        "grid_truncated": truncated,
        "statistics": deviation_stats(deltas, tolerances),
        "rows": rows,
    }


def _elev(surface, x: float, y: float) -> Optional[float]:
    try:
        return round(float(surface.FindElevationAtXY(x, y)), 6)
    except Exception as exc:
        reraise_if_transient(exc)
        return None


# --------------------------------------------------------------------------
# Xuất file
# --------------------------------------------------------------------------

def export_surface_comparison(client: Civil3DClient, surface_a: str, surface_b: str,
                              out_dir: str, spacing: float = 1.0,
                              edge_inset: float = 0.0,
                              volume_surface_name: Optional[str] = None,
                              tolerances: Sequence[float] = (0.02, 0.05, 0.10)
                              ) -> Dict[str, object]:
    """Bộ sản phẩm đầy đủ khi đối chiếu hoàn công với thiết kế.

    Ghi ra ba file: CSV lưới sai lệch, CSV thống kê, và một báo cáo Markdown ghi
    đủ điều kiện sinh số liệu. Nếu `volume_surface_name` được truyền, tạo thêm một
    TIN volume surface để lấy khối lượng đào đắp do chính Civil 3D tính - một phép
    đo độc lập với lưới lấy mẫu, dùng để đối chứng.
    """
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    grid = surface_deviation_grid(client, surface_a, surface_b, spacing=spacing,
                                  edge_inset=edge_inset, tolerances=tolerances)

    grid_csv = write_csv(
        os.path.join(out_dir, "sai_lech_luoi.csv"),
        ["x", "y", f"z_{surface_a}", f"z_{surface_b}", "dz"],
        ([r["x"], r["y"], r["z_a"], r["z_b"], r["dz"]] for r in grid["rows"]),
    )
    stats = grid["statistics"]
    stats_csv = write_csv(
        os.path.join(out_dir, "thong_ke_sai_lech.csv"),
        ["chi_tieu", "gia_tri"],
        ([k, v] for k, v in stats.items() if not isinstance(v, dict)),
    )

    volumes: Optional[Dict[str, object]] = None
    if volume_surface_name:
        volumes = surf_mod.create_volume_surface(client, volume_surface_name,
                                                 base_surface=surface_b,
                                                 comparison_surface=surface_a)

    report_path = os.path.join(out_dir, "BAO_CAO_SO_SANH.md")
    _write_comparison_report(report_path, client, grid, volumes, grid_csv, stats_csv)

    return {
        "out_dir": out_dir,
        "grid_csv": grid_csv,
        "stats_csv": stats_csv,
        "report": {"path": report_path, "bytes": os.path.getsize(report_path)},
        "statistics": stats,
        "volumes": volumes,
        "nodes_compared": grid["nodes_compared"],
        "verified": grid["nodes_compared"] > 0 and os.path.exists(report_path),
        "verified_by": "đếm số nút so sánh được và kiểm tra file báo cáo tồn tại trên đĩa",
    }


def _write_comparison_report(path: str, client: Civil3DClient, grid: Dict[str, object],
                             volumes: Optional[Dict[str, object]],
                             grid_csv: Dict[str, object],
                             stats_csv: Dict[str, object]) -> None:
    env = environment_report(client)
    stats = grid["statistics"]
    lines: List[str] = []
    lines.append(f"# Đối chiếu bề mặt {grid['surface_a']} với {grid['surface_b']}")
    lines.append("")
    lines.append(f"Lập lúc: {env['captured_at']}")
    lines.append("")
    lines.append("## Điều kiện lấy mẫu")
    lines.append("")
    lines.append(f"- Quy ước: {grid['convention']}")
    lines.append(f"- Bước lưới: {grid['grid_spacing']}")
    lines.append(f"- Co biên: {grid['edge_inset']}")
    ob = grid["overlap_bounds"]
    lines.append(f"- Vùng chồng lấn: X[{ob['min_x']}, {ob['max_x']}] Y[{ob['min_y']}, {ob['max_y']}]")
    lines.append(f"- Số nút lấy mẫu: {grid['nodes_sampled']}")
    lines.append(f"- Số nút so sánh được: {grid['nodes_compared']}")
    lines.append(f"- Nút ngoài phạm vi {grid['surface_a']}: {grid['nodes_outside_a']}")
    lines.append(f"- Nút ngoài phạm vi {grid['surface_b']}: {grid['nodes_outside_b']}")
    if grid.get("grid_truncated"):
        lines.append("- Lưới đã bị rút bớt để không vượt trần số mẫu; mật độ thực tế "
                     "thưa hơn bước lưới khai báo.")
    lines.append("")
    lines.append("## Thống kê sai lệch cao độ")
    lines.append("")
    lines.append("| Chỉ tiêu | Giá trị |")
    lines.append("|---|---|")
    for key in ("count", "mean", "std_dev", "rmse", "mae", "min", "max", "median",
                "p90_abs", "p95_abs"):
        if key in stats:
            lines.append(f"| {key} | {stats[key]} |")
    within = stats.get("within_tolerance")
    if isinstance(within, dict):
        lines.append("")
        lines.append("| Dung sai | Số nút đạt | Tỉ lệ (%) |")
        lines.append("|---|---|---|")
        for tol, info in within.items():
            lines.append(f"| {tol} | {info['count']} | {info['percent']} |")
    if volumes:
        lines.append("")
        lines.append("## Khối lượng do Civil 3D tính (volume surface)")
        lines.append("")
        v = volumes.get("volumes") or {}
        for key in ("cut_volume", "fill_volume", "net_volume"):
            if key in v:
                lines.append(f"- {key}: {v[key]}")
        lines.append(f"- Quy ước dấu: {volumes.get('sign_convention')}")
        lines.append("")
        lines.append("Khối lượng này do Civil 3D tính trên TIN, độc lập với lưới lấy mẫu "
                     "ở trên; hai kết quả không bắt buộc trùng nhau và chênh lệch giữa "
                     "chúng chính là ảnh hưởng của bước lưới.")
    lines.append("")
    lines.append("## Môi trường phần mềm")
    lines.append("")
    for item in env.get("civil3d_installations") or []:
        lines.append(f"- {item.get('product')} - {item.get('location')} "
                     f"(acad.exe {item.get('executable_version')})")
    lines.append(f"- Phiên bản COM đang dùng: {env.get('com_version_active')}")
    lines.append("")
    lines.append("## File số liệu")
    lines.append("")
    lines.append(f"- Lưới sai lệch: `{grid_csv['path']}` ({grid_csv['rows_written']} dòng)")
    lines.append(f"- Thống kê: `{stats_csv['path']}`")
    lines.append("")
    lines.append("Số liệu trong báo cáo này do máy sinh ra từ mô hình đang mở tại thời "
                 "điểm ghi. Trước khi dùng làm kết quả công bố, cần đối chiếu lại phạm vi "
                 "bề mặt và nguồn gốc dữ liệu đầu vào của cả hai bề mặt.")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def export_profile_csv(client: Civil3DClient, alignment: str, profile: str,
                       out_path: str, interval: float = 5.0) -> Dict[str, object]:
    """Xuất trắc dọc ra CSV: lý trình, cao độ, độ dốc."""
    data = al_mod.sample_profile(client, alignment, profile, interval)
    written = write_csv(
        out_path,
        ["ly_trinh", "cao_do", "do_doc"],
        ([r["station"], r["elevation"], r["grade"]] for r in data["rows"]),
    )
    return {
        "alignment": alignment,
        "profile": profile,
        "interval": float(interval),
        "file": written,
        "rows_with_elevation": data["rows_with_elevation"],
        "verified": written["rows_written"] > 0,
        "verified_by": "đếm số dòng đã ghi và kích thước file trên đĩa",
    }


def export_sections_csv(client: Civil3DClient, alignment: str, group: str,
                        out_path: str, offset_interval: float = 1.0,
                        max_sample_lines: Optional[int] = None) -> Dict[str, object]:
    """Xuất trắc ngang ra CSV: lý trình, bề mặt, offset, cao độ."""
    data = cor_mod.read_sections(client, alignment, group,
                                 offset_interval=offset_interval,
                                 max_sample_lines=max_sample_lines)
    written = write_csv(
        out_path,
        ["ly_trinh", "be_mat", "offset", "cao_do"],
        ([r["station"], r["surface"], r["offset"], r["elevation"]] for r in data["rows"]),
    )
    # Nhóm sample line lấy mẫu N bề mặt thì CSV phải có đủ N tên bề mặt. Đếm số dòng
    # KHÔNG phát hiện được việc mất trọn một bề mặt: file vẫn đầy dòng và vẫn đúng
    # định dạng, chỉ thiếu một nửa nội dung. Vì vậy phép kiểm chứng ở đây đối chiếu
    # danh sách bề mặt có dữ liệu với danh sách bề mặt mà nhóm khai báo lấy mẫu.
    sampled = cor_mod.sampled_surface_names(client, alignment, group)
    with_data = list(data.get("surfaces_with_data") or [])
    missing = [n for n in sampled if n not in with_data]
    return {
        "alignment": alignment,
        "group": group,
        "sample_lines_read": data["sample_lines_read"],
        "surfaces_sampled_by_group": sampled,
        "surfaces_with_data": with_data,
        "surfaces_missing_from_export": missing,
        "rows_by_surface": data.get("rows_by_surface"),
        "sections_skipped": data.get("sections_skipped"),
        "sections_skipped_by_surface": data.get("sections_skipped_by_surface"),
        "file": written,
        "verified": written["rows_written"] > 0 and not missing,
        "verified_by": ("đếm số dòng đã ghi VÀ đối chiếu danh sách bề mặt có dữ liệu "
                        "với danh sách bề mặt nhóm khai báo lấy mẫu"),
    }


def export_corridor_points_csv(client: Civil3DClient, corridor: str, out_path: str,
                               interval: Optional[float] = None,
                               baseline: int = 0, region: int = 0) -> Dict[str, object]:
    """Xuất hình học corridor đã tính ra CSV: lý trình, offset, cao độ, toạ độ, mã."""
    data = cor_mod.corridor_points(client, corridor, baseline, region, interval)
    written = write_csv(
        out_path,
        ["ly_trinh", "offset", "cao_do", "cao_do_so_voi_tim", "x", "y", "ma"],
        ([r["station"], r["offset"], r["elevation"], r["elevation_to_baseline"],
          r.get("x"), r.get("y"), "|".join(r["codes"])] for r in data["points"]),
    )
    return {
        "corridor": corridor,
        "stations_total": data["stations_total"],
        "stations_read": data["stations_read"],
        "point_count": data["point_count"],
        "elevation_is_absolute": data["elevation_is_absolute"],
        "file": written,
        "verified": written["rows_written"] > 0,
        "verified_by": "đếm số dòng đã ghi và kích thước file trên đĩa",
    }


def export_corridor_quantities(client: Civil3DClient, corridor: str, out_dir: str,
                               interval: float = 20.0,
                               baseline: int = 0, region: int = 0) -> Dict[str, object]:
    """Khối lượng theo lớp kết cấu, tính từ diện tích shape của corridor.

    Phép tính là hình thang giữa hai lý trình liên tiếp ĐÃ ĐỌC: thể tích của một
    lớp bằng tổng (A1 + A2)/2 * khoảng cách. Vì vậy con số phụ thuộc bước lý trình
    - bước thưa làm trơn những chỗ mặt cắt đổi nhanh. Bước đã dùng luôn được ghi
    kèm trong kết quả, và báo cáo nên nêu nó chứ đừng chỉ nêu thể tích.
    """
    data = cor_mod.corridor_shape_areas(client, corridor, baseline, region, interval)
    by_station: Dict[float, Dict[str, float]] = {}
    for row in data["shapes"]:
        if row["area"] is None:
            continue
        # Một mã có thể xuất hiện nhiều lần trên cùng mặt cắt (ví dụ SubBase hai
        # bên); cộng dồn chứ không ghi đè, nếu không một nửa khối lượng biến mất.
        st = by_station.setdefault(float(row["station"]), {})
        st[row["code"]] = st.get(row["code"], 0.0) + float(row["area"])

    stations = sorted(by_station)
    codes = sorted({c for st in by_station.values() for c in st})
    volumes = dict.fromkeys(codes, 0.0)
    for a, b in zip(stations, stations[1:]):
        length = b - a
        for c in codes:
            volumes[c] += (by_station[a].get(c, 0.0) + by_station[b].get(c, 0.0)) / 2.0 * length

    os.makedirs(out_dir, exist_ok=True)
    area_path = os.path.join(out_dir, "corridor_dien_tich_lop.csv")
    vol_path = os.path.join(out_dir, "corridor_khoi_luong_lop.csv")
    area_written = write_csv(
        area_path, ["ly_trinh"] + codes,
        ([s] + [round(by_station[s].get(c, 0.0), 6) for c in codes] for s in stations),
    )
    vol_written = write_csv(
        vol_path, ["lop", "the_tich_m3", "dien_tich_tb_m2"],
        ([c, round(volumes[c], 3),
          round(volumes[c] / (stations[-1] - stations[0]), 4) if len(stations) > 1 else 0.0]
         for c in codes),
    )
    return {
        "corridor": corridor,
        "interval": interval,
        "stations_total": data["stations_total"],
        "stations_used": len(stations),
        "station_range": [stations[0], stations[-1]] if stations else [],
        "codes": codes,
        "volumes_m3": {c: round(volumes[c], 3) for c in codes},
        "files": {"areas": area_written, "volumes": vol_written},
        "method": "hình thang giữa hai lý trình liên tiếp đã đọc",
        "verified": bool(stations) and all(v >= 0 for v in volumes.values()),
        "verified_by": "đếm lại số lý trình dùng để tính và kiểm tra thể tích không âm",
    }


def export_check_point_report(client: Civil3DClient, surface: str, points_file: str,
                              out_path: str,
                              tolerances: Sequence[float] = (0.02, 0.05, 0.10)
                              ) -> Dict[str, object]:
    """Đối chiếu bề mặt với các điểm kiểm tra ngoại nghiệp, xuất CSV.

    Đây là phép đánh giá độ chính xác có giá trị nhất trong báo cáo: điểm kiểm tra
    là số đo độc lập, không tham gia dựng bề mặt.
    """
    points = read_xyz(points_file)
    if not points:
        raise C3DError(f"Không đọc được điểm kiểm tra nào từ {points_file}.")
    result = surf_mod.compare_to_points(client, surface, points, tolerances)
    written = write_csv(
        out_path,
        ["x", "y", "z_diem_kiem_tra", "z_be_mat", "dz"],
        ([r["x"], r["y"], r["z_point"], r["z_surface"], r["dz"]] for r in result["rows"]),
    )
    return {
        "surface": surface,
        "points_file": os.path.abspath(points_file),
        "points_total": result["points_total"],
        "points_compared": result["points_compared"],
        "points_outside_surface": result["points_outside_surface"],
        "statistics": result["statistics"],
        "file": written,
        "verified": result["points_compared"] > 0,
        "verified_by": "đếm số điểm thật sự so sánh được (điểm ngoài phạm vi bị loại)",
    }
