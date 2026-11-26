# picker_web_view.py
"""
此 WebView 不校验 TLS 证书，仅用于预览不可信源站，不得用于登录/支付场景

可复用的 WebView + Picker 组件。

对外只暴露信号；锁、UA 切换、注入逻辑全部内部处理。
"""
import re
from urllib.parse import quote
from PySide6.QtCore import QUrl, QFile, QIODevice, Signal, Qt, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QProgressBar, QHBoxLayout,
    QLineEdit, QToolButton, QStyle
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

from app_state import app_state
from webview_profile_factory import create_preview_profile
from picker_injector import build_picker_js
from picker_js_bridge import PickerJsBridge


# 页面创建阶段注入，用来接管 window.open / target=_blank
POPUP_GUARD_JS = r"""
(function() {
    if (window.__apt_popup_guard_installed__) return;
    window.__apt_popup_guard_installed__ = true;

    var USER_NAV_DEBOUNCE_MS = 400;   // 用户主动点击：仅防连点
    var lastNavAt = 0;

    function userGestureActive() {
        return !!(navigator.userActivation && navigator.userActivation.isActive);
    }

    function allowUserNav(url) {
        var now = Date.now();
        if (now - lastNavAt < USER_NAV_DEBOUNCE_MS) {
            console.warn('[Picker] 点击过快，防抖:', url);
            return false;
        }
        lastNavAt = now;
        return true;
    }

    // 1) window.open：无手势一律拒；有手势同页跳
    window.open = function(url, name, features) {
        if (!url) return null;
        if (!userGestureActive()) {
            console.warn('[Picker] 拦截无手势 window.open:', url);
            return null;
        }
        if (!allowUserNav(url)) return null;
        window.location.href = url;
        return null;   // ★ 永远不返回 window
    };

    // 2) target=_blank 点击：同页跳 + 防抖
    document.addEventListener('click', function(e) {
        if (!e.isTrusted) return;
        var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
        if (!a) return;
        if ((a.getAttribute('target') || '').toLowerCase() !== '_blank') return;
        var href = a.href;
        if (!href) return;
        e.preventDefault();
        if (allowUserNav(href)) window.location.href = href;
    }, true);
})();
"""


class SafeWebPage(QWebEnginePage):
    def createWindow(self, _type):
        print("🛡️ [WebEngine] 兜底拦截一个新的弹窗请求（已被页面层守卫覆盖）")
        return None

    def javaScriptAlert(self, securityOrigin, msg):
        pass

    def javaScriptConfirm(self, securityOrigin, msg):
        return False

    def javaScriptPrompt(self, securityOrigin, msg, defaultValue):
        return (False, "")

    def certificateError(self, error):
        return True  # ★ 接受所有证书，保证盗文站可预览（业务必需）


class PickerWebView(QWidget):
    picked = Signal(dict)
    saved_page = Signal(dict)  # ★ 与 bridge 保持一致
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

        # ★★★ 修复点：必须先实例化 web_view，导航栏里才能 connect 它的槽函数 ★★★
        self.web_view = QWebEngineView()
        self._setup_profile()

        # ★ 新增：地址栏导航条（必须放在 web_view 创建之后）
        self._setup_nav_bar(layout)

        layout.addWidget(self.web_view)

        self.channel = QWebChannel(self)
        self.bridge = PickerJsBridge(self._context_provider, self)
        self.channel.registerObject("pyBridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)
        self._install_popup_guard()

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

        # ★ 地址栏与 WebView 状态同步
        self.web_view.urlChanged.connect(self._on_url_changed)
        self.web_view.loadFinished.connect(self._update_back_button_state)

    # ---------- 新增：构建顶部导航栏 ----------
    def _setup_nav_bar(self, parent_layout):
        nav_widget = QWidget()
        nav_widget.setFixedHeight(40)
        nav_widget.setStyleSheet("background-color: #f6f8fa; border-bottom: 1px solid #eaeef2;")
        nav_layout = QHBoxLayout(nav_widget)
        nav_layout.setContentsMargins(10, 5, 10, 5)
        nav_layout.setSpacing(8)

        # 后退按钮
        self.btn_back = QToolButton()
        self.btn_back.setIcon(self.style().standardIcon(QStyle.SP_ArrowBack))
        self.btn_back.setToolTip("后退")
        self.btn_back.setEnabled(False)
        self.btn_back.setCursor(Qt.PointingHandCursor)
        self.btn_back.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 4px; } QToolButton:hover { background: #e1e4e8; border-radius: 4px; } QToolButton:disabled { opacity: 0.3; }")
        self.btn_back.clicked.connect(self.web_view.back)

        # 刷新按钮
        self.btn_refresh = QToolButton()
        self.btn_refresh.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.btn_refresh.setToolTip("刷新")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 4px; } QToolButton:hover { background: #e1e4e8; border-radius: 4px; }")
        self.btn_refresh.clicked.connect(self.web_view.reload)

        # 地址栏 / 搜索框
        self.address_bar = QLineEdit()
        self.address_bar.setPlaceholderText("搜索或输入网址...")
        self.address_bar.setClearButtonEnabled(True)
        # 添加搜索图标
        search_action = self.address_bar.addAction(
            self.style().standardIcon(QStyle.SP_FileDialogContentsView),
            QLineEdit.LeadingPosition
        )

        # 模仿图片中的圆角蓝框样式
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
                border: 2px solid #1f6feb; /* 聚焦时蓝色边框 */
                padding: 3px 9px;          /* 调整内边距防止文字跳动 */
            }
        """)
        self.address_bar.returnPressed.connect(self._on_address_entered)

        nav_layout.addWidget(self.btn_back)
        nav_layout.addWidget(self.btn_refresh)
        nav_layout.addWidget(self.address_bar)

        parent_layout.addWidget(nav_widget)

    _BAIDU_SEARCH_URL = "https://www.baidu.com/s?ie=utf-8&wd={}"

    def _looks_like_url(self, text: str) -> bool:
        """判断用户输入是否更像一个网址，而不是搜索词。"""
        text = text.strip()
        if not text:
            return False
        # 有显式协议，直接当 URL
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.\-]*://', text):
            return True
        # 含空白/中文，肯定是搜索词
        if " " in text or "\t" in text:
            return False
        if re.search(r'[\u4e00-\u9fff]', text):
            return False

        # 取出 host 部分（去掉路径 / 查询 / 端口）
        host = re.split(r'[/?#]', text, maxsplit=1)[0]
        if host.startswith("localhost"):
            return True
        if ":" in host:
            host = host.rsplit(":", 1)[0]

        # IP 直连
        if re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', host):
            return True

        # 域名：含点 且 TLD 是 ≥2 位字母
        if "." in host and re.search(r'\.[a-zA-Z]{2,}$', host):
            return True

        return False

    def _search_on_baidu(self, keyword: str):
        keyword = keyword.strip()
        if not keyword:
            return
        url = self._BAIDU_SEARCH_URL.format(quote(keyword, encoding="utf-8"))
        print(f"🔍 [Picker] 百度搜索: {keyword} -> {url}")
        self.load_url(url)

    def _on_address_entered(self):
        """地址栏按下回车：像网址就打开，否则用百度搜索。"""
        text = self.address_bar.text().strip()
        if not text:
            return
        if self._looks_like_url(text):
            self.load_url(text)
        else:
            self._search_on_baidu(text)
        self.web_view.setFocus()

    def _on_url_changed(self, qurl: QUrl):
        """网址变化时更新地址栏"""
        if not self.address_bar.hasFocus():
            self.address_bar.setText(qurl.toString())

    def _update_back_button_state(self, ok):
        """更新后退按钮的可用状态"""
        if hasattr(self, "web_view") and hasattr(self, "btn_back"):
            self.btn_back.setEnabled(self.web_view.history().canGoBack())

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

    def trigger_picking(self, enter: bool = True):
        """供 Qt 宿主窗口在 WebView 失焦时转发 Alt，进入/退出拾取。"""
        if not getattr(self, "web_view", None):
            return
        page = self.web_view.page()
        if not page:
            return
        fn = "__apt_enter_picking__" if enter else "__apt_exit_picking__"
        page.runJavaScript(f"window.{fn} && window.{fn}();")

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
    def _install_popup_guard(self):
        """在每个新文档创建时注入弹窗守卫（window.open / target=_blank）。"""
        script = QWebEngineScript()
        script.setName("picker_popup_guard")
        script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(True)
        script.setSourceCode(POPUP_GUARD_JS)
        self.page.scripts().insert(script)

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

        # 同步一下后退按钮状态
        self._update_back_button_state(ok)

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