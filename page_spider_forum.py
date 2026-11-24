# page_spider_forum.py
"""待抓取页面 —— 论坛/网盘 Tab。

三列布局：
  左：SpiderForumWebView（双击中列任意行 → 在此预览）
  中：可预览链接列表（sites 表里 MODULE_FORUM ∪ MODULE_PANSITE 分类）
  右：拾取结果面板（draft_links 里 batch_id=SPIDER_FORUM_PICK 的草稿，
      复用 search_result_panel 的 CardListWidget + DataCard）

拾取流程：
  用户在左列 picker 里点击"手动拾取 / 仅保存 / 解析本次拾取"
    → PickerJsBridge → save_picked_link → draft_links(SPIDER_FORUM_PICK)
    → PickerWebView 信号回传 → 这里刷新右列
    → 卡片按钮（入库/永删/忽略）复用 archive_batch_by_ids
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QFrame, QTableWidget, QTableWidgetItem,
                               QHeaderView, QAbstractItemView, QSplitter,
                               QPushButton, QSizePolicy, QMessageBox)

from app_data.complaint_platform_consts import (
    PLATFORM_CONFIG, MODULE_FORUM, MODULE_PANSITE,
)
from app_data import ALL_SUB_PLATFORMS
from db_config import get_conn
from mobile_url_utils import resolve_is_mobile

from ui_styles import ROW_SELECTED_BG, TABLE_HEADER_BASE
from ui_components import (
    CenteredCheckBoxDelegate,
    HEADER_BG_COLOR,
)
from ui_card_base import BaseCard

# 复用 search_result_panel 里已经写好的卡片与工具
from search_result_panel import CardListWidget, ToastLabel
from search_batch_queries import get_batch_tables
from link_ingest_service import archive_batch_by_ids
from parser_service import parser_signals, trigger_parse

from spider_forum_webview import SpiderForumWebView, FORUM_PICK_BATCH_ID
from license_manager import license_mgr


# ============ 模块 → 分类映射 ============
FORUM_PANSITE_CATEGORIES = [
    display_name
    for sub_list in PLATFORM_CONFIG.values()
    for display_name, _p, m_code, _w, _a in sub_list
    if m_code in (MODULE_FORUM, MODULE_PANSITE)
]


# =========================================================================
# 左列：WebView 容器（直接复用 spider_forum_webview.SpiderForumWebView）
# （本文件不重复定义，见 import）
# =========================================================================


# =========================================================================
# 中列：可预览链接列表
# =========================================================================
class ForumLinkTable(QTableWidget):
    """中列链接表。双击行 → 发信号，由父页面转去左列 WebView。"""

    from PySide6.QtCore import Signal
    row_activated = Signal(int)  # 行号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(5)
        self.setHorizontalHeaderLabels(
            ["关联作品", "分类", "页面链接", "名称", "属性"])

        self.verticalHeader().setFixedWidth(35)
        self.verticalHeader().setDefaultSectionSize(35)

        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)  # 链接列宽一点

        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setShowGrid(True)
        self.setStyleSheet(
            f"""
            QTableWidget {{ border: 1px solid #E9ECEF; background-color: #FFFFFF; }}
            QTableWidget::item:selected {{ background-color: {ROW_SELECTED_BG}; color: #333; }}
            """
            + TABLE_HEADER_BASE
            + "QHeaderView::section:horizontal { background: transparent; }"
        )
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

        self.cellDoubleClicked.connect(self._on_double_click)

    def _on_double_click(self, row, _col):
        self.row_activated.emit(row)


# =========================================================================
# 右列：拾取结果面板（复用 CardListWidget + DataCard）
# =========================================================================
class SpiderForumResultPanel(QWidget):
    """给 CardListWidget / DataCard 提供 parent_panel 契约：

    - books_data              （DataCard 下拉框用）
    - main_page               （DataCard 高亮判断用，这里指向父页面即可）
    - _card_action            （入库/永删/忽略）
    - _preview_card_url       （双击卡片链接 → 左列 WebView 加载）
    - update_pagination_ui    （CardListWidget 底部刷新钩子）
    """

    def __init__(self, parent_page):
        super().__init__(parent_page)
        self.parent_page = parent_page
        self.main_page = parent_page         # BaseCard 用 self.parent_panel.main_page
        self._books_data = {}
        self._load_books()
        self.setup_ui()

        parser_signals.parse_finished.connect(self._on_parse_finished)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 顶部栏
        top = QHBoxLayout()
        top.setContentsMargins(8, 6, 8, 0)

        title = QLabel("拾取结果")
        title.setStyleSheet(
            "QLabel { font-size: 14px; font-weight: bold; color: #111827; border: none; }")
        top.addWidget(title)
        top.addStretch()

        self.btn_parse = QPushButton("解析本次拾取")
        self.btn_parse.setFixedHeight(26)
        self.btn_parse.setStyleSheet(
            "QPushButton { background: #FEF3C7; color: #D97706; border: 1px solid #F59E0B; "
            "border-radius: 4px; font-size: 12px; font-weight: bold; padding: 0 10px; } "
            "QPushButton:hover { background: #FDE68A; } "
            "QPushButton:disabled { background: #F3F4F6; color: #9CA3AF; border-color: #E5E7EB; }")
        self.btn_parse.setCursor(Qt.PointingHandCursor)
        self.btn_parse.clicked.connect(self._trigger_parse)
        top.addWidget(self.btn_parse)

        layout.addLayout(top)

        # 卡片列表
        self.card_list = CardListWidget(self, "actionable")
        layout.addWidget(self.card_list, 1)

    # ---------- books ----------
    def _load_books(self):
        self._books_data = {}
        conn = get_conn()
        try:
            for b in conn.execute("SELECT id, name, pen_name, aliases FROM books ORDER BY name"):
                self._books_data[b[0]] = {"name": b[1], "pen_name": b[2], "aliases": b[3]}
        finally:
            conn.close()

    @property
    def books_data(self):
        if not self._books_data:
            self._load_books()
        return self._books_data

    # ---------- 刷新 ----------
    def refresh_tables(self):
        self._load_books()
        try:
            tables = get_batch_tables(FORUM_PICK_BATCH_ID)
        except Exception as e:
            print(f"[ForumPanel] 查询草稿失败: {e}")
            return

        records = list(tables.get("tab1_todo", [])) + list(tables.get("tab2_pending", []))

        # ★ 只看手动拾取（picker 里"手动拾取链接 / 仅保存当前网址"两条路径）
        #   record[13] 是 pick_origin，与 BaseCard.is_manual 同源
        records = [r for r in records if len(r) > 13 and r[13] == "手动获取"]

        # 去重（同一 id 可能同时落两个 tab）
        seen, uniq = set(), []
        for r in records:
            if r[0] in seen:
                continue
            seen.add(r[0])
            uniq.append(r)

        self.card_list.set_data(uniq)
        self.btn_parse.setEnabled(bool(uniq))

    # ---------- 卡片动作 ----------
    def _card_action(self, card_widget, db_id, action_data, action_type):
        if action_type == "keep":
            category = action_data.split(":")[1] if action_data and ":" in action_data else "其他"
            archive_batch_by_ids([(db_id, category)], [], [])
            ToastLabel(self, "✅ 已成功入库", "#10B981")
        elif action_type == "black":
            archive_batch_by_ids([], [], [db_id])
            ToastLabel(self, "🚫 已加入黑名单", "#EF4444")
        elif action_type == "drop":
            archive_batch_by_ids([], [db_id], [])
            ToastLabel(self, "⏭️ 已忽略", "#6B7280")

        card_widget.smooth_disappear()
        QTimer.singleShot(310, self.refresh_tables)

        # 顺手通知盗版链接库刷新
        try:
            mw = getattr(self.parent_page, "main_window", None)
            if mw and hasattr(mw, "links_page"):
                mw.links_page.refresh_table()
        except Exception:
            pass

    # ---------- 卡片链接双击 → 左列 WebView ----------
    def _preview_card_url(self, db_id, raw_url, category, book_name="", is_mobile=0):
        self.parent_page.load_to_webview(raw_url, book_name, category, is_mobile)

    # ---------- 分页钩子（不用，空实现即可）----------
    def update_pagination_ui(self, current_list_widget):
        pass

    # ---------- 解析 ----------
    def _trigger_parse(self):
        if getattr(self.parent_page, "_is_background_parsing", False):
            return
        self.parent_page._is_background_parsing = True
        self.btn_parse.setText("解析中...")
        self.btn_parse.setEnabled(False)
        try:
            trigger_parse(FORUM_PICK_BATCH_ID, only_manual=True)
        except Exception as e:
            print(f"[ForumPanel] 触发解析失败: {e}")
            self.parent_page._is_background_parsing = False
            self.btn_parse.setText("解析本次拾取")
            self.btn_parse.setEnabled(True)

    def _on_parse_finished(self, success, batch_id=None):
        self.parent_page._is_background_parsing = False
        self.btn_parse.setText("解析本次拾取")
        self.btn_parse.setEnabled(True)
        self.refresh_tables()
        if success:
            ToastLabel(self, "✅ 解析完成", "#10B981")


# =========================================================================
# 主页面
# =========================================================================
class SpiderForumPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self._is_background_parsing = False
        self.setup_ui()
        self.refresh_data()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(0)

        # ===== 横向主分割：左 WebView | 右侧上下分栏 =====
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ----- 左：WebView（放大）-----
        self.webview = SpiderForumWebView(self)
        self.webview.setMinimumWidth(360)
        splitter.addWidget(self.webview)

        # ----- 右侧：垂直分栏（上：可预览链接 / 下：拾取结果）-----
        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.setChildrenCollapsible(False)

        # 上：可预览链接列表
        mid = QWidget()
        mid_layout = QVBoxLayout(mid)
        mid_layout.setContentsMargins(6, 0, 6, 0)
        mid_layout.setSpacing(4)

        mid_title = QLabel("可预览链接")
        mid_title.setStyleSheet(
            "QLabel { font-size: 14px; font-weight: bold; color: #111827; border: none; }")
        mid_layout.addWidget(mid_title)

        self.link_table = ForumLinkTable()
        self.link_table.row_activated.connect(self._on_link_double_clicked)
        mid_layout.addWidget(self.link_table, 1)

        right_splitter.addWidget(mid)

        # 下：拾取结果面板
        self.result_panel = SpiderForumResultPanel(self)
        right_splitter.addWidget(self.result_panel)

        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 1)
        right_splitter.setSizes([420, 520])

        splitter.addWidget(right_splitter)

        # 左右比例：WebView 占大头
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([980, 460])

        layout.addWidget(splitter, 1)

        # ===== 信号：picker → 结果面板刷新 =====
        self.webview.picked.connect(lambda _rec: self.result_panel.refresh_tables())
        self.webview.saved_page.connect(lambda _rec: self.result_panel.refresh_tables())
        self.webview.batch_saved.connect(lambda _bid: self.result_panel.refresh_tables())

    # ---------- 中列数据 ----------
    def refresh_data(self):
        self._refresh_link_table()
        self.result_panel.refresh_tables()

    def _refresh_link_table(self):
        self.link_table.setRowCount(0)

        if not FORUM_PANSITE_CATEGORIES:
            return

        conn = get_conn()
        try:
            ph = ",".join("?" * len(FORUM_PANSITE_CATEGORIES))
            rows = conn.execute(
                f"""SELECT s.id,
                           COALESCE(b.name, s.book_name) AS real_book_name,
                           s.category, s.site_url, s.name, s.attr,
                           s.crawl_status, s.resource_status, s.complaint_time, s.is_mobile
                    FROM sites s
                    LEFT JOIN books b ON s.book_id = b.id
                    WHERE COALESCE(s.category, '') IN ({ph})
                      AND (s.complaint_time IS NULL OR s.complaint_time = '')
                      AND (s.resource_status IS NULL OR s.resource_status = '')
                      AND (s.picker_status IS NULL OR s.picker_status = '' OR s.picker_status = '未拾取')
                    ORDER BY s.id DESC""",
                list(FORUM_PANSITE_CATEGORIES),
            ).fetchall()
        finally:
            conn.close()

        for rec in rows:
            (db_id, book_name, cat, url, name, attr,
             crawl_status, res_status, comp_time, is_mobile) = rec
            final_mobile = resolve_is_mobile(is_mobile, url)

            row = self.link_table.rowCount()
            self.link_table.insertRow(row)

            # 关联作品
            it_book = QTableWidgetItem(book_name or "未关联")
            it_book.setData(Qt.UserRole, {
                "id": db_id, "is_mobile": final_mobile,
            })
            self.link_table.setItem(row, 0, it_book)

            # 分类
            self.link_table.setItem(row, 1, QTableWidgetItem(cat or "未分类"))

            # 页面链接（蓝色下划线）
            it_url = QTableWidgetItem(url or "")
            it_url.setToolTip("双击此行即可预览")
            it_url.setForeground(QColor("#3B82F6"))
            f = it_url.font()
            f.setUnderline(True)
            it_url.setFont(f)
            self.link_table.setItem(row, 2, it_url)

            # 名称 / 属性
            self.link_table.setItem(row, 3, QTableWidgetItem(name or ""))
            self.link_table.setItem(row, 4, QTableWidgetItem(attr or ""))

    # ---------- 双击中列 → 加载到左列 ----------
    def _on_link_double_clicked(self, row):
        url_item = self.link_table.item(row, 2)
        if not url_item:
            return
        url = url_item.text().strip()
        if not url:
            return

        book_item = self.link_table.item(row, 0)
        data = book_item.data(Qt.UserRole) if book_item else None
        is_mobile = (data or {}).get("is_mobile", 0)
        book_name = book_item.text() if book_item else ""
        if book_name == "未关联":
            book_name = ""
        cat = self.link_table.item(row, 1).text() or "其他"

        self.load_to_webview(url, book_name, cat, is_mobile)

    # ---------- 对外：把 URL 加载到左列 ----------
    def load_to_webview(self, url, book_name, category, is_mobile):
        if license_mgr.is_expired:
            QMessageBox.warning(
                self, "授权已过期",
                "您的会员已过期，无法使用网页预览功能。\n"
                "请前往【个人中心】查看详情或联系管理员续费。")
            return

        self.webview.set_context(book_name, category, url, is_mobile)
        ok = self.webview.load_url(url)
        if not ok:
            ToastLabel(
                self,
                "⚠️ 后台正在执行其他预览/搜索任务，请稍后再试",
                "#F59E0B",
            )