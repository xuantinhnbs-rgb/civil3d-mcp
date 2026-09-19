"""
Civil 3D MCP Server
====================
Giao tiếp chuẩn Model Context Protocol để AI điều khiển Autodesk Civil 3D đang
chạy trên máy này, phục vụ quy trình nghiên cứu Scan-to-BIM:

    đám mây điểm đã lọc  ->  TIN surface  ->  alignment  ->  profile
                                  |                            |
                                  +--> volume surface          +--> profile view
                                  |    (khối lượng đào/đắp)
                                  +--> sample line -> trắc ngang -> CSV
                                  +--> đối chiếu điểm kiểm tra -> RMSE

Hợp đồng trả về: MỌI tool trả về một object JSON có khoá "ok".
  * Thành công -> {"ok": true, ...}
  * Thất bại   -> {"ok": false, "error": "<thông điệp tiếng Việt nói rõ cách khắc phục>"}

Các tool GHI còn trả về "verified" và "verified_by": một lời gọi COM không ném lỗi
KHÔNG phải bằng chứng thao tác đã có hiệu lực, nên mỗi thao tác ghi đều đọc lại
kết quả và nói rõ đã kiểm chứng bằng cách nào.
"""

from __future__ import annotations

import functools
from typing import List, Optional

from mcp.server.mcpserver import MCPServer

from . import alignments as al_mod
from . import corridors as cor_mod
from . import research as res_mod
from . import surfaces as surf_mod
from .client import Civil3DClient
from .com import C3DError

mcp = MCPServer(
    "Civil3D-MCP",
    instructions=(
        "Điều khiển Autodesk Civil 3D đang chạy trên máy này: dựng và phân tích bề mặt TIN, "
        "tuyến, trắc dọc, corridor, trắc ngang, và xuất số liệu cho báo cáo nghiên cứu.\n"
        "Gọi check_civil3d_connection TRƯỚC TIÊN. Civil 3D chạy trên nền AutoCAD, và máy này "
        "có thể có cả AutoCAD thuần - nếu đang bám nhầm vào AutoCAD thuần thì tool đó sẽ nói rõ. "
        "Chưa chạy thì gọi launch_civil3d, quá trình mở mất 1-3 phút.\n"
        "Mọi thao tác đều cần một bản vẽ đang mở, và bản vẽ phải dựng từ template Civil 3D "
        "(có sẵn style bề mặt, tuyến, trắc dọc) - bản vẽ AutoCAD trắng sẽ báo thiếu style.\n"
        "Đối tượng được định danh bằng TÊN, không phải chỉ số; tên sai sẽ nhận lại danh sách "
        "các tên đang có.\n"
        "GIỚI HẠN: COM không tạo được assembly/subassembly - create_corridor đòi một assembly "
        "đã có trong bản vẽ. Lệnh gửi qua send_civil3d_command chạy bất đồng bộ và không trả "
        "kết quả, luôn phải kiểm chứng lại bằng một tool đọc.\n"
        "Với dữ liệu quét thật (hàng chục nghìn điểm trở lên) dùng add_point_file_to_surface "
        "thay vì add_points_to_surface: nạp qua file trên đĩa nhanh hơn nhiều lần so với đẩy "
        "mảng qua COM."
    ),
)

c3d = Civil3DClient()


def safe(fn):
    """Bọc một tool: không bao giờ ném lỗi, luôn trả về dict có khoá 'ok'."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> dict:
        try:
            result = fn(*args, **kwargs)
        except C3DError as exc:
            return {"ok": False, "error": str(exc)}
        except (ValueError, TypeError, KeyError) as exc:
            return {"ok": False, "error": f"Tham số không hợp lệ: {exc}"}
        except FileNotFoundError as exc:
            return {"ok": False, "error": f"Không tìm thấy file: {exc}"}
        except Exception as exc:                     # lưới an toàn cuối cùng
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        if isinstance(result, dict):
            return result if "ok" in result else {"ok": True, **result}
        if isinstance(result, list):
            return {"ok": True, "count": len(result), "items": result}
        return {"ok": True, "result": result}

    return wrapper


# ============================================================================
# Kết nối và tài liệu
# ============================================================================

@mcp.tool()
@safe
def check_civil3d_connection() -> dict:
    """Kiểm tra Civil 3D: đã cài chưa, đang chạy chưa, phiên bản COM, bản vẽ đang mở.

    Nên gọi đầu tiên. Tool này phân biệt rõ ba tình huống hay bị lẫn với nhau:
    chưa cài Civil 3D, đã cài nhưng chưa chạy, và đang chạy nhưng con trỏ COM bám
    vào một phiên AutoCAD thuần không có module Civil 3D.
    """
    return c3d.get_status()


@mcp.tool()
@safe
def launch_civil3d(measurement: str = "metric") -> dict:
    """Khởi động Civil 3D với đúng tham số dòng lệnh của shortcut chính hãng.

    Chạy acad.exe trần chỉ ra AutoCAD thuần; phải kèm /ld AecBase.dbx, hồ sơ C3D và
    /product C3D thì các module Civil 3D mới nạp.

    :param measurement: 'metric' (hồ sơ C3D_Metric) hoặc 'imperial'.
    """
    return c3d.launch(measurement)


@mcp.tool()
@safe
def get_drawing_info() -> dict:
    """Thông tin bản vẽ hiện hành: tên file, đơn vị, và số lượng đối tượng Civil 3D."""
    return c3d.get_document_info()


@mcp.tool()
@safe
def list_open_drawings() -> dict:
    """Liệt kê mọi bản vẽ đang mở, kèm chỉ số và cờ đánh dấu bản vẽ hiện hành."""
    return {"documents": c3d.list_documents()}


@mcp.tool()
@safe
def open_drawing(file_path: str, read_only: bool = False) -> dict:
    """Mở một file .dwg/.dwt/.dxf trong Civil 3D.

    :param file_path: Đường dẫn tới file.
    :param read_only: True để mở ở chế độ chỉ đọc.
    """
    return c3d.open_document(file_path, read_only)


@mcp.tool()
@safe
def create_new_drawing(template_path: Optional[str] = None) -> dict:
    """Tạo bản vẽ mới từ template Civil 3D - cách đúng để bắt đầu một bài toán.

    Mở thẳng file .dwt bằng open_drawing là đang sửa chính template của máy; một
    lần lưu nhầm sẽ làm hỏng template cho mọi bản vẽ sau.

    :param template_path: Đường dẫn .dwt, vd
        'C:/Users/<user>/AppData/Local/Autodesk/C3D 2026/enu/Template/_Autodesk Civil 3D (Metric) NCS.dwt'.
        Bỏ trống thì dùng template mặc định của Civil 3D.
    """
    return c3d.new_document(template_path)


@mcp.tool()
@safe
def save_drawing(file_path: Optional[str] = None) -> dict:
    """Lưu bản vẽ hiện hành.

    :param file_path: Bỏ trống để lưu đè; truyền đường dẫn để lưu thành file mới.
    """
    return c3d.save_document(file_path)


@mcp.tool()
@safe
def switch_drawing(name_or_index: str) -> dict:
    """Chuyển bản vẽ hiện hành sang một bản vẽ khác đang mở.

    :param name_or_index: Tên file (vd 'tuyen.dwg') hoặc chỉ số từ list_open_drawings.
    """
    return c3d.activate_document(name_or_index)


@mcp.tool()
@safe
def send_civil3d_command(command: str) -> dict:
    """Gửi một dòng lệnh vào Civil 3D (lối thoát cho chức năng COM không có).

    CẢNH BÁO: lệnh chạy BẤT ĐỒNG BỘ. COM trả về ngay khi lệnh được xếp hàng, không
    có mã lỗi nào quay lại, và lệnh nào chờ người dùng bấm chuột sẽ treo giao diện.
    Sau khi gửi, luôn dùng một tool đọc để kiểm chứng hiệu lực.

    :param command: Dòng lệnh, vd '_.AeccCreateSurface' hoặc '(command "_.ZOOM" "_E")'.
    """
    return c3d.send_command(command)


@mcp.tool()
@safe
def get_environment_report() -> dict:
    """Bản ghi môi trường phần mềm (phiên bản Civil 3D, COM, AutoCAD) cho mục thực nghiệm.

    Kết quả nghiên cứu chỉ tái lập được khi biết chính xác phiên bản đã chạy; số
    hiệu ở đây lấy từ registry và từ chính file acad.exe.
    """
    return res_mod.environment_report(c3d)


# ============================================================================
# Bề mặt
# ============================================================================

@mcp.tool()
@safe
def list_surfaces(with_statistics: bool = False) -> dict:
    """Liệt kê mọi bề mặt trong bản vẽ.

    :param with_statistics: True để kèm thống kê (số điểm, số tam giác, hộp bao,
        biên độ cao độ). Chậm hơn trên bề mặt lớn.
    """
    return c3d.run(surf_mod.list_surfaces, with_statistics)


@mcp.tool()
@safe
def get_surface_info(name: str) -> dict:
    """Thông tin đầy đủ một bề mặt: thống kê, hộp bao, thành phần định nghĩa.

    :param name: Tên bề mặt.
    """
    return c3d.run(surf_mod.surface_info, name)


@mcp.tool()
@safe
def create_tin_surface(name: str, style: Optional[str] = None,
                       layer: Optional[str] = None,
                       description: Optional[str] = None) -> dict:
    """Tạo một TIN surface rỗng, sẵn sàng nhận điểm hoặc file điểm.

    :param name: Tên bề mặt (không được trùng tên đã có).
    :param style: Tên style bề mặt; bỏ trống thì lấy style đầu tiên của bản vẽ.
    :param layer: Layer đặt bề mặt.
    :param description: Mô tả - nên ghi nguồn dữ liệu để truy vết về sau.
    """
    return c3d.run(surf_mod.create_tin_surface, name, style, layer, description)


@mcp.tool()
@safe
def add_points_to_surface(name: str, points: List[List[float]],
                          chunk_size: int = 5000) -> dict:
    """Nạp điểm (x, y, z) trực tiếp vào TIN surface qua COM.

    Hợp cho vài nghìn điểm. Nhiều hơn thì dùng add_point_file_to_surface: đẩy mảng
    lớn qua COM chậm và có thể làm Civil 3D treo.

    Số điểm báo cáo là số ĐO ĐƯỢC (chênh lệch số điểm trước/sau), không phải số đã
    gửi - Civil 3D loại điểm trùng toạ độ và điểm ngoài ranh giới.

    :param points: Danh sách [[x, y, z], ...].
    :param chunk_size: Số điểm mỗi lô gửi qua COM.
    """
    return c3d.run(surf_mod.add_points, name, points, chunk_size)


@mcp.tool()
@safe
def add_point_file_to_surface(name: str, file_path: str,
                              file_format: str = "PENZD (comma delimited)") -> dict:
    """Gắn một file điểm trên đĩa vào định nghĩa bề mặt - đường nạp cho dữ liệu quét thật.

    :param name: Tên bề mặt.
    :param file_path: Đường dẫn file điểm.
    :param file_format: Tên định dạng điểm đã khai trong bản vẽ (Toolspace >
        Settings > Point File Formats). Tên sai sẽ bị Civil 3D từ chối.
    """
    return c3d.run(surf_mod.add_point_file, name, file_path, file_format)


@mcp.tool()
@safe
def build_surface_from_xyz_file(surface_name: str, xyz_path: str,
                                thin_every: int = 1,
                                max_points: Optional[int] = None,
                                max_triangle_length: Optional[float] = None,
                                style: Optional[str] = None) -> dict:
    """Dựng TIN surface từ file điểm XYZ/CSV trong một bước: tạo, nạp, đặt tham số, rebuild.

    :param surface_name: Tên bề mặt sẽ tạo.
    :param xyz_path: File điểm, ba cột số đầu tiên là X, Y, Z.
    :param thin_every: Giữ mỗi điểm thứ n (1 = giữ hết). Chỉ dùng khi file chưa
        được sắp xếp theo toạ độ; file đã sắp xếp thì cách này bỏ sót cả vùng.
    :param max_points: Trần số điểm nạp vào.
    :param max_triangle_length: Cắt tam giác dài hơn ngưỡng này - tham số quan
        trọng nhất với dữ liệu quét, nó loại các tam giác bắc qua vùng bóng khuất.
    :param style: Tên style bề mặt.
    """
    return c3d.run(res_mod.surface_from_point_file, surface_name, xyz_path,
                   thin_every, max_points, style, max_triangle_length)


@mcp.tool()
@safe
def import_surface_file(file_path: str) -> dict:
    """Nhập bề mặt có sẵn từ LandXML (.xml), TIN (.tin) hoặc DEM (.dem/.tif/.asc).

    :param file_path: Đường dẫn file; định dạng chọn theo phần mở rộng.
    """
    return c3d.run(surf_mod.import_surface, file_path)


@mcp.tool()
@safe
def set_surface_build_options(name: str,
                              max_triangle_length: Optional[float] = None,
                              exclude_below: Optional[float] = None,
                              exclude_above: Optional[float] = None,
                              use_boundaries: Optional[bool] = None,
                              rebuild: bool = True) -> dict:
    """Đặt tham số dựng TIN rồi rebuild, và đọc lại từng tham số để kiểm chứng.

    :param max_triangle_length: Chiều dài cạnh tam giác tối đa.
    :param exclude_below: Loại điểm có cao độ nhỏ hơn giá trị này.
    :param exclude_above: Loại điểm có cao độ lớn hơn giá trị này.
    :param use_boundaries: Bật/tắt áp dụng ranh giới.
    :param rebuild: True để rebuild ngay sau khi đổi tham số.
    """
    return c3d.run(surf_mod.set_build_options, name, max_triangle_length,
                   exclude_below, exclude_above, use_boundaries, rebuild)


@mcp.tool()
@safe
def rebuild_surface(name: str) -> dict:
    """Tính lại một bề mặt và trả về thống kê sau khi tính.

    :param name: Tên bề mặt.
    """
    return c3d.run(surf_mod.rebuild_surface, name)


@mcp.tool()
@safe
def delete_surface(name: str, confirm: bool = False) -> dict:
    """Xoá một bề mặt. Bắt buộc confirm=True.

    Xoá bề mặt kéo theo mọi đối tượng phụ thuộc (trắc dọc lấy từ nó, volume surface
    tham chiếu nó) và không hoàn tác được qua COM.

    :param name: Tên bề mặt.
    :param confirm: Phải đặt True thì thao tác mới chạy.
    """
    return c3d.run(surf_mod.delete_surface, name, confirm)


@mcp.tool()
@safe
def create_volume_surface(name: str, base_surface: str, comparison_surface: str,
                          style: Optional[str] = None,
                          layer: Optional[str] = None,
                          description: Optional[str] = None) -> dict:
    """Tạo TIN volume surface so sánh hai bề mặt, trả về khối lượng đào/đắp ngay.

    Phép đo trung tâm khi đối chiếu hoàn công với thiết kế. Quy ước: base là bề mặt
    gốc/thiết kế, comparison là bề mặt hoàn công; cut là phần comparison thấp hơn base.

    :param name: Tên volume surface sẽ tạo.
    :param base_surface: Bề mặt gốc (thường là thiết kế).
    :param comparison_surface: Bề mặt so sánh (thường là hoàn công).
    :param style: Tên style bề mặt.
    :param layer: Layer đặt volume surface; tự tạo nếu layer chưa có.
    :param description: Mô tả. Bỏ trống thì tự sinh - Civil 3D từ chối tạo volume
        surface khi mô tả rỗng, nên đây không phải trường tuỳ chọn thật sự.
    """
    return c3d.run(surf_mod.create_volume_surface, name, base_surface,
                   comparison_surface, style, layer, description)


@mcp.tool()
@safe
def sample_surface_elevations(name: str, points: List[List[float]]) -> dict:
    """Cao độ bề mặt tại một loạt toạ độ XY.

    Điểm nằm ngoài phạm vi bề mặt trả về null và được đếm riêng - số đó chính là
    thước đo độ phủ của dữ liệu.

    :param name: Tên bề mặt.
    :param points: Danh sách [[x, y], ...].
    """
    return c3d.run(surf_mod.elevation_at, name, points)


@mcp.tool()
@safe
def sample_surface_section(name: str, start: List[float], end: List[float]) -> dict:
    """Mặt cắt bề mặt theo một đoạn thẳng.

    Civil 3D trả điểm tại mọi giao với cạnh tam giác TIN, tức là mặt cắt đúng theo
    hình học TIN chứ không phải lấy mẫu theo bước đều.

    :param name: Tên bề mặt.
    :param start: [x, y] điểm đầu.
    :param end: [x, y] điểm cuối.
    """
    return c3d.run(surf_mod.sample_section, name, start, end)


@mcp.tool()
@safe
def compare_surface_to_check_points(name: str, points: List[List[float]],
                                    tolerances: Optional[List[float]] = None) -> dict:
    """So bề mặt với các điểm kiểm tra - phép đánh giá độ chính xác mô hình.

    dz = cao độ điểm kiểm tra - cao độ bề mặt. Trả về RMSE, độ lệch chuẩn, sai số
    trung bình và tỉ lệ điểm đạt từng mức dung sai.

    :param name: Tên bề mặt.
    :param points: Danh sách [[x, y, z], ...] của điểm kiểm tra.
    :param tolerances: Các mức dung sai cần thống kê, mặc định [0.02, 0.05, 0.10].
    """
    return c3d.run(surf_mod.compare_to_points, name, points,
                   tuple(tolerances) if tolerances else (0.02, 0.05, 0.10))


@mcp.tool()
@safe
def compare_two_surfaces(surface_a: str, surface_b: str, spacing: float = 1.0,
                         edge_inset: float = 0.0,
                         tolerances: Optional[List[float]] = None,
                         max_samples: int = 20000) -> dict:
    """Chênh cao độ giữa hai bề mặt, lấy mẫu trên lưới đều trong vùng chồng lấn.

    dz = z(A) - z(B). Chỉ nút có cao độ trên CẢ HAI bề mặt mới vào thống kê; nút
    rơi ngoài được đếm riêng.

    :param surface_a: Bề mặt thứ nhất (thường là hoàn công).
    :param surface_b: Bề mặt thứ hai (thường là thiết kế).
    :param spacing: Bước lưới lấy mẫu.
    :param edge_inset: Co vùng lấy mẫu vào trong bấy nhiêu đơn vị - mép TIN là nơi
        nội suy kém tin cậy nhất và dễ kéo lệch RMSE.
    :param tolerances: Các mức dung sai cần thống kê.
    :param max_samples: Trần số nút lấy mẫu.
    """
    return c3d.run(res_mod.surface_deviation_grid, surface_a, surface_b, spacing,
                   edge_inset, None,
                   tuple(tolerances) if tolerances else (0.02, 0.05, 0.10),
                   max_samples)


# ============================================================================
# Tuyến
# ============================================================================

@mcp.tool()
@safe
def list_alignments() -> dict:
    """Liệt kê mọi tuyến trong bản vẽ, kể cả tuyến nằm trong Site."""
    return c3d.run(al_mod.list_alignments)


@mcp.tool()
@safe
def get_alignment_info(name: str, include_entities: bool = True) -> dict:
    """Thông tin một tuyến: lý trình đầu/cuối, chiều dài, các phần tử hình học, trắc dọc.

    :param name: Tên tuyến.
    :param include_entities: True để liệt kê từng đoạn thẳng/cong của tuyến.
    """
    return c3d.run(al_mod.alignment_info, name, include_entities)


@mcp.tool()
@safe
def create_alignment(name: str, points: List[List[float]],
                     layer: Optional[str] = None,
                     style: Optional[str] = None,
                     label_set: Optional[str] = None,
                     description: Optional[str] = None) -> dict:
    """Tạo tuyến từ một dãy đỉnh, nối bằng các đoạn thẳng.

    Không tự chèn đường cong: cong hoá và chọn bán kính là quyết định thiết kế của
    người kỹ sư, không nên để tool làm thay. Muốn có đường cong thì dựng polyline
    trước rồi dùng create_alignment_from_polyline với add_curves=True.

    :param name: Tên tuyến.
    :param points: Danh sách [[x, y], ...] theo thứ tự tăng lý trình.
    :param layer: Layer đặt tuyến.
    :param style: Tên style tuyến.
    :param label_set: Tên bộ nhãn tuyến.
    :param description: Mô tả.
    """
    return c3d.run(al_mod.create_alignment, name, points, layer, style,
                   label_set, description)


@mcp.tool()
@safe
def create_alignment_from_polyline(name: str, handle: str,
                                   layer: Optional[str] = None,
                                   style: Optional[str] = None,
                                   label_set: Optional[str] = None,
                                   add_curves: bool = False,
                                   erase_polyline: bool = False) -> dict:
    """Tạo tuyến từ một polyline đã có, định danh bằng handle AutoCAD.

    :param name: Tên tuyến.
    :param handle: Handle của polyline.
    :param add_curves: True để Civil 3D tự chèn đường cong giữa các đoạn thẳng.
    :param erase_polyline: True để xoá polyline gốc sau khi tạo tuyến.
    """
    return c3d.run(al_mod.create_alignment_from_polyline, name, handle, layer,
                   style, label_set, add_curves, erase_polyline)


@mcp.tool()
@safe
def alignment_station_offset(name: str, points: List[List[float]]) -> dict:
    """Đổi toạ độ (Đông, Bắc) sang (lý trình, offset) trên một tuyến.

    :param name: Tên tuyến.
    :param points: Danh sách [[easting, northing], ...].
    """
    return c3d.run(al_mod.station_offset, name, points)


@mcp.tool()
@safe
def alignment_point_location(name: str, stations: List[float],
                             offset: float = 0.0) -> dict:
    """Đổi (lý trình, offset) sang toạ độ (Đông, Bắc) trên một tuyến.

    :param name: Tên tuyến.
    :param stations: Danh sách lý trình.
    :param offset: Khoảng lệch tim; dương là bên phải theo chiều tăng lý trình.
    """
    return c3d.run(al_mod.point_location, name, stations, offset)


@mcp.tool()
@safe
def sample_alignment(name: str, interval: float, offset: float = 0.0) -> dict:
    """Rải điểm đều theo lý trình dọc tuyến - đầu vào cho mọi phép cắt ngang.

    :param name: Tên tuyến.
    :param interval: Bước lý trình.
    :param offset: Khoảng lệch tim.
    """
    return c3d.run(al_mod.sample_alignment, name, interval, offset)


# ============================================================================
# Trắc dọc
# ============================================================================

@mcp.tool()
@safe
def list_profiles(alignment: str) -> dict:
    """Liệt kê trắc dọc của một tuyến.

    :param alignment: Tên tuyến.
    """
    return c3d.run(al_mod.list_profiles, alignment)


@mcp.tool()
@safe
def create_profile_from_surface(alignment: str, surface: str,
                                name: Optional[str] = None,
                                style: Optional[str] = None,
                                start_station: Optional[float] = None,
                                end_station: Optional[float] = None,
                                layer: Optional[str] = None) -> dict:
    """Cắt bề mặt theo tim tuyến để ra trắc dọc tự nhiên.

    :param alignment: Tên tuyến.
    :param surface: Tên bề mặt cần cắt.
    :param name: Tên trắc dọc; bỏ trống sẽ tự đặt theo tuyến và bề mặt.
    :param style: Tên style trắc dọc.
    :param start_station: Lý trình bắt đầu lấy mẫu; bỏ trống = đầu tuyến.
    :param end_station: Lý trình kết thúc; bỏ trống = cuối tuyến.
    :param layer: Layer đặt trắc dọc.
    """
    return c3d.run(al_mod.create_profile_from_surface, alignment, surface, name,
                   style, start_station, end_station, layer)


@mcp.tool()
@safe
def sample_profile(alignment: str, profile: str, interval: float) -> dict:
    """Bảng lý trình - cao độ - độ dốc của một trắc dọc, bước đều.

    :param alignment: Tên tuyến.
    :param profile: Tên trắc dọc.
    :param interval: Bước lý trình.
    """
    return c3d.run(al_mod.sample_profile, alignment, profile, interval)


@mcp.tool()
@safe
def compare_profiles(alignment: str, profile_a: str, profile_b: str,
                     interval: float,
                     tolerances: Optional[List[float]] = None) -> dict:
    """So hai trắc dọc trên cùng tuyến theo bước lý trình đều (hoàn công vs thiết kế).

    dz = cao độ A - cao độ B, kèm thống kê RMSE.

    :param alignment: Tên tuyến.
    :param profile_a: Trắc dọc thứ nhất (thường là hoàn công).
    :param profile_b: Trắc dọc thứ hai (thường là thiết kế).
    :param interval: Bước lý trình.
    :param tolerances: Các mức dung sai cần thống kê.
    """
    return c3d.run(al_mod.compare_profiles, alignment, profile_a, profile_b,
                   interval, tuple(tolerances) if tolerances else (0.02, 0.05, 0.10))


@mcp.tool()
@safe
def create_profile_view(alignment: str, name: str, origin: List[float],
                        style: Optional[str] = None,
                        layer: Optional[str] = None,
                        band_set: Optional[str] = None) -> dict:
    """Dựng khung nhìn trắc dọc tại một điểm chèn.

    :param alignment: Tên tuyến.
    :param name: Tên profile view.
    :param origin: [x, y] hoặc [x, y, z] điểm chèn khung nhìn.
    :param style: Tên style profile view.
    :param layer: Layer đặt khung nhìn.
    :param band_set: Tên bộ band (ProfileViewBandStyleSets). Bỏ trống thì dùng
        "_No Bands". Chuỗi rỗng bị Civil 3D từ chối, nên không có cách "không band"
        nào khác ngoài việc chỉ đích danh một bộ band có thật.
    """
    return c3d.run(al_mod.create_profile_view, alignment, name, origin, style, layer,
                   band_set)


# ============================================================================
# Corridor và trắc ngang
# ============================================================================

@mcp.tool()
@safe
def list_assemblies() -> dict:
    """Liệt kê assembly có trong bản vẽ.

    COM không tạo được assembly. Nếu danh sách rỗng, dùng `list_assembly_library`
    rồi `import_assembly` để chép một assembly mẫu của Civil 3D vào bản vẽ.
    """
    return c3d.run(cor_mod.list_assemblies)


@mcp.tool()
@safe
def list_assembly_library(version: str = "2026", units: str = "Metric") -> dict:
    """Liệt kê các bản vẽ assembly mẫu Civil 3D cài sẵn trên máy.

    :param version: Số hiệu bản Civil 3D, ví dụ "2026".
    :param units: "Metric" hoặc "Imperial".
    """
    return cor_mod.list_assembly_library(version, units)


@mcp.tool()
@safe
def import_assembly(source_drawing: str, insert_point: List[float]) -> dict:
    """Chép assembly từ một bản vẽ khác vào bản vẽ hiện hành.

    Đây là cách tạo assembly khi chỉ có COM. Chọn bản vẽ nguồn bằng
    `list_assembly_library`, hoặc trỏ tới bản vẽ của chính bạn.

    :param source_drawing: Đường dẫn .dwg chứa assembly cần chép.
    :param insert_point: [x, y] hoặc [x, y, z] nơi đặt assembly, nên chọn chỗ trống.
    """
    return c3d.run(cor_mod.import_assembly, source_drawing, insert_point)


@mcp.tool()
@safe
def corridor_points(corridor: str, interval: Optional[float] = None,
                    baseline: int = 0, region: int = 0,
                    with_coordinates: bool = True) -> dict:
    """Đọc điểm hình học corridor ĐÃ TÍNH theo lý trình, kèm mã điểm.

    :param corridor: Tên corridor.
    :param interval: Bước lý trình (m). Bỏ trống thì lấy mọi trạm - corridor dài
        thường có hàng nghìn trạm, nên hãy đặt bước khi chỉ cần bảng báo cáo.
    :param baseline: Chỉ số baseline, mặc định 0.
    :param region: Chỉ số region trong baseline, mặc định 0.
    :param with_coordinates: Quy đổi lý trình/offset sang X, Y.
    """
    return c3d.run(cor_mod.corridor_points, corridor, baseline, region, interval,
                   None, with_coordinates)


@mcp.tool()
@safe
def corridor_shape_areas(corridor: str, interval: Optional[float] = None,
                         baseline: int = 0, region: int = 0) -> dict:
    """Diện tích từng lớp kết cấu (shape) của corridor theo lý trình.

    :param corridor: Tên corridor.
    :param interval: Bước lý trình (m).
    :param baseline: Chỉ số baseline.
    :param region: Chỉ số region.
    """
    return c3d.run(cor_mod.corridor_shape_areas, corridor, baseline, region, interval)


@mcp.tool()
@safe
def list_corridors() -> dict:
    """Liệt kê corridor trong bản vẽ, kèm số baseline và cờ cần rebuild."""
    return c3d.run(cor_mod.list_corridors)


@mcp.tool()
@safe
def get_corridor_info(name: str) -> dict:
    """Thông tin một corridor: baseline, vùng lý trình, các mặt corridor.

    :param name: Tên corridor.
    """
    return c3d.run(cor_mod.corridor_info, name)


@mcp.tool()
@safe
def create_corridor(name: str, alignment: str, profile: str, assembly: str) -> dict:
    """Tạo corridor từ tuyến + trắc dọc + assembly đã có trong bản vẽ.

    Cả ba tên đều được kiểm tra tồn tại trước khi gọi COM, nên tên sai sẽ nhận lại
    danh sách các tên đang có thay vì một lỗi COM chung chung.

    :param name: Tên corridor.
    :param alignment: Tên tuyến làm baseline.
    :param profile: Tên trắc dọc của tuyến đó.
    :param assembly: Tên assembly đã có trong bản vẽ.
    """
    return c3d.run(cor_mod.create_corridor, name, alignment, profile, assembly)


@mcp.tool()
@safe
def add_corridor_baseline(corridor: str, alignment: str, profile: str,
                          assembly: str) -> dict:
    """Thêm một baseline vào corridor đã có.

    :param corridor: Tên corridor.
    :param alignment: Tên tuyến.
    :param profile: Tên trắc dọc.
    :param assembly: Tên assembly.
    """
    return c3d.run(cor_mod.add_baseline, corridor, alignment, profile, assembly)


@mcp.tool()
@safe
def rebuild_corridor(name: str) -> dict:
    """Tính lại hình học corridor. Corridor lớn có thể mất vài phút.

    Cờ out_of_date sau khi chạy là bằng chứng: còn True nghĩa là chưa tính xong.

    :param name: Tên corridor.
    """
    return c3d.run(cor_mod.rebuild_corridor, name)


@mcp.tool()
@safe
def sample_corridor_surface(corridor: str, surface: str,
                            points: List[List[float]]) -> dict:
    """Cao độ mặt corridor tại một loạt toạ độ XY.

    :param corridor: Tên corridor.
    :param surface: Tên mặt corridor.
    :param points: Danh sách [[x, y], ...].
    """
    return c3d.run(cor_mod.corridor_surface_elevations, corridor, surface, points)


@mcp.tool()
@safe
def list_sample_line_groups(alignment: str) -> dict:
    """Liệt kê nhóm sample line của một tuyến.

    :param alignment: Tên tuyến.
    """
    return c3d.run(cor_mod.list_sample_line_groups, alignment)


@mcp.tool()
@safe
def create_sample_lines(alignment: str, group_name: str,
                        interval: Optional[float] = None,
                        stations: Optional[List[float]] = None,
                        left_width: float = 20.0, right_width: float = 20.0,
                        layer: Optional[str] = None,
                        sample_all_surfaces: bool = True) -> dict:
    """Tạo nhóm sample line và rải sample line theo lý trình.

    :param alignment: Tên tuyến.
    :param group_name: Tên nhóm sample line.
    :param interval: Bước lý trình để rải đều trên toàn tuyến.
    :param stations: Hoặc chỉ đích danh danh sách lý trình (ưu tiên hơn interval).
    :param left_width: Bề rộng cắt bên trái tim tuyến.
    :param right_width: Bề rộng cắt bên phải tim tuyến.
    :param layer: Layer đặt sample line.
    :param sample_all_surfaces: True để lấy mẫu mọi bề mặt trong bản vẽ.
    """
    return c3d.run(cor_mod.create_sample_line_group, alignment, group_name,
                   stations, interval, left_width, right_width, layer,
                   sample_all_surfaces)


@mcp.tool()
@safe
def create_sections(alignment: str, group_name: str) -> dict:
    """Cắt trắc ngang tại mọi sample line của nhóm.

    :param alignment: Tên tuyến.
    :param group_name: Tên nhóm sample line.
    """
    return c3d.run(cor_mod.create_sections, alignment, group_name)


@mcp.tool()
@safe
def read_sections(alignment: str, group_name: str, offset_interval: float = 1.0,
                  max_sample_lines: Optional[int] = None) -> dict:
    """Đọc trắc ngang thành bảng lý trình - bề mặt - offset - cao độ.

    :param alignment: Tên tuyến.
    :param group_name: Tên nhóm sample line.
    :param offset_interval: Bước offset khi đọc cao độ trên mỗi trắc ngang.
    :param max_sample_lines: Trần số sample line đọc, để tránh kết quả quá lớn.
    """
    return c3d.run(cor_mod.read_sections, alignment, group_name, offset_interval,
                   max_sample_lines)


# ============================================================================
# Xuất số liệu cho báo cáo
# ============================================================================

@mcp.tool()
@safe
def export_surface_comparison(surface_a: str, surface_b: str, out_dir: str,
                              spacing: float = 1.0, edge_inset: float = 0.0,
                              volume_surface_name: Optional[str] = None,
                              tolerances: Optional[List[float]] = None) -> dict:
    """Bộ sản phẩm đầy đủ khi đối chiếu hoàn công với thiết kế.

    Ghi ra ba file: CSV lưới sai lệch, CSV thống kê, và báo cáo Markdown ghi đủ
    điều kiện sinh số liệu (bước lưới, vùng chồng lấn, số nút rơi ngoài, phiên bản
    phần mềm). Nếu truyền volume_surface_name thì tạo thêm volume surface để lấy
    khối lượng do chính Civil 3D tính - một phép đo độc lập với lưới, dùng đối chứng.

    :param surface_a: Bề mặt thứ nhất (thường là hoàn công).
    :param surface_b: Bề mặt thứ hai (thường là thiết kế).
    :param out_dir: Thư mục ghi kết quả.
    :param spacing: Bước lưới lấy mẫu.
    :param edge_inset: Co vùng lấy mẫu vào trong.
    :param volume_surface_name: Tên volume surface sẽ tạo thêm; bỏ trống thì không tạo.
    :param tolerances: Các mức dung sai cần thống kê.
    """
    return c3d.run(res_mod.export_surface_comparison, surface_a, surface_b, out_dir,
                   spacing, edge_inset, volume_surface_name,
                   tuple(tolerances) if tolerances else (0.02, 0.05, 0.10))


@mcp.tool()
@safe
def export_profile_csv(alignment: str, profile: str, out_path: str,
                       interval: float = 5.0) -> dict:
    """Xuất trắc dọc ra CSV: lý trình, cao độ, độ dốc.

    :param alignment: Tên tuyến.
    :param profile: Tên trắc dọc.
    :param out_path: Đường dẫn file CSV.
    :param interval: Bước lý trình.
    """
    return c3d.run(res_mod.export_profile_csv, alignment, profile, out_path, interval)


@mcp.tool()
@safe
def export_sections_csv(alignment: str, group_name: str, out_path: str,
                        offset_interval: float = 1.0,
                        max_sample_lines: Optional[int] = None) -> dict:
    """Xuất trắc ngang ra CSV: lý trình, bề mặt, offset, cao độ.

    :param alignment: Tên tuyến.
    :param group_name: Tên nhóm sample line.
    :param out_path: Đường dẫn file CSV.
    :param offset_interval: Bước offset.
    :param max_sample_lines: Trần số sample line đọc.
    """
    return c3d.run(res_mod.export_sections_csv, alignment, group_name, out_path,
                   offset_interval, max_sample_lines)


@mcp.tool()
@safe
def export_corridor_points_csv(corridor: str, out_path: str,
                               interval: Optional[float] = None,
                               baseline: int = 0, region: int = 0) -> dict:
    """Xuất hình học corridor đã tính ra CSV: lý trình, offset, cao độ, X, Y, mã.

    :param corridor: Tên corridor.
    :param out_path: Đường dẫn file CSV sẽ ghi.
    :param interval: Bước lý trình (m).
    :param baseline: Chỉ số baseline.
    :param region: Chỉ số region.
    """
    return c3d.run(res_mod.export_corridor_points_csv, corridor, out_path, interval,
                   baseline, region)


@mcp.tool()
@safe
def export_corridor_quantities(corridor: str, out_dir: str, interval: float = 20.0,
                               baseline: int = 0, region: int = 0) -> dict:
    """Khối lượng theo lớp kết cấu của corridor, xuất CSV diện tích và thể tích.

    Thể tích tính bằng hình thang giữa hai lý trình liên tiếp, nên phụ thuộc
    `interval`; bước đã dùng luôn nằm trong kết quả và nên được nêu trong báo cáo.

    :param corridor: Tên corridor.
    :param out_dir: Thư mục ghi hai file CSV.
    :param interval: Bước lý trình (m), mặc định 20.
    :param baseline: Chỉ số baseline.
    :param region: Chỉ số region.
    """
    return c3d.run(res_mod.export_corridor_quantities, corridor, out_dir, interval,
                   baseline, region)


@mcp.tool()
@safe
def export_check_point_report(surface: str, points_file: str, out_path: str,
                              tolerances: Optional[List[float]] = None) -> dict:
    """Đối chiếu bề mặt với file điểm kiểm tra ngoại nghiệp và xuất CSV kèm thống kê.

    Đây là phép đánh giá độ chính xác có giá trị nhất trong báo cáo: điểm kiểm tra
    là số đo độc lập, không tham gia dựng bề mặt.

    :param surface: Tên bề mặt.
    :param points_file: File điểm kiểm tra (ba cột số đầu là X, Y, Z).
    :param out_path: Đường dẫn file CSV kết quả.
    :param tolerances: Các mức dung sai cần thống kê.
    """
    return c3d.run(res_mod.export_check_point_report, surface, points_file, out_path,
                   tuple(tolerances) if tolerances else (0.02, 0.05, 0.10))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
