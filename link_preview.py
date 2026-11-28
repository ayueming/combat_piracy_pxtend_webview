# link_preview.py
"""链接预览窗口（独立文件，供 page_links 双击「资源链接」调用）。

为什么不直接复用 picker_web_view.PickerWebView：
  - PickerWebView 会注入拾取脚本、抢 app_state 的 previewing 锁，还会向上层
    要“作品/分类”上下文；page_links 只是“打开链接看看”，这些都用不上，
    抢锁还会挡住正在进行的抓取/拾取任务；
  - page_spider_site.py 后期可能整体重构，page_links 不该被绑到那条链路上。

本文件只做“看”：
  - 不注入拾取脚本、不抢全局锁；
  - 复用 webview_profile_factory 的持久化 profile（UA / stealth / 证书策略一致）；
  - 页面弹窗一律同页打开，不新开窗口；
  - 不修改 page_spider_site.py / picker_web_view.py。

对外入口：
    from link_preview import LinkPreviewDialog
    LinkPreviewDialog(parent, url, book_name="").exec()
"""
import re
from urllib.parse import quote

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QToolButton,
    QProgressBar, QStyle,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

from webview_profile_factory import create_preview_profile
from url_mobile_utils import url_is_mobile


_BAIDU_SEARCH_URL = "https://www.baidu.com/s?ie=utf-8&wd={}"

# 页面内弹窗守卫：window.open / target=_blank 一律同页打开
_POPUP_GUARD_JS = r"""
(function() {
    if (window.__link_preview_guard__) return;
    window.__link_preview_guard__ = true;

    window.open = function(url) {
        if (!url) return null;
        // 只放行用户真实点击触发的弹窗，挡住广告/自动跳转
        if (!navigator.userActivation || !navigator.userActivation.isActive) {
            return null;
        }
        window.location.href = url;
        return null;
    };

    document.addEventListener('click', function(e) {
        var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
        if (!a) return;
        if ((a.getAttribute('target') || '').toLowerCase() !== '_blank') return;
        e.preventDefault();
        window.location.href = a.href;
    }, true);
})();
"""


class _PreviewPage(QWebEnginePage):
    """预览专用页面：不弹新窗、静默 JS 对话框、接受自签证书。

    注意：接受任意证书只为预览不可信源站，本窗口不得用于登录/支付。
    """

    def createWindow(self, _type):
        return None

    def javaScriptAlert(self, _origin, _msg):
        pass

    def javaScriptConfirm(self, _origin, _msg):
        return False

    def javaScriptPrompt(self, _origin, _msg, _default):
        return (False, "")

    def certificateError(self, _error):
        return True


class LinkPreviewDialog(QDialog):
    """只读预览窗口。"""

    def __init__(self, parent, url, book_name=""):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(1100, 820)

        self._url = (url or "").strip()
        self._is_mobile = 1 if url_is_mobile(self._url) else 0
        self._loaded = False
        self._base_title = "链接预览"
        if book_name:
            self._base_title += f" - {book_name}"
        self.setWindowTitle(self._base_title)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._progress = QProgressBar()
        self._progress.setFixedHeight(3)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setStyleSheet("""
            QProgressBar { background: transparent; border: none; }
            QProgressBar::chunk { background: #1f6feb; }
        """)
        root.addWidget(self._progress)

        # ★ 必须先建 web_view，导航栏的按钮才能连它的槽
        self.web_view = QWebEngineView(self)
        self.profile = create_preview_profile(self.web_view, self._is_mobile)
        self.page = _PreviewPage(self.profile, self.web_view)
        self.web_view.setPage(self.page)

        self._setup_nav_bar(root)
        root.addWidget(self.web_view, 1)
        self._install_popup_guard()

        self.web_view.loadStarted.connect(self._on_load_started)
        self.web_view.loadProgress.connect(self._progress.setValue)
        self.web_view.loadFinished.connect(self._on_load_finished)
        self.web_view.urlChanged.connect(self._on_url_changed)
        self.web_view.titleChanged.connect(self._on_title_changed)

        self.address_bar.setText(self._url)

    # ---------------- 顶部导航栏 ----------------
    def _tool_button(self, icon, tip, slot):
        btn = QToolButton()
        btn.setIcon(self.style().standardIcon(icon))
        btn.setToolTip(tip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 4px; }"
            "QToolButton:hover { background: #e1e4e8; border-radius: 4px; }"
            "QToolButton:disabled { opacity: 0.3; }"
        )
        btn.clicked.connect(slot)
        return btn

    def _setup_nav_bar(self, root):
        bar = QWidget()
        bar.setFixedHeight(40)
        bar.setStyleSheet("background-color: #f6f8fa; border-bottom: 1px solid #eaeef2;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(8)

        self.btn_back = self._tool_button(QStyle.SP_ArrowBack, "后退", self.web_view.back)
        self.btn_back.setEnabled(False)
        self.btn_refresh = self._tool_button(QStyle.SP_BrowserReload, "刷新", self.web_view.reload)

        self.btn_mobile = QToolButton()
        self.btn_mobile.setCursor(Qt.PointingHandCursor)
        self.btn_mobile.setToolTip("切换移动版/桌面版 UA 并刷新")
        self.btn_mobile.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 4px 8px; font-size: 12px; }"
            "QToolButton:hover { background: #e1e4e8; border-radius: 4px; }"
        )
        self.btn_mobile.clicked.connect(self._toggle_mobile)
        self._update_mobile_button()

        self.address_bar = QLineEdit()
        self.address_bar.setPlaceholderText("网址或搜索词...")
        self.address_bar.setClearButtonEnabled(True)
        self.address_bar.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d0d7de;
                border-radius: 14px;
                padding: 4px 10px;
                background: white;
                font-size: 13px;
                color: #24292f;
            }
            QLineEdit:focus {
                border: 2px solid #1f6feb;
                padding: 3px 9px;
            }
        """)
        self.address_bar.returnPressed.connect(self._on_address_entered)

        lay.addWidget(self.btn_back)
        lay.addWidget(self.btn_refresh)
        lay.addWidget(self.btn_mobile)
        lay.addWidget(self.address_bar, 1)
        root.addWidget(bar)

    # ---------------- 加载 ----------------
    def _install_popup_guard(self):
        script = QWebEngineScript()
        script.setName("link_preview_popup_guard")
        script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(True)
        script.setSourceCode(_POPUP_GUARD_JS)
        self.page.scripts().insert(script)

    def showEvent(self, event):
        super().showEvent(event)
        # 延迟到窗口显示后再加载，避免 QDialog 未就绪导致白屏
        if not self._loaded and self._url:
            self._loaded = True
            self.load_url(self._url)

    def load_url(self, url: str):
        url = (url or "").strip()
        if not url:
            return
        qurl = QUrl.fromUserInput(url)
        if not qurl.isValid() or qurl.isEmpty():
            print(f"[LinkPreview] 无效的 URL，拒绝加载: {url}")
            return
        print(f"[LinkPreview] 正在加载: {qurl.toString()}")
        self.web_view.load(qurl)

    # ---------------- 导航栏交互 ----------------
    _URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")

    def _looks_like_url(self, text: str) -> bool:
        if self._URL_RE.match(text):
            return True
        if re.search(r"\s", text) or re.search(r"[\u4e00-\u9fff]", text):
            return False
        host = re.split(r"[/?#]", text, maxsplit=1)[0]
        if host.startswith("localhost"):
            return True
        if ":" in host:
            host = host.rsplit(":", 1)[0]
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host):
            return True
        return bool("." in host and re.search(r"\.[a-zA-Z]{2,}$", host))

    def _on_address_entered(self):
        text = self.address_bar.text().strip()
        if not text:
            return
        if self._looks_like_url(text):
            self.load_url(text)
        else:
            self.load_url(_BAIDU_SEARCH_URL.format(quote(text, encoding="utf-8")))
        self.web_view.setFocus()

    def _toggle_mobile(self):
        from app_data.mobile_consts import MOBILE_UA, DESKTOP_UA

        self._is_mobile = 0 if self._is_mobile else 1
        self.profile.setHttpUserAgent(MOBILE_UA if self._is_mobile else DESKTOP_UA)
        self._update_mobile_button()
        self.web_view.reload()

    def _update_mobile_button(self):
        self.btn_mobile.setText("📱 移动版" if self._is_mobile else "🖥 桌面版")

    # ---------------- 状态同步 ----------------
    def _on_load_started(self):
        self._progress.setValue(0)

    def _on_load_finished(self, ok):
        self._progress.setValue(0)
        self.btn_back.setEnabled(self.web_view.history().canGoBack())

    def _on_url_changed(self, qurl: QUrl):
        if not self.address_bar.hasFocus():
            self.address_bar.setText(qurl.toString())

    def _on_title_changed(self, title: str):
        title = (title or "").strip()
        if title:
            self.setWindowTitle(f"{title} - {self._base_title}"[:120])
        else:
            self.setWindowTitle(self._base_title)

    # ---------------- 销毁 ----------------
    def closeEvent(self, event):
        try:
            self.web_view.stop()
            self.web_view.setUrl(QUrl("about:blank"))
        except Exception:
            pass
        super().closeEvent(event)
