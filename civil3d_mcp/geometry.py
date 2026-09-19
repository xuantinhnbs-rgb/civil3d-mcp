"""
Phép tính thuần Python dùng chung cho các tool Civil 3D
=======================================================
Mọi thứ trong module này chạy được mà KHÔNG cần Civil 3D: chia lô dữ liệu, sinh
dãy lý trình, thống kê sai lệch, đọc/ghi CSV. Tách ra như vậy để phần tính toán
- phần trực tiếp sinh ra số liệu trong báo cáo nghiên cứu - kiểm thử được bằng
dữ liệu giải tích, không phụ thuộc một phiên Civil 3D đang mở.
"""

from __future__ import annotations

import csv
import math
import os
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

Point2 = Tuple[float, float]
Point3 = Tuple[float, float, float]


# --------------------------------------------------------------------------
# Chia lô
# --------------------------------------------------------------------------

def chunk(items: Sequence, size: int) -> Iterator[Sequence]:
    """Cắt một dãy thành các lô không quá `size` phần tử.

    Dùng cho mọi lời gọi COM nhận mảng: một SAFEARRAY quá lớn không báo lỗi tử tế
    mà làm Civil 3D treo hoặc trả về lỗi không nói lên điều gì, nên phía gọi phải
    tự đặt trần thay vì tin rằng "truyền hết một lần cũng được".
    """
    if size <= 0:
        raise ValueError("size phải là số nguyên dương")
    for i in range(0, len(items), size):
        yield items[i:i + size]


# --------------------------------------------------------------------------
# Lý trình
# --------------------------------------------------------------------------

def station_range(start: float, end: float, interval: float,
                  include_end: bool = True) -> List[float]:
    """Dãy lý trình từ `start` tới `end`, bước `interval`.

    Luôn giữ lý trình cuối khi `include_end` (mặc định): mặt cắt tại lý trình kết
    thúc là dữ liệu bắt buộc của một trắc dọc, và nếu chiều dài tuyến không chia
    hết cho bước thì vòng lặp thuần tuý sẽ đánh rơi đúng điểm đó.
    """
    if interval <= 0:
        raise ValueError("interval phải là số dương")
    if end < start:
        start, end = end, start
    out: List[float] = []
    n = int(math.floor((end - start) / interval + 1e-9))
    for i in range(n + 1):
        out.append(round(start + i * interval, 6))
    if include_end and (not out or abs(out[-1] - end) > 1e-6):
        out.append(round(end, 6))
    return out


def format_station(station: float, decimals: int = 3) -> str:
    """Định dạng lý trình kiểu Km: 1234.567 -> 'Km1+234.567'."""
    km = int(station // 1000)
    rest = station - km * 1000
    # Phần dư luôn chiếm đúng 3 chữ số phần nguyên, cộng dấu chấm và phần thập phân.
    width = 3 + (decimals + 1 if decimals else 0)
    return f"Km{km}+{rest:0{width}.{decimals}f}"


# --------------------------------------------------------------------------
# Thống kê sai lệch
# --------------------------------------------------------------------------

def deviation_stats(values: Sequence[float],
                    tolerances: Optional[Sequence[float]] = None) -> Dict[str, object]:
    """Thống kê một tập sai lệch (đơn vị theo bản vẽ, thường là mét).

    Trả về đủ các đại lượng mà một báo cáo kiểm định độ chính xác cần: n, trung
    bình (bias), độ lệch chuẩn, RMSE, MAE, min/max, và các phân vị. RMSE tính
    quanh 0 (sai số tuyệt đối so với chuẩn), KHÔNG quanh trung bình - đây là hai
    số khác nhau và lẫn lộn chúng là lỗi phổ biến khi báo cáo độ chính xác.
    """
    vals = [float(v) for v in values if v is not None and not _is_nan(v)]
    n = len(vals)
    if n == 0:
        return {"count": 0, "note": "không có giá trị hợp lệ nào để thống kê"}

    mean = sum(vals) / n
    # Độ lệch chuẩn mẫu (n-1): tập sai lệch là một mẫu, không phải toàn bộ tổng thể.
    if n > 1:
        variance = sum((v - mean) ** 2 for v in vals) / (n - 1)
    else:
        variance = 0.0
    std = math.sqrt(variance)
    rmse = math.sqrt(sum(v * v for v in vals) / n)
    mae = sum(abs(v) for v in vals) / n
    ordered = sorted(vals)

    stats: Dict[str, object] = {
        "count": n,
        "mean": round(mean, 6),
        "std_dev": round(std, 6),
        "rmse": round(rmse, 6),
        "mae": round(mae, 6),
        "min": round(ordered[0], 6),
        "max": round(ordered[-1], 6),
        "median": round(percentile(ordered, 50), 6),
        "p90_abs": round(percentile(sorted(abs(v) for v in vals), 90), 6),
        "p95_abs": round(percentile(sorted(abs(v) for v in vals), 95), 6),
        "rmse_note": "RMSE tính quanh 0 (sai số so với chuẩn), không quanh trung bình",
    }
    if tolerances:
        within: Dict[str, object] = {}
        for tol in tolerances:
            tol = float(tol)
            hit = sum(1 for v in vals if abs(v) <= tol)
            within[f"<= {tol:g}"] = {"count": hit, "percent": round(100.0 * hit / n, 2)}
        stats["within_tolerance"] = within
    return stats


def percentile(ordered: Sequence[float], pct: float) -> float:
    """Phân vị theo nội suy tuyến tính trên một dãy ĐÃ SẮP XẾP tăng dần."""
    data = list(ordered)
    if not data:
        raise ValueError("dãy rỗng")
    if len(data) == 1:
        return float(data[0])
    k = (len(data) - 1) * (pct / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(data[int(k)])
    return float(data[lo]) * (hi - k) + float(data[hi]) * (k - lo)


def _is_nan(value) -> bool:
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True


# --------------------------------------------------------------------------
# Hình học phẳng
# --------------------------------------------------------------------------

def bbox(points: Iterable[Sequence[float]]) -> Optional[Dict[str, float]]:
    """Hộp bao 2D của một tập điểm. None nếu tập rỗng."""
    xs: List[float] = []
    ys: List[float] = []
    for p in points:
        xs.append(float(p[0]))
        ys.append(float(p[1]))
    if not xs:
        return None
    return {"min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys)}


def polygon_area(points: Sequence[Sequence[float]]) -> float:
    """Diện tích đa giác theo công thức dây giày. Trả về trị tuyệt đối."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for i in range(len(points)):
        x1, y1 = float(points[i][0]), float(points[i][1])
        x2, y2 = float(points[(i + 1) % len(points)][0]), float(points[(i + 1) % len(points)][1])
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def grid_points(bounds: Dict[str, float], spacing: float,
                inset: float = 0.0) -> List[Point2]:
    """Lưới điểm đều phủ một hộp bao, dùng để lấy mẫu chênh cao giữa hai bề mặt.

    `inset` co hộp bao vào trong trước khi rải lưới: mép TIN surface là nơi phép
    nội suy kém tin cậy nhất, nên khi đánh giá độ chính xác thường phải loại một
    dải biên thay vì để nó kéo lệch thống kê.
    """
    if spacing <= 0:
        raise ValueError("spacing phải là số dương")
    min_x = bounds["min_x"] + inset
    min_y = bounds["min_y"] + inset
    max_x = bounds["max_x"] - inset
    max_y = bounds["max_y"] - inset
    if max_x < min_x or max_y < min_y:
        return []
    out: List[Point2] = []
    nx = int((max_x - min_x) / spacing)
    ny = int((max_y - min_y) / spacing)
    for i in range(nx + 1):
        for j in range(ny + 1):
            out.append((min_x + i * spacing, min_y + j * spacing))
    return out


def point_in_polygon(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Kiểm tra điểm nằm trong đa giác (ray casting). Biên coi như nằm trong."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        if abs((xj - xi) * (y - yi) - (x - xi) * (yj - yi)) < 1e-12 and \
           min(xi, xj) - 1e-12 <= x <= max(xi, xj) + 1e-12 and \
           min(yi, yj) - 1e-12 <= y <= max(yi, yj) + 1e-12:
            return True                      # nằm đúng trên cạnh
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


# --------------------------------------------------------------------------
# Đọc / ghi file
# --------------------------------------------------------------------------

_DELIMITERS = (",", ";", "\t", " ")


def read_xyz(path: str, max_points: Optional[int] = None,
             skip_header: Optional[bool] = None) -> List[Point3]:
    """Đọc file điểm XYZ/CSV thành danh sách (x, y, z).

    Chấp nhận dấu phân cách phổ biến, tự bỏ qua dòng tiêu đề và dòng rỗng. Ba cột
    đầu tiên phân tích được thành số sẽ được lấy làm X, Y, Z - đủ cho định dạng
    XYZ, PENZD (số hiệu, N, E, Z, mô tả) thì phải chuyển trước vì thứ tự cột khác.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Không tìm thấy file điểm: {path}")
    out: List[Point3] = []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for lineno, raw in enumerate(fh):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = _split_line(line)
            if len(parts) < 3:
                continue
            try:
                x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
            except ValueError:
                if lineno == 0 or skip_header:
                    continue                  # dòng tiêu đề
                continue
            out.append((x, y, z))
            if max_points and len(out) >= max_points:
                break
    return out


def _split_line(line: str) -> List[str]:
    for delim in _DELIMITERS:
        if delim in line:
            return [p for p in line.replace("\t", delim).split(delim) if p != ""]
    return [line]


def write_csv(path: str, header: Sequence[str], rows: Iterable[Sequence]) -> Dict[str, object]:
    """Ghi một bảng ra CSV UTF-8 kèm BOM (Excel trên Windows đọc đúng dấu tiếng Việt).

    Trả về đường dẫn tuyệt đối và SỐ DÒNG ĐÃ GHI đọc lại từ chính biến đếm, để
    phía gọi có cái đối chiếu thay vì chỉ biết "hàm không ném lỗi".
    """
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(list(header))
        for row in rows:
            writer.writerow(list(row))
            count += 1
    return {"path": path, "rows_written": count, "bytes": os.path.getsize(path)}
