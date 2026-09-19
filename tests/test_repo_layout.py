# -*- coding: utf-8 -*-
"""
Kiểm tra bố cục repo và cấu hình mẫu.
=====================================
Những lỗi bắt ở đây là loại chỉ lộ ra lúc NGƯỜI KHÁC clone về: file mẫu sai JSON,
`install.py` trỏ vào script không tồn tại, README nhắc tới ảnh không có thật, hay
`.mcp.json` của máy bị commit lên.

Repo này vốn là một thư mục con của một kho làm việc lớn hơn. Khi một thư mục con
được tách ra thành repo độc lập, phần hỏng không phải mã nguồn — mã nguồn chuyển
sang nguyên vẹn và test xanh ngay — mà là TÀI LIỆU: mọi câu viết dựa vào bối cảnh
kho mẹ ("thêm vào file cấu hình đã có sẵn trong repo", "lấp khoảng trống giữa hai
máy chủ đã có", `cd civil3d-mcp` đứng một mình, đường dẫn tuyệt đối trên máy tác
giả) vẫn đọc trôi chảy sau khi đã sai, nên không có phép kiểm tra thông thường nào
bắt được. Vài mục dưới đây tồn tại đúng để bắt loại đó.

Không cần Windows lẫn Civil 3D để chạy nhóm test này.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def example_config():
    with open(ROOT / ".mcp.json.example", encoding="utf-8") as fh:
        return json.load(fh)


def test_file_mau_khai_dung_mot_server(example_config):
    assert set(example_config["mcpServers"]) == {"civil3d-2026"}


def test_muc_trong_file_mau_du_khoa_can_thiet(example_config):
    for name, entry in example_config["mcpServers"].items():
        assert set(entry) >= {"command", "args", "cwd", "env"}, name
        assert entry["args"], name
        # stdio của MCP là văn bản UTF-8; thiếu hai biến này là gõ tiếng Việt ra rác.
        assert entry["env"].get("PYTHONIOENCODING") == "utf-8", name
        assert entry["env"].get("PYTHONUNBUFFERED") == "1", name


def test_install_py_tro_dung_vao_script_co_that():
    """`install.py` khai báo đường dẫn server bằng tay — kiểm tra nó chưa lệch."""
    import sys

    sys.path.insert(0, str(ROOT))
    import install

    assert (ROOT / install.SERVER_SCRIPT).is_file()


def test_ten_server_trong_install_py_khop_voi_file_mau(example_config):
    import sys

    sys.path.insert(0, str(ROOT))
    import install

    assert {install.SERVER_NAME} == set(example_config["mcpServers"])


def test_thu_muc_repo_tu_chua_du_tai_lieu_va_phu_thuoc():
    """Repo phải gửi đi độc lập được, nên cần đủ giấy tờ tối thiểu."""
    for ten in [
        "README.md",
        "README.vi.md",
        "LICENSE",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "requirements.txt",
        "requirements-dev.txt",
        ".mcp.json.example",
    ]:
        assert (ROOT / ten).is_file(), ten


def test_gitignore_chan_cau_hinh_rieng_cua_may():
    """`.mcp.json` chứa đường dẫn tuyệt đối, lỡ commit lên là hỏng máy người khác."""
    noi_dung = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for muc in [".mcp.json", "__pycache__/"]:
        assert muc in noi_dung, muc


def test_gitignore_chan_ban_ve_va_du_lieu_cong_trinh():
    """Một bản vẽ Civil 3D là dữ liệu của một công trình có thật, và `git add .`
    ở thư mục làm việc sẽ kéo nó lên repo CÔNG KHAI. Đã lên thì nằm vĩnh viễn
    trong lịch sử git."""
    noi_dung = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for muc in ["*.dwg", "*.dwt", "*.las", "*.xyz"]:
        assert muc in noi_dung, muc


# --------------------------------------------------------------------------
# Dấu vết của kho mẹ — loại lỗi duy nhất mà việc tách repo sinh ra
# --------------------------------------------------------------------------

VAN_BAN_CONG_KHAI = ["README.md", "README.vi.md", "CONTRIBUTING.md", "docs/images/README.md"]

# Đường dẫn tuyệt đối kiểu `C:\Users\<tên người dùng>\...`. Tên người dùng của
# máy tác giả là thông tin nhận diện, và đường dẫn ấy không tồn tại ở máy ai khác.
DUONG_DAN_MAY_TAC_GIA = re.compile(r"C:[\\/]Users[\\/](?!<)[A-Za-z0-9_.-]+[\\/]", re.I)


@pytest.mark.parametrize("ten_file", VAN_BAN_CONG_KHAI)
def test_tai_lieu_khong_chua_duong_dan_tuyet_doi_cua_mot_may_cu_the(ten_file):
    """Người đọc phải chạy được mà không cần đoán xem chỗ nào là tên máy tác giả."""
    van_ban = (ROOT / ten_file).read_text(encoding="utf-8")
    dinh = DUONG_DAN_MAY_TAC_GIA.findall(van_ban)
    assert not dinh, "%s còn đường dẫn của một máy cụ thể: %s" % (ten_file, dinh)


@pytest.mark.parametrize("ten_file", VAN_BAN_CONG_KHAI)
def test_tai_lieu_khong_gia_dinh_minh_van_nam_trong_kho_me(ten_file):
    """Các cụm chỉ định bối cảnh là nơi giả định về kho mẹ ẩn náu. Chúng đúng khi
    repo còn là thư mục con, và sai — mà vẫn đọc xuôi — sau khi tách ra."""
    van_ban = (ROOT / ten_file).read_text(encoding="utf-8")
    cam = [
        "đã có sẵn trong repo này",
        "hai máy chủ đã có trong repo",
        "các máy chủ khác trong repo",
        "thư mục cha",
    ]
    dinh = [c for c in cam if c in van_ban]
    assert not dinh, "%s còn giả định của kho mẹ: %s" % (ten_file, dinh)


@pytest.mark.parametrize("ten_file", VAN_BAN_CONG_KHAI)
def test_lenh_cd_vao_thu_muc_repo_luon_di_ngay_sau_git_clone(ten_file):
    """`cd civil3d-mcp` hợp lệ hay không tuỳ chỗ nó đứng, nên không cấm thẳng được.

    Ngay sau `git clone` thì đúng: người đọc vừa tạo ra thư mục đó. Đứng một mình
    ở mục kiểm thử hay mục sử dụng thì sai — nó giả định người đọc đang ở thư mục
    CHA, tức giả định repo này vẫn là thư mục con của một kho lớn hơn. Đây đúng là
    loại câu vẫn đọc trôi chảy sau khi đã sai.
    """
    dong = (ROOT / ten_file).read_text(encoding="utf-8").splitlines()
    lac_long = []
    for i, d in enumerate(dong):
        if d.strip() != "cd civil3d-mcp":
            continue
        truoc = " ".join(dong[max(0, i - 3):i])
        if "git clone" not in truoc:
            lac_long.append(i + 1)
    assert not lac_long, (
        "%s: 'cd civil3d-mcp' ở dòng %s không đi sau 'git clone', nên nó giả định "
        "người đọc đang đứng ở thư mục cha" % (ten_file, lac_long)
    )


# --------------------------------------------------------------------------
# Ảnh minh chứng
# --------------------------------------------------------------------------


def test_anh_minh_chung_khong_bi_gitignore_chan():
    """Chặn nhầm thì README trên GitHub hiện ô ảnh vỡ, mà ở máy vẫn nhìn thấy
    bình thường nên người sửa `.gitignore` không hề biết."""
    anh = sorted((ROOT / "docs" / "images").glob("*.png"))
    assert anh, "docs/images/ phải có ít nhất một ảnh minh chứng"

    for path in anh:
        out = subprocess.run(
            ["git", "check-ignore", str(path.relative_to(ROOT)).replace("\\", "/")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        # check-ignore trả 0 khi đường dẫn BỊ chặn, 1 khi không.
        assert out.returncode != 0, "%s đang bị .gitignore chặn" % path.name


def test_moi_anh_deu_duoc_nhac_toi_trong_tai_lieu():
    """Ảnh không README nào nhắc tới là ảnh chết: nó vẫn nằm trong lịch sử git,
    vẫn phải rà thông tin nhận diện, mà không phục vụ ai."""
    van_ban = "\n".join(
        (ROOT / p).read_text(encoding="utf-8")
        for p in ["README.md", "README.vi.md", "docs/images/README.md"]
    )
    for path in sorted((ROOT / "docs" / "images").glob("*.png")):
        assert path.name in van_ban, "%s không được nhắc trong tài liệu nào" % path.name


def test_moi_anh_duoc_readme_nhac_toi_deu_ton_tai():
    """Chiều ngược lại: một liên kết ảnh gõ sai tên chỉ lộ ra khi đã đẩy lên
    GitHub, vì ở máy không ai mở README dưới dạng đã render."""
    co_that = {p.name for p in (ROOT / "docs" / "images").glob("*.png")}
    for ten_file in ["README.md", "README.vi.md", "docs/images/README.md"]:
        van_ban = (ROOT / ten_file).read_text(encoding="utf-8")
        for ten_anh in re.findall(r"docs/images/([A-Za-z0-9_.-]+\.png)", van_ban):
            assert ten_anh in co_that, "%s trỏ tới ảnh không có: %s" % (ten_file, ten_anh)


def test_khong_commit_cau_hinh_thuc_te():
    out = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".mcp.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert out.returncode != 0, ".mcp.json đang bị theo dõi bởi git"
