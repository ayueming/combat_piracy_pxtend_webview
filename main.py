# ui调整

import os
import sys
import asyncio
import faulthandler

# =========================================================
# 🌟 核心修复：彻底禁用硬件加速和 WebGL，防止渲染进程崩溃
# =========================================================
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox"
# =========================================================

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QDialog
from PySide6.QtCore import Qt, QEvent, QTimer, Signal, QEasingCurve

# ===== 引入 Fluent 组件 =====
from qfluentwidgets import FluentWindow, NavigationItemPosition, FluentIcon as FIF, setTheme, Theme

# 导入后台通信服务
from ws_server import start_ws_server, ws_signals

# 导入你的页面模块
from page_base_info import BaseInfoPage
from page_links import LinksPage
from page_search import SearchConsolePage
from page_spider import SpiderPage
from page_complaints import ComplaintsPage
from page_evidence import EvidencePage
from page_platforms import PlatformsPage
from page_profile import ProfilePage

# 导入登录模块
from login_window import LoginWindow
from login_auto import try_auto_login, clear_session

from app_state import app_state
from spider_forum_webview import SpiderForumWebView
from picker_web_view import PickerWebView


VERSION = "V3.8.4"


# ==========================================
# 🌟 仅保留版本号的辅助类（框架强制要求有这个类才能加自定义控件）
# ==========================================

# ==========================================
# 🌟 修改 1：将 faulthandler 日志存入 AppData
# ==========================================
# 获取用户 AppData/Roaming 目录 (如果找不到，就退回用户的家目录)
_app_data_dir = os.path.join(os.environ.get('APPDATA', os.path.expanduser("~")), "打赏维权系统")
# 确保该文件夹存在，如果不存在就自动创建
os.makedirs(_app_data_dir, exist_ok=True)

# 拼接日志文件的完整路径
_FAULT_LOG_PATH = os.path.join(_app_data_dir, "faulthandler.log")
_FAULT_LOG_FILE = open(_FAULT_LOG_PATH, "w", encoding="utf-8")   # 必须保持引用，否则被 GC 关掉
faulthandler.enable(file=_FAULT_LOG_FILE, all_threads=True)


class NavLabel(QLabel):
    clicked = Signal()

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setStyleSheet("color: #9CA3AF; font-size: 11px; padding: 10px;")

    def setSelected(self, isSelected):
        pass


class MainApp(FluentWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(f"打盗维权系统_试用版_v{VERSION}")

        # 全局美化：覆盖原生的丑陋滚动条，并将主窗口整体背景色设为侧边栏颜色
        self.setStyleSheet("""
                    /* 主窗口背景色，让标题栏和四周边缘与侧边栏融为一体 */
                    FluentWindow {
                        background-color: #F0F4F1;
                    }

                    /* 垂直滚动条 */
                    QScrollBar:vertical {
                        background: transparent;
                        width: 5px;       /* 滚动条宽度 */
                        margin: 0px;
                    }
                    QScrollBar::handle:vertical {
                        background: rgba(0, 0, 0, 0.15); /* 默认半透明灰色 */
                        min-height: 20px;
                        border-radius: 5px; /* 圆角 */
                    }
                    QScrollBar::handle:vertical:hover {
                        background: rgba(0, 0, 0, 0.3);  /* 鼠标悬停变深 */
                    }
                    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                        height: 0px;       /* 隐藏上下箭头 */
                    }
                    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                        background: transparent; /* 轨道透明 */
                    }

                    /* 水平滚动条 */
                    QScrollBar:horizontal {
                        background: transparent;
                        height: 5px;
                        margin: 0px;
                    }
                    QScrollBar::handle:horizontal {
                        background: rgba(0, 0, 0, 0.15);
                        min-width: 20px;
                        border-radius: 5px;
                    }
                    QScrollBar::handle:horizontal:hover {
                        background: rgba(0, 0, 0, 0.3);
                    }
                    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                        width: 0px;        /* 隐藏左右箭头 */
                    }
                    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                        background: transparent;
                    }
                """)

        # 强制将标题栏背景色设为侧边栏同款色，解决箭头所指处的颜色差异
        if hasattr(self, 'titleBar'):
            self.titleBar.setStyleSheet("background-color: #F0F4F1;")

        # ===== 窗口尺寸和居中逻辑保留 =====
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            target_w = min(1400, available.width() - 100)
            target_h = min(900, available.height() - 100)
            self.resize(target_w, target_h)
            frame = self.frameGeometry()
            frame.moveCenter(available.center())
            self.move(frame.topLeft())
        else:
            self.resize(1400, 900)

        # ===== 1. 实例化所有页面 =====
        self.base_info_page = BaseInfoPage(self)
        self.base_info_page.setObjectName("baseInfoPage")

        self.links_page = LinksPage(self)
        self.links_page.setObjectName("linksPage")

        self.search_page = SearchConsolePage(self)
        self.search_page.setObjectName("searchPage")

        self.spider_page = SpiderPage(self)
        self.spider_page.setObjectName("spiderPage")

        self.complaint_page = ComplaintsPage(self)
        self.complaint_page.setObjectName("complaintPage")

        self.platforms_page = PlatformsPage()
        self.platforms_page.setObjectName("platformsPage")

        self.evidence_page = EvidencePage()
        self.evidence_page.setObjectName("evidencePage")

        self.profile_page = ProfilePage(self)
        self.profile_page.setObjectName("profilePage")

        # 强制把内部页面背景改成纯白色
        self.stackedWidget.setStyleSheet("""
                    QStackedWidget {
                        background-color: white;
                        border-radius: 12px;
                        margin: 10px;
                    }
                """)

        pages = [
            self.base_info_page,
            self.links_page,
            self.search_page,
            self.spider_page,
            self.complaint_page,
            self.platforms_page,
            self.evidence_page,
            self.profile_page
        ]
        for page in pages:
            page.setStyleSheet("background-color: white;")

        self.navigationInterface.panel.setStyleSheet("background-color: #F0F4F1; border: none;")

        # ===== 2. 初始化导航栏 =====
        self.init_navigation()

        # 🌟 悬停收起的延迟定时器 (从 150ms 延长到 250ms，给用户反应时间)
        self.collapse_timer = QTimer(self)
        self.collapse_timer.setSingleShot(True)
        self.collapse_timer.setInterval(250)
        self.collapse_timer.timeout.connect(self.navigationInterface.panel.collapse)

        # 🌟 悬停展开的防抖定时器 (延迟 50ms，防止鼠标快速划过边缘时误触展开)
        self.expand_timer = QTimer(self)
        self.expand_timer.setSingleShot(True)
        self.expand_timer.setInterval(50)
        self.expand_timer.timeout.connect(self.navigationInterface.panel.expand)

        self.navigationInterface.installEventFilter(self)

        # ★ 全局键盘捕获：不依赖 WebView 焦点，Alt 也能触发拾取
        QApplication.instance().installEventFilter(self)

        # ===== 3. 监听后台信号 =====
        ws_signals.new_data_inserted.connect(self.on_new_data)
        ws_signals.batch_finished.connect(self._on_batch_finished)

        # ===== 4. 监听页面切换，触发数据刷新 =====
        self.stackedWidget.currentChanged.connect(self.change_page)

    def init_navigation(self):
        """将页面添加到左侧导航栏，并配上漂亮的图标"""
        self.navigationInterface.setExpandWidth(160)
        self.navigationInterface.setMenuButtonVisible(False)
        self.navigationInterface.panel.collapse()

        # ==========================================
        # 🌟 最简单的下移方法：直接修改布局的上边距
        # (4, 16, 4, 4) 分别代表 左、上、右、下 的边距。把 16 改大或改小即可微调！
        # ==========================================
        try:
            self.navigationInterface.panel.vBoxLayout.setContentsMargins(4, 16, 4, 4)
        except AttributeError:
            pass

        # ==========================================
        # 🌟 优化侧边栏动画：拉长动画时间，设置丝滑的缓动曲线
        # ==========================================
        try:
            ani = self.navigationInterface.panel.expandAni
            ani.setDuration(300)  # 延长动画时长到 300ms
            ani.setEasingCurve(QEasingCurve.OutCubic)  # 设置“先快后慢”的丝滑曲线
        except AttributeError:
            pass

        # 添加页面到导航栏
        self.addSubInterface(self.base_info_page, FIF.LIBRARY, "作品管理")
        self.addSubInterface(self.links_page, FIF.PENCIL_INK, "链接管理")
        self.addSubInterface(self.search_page, FIF.GLOBE, "检索管理")
        self.addSubInterface(self.spider_page, FIF.LINK, "抓取管理")
        self.addSubInterface(self.complaint_page, FIF.SEND, "投诉管理")
        self.addSubInterface(self.platforms_page, FIF.TAG, "投诉平台")
        self.addSubInterface(self.evidence_page, FIF.FOLDER, "维权资料")

        self.addSubInterface(self.profile_page, FIF.SETTING, "个人与帮助", NavigationItemPosition.BOTTOM)

        # 默认选中“作品管理”
        self.navigationInterface.setCurrentItem("booksPage")

        # ==========================================
        # 🌟 使用自定义的 NavLabel 做版本号
        # ==========================================
        version_label = NavLabel(f"v.{VERSION} ©HowLittle")
        self.navigationInterface.addWidget(
            routeKey="version_label",
            widget=version_label,
            onClick=lambda: None,
            position=NavigationItemPosition.BOTTOM
        )

    def eventFilter(self, obj, event):
        # ===== 原有：导航栏悬停展开/收起 =====
        if hasattr(self, 'navigationInterface') and obj == self.navigationInterface:
            if event.type() == QEvent.Enter:
                self.collapse_timer.stop()
                self.expand_timer.start()
            elif event.type() == QEvent.Leave:
                self.expand_timer.stop()
                self.collapse_timer.start()

        et = event.type()

        # ===== Alt 转发（覆盖 MainApp 与 FocusWindow） =====
        if et == QEvent.KeyPress:
            if event.key() == Qt.Key_Alt and not event.isAutoRepeat():
                self._forward_alt_to_picker(True)
        elif et == QEvent.KeyRelease:
            if event.key() == Qt.Key_Alt and not event.isAutoRepeat():
                self._forward_alt_to_picker(False)

        # ===== 窗口失活兜底：Alt+Tab 切走 / 点别的程序 → 退出拾取 =====
        if et == QEvent.WindowDeactivate and isinstance(obj, QWidget):
            try:
                for w in obj.findChildren(PickerWebView):
                    if w.isVisible():
                        w.trigger_picking(False)
            except (AttributeError, RuntimeError):
                pass

        return super().eventFilter(obj, event)

    def _forward_alt_to_picker(self, enter: bool):
        """把 Alt 状态转发给『当前活动窗口』里的 PickerWebView。
        activeWindow 在 MainApp 里就找 MainApp 下的 picker，
        在 FocusWindow 里就找 FocusWindow 下的 picker，互不干扰。"""
        active = QApplication.activeWindow()
        if not active:
            return
        try:
            for w in active.findChildren(PickerWebView):
                if w.isVisible():
                    try:
                        w.trigger_picking(enter)
                    except Exception as e:
                        print(f"[Alt 转发] {e}")
        except (AttributeError, RuntimeError):
            pass

    def change_page(self, index):
        widget = self.stackedWidget.widget(index)
        if not widget:
            return
        name = widget.objectName()

        # ★ 新增：离开抓取管理页 → 关闭 spider forum 的 WebView，释放锁
        if name != "spiderPage":
            try:
                self.spider_page.release_preview_lock()
            except Exception as e:
                print(f"[change_page] 释放 spider 预览锁失败: {e}")

        if name == "linksPage":
            self.links_page.refresh_table()
        elif name == "baseInfoPage":
            self.base_info_page.refresh_table()
        elif name == "searchPage":
            self.search_page.load_books()
            if hasattr(self.search_page, 'result_panel'):
                self.search_page.result_panel.refresh_tables()
        elif name == "spiderPage":
            self.spider_page.refresh_data()
        elif name == "evidencePage":
            self.evidence_page.refresh_books()
            self.evidence_page.load_evidences()
        elif name == "profilePage":
            self.profile_page.refresh_profile()

    def on_new_data(self, count):
        """后台洗完数据后，自动刷新你的检索页"""
        print(f"UI收到通知：后台新增 {count} 条数据，正在刷新界面...")

    def _on_batch_finished(self, batch_id):
        """一次搜索 batch 彻底结束（正常/停止/失败/断联均算），任务栏图标闪烁提醒。"""
        print(f"[UI] batch 结束，触发图标闪烁: {batch_id}")
        QApplication.alert(self, 0)  # 0 = 一直闪到窗口被激活
        QTimer.singleShot(10000, lambda: QApplication.alert(self, 1))  # 10s 兜底停

    def closeEvent(self, event):
        print(f"[退出追踪] MainApp.closeEvent 触发, spontaneous={event.spontaneous()}")

        # 逐个释放，单个失败不影响其他
        try:
            for w in self.findChildren(SpiderForumWebView):
                try:
                    w.release_lock()
                except Exception as e:
                    print(f"[退出] SpiderForumWebView.release_lock 失败: {e}")
        except Exception as e:
            print(f"[退出] 遍历 SpiderForumWebView 失败: {e}")

        try:
            for w in self.findChildren(PickerWebView):
                try:
                    w.release_lock()
                except Exception as e:
                    print(f"[退出] PickerWebView.release_lock 失败: {e}")
        except Exception as e:
            print(f"[退出] 遍历 PickerWebView 失败: {e}")

        # 兜底：无论如何把全局状态归零
        try:
            app_state.set_mode("idle")
        except Exception as e:
            print(f"[退出] set_mode(idle) 失败: {e}")

        super().closeEvent(event)



if __name__ == "__main__":
    import traceback
    import datetime


    def exception_hook(exctype, value, tb):
        error_msg = "".join(traceback.format_exception(exctype, value, tb))
        # 使用上面定义好的 _app_data_dir 来保存崩溃日志
        crash_log_path = os.path.join(_app_data_dir, "crash_log.txt")
        with open(crash_log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.datetime.now()}] 崩溃信息:\n{error_msg}\n")
        sys.__excepthook__(exctype, value, tb)


    sys.excepthook = exception_hook

    app = QApplication(sys.argv)

    app.lastWindowClosed.connect(lambda: print("[退出追踪] lastWindowClosed 触发（最后一个窗口已关闭）"))
    app.aboutToQuit.connect(lambda: print("[退出追踪] aboutToQuit 触发（Qt 事件循环即将结束）"))

    start_ws_server()

    app.aboutToQuit.connect(lambda: app_state.set_mode("idle"))

    auto_ok = try_auto_login()

    while True:
        if not auto_ok:
            login = LoginWindow()
            if login.exec() != QDialog.Accepted:
                sys.exit(0)

        auto_ok = False

        window = MainApp()
        window.show()

        exit_code = app.exec()

        print(f"[退出追踪] app.exec() 返回, exit_code={exit_code}, "
              f"logout={getattr(window, '_logout_requested', False)}")

        if getattr(window, "_logout_requested", False):
            clear_session()
            continue

        sys.exit(exit_code)