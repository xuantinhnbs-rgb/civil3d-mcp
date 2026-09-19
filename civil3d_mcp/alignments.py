"""
Tuyến (alignment), trắc dọc (profile) và khung nhìn trắc dọc (profile view)
===========================================================================
Bước thứ hai của quy trình Scan-to-BIM: từ bề mặt hoàn công dựng tim tuyến, rồi
cắt bề mặt theo tim tuyến để ra trắc dọc.

Một chi tiết dễ sai về nơi lưu tuyến: alignment có thể nằm trong một Site hoặc
nằm ngoài mọi Site ("siteless"). Bản vẽ hạ tầng giao thông gần như luôn dùng
siteless, nhưng bản vẽ do người khác gửi tới thì không chắc - vì vậy mọi hàm
liệt kê ở đây đều quét CẢ HAI chỗ, còn hàm tạo mới thì tạo ở siteless.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from .client import Civil3DClient, variant_point
from .com import C3DError, reraise_if_transient
from .geometry import station_range

# CẢNH BÁO chung cho mọi enum của Civil 3D COM: phần lớn đánh số TỪ 1, không từ 0.
# Truyền 0 vào một tham số enum bị từ chối bằng E_INVALIDARG chứ không phải một lỗi
# nói rõ "giá trị enum sai", nên nhầm gốc đánh số rất khó truy. Các giá trị dưới đây
# đọc trực tiếp từ type library của bản 13.8, không suy đoán.
PROFILE_TYPE_EG = 1     # aeccExistingGround - bám bề mặt
PROFILE_TYPE_FG = 2     # aeccFinishedGround - thiết kế
PROFILE_TYPES = {1: "existing_ground", 2: "finished_grade", 3: "superimposed"}

# AeccAlignmentEntityType, cũng đánh số từ 1.
ENTITY_TYPES = {
    1: "tangent", 2: "arc", 3: "spiral",
    4: "spiral-curve-spiral", 5: "spiral-tangent-spiral", 6: "spiral-tangent",
    7: "tangent-spiral", 8: "spiral-curve", 9: "curve-spiral",
    10: "spiral-spiral-curve", 11: "curve-spiral-spiral",
    12: "spiral-curve-spiral-curve-spiral",
    13: "spiral-curve-spiral-spiral-curve-spiral", 14: "spiral-spiral",
}


# --------------------------------------------------------------------------
# Tra cứu
# --------------------------------------------------------------------------

def _iter_alignment_collections(client: Civil3DClient):
    """Sinh ra mọi collection alignment trong bản vẽ: siteless trước, rồi từng Site."""
    doc = client.aecc_doc
    yield "siteless", doc.AlignmentsSiteless
    sites = doc.Sites
    for i in range(int(sites.Count)):
        site = sites.Item(i)
        try:
            yield str(site.Name), site.Alignments
        except Exception as exc:
            reraise_if_transient(exc)


def list_alignments(client: Civil3DClient) -> Dict[str, object]:
    items: List[Dict[str, object]] = []
    for site_name, coll in _iter_alignment_collections(client):
        for i in range(int(coll.Count)):
            al = coll.Item(i)
            items.append({
                "name": str(al.Name),
                "site": site_name,
                "start_station": _safe_float(al, "StartingStation"),
                "end_station": _safe_float(al, "EndingStation"),
                "length": _safe_float(al, "Length"),
                "style": Civil3DClient._safe(lambda a=al: str(a.StyleName)),
                "layer": Civil3DClient._safe(lambda a=al: str(a.Layer)),
                "handle": Civil3DClient._safe(lambda a=al: str(a.Handle)),
                "profiles": Civil3DClient._safe(lambda a=al: int(a.Profiles.Count), default=None),
            })
    return {"count": len(items), "alignments": items}


def get_alignment(client: Civil3DClient, name: str):
    wanted = str(name).strip().lower()
    available: List[str] = []
    for _site, coll in _iter_alignment_collections(client):
        for i in range(int(coll.Count)):
            al = coll.Item(i)
            n = str(al.Name)
            available.append(n)
            if n.lower() == wanted:
                return al
    raise C3DError(
        f"Không có tuyến (alignment) tên {name!r}. Đang có: "
        + (", ".join(repr(n) for n in available[:20]) if available else "(chưa có tuyến nào)")
    )


def _safe_float(obj, attr: str) -> Optional[float]:
    try:
        return round(float(getattr(obj, attr)), 6)
    except Exception:
        return None


def alignment_info(client: Civil3DClient, name: str,
                   include_entities: bool = True) -> Dict[str, object]:
    al = get_alignment(client, name)
    out: Dict[str, object] = {
        "name": str(al.Name),
        "description": Civil3DClient._safe(lambda: str(al.Description)),
        "start_station": _safe_float(al, "StartingStation"),
        "end_station": _safe_float(al, "EndingStation"),
        "length": _safe_float(al, "Length"),
        "style": Civil3DClient._safe(lambda: str(al.StyleName)),
        "layer": Civil3DClient._safe(lambda: str(al.Layer)),
        "handle": Civil3DClient._safe(lambda: str(al.Handle)),
        "reverse_stationing": Civil3DClient._safe(lambda: bool(al.ReverseStationing), default=None),
    }
    if include_entities:
        ents = al.Entities
        rows: List[Dict[str, object]] = []
        for i in range(int(ents.Count)):
            e = ents.Item(i)
            row: Dict[str, object] = {
                "index": i,
                "id": Civil3DClient._safe(lambda x=e: int(x.Id), default=None),
                "type": ENTITY_TYPES.get(
                    Civil3DClient._safe(lambda x=e: int(x.Type), default=-1), "unknown"),
            }
            for attr in ("Length", "Radius", "StartStation", "EndStation"):
                value = _safe_float(e, attr)
                if value is not None:
                    row[attr.lower()] = value
            rows.append(row)
        out["entities"] = rows
        out["entity_count"] = len(rows)
    profiles = al.Profiles
    out["profiles"] = [str(profiles.Item(i).Name) for i in range(int(profiles.Count))]
    return out


# --------------------------------------------------------------------------
# Tạo tuyến
# --------------------------------------------------------------------------

def _default_alignment_styles(client: Civil3DClient) -> Dict[str, str]:
    doc = client.aecc_doc
    styles = doc.AlignmentStyles
    label_sets = doc.AlignmentLabelStyleSets
    if int(styles.Count) == 0 or int(label_sets.Count) == 0:
        raise C3DError(
            "Bản vẽ thiếu style tuyến hoặc bộ nhãn tuyến. Hãy dùng template Civil 3D "
            "thay vì bản vẽ AutoCAD trắng."
        )
    return {"style": str(styles.Item(0).Name), "label_set": str(label_sets.Item(0).Name)}


def create_alignment(client: Civil3DClient, name: str,
                     points: Sequence[Sequence[float]],
                     layer: Optional[str] = None,
                     style: Optional[str] = None,
                     label_set: Optional[str] = None,
                     description: Optional[str] = None) -> Dict[str, object]:
    """Tạo tuyến từ một dãy đỉnh, nối bằng các đoạn thẳng cố định.

    Chỉ sinh ra tuyến đa tuyến thẳng (không chèn đường cong). Với tim tuyến hoàn
    công lấy từ đám mây điểm thì đây thường là dạng đúng để bắt đầu: cong hoá và
    khớp bán kính là bước thiết kế, cần quyết định của người kỹ sư, không nên để
    tool tự ý làm thay.
    """
    if len(points) < 2:
        raise C3DError("Cần ít nhất 2 điểm để tạo tuyến.")
    doc = client.aecc_doc
    coll = doc.AlignmentsSiteless
    if any(n.lower() == name.lower() for n in Civil3DClient.collection_names(coll)):
        raise C3DError(f"Bản vẽ đã có tuyến tên {name!r}.")

    defaults = _default_alignment_styles(client)
    al = coll.Add(
        name,
        client.ensure_layer(layer),
        style or defaults["style"],
        label_set or defaults["label_set"],
    )
    if description:
        try:
            al.Description = description
        except Exception as exc:
            reraise_if_transient(exc)

    ents = al.Entities
    added = 0
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        try:
            # Toạ độ phải đủ BA thành phần. Mảng 2 phần tử bị Civil 3D từ chối với
            # E_INVALIDARG - đã đo được trên Civil 3D 2026 (COM 13.8): cùng một cặp
            # điểm, dạng 2D hỏng và dạng 3D chạy.
            ents.AddFixedLine1(
                variant_point(a[0], a[1], a[2] if len(a) > 2 else 0.0),
                variant_point(b[0], b[1], b[2] if len(b) > 2 else 0.0),
            )
        except Exception as exc:
            reraise_if_transient(exc)
            raise C3DError(
                f"Tạo được tuyến {name!r} nhưng thất bại ở đoạn thứ {i + 1} "
                f"({list(a)[:2]} -> {list(b)[:2]}): {exc}. Tuyến hiện có "
                f"{int(ents.Count)} đoạn và CHƯA hoàn chỉnh - hãy xoá nó trước khi thử lại."
            ) from exc
        added += 1

    entity_count = int(ents.Count)
    length = _safe_float(al, "Length")
    # Kiểm chứng bằng CHIỀU DÀI chứ không bằng Entities.Count: đo được trên Civil 3D
    # 2026 rằng Count còn trả 0 ngay sau khi thêm đoạn, rồi mới đúng ở lần đọc sau,
    # trong khi Length đã đúng ngay. Một phép kiểm chứng đọc quá sớm còn tệ hơn không
    # kiểm chứng: nó báo hỏng một thao tác đã thành công.
    return {
        "created": name,
        "segments_requested": added,
        "entity_count": entity_count,
        "start_station": _safe_float(al, "StartingStation"),
        "end_station": _safe_float(al, "EndingStation"),
        "length": length,
        "verified": length is not None and length > 0.0,
        "verified_by": "đọc lại chiều dài tuyến sau khi thêm đủ các đoạn",
        "entity_count_note": ("Entities.Count có thể còn là 0 ngay sau khi tạo; đọc lại "
                              "bằng get_alignment_info sẽ ra số đúng."),
    }


def create_alignment_from_polyline(client: Civil3DClient, name: str, handle: str,
                                   layer: Optional[str] = None,
                                   style: Optional[str] = None,
                                   label_set: Optional[str] = None,
                                   add_curves: bool = False,
                                   erase_polyline: bool = False) -> Dict[str, object]:
    """Tạo tuyến từ một polyline đã có trong bản vẽ, định danh bằng handle AutoCAD."""
    doc = client.aecc_doc
    adoc = client.acad_doc
    try:
        entity = adoc.HandleToObject(handle)
    except Exception as exc:
        reraise_if_transient(exc)
        raise C3DError(f"Không tìm thấy đối tượng có handle {handle!r} trong bản vẽ.") from exc

    coll = doc.AlignmentsSiteless
    if any(n.lower() == name.lower() for n in Civil3DClient.collection_names(coll)):
        raise C3DError(f"Bản vẽ đã có tuyến tên {name!r}.")
    defaults = _default_alignment_styles(client)
    al = coll.AddFromPolylineEx(
        name,
        client.ensure_layer(layer),
        entity,
        style or defaults["style"],
        label_set or defaults["label_set"],
        bool(add_curves),
        bool(erase_polyline),
    )
    length = _safe_float(al, "Length")
    return {
        "created": name,
        "source_handle": handle,
        "length": length,
        "start_station": _safe_float(al, "StartingStation"),
        "end_station": _safe_float(al, "EndingStation"),
        "entity_count": Civil3DClient._safe(lambda: int(al.Entities.Count), default=None),
        "verified": length not in (None, 0.0),
        "verified_by": "đọc lại chiều dài tuyến vừa tạo",
    }


# --------------------------------------------------------------------------
# Quy đổi toạ độ <-> lý trình
# --------------------------------------------------------------------------

def station_offset(client: Civil3DClient, name: str,
                   points: Sequence[Sequence[float]]) -> Dict[str, object]:
    """Đổi toạ độ (Đông, Bắc) sang (lý trình, khoảng cách lệch tim) trên một tuyến."""
    al = get_alignment(client, name)
    rows: List[Dict[str, object]] = []
    for p in points:
        easting, northing = float(p[0]), float(p[1])
        try:
            station, offset = al.StationOffset(easting, northing)
            rows.append({"easting": easting, "northing": northing,
                         "station": round(float(station), 6),
                         "offset": round(float(offset), 6)})
        except Exception as exc:
            reraise_if_transient(exc)
            rows.append({"easting": easting, "northing": northing,
                         "station": None, "offset": None,
                         "note": "điểm không chiếu được lên tuyến"})
    return {"alignment": name, "count": len(rows), "results": rows,
            "convention": "offset dương là bên phải theo chiều tăng lý trình"}


def point_location(client: Civil3DClient, name: str,
                   stations: Sequence[float], offset: float = 0.0) -> Dict[str, object]:
    """Đổi (lý trình, offset) sang toạ độ (Đông, Bắc) trên một tuyến."""
    al = get_alignment(client, name)
    rows: List[Dict[str, object]] = []
    for st in stations:
        st = float(st)
        try:
            easting, northing = al.PointLocation(st, float(offset))
            rows.append({"station": st, "offset": float(offset),
                         "easting": round(float(easting), 6),
                         "northing": round(float(northing), 6)})
        except Exception as exc:
            reraise_if_transient(exc)
            rows.append({"station": st, "offset": float(offset),
                         "easting": None, "northing": None,
                         "note": "lý trình nằm ngoài phạm vi tuyến"})
    return {"alignment": name, "count": len(rows), "results": rows}


def sample_alignment(client: Civil3DClient, name: str, interval: float,
                     offset: float = 0.0) -> Dict[str, object]:
    """Rải điểm đều theo lý trình dọc tuyến - đầu vào cho mọi phép cắt ngang."""
    al = get_alignment(client, name)
    start = float(al.StartingStation)
    end = float(al.EndingStation)
    stations = station_range(start, end, float(interval))
    result = point_location(client, name, stations, offset)
    result.update({"start_station": round(start, 6), "end_station": round(end, 6),
                   "interval": float(interval)})
    return result


# --------------------------------------------------------------------------
# Trắc dọc
# --------------------------------------------------------------------------

def list_profiles(client: Civil3DClient, alignment: str) -> Dict[str, object]:
    al = get_alignment(client, alignment)
    coll = al.Profiles
    rows: List[Dict[str, object]] = []
    for i in range(int(coll.Count)):
        pr = coll.Item(i)
        rows.append({
            "name": str(pr.Name),
            "type": PROFILE_TYPES.get(
                Civil3DClient._safe(lambda p=pr: int(p.Type), default=-1), "unknown"),
            "start_station": _safe_float(pr, "StartingStation"),
            "end_station": _safe_float(pr, "EndingStation"),
            "length": _safe_float(pr, "Length"),
            "min_elevation": _safe_float(pr, "ElevationMin"),
            "max_elevation": _safe_float(pr, "ElevationMax"),
            "surface": Civil3DClient._safe(lambda p=pr: str(p.Surface.Name), default=None),
            "pvi_count": Civil3DClient._safe(lambda p=pr: int(p.PVIs.Count), default=None),
            "style": Civil3DClient._safe(lambda p=pr: str(p.StyleName)),
        })
    return {"alignment": alignment, "count": len(rows), "profiles": rows}


def get_profile(client: Civil3DClient, alignment: str, profile: str):
    al = get_alignment(client, alignment)
    return Civil3DClient.find_item(al.Profiles, profile, "trắc dọc (profile)")


def create_profile_from_surface(client: Civil3DClient, alignment: str, surface: str,
                                name: Optional[str] = None,
                                style: Optional[str] = None,
                                start_station: Optional[float] = None,
                                end_station: Optional[float] = None,
                                layer: Optional[str] = None) -> Dict[str, object]:
    """Cắt bề mặt theo tim tuyến để ra trắc dọc tự nhiên.

    Đây là bước nối bề mặt hoàn công với tuyến: trắc dọc sinh ra ở đây là dữ liệu
    thô để so sánh với trắc dọc thiết kế.
    """
    al = get_alignment(client, alignment)
    doc = client.aecc_doc
    surf = Civil3DClient.find_item(doc.Surfaces, surface, "bề mặt")
    profile_name = name or f"{alignment} - {surface} (EG)"
    coll = al.Profiles
    if any(n.lower() == profile_name.lower() for n in Civil3DClient.collection_names(coll)):
        raise C3DError(f"Tuyến {alignment!r} đã có trắc dọc tên {profile_name!r}.")

    # LandProfileStyles mới là style trắc dọc của Civil 3D. AeccDocument còn có một
    # collection tên ProfileStyles thuộc tầng AEC nền, và nó LUÔN rỗng trong bản vẽ
    # Civil 3D - lấy nhầm nó sẽ báo "bản vẽ không có style" trên một template đủ style.
    styles = doc.LandProfileStyles
    if int(styles.Count) == 0:
        raise C3DError("Bản vẽ không có style trắc dọc nào - hãy dùng template Civil 3D.")
    style_name = style or str(styles.Item(0).Name)
    st = float(start_station) if start_station is not None else float(al.StartingStation)
    en = float(end_station) if end_station is not None else float(al.EndingStation)

    # Tham số Surface phải là TÊN bề mặt dạng chuỗi, KHÔNG phải đối tượng bề mặt -
    # dù type library khai nó là một tham số kiểu bề mặt. Truyền đối tượng bị từ chối
    # bằng E_INVALIDARG; đã đo được sáu biến thể trên Civil 3D 2026 (COM 13.8) và chỉ
    # biến thể truyền tên chạy được. Vẫn tra đối tượng ở trên để tên sai bị bắt sớm
    # kèm danh sách tên có thật, thay vì nhận lại một lỗi COM chung chung.
    coll.AddFromSurface(profile_name, PROFILE_TYPE_EG, style_name, str(surf.Name), st, en,
                        client.ensure_layer(layer))

    pr = Civil3DClient.find_item(coll, profile_name, "trắc dọc")
    length = _safe_float(pr, "Length")
    return {
        "created": profile_name,
        "alignment": alignment,
        "surface": surface,
        "sampled_start": _safe_float(pr, "SampledStartingStation"),
        "sampled_end": _safe_float(pr, "SampledEndingStation"),
        "length": length,
        "min_elevation": _safe_float(pr, "ElevationMin"),
        "max_elevation": _safe_float(pr, "ElevationMax"),
        "verified": length not in (None, 0.0),
        "verified_by": "đọc lại chiều dài và biên độ cao độ của trắc dọc vừa tạo",
    }


def sample_profile(client: Civil3DClient, alignment: str, profile: str,
                   interval: float) -> Dict[str, object]:
    """Bảng lý trình - cao độ - độ dốc của một trắc dọc, bước đều.

    Đây là dạng dữ liệu đưa thẳng được vào bảng trong báo cáo hoặc vào so sánh
    thiết kế/hoàn công. Lý trình nằm ngoài phạm vi lấy mẫu trả về null thay vì bị
    bỏ đi, để số dòng luôn khớp với dãy lý trình yêu cầu.
    """
    pr = get_profile(client, alignment, profile)
    start = float(pr.SampledStartingStation)
    end = float(pr.SampledEndingStation)
    rows: List[Dict[str, object]] = []
    valid = 0
    for st in station_range(start, end, float(interval)):
        row: Dict[str, object] = {"station": st}
        try:
            row["elevation"] = round(float(pr.ElevationAt(st)), 6)
            valid += 1
        except Exception as exc:
            reraise_if_transient(exc)
            row["elevation"] = None
        try:
            row["grade"] = round(float(pr.InstantGrade(st)), 8)
        except Exception as exc:
            reraise_if_transient(exc)
            row["grade"] = None
        rows.append(row)
    return {
        "alignment": alignment,
        "profile": profile,
        "start_station": round(start, 6),
        "end_station": round(end, 6),
        "interval": float(interval),
        "rows": rows,
        "rows_with_elevation": valid,
        "grade_unit": "tỉ số (0.025 = 2,5%)",
    }


def compare_profiles(client: Civil3DClient, alignment: str, profile_a: str,
                     profile_b: str, interval: float,
                     tolerances: Sequence[float] = (0.02, 0.05, 0.10)) -> Dict[str, object]:
    """So hai trắc dọc trên cùng một tuyến theo bước lý trình đều.

    dz = cao độ A - cao độ B. Dùng cho đối chiếu hoàn công (A) với thiết kế (B):
    kết quả là bảng sai lệch cao độ theo lý trình, kèm thống kê RMSE.
    """
    from .geometry import deviation_stats

    pa = get_profile(client, alignment, profile_a)
    pb = get_profile(client, alignment, profile_b)
    start = max(float(pa.SampledStartingStation), float(pb.SampledStartingStation))
    end = min(float(pa.SampledEndingStation), float(pb.SampledEndingStation))
    if end <= start:
        raise C3DError(
            f"Hai trắc dọc không có đoạn lý trình chung: {profile_a!r} phủ "
            f"[{pa.SampledStartingStation:.3f}, {pa.SampledEndingStation:.3f}], "
            f"{profile_b!r} phủ [{pb.SampledStartingStation:.3f}, {pb.SampledEndingStation:.3f}]."
        )

    rows: List[Dict[str, object]] = []
    deltas: List[float] = []
    for st in station_range(start, end, float(interval)):
        za = _elevation_or_none(pa, st)
        zb = _elevation_or_none(pb, st)
        dz = round(za - zb, 6) if (za is not None and zb is not None) else None
        if dz is not None:
            deltas.append(dz)
        rows.append({"station": st, "elevation_a": za, "elevation_b": zb, "dz": dz})
    return {
        "alignment": alignment,
        "profile_a": profile_a,
        "profile_b": profile_b,
        "overlap_start": round(start, 6),
        "overlap_end": round(end, 6),
        "interval": float(interval),
        "compared": len(deltas),
        "statistics": deviation_stats(deltas, tolerances),
        "convention": f"dz = cao độ {profile_a!r} - cao độ {profile_b!r}",
        "rows": rows,
    }


def _elevation_or_none(profile, station: float) -> Optional[float]:
    try:
        return round(float(profile.ElevationAt(float(station))), 6)
    except Exception as exc:
        reraise_if_transient(exc)
        return None


BAND_SET_NONE = "_No Bands"


def _band_set_names(client: Civil3DClient) -> List[str]:
    """Tên các bộ band của profile view.

    Tên collection là `ProfileViewBandStyleSets`, KHÔNG phải `ProfileViewBandSetStyles`
    như trực giác đặt tên của Civil 3D gợi ý; `ProfileViewBandStyles` có tồn tại nhưng
    không đọc được `Count`, nên nó không dùng được để dò.
    """
    try:
        coll = client.aecc_doc.ProfileViewBandStyleSets
        return [str(coll.Item(i).Name) for i in range(int(coll.Count))]
    except Exception as exc:
        reraise_if_transient(exc)
        return []


def create_profile_view(client: Civil3DClient, alignment: str, name: str,
                        origin: Sequence[float],
                        style: Optional[str] = None,
                        layer: Optional[str] = None,
                        band_set: Optional[str] = None) -> Dict[str, object]:
    """Dựng khung nhìn trắc dọc tại một điểm chèn - phần nhìn được của trắc dọc."""
    al = get_alignment(client, alignment)
    doc = client.aecc_doc
    views = al.ProfileViews
    if any(n.lower() == name.lower() for n in Civil3DClient.collection_names(views)):
        raise C3DError(f"Tuyến {alignment!r} đã có profile view tên {name!r}.")
    styles = doc.ProfileViewStyles
    if int(styles.Count) == 0:
        raise C3DError("Bản vẽ không có style profile view nào - hãy dùng template Civil 3D.")

    # ProfileViews.Add đòi một bộ band CÓ THẬT. Chuỗi rỗng - cách tự nhiên để nói
    # "không cần band" - bị từ chối bằng E_INVALIDARG, y hệt lỗi thiếu BaseLayer, nên
    # thông điệp không chỉ ra được tham số nào sai. Đo được trên Civil 3D 2026 (COM 13.8).
    available = _band_set_names(client)
    if band_set is None:
        band_set = BAND_SET_NONE if BAND_SET_NONE in available else (
            available[0] if available else "")
    elif available and band_set not in available:
        raise C3DError(
            f"Bản vẽ không có bộ band tên {band_set!r}. Các bộ đang có: {available}."
        )
    if not band_set:
        raise C3DError(
            "Bản vẽ không có bộ band profile view nào (ProfileViewBandStyleSets rỗng) - "
            "ProfileViews.Add sẽ bị từ chối. Hãy dùng bản vẽ dựng từ template Civil 3D."
        )

    origin_pt = variant_point(origin[0], origin[1],
                              origin[2] if len(origin) > 2 else 0.0)
    views.Add(name, client.ensure_layer(layer), origin_pt,
              style or str(styles.Item(0).Name), band_set)
    created = any(n.lower() == name.lower() for n in Civil3DClient.collection_names(views))
    return {
        "created": name,
        "alignment": alignment,
        "origin": [float(v) for v in origin],
        "band_set": band_set,
        "profile_view_count": int(views.Count),
        "verified": created,
        "verified_by": "đọc lại danh sách profile view của tuyến",
    }
