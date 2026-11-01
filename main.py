# ui调整

import os
import sys
import asyncio
import faulthandler

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QDialog
from PySide6.QtCore import Qt, QEvent, QTimer, Signal, QEasingCurve

# ===== 引入 Fluent 组件 =====
from qfluentwidgets import FluentWindow, NavigationItemPosition, FluentIcon as FIF, setTheme, Theme

# 导入后台通信服务
from ws_server import start_ws_server, ws_signals

# 导入你的页面模块
from page_books import BooksPage
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

VERSION = "3.4.3"


# ==========================================
# 🌟 仅保留版本号的辅助类（框架强制要求有这个类才能加自定义控件）
# ==========================================

_FAULT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faulthandler.log")
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
                        width: 10px;       /* 滚动条宽度 */
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
                        height: 10px;
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
        self.books_page = BooksPage(self)
        self.books_page.setObjectName("booksPage")

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
            self.books_page, self.links_page, self.search_page,
            self.spider_page, self.complaint_page, self.platforms_page,
            self.evidence_page, self.profile_page
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

        # ===== 3. 监听后台信号 =====
        ws_signals.new_data_inserted.connect(self.on_new_data)

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
        self.addSubInterface(self.books_page, FIF.LIBRARY, "作品管理")
        self.addSubInterface(self.links_page, FIF.PENCIL_INK, "盗文链接")
        self.addSubInterface(self.search_page, FIF.GLOBE, "检索管理")
        self.addSubInterface(self.spider_page, FIF.PASTE, "抓取管理")
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
        if hasattr(self, 'navigationInterface') and obj == self.navigationInterface:
            if event.type() == QEvent.Enter:
                self.collapse_timer.stop()
                self.expand_timer.start()  # 延迟 50ms 后再展开
            elif event.type() == QEvent.Leave:
                self.expand_timer.stop()
                self.collapse_timer.start()  # 延迟 250ms 后再收起
        return super().eventFilter(obj, event)

    def change_page(self, index):
        """页面切换时触发"""
        widget = self.stackedWidget.widget(index)
        if not widget:
            return

        name = widget.objectName()

        if name == "linksPage":
            self.links_page.refresh_table()
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

    def closeEvent(self, event):
        # spontaneous=True  → 用户/系统点了 X 或者 Alt+F4
        # spontaneous=False → 代码里某处调用了 close() / hide() 之类
        print(f"[退出追踪] MainApp.closeEvent 触发, spontaneous={event.spontaneous()}")
        super().closeEvent(event)


if __name__ == "__main__":
    import traceback
    import datetime


    def exception_hook(exctype, value, tb):
        error_msg = "".join(traceback.format_exception(exctype, value, tb))
        with open("crash_log.txt", "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.datetime.now()}] 崩溃信息:\n{error_msg}\n")
        sys.__excepthook__(exctype, value, tb)


    sys.excepthook = exception_hook

    app = QApplication(sys.argv)

    app.lastWindowClosed.connect(lambda: print("[退出追踪] lastWindowClosed 触发（最后一个窗口已关闭）"))
    app.aboutToQuit.connect(lambda: print("[退出追踪] aboutToQuit 触发（Qt 事件循环即将结束）"))

    start_ws_server()

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