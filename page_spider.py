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

        # ★ 新增：内部 tab 切换时释放离开 tab 的 picker
        self.tabs.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(self.tabs)

    def _on_tab_changed(self, index: int):
        """抓取管理页内部 tab 切换：释放刚才那个 tab 的 picker 锁。
        site 的 picker 是独立模态窗口，无需处理；只需管 forum。
        切到 site → 释放 forum；切回 forum → 无事可做（site 已自清）。
        """
        w = self.tabs.widget(index)
        if w is not self.forum_page:
            # 当前不是 forum tab → 释放 forum 的 picker
            try:
                self.forum_page.release_preview_lock()
            except Exception as e:
                print(f"[SpiderPage] 切 tab 释放 forum 预览锁失败: {e}")

    def refresh_data(self):
        if hasattr(self.site_page, "refresh_data"):
            self.site_page.refresh_data()
        if hasattr(self.forum_page, "refresh_data"):
            self.forum_page.refresh_data()

    def refresh_table(self):
        self.refresh_data()

    def release_preview_lock(self):
        if hasattr(self, "forum_page"):
            self.forum_page.release_preview_lock()