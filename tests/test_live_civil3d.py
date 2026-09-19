"""Kiểm thử tích hợp trên Civil 3D THẬT - có chọn mới chạy.

Chạy:
    set CIVIL3D_LIVE_TEST=1
    python -m pytest tests/test_live_civil3d.py -v

Không đặt biến môi trường đó thì cả file được bỏ qua, nên `pytest tests` trên máy
CI hoặc trên máy không có Civil 3D vẫn xanh.

Cách kiểm chứng: dựng dữ liệu tổng hợp có ĐÁP SỐ TÍNH TAY ĐƯỢC, rồi so kết quả
Civil 3D trả về với đáp số đó. Hai bề mặt là hai mặt phẳng song song cách nhau
đúng 0,05 m trên diện 100 x 40 m, nên:

    RMSE chênh cao độ = 0,050 m        (chênh không đổi -> std = 0)
    khối lượng đắp    = 100 * 40 * 0,05 = 200 m3
    độ dốc dọc        = 0,02

LƯU Ý khi đọc kết quả: pytest bật faulthandler, nên nếu Civil 3D đang bận đúng lúc
máy chủ bám vào nó, bạn sẽ thấy một khối "Windows fatal exception: code 0x80010001"
kèm stack trace in ra giữa kết quả. Đó là ngoại lệ SEH first-chance mà pywin32 bắt và
xử lý bình thường (mã đó là RPC_E_CALL_REJECTED - "đang bận"), KHÔNG phải sự cố. Cứ
đọc dòng tổng kết của pytest ở cuối.

"Chạy không lỗi" KHÔNG phải tiêu chí ở đây: mọi bước đều phải ra đúng con số.
Chính bộ thử này đã phát hiện tám khác biệt giữa khai báo COM và hành vi thật của
Civil 3D 2026 - xem mục "Khác biệt so với tài liệu COM" trong README.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CIVIL3D_LIVE_TEST") != "1",
    reason="Cần Civil 3D đang chạy; đặt CIVIL3D_LIVE_TEST=1 để bật.",
)

TEMPLATE_CANDIDATES = [
    os.path.expandvars(
        r"%LOCALAPPDATA%\Autodesk\C3D 2026\enu\Template"
        r"\_Autodesk Civil 3D (Metric) NCS.dwt"),
    os.path.expandvars(
        r"%LOCALAPPDATA%\Autodesk\C3D 2025\enu\Template"
        r"\_Autodesk Civil 3D (Metric) NCS.dwt"),
]

# Mặt "thiết kế": z = 10 + 0,02x trên lưới 100 x 40 m. Mặt "hoàn công" cao hơn 0,05 m.
DESIGN = [[x, y, 10.0 + 0.02 * x] for x in range(0, 101, 5) for y in range(0, 41, 5)]
ASBUILT = [[x, y, z + 0.05] for x, y, z in DESIGN]


@pytest.fixture(scope="module")
def tools():
    from civil3d_mcp import server
    status = server.check_civil3d_connection()
    if not status.get("ok"):
        pytest.skip(f"Civil 3D chưa sẵn sàng: {status.get('error')}")
    return server


@pytest.fixture(scope="module")
def drawing(tools):
    """Một bản vẽ mới từ template, dùng chung cho cả module.

    Tạo bản vẽ mới thay vì mở file .dwt: mở template rồi làm việc trên đó là đang
    sửa template của máy.
    """
    template = next((p for p in TEMPLATE_CANDIDATES if os.path.exists(p)), None)
    if template is None:
        pytest.skip("Không tìm thấy template Civil 3D bản Metric.")
    result = tools.create_new_drawing(template)
    assert result["ok"], result
    return result["created"]


@pytest.fixture(scope="module")
def surfaces(tools, drawing):
    """Hai bề mặt tổng hợp đã nạp điểm và rebuild."""
    for name, points in (("T_THIETKE", DESIGN), ("T_HOANCONG", ASBUILT)):
        created = tools.create_tin_surface(name, description=f"kiểm thử {name}")
        assert created["ok"], created
        assert created["verified"] is True
        loaded = tools.add_points_to_surface(name, points)
        assert loaded["ok"], loaded
        assert loaded["points_added_measured"] == len(points)
        assert loaded["verified"] is True
    return "T_THIETKE", "T_HOANCONG"


# --------------------------------------------------------------------------
# Kết nối
# --------------------------------------------------------------------------

def test_ket_noi_bao_dung_phien_ban(tools):
    status = tools.check_civil3d_connection()
    assert status["ok"] is True
    assert status["installed"] is True
    assert status["com_version_active"] in status["com_versions_installed"]
    assert status["roadway_interface"] is True


def test_ban_ghi_moi_truong_co_phien_ban_exe(tools):
    env = tools.get_environment_report()
    assert env["ok"] is True
    assert env["civil3d_installations"], "phải tìm được ít nhất một bản cài"
    assert env["civil3d_installations"][0]["executable_version"], \
        "phải đọc được số hiệu acad.exe - trường này là bắt buộc trong báo cáo"


def test_ban_ve_moi_la_he_met(tools, drawing):
    info = tools.get_drawing_info()
    assert info["ok"] is True
    assert info["measurement"] == "metric"


# --------------------------------------------------------------------------
# Bề mặt: kết quả phải khớp đáp số giải tích
# --------------------------------------------------------------------------

def test_be_mat_bao_dung_loai_TIN(tools, surfaces):
    listing = tools.list_surfaces()
    assert listing["ok"] is True
    kinds = {s["name"]: s["type"] for s in listing["surfaces"]}
    for name in surfaces:
        # Enum AeccSurfaceType đánh số từ 1; đánh số từ 0 sẽ báo nhầm TIN thành volume.
        assert kinds[name] == "TIN", f"{name} bị báo là {kinds[name]!r}"


def test_mat_cat_cho_dung_do_doc_hai_phan_tram(tools, surfaces):
    design = surfaces[0]
    section = tools.sample_surface_section(design, [0, 20], [100, 20])
    assert section["ok"] is True
    assert section["length_2d"] == pytest.approx(100.0, abs=1e-6)
    z0 = section["points"][0]["z"]
    z1 = section["points"][-1]["z"]
    assert (z1 - z0) / section["length_2d"] == pytest.approx(0.02, abs=1e-9)


def test_diem_kiem_tra_cho_dung_RMSE(tools, surfaces):
    design = surfaces[0]
    checks = [[x, 20.0, 10.0 + 0.02 * x + 0.05] for x in (10, 30, 50, 70, 90)]
    result = tools.compare_surface_to_check_points(design, checks)
    assert result["ok"] is True
    assert result["points_compared"] == 5
    assert result["points_outside_surface"] == 0
    stats = result["statistics"]
    assert stats["rmse"] == pytest.approx(0.05, abs=1e-6)
    assert stats["std_dev"] == pytest.approx(0.0, abs=1e-6)


def test_luoi_so_hai_be_mat_cho_dung_chenh_cao(tools, surfaces):
    design, asbuilt = surfaces
    grid = tools.compare_two_surfaces(asbuilt, design, spacing=10, edge_inset=5)
    assert grid["ok"] is True
    assert grid["nodes_compared"] > 0
    assert grid["nodes_outside_a"] == 0 and grid["nodes_outside_b"] == 0
    assert grid["statistics"]["mean"] == pytest.approx(0.05, abs=1e-6)
    assert grid["statistics"]["std_dev"] == pytest.approx(0.0, abs=1e-6)


def test_volume_surface_cho_dung_khoi_luong_dap(tools, surfaces):
    design, asbuilt = surfaces
    result = tools.create_volume_surface("T_SO_SANH", base_surface=design,
                                         comparison_surface=asbuilt)
    assert result["ok"] is True, result
    assert result["verified"] is True
    volumes = result["volumes"]
    # 100 m x 40 m x 0,05 m = 200 m3, toàn bộ là đắp.
    assert volumes["fill_volume"] == pytest.approx(200.0, abs=0.5)
    assert volumes["cut_volume"] == pytest.approx(0.0, abs=0.5)
    # Tên bề mặt phải là tên thật, không phải chuỗi "<COMObject ...>".
    assert "COMObject" not in str(volumes.get("base_surface"))


# --------------------------------------------------------------------------
# Tuyến và trắc dọc
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def alignment(tools, surfaces):
    result = tools.create_alignment("T_TIM", [[0, 20], [100, 20]])
    assert result["ok"], result
    assert result["verified"] is True
    assert result["length"] == pytest.approx(100.0, abs=1e-6)
    return "T_TIM"


def test_trac_doc_bam_dung_be_mat(tools, alignment, surfaces):
    design, asbuilt = surfaces
    for profile_name, surface in (("T_PR_TK", design), ("T_PR_HC", asbuilt)):
        created = tools.create_profile_from_surface(alignment, surface, name=profile_name)
        assert created["ok"], created
        assert created["verified"] is True

    sampled = tools.sample_profile(alignment, "T_PR_TK", 25)
    assert sampled["ok"] is True
    rows = sampled["rows"]
    assert len(rows) == 5
    for row in rows:
        expected = 10.0 + 0.02 * row["station"]
        assert row["elevation"] == pytest.approx(expected, abs=1e-6)


def test_so_hai_trac_doc_cho_dung_chenh_cao(tools, alignment, surfaces):
    result = tools.compare_profiles(alignment, "T_PR_HC", "T_PR_TK", 20)
    assert result["ok"] is True, result
    assert result["compared"] > 0
    assert result["statistics"]["mean"] == pytest.approx(0.05, abs=1e-6)


def test_xuat_trac_doc_ra_csv(tools, alignment, tmp_path):
    out = tmp_path / "trac_doc.csv"
    result = tools.export_profile_csv(alignment, "T_PR_TK", str(out), 10)
    assert result["ok"] is True
    assert result["verified"] is True
    assert result["file"]["rows_written"] == 11
    assert out.exists()


# --------------------------------------------------------------------------
# Trắc ngang
# --------------------------------------------------------------------------

def test_trac_ngang_tao_duoc_va_doc_ra_so_lieu(tools, alignment, surfaces):
    group = "T_SL"
    created = tools.create_sample_lines(alignment, group, interval=25,
                                        left_width=15, right_width=15)
    assert created["ok"], created
    assert created["sample_lines_created"] == 5
    # AddAllSurfaces thêm bề mặt nhưng để cờ Sample TẮT; nếu không bật thì bước sau
    # chạy trót lọt mà không sinh section nào.
    assert created["sampled_surfaces_enabled"] > 0

    sections = tools.create_sections(alignment, group)
    assert sections["ok"], sections
    assert sections["sections_total"] > 0, sections.get("diagnosis")
    assert sections["verified"] is True

    data = tools.read_sections(alignment, group, offset_interval=5)
    assert data["ok"] is True
    assert data["row_count"] > 0
    at_zero = [r for r in data["rows"]
               if r["station"] == pytest.approx(0.0) and r["surface"] == "T_THIETKE"]
    assert at_zero, "phải đọc được trắc ngang của bề mặt thiết kế tại lý trình 0"
    for row in at_zero:
        assert row["elevation"] == pytest.approx(10.0, abs=1e-6)


# --------------------------------------------------------------------------
# Bộ sản phẩm báo cáo
# --------------------------------------------------------------------------

def test_xuat_bo_san_pham_so_sanh(tools, surfaces, tmp_path):
    design, asbuilt = surfaces
    out_dir = tmp_path / "bao_cao"
    result = tools.export_surface_comparison(asbuilt, design, str(out_dir),
                                             spacing=10, edge_inset=5)
    assert result["ok"] is True, result
    assert result["verified"] is True
    assert os.path.exists(result["grid_csv"]["path"])
    assert os.path.exists(result["report"]["path"])
    report = open(result["report"]["path"], encoding="utf-8").read()
    # Báo cáo phải ghi lại điều kiện sinh số liệu, không chỉ con số.
    assert "Bước lưới" in report
    assert "Số nút so sánh được" in report
    assert "Môi trường phần mềm" in report
    assert "rmse" in report.lower()
