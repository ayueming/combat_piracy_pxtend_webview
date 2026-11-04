# picker_web_view.py
"""可复用的 WebView + Picker 组件。

对外只暴露信号；锁、UA 切换、注入逻辑全部内部处理。
"""
from PySide6.QtCore import QUrl, QFile, QIODevice, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebChannel import QWebChannel

from app_state import app_state
from webview_profile_factory import create_preview_profile
from picker_injector import build_picker_js
from picker_js_bridge import PickerJsBridge


class PickerWebView(QWidget):
    picked      = Signal(dict)
    saved_page  = Signal(dict)    # ★ 与 bridge 保持一致
    batch_saved = Signal(str)
    ua_switched = Signal(int)

    def __init__(self, parent,
                 context_provider,
                 lock_mode="previewing",
                 allow_change_book=True,
                 allow_change_category=True,
                 initial_is_mobile=0):
        super().__init__(parent)
        self._context_provider = context_provider
        self._lock_mode = lock_mode
        self._allow_change_book = allow_change_book
        self._allow_change_category = allow_change_category
        self._is_mobile = int(bool(initial_is_mobile))
        self._locked = False

        # ★ 内部抢锁
        if lock_mode and app_state.check_and_lock(lock_mode):
            self._locked = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.web_view = QWebEngineView()
        self._setup_profile()
        layout.addWidget(self.web_view)

        self.channel = QWebChannel()
        self.bridge = PickerJsBridge(self._context_provider)
        self.channel.registerObject("pyBridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        self.bridge.picked.connect(self.picked.emit)
        self.bridge.saved_page.connect(self.saved_page.emit)
        self.bridge.batch_saved.connect(self.batch_saved.emit)
        self.bridge.ua_switched.connect(self._on_ua_switched)

        self.web_view.loadFinished.connect(self._on_load_finished)

        self.web_view.renderProcessTerminated.connect(
            lambda status, code: print(f"❌ [Picker] 渲染进程死了: status={status}, code={code}")
        )
        self.web_view.loadStarted.connect(
            lambda: print(f"▶️ [Picker] 开始加载网页...")
        )

    # ---------- 对外：锁状态 ----------
    def is_locked_by_us(self) -> bool:
        if not self._lock_mode:
            return True
        return self._locked

    def release_lock(self):
        if self._locked:
            app_state.set_mode("idle")
            self._locked = False

    # ---------- 对外：操作 ----------
    def load_url(self, url: str):
        url = (url or "").strip()
        if not url:
            return

        # ★ 核心：使用 QUrl.fromUserInput 让 Qt 自动智能推断协议
        # 它会自动把 "www.baidu.com" 变成 "http://www.baidu.com"
        # 会自动处理 "//www.baidu.com"、"localhost:8080" 甚至 "C:\test.html"
        qurl = QUrl.fromUserInput(url)

        # 安全检查：如果解析出来还是个无效的（比如输入了一堆乱码中文），直接拦截
        if not qurl.isValid() or qurl.isEmpty():
            print(f"❌ [Picker] 无效的 URL，拒绝加载: {url}")
            return

        # 如果你只想允许 http/https 协议（防止用户输入本地 exe 或 file 路径），可以加这层判断
        # if qurl.scheme() not in ("http", "https"):
        #     print(f"❌ [Picker] 非法的协议类型，拒绝加载: {qurl.scheme()}")
        #     return

        print(f"🌐 [Picker] 正在加载: {qurl.toString()}")
        self.web_view.load(qurl)

    def set_mobile_mode(self, is_mobile: int):
        target = int(bool(is_mobile))
        if target == self._is_mobile:
            return
        self._is_mobile = target
        page = self.web_view.page()
        if page and page.profile():
            from app_data.mobile_consts import MOBILE_UA, DESKTOP_UA
            page.profile().setHttpUserAgent(MOBILE_UA if target else DESKTOP_UA)

    # ---------- 内部 ----------
    def _setup_profile(self):
        self.profile = create_preview_profile(self.web_view, self._is_mobile)
        # ★ 必须把 page 挂到 self 上保活
        self.page = QWebEnginePage(self.profile, self.web_view)
        self.web_view.setPage(self.page)

    def _on_ua_switched(self, mode):
        self.set_mobile_mode(mode)
        self.ua_switched.emit(mode)
        self.web_view.reload()

    def _on_load_finished(self, ok):
        if not ok:
            return
        f = QFile(":/qtwebchannel/qwebchannel.js")
        if not f.open(QIODevice.ReadOnly):
            print("❌ [Picker] 无法加载 qwebchannel.js")
            return
        js_channel = bytes(f.readAll()).decode("utf-8")
        f.close()
        self.web_view.page().runJavaScript(js_channel)

        ctx = dict(self._context_provider() or {})
        ctx["allow_change_book"] = self._allow_change_book
        ctx["allow_change_category"] = self._allow_change_category
        ctx["is_mobile"] = self._is_mobile

        self.web_view.page().runJavaScript(build_picker_js(ctx))

    def closeEvent(self, e):
        self.release_lock()
        self.destroy_picker()
        super().closeEvent(e)

    def destroy_picker(self):
        """安全停止 WebView 加载，防止后台网络线程崩溃"""
        try:
            if hasattr(self, "channel") and self.channel and hasattr(self, "bridge"):
                self.channel.deregisterObject(self.bridge)

            if hasattr(self, "web_view") and self.web_view:
                self.web_view.stop()
                self.web_view.setUrl(QUrl("about:blank"))
                # ★ 绝对不要在这里写 self.web_view.deleteLater()
                # 交由 Qt 的 C++ 父子树自动安全回收
        except Exception as e:
            print(f"[Picker] 销毁 WebView 失败: {e}")