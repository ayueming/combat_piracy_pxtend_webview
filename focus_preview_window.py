# focus_preview_window.py
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
                               QApplication, QMessageBox)
from PySide6.QtCore import Qt, QUrl

from ws_server import current_context
from app_data import ALL_SUB_PLATFORMS
from mobile_url_utils import resolve_is_mobile
from picker_web_view import PickerWebView


class BaseFocusWindow(QWidget):
    def __init__(self, main_page, workbench_widget, preview_url="",
                 title="专注预览 (按 Esc 退出)", is_mobile=0):
        super().__init__()
        self.main_page = main_page
        self.workbench_widget = workbench_widget
        self.preview_url = preview_url
        self.is_mobile = resolve_is_mobile(is_mobile, preview_url)
        self._closed_handled = False
        self._lock_failed = False

        self.setWindowTitle(title)
        self.setStyleSheet("QWidget { background-color: #F8FAFC; }")
        self.setFocusPolicy(Qt.StrongFocus)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ===== 左侧：PickerWebView =====
        self.picker = PickerWebView(
            self,
            context_provider=self._build_ctx,
            lock_mode="previewing",
            allow_change_book=True,
            allow_change_category=True,
            initial_is_mobile=self.is_mobile,
        )
        # ★ 兼容 page_search.py 里对 focus_window.web_view 的直接引用
        self.web_view = self.picker.web_view

        if not self.picker.is_locked_by_us():
            self._lock_failed = True
            return

        # 转发通知
        from ws_server import ws_signals
        self.picker.picked.connect(lambda rec: ws_signals.new_data_inserted.emit(1))
        self.picker.batch_saved.connect(lambda bid: ws_signals.new_data_inserted.emit(1))

        main_layout.addWidget(self.picker, 65)

        # ===== 右侧：workbench =====
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(8)

        btn_close = QPushButton("🛑 退出分屏 (Esc)")
        btn_close.setStyleSheet(
            "QPushButton { border: 1px solid #FECACA; border-radius: 4px; "
            "background: #FEF2F2; color: #DC2626; font-size: 12px; font-weight: bold; padding: 6px; } "
            "QPushButton:hover { background: #FEE2E2; }")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.close)
        right_layout.addWidget(btn_close)

        if self.workbench_widget is not None:
            right_layout.addWidget(self.workbench_widget, 1)

        main_layout.addWidget(right_panel, 35)

    def _build_ctx(self):
        books = []
        if hasattr(self.main_page, "input_panel") and hasattr(self.main_page.input_panel, "books_data"):
            books = [b["name"] for b in self.main_page.input_panel.books_data.values()]
        return {
            "book_name": current_context.get("book_name", ""),
            "category": current_context.get("source_platform", "") or "其他",
            "keyword": current_context.get("keyword", ""),
            "batch_id": current_context.get("batch_id", ""),
            "source_platform": current_context.get("source_platform", ""),
            "books": books,
            "categories": list(ALL_SUB_PLATFORMS),
        }

    def set_mobile_mode(self, is_mobile):
        """供 page_search.py 复用已有窗口时切换 UA。"""
        target = int(bool(is_mobile))
        if target == self.is_mobile:
            return
        self.is_mobile = target
        self.picker.set_mobile_mode(target)

    @classmethod
    def launch(cls, main_page, workbench_widget, preview_url, title="专注预览", is_mobile=0):
        if not preview_url:
            return None

        if getattr(main_page, "focus_window", None):
            main_page.focus_window.close()

        win = cls(main_page, workbench_widget, preview_url, title, is_mobile=is_mobile)

        if win._lock_failed:
            try:
                win.picker.web_view.stop()
                win.picker.web_view.setUrl(QUrl("about:blank"))
            except Exception:
                pass
            win.deleteLater()
            QMessageBox.warning(main_page, "提示", "后台正在执行自动任务，请等待完成后再预览。")
            return None

        main_page.focus_window = win

        screen_rect = QApplication.primaryScreen().availableGeometry()
        win.setGeometry(screen_rect)
        win.show()
        win.setFocus()
        win.picker.load_url(preview_url)
        return win

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if self._closed_handled:
            event.accept()
            return
        self._closed_handled = True

        if getattr(self, "picker", None):
            self.picker.release_lock()   # 仅释放锁，不要在这里销毁 web_view
            self.picker = None

        if self.workbench_widget is not None:
            self.workbench_widget.setParent(None)

        try:
            self.main_page.window().show()
        except Exception:
            pass

        if hasattr(self.main_page, "on_focus_window_closed"):
            try:
                self.main_page.on_focus_window_closed()
            except Exception as e:
                print(f"[FocusWindow] on_focus_window_closed 出错: {e}")

        event.accept()