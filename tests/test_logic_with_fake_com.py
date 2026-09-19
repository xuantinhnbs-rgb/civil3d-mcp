"""Kiểm thử phần nghiệp vụ bằng một lớp COM giả.

Mục đích: bắt được các lỗi logic mà không cần Civil 3D chạy - đặc biệt là những
chỗ mà một lời gọi COM "thành công" nhưng KHÔNG có hiệu lực. Đó chính là loại lỗi
mà kiểm thử tay trên máy có Civil 3D hay bỏ sót, vì khi mọi thứ chạy đúng thì
đường xử lý thất bại không bao giờ được đi qua.
"""

import pytest

from civil3d_mcp import alignments as al_mod
from civil3d_mcp import corridors as cor_mod
from civil3d_mcp import research as res_mod
from civil3d_mcp import surfaces as surf_mod
from civil3d_mcp.com import C3DError

# --------------------------------------------------------------------------
# Bộ khung COM giả
# --------------------------------------------------------------------------

class FakeCollection:
    """Bắt chước một collection COM: Count + Item(chỉ số hoặc tên)."""

    def __init__(self, items=None):
        self._items = list(items or [])

    @property
    def Count(self):
        return len(self._items)

    def Item(self, key):
        if isinstance(key, int):
            return self._items[key]
        for it in self._items:
            if getattr(it, "Name", None) == key:
                return it
        raise RuntimeError(f"không có phần tử tên {key!r}")

    def append(self, item):
        self._items.append(item)

    def remove(self, item):
        self._items.remove(item)


class Named:
    def __init__(self, name):
        self.Name = name


class FakeStats:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeSurface:
    """TIN surface giả: đếm điểm, trả cao độ theo một mặt phẳng nghiêng."""

    def __init__(self, name, bounds=(0, 0, 100, 100), plane=(0.0, 0.0, 10.0),
                 num_points=0, accepts_layout="flat"):
        self.Name = name
        self.Description = ""
        self.StyleName = "Standard"
        self.Layer = "0"
        self.Handle = "ABC"
        self.Type = 0
        self._bounds = bounds
        self._plane = plane                  # z = a*x + b*y + c
        self._num_points = num_points
        self._accepts = accepts_layout       # bố cục mảng mà "Civil 3D" này chấp nhận
        self.rebuild_calls = 0
        self.add_calls = []
        self.deleted = False

    # --- thống kê ---
    @property
    def Statistics(self):
        min_x, min_y, max_x, max_y = self._bounds
        a, b, c = self._plane
        corners = [a * x + b * y + c for x in (min_x, max_x) for y in (min_y, max_y)]
        return FakeStats(
            NumberOfPoints=self._num_points, NumberOfTriangles=max(0, self._num_points - 2),
            MinX=min_x, MinY=min_y, MaxX=max_x, MaxY=max_y,
            MinElevation=min(corners), MaxElevation=max(corners),
            MeanElevation=sum(corners) / 4, Area2d=(max_x - min_x) * (max_y - min_y),
            Area3d=(max_x - min_x) * (max_y - min_y), MinGrade=0.0, MaxGrade=0.1,
            MeanGrade=0.05, MaxTriangleLength=10.0, MinTriangleLength=1.0,
        )

    # --- hình học ---
    def FindElevationAtXY(self, x, y):
        min_x, min_y, max_x, max_y = self._bounds
        if not (min_x <= x <= max_x and min_y <= y <= max_y):
            raise RuntimeError("điểm nằm ngoài bề mặt")
        a, b, c = self._plane
        return a * x + b * y + c

    def SampleElevations(self, x1, y1, x2, y2):
        out = []
        for t in (0.0, 0.5, 1.0):
            x = x1 + (x2 - x1) * t
            y = y1 + (y2 - y1) * t
            out.extend([x, y, self.FindElevationAtXY(x, y)])
        return out

    # --- ghi ---
    def AddPointMultiple(self, payload):
        layout = "flat" if not isinstance(payload, list) else "nested"
        self.add_calls.append(layout)
        if layout != self._accepts:
            return                        # im lặng không làm gì - đúng như COM thật
        count = len(payload.value) // 3 if layout == "flat" else len(payload)
        self._num_points += count

    def Rebuild(self):
        self.rebuild_calls += 1

    def Delete(self):
        self.deleted = True


class FakeAeccDoc:
    def __init__(self, surfaces=(), surface_styles=("Standard",), alignments=(),
                 sites=(), profile_styles=("Standard",), alignment_styles=("Standard",),
                 label_sets=("Standard",)):
        self.Surfaces = FakeCollection(surfaces)
        self.SurfaceStyles = FakeCollection([Named(n) for n in surface_styles])
        self.AlignmentsSiteless = FakeCollection(alignments)
        self.Sites = FakeCollection(sites)
        self.ProfileStyles = FakeCollection([Named(n) for n in profile_styles])
        self.AlignmentStyles = FakeCollection([Named(n) for n in alignment_styles])
        self.AlignmentLabelStyleSets = FakeCollection([Named(n) for n in label_sets])


class FakeAcad:
    """Chỉ cần GetInterfaceObject để dựng các đối tượng CreationData."""

    def __init__(self, on_create=None):
        self._on_create = on_create

    def GetInterfaceObject(self, prog_id):
        obj = FakeCreationData()
        if self._on_create:
            self._on_create(obj)
        return obj


class FakeCreationData:
    """Ghi lại đúng những thuộc tính đã được gán.

    Civil 3D thật từ chối AddTinSurface khi thiếu BaseLayer, và chỉ báo
    "Exception occurred" - không có cách nào biết thiếu cái gì. Lớp giả này ghi
    lại để phép thử khẳng định được thuộc tính bắt buộc đó vẫn còn được gán.
    """

    def __init__(self):
        self.assigned = {}

    def __setattr__(self, key, value):
        if key != "assigned":
            self.assigned[key] = value
        object.__setattr__(self, key, value)

    def __getattr__(self, key):
        raise AttributeError(key)


class FakeConn:
    def __init__(self, acad):
        self.acad = acad
        self.version = "13.8"


class FakeClient:
    """Đủ để các hàm nghiệp vụ chạy: aecc_doc, roadway_doc, ensure_connected."""

    def __init__(self, aecc_doc=None, roadway_doc=None, acad=None):
        self._aecc = aecc_doc or FakeAeccDoc()
        self._roadway = roadway_doc
        self._conn = FakeConn(acad or FakeAcad())
        self.layers_requested = []

    @property
    def aecc_doc(self):
        return self._aecc

    @property
    def roadway_doc(self):
        if self._roadway is None:
            raise C3DError("Giao diện corridor không sẵn sàng.")
        return self._roadway

    def ensure_connected(self):
        return self._conn

    def ensure_layer(self, name):
        self.layers_requested.append(name)
        return (name or "").strip() or "0"


# --------------------------------------------------------------------------
# Tra cứu theo tên
# --------------------------------------------------------------------------

def test_tim_khong_thay_thi_liet_ke_ten_dang_co():
    client = FakeClient(FakeAeccDoc(surfaces=[FakeSurface("HOAN_CONG"),
                                              FakeSurface("THIET_KE")]))
    with pytest.raises(C3DError) as err:
        surf_mod.get_surface(client, "KHONG_CO")
    message = str(err.value)
    assert "HOAN_CONG" in message and "THIET_KE" in message


def test_tim_theo_ten_khong_phan_biet_hoa_thuong():
    client = FakeClient(FakeAeccDoc(surfaces=[FakeSurface("HoanCong")]))
    assert surf_mod.get_surface(client, "hoancong").Name == "HoanCong"


# --------------------------------------------------------------------------
# Tạo bề mặt
# --------------------------------------------------------------------------

def test_tao_be_mat_tu_choi_ten_trung():
    client = FakeClient(FakeAeccDoc(surfaces=[FakeSurface("EG")]))
    with pytest.raises(C3DError) as err:
        surf_mod.create_tin_surface(client, "eg")
    assert "đã có bề mặt" in str(err.value)


def test_tao_be_mat_bao_loi_khi_ban_ve_thieu_style():
    client = FakeClient(FakeAeccDoc(surface_styles=()))
    with pytest.raises(C3DError) as err:
        surf_mod.create_tin_surface(client, "EG")
    assert "style" in str(err.value).lower()


def test_tao_be_mat_phat_hien_lenh_chay_xong_ma_khong_co_hieu_luc():
    """AddTinSurface không ném lỗi nhưng cũng không tạo gì - phải bị bắt."""
    doc = FakeAeccDoc()
    doc.Surfaces.AddTinSurface = lambda data: None          # nuốt lặng lẽ
    client = FakeClient(doc)
    with pytest.raises(C3DError) as err:
        surf_mod.create_tin_surface(client, "EG")
    assert "không có trong bản vẽ" in str(err.value)


def test_tao_be_mat_thanh_cong_tra_ve_verified():
    doc = FakeAeccDoc()
    doc.Surfaces.AddTinSurface = lambda data: doc.Surfaces.append(FakeSurface(data.Name))
    client = FakeClient(doc)
    result = surf_mod.create_tin_surface(client, "EG", description="tu LAS")
    assert result["verified"] is True
    assert result["created"] == "EG"
    assert result["surface_count"] == 1


def test_tao_be_mat_luon_gan_BaseLayer():
    """Hồi quy: Civil 3D 2026 từ chối AddTinSurface nếu thiếu BaseLayer.

    Đo được trên máy thật: bốn tổ hợp thiếu BaseLayer đều ném E_INVALIDARG dưới
    lớp vỏ "Exception occurred"; tổ hợp có BaseLayer thì chạy. Bỏ dòng gán
    BaseLayer khỏi mã nguồn sẽ làm phép thử này đỏ.
    """
    captured = {}
    doc = FakeAeccDoc()
    doc.Surfaces.AddTinSurface = lambda data: (
        captured.update(data.assigned), doc.Surfaces.append(FakeSurface(data.Name)))
    client = FakeClient(doc)
    surf_mod.create_tin_surface(client, "EG", layer="C-TOPO")
    assert captured.get("Layer") == "C-TOPO"
    assert captured.get("BaseLayer") == "C-TOPO", "thiếu BaseLayer - Civil 3D thật sẽ từ chối"
    assert captured.get("Style")
    assert client.layers_requested == ["C-TOPO"], "layer phải đi qua ensure_layer"


def test_tao_volume_surface_cung_gan_BaseLayer():
    captured = {}
    doc = FakeAeccDoc(surfaces=[FakeSurface("A"), FakeSurface("B")])
    doc.Surfaces.AddTinVolumeSurface = lambda data: (
        captured.update(data.assigned), doc.Surfaces.append(FakeSurface(data.Name)))
    client = FakeClient(doc)
    surf_mod.create_volume_surface(client, "SS", base_surface="A", comparison_surface="B")
    assert captured.get("BaseLayer") == "0"
    assert captured.get("BaseSurface") is not None
    assert captured.get("ComparisonSurface") is not None


# --------------------------------------------------------------------------
# Nạp điểm - phần dễ "thành công" mà không có hiệu lực nhất
# --------------------------------------------------------------------------

def test_nap_diem_bo_cuc_phang_chay_thang():
    surface = FakeSurface("EG", accepts_layout="flat")
    client = FakeClient(FakeAeccDoc(surfaces=[surface]))
    points = [[i, i, i] for i in range(10)]
    result = surf_mod.add_points(client, "EG", points, chunk_size=5)
    assert result["array_layout_used"] == "flat"
    assert result["points_added_measured"] == 10
    assert result["verified"] is True
    assert surface.rebuild_calls == 1


def test_nap_diem_tu_chuyen_bo_cuc_khi_lo_dau_khong_an():
    """Civil 3D chỉ nhận mảng-của-mảng: phép thử ở lô đầu phải phát hiện ra."""
    surface = FakeSurface("EG", accepts_layout="nested")
    client = FakeClient(FakeAeccDoc(surfaces=[surface]))
    points = [[i, i, i] for i in range(6)]
    result = surf_mod.add_points(client, "EG", points, chunk_size=3)
    assert result["array_layout_used"] == "nested"
    assert result["points_added_measured"] == 6
    assert result["verified"] is True


def test_nap_diem_bao_verified_false_khi_khong_diem_nao_vao():
    surface = FakeSurface("EG", accepts_layout="khong-nhan-gi")
    client = FakeClient(FakeAeccDoc(surfaces=[surface]))
    result = surf_mod.add_points(client, "EG", [[1, 1, 1], [2, 2, 2]])
    assert result["points_added_measured"] == 0
    assert result["verified"] is False


def test_nap_diem_tu_choi_diem_thieu_cao_do():
    surface = FakeSurface("EG")
    client = FakeClient(FakeAeccDoc(surfaces=[surface]))
    with pytest.raises(C3DError) as err:
        surf_mod.add_points(client, "EG", [[1, 2]])
    assert "thiếu cao độ" in str(err.value)


def test_nap_diem_tu_choi_danh_sach_rong():
    client = FakeClient(FakeAeccDoc(surfaces=[FakeSurface("EG")]))
    with pytest.raises(C3DError):
        surf_mod.add_points(client, "EG", [])


# --------------------------------------------------------------------------
# Xoá bề mặt
# --------------------------------------------------------------------------

def test_xoa_be_mat_can_confirm():
    surface = FakeSurface("EG")
    client = FakeClient(FakeAeccDoc(surfaces=[surface]))
    with pytest.raises(C3DError) as err:
        surf_mod.delete_surface(client, "EG")
    assert "confirm=True" in str(err.value)
    assert surface.deleted is False


def test_xoa_be_mat_voi_confirm_kiem_chung_da_bien_mat():
    doc = FakeAeccDoc()
    surface = FakeSurface("EG")
    doc.Surfaces.append(surface)
    surface.Delete = lambda: doc.Surfaces.remove(surface)
    client = FakeClient(doc)
    result = surf_mod.delete_surface(client, "EG", confirm=True)
    assert result["verified"] is True
    assert result["surfaces_remaining"] == []


# --------------------------------------------------------------------------
# Lấy mẫu và so sánh
# --------------------------------------------------------------------------

def test_cao_do_tai_xy_dem_rieng_diem_ngoai_pham_vi():
    surface = FakeSurface("EG", bounds=(0, 0, 10, 10), plane=(0, 0, 5.0))
    client = FakeClient(FakeAeccDoc(surfaces=[surface]))
    result = surf_mod.elevation_at(client, "EG", [[1, 1], [5, 5], [999, 999]])
    assert result["inside_surface"] == 2
    assert result["outside_surface"] == 1
    assert result["coverage_percent"] == pytest.approx(66.67, abs=0.01)
    assert result["points"][2]["z"] is None


def test_mat_cat_tu_choi_mang_khong_chia_het_cho_ba():
    surface = FakeSurface("EG")
    surface.SampleElevations = lambda *a: [1.0, 2.0, 3.0, 4.0]
    client = FakeClient(FakeAeccDoc(surfaces=[surface]))
    with pytest.raises(C3DError) as err:
        surf_mod.sample_section(client, "EG", [0, 0], [10, 10])
    assert "không chia hết cho 3" in str(err.value)


def test_mat_cat_tinh_khoang_cach_doc_tuyen():
    surface = FakeSurface("EG", bounds=(0, 0, 100, 100), plane=(0, 0, 7.0))
    client = FakeClient(FakeAeccDoc(surfaces=[surface]))
    result = surf_mod.sample_section(client, "EG", [0, 0], [30, 40])
    assert result["point_count"] == 3
    assert result["length_2d"] == pytest.approx(50.0)       # tam giác 3-4-5
    assert result["points"][-1]["distance"] == pytest.approx(50.0)


def test_so_be_mat_voi_diem_kiem_tra_loai_diem_ngoai_pham_vi():
    # Bề mặt phẳng z = 10. Điểm kiểm tra lệch đều +0.05 và -0.05.
    surface = FakeSurface("EG", bounds=(0, 0, 100, 100), plane=(0, 0, 10.0))
    client = FakeClient(FakeAeccDoc(surfaces=[surface]))
    points = [[10, 10, 10.05], [20, 20, 9.95], [500, 500, 10.0]]
    result = surf_mod.compare_to_points(client, "EG", points, tolerances=[0.02, 0.10])
    assert result["points_compared"] == 2
    assert result["points_outside_surface"] == 1
    st = result["statistics"]
    assert st["count"] == 2
    assert st["rmse"] == pytest.approx(0.05, abs=1e-6)
    assert st["mean"] == pytest.approx(0.0, abs=1e-9)
    assert st["within_tolerance"]["<= 0.02"]["count"] == 0
    assert st["within_tolerance"]["<= 0.1"]["count"] == 2


# --------------------------------------------------------------------------
# So sánh hai bề mặt trên lưới
# --------------------------------------------------------------------------

def test_luoi_sai_lech_bat_truong_hop_hai_be_mat_khong_chong_lan():
    a = FakeSurface("A", bounds=(0, 0, 10, 10))
    b = FakeSurface("B", bounds=(1000, 1000, 1010, 1010))
    client = FakeClient(FakeAeccDoc(surfaces=[a, b]))
    with pytest.raises(C3DError) as err:
        res_mod.surface_deviation_grid(client, "A", "B", spacing=1)
    message = str(err.value)
    assert "không chồng lấn" in message
    assert "hệ toạ độ" in message           # gợi ý nguyên nhân thật hay gặp


def test_luoi_sai_lech_tinh_dung_chenh_cao_khong_doi():
    # Hai mặt phẳng song song cách nhau đúng 0,20 m.
    a = FakeSurface("HOANCONG", bounds=(0, 0, 20, 20), plane=(0, 0, 10.2))
    b = FakeSurface("THIETKE", bounds=(0, 0, 20, 20), plane=(0, 0, 10.0))
    client = FakeClient(FakeAeccDoc(surfaces=[a, b]))
    grid = res_mod.surface_deviation_grid(client, "HOANCONG", "THIETKE", spacing=5)
    assert grid["nodes_compared"] == 25          # lưới 5x5
    st = grid["statistics"]
    assert st["mean"] == pytest.approx(0.2, abs=1e-9)
    assert st["rmse"] == pytest.approx(0.2, abs=1e-9)
    assert st["std_dev"] == pytest.approx(0.0, abs=1e-9)


def test_luoi_sai_lech_dem_nut_roi_ngoai_be_mat():
    a = FakeSurface("A", bounds=(0, 0, 20, 20), plane=(0, 0, 10.0))
    b = FakeSurface("B", bounds=(0, 0, 20, 20), plane=(0, 0, 10.0))
    # B thật ra chỉ phủ nửa dưới - hộp bao nói dối, đúng như TIN có lỗ.
    b.FindElevationAtXY = lambda x, y: 10.0 if y <= 10 else (_ for _ in ()).throw(
        RuntimeError("ngoài bề mặt"))
    client = FakeClient(FakeAeccDoc(surfaces=[a, b]))
    grid = res_mod.surface_deviation_grid(client, "A", "B", spacing=5)
    assert grid["nodes_sampled"] == 25
    assert grid["nodes_compared"] == 15
    assert grid["nodes_outside_b"] == 10


# --------------------------------------------------------------------------
# Tuyến và trắc dọc
# --------------------------------------------------------------------------

class FakeAlignment:
    def __init__(self, name, start=0.0, end=100.0, profiles=()):
        self.Name = name
        self.Description = ""
        self.StyleName = "Standard"
        self.Layer = "0"
        self.Handle = "A1"
        self.StartingStation = start
        self.EndingStation = end
        self.Length = end - start
        self.ReverseStationing = False
        self.Profiles = FakeCollection(profiles)
        self.Entities = FakeCollection()
        self.SampleLineGroups = FakeCollection()
        self.ProfileViews = FakeCollection()

    def PointLocation(self, station, offset):
        if not (self.StartingStation <= station <= self.EndingStation):
            raise RuntimeError("ngoài tuyến")
        return (station, offset)

    def StationOffset(self, easting, northing):
        return (easting, northing)


class FakeProfile:
    def __init__(self, name, start=0.0, end=100.0, elevation=10.0, grade=0.025):
        self.Name = name
        self.Type = 0
        self.SampledStartingStation = start
        self.SampledEndingStation = end
        self.StartingStation = start
        self.EndingStation = end
        self.Length = end - start
        self.ElevationMin = elevation
        self.ElevationMax = elevation
        self.StyleName = "Standard"
        self._elevation = elevation
        self._grade = grade
        self.PVIs = FakeCollection()

    def ElevationAt(self, station):
        if not (self.SampledStartingStation <= station <= self.SampledEndingStation):
            raise RuntimeError("ngoài phạm vi")
        return self._elevation

    def InstantGrade(self, station):
        return self._grade


class FakeSite:
    def __init__(self, name, alignments=()):
        self.Name = name
        self.Alignments = FakeCollection(alignments)


def test_tim_tuyen_quet_ca_site_va_siteless():
    inside = FakeAlignment("TRONG_SITE")
    doc = FakeAeccDoc(alignments=[FakeAlignment("SITELESS")],
                      sites=[FakeSite("Site 1", [inside])])
    client = FakeClient(doc)
    assert al_mod.get_alignment(client, "TRONG_SITE") is inside
    assert al_mod.get_alignment(client, "SITELESS").Name == "SITELESS"


def test_tim_tuyen_khong_thay_liet_ke_ca_hai_noi():
    doc = FakeAeccDoc(alignments=[FakeAlignment("A")],
                      sites=[FakeSite("S", [FakeAlignment("B")])])
    client = FakeClient(doc)
    with pytest.raises(C3DError) as err:
        al_mod.get_alignment(client, "C")
    assert "'A'" in str(err.value) and "'B'" in str(err.value)


def test_lay_mau_trac_doc_giu_du_so_dong_ke_ca_dong_khong_co_cao_do():
    al = FakeAlignment("TUYEN", 0, 100, profiles=[FakeProfile("EG", 0, 50)])
    client = FakeClient(FakeAeccDoc(alignments=[al]))
    data = al_mod.sample_profile(client, "TUYEN", "EG", 25)
    assert len(data["rows"]) == 3                 # 0, 25, 50
    assert data["rows_with_elevation"] == 3
    assert all(r["grade"] == 0.025 for r in data["rows"])


def test_so_hai_trac_doc_bao_loi_khi_khong_co_ly_trinh_chung():
    al = FakeAlignment("TUYEN", 0, 200, profiles=[
        FakeProfile("A", 0, 50), FakeProfile("B", 100, 200)])
    client = FakeClient(FakeAeccDoc(alignments=[al]))
    with pytest.raises(C3DError) as err:
        al_mod.compare_profiles(client, "TUYEN", "A", "B", 10)
    assert "không có đoạn lý trình chung" in str(err.value)


def test_so_hai_trac_doc_tinh_dung_chenh_cao():
    al = FakeAlignment("TUYEN", 0, 100, profiles=[
        FakeProfile("HOANCONG", 0, 100, elevation=10.03),
        FakeProfile("THIETKE", 0, 100, elevation=10.00)])
    client = FakeClient(FakeAeccDoc(alignments=[al]))
    result = al_mod.compare_profiles(client, "TUYEN", "HOANCONG", "THIETKE", 50)
    assert result["compared"] == 3
    assert result["statistics"]["mean"] == pytest.approx(0.03, abs=1e-9)
    assert result["statistics"]["rmse"] == pytest.approx(0.03, abs=1e-9)


def test_tao_tuyen_luon_truyen_toa_do_ba_thanh_phan():
    """Hồi quy: AddFixedLine1 từ chối mảng 2 phần tử với E_INVALIDARG.

    Đo được trên máy thật: cùng một cặp điểm, dạng 2D hỏng và dạng 3D chạy.
    """
    captured = []

    class Entities(FakeCollection):
        def AddFixedLine1(self, p1, p2):
            captured.append((list(p1.value), list(p2.value)))
            self.append(object())

    al = FakeAlignment("T")
    al.Entities = Entities()
    al.Length = 100.0
    doc = FakeAeccDoc()
    doc.AlignmentsSiteless.Add = lambda *args: al
    client = FakeClient(doc)

    result = al_mod.create_alignment(client, "T", [[0, 20], [100, 20]])
    assert len(captured) == 1
    for p1, p2 in captured:
        assert len(p1) == 3 and len(p2) == 3, "toạ độ 2D sẽ bị Civil 3D thật từ chối"
    assert result["verified"] is True


def test_tao_tuyen_bao_doan_nao_hong_va_tuyen_dang_do_dang():
    class Entities(FakeCollection):
        def AddFixedLine1(self, p1, p2):
            if self.Count >= 1:
                raise RuntimeError("The parameter is incorrect.")
            self.append(object())

    al = FakeAlignment("T")
    al.Entities = Entities()
    doc = FakeAeccDoc()
    doc.AlignmentsSiteless.Add = lambda *args: al
    client = FakeClient(doc)
    with pytest.raises(C3DError) as err:
        al_mod.create_alignment(client, "T", [[0, 0], [10, 0], [20, 0]])
    message = str(err.value)
    assert "đoạn thứ 2" in message
    assert "CHƯA hoàn chỉnh" in message        # nói rõ bản vẽ đang ở trạng thái dở


def test_tao_tuyen_can_it_nhat_hai_diem():
    client = FakeClient(FakeAeccDoc())
    with pytest.raises(C3DError):
        al_mod.create_alignment(client, "T", [[0, 0]])


def test_tao_tuyen_tu_choi_ten_trung():
    client = FakeClient(FakeAeccDoc(alignments=[FakeAlignment("T")]))
    with pytest.raises(C3DError) as err:
        al_mod.create_alignment(client, "t", [[0, 0], [10, 0]])
    assert "đã có tuyến" in str(err.value)


def test_lay_mau_tuyen_giu_ly_trinh_cuoi():
    al = FakeAlignment("TUYEN", 0, 130)
    client = FakeClient(FakeAeccDoc(alignments=[al]))
    result = al_mod.sample_alignment(client, "TUYEN", 50)
    stations = [r["station"] for r in result["results"]]
    assert stations == [0, 50, 100, 130]


# --------------------------------------------------------------------------
# Corridor
# --------------------------------------------------------------------------

class FakeRoadwayDoc:
    def __init__(self, corridors=(), assemblies=()):
        self.Corridors = FakeCollection(corridors)
        self.Assemblies = FakeCollection([Named(n) for n in assemblies])
        self.Subassemblies = FakeCollection()


def test_tao_corridor_bao_loi_neu_assembly_khong_co():
    al = FakeAlignment("TUYEN", profiles=[FakeProfile("FG")])
    client = FakeClient(FakeAeccDoc(alignments=[al]),
                        roadway_doc=FakeRoadwayDoc(assemblies=["Assembly - (1)"]))
    with pytest.raises(C3DError) as err:
        cor_mod.create_corridor(client, "COR", "TUYEN", "FG", "KHONG_CO")
    assert "Assembly - (1)" in str(err.value)


def test_tao_corridor_bao_loi_neu_trac_doc_khong_thuoc_tuyen():
    al = FakeAlignment("TUYEN", profiles=[FakeProfile("FG")])
    client = FakeClient(FakeAeccDoc(alignments=[al]),
                        roadway_doc=FakeRoadwayDoc(assemblies=["ASM"]))
    with pytest.raises(C3DError) as err:
        cor_mod.create_corridor(client, "COR", "TUYEN", "SAI_TEN", "ASM")
    assert "trắc dọc" in str(err.value)


def test_liet_ke_assembly_rong_kem_ghi_chu_giai_thich():
    client = FakeClient(FakeAeccDoc(), roadway_doc=FakeRoadwayDoc())
    result = cor_mod.list_assemblies(client)
    assert result["count"] == 0
    assert "COM không tạo được assembly" in result["note"]


def test_corridor_bao_loi_ro_khi_giao_dien_roadway_thieu():
    client = FakeClient(FakeAeccDoc())          # không truyền roadway_doc
    with pytest.raises(C3DError) as err:
        cor_mod.list_corridors(client)
    assert "corridor" in str(err.value).lower()


# --------------------------------------------------------------------------
# Profile view: bộ band phải là một thành viên CÓ THẬT
# --------------------------------------------------------------------------

class FakeProfileViews(FakeCollection):
    """ProfileViews.Add của Civil 3D thật từ chối chuỗi rỗng ở tham số bộ band."""

    def __init__(self, band_sets=()):
        super().__init__()
        self._band_sets = set(band_sets)
        self.add_calls = []

    def Add(self, name, layer, origin, style, band_set):
        self.add_calls.append((name, layer, style, band_set))
        if band_set not in self._band_sets:
            raise RuntimeError("The parameter is incorrect.")
        self.append(Named(name))


def _doc_with_band_sets(alignment, band_sets=("_No Bands", "Stations Only")):
    doc = FakeAeccDoc(alignments=[alignment])
    doc.ProfileViewStyles = FakeCollection([Named("Full Grid")])
    doc.ProfileViewBandStyleSets = FakeCollection([Named(n) for n in band_sets])
    return doc


def test_profile_view_khong_bao_gio_gui_chuoi_rong_lam_bo_band():
    # Chuỗi rỗng là cách nói "không cần band" tự nhiên nhất và là cách Civil 3D 2026
    # từ chối bằng E_INVALIDARG. Nghĩa "không có" nằm ở một thành viên CÓ TÊN.
    al = FakeAlignment("TUYEN")
    al.ProfileViews = FakeProfileViews(band_sets=("_No Bands", "Stations Only"))
    client = FakeClient(_doc_with_band_sets(al))
    result = al_mod.create_profile_view(client, "TUYEN", "PV", [0.0, 0.0])
    assert result["verified"] is True
    assert result["band_set"] == "_No Bands"
    assert al.ProfileViews.add_calls[0][3] == "_No Bands"


def test_profile_view_bao_loi_ro_khi_ten_bo_band_khong_co_trong_ban_ve():
    al = FakeAlignment("TUYEN")
    al.ProfileViews = FakeProfileViews(band_sets=("_No Bands",))
    client = FakeClient(_doc_with_band_sets(al, band_sets=("_No Bands",)))
    with pytest.raises(C3DError) as err:
        al_mod.create_profile_view(client, "TUYEN", "PV", [0.0, 0.0],
                                   band_set="Khong Ton Tai")
    assert "_No Bands" in str(err.value)


def test_profile_view_dung_bo_band_dau_tien_khi_khong_co_ten_trung_tinh():
    al = FakeAlignment("TUYEN")
    al.ProfileViews = FakeProfileViews(band_sets=("Stations Only", "Cut and Fill"))
    client = FakeClient(_doc_with_band_sets(al, band_sets=("Stations Only", "Cut and Fill")))
    result = al_mod.create_profile_view(client, "TUYEN", "PV", [0.0, 0.0])
    assert result["band_set"] == "Stations Only"


# --------------------------------------------------------------------------
# Assemblies: collection rỗng bị từ chối, không trả về collection rỗng
# --------------------------------------------------------------------------

class FakeRoadwayDocNoAssemblies:
    """Civil 3D 2026 từ chối CHÍNH property Assemblies khi bản vẽ chưa có assembly."""

    def __init__(self):
        self.Corridors = FakeCollection()
        self.Subassemblies = FakeCollection()

    @property
    def Assemblies(self):
        raise RuntimeError("The parameter is incorrect.")


def test_liet_ke_assembly_dich_loi_tu_choi_collection_thanh_so_khong():
    client = FakeClient(FakeAeccDoc(), roadway_doc=FakeRoadwayDocNoAssemblies())
    result = cor_mod.list_assemblies(client)
    assert result["count"] == 0
    assert result["collection_unavailable"] is True
    assert "chưa có assembly" in result["note"]


# --------------------------------------------------------------------------
# Corridor vừa tạo chưa xuất hiện ngay trong collection
# --------------------------------------------------------------------------

class LaggingCorridors(FakeCollection):
    """Collection mô phỏng độ trễ: Add xong nhưng n lần đọc đầu vẫn chưa thấy."""

    def __init__(self, lag):
        super().__init__()
        self._lag = lag
        self._pending = None

    def Add(self, name, alignment, profile, assembly):
        self._pending = FakeCorridor(name)

    @property
    def Count(self):
        if self._pending is not None:
            if self._lag > 0:
                self._lag -= 1
            else:
                self.append(self._pending)
                self._pending = None
        return len(self._items)


class FakeCorridor:
    def __init__(self, name):
        self.Name = name
        self.Description = ""
        self.OutOfDate = True
        self.Baselines = FakeCollection([object()])


def _client_for_corridor(lag):
    al = FakeAlignment("TUYEN", profiles=[FakeProfile("FG")])
    rd = FakeRoadwayDoc(assemblies=["ASM"])
    rd.Corridors = LaggingCorridors(lag)
    return FakeClient(FakeAeccDoc(alignments=[al]), roadway_doc=rd)


def test_tao_corridor_thu_lai_khi_collection_chua_kip_hien():
    # Kiểm chứng đọc quá sớm báo hỏng một thao tác đã thành công; người dùng sẽ tạo
    # lại và sinh ra corridor thứ hai. Phép thử này đỏ nếu bỏ vòng thử lại.
    client = _client_for_corridor(lag=3)
    result = cor_mod.create_corridor(client, "COR", "TUYEN", "FG", "ASM")
    assert result["created"] == "COR"
    assert result["verified"] is True


def test_tao_corridor_bao_loi_neu_cho_mai_khong_thay(monkeypatch):
    monkeypatch.setattr(cor_mod, "CREATE_LOOKUP_DELAY", 0.0)
    client = _client_for_corridor(lag=999)
    with pytest.raises(C3DError) as err:
        cor_mod.create_corridor(client, "COR", "TUYEN", "FG", "ASM")
    assert "tạo lại" in str(err.value)
