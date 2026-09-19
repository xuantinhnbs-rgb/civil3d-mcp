# -*- coding: utf-8 -*-
"""
Cài đặt MCP server cho Autodesk Civil 3D.
=========================================
Dò đường dẫn của chính máy đang chạy rồi sinh ra `.mcp.json` ở gốc repo, nên
chép repo đi đâu, đổi tên thư mục hay dùng Python bản nào cũng chạy đúng. Không
phải sửa đường dẫn bằng tay, và không có đường dẫn nào của máy người khác lọt
vào file cấu hình của bạn.

    python install.py                  # kiểm tra rồi ghi .mcp.json
    python install.py --check          # chỉ kiểm tra, không ghi gì
    python install.py --claude-desktop # ghi thêm vào config của Claude Desktop

Mã thoát: 0 nếu mọi thứ sẵn sàng, khác 0 nếu có mục hỏng — dùng được trong CI.

Civil 3D KHÔNG cần đang chạy để script này chạy xong: server bám vào phiên đang
mở một cách lười, nên việc đăng ký tool không chạm tới COM.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SERVER_NAME = "civil3d-2026"
SERVER_SCRIPT = "civil3d_server.py"
SERVER_LABEL = "Autodesk Civil 3D"

# Thư viện bắt buộc, kèm lý do — để thông báo lúc thiếu nói được cái gì sẽ hỏng.
#
# Mọi chuỗi IN RA MÀN HÌNH trong file này đều không dấu, có chủ ý. Console
# Windows mặc định là cp1252: một chữ có dấu trong dòng thông báo làm `print` ném
# UnicodeEncodeError và script chết ngay giữa bước kiểm tra, tức là công cụ chẩn
# đoán lại hỏng đúng vào lúc người dùng cần nó nhất. Chú thích và docstring thì
# vẫn tiếng Việt có dấu — chúng không bao giờ đi qua `print`.
NEEDS = [
    ("mcp", "giao thuc MCP"),
    ("win32com", "cau COM sang Civil 3D (goi pywin32)"),
]

OK, WARN, BAD = "  [OK]  ", "  [!]   ", "  [X]  "


def say(mark, msg):
    """In một dòng, và không bao giờ chết vì bảng mã của console.

    Hàng rào thứ hai sau quy ước không dấu ở trên: một thông báo lỗi ghép từ
    đường dẫn hay từ stderr của tiến trình con có thể mang ký tự bất kỳ, và
    những chuỗi đó thì không kiểm soát trước được.
    """
    dong = mark + msg
    try:
        print(dong)
    except UnicodeEncodeError:
        bang_ma = getattr(sys.stdout, "encoding", None) or "ascii"
        print(dong.encode(bang_ma, "replace").decode(bang_ma, "replace"))


def step(title):
    print("\n" + "=" * 62 + "\n  " + title + "\n" + "=" * 62)


def python_exe():
    """Đường dẫn python.exe (không dùng pythonw.exe: MCP stdio cần stdout)."""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        candidate = exe[: -len("pythonw.exe")] + "python.exe"
        if os.path.exists(candidate):
            return candidate
    return exe


def as_json_path(*parts):
    """Ghép đường dẫn và đổi sang dấu / — JSON khỏi phải escape dấu gạch ngược."""
    return os.path.join(*parts).replace("\\", "/")


def snippet(body, path=None):
    """Ghép một đoạn mã Python ngắn có nhúng đường dẫn repo, để chạy bằng `python -c`.

    Đường dẫn PHẢI đi qua `%r`. Cách viết trực giác hơn — `r'%s'` — sinh ra mã hỏng
    khi repo nằm ở gốc một ổ đĩa: `r'X:\\'` có dấu gạch ngược cuối chuỗi, nó nuốt
    luôn dấu nháy đóng và cả lời gọi chết vì `SyntaxError: unterminated string
    literal`. Đường dẫn nào có thư mục con cũng chạy tốt, nên lỗi này không bao giờ
    lộ ra trong lúc phát triển — nó chỉ chờ một người dùng clone repo về `D:\\`.
    """
    return "import sys; sys.path.insert(0, %r); %s" % (path if path is not None else HERE, body)


def check_module(name):
    return (
        subprocess.run(
            [python_exe(), "-c", "import " + name],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def entry_for():
    """Sinh mục mcpServers với đường dẫn tuyệt đối của máy này."""
    return {
        "command": python_exe().replace("\\", "/"),
        "args": [as_json_path(HERE, SERVER_SCRIPT)],
        "cwd": HERE.replace("\\", "/"),
        "env": {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
    }


def load_test():
    """Nạp thử server và đếm số tool.

    Chạy trong một TIẾN TRÌNH RIÊNG chứ không import thẳng: đây là phép thử xem
    Claude có khởi động nổi server hay không, mà Claude cũng khởi động nó như
    một tiến trình riêng. Import thẳng vào tiến trình này sẽ bỏ qua đúng những
    lỗi hay gặp nhất — thiếu thư viện ở interpreter khác, lỗi mã hoá đầu ra.
    """
    code = snippet(
        "import asyncio;"
        "from civil3d_mcp import server;"
        "print(len(asyncio.run(server.mcp.list_tools())))"
    )
    try:
        proc = subprocess.run(
            [python_exe(), "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=HERE,
        )
    except Exception as exc:
        return None, str(exc)
    out = (proc.stdout or "").strip().splitlines()
    count = out[-1] if out else ""
    if proc.returncode == 0 and count.isdigit():
        return int(count), None
    return None, (proc.stderr or proc.stdout or "")[-500:]


def civil3d_report():
    """Hỏi chính server xem nó thấy Civil 3D nào trên máy — không đoán lại registry.

    KHÔNG bám vào phiên đang chạy ở đây: chỉ dò bản cài. Bám vào một phiên đang
    bận sẽ treo cả script cài đặt, và "chưa mở Civil 3D" không phải lỗi cấu hình.
    """
    code = snippet(
        "import json;"
        "from civil3d_mcp import com;"
        "print(json.dumps({'installations': com.discover_install_paths(),"
        " 'com_versions_installed': com.discover_civil3d_versions()}))"
    )
    try:
        proc = subprocess.run(
            [python_exe(), "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            cwd=HERE,
        )
        if proc.returncode == 0:
            return json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception:
        pass
    return None


def merge_into(path, entries):
    """Ghi các mục vào file cấu hình, giữ nguyên những server khác đã có sẵn."""
    cfg = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:
            cfg = {}  # file hỏng thì ghi đè, không để chặn việc cài
    cfg.setdefault("mcpServers", {}).update(entries)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def main():
    args = sys.argv[1:]
    check_only = "--check" in args

    step("1. MOI TRUONG")
    say(OK, "Python  : %s" % sys.version.split()[0])
    say(OK, "Thuc thi: %s" % python_exe())
    say(OK, "Repo    : %s" % HERE)
    if os.name != "nt":
        say(BAD, "Server nay chi chay tren Windows (COM cua Civil 3D).")
        return 1

    step("2. THU VIEN")
    missing = False
    for mod, vaitro in NEEDS:
        if check_module(mod):
            say(OK, "%-10s co san   (%s)" % (mod, vaitro))
        else:
            say(BAD, "%-10s CHUA CO  (%s)" % (mod, vaitro))
            missing = True
    if missing:
        say(WARN, "Chay: pip install -r requirements.txt")
        return 1

    step("3. NAP THU SERVER")
    count, err = load_test()
    if count is None:
        say(BAD, "Khong nap duoc server:\n%s" % err)
        return 1
    say(OK, "Nap duoc %d tool" % count)

    step("4. AUTODESK CIVIL 3D")
    rp = civil3d_report()
    ban_cai = (rp or {}).get("installations") or []
    if ban_cai:
        for ins in ban_cai:
            say(OK, "%s" % ins.get("product", "?"))
            say(OK, "   %s" % ins.get("location", "?"))
        com_ver = (rp or {}).get("com_versions_installed") or []
        if com_ver:
            # So hieu COM KHONG trung nam phat hanh (2026 -> 13.8). Server tu do
            # tu registry, nhung in ra day de nguoi dung doi chieu khi bao loi.
            say(OK, "Phien ban COM: %s" % ", ".join(com_ver))
    else:
        say(WARN, "Khong thay ban cai Civil 3D nao tren may nay.")
        say(WARN, "Server van dang ky tool binh thuong, nhung moi tool se bao loi ro rang.")
        say(WARN, "Civil 3D chay tren nen AutoCAD: AutoCAD thuan KHONG thay the duoc.")

    if check_only:
        step("XONG")
        say(WARN, "Che do --check: khong ghi file cau hinh nao.")
        return 0

    step("5. GHI CAU HINH")
    entries = {SERVER_NAME: entry_for()}
    target = merge_into(os.path.join(HERE, ".mcp.json"), entries)
    say(OK, "Da ghi %s" % target)
    print("         %-14s -> %s" % (SERVER_NAME, entries[SERVER_NAME]["args"][0]))

    if "--claude-desktop" in args:
        appdata = os.getenv("APPDATA", "")
        if appdata:
            path = os.path.join(appdata, "Claude", "claude_desktop_config.json")
            merge_into(path, entries)
            say(OK, "Da ghi %s" % path)
            say(WARN, "Khoi dong lai Claude Desktop de nap cau hinh moi.")
        else:
            say(WARN, "Khong doc duoc bien APPDATA, bo qua Claude Desktop.")

    step("XONG")
    say(OK, "%s san sang." % SERVER_LABEL)
    print("\n  Buoc tiep theo:")
    print("    1. Mo Claude Code tai thu muc: %s" % HERE)
    print("    2. Go /mcp de kiem tra server da ket noi chua.")
    print("    3. Goi check_civil3d_connection - no phan biet duoc")
    print("       'chua cai' / 'chua chay' / 'dang bam nham AutoCAD thuan'.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
