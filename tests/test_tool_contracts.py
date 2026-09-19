# -*- coding: utf-8 -*-
"""
Kiểm tra hợp đồng của toàn bộ tool MCP.
=======================================
Chạy được trên máy không cài Civil 3D: chỉ nạp server và soi phần khai báo tool.
Đây là lưới an toàn cho việc đổi chữ ký hàm — thứ trực tiếp sinh ra JSON schema
mà AI nhìn thấy, và là thứ duy nhất AI dựa vào để quyết định gọi tool nào.

Một con số nhắc lại trong tài liệu KHÔNG được chứng thực bởi việc nó được nhắc
lại: mọi bản sao đều bắt nguồn từ một lần đếm duy nhất, không ai kiểm. Nên số
tool ở đây được suy ra từ ba nguồn độc lập — mã nguồn, server đã nạp, và câu chữ
trong README — rồi bắt cả ba phải khớp nhau.
"""

import ast
import asyncio
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SERVER_PY = ROOT / "civil3d_mcp" / "server.py"

SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")

chi_tren_windows = pytest.mark.skipif(
    sys.platform != "win32", reason="Nạp server cần pywin32 (chỉ có trên Windows)"
)


def _dem_tool_trong_ma_nguon():
    """Đếm hàm mang decorator `@mcp.tool` bằng AST, không chạy gì cả.

    Dùng AST chứ không dùng grep: một dòng `@mcp.tool` nằm trong chuỗi hay trong
    khối chú thích vẫn khớp biểu thức chính quy, còn AST thì chỉ thấy decorator
    thật. Và cách này chạy được cả ở nơi không cài nổi `pywin32`.
    """
    cay = ast.parse(SERVER_PY.read_text(encoding="utf-8"))
    so = 0
    for node in ast.walk(cay):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            dich = deco.func if isinstance(deco, ast.Call) else deco
            if isinstance(dich, ast.Attribute) and dich.attr == "tool":
                so += 1
                break
    return so


# Nguồn sự thật cho mọi con số "bao nhiêu tool" trong repo này.
SO_TOOL_TRONG_TAI_LIEU = _dem_tool_trong_ma_nguon()


@pytest.fixture(scope="module")
def tools():
    """Danh sách tool đã đăng ký. Không cần Civil 3D chạy: client kết nối lười."""
    from civil3d_mcp import server

    return asyncio.run(server.mcp.list_tools())


def test_ma_nguon_co_dang_ky_tool():
    """Nếu cách viết decorator đổi, phép đếm AST ở trên phải hỏng to chứ không
    được âm thầm trả về 0 rồi làm mọi phép so sánh bên dưới thành đúng."""
    assert SO_TOOL_TRONG_TAI_LIEU > 0


@chi_tren_windows
def test_server_nap_duoc_dung_so_tool_nhu_ma_nguon(tools):
    """Bắc cầu giữa tĩnh và động: một tool được định nghĩa nhưng đăng ký hỏng
    (trùng tên, decorator gọi sai) sẽ làm hai con số lệch nhau."""
    assert len(tools) == SO_TOOL_TRONG_TAI_LIEU


@pytest.mark.parametrize("ten_file", ["README.md", "README.vi.md"])
def test_so_tool_trong_readme_khop_voi_ma_nguon(ten_file):
    """README nêu số tool ở tiêu đề mục. Thêm một tool mà quên sửa câu đó thì
    test này đỏ — đó là toàn bộ lý do nó tồn tại.

    Hai README song ngữ đều bị kiểm: bản dịch bao giờ cũng là bản trôi trước,
    vì người sửa mã nguồn thường chỉ mở đúng một trong hai file.
    """
    van_ban = (ROOT / ten_file).read_text(encoding="utf-8")
    so = [int(n) for n in re.findall(r"(?:tool|Tools?)\s*\((\d+)\)", van_ban)]
    assert so, "%s không nêu số tool ở dạng 'tool (N)'" % ten_file
    assert set(so) == {SO_TOOL_TRONG_TAI_LIEU}, (
        "%s ghi %s tool, mã nguồn có %d" % (ten_file, so, SO_TOOL_TRONG_TAI_LIEU)
    )


@chi_tren_windows
@pytest.mark.parametrize("ten_file", ["README.md", "README.vi.md"])
def test_moi_tool_deu_duoc_liet_ke_trong_readme(ten_file, tools):
    """Một tool không có trong bảng tài liệu là một tool không ai biết để gọi."""
    van_ban = (ROOT / ten_file).read_text(encoding="utf-8")
    thieu = [t.name for t in tools if "`%s`" % t.name not in van_ban]
    assert not thieu, "%s không nhắc tới: %s" % (ten_file, thieu)


@chi_tren_windows
def test_ten_tool_khong_trung_va_dung_snake_case(tools):
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), "Có tool bị đặt trùng tên"
    sai = [n for n in names if not SNAKE_CASE.match(n)]
    assert not sai, "Tên tool không đúng snake_case: %s" % sai


@chi_tren_windows
def test_moi_tool_deu_co_mo_ta(tools):
    """Không có mô tả thì AI không biết khi nào nên gọi tool."""
    thieu = [t.name for t in tools if not (t.description or "").strip()]
    assert not thieu, "Tool thiếu docstring: %s" % thieu


@chi_tren_windows
def test_moi_tool_co_input_schema_kieu_object(tools):
    for tool in tools:
        schema = tool.input_schema
        assert isinstance(schema, dict), tool.name
        assert schema.get("type") == "object", tool.name
        assert "properties" in schema, tool.name


@chi_tren_windows
def test_moi_tham_so_bat_buoc_deu_ton_tai_trong_properties(tools):
    """Tham số nằm trong `required` mà không có trong `properties` là schema hỏng."""
    for tool in tools:
        schema = tool.input_schema
        props = set(schema.get("properties", {}))
        thieu = [r for r in schema.get("required", []) if r not in props]
        assert not thieu, "%s: %s" % (tool.name, thieu)


@chi_tren_windows
def test_tool_xoa_du_lieu_deu_doi_co_xac_nhan(tools):
    """Xoá một bề mặt là không hoàn tác được qua COM, nên chữ ký phải buộc AI nêu
    ý định rõ ràng chứ không để nó xoá bằng một lời gọi mặc định."""
    for tool in tools:
        if not tool.name.startswith("delete_"):
            continue
        props = tool.input_schema.get("properties", {})
        assert "confirm" in props, "%s thiếu tham số confirm" % tool.name


# --------------------------------------------------------------------------
# Bảng khác biệt so với tài liệu COM
# --------------------------------------------------------------------------


# Bảng đầy đủ chỉ nằm ở bản tiếng Việt; bản tiếng Anh tóm tắt và trỏ sang.
FILE_CHUA_BANG = "README.vi.md"


def _so_hang_bang_khac_biet():
    """Đếm số hàng đánh số trong bảng 'khác biệt so với tài liệu COM'."""
    van_ban = (ROOT / FILE_CHUA_BANG).read_text(encoding="utf-8")
    return len(re.findall(r"^\| \d+ \| ", van_ban, re.M))


def test_bang_khac_biet_van_con_trong_readme():
    """Nếu bảng bị đổi định dạng hay chuyển đi nơi khác, phép đếm dưới đây sẽ âm
    thầm trả về 0 và làm mọi so sánh bên dưới thành đúng. Chặn trước ở đây."""
    assert _so_hang_bang_khac_biet() > 0, "%s không còn bảng khác biệt nào" % FILE_CHUA_BANG


@pytest.mark.parametrize("ten_file", ["README.md", "README.vi.md"])
def test_so_khac_biet_neu_trong_van_khop_voi_so_hang_cua_bang(ten_file):
    """Bảng khác biệt là phần có giá trị nhất của README, và cũng là phần dễ trôi
    nhất: mỗi lần dò ra một khác biệt mới thì thêm một hàng, còn những câu văn
    nhắc tới "N khác biệt" ở chỗ khác thì không ai nhớ sửa.

    Đã xảy ra thật: bảng có 16 hàng trong khi mục kiểm thử vẫn viết "tám khác
    biệt" — hai câu cách nhau 70 dòng trong cùng một file.

    Chỉ bắt được con số viết bằng CHỮ SỐ. Viết bằng chữ ("mười sáu", "sixteen")
    thì phép thử này không thấy, nên quy ước của repo là dùng chữ số ở mọi câu
    nhắc tới số lượng khác biệt.
    """
    van_ban = (ROOT / ten_file).read_text(encoding="utf-8")
    so_hang = _so_hang_bang_khac_biet()

    neu_trong_van = [
        int(n)
        for n in re.findall(r"(\d+)\s+(?:khác biệt|differences)", van_ban, re.I)
    ]
    assert neu_trong_van, (
        "%s không nhắc số khác biệt bằng chữ số — xem docstring" % ten_file
    )
    sai = [n for n in neu_trong_van if n != so_hang]
    assert not sai, (
        "%s: bảng có %d hàng nhưng trong văn nói tới %s khác biệt"
        % (ten_file, so_hang, sai)
    )
