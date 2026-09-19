# -*- coding: utf-8 -*-
"""
Kiểm tra `install.py` — script mà người dùng chạy đầu tiên.
===========================================================
Không chạy `main()` ở đây: nó gọi tiến trình con và ghi file. Nhóm test này nhắm
vào những chỗ mà lỗi *chỉ xuất hiện trên máy người khác* — đường dẫn có hình dạng
khác, console có bảng mã khác. Đó đúng là loại lỗi mà chạy thử trên máy tác giả
không bao giờ bắt được.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import install  # noqa: E402

# Ba dạng đầu là đường dẫn bình thường; ba dạng sau là những hình dạng đã hoặc có
# thể làm hỏng phép nhúng chuỗi. `X:\` là trường hợp thật: ổ ảo tạo bằng `subst`,
# dùng khi chụp ảnh minh chứng để đường dẫn không mang tên người dùng — và chính
# nó đã làm `install.py` chết vì SyntaxError.
DUONG_DAN = [
    r"C:\Users\ai-do\civil3d-mcp",
    r"C:\du an\civil3d-mcp",
    "/home/ai-do/civil3d-mcp",
    "X:\\",
    "D:\\",
    r"C:\thu muc\ket thuc bang gach nguoc\\",
]


@pytest.mark.parametrize("duong_dan", DUONG_DAN)
def test_doan_ma_nhung_duong_dan_luon_bien_dich_duoc(duong_dan):
    """Hồi quy: repo nằm ở gốc một ổ đĩa từng sinh ra mã không biên dịch được.

    `r'%s'` với đường dẫn `X:\\` cho ra `r'X:\\'` — dấu gạch ngược cuối chuỗi nuốt
    dấu nháy đóng. Đổi `snippet` về cách viết đó thì phép thử này đỏ ngay.
    """
    ma = install.snippet("print(1)", duong_dan)
    compile(ma, "<snippet>", "exec")


@pytest.mark.parametrize("duong_dan", DUONG_DAN)
def test_doan_ma_giu_nguyen_ven_duong_dan(duong_dan):
    """Biên dịch được vẫn chưa đủ: đường dẫn phải tới nơi đúng y như ban đầu.

    Một phép escape sai có thể cho ra mã hợp lệ mà trỏ vào thư mục khác — `\\t`
    thành ký tự tab là ví dụ kinh điển, và nó im lặng.
    """
    ns = {}
    exec(install.snippet("ket_qua = sys.path[0]", duong_dan), ns)  # noqa: S102
    assert ns["ket_qua"] == duong_dan


def test_moi_chuoi_in_ra_man_hinh_deu_la_ascii():
    """Console Windows mặc định là cp1252. Một ký tự có dấu trong dòng trạng thái
    làm `print` ném UnicodeEncodeError, và script chẩn đoán chết ngay giữa bảng
    kiểm tra — đúng vào lúc người dùng cần nó nhất.

    Chú thích và docstring thì không bị ràng buộc: chúng không đi qua `print`. Nên
    phép thử chỉ soi những chuỗi THỰC SỰ được in ra.
    """
    khong_ascii = []
    for mod, vaitro in install.NEEDS:
        for chuoi in (mod, vaitro):
            if not chuoi.isascii():
                khong_ascii.append(chuoi)
    for ten in ["OK", "WARN", "BAD", "SERVER_NAME", "SERVER_SCRIPT", "SERVER_LABEL"]:
        gia_tri = getattr(install, ten)
        if not gia_tri.isascii():
            khong_ascii.append(gia_tri)
    assert not khong_ascii, "Chuỗi in ra màn hình có ký tự ngoài ASCII: %s" % khong_ascii


def test_ham_in_song_sot_khi_console_khong_ma_hoa_duoc(capsys, monkeypatch):
    """Hàng rào thứ hai: `say()` không được chết vì bảng mã, kể cả khi chuỗi đến từ
    chỗ không kiểm soát được — đường dẫn của người dùng, hay `stderr` của tiến
    trình con. Quy ước ASCII ở trên chỉ áp được cho chuỗi do repo này viết ra.
    """

    class ConsoleHep:
        """Giả lập stdout cp1252: ném đúng ngoại lệ mà console thật ném."""

        encoding = "cp1252"

        def write(self, s):
            s.encode("cp1252")  # ném UnicodeEncodeError nếu có ký tự ngoài cp1252
            return len(s)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", ConsoleHep())
    # Chuỗi PHẢI có ký tự ngoài cp1252, nếu không phép thử chẳng kiểm gì cả:
    # ConsoleHep chỉ ném ngoại lệ khi gặp ký tự nó không mã hoá nổi.
    install.say(install.OK, "Repo    : C:\\du an\\tuyến đường\\civil3d-mcp")

