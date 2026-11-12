# picker_web_view.py
"""可复用的 WebView + Picker 组件。

对外只暴露信号；锁、UA 切换、注入逻辑全部内部处理。
"""
from PySide6.QtCore import QUrl, QFile, QIODevice, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QProgressBar
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebChannel import QWebChannel

from app_state import app_state
from webview_profile_factory import create_preview_profile
from picker_injector import build_picker_js
from picker_js_bridge import PickerJsBridge


class SafeWebPage(QWebEnginePage):
    def createWindow(self, _type):
        print("🛡️ [WebEngine] 成功拦截一个网页弹窗")
        return None

    def javaScriptAlert(self, securityOrigin, msg):
        pass

    def javaScriptConfirm(self, securityOrigin, msg):
        return False

    def javaScriptPrompt(self, securityOrigin, msg, defaultValue):
        return (False, "")

    def certificateError(self, error):
        return True     # ★ 接受所有证书，保证盗文站可预览（业务必需）

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
        # ★ 清理阶段（about:blank）屏蔽进度条，防止闪一下
        self._suppress_loading = False

        # ★ 内部抢锁
        if lock_mode and app_state.check_and_lock(lock_mode):
            self._locked = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ★ 顶部 3px 进度条：始终占位，值 0 时背景透明视觉不可见
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(3)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setStyleSheet("""
            QProgressBar { background: transparent; border: none; }
            QProgressBar::chunk { background: #1f6feb; }
        """)
        layout.addWidget(self._progress_bar)

        self.web_view = QWebEngineView()
        # ★ 绑定我们写的安全 Page 类
        self._setup_profile()
        layout.addWidget(self.web_view)

        self.channel = QWebChannel(self)  # ★ 加 self
        self.bridge = PickerJsBridge(self._context_provider, self)  # ★ 加 self
        self.channel.registerObject("pyBridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        self.bridge.picked.connect(self.picked.emit)
        self.bridge.saved_page.connect(self.saved_page.emit)
        self.bridge.batch_saved.connect(self.batch_saved.emit)
        self.bridge.ua_switched.connect(self._on_ua_switched)

        # ★ 加载进度信号
        self.web_view.loadStarted.connect(self._on_load_started)
        self.web_view.loadProgress.connect(self._on_load_progress)
        self.web_view.loadFinished.connect(self._on_load_finished)

        self.web_view.renderProcessTerminated.connect(
            lambda status, code: (
                self._progress_bar.setValue(0),
                print(f"❌ [Picker] 渲染进程死了: status={status}, code={code}")
            )
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

        # ★ 使用 QUrl.fromUserInput 让 Qt 自动智能推断协议
        qurl = QUrl.fromUserInput(url)

        if not qurl.isValid() or qurl.isEmpty():
            print(f"❌ [Picker] 无效的 URL，拒绝加载: {url}")
            return

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

    # ---------- 内部：加载进度 ----------
    def _on_load_started(self):
        if self._suppress_loading:
            return
        self._progress_bar.setValue(0)

    def _on_load_progress(self, p: int):
        if self._suppress_loading:
            return
        self._progress_bar.setValue(p)

    # ---------- 内部 ----------
    def _setup_profile(self):
        self.profile = create_preview_profile(self.web_view, self._is_mobile)
        # ★ 直接把原来的 QWebEnginePage 替换为 SafeWebPage，并传入 profile
        self.page = SafeWebPage(self.profile, self.web_view)
        self.web_view.setPage(self.page)

    def _on_ua_switched(self, mode):
        self.set_mobile_mode(mode)
        self.ua_switched.emit(mode)
        self.web_view.reload()

    def _on_load_finished(self, ok):
        # ★ 结束归零（进度条始终占位，视觉上即隐藏）
        self._progress_bar.setValue(0)
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
        # ★ 屏蔽清理阶段 about:blank 触发的进度条闪动
        self._suppress_loading = True
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