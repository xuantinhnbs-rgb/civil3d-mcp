"""Kiểm thử ở mức giao thức: máy chủ khởi động được và khai báo đủ tool.

Phép thử này chạy máy chủ như một tiến trình con qua stdio, đúng cách client MCP
thật gọi nó. Nó bắt được loại lỗi mà import trực tiếp không bắt được: thiếu phụ
thuộc, lỗi khi dựng schema từ chữ ký hàm, hoặc một tool có annotation mà thư viện
MCP không chuyển được thành JSON schema.

Tiến trình con được chạy với môi trường TỐI GIẢN (chỉ giữ SystemRoot và PATH hệ
thống), vì máy chủ MCP thật cũng được client khởi chạy với môi trường do client
quyết định - không phải môi trường của người phát triển.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "civil3d_server.py"

# Các tool bắt buộc phải có mặt; thiếu một cái là quy trình nghiên cứu bị đứt đoạn.
REQUIRED_TOOLS = {
    "check_civil3d_connection",
    "launch_civil3d",
    "get_drawing_info",
    "get_environment_report",
    "list_surfaces",
    "create_tin_surface",
    "add_points_to_surface",
    "add_point_file_to_surface",
    "build_surface_from_xyz_file",
    "create_volume_surface",
    "sample_surface_section",
    "compare_surface_to_check_points",
    "compare_two_surfaces",
    "list_alignments",
    "create_alignment",
    "create_profile_from_surface",
    "sample_profile",
    "compare_profiles",
    "list_corridors",
    "create_corridor",
    "create_sample_lines",
    "read_sections",
    "export_surface_comparison",
    "export_profile_csv",
    "export_sections_csv",
    "export_check_point_report",
}


def _lean_env() -> dict:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return {
        "SystemRoot": system_root,
        "PATH": os.pathsep.join([
            os.path.join(system_root, "System32"),
            system_root,
            os.path.dirname(sys.executable),
        ]),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }


def _rpc(*messages: dict, expect_ids: tuple = ()) -> list:
    """Gửi một loạt bản tin JSON-RPC qua stdio và đọc các phản hồi.

    Giữ stdin MỞ cho tới khi đọc đủ phản hồi cần chờ. Đóng stdin ngay sau khi ghi
    (cách viết gọn hơn bằng subprocess.run) tạo ra một cuộc đua: máy chủ thấy EOF
    và tắt trước khi tool chạy xong, nên một tool chậm sẽ "biến mất" không dấu vết,
    còn tool nhanh thì vẫn trả lời - phép thử khi đó đỏ hay xanh tuỳ tốc độ máy.
    """
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1, env=_lean_env(),
    )
    wanted = set(expect_ids) or {m["id"] for m in messages if "id" in m}
    out: list = []
    try:
        for message in messages:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
        seen: set = set()
        while wanted - seen:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                reply = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append(reply)
            if "id" in reply:
                seen.add(reply["id"])
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    if not out:
        stderr = proc.stderr.read() if proc.stderr else ""
        pytest.fail(f"Máy chủ không trả về bản tin nào.\nstderr:\n{stderr[-3000:]}")
    return out


def _initialize() -> dict:
    return {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    }


def test_may_chu_khoi_dong_va_bat_tay_duoc():
    replies = _rpc(_initialize())
    init = next(r for r in replies if r.get("id") == 1)
    assert "result" in init, init
    assert "serverInfo" in init["result"]


def test_khai_bao_du_bo_tool():
    replies = _rpc(
        _initialize(),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    listing = next(r for r in replies if r.get("id") == 2)
    assert "result" in listing, listing
    names = {t["name"] for t in listing["result"]["tools"]}
    missing = REQUIRED_TOOLS - names
    assert not missing, f"Thiếu tool: {sorted(missing)}"
    assert len(names) >= 40


def test_moi_tool_deu_co_mo_ta_va_schema():
    replies = _rpc(
        _initialize(),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    tools = next(r for r in replies if r.get("id") == 2)["result"]["tools"]
    for tool in tools:
        assert tool.get("description"), f"{tool['name']} không có mô tả"
        schema = tool.get("inputSchema")
        assert isinstance(schema, dict), f"{tool['name']} không có inputSchema"
        assert schema.get("type") == "object", f"{tool['name']} có schema lạ: {schema}"


def test_tool_kiem_tra_ket_noi_khong_bao_gio_nem_loi():
    """Tool này phải trả lời được cả khi Civil 3D chưa chạy - đó là mục đích của nó."""
    replies = _rpc(
        _initialize(),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "check_civil3d_connection", "arguments": {}}},
    )
    call = next(r for r in replies if r.get("id") == 3)
    assert "result" in call, call
    text = call["result"]["content"][0]["text"]
    data = json.loads(text)
    assert "ok" in data
    assert "installed" in data
