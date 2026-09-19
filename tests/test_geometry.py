"""Kiểm thử phần tính toán thuần Python.

Mọi kỳ vọng ở đây đều tính tay được, nên khi một phép thử đỏ lên thì lỗi nằm ở
code chứ không phải ở một giá trị tham chiếu ai đó chép từ lần chạy trước.
"""

import math

import pytest

from civil3d_mcp import geometry as geo

# --------------------------------------------------------------------------
# chunk
# --------------------------------------------------------------------------

def test_chunk_chia_du_va_giu_nguyen_thu_tu():
    data = list(range(10))
    lots = list(geo.chunk(data, 3))
    assert [list(x) for x in lots] == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]
    assert sum(len(x) for x in lots) == len(data)


def test_chunk_tu_choi_kich_thuoc_khong_duong():
    with pytest.raises(ValueError):
        list(geo.chunk([1, 2, 3], 0))


# --------------------------------------------------------------------------
# station_range
# --------------------------------------------------------------------------

def test_station_range_chia_het():
    assert geo.station_range(0, 100, 25) == [0, 25, 50, 75, 100]


def test_station_range_khong_chia_het_van_giu_ly_trinh_cuoi():
    # 0..100 bước 30 cho 0/30/60/90; lý trình cuối 100 là dữ liệu bắt buộc của
    # một trắc dọc nên phải còn lại.
    result = geo.station_range(0, 100, 30)
    assert result == [0, 30, 60, 90, 100]


def test_station_range_khong_nhan_doi_ly_trinh_cuoi():
    result = geo.station_range(0, 90, 30)
    assert result == [0, 30, 60, 90]
    assert len(result) == len(set(result))


def test_station_range_dao_nguoc_dau_cuoi():
    assert geo.station_range(100, 0, 50) == [0, 50, 100]


def test_format_station():
    assert geo.format_station(1234.567) == "Km1+234.567"
    assert geo.format_station(0) == "Km0+000.000"


# --------------------------------------------------------------------------
# deviation_stats
# --------------------------------------------------------------------------

def test_deviation_stats_tinh_tay_duoc():
    values = [-2.0, -1.0, 0.0, 1.0, 2.0]
    st = geo.deviation_stats(values)
    assert st["count"] == 5
    assert st["mean"] == 0.0
    # RMSE quanh 0: sqrt((4+1+0+1+4)/5) = sqrt(2)
    assert st["rmse"] == pytest.approx(math.sqrt(2), abs=1e-6)
    # MAE = (2+1+0+1+2)/5
    assert st["mae"] == pytest.approx(1.2, abs=1e-9)
    # Độ lệch chuẩn mẫu (n-1): sqrt(10/4)
    assert st["std_dev"] == pytest.approx(math.sqrt(2.5), abs=1e-6)
    assert st["min"] == -2.0 and st["max"] == 2.0
    assert st["median"] == 0.0


def test_deviation_stats_rmse_khac_std_khi_co_bias():
    # Bias thuần: mọi sai lệch đều bằng 1. Std = 0 nhưng RMSE = 1.
    st = geo.deviation_stats([1.0] * 10)
    assert st["std_dev"] == pytest.approx(0.0, abs=1e-12)
    assert st["rmse"] == pytest.approx(1.0, abs=1e-12)


def test_deviation_stats_dung_sai():
    st = geo.deviation_stats([0.01, 0.03, 0.07, 0.2], tolerances=[0.02, 0.05])
    within = st["within_tolerance"]
    assert within["<= 0.02"]["count"] == 1
    assert within["<= 0.05"]["count"] == 2
    assert within["<= 0.05"]["percent"] == 50.0


def test_deviation_stats_tap_rong():
    st = geo.deviation_stats([])
    assert st["count"] == 0
    assert "note" in st


def test_deviation_stats_bo_qua_gia_tri_khong_hop_le():
    st = geo.deviation_stats([1.0, None, float("nan"), -1.0])
    assert st["count"] == 2


# --------------------------------------------------------------------------
# percentile
# --------------------------------------------------------------------------

def test_percentile_noi_suy_tuyen_tinh():
    data = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert geo.percentile(data, 0) == 0.0
    assert geo.percentile(data, 100) == 4.0
    assert geo.percentile(data, 50) == 2.0
    # k = 4 * 0.25 = 1.0 -> đúng phần tử thứ 1
    assert geo.percentile(data, 25) == 1.0


def test_percentile_mot_phan_tu():
    assert geo.percentile([7.0], 95) == 7.0


# --------------------------------------------------------------------------
# hình học phẳng
# --------------------------------------------------------------------------

def test_bbox():
    b = geo.bbox([[0, 0], [10, 5], [-3, 7]])
    assert b == {"min_x": -3, "min_y": 0, "max_x": 10, "max_y": 7}


def test_bbox_rong():
    assert geo.bbox([]) is None


def test_polygon_area_hinh_vuong():
    assert geo.polygon_area([[0, 0], [10, 0], [10, 10], [0, 10]]) == pytest.approx(100.0)


def test_polygon_area_khong_phu_thuoc_chieu_quay():
    cw = [[0, 0], [0, 10], [10, 10], [10, 0]]
    assert geo.polygon_area(cw) == pytest.approx(100.0)


def test_grid_points_dem_dung():
    bounds = {"min_x": 0, "min_y": 0, "max_x": 10, "max_y": 10}
    pts = geo.grid_points(bounds, 5)
    assert len(pts) == 9          # 3 cột x 3 hàng
    assert (0.0, 0.0) in pts and (10.0, 10.0) in pts


def test_grid_points_co_bien():
    bounds = {"min_x": 0, "min_y": 0, "max_x": 10, "max_y": 10}
    pts = geo.grid_points(bounds, 5, inset=2)
    assert all(2 <= x <= 8 and 2 <= y <= 8 for x, y in pts)


def test_grid_points_co_bien_qua_lon_tra_ve_rong():
    bounds = {"min_x": 0, "min_y": 0, "max_x": 10, "max_y": 10}
    assert geo.grid_points(bounds, 1, inset=6) == []


def test_point_in_polygon():
    square = [[0, 0], [10, 0], [10, 10], [0, 10]]
    assert geo.point_in_polygon(5, 5, square) is True
    assert geo.point_in_polygon(15, 5, square) is False
    assert geo.point_in_polygon(0, 5, square) is True       # nằm trên cạnh


def test_point_in_polygon_hinh_lom():
    # Hình chữ L: điểm trong phần bị khoét phải nằm ngoài.
    l_shape = [[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10]]
    assert geo.point_in_polygon(2, 2, l_shape) is True
    assert geo.point_in_polygon(8, 8, l_shape) is False


# --------------------------------------------------------------------------
# đọc / ghi file
# --------------------------------------------------------------------------

def test_read_xyz_nhieu_dau_phan_cach(tmp_path):
    f = tmp_path / "diem.csv"
    f.write_text("X,Y,Z\n1.5,2.5,3.5\n4;5;6\n7 8 9\n\n# ghi chu\n", encoding="utf-8")
    pts = geo.read_xyz(str(f))
    assert pts == [(1.5, 2.5, 3.5), (4.0, 5.0, 6.0), (7.0, 8.0, 9.0)]


def test_read_xyz_gioi_han_so_diem(tmp_path):
    f = tmp_path / "diem.txt"
    f.write_text("\n".join(f"{i},{i},{i}" for i in range(100)), encoding="utf-8")
    assert len(geo.read_xyz(str(f), max_points=10)) == 10


def test_read_xyz_file_khong_ton_tai(tmp_path):
    with pytest.raises(FileNotFoundError):
        geo.read_xyz(str(tmp_path / "khong-co.csv"))


def test_write_csv_bao_cao_so_dong_that(tmp_path):
    out = tmp_path / "sub" / "ket_qua.csv"
    info = geo.write_csv(str(out), ["a", "b"], [[1, 2], [3, 4], [5, 6]])
    assert info["rows_written"] == 3
    assert info["bytes"] > 0
    text = out.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == "a,b"
    assert len(text.strip().splitlines()) == 4     # 1 tiêu đề + 3 dòng
