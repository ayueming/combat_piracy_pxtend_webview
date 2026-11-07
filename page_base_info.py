# page_base_info.py
from PySide6.QtWidgets import QWidget, QVBoxLayout
from page_books import BooksPage
from page_author import AuthorPage

# 1. 引入我们刚写好的组件
from ui_components import GreenTabWidget

class BaseInfoPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 20)
        layout.setSpacing(10)

        # 2. 直接使用封装好的组件，不用再写 setStyleSheet 了！
        self.tabs = GreenTabWidget()

        self.books_page = BooksPage(self.main_window)
        self.author_page = AuthorPage(self.main_window)

        self.tabs.addTab(self.books_page, "作品管理")
        self.tabs.addTab(self.author_page, "作者管理")

        layout.addWidget(self.tabs)

    def refresh_table(self):
        self.books_page.refresh_table()
        self.author_page.refresh_table()