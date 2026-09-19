# -*- coding: utf-8 -*-
"""
Điểm khởi chạy Autodesk Civil 3D MCP Server.
============================================
Dùng file này trong cấu hình MCP (Claude Code / Claude Desktop / VS Code):

    "command": "<đường dẫn python.exe>",
    "args": ["<thư mục dự án>/civil3d-mcp/civil3d_server.py"],
    "cwd": "<thư mục dự án>/civil3d-mcp"

Máy chủ không tự khởi động Civil 3D khi nạp: nó chỉ bám vào phiên đang chạy khi
có tool được gọi. Muốn khởi động thì gọi tool launch_civil3d.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from civil3d_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
