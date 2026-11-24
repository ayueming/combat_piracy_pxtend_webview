# page_spider.py
"""待抓取页面 —— Tab 容器。

参考 page_base_info.py 的写法，把两个子页拼在一个 GreenTabWidget 里：
  - 站点抓取（SpiderSitePage）：MODULE_SEARCH 分类，即原来的 page_spider_site
  - 论坛/网盘（SpiderForumPage）：MODULE_FORUM + MODULE_PANSITE 分类
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout

from page_spider_site import SpiderPage as SpiderSitePage
from page_spider_forum import SpiderForumPage
from ui_components import GreenTabWidget


class SpiderPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 20)
        layout.setSpacing(10)

        self.tabs = GreenTabWidget()

        self.site_page = SpiderSitePage(self.main_window)
        self.forum_page = SpiderForumPage(self.main_window)

        self.tabs.addTab(self.site_page, "盗文网站")
        self.tabs.addTab(self.forum_page, "论坛帖子/智能体等")

        layout.addWidget(self.tabs)

    # ---------- 外部刷新入口 ----------
    def refresh_data(self):
        if hasattr(self.site_page, "refresh_data"):
            self.site_page.refresh_data()
        if hasattr(self.forum_page, "refresh_data"):
            self.forum_page.refresh_data()

    # 兼容 page_base_info 同名接口
    def refresh_table(self):
        self.refresh_data()