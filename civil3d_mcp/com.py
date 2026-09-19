"""
Lớp giao tiếp COM tới Autodesk Civil 3D
========================================
Civil 3D chạy TRÊN AutoCAD: tiến trình vẫn là `acad.exe`, và API tự động hoá gồm
hai tầng chồng lên nhau.

  * Tầng AutoCAD  - `AutoCAD.Application` -> ModelSpace, Layers, SendCommand...
  * Tầng Civil 3D - lấy qua `acad.GetInterfaceObject("AeccXUiLand.AeccApplication.<ver>")`
    -> Surfaces, Alignments, Profiles; và `AeccXUiRoadway.AeccRoadwayApplication.<ver>`
    -> Corridors, Assemblies.

Số hiệu `<ver>` KHÔNG phải năm phát hành (Civil 3D 2026 = 13.8). Module này dò số
đó từ registry chứ không hardcode, nên cùng một bản code chạy được với phiên bản
Civil 3D đang thật sự cài trên máy.

BẪY QUAN TRỌNG: nếu máy có cả AutoCAD thuần lẫn Civil 3D, GetActiveObject có thể
bám vào bản AutoCAD thuần. Bản đó trả lời mọi lệnh AutoCAD bình thường, chỉ tới
khi GetInterfaceObject thất bại mới lộ ra là bám nhầm. connect() vì vậy luôn kiểm
chứng tầng Civil 3D ngay khi bám được, và báo lỗi nói rõ đang bám vào cửa sổ nào.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import winreg
from typing import Any, Dict, List, Optional, Tuple

import pythoncom
import win32com.client


class C3DError(Exception):
    """Lỗi đã được diễn giải sang thông điệp tiếng Việt có tính hành động."""


# --------------------------------------------------------------------------
# Bảng mã HRESULT
# --------------------------------------------------------------------------
# Civil 3D đang bận (đang trong lệnh, đang mở hộp thoại, đang rebuild surface).
_BUSY_HRESULTS = {
    -2147418111,  # 0x80010001 RPC_E_CALL_REJECTED
    -2147417846,  # 0x8001010A RPC_E_SERVERCALL_RETRYLATER
    -2147417851,  # 0x80010105 RPC_E_SERVERFAULT
}
# Con trỏ COM đã chết (ứng dụng bị đóng hoặc khởi động lại).
_DEAD_HRESULTS = {
    -2147417848,  # 0x80010108 RPC_E_DISCONNECTED
    -2147023174,  # 0x800706BA RPC_S_SERVER_UNAVAILABLE
    -2147220995,  # 0x800401FD CO_E_OBJNOTCONNECTED
    -2147221021,  # 0x800401E3 MK_E_UNAVAILABLE
}

MAX_ATTEMPTS = 6
RETRY_DELAY = 0.4
CONNECTION_TTL = 1.0      # giây - trong khoảng này coi kết nối COM vẫn còn sống

# Thứ tự ưu tiên khi bám vào AutoCAD. Bản đời mới đứng trước vì Civil 3D luôn chạy
# trên nền AutoCAD mới nhất mà nó được cài kèm.
ACAD_PROG_IDS = (
    "AutoCAD.Application.25.1",
    "AutoCAD.Application.25",
    "AutoCAD.Application.24.3",
    "AutoCAD.Application.24.2",
    "AutoCAD.Application.24.1",
    "AutoCAD.Application",
)


def hresult(exc: BaseException) -> Optional[int]:
    """Trích mã HRESULT từ một pythoncom.com_error."""
    if isinstance(exc, pythoncom.com_error):
        try:
            return int(exc.args[0])
        except Exception:
            return None
    return None


# Mã lỗi hay gặp nằm SÂU trong EXCEPINFO của Civil 3D. Không có bảng này thì mọi
# lỗi tham số đều hiện ra dưới dạng "Exception occurred" - một thông điệp không
# phân biệt được "sai tên style" với "thiếu một thuộc tính bắt buộc".
_SCODE_HINTS = {
    -2147024809: ("Tham số không hợp lệ (E_INVALIDARG). Với các lời gọi tạo đối tượng, "
                  "nguyên nhân thường là một thuộc tính BẮT BUỘC chưa được gán - "
                  "AeccTinCreationData đòi cả Layer lẫn BaseLayer, không chỉ Name và Style."),
    -2147024894: "Không tìm thấy file (ERROR_FILE_NOT_FOUND).",
    -2147024891: "Bị từ chối quyền truy cập (E_ACCESSDENIED).",
    -2147024882: "Không đủ bộ nhớ (E_OUTOFMEMORY).",
}


def _excepinfo_scode(exc: BaseException) -> Optional[int]:
    """Mã scode nằm ở phần tử cuối của EXCEPINFO - chỗ Civil 3D giấu lý do thật."""
    if isinstance(exc, pythoncom.com_error):
        try:
            excep = exc.args[2]
            if excep and len(excep) >= 6 and isinstance(excep[5], int) and excep[5] != 0:
                return int(excep[5])
        except Exception:
            pass
    return None


def _com_description(exc: BaseException) -> str:
    """Lấy mô tả lỗi mà chính Civil 3D trả về (nằm trong EXCEPINFO).

    EXCEPINFO thường có mô tả rỗng nhưng scode thì có giá trị; khi đó mô tả duy
    nhất còn lại là "Exception occurred", vô dụng cho chẩn đoán. Hàm này ghép thêm
    scode và lời giải thích của nó.
    """
    base = ""
    if isinstance(exc, pythoncom.com_error):
        try:
            excep = exc.args[2]
            if excep and len(excep) > 2 and excep[2]:
                base = str(excep[2]).strip()
        except Exception:
            pass
        if not base:
            try:
                base = str(exc.args[1] or "").strip()
            except Exception:
                base = ""
        scode = _excepinfo_scode(exc)
        if scode is not None:
            hint = _SCODE_HINTS.get(scode)
            detail = f"scode=0x{scode & 0xFFFFFFFF:08X}"
            base = f"{base} ({detail}{': ' + hint if hint else ''})" if base else (
                hint or detail)
        return base
    return str(exc)


def explain(exc: BaseException) -> str:
    """Dịch một ngoại lệ COM thành thông điệp tiếng Việt có tính hành động."""
    hr = hresult(exc)
    desc = _com_description(exc)
    if hr in _BUSY_HRESULTS:
        return ("Civil 3D đang bận (đang chạy lệnh, đang mở hộp thoại, hoặc đang rebuild "
                "surface). Nhấn ESC trong Civil 3D rồi thử lại.")
    if hr in _DEAD_HRESULTS:
        return ("Mất kết nối tới Civil 3D (ứng dụng đã bị đóng hoặc khởi động lại). "
                "Hãy mở lại Civil 3D với ít nhất một bản vẽ.")
    if hr == -2147352571:   # 0x80020005 DISP_E_TYPEMISMATCH
        return f"Sai kiểu dữ liệu truyền vào Civil 3D: {desc}"
    if hr == -2147352567:   # 0x80020009 DISP_E_EXCEPTION
        return f"Civil 3D từ chối thao tác: {desc}"
    if hr == -2147352562:   # 0x8002000E DISP_E_BADPARAMCOUNT
        return f"Sai số lượng tham số gọi vào Civil 3D: {desc}"
    if hr == -2147352570:   # 0x80020006 DISP_E_UNKNOWNNAME
        return (f"Civil 3D không có thuộc tính/phương thức này: {desc}. Nhiều khả năng "
                "phiên bản COM đang chạy khác với phiên bản mà tool giả định.")
    # Mã lạ: giữ cả mô tả lẫn HRESULT. Thiếu con số thì lỗi này không tra được, mà
    # mô tả chung của COM ("Exception occurred") thường chẳng nói lên điều gì.
    if desc:
        return f"{desc} (HRESULT={hr})" if hr is not None else desc
    return f"Lỗi COM không xác định (HRESULT={hr})"


def is_busy(exc: BaseException) -> bool:
    return hresult(exc) in _BUSY_HRESULTS


def is_dead(exc: BaseException) -> bool:
    return hresult(exc) in _DEAD_HRESULTS


def reraise_if_transient(exc: BaseException) -> None:
    """Ném lại các lỗi CHỈ mang tính tạm thời để guard còn cơ hội thử lại.

    Dùng trong những khối `except` vốn dịch lỗi thành "không tìm thấy X": nếu không
    lọc, một lần Civil 3D bận sẽ bị báo nhầm thành "surface không tồn tại" và mất
    luôn cơ chế thử lại.
    """
    if is_busy(exc) or is_dead(exc):
        raise exc
    if isinstance(exc, AttributeError):
        raise exc


def read_optional(getter, default=None, retry_delay: float = 0.3):
    """Đọc một thuộc tính COM CÓ THỂ không tồn tại trên đối tượng này.

    pywin32 phát AttributeError cho hai tình huống hoàn toàn khác nhau: con trỏ
    đang bận hoặc đã chết (tạm thời, phải thử lại) và thuộc tính không có trên
    dispatch này (vĩnh viễn, phải bỏ qua). Thông điệp của hai trường hợp giống
    nhau, nên không phân biệt được bằng cách đọc chuỗi lỗi.

    Phép phân biệt duy nhất đáng tin là THỜI GIAN: chờ một nhịp rồi đọc lại. Bận
    thì lần hai qua; không tồn tại thì hỏng y hệt lần đầu. Đây là lý do một bề mặt
    TIN thường và một volume surface dùng chung được cùng một hàm đọc thống kê,
    dù hai loại có tập thuộc tính khác nhau.
    """
    try:
        return getter()
    except Exception as exc:
        if is_busy(exc) or is_dead(exc):
            raise
        if not isinstance(exc, AttributeError):
            return default
    time.sleep(retry_delay)
    try:
        return getter()
    except Exception as exc:
        if is_busy(exc) or is_dead(exc):
            raise
        return default


class ComThread:
    """Thực thi mọi thao tác COM trên ĐÚNG MỘT luồng chuyên trách.

    Máy chủ MCP chạy tool đồng bộ trong thread pool nên mỗi lần gọi có thể rơi vào
    một luồng khác nhau, trong khi COM đòi CoInitialize riêng từng luồng và không
    cho dùng con trỏ giao diện chéo luồng. Dồn về một luồng vừa khử lỗi
    "CoInitialize has not been called", vừa tuần tự hoá lời gọi (bản thân Civil 3D
    cũng chỉ xử lý được một yêu cầu tại một thời điểm).
    """

    def __init__(self, name: str = "civil3d-com") -> None:
        self._queue: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._name = name

    def _ensure_started(self) -> threading.Thread:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
                self._thread.start()
            return self._thread

    def _run(self) -> None:
        pythoncom.CoInitialize()
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    return
                fn, args, kwargs, box, done = item
                try:
                    box.append((True, fn(*args, **kwargs)))
                except BaseException as exc:      # chuyển nguyên vẹn về luồng gọi
                    box.append((False, exc))
                finally:
                    done.set()
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def call(self, fn, *args, **kwargs):
        thread = self._ensure_started()
        if threading.current_thread() is thread:
            return fn(*args, **kwargs)           # lời gọi lồng nhau - chạy thẳng
        box: List[tuple] = []
        done = threading.Event()
        self._queue.put((fn, args, kwargs, box, done))
        done.wait()
        ok, value = box[0]
        if ok:
            return value
        raise value


# --------------------------------------------------------------------------
# Dò phiên bản và đường dẫn cài đặt từ registry
# --------------------------------------------------------------------------
# Cố tình KHÔNG gọi tiến trình phụ (PowerShell/reg.exe) ở đây: máy chủ MCP được
# client khởi chạy với môi trường do client quyết định, và một PATH rút gọn sẽ làm
# lệnh ngoài biến mất trong im lặng rồi trả về None - trông y hệt "chưa cài Civil 3D".
# winreg là API hệ thống, không phụ thuộc PATH.

def _version_sort_key(version: str) -> Tuple[int, ...]:
    """Sắp xếp theo số, không theo chuỗi: '13.10' phải đứng sau '13.8'."""
    parts: List[int] = []
    for chunk in version.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(-1)
    return tuple(parts)


def _iter_progid_versions(prefix: str) -> List[str]:
    """Hậu tố phiên bản của mọi ProgID bắt đầu bằng `prefix`, mới nhất đứng đầu.

    Ví dụ prefix "AeccXUiLand.AeccApplication." -> ["13.8"].
    """
    found: List[str] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, "SOFTWARE\\Classes")
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                if name.startswith(prefix):
                    suffix = name[len(prefix):]
                    if suffix and suffix not in found:
                        found.append(suffix)
        finally:
            key.Close()
    return sorted(found, key=_version_sort_key, reverse=True)


def discover_civil3d_versions() -> List[str]:
    """Số hiệu COM của các bản Civil 3D đang cài, mới nhất đứng đầu (vd ["13.8"])."""
    return _iter_progid_versions("AeccXUiLand.AeccApplication.")


def _read_value(key, name: str) -> str:
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return str(value)
    except OSError:
        return ""


def discover_install_paths() -> List[Dict[str, str]]:
    """Đường dẫn cài đặt của các sản phẩm Civil 3D tìm thấy trong registry.

    Trả về danh sách dict {product, location, release}. Dùng cho launch_civil3d và
    cho phần "môi trường thực nghiệm" của báo cáo nghiên cứu - phiên bản phần mềm
    là thông tin bắt buộc phải ghi lại khi công bố kết quả.
    """
    out: List[Dict[str, str]] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\Autodesk\\AutoCAD")
    except OSError:
        return out
    try:
        r = 0
        while True:
            try:
                release = winreg.EnumKey(root, r)
            except OSError:
                break
            r += 1
            try:
                rel_key = winreg.OpenKey(root, release)
            except OSError:
                continue
            try:
                p = 0
                while True:
                    try:
                        prod = winreg.EnumKey(rel_key, p)
                    except OSError:
                        break
                    p += 1
                    try:
                        with winreg.OpenKey(rel_key, prod) as pk:
                            name = _read_value(pk, "ProductName")
                            loc = _read_value(pk, "AcadLocation")
                    except OSError:
                        continue
                    if name and "civil" in name.lower() and loc:
                        out.append({"product": name, "location": loc, "release": release})
            finally:
                rel_key.Close()
    finally:
        root.Close()
    return out


def file_version(path: str) -> Optional[str]:
    """Số hiệu phiên bản của một file .exe, đọc bằng API Windows (không qua shell)."""
    if not path or not os.path.exists(path):
        return None
    try:
        import win32api
        block = win32api.GetFileVersionInfo(path, "\\")
        ms, ls = block["FileVersionMS"], block["FileVersionLS"]
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return None


# --------------------------------------------------------------------------
# Kết nối
# --------------------------------------------------------------------------

class Connection:
    """Ba con trỏ COM của một phiên Civil 3D, cộng số hiệu phiên bản đã dò được."""

    def __init__(self, acad: Any, land_app: Any, roadway_app: Any, version: str) -> None:
        self.acad = acad                  # AcadApplication
        self.land_app = land_app          # AeccApplication        (surface/alignment/profile)
        self.roadway_app = roadway_app    # AeccRoadwayApplication (corridor/assembly)
        self.version = version            # vd "13.8"


class AttachResult:
    """Kết quả một lần thử bám vào AutoCAD.

    `busy_seen` là thông tin KHÔNG thể bỏ: một phiên Civil 3D đang bận làm
    GetActiveObject hỏng với RPC_E_CALL_REJECTED cho MỌI ProgID, và nếu chỉ nhìn
    "không bám được" thì kết luận sẽ là "chưa chạy Civil 3D" - sai hẳn về nguyên
    nhân, và đẩy người dùng đi khởi động thêm một phiên thứ hai.
    """

    def __init__(self, app: Any, errors: List[str], busy_seen: bool) -> None:
        self.app = app
        self.errors = errors
        self.busy_seen = busy_seen

    def __iter__(self):
        """Cho phép giải nén như tuple (app, errors) để mã cũ vẫn chạy."""
        return iter((self.app, self.errors))


ATTACH_ATTEMPTS = 3


def attach_autocad() -> AttachResult:
    """Bám vào một tiến trình AutoCAD/Civil 3D đang chạy. KHÔNG tự khởi động.

    Thử lại khi ứng dụng báo bận: lúc Civil 3D đang nạp bản vẽ hoặc đang rebuild,
    nó từ chối mọi lời gọi COM trong vài giây rồi lại bình thường.
    """
    errors: List[str] = []
    busy_seen = False
    for attempt in range(ATTACH_ATTEMPTS):
        for prog_id in ACAD_PROG_IDS:
            try:
                return AttachResult(win32com.client.GetActiveObject(prog_id), errors, busy_seen)
            except Exception as exc:
                if is_busy(exc):
                    busy_seen = True
                if attempt == 0:
                    errors.append(f"{prog_id}: {exc}")
        if not busy_seen:
            break                      # không phải bận -> thử lại cũng vô ích
        time.sleep(RETRY_DELAY * (attempt + 1))

    try:
        import comtypes.client
        for prog_id in ACAD_PROG_IDS:
            try:
                return AttachResult(comtypes.client.GetActiveObject(prog_id), errors, busy_seen)
            except Exception as exc:
                if is_busy(exc):
                    busy_seen = True
                errors.append(f"comtypes {prog_id}: {exc}")
    except Exception:
        pass
    return AttachResult(None, errors, busy_seen)


BUSY_ATTACH_MESSAGE = (
    "Civil 3D ĐANG CHẠY nhưng từ chối mọi lời gọi COM vì đang bận (đang nạp bản vẽ, "
    "đang chạy lệnh, hoặc đang mở hộp thoại). Đây KHÔNG phải tình trạng chưa khởi "
    "động - đừng mở thêm một phiên nữa. Hãy nhấn ESC trong Civil 3D, đóng hộp thoại "
    "đang mở, rồi thử lại."
)


def connect() -> Connection:
    """Bám vào Civil 3D đang chạy và KIỂM CHỨNG tầng Civil 3D thật sự dùng được.

    Không bao giờ trả về một kết nối "nửa vời": hoặc cả tầng AutoCAD lẫn tầng
    Civil 3D đều sẵn sàng, hoặc ném C3DError nói rõ đang bám vào cửa sổ nào.
    """
    attached = attach_autocad()
    acad, errors = attached.app, attached.errors
    if acad is None:
        if attached.busy_seen:
            raise C3DError(BUSY_ATTACH_MESSAGE)
        raise C3DError(
            "Không tìm thấy phiên AutoCAD/Civil 3D nào đang chạy. Hãy mở Civil 3D "
            "(hoặc gọi tool launch_civil3d) rồi thử lại. Chi tiết: " + " | ".join(errors[:3])
        )

    versions = discover_civil3d_versions()
    if not versions:
        raise C3DError(
            "Registry không có ProgID AeccXUiLand.AeccApplication.* - máy này chưa cài "
            "Civil 3D, hoặc bản cài đã hỏng đăng ký COM."
        )

    last_err = ""
    for ver in versions:
        try:
            land = acad.GetInterfaceObject(f"AeccXUiLand.AeccApplication.{ver}")
        except Exception as exc:
            last_err = f"{ver}: {exc}"
            continue
        try:
            roadway = acad.GetInterfaceObject(f"AeccXUiRoadway.AeccRoadwayApplication.{ver}")
        except Exception:
            roadway = None      # corridor không dùng được, phần còn lại vẫn chạy
        return Connection(acad, land, roadway, ver)

    try:
        caption = str(acad.Caption)
    except Exception:
        caption = "<không đọc được>"
    raise C3DError(
        "Đã bám được vào một phiên AutoCAD nhưng KHÔNG lấy được giao diện Civil 3D. "
        f"Cửa sổ đang bám: {caption!r}. Nguyên nhân thường gặp: đang mở AutoCAD thuần "
        "chứ không phải Civil 3D (máy này có cả hai bản). Hãy đóng AutoCAD thuần hoặc "
        f"khởi động Civil 3D rồi thử lại. Chi tiết: {last_err}"
    )
