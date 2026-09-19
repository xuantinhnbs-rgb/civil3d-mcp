"""Kiểm thử lớp COM: dịch lỗi, phân loại lỗi tạm thời, sắp xếp phiên bản.

Phần dịch lỗi quan trọng hơn vẻ ngoài của nó: nếu một lỗi "Civil 3D đang bận" bị
xếp sai thành lỗi vĩnh viễn thì cơ chế thử lại mất tác dụng, và người dùng nhận
lại một thông điệp sai về nguyên nhân.
"""

import pytest
import pythoncom
from civil3d_mcp import com as com_mod
from civil3d_mcp.com import C3DError


def make_com_error(hresult: int, description: str = "") -> pythoncom.com_error:
    """Dựng một com_error giống thật: (hresult, mô tả chung, EXCEPINFO, argerr)."""
    excepinfo = (0, "Civil 3D", description, None, 0, 0) if description else None
    return pythoncom.com_error(hresult, "mô tả chung", excepinfo, None)


# --------------------------------------------------------------------------
# Phân loại
# --------------------------------------------------------------------------

def test_loi_ban_duoc_nhan_dien():
    exc = make_com_error(-2147418111)          # RPC_E_CALL_REJECTED
    assert com_mod.is_busy(exc) is True
    assert com_mod.is_dead(exc) is False
    assert "đang bận" in com_mod.explain(exc)
    assert "ESC" in com_mod.explain(exc)


def test_loi_mat_ket_noi_duoc_nhan_dien():
    exc = make_com_error(-2147417848)          # RPC_E_DISCONNECTED
    assert com_mod.is_dead(exc) is True
    assert com_mod.is_busy(exc) is False
    assert "Mất kết nối" in com_mod.explain(exc)


def test_loi_khong_co_phuong_thuc_goi_y_lech_phien_ban():
    exc = make_com_error(-2147352570, "Unknown name: AddTinSurfaceEx")
    message = com_mod.explain(exc)
    assert "không có thuộc tính/phương thức" in message
    assert "phiên bản" in message              # gợi ý nguyên nhân thật hay gặp


def test_loi_civil3d_tu_choi_giu_nguyen_mo_ta_goc():
    exc = make_com_error(-2147352567, "Surface style not found")
    assert "Surface style not found" in com_mod.explain(exc)


def test_loi_khong_ro_van_tra_ve_thong_diep_co_hresult():
    exc = make_com_error(-1234567)
    assert "-1234567" in com_mod.explain(exc)


def test_hresult_cua_ngoai_le_thuong_la_none():
    assert com_mod.hresult(ValueError("không phải lỗi COM")) is None


# --------------------------------------------------------------------------
# reraise_if_transient - cái chốt giữ cho cơ chế thử lại còn hoạt động
# --------------------------------------------------------------------------

def test_reraise_nem_lai_loi_ban():
    exc = make_com_error(-2147418111)
    with pytest.raises(pythoncom.com_error):
        com_mod.reraise_if_transient(exc)


def test_reraise_nem_lai_attribute_error():
    with pytest.raises(AttributeError):
        com_mod.reraise_if_transient(AttributeError("<unknown>.Surfaces"))


def test_reraise_im_lang_voi_loi_that_su_la_khong_ton_tai():
    # Lỗi này KHÔNG tạm thời, phải để phía gọi dịch thành "không tìm thấy X".
    com_mod.reraise_if_transient(make_com_error(-2147352567, "Key not found"))
    com_mod.reraise_if_transient(RuntimeError("không có phần tử"))


# --------------------------------------------------------------------------
# Sắp xếp phiên bản
# --------------------------------------------------------------------------

def test_sap_xep_phien_ban_theo_so_khong_theo_chuoi():
    versions = ["13.8", "13.10", "13.9", "12.4"]
    ordered = sorted(versions, key=com_mod._version_sort_key, reverse=True)
    # So chuỗi sẽ cho "13.9" > "13.10"; so số mới ra đúng thứ tự.
    assert ordered[0] == "13.10"
    assert ordered == ["13.10", "13.9", "13.8", "12.4"]


def test_sap_xep_phien_ban_khong_gay_voi_hau_to_la():
    assert com_mod._version_sort_key("13.x") == (13, -1)


# --------------------------------------------------------------------------
# Dò cài đặt trên máy thật (không cần Civil 3D đang chạy)
# --------------------------------------------------------------------------

def test_do_phien_ban_tu_registry_khong_nem_loi():
    versions = com_mod.discover_civil3d_versions()
    assert isinstance(versions, list)
    assert all(isinstance(v, str) for v in versions)


def test_do_duong_dan_cai_dat_khong_nem_loi():
    installs = com_mod.discover_install_paths()
    assert isinstance(installs, list)
    for item in installs:
        assert "product" in item and "location" in item


def test_file_version_tra_none_voi_duong_dan_khong_ton_tai():
    assert com_mod.file_version("C:/khong/co/file.exe") is None


def test_c3derror_la_exception():
    with pytest.raises(C3DError):
        raise C3DError("thông điệp")


# --------------------------------------------------------------------------
# Bận khác với chưa chạy
# --------------------------------------------------------------------------

def _stub_all_attach_paths(monkeypatch, raiser):
    """Chặn CẢ HAI đường bám: win32com và comtypes.

    Chỉ chặn một đường là phép thử vẫn bám được vào phiên Civil 3D thật đang chạy
    trên máy - và khi đó nó không kiểm tra gì cả, chỉ trông như đã kiểm tra.
    """
    monkeypatch.setattr(com_mod.win32com.client, "GetActiveObject", raiser)
    monkeypatch.setattr(com_mod, "RETRY_DELAY", 0.0)
    try:
        import comtypes.client
        monkeypatch.setattr(comtypes.client, "GetActiveObject", raiser)
    except ImportError:
        pass


def test_bam_that_bai_vi_ban_duoc_phan_biet_voi_chua_chay(monkeypatch):
    """Hai nguyên nhân cho cùng một triệu chứng, hai kết luận trái ngược.

    Civil 3D đang bận từ chối GetActiveObject cho MỌI ProgID, y như khi chưa có
    phiên nào. Nếu không phân biệt, người dùng được khuyên đi mở thêm một phiên
    thứ hai - đúng việc KHÔNG nên làm.
    """
    busy = make_com_error(-2147418111)

    def always_busy(prog_id):
        raise busy

    _stub_all_attach_paths(monkeypatch, always_busy)
    result = com_mod.attach_autocad()
    assert result.app is None
    assert result.busy_seen is True


def test_bam_that_bai_vi_chua_chay_thi_khong_bao_ban(monkeypatch):
    def not_running(prog_id):
        raise make_com_error(-2147221021)      # MK_E_UNAVAILABLE - không có trong ROT

    _stub_all_attach_paths(monkeypatch, not_running)
    result = com_mod.attach_autocad()
    assert result.app is None
    assert result.busy_seen is False


def test_bam_thanh_cong_tra_ve_dung_doi_tuong(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(com_mod.win32com.client, "GetActiveObject", lambda p: sentinel)
    result = com_mod.attach_autocad()
    assert result.app is sentinel
    app, errors = result            # vẫn giải nén được như tuple
    assert app is sentinel and errors == []


def test_read_optional_bo_qua_thuoc_tinh_khong_co_nhung_nem_lai_loi_ban(monkeypatch):
    monkeypatch.setattr(com_mod.time, "sleep", lambda s: None)

    def missing():
        raise AttributeError("<unknown>.CutVolume")

    assert com_mod.read_optional(missing, default="vang") == "vang"

    def busy():
        raise make_com_error(-2147418111)

    with pytest.raises(pythoncom.com_error):
        com_mod.read_optional(busy)
