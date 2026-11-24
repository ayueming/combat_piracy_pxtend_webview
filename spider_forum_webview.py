# spider_forum_webview.py
"""论坛/网盘 Tab 左列 WebView 容器。

封装 PickerWebView，管理"当前要预览的链接上下文"。
每次双击中列某行/回灌某条结果 → set_context() → load_url()。

batch_id 固定为 SPIDER_FORUM_PICK，与站点页的 SPIDER_PICK 互相隔离。
"""
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

from app_data import ALL_SUB_PLATFORMS
from picker_web_view import PickerWebView


FORUM_PICK_BATCH_ID = "SPIDER_FORUM_PICK"


class SpiderForumWebView(QWidget):
    picked = Signal(dict)
    saved_page = Signal(dict)
    batch_saved = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        # 当前上下文
        self._book_name = ""
        self._category = "其他"
        self._url = ""
        self._is_mobile = 0

        # PickerWebView 延迟到首次 load_url 才创建，避免抢锁失败直接废掉
        self._picker: PickerWebView | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._placeholder = QLabel("双击链接，预览网页，拾取链接")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet(
            "QLabel { color: #9CA3AF; font-size: 14px; background: #F8FAFC; "
            "border: 1px dashed #E5E7EB; border-radius: 6px; padding: 40px; }"
        )
        layout.addWidget(self._placeholder)

    # ---------- 内部：ctx ----------
    def _build_ctx(self):
        books = []
        try:
            from db_config import get_conn
            conn = get_conn()
            books = [r[0] for r in conn.execute(
                "SELECT name FROM books ORDER BY name").fetchall()]
            conn.close()
        except Exception as e:
            print(f"[ForumWebView] 加载 books 失败: {e}")

        return {
            "book_name": self._book_name,
            "category": self._category,
            "keyword": "",
            "batch_id": FORUM_PICK_BATCH_ID,
            "source_platform": self._category,
            "parent_url": self._url,
            "books": books,
            "categories": list(ALL_SUB_PLATFORMS),
            "is_mobile": self._is_mobile,
        }

    # ---------- 对外 ----------
    def set_context(self, book_name, category, url, is_mobile=0):
        self._book_name = book_name or ""
        self._category = category or "其他"
        self._url = url or ""
        self._is_mobile = int(bool(is_mobile))

    def load_url(self, url: str) -> bool:
        """返回 True 表示已发起加载；False 表示锁被占或 URL 无效。"""
        url = (url or "").strip()
        if not url:
            return False
        if not self._ensure_picker():
            return False

        # UA 同步（如果变了，下一次 load 生效）
        self._picker.set_mobile_mode(self._is_mobile)
        self._picker.load_url(url)
        return True

    def release_lock(self):
        if self._picker is not None:
            try:
                self._picker.destroy_picker()
            except Exception:
                pass
            self._picker.release_lock()

    # ---------- 内部：延迟创建 ----------
    def _ensure_picker(self) -> bool:
        if self._picker is not None:
            return True

        picker = PickerWebView(
            self,
            context_provider=self._build_ctx,
            lock_mode="previewing",
            allow_change_book=True,
            allow_change_category=True,
            initial_is_mobile=self._is_mobile,
        )
        if not picker.is_locked_by_us():
            try:
                picker.web_view.stop()
                picker.web_view.setUrl(QUrl("about:blank"))
            except Exception:
                pass
            picker.deleteLater()
            return False

        picker.picked.connect(self.picked.emit)
        picker.saved_page.connect(self.saved_page.emit)
        picker.batch_saved.connect(self.batch_saved.emit)

        # ================= 新增：网页缩放 80% =================
        ZOOM_FACTOR = 0.8

        def _apply_zoom(_ok=None):
            try:
                if getattr(picker, "web_view", None) is None:
                    return
                picker.web_view.setZoomFactor(ZOOM_FACTOR)
            except RuntimeError:
                pass
            except Exception as e:
                print(f"[SpiderForum] setZoomFactor 失败: {e}")

        picker.web_view.loadStarted.connect(_apply_zoom)
        picker.web_view.loadFinished.connect(_apply_zoom)
        # ======================================================

        # 替换占位提示
        self.layout().removeWidget(self._placeholder)
        self._placeholder.deleteLater()
        self.layout().addWidget(picker)

        self._picker = picker

    def closeEvent(self, event):
        self.release_lock()
        super().closeEvent(event)