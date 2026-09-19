"""
Civil3DClient - mặt tiền duy nhất để các tool nói chuyện với Civil 3D
=====================================================================
Giữ kết nối COM, tự phục hồi khi Civil 3D bận hoặc bị khởi động lại, và cung cấp
các tiện ích dùng chung (đóng gói VARIANT, tra cứu phần tử trong collection,
thông tin tài liệu). Nghiệp vụ chuyên ngành nằm ở surfaces.py / alignments.py /
corridors.py, mỗi module nhận client này làm tham số đầu.

Quy ước xuyên suốt: mọi phương thức GHI đều đọc lại kết quả để xác nhận, và trả
về khoá `verified` nói rõ đã kiểm chứng bằng cách nào. Một lời gọi COM không ném
lỗi KHÔNG phải bằng chứng là thao tác đã có hiệu lực.
"""

from __future__ import annotations

import functools
import os
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence

import pythoncom
from win32com.client import VARIANT

from .com import (
    BUSY_ATTACH_MESSAGE,
    CONNECTION_TTL,
    MAX_ATTEMPTS,
    RETRY_DELAY,
    C3DError,
    ComThread,
    Connection,
    attach_autocad,
    connect,
    discover_civil3d_versions,
    discover_install_paths,
    explain,
    file_version,
    is_busy,
    is_dead,
)

# Trần số điểm cho một lời gọi COM nhận mảng. Con số này là một hàng rào tự đặt,
# không phải giới hạn được Autodesk công bố: SAFEARRAY quá lớn làm Civil 3D treo
# thay vì báo lỗi, nên phía gọi phải chia lô chứ đừng chờ COM phàn nàn.
MAX_POINTS_PER_CALL = 5000


def variant_point(x: float, y: float, z: Optional[float] = None) -> VARIANT:
    """Đóng gói một toạ độ thành SAFEARRAY doubles như Civil 3D đòi hỏi."""
    values = [float(x), float(y)] if z is None else [float(x), float(y), float(z)]
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, values)


def variant_doubles(values: Sequence[float]) -> VARIANT:
    """Đóng gói một dãy số thực thành SAFEARRAY doubles."""
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) for v in values])


def flatten_points(points: Sequence[Sequence[float]], dims: int = 3) -> List[float]:
    """Trải [[x,y,z], ...] thành [x,y,z,x,y,z,...] - dạng mảng phẳng của COM."""
    flat: List[float] = []
    for p in points:
        if len(p) < dims:
            raise ValueError(f"Điểm {p!r} thiếu toạ độ: cần {dims} giá trị")
        for i in range(dims):
            flat.append(float(p[i]))
    return flat


class Civil3DClient:
    """Bọc toàn bộ API COM của Civil 3D. Mọi phương thức public đều tự phục hồi."""

    def __init__(self) -> None:
        self.conn: Optional[Connection] = None
        self._verified_at = 0.0
        self._worker = ComThread()
        self._lock = threading.RLock()

    # ==================================================================
    # Kết nối và tự phục hồi
    # ==================================================================

    def call(self, fn: Callable, *args, **kwargs):
        """Chạy một hàm trên luồng COM chuyên trách."""
        return self._worker.call(fn, *args, **kwargs)

    def _coinit(self) -> None:
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass

    def ensure_connected(self) -> Connection:
        """Bảo đảm kết nối còn sống. Gọi trước mọi thao tác COM.

        Luôn chạy trên luồng COM chuyên trách: con trỏ giao diện COM không dùng
        được chéo luồng, nên nếu hàm này chạy ở luồng gọi thì con trỏ nó tạo ra sẽ
        vô dụng - và lỗi chỉ lộ ra muộn hơn, ở một chỗ khác, dưới một cái tên khác.
        """
        return self._worker.call(self._ensure_connected_impl)

    def _ensure_connected_impl(self) -> Connection:
        with self._lock:
            self._coinit()
            if self.conn is not None and time.time() - self._verified_at < CONNECTION_TTL:
                return self.conn
            last: Optional[BaseException] = None
            for attempt in range(MAX_ATTEMPTS):
                try:
                    if self.conn is None:
                        self.conn = connect()
                    self.conn.acad.Documents.Count          # ping, phát hiện con trỏ chết
                    self._verified_at = time.time()
                    return self.conn
                except C3DError:
                    raise
                except Exception as exc:
                    last = exc
                    if is_busy(exc):
                        time.sleep(RETRY_DELAY * (attempt + 1))
                        continue
                    self.reset()
                    if attempt < MAX_ATTEMPTS - 1:
                        time.sleep(RETRY_DELAY)
            raise C3DError(explain(last) if last else "Không kết nối được tới Civil 3D.")

    def reset(self) -> None:
        """Vứt mọi con trỏ COM để lần gọi sau dựng lại kết nối từ đầu."""
        self.conn = None
        self._verified_at = 0.0

    def _invoke(self, fn: Callable, args: tuple, kwargs: dict):
        """Chạy `fn(self, *args)` trên luồng COM, có thử lại và dịch lỗi.

        An toàn để thử lại vì mọi thao tác ghi trong dự án này đều kiểm chứng hiệu
        lực trước khi trả về, nên một lần chạy lại chỉ có thể lặp lại công việc chứ
        không sinh ra đối tượng trùng lặp không phát hiện được.
        """
        last: Optional[BaseException] = None
        revived = 0
        for attempt in range(MAX_ATTEMPTS):
            try:
                self.ensure_connected()
                return self.call(fn, self, *args, **kwargs)
            except C3DError:
                raise
            except AttributeError as exc:
                # AttributeError từ một đối tượng COM luôn là dấu hiệu tạm thời (con
                # trỏ đã chết, hoặc Civil 3D bận không phân giải được tên), chứ không
                # phải "thuộc tính không tồn tại".
                last = exc
                revived += 1
                if revived <= 3:
                    if "<unknown>" in str(exc):
                        self.reset()
                    time.sleep(RETRY_DELAY * revived)
                    continue
                raise C3DError(
                    f"Không truy cập được đối tượng Civil 3D ({exc}). Civil 3D có thể "
                    "đang bận hoặc bản vẽ đã bị đóng - kiểm tra ứng dụng rồi thử lại."
                ) from exc
            except pythoncom.com_error as exc:
                last = exc
                if is_busy(exc):
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                if is_dead(exc):
                    self.reset()
                    time.sleep(RETRY_DELAY)
                    continue
                raise C3DError(explain(exc)) from exc
        raise C3DError(explain(last) if last else "Thao tác Civil 3D thất bại.")

    def run(self, fn: Callable, *args, **kwargs):
        """Chạy một hàm nghiệp vụ dạng `fn(client, ...)` với đủ bảo hộ kết nối.

        Mọi hàm trong surfaces.py / alignments.py / corridors.py / research.py đều
        phải đi qua đây. Gọi thẳng chúng từ luồng khác sẽ dùng con trỏ COM chéo
        luồng - lỗi đó không hiện ra ngay mà biểu hiện muộn hơn dưới dạng
        AttributeError ở một dòng chẳng liên quan gì.
        """
        return self._invoke(fn, args, kwargs)

    @staticmethod
    def guard(fn):
        """Bọc một phương thức của chính client: ensure_connected + retry + dịch lỗi."""
        @functools.wraps(fn)
        def wrapper(self: "Civil3DClient", *args, **kwargs):
            return self._invoke(fn, args, kwargs)
        return wrapper

    # ==================================================================
    # Truy cập tài liệu
    # ==================================================================

    @property
    def acad_doc(self):
        """AcadDocument hiện hành (tầng AutoCAD)."""
        conn = self.ensure_connected()
        if conn.acad.Documents.Count == 0:
            raise C3DError("Civil 3D đang mở nhưng không có bản vẽ nào. Hãy mở một file .dwg.")
        return conn.acad.ActiveDocument

    @property
    def aecc_doc(self):
        """AeccDocument hiện hành (tầng Civil 3D: Surfaces, Alignments, Points...)."""
        conn = self.ensure_connected()
        doc = conn.land_app.ActiveDocument
        if doc is None:
            raise C3DError("Không lấy được AeccDocument. Hãy mở một bản vẽ trong Civil 3D.")
        return doc

    @property
    def roadway_doc(self):
        """AeccRoadwayDocument hiện hành (Corridors, Assemblies, Subassemblies)."""
        conn = self.ensure_connected()
        if conn.roadway_app is None:
            raise C3DError(
                "Không lấy được giao diện AeccXUiRoadway - phần corridor/assembly không "
                "dùng được trong phiên này. Các tool về surface, alignment, profile vẫn chạy."
            )
        doc = conn.roadway_app.ActiveDocument
        if doc is None:
            raise C3DError("Không lấy được AeccRoadwayDocument. Hãy mở một bản vẽ.")
        return doc

    # ==================================================================
    # Trạng thái và thông tin
    # ==================================================================

    def get_status(self) -> Dict[str, object]:
        """Trạng thái kết nối. KHÔNG ném lỗi khi chưa kết nối - trả về ok=False.

        Đây là tool đầu tiên nên gọi: nó phân biệt rõ ba tình huống hay bị lẫn -
        chưa cài Civil 3D, đã cài nhưng chưa chạy, và đang chạy nhưng bám nhầm vào
        AutoCAD thuần. Vì phải trả lời được cả khi chưa kết nối, đây là phương thức
        duy nhất không đi qua guard.
        """
        return self.call(self._get_status_impl)

    def _get_status_impl(self) -> Dict[str, object]:
        versions = discover_civil3d_versions()
        installs = discover_install_paths()
        info: Dict[str, object] = {
            "installed": bool(versions),
            "com_versions_installed": versions,
            "installations": installs,
        }
        if not versions:
            info["ok"] = False
            info["error"] = ("Máy này chưa cài Civil 3D (registry không có ProgID "
                             "AeccXUiLand.AeccApplication.*).")
            return info

        attached = attach_autocad()
        acad, errors = attached.app, attached.errors
        if acad is None:
            info["ok"] = False
            # "Đang bận" và "chưa chạy" cho cùng một triệu chứng là không bám được,
            # nhưng hai kết luận trái ngược nhau về việc cần làm gì tiếp.
            info["running"] = bool(attached.busy_seen)
            info["busy"] = bool(attached.busy_seen)
            info["error"] = (BUSY_ATTACH_MESSAGE if attached.busy_seen else
                             "Chưa có phiên AutoCAD/Civil 3D nào đang chạy. Gọi "
                             "launch_civil3d để khởi động, quá trình mở mất 1-3 phút.")
            info["attach_errors"] = errors[:3]
            return info

        info["running"] = True
        try:
            conn = self.ensure_connected()
        except C3DError as exc:
            info["ok"] = False
            info["error"] = str(exc)
            return info

        doc_count = conn.acad.Documents.Count
        info.update({
            "ok": True,
            "com_version_active": conn.version,
            "application": self._safe(lambda: str(conn.acad.Caption)),
            "autocad_version": self._safe(lambda: str(conn.acad.Version)),
            "roadway_interface": conn.roadway_app is not None,
            "open_documents": doc_count,
        })
        if doc_count:
            info["active_document"] = self._safe(lambda: str(conn.acad.ActiveDocument.Name))
        else:
            info["note"] = "Không có bản vẽ nào đang mở - hãy mở một file .dwg trước khi thao tác."
        return info

    def get_document_info(self) -> Dict[str, object]:
        """Thông tin chi tiết bản vẽ hiện hành, kèm số lượng đối tượng Civil 3D."""
        adoc = self.acad_doc
        cdoc = self.aecc_doc
        out: Dict[str, object] = {
            "name": str(adoc.Name),
            "full_path": self._safe(lambda: str(adoc.FullName)),
            "saved": bool(adoc.Saved),
            "read_only": self._safe(lambda: bool(adoc.ReadOnly)),
            "measurement": self._measurement(adoc),
            "drawing_units": self._safe(lambda: int(adoc.GetVariable("INSUNITS"))),
        }
        out["counts"] = {
            "surfaces": self._safe(lambda: int(cdoc.Surfaces.Count), default=None),
            "alignments_siteless": self._safe(lambda: int(cdoc.AlignmentsSiteless.Count), default=None),
            "sites": self._safe(lambda: int(cdoc.Sites.Count), default=None),
            "cogo_points": self._safe(lambda: int(cdoc.Points.Count), default=None),
            "point_groups": self._safe(lambda: int(cdoc.PointGroups.Count), default=None),
        }
        try:
            rdoc = self.roadway_doc
            out["counts"]["corridors"] = self._safe(lambda: int(rdoc.Corridors.Count), default=None)
            out["counts"]["assemblies"] = self._safe(lambda: int(rdoc.Assemblies.Count), default=None)
        except C3DError:
            out["counts"]["corridors"] = None
            out["counts"]["assemblies"] = None
            out["roadway_note"] = "Giao diện corridor không sẵn sàng trong phiên này."
        return out

    @staticmethod
    def _measurement(adoc) -> str:
        try:
            return "metric" if int(adoc.GetVariable("MEASUREMENT")) == 1 else "imperial"
        except Exception:
            return "unknown"

    @staticmethod
    def _safe(getter: Callable, default: object = "") -> object:
        """Đọc một thuộc tính COM có thể không tồn tại, không làm hỏng cả báo cáo."""
        try:
            return getter()
        except Exception:
            return default

    # ==================================================================
    # Quản lý bản vẽ
    # ==================================================================

    def list_documents(self) -> List[Dict[str, object]]:
        conn = self.ensure_connected()
        docs = conn.acad.Documents
        active = self._safe(lambda: str(conn.acad.ActiveDocument.Name), default=None)
        out: List[Dict[str, object]] = []
        for i in range(docs.Count):
            doc = docs.Item(i)
            name = str(doc.Name)
            out.append({
                "index": i,
                "name": name,
                "path": self._safe(lambda d=doc: str(d.FullName)),
                "saved": self._safe(lambda d=doc: bool(d.Saved), default=None),
                "active": name == active,
            })
        return out

    def open_document(self, path: str, read_only: bool = False) -> Dict[str, object]:
        path = os.path.abspath(path)
        if not os.path.exists(path):
            raise C3DError(f"Không tìm thấy file: {path}")
        if os.path.splitext(path)[1].lower() not in (".dwg", ".dwt", ".dxf"):
            raise C3DError("Chỉ mở được .dwg, .dwt hoặc .dxf.")
        conn = self.ensure_connected()
        conn.acad.Documents.Open(path, read_only)
        self._verified_at = 0.0
        # Kiểm chứng: bản vẽ mới phải là bản vẽ hiện hành.
        opened = str(self.acad_doc.FullName)
        return {
            "opened": path,
            "active_document": opened,
            "verified": os.path.normcase(opened) == os.path.normcase(path),
        }

    def new_document(self, template_path: Optional[str] = None) -> Dict[str, object]:
        """Tạo bản vẽ MỚI từ một template Civil 3D.

        Đây là cách đúng để bắt đầu một bài toán: mở thẳng file .dwt rồi làm việc
        trên đó là đang sửa chính template của máy, và một lần lưu nhầm sẽ làm hỏng
        template cho mọi bản vẽ sau.
        """
        conn = self.ensure_connected()
        if template_path:
            template_path = os.path.abspath(template_path)
            if not os.path.exists(template_path):
                raise C3DError(f"Không tìm thấy template: {template_path}")
            doc = conn.acad.Documents.Add(template_path)
        else:
            doc = conn.acad.Documents.Add()
        self._verified_at = 0.0
        name = str(doc.Name)
        return {
            "created": name,
            "template": template_path,
            "open_documents": int(conn.acad.Documents.Count),
            "verified": bool(name),
            "verified_by": "đọc lại tên bản vẽ mới từ đối tượng Civil 3D trả về",
        }

    def save_document(self, path: Optional[str] = None) -> Dict[str, object]:
        adoc = self.acad_doc
        if path:
            path = os.path.abspath(path)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            adoc.SaveAs(path)
        else:
            adoc.Save()
        saved_to = str(adoc.FullName)
        return {
            "saved_to": saved_to,
            "exists_on_disk": os.path.exists(saved_to),
            "saved_flag": bool(adoc.Saved),
            "verified": os.path.exists(saved_to) and bool(adoc.Saved),
        }

    def activate_document(self, name_or_index) -> Dict[str, object]:
        conn = self.ensure_connected()
        docs = conn.acad.Documents
        target = None
        if isinstance(name_or_index, int) or str(name_or_index).isdigit():
            idx = int(name_or_index)
            if not 0 <= idx < docs.Count:
                raise C3DError(f"Chỉ số bản vẽ {idx} nằm ngoài khoảng 0..{docs.Count - 1}.")
            target = docs.Item(idx)
        else:
            wanted = str(name_or_index).lower()
            for i in range(docs.Count):
                doc = docs.Item(i)
                if str(doc.Name).lower() == wanted or str(doc.Name).lower().startswith(wanted):
                    target = doc
                    break
        if target is None:
            raise C3DError(f"Không có bản vẽ nào tên {name_or_index!r} đang mở.")
        target.Activate()
        self._verified_at = 0.0
        active = str(self.acad_doc.Name)
        return {"active_document": active, "verified": active == str(target.Name)}

    def send_command(self, command: str) -> Dict[str, object]:
        """Gửi một dòng lệnh vào Civil 3D.

        CẢNH BÁO: lệnh chạy BẤT ĐỒNG BỘ - COM trả về ngay khi lệnh được xếp hàng,
        không phải khi lệnh chạy xong, và không có mã lỗi nào quay lại. Sau khi gửi
        lệnh phải kiểm chứng hiệu lực bằng một tool đọc (list_surfaces, ...) chứ
        không được coi việc gửi trót lọt là bằng chứng thành công.
        """
        if not command:
            raise C3DError("Chuỗi lệnh rỗng.")
        if not command.endswith("\n"):
            command += "\n"
        self.acad_doc.SendCommand(command)
        return {
            "sent": command.strip(),
            "verified": False,
            "note": ("Lệnh đã được xếp hàng. COM không báo kết quả thực thi - hãy gọi một "
                     "tool đọc để xác nhận hiệu lực."),
        }

    # ==================================================================
    # Khởi động ứng dụng
    # ==================================================================

    def launch(self, measurement: str = "metric") -> Dict[str, object]:
        """Khởi động Civil 3D bằng đúng tham số dòng lệnh của shortcut chính hãng.

        Chạy acad.exe trần sẽ ra AutoCAD thuần, KHÔNG có Civil 3D: phải kèm
        /ld AecBase.dbx, /p hồ sơ C3D và /product C3D thì các module Civil 3D mới nạp.
        """
        installs = discover_install_paths()
        if not installs:
            raise C3DError("Không tìm thấy bản cài Civil 3D nào trong registry.")
        location = installs[0]["location"]
        exe = os.path.join(location, "acad.exe")
        if not os.path.exists(exe):
            raise C3DError(f"Không tìm thấy acad.exe tại {location}")
        profile = "<<C3D_Metric>>" if str(measurement).lower().startswith("m") else "<<C3D_Imperial>>"
        args = [
            exe,
            "/ld", os.path.join(location, "AecBase.dbx"),
            "/p", profile,
            "/product", "C3D",
            "/language", "en-US",
        ]
        proc = subprocess.Popen(args, cwd=location, close_fds=True)
        return {
            "launched": True,
            "pid": proc.pid,
            "product": installs[0]["product"],
            "executable": exe,
            "executable_version": file_version(exe),
            "profile": profile,
            "verified": False,
            "note": ("Civil 3D cần 1-3 phút để nạp xong. Gọi check_civil3d_connection để "
                     "biết khi nào sẵn sàng; trước đó mọi tool khác sẽ báo chưa kết nối."),
        }

    def ensure_layer(self, name: Optional[str]) -> str:
        """Bảo đảm layer tồn tại, tạo nếu chưa có, và trả về tên dùng được.

        Các hàm tạo đối tượng của Civil 3D nhận TÊN layer và đòi layer đó đã có
        thật: tên chưa tồn tại trả về lỗi "Key not found", còn chuỗi rỗng cũng vậy.
        Thông điệp đó không nói layer nào sai, nên chuẩn hoá ở đây một lần thay vì
        để mỗi hàm tạo tự xoay xở.
        """
        layer = (name or "").strip() or "0"
        layers = self.acad_doc.Layers
        try:
            layers.Item(layer)
            return layer
        except Exception as exc:
            from .com import reraise_if_transient
            reraise_if_transient(exc)
        layers.Add(layer)
        return layer

    # ==================================================================
    # Tiện ích tra cứu collection
    # ==================================================================

    @staticmethod
    def collection_names(collection) -> List[str]:
        """Tên của mọi phần tử trong một collection Civil 3D."""
        names: List[str] = []
        for i in range(int(collection.Count)):
            try:
                names.append(str(collection.Item(i).Name))
            except Exception as exc:
                from .com import reraise_if_transient
                reraise_if_transient(exc)
                names.append(f"<phần tử {i} không đọc được tên>")
        return names

    @classmethod
    def find_item(cls, collection, name: str, kind: str = "đối tượng"):
        """Tìm phần tử theo tên. Ném C3DError liệt kê các tên có thật nếu trượt.

        Item(name) của Civil 3D ném lỗi COM chung chung khi không thấy; thông điệp
        đó không nói được tên nào đang có, nên người dùng không biết mình gõ sai ở đâu.
        """
        wanted = str(name).strip()
        try:
            item = collection.Item(wanted)
            if item is not None:
                return item
        except Exception as exc:
            from .com import reraise_if_transient
            reraise_if_transient(exc)
        lowered = wanted.lower()
        for i in range(int(collection.Count)):
            try:
                item = collection.Item(i)
                if str(item.Name).lower() == lowered:
                    return item
            except Exception as exc:
                from .com import reraise_if_transient
                reraise_if_transient(exc)
        available = cls.collection_names(collection)
        raise C3DError(
            f"Không có {kind} tên {wanted!r} trong bản vẽ. Đang có: "
            + (", ".join(repr(n) for n in available[:20]) if available else "(chưa có cái nào)")
        )


# Gắn guard cho các phương thức cần kết nối. Làm ở đây thay vì bằng decorator tại
# chỗ khai báo để get_status() - phương thức DUY NHẤT phải trả lời được khi chưa
# kết nối - không bị bọc.
for _name in (
    "list_documents", "open_document", "new_document", "save_document",
    "activate_document", "send_command", "get_document_info",
):
    setattr(Civil3DClient, _name, Civil3DClient.guard(getattr(Civil3DClient, _name)))
