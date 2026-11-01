# ui调整

import os
import sys
import asyncio

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QDialog
from PySide6.QtCore import Qt

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

VERSION = "2.0.0"


class MainApp(FluentWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(f"打盗维权系统_试用版_v{VERSION}")

        # 全局美化：覆盖原生的丑陋滚动条
        self.setStyleSheet("""
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

        # 设置主题颜色（可选：深色或浅色）
        # setTheme(Theme.DARK)

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
        # FluentWindow 要求每个页面必须有唯一的 objectName
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

        self.platforms_page = PlatformsPage()  # 注意：如果你的这个类不接收 main_page，就不传 self
        self.platforms_page.setObjectName("platformsPage")

        self.evidence_page = EvidencePage()
        self.evidence_page.setObjectName("evidencePage")

        self.profile_page = ProfilePage(self)
        self.profile_page.setObjectName("profilePage")

        # ==========================================
        # 🌟 新增：强制把内部页面背景改成纯白色，解决底色断层问题
        # ==========================================
        # 方法 1：直接把管理页面的 stackedWidget 背景设为白色
        self.stackedWidget.setStyleSheet("""
                    QStackedWidget {
                        background-color: white;
                        border-radius: 12px; /* 🌟 这里控制圆角的大小，数值越大越圆 */
                        margin: 10px;        /* 🌟 关键：四周留出空隙，否则白框顶死边缘，圆角出不来 */
                    }
                """)

        # 方法 2：遍历你所有的页面，把它们的背景强制设为白色
        pages = [
            self.books_page, self.links_page, self.search_page,
            self.spider_page, self.complaint_page, self.platforms_page,
            self.evidence_page, self.profile_page
        ]
        for page in pages:
            page.setStyleSheet("background-color: white;")
        # ==========================================

        self.navigationInterface.panel.setStyleSheet("background-color: #F0F4F1; border: none;")

        # ===== 2. 初始化导航栏 =====
        self.init_navigation()

        # ===== 3. 监听后台信号 =====
        ws_signals.new_data_inserted.connect(self.on_new_data)

        # ===== 4. 监听页面切换，触发数据刷新 =====
        # FluentWindow 内部用 stackedWidget 管理页面，监听它的切换即可
        self.stackedWidget.currentChanged.connect(self.change_page)

    def init_navigation(self):
        """将页面添加到左侧导航栏，并配上漂亮的图标"""

        # ==========================================
        # 方案A：调整侧边栏展开的宽度 (默认约240，这里改为200)
        # 如果觉得还是宽，可以继续改小，如 180
        # ==========================================
        self.navigationInterface.setExpandWidth(160)

        # 注意：addSubInterface 的第 3 个参数是图标，FIF 里有大量内置图标
        # 已根据你的要求替换为最合适的图标
        self.addSubInterface(self.books_page, FIF.LIBRARY, "作品管理")
        self.addSubInterface(self.links_page, FIF.PENCIL_INK, "盗文链接")
        self.addSubInterface(self.search_page, FIF.GLOBE, "检索管理")  # 纠正：global -> GLOBE
        self.addSubInterface(self.spider_page, FIF.PASTE, "抓取管理")
        self.addSubInterface(self.complaint_page, FIF.SEND, "投诉管理")
        self.addSubInterface(self.platforms_page, FIF.TAG, "投诉平台")  # 纠正：sent -> SEND
        self.addSubInterface(self.evidence_page, FIF.FOLDER, "维权资料")

        # 把「个人与帮助」固定在左下角
        self.addSubInterface(self.profile_page, FIF.SETTING, "个人与帮助", NavigationItemPosition.BOTTOM)

        # 默认选中“作品管理”
        self.navigationInterface.setCurrentItem("booksPage")

        # （可选）把版本号写在左下角
        version_label = QLabel(f"v.{VERSION} ©HowLittle")
        version_label.setStyleSheet("color: #9CA3AF; font-size: 11px; padding: 10px;")
        # 附加到侧边栏底部
        try:
            self.navigationInterface.addWidget(
                routeKey="version_label",
                widget=version_label,
                onClick=lambda: None,
                position=NavigationItemPosition.BOTTOM
            )
        except Exception:
            pass

    def change_page(self, index):
        """页面切换时触发，替代你原来的 change_page"""
        widget = self.stackedWidget.widget(index)
        if not widget:
            return

        # 通过 objectName 来判断当前切到了哪个页面
        name = widget.objectName()

        if name == "linksPage":
            self.links_page.refresh_table()
        elif name == "searchPage":
            self.search_page.load_books()
            # 右侧面板刷新
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
        # SearchResultPanel 已经自己监听并刷新了，这里不需要额外操作。


if __name__ == "__main__":
    # ==========================================
    # ★ 新增：全局异常捕获（防闪退无提示，用于写日志排查）
    # ==========================================
    import traceback
    import datetime


    def exception_hook(exctype, value, tb):
        # 捕获所有未处理的异常并写入本地文件
        error_msg = "".join(traceback.format_exception(exctype, value, tb))
        with open("crash_log.txt", "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.datetime.now()}] 崩溃信息:\n{error_msg}\n")
        # 调用系统默认的异常处理（如果你在IDE里运行，还是会在控制台打印）
        sys.__excepthook__(exctype, value, tb)


    # 将系统的异常钩子替换成我们自己的
    sys.excepthook = exception_hook
    # ==========================================

    app = QApplication(sys.argv)

    # 启动后台通信线程
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

        if getattr(window, "_logout_requested", False):
            clear_session()
            continue

        sys.exit(exit_code)