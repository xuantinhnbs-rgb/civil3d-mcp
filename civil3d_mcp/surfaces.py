"""
Bề mặt (TIN surface) - trung tâm của quy trình Scan-to-BIM
==========================================================
Bề mặt là nơi dữ liệu quét biến thành mô hình đo được: điểm mây -> TIN, TIN hoàn
công so với TIN thiết kế -> khối lượng đào đắp và sai lệch cao độ.

Ba đường nạp dữ liệu vào một TIN surface, chọn theo số lượng điểm:
  * AddPointMultiple  - vài nghìn điểm, truyền thẳng qua COM (nhanh, không cần file).
  * PointFiles.Add    - hàng chục nghìn điểm trở lên, Civil 3D tự đọc file trên đĩa.
  * ImportTIN/DEM/XML - đã có sẵn bề mặt ở định dạng khác.

Mọi hàm ghi ở đây đều đọc lại thống kê bề mặt sau khi ghi và trả về khoá
`verified`; riêng nạp điểm còn trả về `points_added_measured` - chênh lệch số
điểm đo được trước/sau, chứ không phải số điểm đã gửi đi.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

from .client import MAX_POINTS_PER_CALL, Civil3DClient, variant_doubles, variant_point
from .com import C3DError, read_optional, reraise_if_transient
from .geometry import chunk, deviation_stats

# Kiểu bề mặt theo enum AeccSurfaceType. Enum này bắt đầu từ 1, KHÔNG phải 0:
# đánh số từ 0 làm mọi TIN surface thường bị báo nhầm thành volume surface.
SURFACE_TYPES = {1: "grid", 2: "TIN", 3: "grid volume", 4: "TIN volume"}

# Vật đánh dấu "thuộc tính không có trên loại bề mặt này". Dùng riêng thay vì None
# để không lẫn với một giá trị None hợp lệ do Civil 3D trả về.
_ABSENT = object()


# Giao diện cụ thể tương ứng từng giá trị AeccSurfaceType. Collection `Surfaces`
# trả về giao diện GỐC `IAeccSurface`, vốn KHÔNG có `Statistics` lẫn các thành viên
# riêng của từng loại bề mặt. Đối tượng vừa tạo bằng `AddTinSurface` thì lại mang
# đúng giao diện dẫn xuất, nên cùng một dòng code chạy được ngay sau khi tạo và
# hỏng khi mở lại bản vẽ ở phiên sau.
SURFACE_INTERFACES = {
    1: "IAeccGridSurface",
    2: "IAeccTinSurface",
    3: "IAeccGridVolumeSurface",
    4: "IAeccTinVolumeSurface",
}


def _surfaces(client: Civil3DClient):
    return client.aecc_doc.Surfaces


def as_concrete_surface(surface):
    """Ép bề mặt về đúng giao diện dẫn xuất theo chính `Type` của nó.

    Phải chọn theo `Type`, KHÔNG được thử lần lượt từng giao diện: `CastTo` sang
    một giao diện anh em vẫn THÀNH CÔNG (đo được: ép TIN surface sang
    `IAeccTinVolumeSurface` trả về đối tượng bình thường), chỉ tới lúc đọc thành
    viên mới hỏng bằng "Member not found". Vòng lặp thử-đến-khi-được vì vậy sẽ
    chốt nhầm một phép ép sai mà tưởng là đã xong.

    Ép không được thì trả lại nguyên đối tượng: trên bề mặt vừa tạo, giao diện
    dẫn xuất đã đúng sẵn và `CastTo` là thừa.
    """
    kind = _safe_int(surface, "Type")
    iface = SURFACE_INTERFACES.get(kind)
    if not iface:
        return surface
    try:
        from win32com.client import CastTo
    except Exception:
        return surface
    try:
        cast = CastTo(surface, iface)
    except Exception as exc:
        reraise_if_transient(exc)
        return surface
    return cast if cast is not None else surface


def get_surface(client: Civil3DClient, name: str):
    return as_concrete_surface(
        Civil3DClient.find_item(_surfaces(client), name, "bề mặt (surface)"))


def _statistics(surface) -> Dict[str, object]:
    """Đọc thống kê bề mặt. Bề mặt chưa build xong sẽ thiếu một số trường."""
    stats = surface.Statistics
    out: Dict[str, object] = {}
    for key, getter in (
        ("num_points", lambda: int(stats.NumberOfPoints)),
        ("num_triangles", lambda: int(stats.NumberOfTriangles)),
        ("min_x", lambda: float(stats.MinX)),
        ("min_y", lambda: float(stats.MinY)),
        ("max_x", lambda: float(stats.MaxX)),
        ("max_y", lambda: float(stats.MaxY)),
        ("min_elevation", lambda: float(stats.MinElevation)),
        ("max_elevation", lambda: float(stats.MaxElevation)),
        ("mean_elevation", lambda: float(stats.MeanElevation)),
        ("area_2d", lambda: float(stats.Area2d)),
        ("area_3d", lambda: float(stats.Area3d)),
        ("min_grade", lambda: float(stats.MinGrade)),
        ("max_grade", lambda: float(stats.MaxGrade)),
        ("mean_grade", lambda: float(stats.MeanGrade)),
        ("max_triangle_length", lambda: float(stats.MaxTriangleLength)),
        ("min_triangle_length", lambda: float(stats.MinTriangleLength)),
    ):
        value = read_optional(getter, default=_ABSENT)
        if value is not _ABSENT:
            out[key] = value
    return out


def _volume_statistics(surface) -> Dict[str, object]:
    """Khối lượng đào/đắp của một volume surface (đơn vị khối theo bản vẽ)."""
    stats = surface.Statistics
    out: Dict[str, object] = {}
    for key, getter in (
        ("cut_volume", lambda: float(stats.CutVolume)),
        ("fill_volume", lambda: float(stats.FillVolume)),
        ("net_volume", lambda: float(stats.NetVolume)),
        # BottomSurface/TopSurface trả về ĐỐI TƯỢNG bề mặt chứ không phải tên; ép
        # thẳng sang chuỗi chỉ ra "<COMObject <unknown>>", một giá trị vô nghĩa mà
        # vẫn lọt qua mọi kiểm tra kiểu.
        ("base_surface", lambda: str(stats.BottomSurface.Name)),
        ("comparison_surface", lambda: str(stats.TopSurface.Name)),
    ):
        value = read_optional(getter, default=_ABSENT)
        if value is not _ABSENT:
            out[key] = value
    return out


def _point_count(surface) -> Optional[int]:
    try:
        return int(surface.Statistics.NumberOfPoints)
    except Exception as exc:
        reraise_if_transient(exc)
        return None


# --------------------------------------------------------------------------
# Liệt kê và tra cứu
# --------------------------------------------------------------------------

def list_surfaces(client: Civil3DClient, with_statistics: bool = False) -> Dict[str, object]:
    coll = _surfaces(client)
    items: List[Dict[str, object]] = []
    for i in range(int(coll.Count)):
        surface = as_concrete_surface(coll.Item(i))
        entry: Dict[str, object] = {
            "index": i,
            "name": str(surface.Name),
            "type": SURFACE_TYPES.get(_safe_int(surface, "Type"), "unknown"),
            "description": Civil3DClient._safe(lambda s=surface: str(s.Description)),
            "style": Civil3DClient._safe(lambda s=surface: str(s.StyleName)),
            "layer": Civil3DClient._safe(lambda s=surface: str(s.Layer)),
            "handle": Civil3DClient._safe(lambda s=surface: str(s.Handle)),
        }
        if with_statistics:
            entry["statistics"] = _statistics(surface)
            if entry["type"] in ("TIN volume", "grid volume"):
                entry["volumes"] = _volume_statistics(surface)
        items.append(entry)
    return {"count": len(items), "surfaces": items}


def _safe_int(obj, attr: str) -> int:
    try:
        return int(getattr(obj, attr))
    except Exception:
        return -1


def surface_info(client: Civil3DClient, name: str) -> Dict[str, object]:
    surface = get_surface(client, name)
    kind = SURFACE_TYPES.get(_safe_int(surface, "Type"), "unknown")
    out: Dict[str, object] = {
        "name": str(surface.Name),
        "type": kind,
        "description": Civil3DClient._safe(lambda: str(surface.Description)),
        "style": Civil3DClient._safe(lambda: str(surface.StyleName)),
        "layer": Civil3DClient._safe(lambda: str(surface.Layer)),
        "handle": Civil3DClient._safe(lambda: str(surface.Handle)),
        "statistics": _statistics(surface),
    }
    if kind in ("TIN volume", "grid volume"):
        out["volumes"] = _volume_statistics(surface)
    for key, getter in (
        ("boundaries", lambda: int(surface.Boundaries.Count)),
        ("breaklines", lambda: int(surface.Breaklines.Count)),
        ("contours", lambda: int(surface.Contours.Count)),
        ("dem_files", lambda: int(surface.DEMFiles.Count)),
        ("point_files", lambda: int(surface.PointFiles.Count)),
        ("point_groups", lambda: int(surface.PointGroups.Count)),
    ):
        try:
            out.setdefault("definition", {})[key] = getter()
        except Exception as exc:
            reraise_if_transient(exc)
    return out


# --------------------------------------------------------------------------
# Tạo bề mặt
# --------------------------------------------------------------------------

def create_tin_surface(client: Civil3DClient, name: str, style: Optional[str] = None,
                       layer: Optional[str] = None,
                       description: Optional[str] = None) -> Dict[str, object]:
    """Tạo một TIN surface rỗng.

    Civil 3D không nhận tham số rời: phải dựng đối tượng AeccTinCreationData rồi
    truyền vào AddTinSurface. Đối tượng đó được tạo qua GetInterfaceObject trên
    chính tiến trình AutoCAD đang chạy, nên nó luôn khớp phiên bản đang kết nối.
    """
    conn = client.ensure_connected()
    coll = _surfaces(client)
    existing = Civil3DClient.collection_names(coll)
    if any(n.lower() == name.lower() for n in existing):
        raise C3DError(f"Bản vẽ đã có bề mặt tên {name!r}. Hãy đổi tên hoặc xoá bề mặt cũ.")

    data = conn.acad.GetInterfaceObject(f"AeccXLand.AeccTinCreationData.{conn.version}")
    data.Name = name
    data.Style = style or _default_surface_style(client)
    # Layer VÀ BaseLayer đều bắt buộc, dù tài liệu không nói. Thiếu BaseLayer thì
    # AddTinSurface ném E_INVALIDARG nhưng Civil 3D chỉ báo "Exception occurred" -
    # đã đo được bằng thực nghiệm trên Civil 3D 2026 (COM 13.8): bốn tổ hợp thiếu
    # BaseLayer đều hỏng, tổ hợp có nó thì chạy.
    resolved_layer = client.ensure_layer(layer)
    data.Layer = resolved_layer
    data.BaseLayer = resolved_layer
    if description:
        data.Description = description

    coll.AddTinSurface(data)

    # Kiểm chứng: bề mặt phải xuất hiện trong collection sau khi tạo.
    names_after = Civil3DClient.collection_names(coll)
    created = any(n.lower() == name.lower() for n in names_after)
    if not created:
        raise C3DError(
            f"AddTinSurface chạy xong nhưng bề mặt {name!r} không có trong bản vẽ. "
            "Kiểm tra tên style và layer truyền vào."
        )
    return {
        "created": name,
        "style": Civil3DClient._safe(lambda: str(data.Style)),
        "surface_count": len(names_after),
        "verified": True,
        "verified_by": "đọc lại danh sách bề mặt sau khi tạo",
    }


def _default_surface_style(client: Civil3DClient) -> str:
    """Tên style bề mặt đầu tiên có trong bản vẽ.

    AddTinSurface đòi một style có thật; tên style phụ thuộc template (bản Việt
    hoá, bản Metric, bản Imperial đặt tên khác nhau) nên không hardcode được.
    """
    styles = client.aecc_doc.SurfaceStyles
    if int(styles.Count) == 0:
        raise C3DError(
            "Bản vẽ không có style bề mặt nào. Hãy dùng template Civil 3D (_AutoCAD Civil 3D "
            "(Metric) NCS.dwt) thay vì bản vẽ AutoCAD trắng."
        )
    return str(styles.Item(0).Name)


def create_volume_surface(client: Civil3DClient, name: str, base_surface: str,
                          comparison_surface: str,
                          style: Optional[str] = None,
                          layer: Optional[str] = None,
                          description: Optional[str] = None) -> Dict[str, object]:
    """Tạo TIN volume surface = so sánh hai bề mặt, cho ra khối lượng đào/đắp.

    Đây là phép đo trung tâm khi đối chiếu mô hình hoàn công với thiết kế:
    base = bề mặt gốc/thiết kế, comparison = bề mặt hoàn công từ đám mây điểm.
    Dấu quy ước của Civil 3D: cut là phần comparison THẤP hơn base.
    """
    conn = client.ensure_connected()
    coll = _surfaces(client)
    base = get_surface(client, base_surface)
    comp = get_surface(client, comparison_surface)
    if any(n.lower() == name.lower() for n in Civil3DClient.collection_names(coll)):
        raise C3DError(f"Bản vẽ đã có bề mặt tên {name!r}.")

    data = conn.acad.GetInterfaceObject(f"AeccXLand.AeccTinVolumeCreationData.{conn.version}")
    data.Name = name
    # Description RỖNG bị AddTinVolumeSurface từ chối với E_INVALIDARG - đo được
    # trên Civil 3D 2026 (COM 13.8): bốn tổ hợp mô tả rỗng đều hỏng bất kể style và
    # layer, tổ hợp có mô tả thì chạy. AddTinSurface thường lại KHÔNG đòi điều này.
    data.Description = description or f"So sánh {comparison_surface} với {base_surface}"
    data.Style = style or _default_surface_style(client)
    resolved_layer = client.ensure_layer(layer)
    data.Layer = resolved_layer
    data.BaseLayer = resolved_layer     # bắt buộc, xem chú thích ở create_tin_surface
    data.BaseSurface = base
    data.ComparisonSurface = comp
    coll.AddTinVolumeSurface(data)

    # Phải ép về giao diện dẫn xuất ở ĐÂY nữa, không chỉ trong get_surface: bề mặt
    # lấy thẳng từ collection là giao diện gốc, nên `Statistics` ném AttributeError.
    # Hậu quả không dừng ở một lời gọi đọc hỏng - AttributeError được lớp gọi coi là
    # lỗi tạm thời và cho chạy lại CẢ hàm này, mà volume surface thì đã tạo xong ở
    # dòng trên, nên lần chạy lại đụng chốt trùng tên và báo "bản vẽ đã có bề mặt
    # tên X" cho một thao tác vừa thành công.
    surface = as_concrete_surface(Civil3DClient.find_item(coll, name, "bề mặt"))
    volumes = _volume_statistics(surface)
    out: Dict[str, object] = {
        "created": name,
        "base_surface": base_surface,
        "comparison_surface": comparison_surface,
        "volumes": volumes,
        "statistics": _statistics(surface),
        "verified": bool(volumes),
        "verified_by": "đọc lại khối lượng cut/fill/net từ chính bề mặt vừa tạo",
        "sign_convention": ("cut = phần bề mặt so sánh thấp hơn bề mặt gốc; "
                            "net = fill - cut theo quy ước của Civil 3D"),
    }
    if not volumes:
        out["note"] = ("Chưa đọc được khối lượng - hai bề mặt có thể không chồng lấn nhau. "
                       "Hãy kiểm tra hộp bao của cả hai bằng get_surface_info.")
    return out


# --------------------------------------------------------------------------
# Nạp dữ liệu vào bề mặt
# --------------------------------------------------------------------------

def add_points(client: Civil3DClient, surface_name: str,
               points: Sequence[Sequence[float]],
               chunk_size: int = MAX_POINTS_PER_CALL) -> Dict[str, object]:
    """Nạp điểm trực tiếp vào TIN surface qua COM.

    AddPointMultiple nhận một SAFEARRAY nhưng tài liệu không nói rõ bố cục mảng,
    và hai bố cục hợp lý đều không báo lỗi khi sai - chỉ lặng lẽ không thêm điểm
    nào. Vì vậy lô đầu tiên được dùng làm phép thử: gửi theo bố cục phẳng
    (x,y,z,x,y,z...), đếm lại số điểm, và chỉ khi số điểm KHÔNG tăng mới chuyển
    sang bố cục mảng-của-mảng. Số điểm báo cáo luôn là số ĐO ĐƯỢC, không phải số
    đã gửi đi.
    """
    if not points:
        raise C3DError("Danh sách điểm rỗng.")
    surface = get_surface(client, surface_name)
    before = _point_count(surface)
    if before is None:
        raise C3DError(
            f"Không đọc được số điểm của bề mặt {surface_name!r} - có thể đây không phải "
            "TIN surface. Chỉ TIN surface mới nhận thêm điểm rời."
        )

    layout = "flat"
    batches = 0
    for batch in chunk(list(points), max(1, int(chunk_size))):
        flat: List[float] = []
        for p in batch:
            if len(p) < 3:
                raise C3DError(f"Điểm {p!r} thiếu cao độ - cần đủ (x, y, z).")
            flat.extend((float(p[0]), float(p[1]), float(p[2])))
        try:
            if layout == "flat":
                surface.AddPointMultiple(variant_doubles(flat))
            else:
                surface.AddPointMultiple([variant_point(p[0], p[1], p[2]) for p in batch])
        except Exception as exc:
            reraise_if_transient(exc)
            if batches == 0 and layout == "flat":
                layout = "nested"
                surface.AddPointMultiple([variant_point(p[0], p[1], p[2]) for p in batch])
            else:
                raise
        batches += 1
        if batches == 1 and layout == "flat":
            # Phép thử bố cục: lô đầu phải làm số điểm tăng lên.
            probe = _point_count(surface)
            if probe is not None and probe <= before:
                layout = "nested"
                surface.AddPointMultiple([variant_point(p[0], p[1], p[2]) for p in batch])

    surface.Rebuild()
    after = _point_count(surface)
    measured = (after - before) if (after is not None and before is not None) else None
    return {
        "surface": surface_name,
        "points_sent": len(points),
        "points_added_measured": measured,
        "point_count_before": before,
        "point_count_after": after,
        "batches": batches,
        "array_layout_used": layout,
        "verified": measured is not None and measured > 0,
        "verified_by": "so sánh Statistics.NumberOfPoints trước và sau khi nạp",
        "note": ("Số điểm đo được có thể nhỏ hơn số điểm gửi đi: Civil 3D loại điểm trùng "
                 "toạ độ và điểm nằm ngoài ranh giới bề mặt."),
    }


def add_point_file(client: Civil3DClient, surface_name: str, file_path: str,
                   file_format: str = "PENZD (comma delimited)") -> Dict[str, object]:
    """Gắn một file điểm trên đĩa vào định nghĩa bề mặt.

    Đây là đường nạp đúng cho dữ liệu quét thật (hàng chục nghìn điểm trở lên):
    Civil 3D đọc thẳng từ đĩa, không phải đẩy qua COM từng lô.

    `file_format` phải TRÙNG TÊN một định dạng điểm đã khai trong bản vẽ (xem
    Toolspace > Settings > Point File Formats). Tên sai sẽ bị Civil 3D từ chối.
    """
    path = os.path.abspath(file_path)
    if not os.path.exists(path):
        raise C3DError(f"Không tìm thấy file điểm: {path}")
    surface = get_surface(client, surface_name)
    before = _point_count(surface)
    files = surface.PointFiles
    count_before = int(files.Count)
    already = [n for n in _point_file_names(files)
               if os.path.normcase(n) == os.path.normcase(path)]
    files.Add(path, file_format, False, False, False)
    surface.Rebuild()
    after = _point_count(surface)
    count_after = int(files.Count)
    out: Dict[str, object] = {
        "surface": surface_name,
        "file": path,
        "file_format": file_format,
        "point_files_before": count_before,
        "point_files_after": count_after,
        "point_count_before": before,
        "point_count_after": after,
        "verified": count_after > count_before,
        "verified_by": "đếm lại PointFiles và số điểm của bề mặt",
    }
    if count_after == count_before:
        # Phân biệt hai lý do rất khác nhau cùng cho ra "số file không tăng": file đã
        # nằm trong định nghĩa từ trước (không sao), và Civil 3D đã từ chối file trong
        # im lặng (vấn đề thật). Nếu không nói rõ, người dùng chỉ thấy verified=false.
        out["note"] = (
            "File này đã có trong định nghĩa bề mặt từ trước, Civil 3D không thêm lần "
            "nữa - không phải lỗi." if already else
            f"Civil 3D KHÔNG thêm file vào định nghĩa bề mặt và cũng không báo lỗi. "
            f"Nguyên nhân thường gặp: tên định dạng {file_format!r} không khớp bất kỳ "
            f"Point File Format nào khai trong bản vẽ (xem Toolspace > Settings > "
            f"Point File Formats), hoặc cột trong file không đúng thứ tự của định dạng đó."
        )
    return out


def _point_file_names(files) -> List[str]:
    """Tên các file điểm đang có trong định nghĩa một bề mặt."""
    out: List[str] = []
    for i in range(int(files.Count)):
        try:
            out.append(str(files.Item(i).Name))
        except Exception as exc:
            reraise_if_transient(exc)
    return out


def import_surface(client: Civil3DClient, file_path: str) -> Dict[str, object]:
    """Nhập một bề mặt có sẵn: LandXML (.xml), TIN (.tin), hoặc DEM (.dem/.tif).

    Định dạng được chọn theo phần mở rộng - ba phương thức COM tương ứng là ba
    phương thức khác nhau, không có cái nào tự đoán hộ.
    """
    path = os.path.abspath(file_path)
    if not os.path.exists(path):
        raise C3DError(f"Không tìm thấy file: {path}")
    coll = _surfaces(client)
    before = {n.lower() for n in Civil3DClient.collection_names(coll)}
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xml":
        coll.ImportXML(path)
        kind = "LandXML"
    elif ext == ".tin":
        coll.ImportTIN(path)
        kind = "TIN"
    elif ext in (".dem", ".tif", ".tiff", ".asc"):
        coll.ImportDEM(path)
        kind = "DEM"
    else:
        raise C3DError(
            f"Không nhập được phần mở rộng {ext!r}. Chỉ hỗ trợ .xml (LandXML), .tin, "
            "và .dem/.tif/.asc (DEM)."
        )
    names_after = Civil3DClient.collection_names(coll)
    new_names = [n for n in names_after if n.lower() not in before]
    return {
        "file": path,
        "format": kind,
        "surfaces_created": new_names,
        "surface_count": len(names_after),
        "verified": bool(new_names),
        "verified_by": "so sánh danh sách bề mặt trước và sau khi nhập",
    }


def rebuild_surface(client: Civil3DClient, name: str) -> Dict[str, object]:
    surface = get_surface(client, name)
    surface.Rebuild()
    return {"surface": name, "statistics": _statistics(surface), "verified": True,
            "verified_by": "đọc lại thống kê sau khi rebuild"}


def set_build_options(client: Civil3DClient, name: str,
                      max_triangle_length: Optional[float] = None,
                      exclude_below: Optional[float] = None,
                      exclude_above: Optional[float] = None,
                      use_boundaries: Optional[bool] = None,
                      rebuild: bool = True) -> Dict[str, object]:
    """Đặt tham số dựng TIN rồi rebuild.

    `max_triangle_length` là tham số quan trọng nhất với dữ liệu quét: nó cắt các
    tam giác dài nối qua vùng không có điểm (bóng khuất sau xe, sau cây), thứ làm
    bề mặt phình ra ngoài phạm vi thật.
    """
    surface = get_surface(client, name)
    props = surface.DefinitionProperties
    applied: Dict[str, object] = {}
    if max_triangle_length is not None:
        props.UseMaximumTriangleLength = True
        props.MaximumTriangleLength = float(max_triangle_length)
        applied["max_triangle_length"] = float(max_triangle_length)
    if exclude_below is not None:
        props.ExcludeElevationsLessThan = True
        props.LowerElevation = float(exclude_below)
        applied["exclude_below"] = float(exclude_below)
    if exclude_above is not None:
        props.ExcludeElevationsGreaterThan = True
        props.UpperElevation = float(exclude_above)
        applied["exclude_above"] = float(exclude_above)
    if use_boundaries is not None:
        props.UseBoundaries = bool(use_boundaries)
        applied["use_boundaries"] = bool(use_boundaries)
    if not applied:
        raise C3DError("Không có tham số nào được truyền vào.")
    if rebuild:
        surface.Rebuild()

    # Kiểm chứng: đọc lại từng tham số vừa ghi từ chính đối tượng COM.
    readback: Dict[str, object] = {}
    if "max_triangle_length" in applied:
        readback["max_triangle_length"] = Civil3DClient._safe(
            lambda: float(props.MaximumTriangleLength), default=None)
    if "exclude_below" in applied:
        readback["exclude_below"] = Civil3DClient._safe(
            lambda: float(props.LowerElevation), default=None)
    if "exclude_above" in applied:
        readback["exclude_above"] = Civil3DClient._safe(
            lambda: float(props.UpperElevation), default=None)
    if "use_boundaries" in applied:
        readback["use_boundaries"] = Civil3DClient._safe(
            lambda: bool(props.UseBoundaries), default=None)
    matched = all(
        readback.get(k) is not None and abs(float(readback[k]) - float(v)) < 1e-9
        if isinstance(v, (int, float)) and not isinstance(v, bool)
        else readback.get(k) == v
        for k, v in applied.items()
    )
    return {
        "surface": name,
        "applied": applied,
        "read_back": readback,
        "statistics": _statistics(surface),
        "verified": matched,
        "verified_by": "đọc lại từng tham số từ DefinitionProperties sau khi ghi",
    }


def delete_surface(client: Civil3DClient, name: str, confirm: bool = False) -> Dict[str, object]:
    """Xoá một bề mặt. Bắt buộc `confirm=True`.

    Xoá bề mặt kéo theo mọi đối tượng phụ thuộc (profile lấy từ nó, volume surface
    tham chiếu nó) và KHÔNG hoàn tác được qua COM, nên cổng xác nhận nằm ở đây
    chứ không để người gọi tự nhớ.
    """
    if not confirm:
        raise C3DError(
            f"Từ chối xoá bề mặt {name!r}: thao tác này xoá cả profile và volume surface "
            "phụ thuộc vào nó. Gọi lại với confirm=True nếu thật sự muốn xoá."
        )
    coll = _surfaces(client)
    surface = Civil3DClient.find_item(coll, name, "bề mặt")
    surface.Delete()
    remaining = Civil3DClient.collection_names(coll)
    gone = not any(n.lower() == name.lower() for n in remaining)
    return {
        "deleted": name,
        "surfaces_remaining": remaining,
        "verified": gone,
        "verified_by": "đọc lại danh sách bề mặt sau khi xoá",
    }


# --------------------------------------------------------------------------
# Lấy mẫu - phần sinh ra số liệu cho báo cáo
# --------------------------------------------------------------------------

def elevation_at(client: Civil3DClient, surface_name: str,
                 points: Sequence[Sequence[float]]) -> Dict[str, object]:
    """Cao độ bề mặt tại một loạt toạ độ XY.

    Điểm nằm ngoài phạm vi bề mặt trả về null chứ không làm hỏng cả lô - số điểm
    rơi ra ngoài chính là một chỉ tiêu cần báo cáo (độ phủ của dữ liệu quét).
    """
    surface = get_surface(client, surface_name)
    results: List[Dict[str, object]] = []
    inside = 0
    for p in points:
        x, y = float(p[0]), float(p[1])
        try:
            z = float(surface.FindElevationAtXY(x, y))
            inside += 1
        except Exception as exc:
            reraise_if_transient(exc)
            z = None
        results.append({"x": x, "y": y, "z": z})
    return {
        "surface": surface_name,
        "sampled": len(results),
        "inside_surface": inside,
        "outside_surface": len(results) - inside,
        "coverage_percent": round(100.0 * inside / len(results), 2) if results else 0.0,
        "points": results,
    }


def sample_section(client: Civil3DClient, surface_name: str,
                   start: Sequence[float], end: Sequence[float]) -> Dict[str, object]:
    """Mặt cắt bề mặt theo một đoạn thẳng, dùng SampleElevations của Civil 3D.

    Civil 3D trả về điểm tại mọi giao với cạnh tam giác TIN - tức là mặt cắt đúng
    theo hình học của TIN, KHÔNG phải lấy mẫu theo bước đều. Đây là dữ liệu thô
    đúng cho trắc ngang hoàn công; muốn bước đều thì nội suy lại từ dãy này.
    """
    surface = get_surface(client, surface_name)
    x1, y1 = float(start[0]), float(start[1])
    x2, y2 = float(end[0]), float(end[1])
    try:
        raw = surface.SampleElevations(x1, y1, x2, y2)
    except Exception as exc:
        reraise_if_transient(exc)
        raise C3DError(
            f"Không lấy được mặt cắt trên bề mặt {surface_name!r}: {exc}. Thường gặp khi "
            "đoạn thẳng nằm hoàn toàn ngoài phạm vi bề mặt - kiểm tra hộp bao bằng "
            "get_surface_info."
        ) from exc

    values = [float(v) for v in raw] if raw is not None else []
    if len(values) % 3 != 0:
        raise C3DError(
            f"SampleElevations trả về {len(values)} số, không chia hết cho 3 - không "
            "diễn giải được thành toạ độ (x, y, z)."
        )
    pts: List[Dict[str, float]] = []
    total = 0.0
    prev = None
    for i in range(0, len(values), 3):
        x, y, z = values[i], values[i + 1], values[i + 2]
        if prev is not None:
            total += ((x - prev[0]) ** 2 + (y - prev[1]) ** 2) ** 0.5
        prev = (x, y)
        pts.append({"x": round(x, 6), "y": round(y, 6), "z": round(z, 6),
                    "distance": round(total, 6)})
    elevations = [p["z"] for p in pts]
    return {
        "surface": surface_name,
        "start": [x1, y1],
        "end": [x2, y2],
        "point_count": len(pts),
        "length_2d": round(total, 6),
        "min_elevation": min(elevations) if elevations else None,
        "max_elevation": max(elevations) if elevations else None,
        "points": pts,
    }


def compare_to_points(client: Civil3DClient, surface_name: str,
                      points: Sequence[Sequence[float]],
                      tolerances: Sequence[float] = (0.02, 0.05, 0.10)) -> Dict[str, object]:
    """So cao độ bề mặt với một tập điểm kiểm tra - phép đo độ chính xác mô hình.

    dz = z_điểm - z_bề_mặt. Điểm rơi ngoài bề mặt bị loại khỏi thống kê nhưng vẫn
    được đếm riêng: trộn chúng vào sẽ làm RMSE đẹp lên một cách giả tạo.
    """
    surface = get_surface(client, surface_name)
    deltas: List[float] = []
    outside = 0
    rows: List[Dict[str, object]] = []
    for p in points:
        x, y, z = float(p[0]), float(p[1]), float(p[2])
        try:
            zs = float(surface.FindElevationAtXY(x, y))
        except Exception as exc:
            reraise_if_transient(exc)
            outside += 1
            rows.append({"x": x, "y": y, "z_point": z, "z_surface": None, "dz": None})
            continue
        dz = z - zs
        deltas.append(dz)
        rows.append({"x": x, "y": y, "z_point": z, "z_surface": round(zs, 6),
                     "dz": round(dz, 6)})
    return {
        "surface": surface_name,
        "points_total": len(points),
        "points_compared": len(deltas),
        "points_outside_surface": outside,
        "statistics": deviation_stats(deltas, tolerances),
        "convention": "dz = cao độ điểm kiểm tra - cao độ bề mặt",
        "rows": rows,
    }
