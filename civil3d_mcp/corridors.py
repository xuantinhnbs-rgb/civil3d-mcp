"""
Corridor, sample line và trắc ngang
====================================
Bước cuối của chuỗi Civil 3D trong quy trình Scan-to-BIM: dựng corridor từ
tuyến + trắc dọc + assembly, rồi cắt trắc ngang để lấy khối lượng.

GIỚI HẠN PHẢI BIẾT TRƯỚC: COM KHÔNG tạo được assembly và subassembly. Ba đường
để có assembly trong bản vẽ:
  1. Bản vẽ mẫu đã chứa sẵn assembly (cách chắc chắn nhất cho công việc lặp lại).
  2. Kéo subassembly từ Tool Palette bằng tay.
  3. Gọi lệnh qua send_civil3d_command - chạy bất đồng bộ, không có kết quả trả về.
Vì vậy create_corridor ở đây đòi tên một assembly ĐÃ CÓ, và báo lỗi liệt kê đủ
các assembly đang có nếu tên không khớp.
"""

from __future__ import annotations

import os
import time

from typing import Dict, List, Optional, Sequence

from .alignments import get_alignment, get_profile
from .client import Civil3DClient, variant_point
from .com import C3DError, reraise_if_transient

CREATE_LOOKUP_ATTEMPTS = 8
CREATE_LOOKUP_DELAY = 0.25
from .geometry import station_range

BASELINE_TYPES = {0: "main", 1: "offset", 2: "hardcoded_offset"}


def _roadway(client: Civil3DClient):
    return client.roadway_doc


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def list_assemblies(client: Civil3DClient) -> Dict[str, object]:
    try:
        coll = _roadway(client).Assemblies
    except Exception as exc:
        # Đo được trên Civil 3D 2026 (COM 13.8): bản vẽ KHÔNG có assembly nào thì
        # chính property `Assemblies` bị từ chối bằng E_INVALIDARG - không phải trả về
        # một collection rỗng như `Corridors` vẫn làm. Nếu để lỗi này nổi lên nguyên
        # trạng, người dùng nhận một thông điệp "tham số không hợp lệ" nói về thuộc
        # tính bắt buộc khi tạo đối tượng, trong khi sự thật chỉ là "chưa có assembly".
        reraise_if_transient(exc)
        return {
            "count": 0,
            "assemblies": [],
            "collection_unavailable": True,
            "note": ("Bản vẽ chưa có assembly nào: Civil 3D từ chối cả việc mở collection "
                     "Assemblies khi nó rỗng. COM không tạo được assembly, nên muốn dựng "
                     "corridor thì phải dựng assembly bằng tay trong Civil 3D hoặc mở bản "
                     "vẽ/template đã có sẵn assembly."),
        }
    rows: List[Dict[str, object]] = []
    for i in range(int(coll.Count)):
        asm = coll.Item(i)
        rows.append({
            "index": i,
            "name": str(asm.Name),
            "description": Civil3DClient._safe(lambda a=asm: str(a.Description)),
            "handle": Civil3DClient._safe(lambda a=asm: str(a.Handle)),
            "subassemblies": Civil3DClient._safe(
                lambda a=asm: int(a.Subassemblies.Count), default=None),
        })
    return {
        "count": len(rows),
        "assemblies": rows,
        "note": ("COM không tạo được assembly. Nếu danh sách rỗng, hãy mở bản vẽ có sẵn "
                 "assembly hoặc dựng assembly bằng tay trong Civil 3D trước."),
    }


# --------------------------------------------------------------------------
# Corridor
# --------------------------------------------------------------------------

def list_corridors(client: Civil3DClient) -> Dict[str, object]:
    coll = _roadway(client).Corridors
    rows: List[Dict[str, object]] = []
    for i in range(int(coll.Count)):
        cor = coll.Item(i)
        rows.append({
            "index": i,
            "name": str(cor.Name),
            "description": Civil3DClient._safe(lambda c=cor: str(c.Description)),
            "baselines": Civil3DClient._safe(lambda c=cor: int(c.Baselines.Count), default=None),
            "corridor_surfaces": Civil3DClient._safe(
                lambda c=cor: int(c.CorridorSurfaces.Count), default=None),
            "out_of_date": Civil3DClient._safe(lambda c=cor: bool(c.OutOfDate), default=None),
            "handle": Civil3DClient._safe(lambda c=cor: str(c.Handle)),
        })
    return {"count": len(rows), "corridors": rows}


def get_corridor(client: Civil3DClient, name: str):
    return Civil3DClient.find_item(_roadway(client).Corridors, name, "corridor")


def corridor_info(client: Civil3DClient, name: str) -> Dict[str, object]:
    cor = get_corridor(client, name)
    out: Dict[str, object] = {
        "name": str(cor.Name),
        "description": Civil3DClient._safe(lambda: str(cor.Description)),
        "out_of_date": Civil3DClient._safe(lambda: bool(cor.OutOfDate), default=None),
        "rebuild_automatic": Civil3DClient._safe(lambda: bool(cor.RebuildAutomatic), default=None),
        "max_triangle_side": Civil3DClient._safe(
            lambda: float(cor.MaximumTriangleSideLength), default=None),
    }
    baselines: List[Dict[str, object]] = []
    coll = cor.Baselines
    for i in range(int(coll.Count)):
        bl = coll.Item(i)
        entry: Dict[str, object] = {
            "index": i,
            "type": BASELINE_TYPES.get(
                Civil3DClient._safe(lambda b=bl: int(b.Type), default=-1), "unknown"),
            "alignment": Civil3DClient._safe(lambda b=bl: str(b.Alignment.Name), default=None),
            "profile": Civil3DClient._safe(lambda b=bl: str(b.Profile.Name), default=None),
            "start_station": Civil3DClient._safe(lambda b=bl: round(float(b.StartStation), 6),
                                                 default=None),
            "end_station": Civil3DClient._safe(lambda b=bl: round(float(b.EndStation), 6),
                                               default=None),
            "processed": Civil3DClient._safe(lambda b=bl: bool(b.IsProcessed), default=None),
        }
        regions: List[Dict[str, object]] = []
        try:
            rcoll = bl.BaselineRegions
            for j in range(int(rcoll.Count)):
                rg = rcoll.Item(j)
                regions.append({
                    "index": j,
                    "start_station": Civil3DClient._safe(
                        lambda r=rg: round(float(r.StartStation), 6), default=None),
                    "end_station": Civil3DClient._safe(
                        lambda r=rg: round(float(r.EndStation), 6), default=None),
                    "processed": Civil3DClient._safe(lambda r=rg: bool(r.IsProcessed),
                                                     default=None),
                })
        except Exception as exc:
            reraise_if_transient(exc)
        entry["regions"] = regions
        baselines.append(entry)
    out["baselines"] = baselines

    surfaces: List[Dict[str, object]] = []
    try:
        scoll = cor.CorridorSurfaces
        for i in range(int(scoll.Count)):
            cs = scoll.Item(i)
            surfaces.append({
                "name": str(cs.Name),
                "is_built": Civil3DClient._safe(lambda s=cs: bool(s.IsBuild), default=None),
                "is_drawn": Civil3DClient._safe(lambda s=cs: bool(s.IsDraw), default=None),
            })
    except Exception as exc:
        reraise_if_transient(exc)
    out["corridor_surfaces"] = surfaces
    return out


def create_corridor(client: Civil3DClient, name: str, alignment: str, profile: str,
                    assembly: str) -> Dict[str, object]:
    """Tạo corridor từ tuyến + trắc dọc + assembly ĐÃ CÓ trong bản vẽ.

    Cả ba tên đều được kiểm tra tồn tại TRƯỚC khi gọi COM: Corridors.Add nhận
    chuỗi tên và khi tên sai nó ném một lỗi COM chung chung không nói tên nào sai.
    """
    rdoc = _roadway(client)
    coll = rdoc.Corridors
    if any(n.lower() == name.lower() for n in Civil3DClient.collection_names(coll)):
        raise C3DError(f"Bản vẽ đã có corridor tên {name!r}.")

    get_alignment(client, alignment)                       # ném lỗi kèm danh sách nếu sai
    get_profile(client, alignment, profile)
    Civil3DClient.find_item(rdoc.Assemblies, assembly, "assembly")

    coll.Add(name, alignment, profile, assembly)

    # Corridor vừa tạo CHƯA xuất hiện ngay trong collection: đo được trên Civil 3D
    # 2026 rằng lần đọc đầu tiên báo "không có corridor nào", rồi vài trăm mili giây
    # sau thì có. Kiểm chứng đọc quá sớm còn tệ hơn không kiểm chứng - nó báo hỏng
    # một thao tác đã thành công, và người dùng sẽ tạo lại, sinh ra corridor thứ hai.
    cor = None
    for _ in range(CREATE_LOOKUP_ATTEMPTS):
        try:
            cor = Civil3DClient.find_item(coll, name, "corridor")
            break
        except Exception as exc:
            reraise_if_transient(exc)
            time.sleep(CREATE_LOOKUP_DELAY)
    if cor is None:
        raise C3DError(
            f"Đã gọi Corridors.Add cho {name!r} nhưng sau "
            f"{CREATE_LOOKUP_ATTEMPTS * CREATE_LOOKUP_DELAY:.1f}s vẫn không thấy corridor "
            f"trong bản vẽ. Hãy kiểm tra bằng list_corridors trước khi tạo lại - tạo lại "
            f"khi nó đã tồn tại sẽ sinh ra corridor thứ hai."
        )
    baseline_count = Civil3DClient._safe(lambda: int(cor.Baselines.Count), default=0)
    return {
        "created": name,
        "alignment": alignment,
        "profile": profile,
        "assembly": assembly,
        "baseline_count": baseline_count,
        "out_of_date": Civil3DClient._safe(lambda: bool(cor.OutOfDate), default=None),
        "verified": baseline_count > 0,
        "verified_by": "đọc lại số baseline của corridor vừa tạo",
        "next_step": "gọi rebuild_corridor để Civil 3D tính hình học corridor.",
    }


def add_baseline(client: Civil3DClient, corridor: str, alignment: str, profile: str,
                 assembly: str) -> Dict[str, object]:
    """Thêm một baseline (tuyến phụ) vào corridor đã có."""
    cor = get_corridor(client, corridor)
    rdoc = _roadway(client)
    get_alignment(client, alignment)
    get_profile(client, alignment, profile)
    Civil3DClient.find_item(rdoc.Assemblies, assembly, "assembly")
    before = int(cor.Baselines.Count)
    cor.AddBaseline(alignment, profile, assembly)
    after = int(cor.Baselines.Count)
    return {
        "corridor": corridor,
        "baseline_count_before": before,
        "baseline_count_after": after,
        "verified": after > before,
        "verified_by": "đếm lại số baseline trước và sau khi thêm",
    }


def rebuild_corridor(client: Civil3DClient, name: str) -> Dict[str, object]:
    """Tính lại hình học corridor.

    Corridor lớn có thể mất vài phút; COM chặn cho tới khi tính xong, nên lời gọi
    này có thể chạy lâu. Cờ `out_of_date` sau khi chạy là bằng chứng: còn True
    nghĩa là Civil 3D chưa tính xong hoặc đã từ chối.
    """
    cor = get_corridor(client, name)
    cor.Rebuild()
    out_of_date = Civil3DClient._safe(lambda: bool(cor.OutOfDate), default=None)
    return {
        "corridor": name,
        "out_of_date": out_of_date,
        "baselines_processed": [
            Civil3DClient._safe(lambda b=cor.Baselines.Item(i): bool(b.IsProcessed), default=None)
            for i in range(int(cor.Baselines.Count))
        ],
        "verified": out_of_date is False,
        "verified_by": "đọc lại cờ OutOfDate sau khi rebuild",
    }


def corridor_surface_elevations(client: Civil3DClient, corridor: str, surface: str,
                                points: Sequence[Sequence[float]]) -> Dict[str, object]:
    """Cao độ mặt corridor tại một loạt toạ độ XY."""
    cor = get_corridor(client, corridor)
    cs = Civil3DClient.find_item(cor.CorridorSurfaces, surface, "mặt corridor")
    rows: List[Dict[str, object]] = []
    inside = 0
    for p in points:
        x, y = float(p[0]), float(p[1])
        try:
            z = round(float(cs.FindElevationAtXY(x, y)), 6)
            inside += 1
        except Exception as exc:
            reraise_if_transient(exc)
            z = None
        rows.append({"x": x, "y": y, "z": z})
    return {"corridor": corridor, "surface": surface, "sampled": len(rows),
            "inside_surface": inside, "points": rows}


# --------------------------------------------------------------------------
# Sample line và trắc ngang
# --------------------------------------------------------------------------

ASSEMBLY_LIBRARY = r"C:\ProgramData\Autodesk\C3D {ver}\enu\Assemblies\{units}"


def _assembly_names(client: Civil3DClient) -> List[str]:
    """Tên các assembly đang có. Trả về [] khi bản vẽ chưa có cái nào."""
    try:
        coll = _roadway(client).Assemblies
    except Exception as exc:
        reraise_if_transient(exc)
        return []
    return [str(coll.Item(i).Name) for i in range(int(coll.Count))]


def list_assembly_library(version: str = "2026", units: str = "Metric") -> Dict[str, object]:
    """Liệt kê các bản vẽ assembly mẫu Civil 3D cài sẵn trên máy.

    Đây là nguồn assembly dùng được cho `import_assembly`: COM không tạo được
    assembly, nhưng chép được một assembly có sẵn vào bản vẽ.
    """
    folder = ASSEMBLY_LIBRARY.format(ver=version, units=units)
    if not os.path.isdir(folder):
        raise C3DError(
            f"Không thấy thư viện assembly tại {folder}. Kiểm tra lại số hiệu bản "
            f"({version!r}) và hệ đơn vị ({units!r}: 'Metric' hoặc 'Imperial')."
        )
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".dwg"))
    return {
        "folder": folder,
        "count": len(files),
        "drawings": files,
        "note": "tên assembly bên trong thường trùng tên file, nhưng hãy đọc lại bằng "
                "list_assemblies sau khi nhập để lấy tên thật.",
    }


def import_assembly(client: Civil3DClient, source_drawing: str,
                    insert_point: Sequence[float] = (0.0, 0.0, 0.0)) -> Dict[str, object]:
    """Chép assembly từ một bản vẽ khác vào bản vẽ hiện hành.

    COM KHÔNG tạo được assembly (`AeccAssemblies` chỉ có `Count` và `Item`), nên đây
    là đường duy nhất thuần COM: chèn bản vẽ nguồn làm block. Đối tượng AEC không
    nằm trong định nghĩa block - Civil 3D trộn thẳng chúng vào cơ sở dữ liệu ngay
    khi chèn. Vì vậy **không được nổ block**: nổ sẽ sinh ra một bản sao thứ hai của
    assembly (đã đo được: chèn rồi nổ cho ra 2 assembly và 6 subassembly thay vì 1
    và 3). Hàm này chèn, đo lại số assembly, rồi xoá block reference.
    """
    path = os.path.abspath(source_drawing)
    if not os.path.exists(path):
        raise C3DError(f"Không tìm thấy bản vẽ nguồn: {path}")
    before = _assembly_names(client)

    adoc = client.acad_doc
    pt = variant_point(insert_point[0], insert_point[1],
                       insert_point[2] if len(insert_point) > 2 else 0.0)
    block_ref = adoc.ModelSpace.InsertBlock(pt, path, 1.0, 1.0, 1.0, 0.0)
    block_name = Civil3DClient._safe(lambda: str(block_ref.Name), default="")
    exploded = False
    try:
        after = _assembly_names(client)
        if len(after) == len(before):
            # Lô hiếm: bản vẽ nguồn giữ assembly bên trong block. Chỉ khi đó mới nổ.
            for obj in block_ref.Explode():
                del obj
            exploded = True
            after = _assembly_names(client)
    finally:
        Civil3DClient._safe(lambda: block_ref.Delete(), default=None)

    added = [n for n in after if n not in before]
    return {
        "source": path,
        "block_name": block_name,
        "assemblies_before": before,
        "assemblies_after": after,
        "imported": added,
        "exploded": exploded,
        "verified": bool(added),
        "verified_by": "so sánh danh sách tên assembly trước và sau khi chèn",
        "note": ("Không có assembly mới nào xuất hiện. Bản vẽ nguồn có thể không chứa "
                 "assembly, hoặc tên đã trùng với assembly sẵn có."
                 if not added else
                 "Subassembly đi kèm được chép theo. Muốn đổi bề rộng làn hay độ dốc "
                 "thì sửa trong bảng thuộc tính của Civil 3D: COM trả về các collection "
                 "tham số dưới dạng đối tượng không gọi được phương thức."),
    }


def _applied_region(client: Civil3DClient, corridor: str, baseline: int, region: int):
    cor = get_corridor(client, corridor)
    bl = cor.Baselines.Item(int(baseline))
    reg = bl.BaselineRegions.Item(int(region))
    return cor, bl, reg


def _pick_stations(applied, interval: Optional[float], max_stations: Optional[int]) -> List[int]:
    """Chọn chỉ số các trạm cần đọc.

    Corridor sinh một trạm ở mỗi điểm hình học của tuyến và trắc dọc, nên số trạm
    thường lớn hơn nhiều so với nhu cầu báo cáo. `interval` lọc theo lý trình chứ
    không theo chỉ số: bước theo chỉ số sẽ cho ra dãy lý trình không đều vì các
    trạm không cách đều nhau.
    """
    count = int(applied.Count)
    stations = [float(s) for s in applied.Stations]
    if interval is None:
        idx = list(range(count))
    else:
        idx, next_station = [], None
        for i, s in enumerate(stations):
            if next_station is None or s >= next_station - 1e-9:
                idx.append(i)
                next_station = (stations[0] if next_station is None else next_station) + float(interval)
                while next_station <= s + 1e-9:
                    next_station += float(interval)
        if idx and idx[-1] != count - 1:
            idx.append(count - 1)
    if max_stations and len(idx) > int(max_stations):
        idx = idx[:int(max_stations)]
    return idx


def corridor_points(client: Civil3DClient, corridor: str, baseline: int = 0,
                    region: int = 0, interval: Optional[float] = None,
                    max_stations: Optional[int] = None,
                    with_coordinates: bool = True) -> Dict[str, object]:
    """Đọc các điểm hình học corridor đã tính, theo lý trình.

    Đây là hình học corridor THẬT mà Civil 3D đã tính ra, không phải hình học
    assembly danh nghĩa: mỗi trạm là một assembly đã áp dụng, và toạ độ điểm đã
    tính đến trắc dọc, siêu cao và mọi tham số của subassembly.

    Cao độ trả về là cao độ TUYỆT ĐỐI khi đọc được trắc dọc của baseline; khoá
    `elevation_is_absolute` nói rõ đang là loại nào, vì gộp hai loại vào một cột
    là cách chắc chắn nhất để sai một mét mà không ai thấy.
    """
    cor, bl, reg = _applied_region(client, corridor, baseline, region)
    applied = reg.AppliedAssemblies
    idx = _pick_stations(applied, interval, max_stations)
    stations = [float(s) for s in applied.Stations]

    al = prof = None
    if with_coordinates:
        try:
            al = bl.Alignment
            prof = bl.Profile
        except Exception as exc:
            reraise_if_transient(exc)

    rows: List[Dict[str, object]] = []
    absolute = prof is not None
    for i in idx:
        station = stations[i]
        base_elev = None
        if prof is not None:
            base_elev = Civil3DClient._safe(
                lambda s=station: float(prof.ElevationAt(s)), default=None)
        for p in applied.Item(i).GetPoints():
            soe = Civil3DClient._safe(
                lambda q=p: [float(v) for v in q.GetStationOffsetElevationToBaseline()],
                default=None)
            if soe is None:
                continue
            offset, dz = soe[1], soe[2]
            row: Dict[str, object] = {
                "station": round(station, 4),
                "offset": round(offset, 4),
                "elevation": round(base_elev + dz, 4) if base_elev is not None else round(dz, 4),
                "elevation_to_baseline": round(dz, 4),
                "codes": Civil3DClient._safe(lambda q=p: list(q.CorridorCodes), default=[]),
            }
            if al is not None:
                xy = Civil3DClient._safe(
                    lambda s=station, o=offset: [float(v) for v in al.PointLocation(s, o)],
                    default=None)
                if xy:
                    row["x"], row["y"] = round(xy[0], 4), round(xy[1], 4)
            rows.append(row)

    return {
        "corridor": corridor,
        "baseline": int(baseline),
        "region": int(region),
        "stations_total": int(applied.Count),
        "stations_read": len(idx),
        "interval": interval,
        "point_count": len(rows),
        "elevation_is_absolute": absolute,
        "points": rows,
        "offset_convention": "offset âm bên trái tim tuyến, dương bên phải",
    }


def corridor_shape_areas(client: Civil3DClient, corridor: str, baseline: int = 0,
                         region: int = 0, interval: Optional[float] = None,
                         max_stations: Optional[int] = None) -> Dict[str, object]:
    """Diện tích từng shape (lớp kết cấu) của corridor theo lý trình.

    Shape là phần mặt cắt khép kín mà subassembly khai báo kèm mã vật liệu
    (Pave1, Base, SubBase, Curb...). Đây là nguồn khối lượng theo lớp.
    """
    cor, bl, reg = _applied_region(client, corridor, baseline, region)
    applied = reg.AppliedAssemblies
    idx = _pick_stations(applied, interval, max_stations)
    stations = [float(s) for s in applied.Stations]

    rows: List[Dict[str, object]] = []
    for i in idx:
        station = stations[i]
        for sh in applied.Item(i).GetShapes():
            area = Civil3DClient._safe(lambda s=sh: float(s.GetArea()), default=None)
            codes = Civil3DClient._safe(lambda s=sh: list(s.CorridorCodes), default=[])
            rows.append({
                "station": round(station, 4),
                "code": codes[0] if codes else "",
                "codes": codes,
                "area": None if area is None else round(area, 6),
            })
    return {
        "corridor": corridor,
        "stations_total": int(applied.Count),
        "stations_read": len(idx),
        "interval": interval,
        "row_count": len(rows),
        "shapes": rows,
        "area_note": "diện tích mặt cắt ngang của shape, đơn vị theo bản vẽ (m² nếu bản vẽ mét)",
    }


def list_sample_line_groups(client: Civil3DClient, alignment: str) -> Dict[str, object]:
    al = get_alignment(client, alignment)
    coll = al.SampleLineGroups
    rows: List[Dict[str, object]] = []
    for i in range(int(coll.Count)):
        grp = coll.Item(i)
        rows.append({
            "index": i,
            "name": str(grp.Name),
            "sample_lines": Civil3DClient._safe(lambda g=grp: int(g.SampleLines.Count),
                                                default=None),
            "sampled_surfaces": Civil3DClient._safe(lambda g=grp: int(g.SampledSurfaces.Count),
                                                    default=None),
        })
    return {"alignment": alignment, "count": len(rows), "groups": rows}


def _default_section_styles(client: Civil3DClient) -> Dict[str, object]:
    """Trả về ĐỐI TƯỢNG style, không phải tên.

    SampleLineGroups.Add và SampledSurfaces.AddAllSurfaces đòi đối tượng style;
    truyền tên làm pywin32 báo "The Python instance can not be converted to a COM
    object". Trái lại Profiles.AddFromSurface lại đòi TÊN bề mặt dạng chuỗi và từ
    chối đối tượng. Hai quy ước ngược nhau nằm trong cùng một API, và type library
    không phân biệt được hai trường hợp - mỗi lời gọi phải thử bằng thực nghiệm.
    """
    doc = client.aecc_doc
    need = {
        "group_plot": doc.GroupPlotStyles,
        "sample_line": doc.SampleLineStyles,
        "sample_line_label": doc.SampleLineLabelStyles,
        "section": doc.SectionStyles,
    }
    out: Dict[str, object] = {}
    for key, coll in need.items():
        if int(coll.Count) == 0:
            raise C3DError(
                f"Bản vẽ thiếu style cho {key} - không tạo được sample line group. "
                "Hãy dùng template Civil 3D."
            )
        out[key] = coll.Item(0)
    return out


def create_sample_line_group(client: Civil3DClient, alignment: str, name: str,
                             stations: Optional[Sequence[float]] = None,
                             interval: Optional[float] = None,
                             left_width: float = 20.0,
                             right_width: float = 20.0,
                             layer: Optional[str] = None,
                             sample_all_surfaces: bool = True) -> Dict[str, object]:
    """Tạo nhóm sample line và rải sample line theo lý trình.

    Dùng `stations` để chỉ đích danh các lý trình, hoặc `interval` để rải đều trên
    toàn tuyến. Mỗi sample line được thêm bằng AddByStation - cách này chỉ cần một
    con số, không phải dựng đối tượng tham số StationRange như lệnh trên giao diện.
    """
    al = get_alignment(client, alignment)
    coll = al.SampleLineGroups
    if any(n.lower() == name.lower() for n in Civil3DClient.collection_names(coll)):
        raise C3DError(f"Tuyến {alignment!r} đã có nhóm sample line tên {name!r}.")
    styles = _default_section_styles(client)

    if stations is None:
        if interval is None:
            raise C3DError("Phải truyền `stations` hoặc `interval`.")
        stations = station_range(float(al.StartingStation), float(al.EndingStation),
                                 float(interval))
    stations = [float(s) for s in stations]
    if not stations:
        raise C3DError("Không có lý trình nào để tạo sample line.")

    grp = coll.Add(name, client.ensure_layer(layer), styles["group_plot"],
                   styles["sample_line"], styles["sample_line_label"])
    sampled_error = None
    sampled_enabled = 0
    if sample_all_surfaces:
        try:
            grp.SampledSurfaces.AddAllSurfaces(styles["section"])
        except Exception as exc:
            reraise_if_transient(exc)
            sampled_error = str(exc)
        # AddAllSurfaces ĐƯA bề mặt vào nhóm nhưng để cờ Sample TẮT, nên
        # CreateSectionsAtSampleLines sau đó chạy trót lọt mà không sinh ra section
        # nào. Đo được trên Civil 3D 2026: bật cờ này rồi cắt lại thì có section
        # ngay. Đây là kiểu lỗi tệ nhất - không thông báo gì, chỉ ra kết quả rỗng.
        sampled = grp.SampledSurfaces
        for i in range(int(sampled.Count)):
            try:
                sampled.Item(i).Sample = True
                sampled_enabled += 1
            except Exception as exc:
                reraise_if_transient(exc)

    lines = grp.SampleLines
    failed: List[Dict[str, object]] = []
    for st in stations:
        try:
            lines.AddByStation(f"{name} - {st:.3f}", st, float(left_width), float(right_width))
        except Exception as exc:
            reraise_if_transient(exc)
            failed.append({"station": st, "error": str(exc)})

    created = int(lines.Count)
    return {
        "created": name,
        "alignment": alignment,
        "stations_requested": len(stations),
        "sample_lines_created": created,
        "failed": failed,
        "sampled_surfaces": Civil3DClient._safe(lambda: int(grp.SampledSurfaces.Count),
                                                default=None),
        "sampled_surfaces_error": sampled_error,
        "sampled_surfaces_enabled": sampled_enabled,
        "swath": {"left": float(left_width), "right": float(right_width)},
        "verified": created > 0,
        "verified_by": "đếm lại số sample line trong nhóm sau khi tạo",
    }


def create_sections(client: Civil3DClient, alignment: str, group: str) -> Dict[str, object]:
    """Cắt trắc ngang tại mọi sample line của nhóm."""
    al = get_alignment(client, alignment)
    grp = Civil3DClient.find_item(al.SampleLineGroups, group, "nhóm sample line")
    sampled = grp.SampledSurfaces
    enabled = 0
    for i in range(int(sampled.Count)):
        try:
            if bool(sampled.Item(i).Sample):
                enabled += 1
        except Exception as exc:
            reraise_if_transient(exc)

    grp.CreateSectionsAtSampleLines()
    lines = grp.SampleLines
    total = 0
    for i in range(int(lines.Count)):
        total += Civil3DClient._safe(lambda ln=lines.Item(i): int(ln.Sections.Count), default=0)

    out: Dict[str, object] = {
        "alignment": alignment,
        "group": group,
        "sample_lines": int(lines.Count),
        "sampled_surfaces": int(sampled.Count),
        "sampled_surfaces_enabled": enabled,
        "sections_total": total,
        "verified": total > 0,
        "verified_by": "đếm lại tổng số section trên tất cả sample line",
    }
    if total == 0:
        out["diagnosis"] = _diagnose_no_sections(int(lines.Count), int(sampled.Count), enabled)
    return out


def _diagnose_no_sections(sample_lines: int, sampled_surfaces: int, enabled: int) -> str:
    """Nói rõ vì sao không có section nào, thay vì chỉ báo verified=false.

    CreateSectionsAtSampleLines không hề báo lỗi khi thiếu điều kiện, nên nếu không
    tự chẩn đoán thì người dùng chỉ thấy một kết quả rỗng không giải thích được.
    """
    if sample_lines == 0:
        return "Nhóm chưa có sample line nào - hãy tạo sample line trước."
    if sampled_surfaces == 0:
        return ("Nhóm chưa đăng ký bề mặt nào để lấy mẫu. Tạo lại nhóm với "
                "sample_all_surfaces=True, hoặc kiểm tra bản vẽ có bề mặt chưa.")
    if enabled == 0:
        return ("Có bề mặt trong nhóm nhưng cờ Sample của chúng đang TẮT nên không "
                "bề mặt nào được cắt. Đây là trạng thái mặc định sau AddAllSurfaces.")
    return ("Sample line và bề mặt đều hợp lệ nhưng không sinh ra section: nhiều khả "
            "năng các sample line nằm ngoài phạm vi bề mặt. Hãy đối chiếu hộp bao bề "
            "mặt (get_surface_info) với phạm vi lý trình của tuyến.")


def read_sections(client: Civil3DClient, alignment: str, group: str,
                  offset_interval: float = 1.0,
                  max_sample_lines: Optional[int] = None) -> Dict[str, object]:
    """Đọc trắc ngang thành bảng lý trình - offset - cao độ.

    Với mỗi sample line và mỗi bề mặt được lấy mẫu, cao độ được đọc theo bước
    offset đều trong phạm vi swath thật của section (LengthLeft/LengthRight), chứ
    không phải phạm vi danh nghĩa đã yêu cầu - hai con số này lệch nhau ở chỗ
    sample line chạy ra ngoài bề mặt.
    """
    al = get_alignment(client, alignment)
    grp = Civil3DClient.find_item(al.SampleLineGroups, group, "nhóm sample line")
    lines = grp.SampleLines
    limit = int(max_sample_lines) if max_sample_lines else int(lines.Count)

    rows: List[Dict[str, object]] = []
    skipped = 0
    for i in range(min(limit, int(lines.Count))):
        line = lines.Item(i)
        station = Civil3DClient._safe(lambda ln=line: round(float(ln.Station), 6), default=None)
        sections = line.Sections
        for j in range(int(sections.Count)):
            sec = sections.Item(j)
            surface_name = Civil3DClient._safe(lambda s=sec: str(s.Surface.Name), default=None)
            left = Civil3DClient._safe(lambda s=sec: float(s.LengthLeft), default=None)
            right = Civil3DClient._safe(lambda s=sec: float(s.LengthRight), default=None)
            if left is None or right is None:
                skipped += 1
                continue
            offset = -abs(left)
            while offset <= abs(right) + 1e-9:
                try:
                    z = round(float(sec.ElevationAt(offset)), 6)
                except Exception as exc:
                    reraise_if_transient(exc)
                    z = None
                rows.append({"station": station, "surface": surface_name,
                             "offset": round(offset, 6), "elevation": z})
                offset += float(offset_interval)
    return {
        "alignment": alignment,
        "group": group,
        "sample_lines_read": min(limit, int(lines.Count)),
        "rows": rows,
        "row_count": len(rows),
        "sections_skipped": skipped,
        "offset_convention": "offset âm bên trái tim tuyến, dương bên phải",
    }
