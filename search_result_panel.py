# search_result_panel.py
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QStackedWidget,
    QTabBar, QPushButton, QLabel, QMessageBox, QScrollArea,
    QSizePolicy, QButtonGroup, QGraphicsOpacityEffect
)
from PySide6.QtCore import (
    Qt, Signal, QTimer, QPropertyAnimation, QEasingCurve,
    QParallelAnimationGroup, QSignalBlocker
)
from PySide6.QtGui import QColor, QGuiApplication, QCursor

from db_config import get_conn
from app_data import ALL_SUB_PLATFORMS
from app_data.crawl_status_consts import (
    CRAWL_STATUS_PENDING, CRAWL_STATUS_UNMATCHED, CRAWL_STATUS_DONE,
    CRAWL_STATUS_ORGANIZED, CRAWL_STATUS_WHITELIST, CRAWL_STATUS_IGNORED,
    CRAWL_STATUS_DUPLICATE, CRAWL_STATUS_PARSE_FAILED
)
from db_schema import ensure_schema
from link_ingest_service import archive_batch_by_ids, change_draft_book, clear_draft_category
from search_batch_queries import get_batch_tables, find_latest_draft_batch

from ws_server import ws_signals
from parser_service import parser_signals, trigger_parse

from ui_components import NoWheelComboBox
from ui_card_base import BaseCard, CARD_STYLES, get_card_status


# =========================================================================
# Toast 提示
# =========================================================================
_ACTIVE_TOASTS = set()   # ★ 模块级保活：防止无 parent 的顶层窗口被 GC

class ToastLabel(QLabel):
    def __init__(self, parent, text, bg_color="#10B981"):
        super().__init__(None)  # 不挂 parent，做成独立顶层窗口

        # ★ 加 Qt.Tool，顶层窗口 + 不占任务栏，比纯 FramelessWindowHint 更稳
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.setText(text)
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {bg_color};
                color: white;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: bold;
            }}
        """)
        self.setAlignment(Qt.AlignCenter)
        self.adjustSize()

        # ===== 定位：屏幕上方居中 =====
        screen = None
        if parent is not None and parent.windowHandle() is not None:
            screen = parent.windowHandle().screen()
        if screen is None:
            screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.primaryScreen()

        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + 60
        self.move(x, y)

        # ★ 关键：加入保活集合，避免 Python GC 回收
        _ACTIVE_TOASTS.add(self)

        self.show()
        self.raise_()

        # ===== 淡出动画 =====
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.anim = QPropertyAnimation(self.effect, b"opacity")
        self.anim.setDuration(1800)
        self.anim.setStartValue(1.0)
        self.anim.setKeyValueAt(0.8, 1.0)
        self.anim.setEndValue(0.0)
        self.anim.finished.connect(self._on_anim_finished)
        self.anim.start()

    def _on_anim_finished(self):
        _ACTIVE_TOASTS.discard(self)   # ★ 动画播完，解除保活
        self.deleteLater()


# =========================================================================
# 数据卡片
# =========================================================================
class DataCard(BaseCard):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ★ 统一改为两行后，需要更高的空间
        if self.mode == "actionable":
            self.setFixedHeight(185)

    def build_top_right_widgets(self):
        if self.mode != "actionable":
            return None

        widgets = []

        # 关联作品下拉框（保持原样）
        self.combo_book = NoWheelComboBox()
        self.combo_book.setStyleSheet(
            "QComboBox { padding: 1px 4px; border: 1px solid #DCDCDC; border-radius: 4px; "
            "font-size: 12px; background: #FFF; font-weight: bold;}")
        self.combo_book.setFixedSize(140, 24)

        books = [b["name"] for b in self.parent_panel.books_data.values()]
        if self.book_name and self.book_name not in books:
            self.combo_book.addItem(self.book_name)
        self.combo_book.addItems(books)
        if self.book_name:
            self.combo_book.setCurrentText(self.book_name)
        elif books:
            self.combo_book.setCurrentIndex(0)
        self.combo_book.currentTextChanged.connect(self._on_book_changed)
        widgets.append(self.combo_book)

        return widgets

    def build_action_left_widgets(self):
        if self.mode != "actionable":
            return None

        widgets = []

        # 分类下拉框（原样搬运，仅是挂载位置变了）
        self.combo_cat = NoWheelComboBox()
        self.combo_cat.setStyleSheet(
            "QComboBox { padding: 1px 4px; border: 1px solid #DCDCDC; border-radius: 4px; "
            "font-size: 12px; background: #FFF; font-weight: bold; color: #059669; }")
        self.combo_cat.setFixedSize(75, 24)

        cur_cat = self.original_category or "其他"
        candidates = [cur_cat] + list(ALL_SUB_PLATFORMS)
        seen = set()
        for opt in candidates:
            if opt in seen:
                continue
            seen.add(opt)
            self.combo_cat.addItem(opt, f"keep:{opt}")
        idx = self.combo_cat.findData(f"keep:{cur_cat}")
        self.combo_cat.setCurrentIndex(idx if idx != -1 else 0)
        widgets.append(self.combo_cat)

        return widgets

    def build_action_widgets(self):
        if self.mode == "actionable":
            row1 = QHBoxLayout()
            row1.setSpacing(8)

            btn_ok = QPushButton("入库")
            btn_ok.setStyleSheet(
                "background: #ECFDF5; color: #059669; border: 1px solid #A7F3D0; "
                "border-radius: 4px; padding: 3px 10px; font-size: 12px;")
            btn_ok.setCursor(Qt.PointingHandCursor)
            btn_ok.clicked.connect(
                lambda: self.parent_panel._card_action(
                    self, self.db_id, self.combo_cat.currentData(), "keep"))

            btn_black = QPushButton("永删")
            btn_black.setStyleSheet(
                "background: #FEF2F2; color: #DC2626; border: 1px solid #FECACA; "
                "border-radius: 4px; padding: 3px 10px; font-size: 12px;")
            btn_black.setCursor(Qt.PointingHandCursor)
            btn_black.clicked.connect(
                lambda: self.parent_panel._card_action(self, self.db_id, "", "black"))

            btn_drop = QPushButton("忽略")
            btn_drop.setStyleSheet(
                "background: #F9FAFB; color: #6B7280; border: 1px solid #E5E7EB; "
                "border-radius: 4px; padding: 3px 10px; font-size: 12px;")
            btn_drop.setCursor(Qt.PointingHandCursor)
            btn_drop.clicked.connect(
                lambda: self.parent_panel._card_action(self, self.db_id, "", "drop"))

            row1.addWidget(btn_ok)
            row1.addWidget(btn_black)
            row1.addWidget(btn_drop)
            return row1

        # readonly：底部只显示分类文本
        row = QHBoxLayout()
        lbl_cat = QLabel(f"分类: {self.original_category or '无'}")
        lbl_cat.setStyleSheet("color: #4B5563; font-weight: bold; border: none;")
        row.addWidget(lbl_cat)
        return row

    def _on_book_changed(self, new_book_name):
        change_draft_book(self.db_id, new_book_name)
        self.book_name = new_book_name


# =========================================================================
# 卡片列表（单列瀑布流 + 懒加载）
# =========================================================================
class CardListWidget(QWidget):
    def __init__(self, parent_panel, mode="readonly"):
        super().__init__()
        self.parent_panel = parent_panel
        self.mode = mode
        self.all_records = []

        self.chunk_size = 10
        self.loaded_count = 0
        self.is_loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")

        self.card_layout = QVBoxLayout(self.scroll_content)
        self.card_layout.setContentsMargins(10, 10, 10, 10)
        self.card_layout.setSpacing(10)
        self.card_layout.setAlignment(Qt.AlignTop)

        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll, 1)

        self.load_timer = QTimer(self)
        self.load_timer.setSingleShot(True)
        self.load_timer.setInterval(150)
        self.load_timer.timeout.connect(self._load_more)

        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def set_data(self, records):
        # 1. 完全一样直接跳过
        if len(records) == len(self.all_records) and records and self.all_records:
            if records[0][0] == self.all_records[0][0] and records[-1][0] == self.all_records[-1][0]:
                return

        # 2. 只是少了一条数据（比如入库/删除），不重建UI，让卡片自己平滑消失
        if len(records) == len(self.all_records) - 1:
            old_ids = [r[0] for r in self.all_records]
            new_ids = [r[0] for r in records]
            diff = list(set(old_ids) - set(new_ids))
            if len(diff) == 1:
                self.all_records = records
                self.loaded_count -= 1
                self._update_footer_ui()
                return  # 保持极其丝滑的视觉

        # ★ 3. 整体重建（新增数据、切 Tab 等）
        self.all_records = records
        self.loaded_count = 0
        self.is_loading = False

        # ★ 核心：在重建期间屏蔽滚动条信号，防止撑开高度时误触懒加载
        sb = self.scroll.verticalScrollBar()
        blocker = QSignalBlocker(sb)

        try:
            # 彻底清空旧卡片
            while self.card_layout.count():
                item = self.card_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            if not self.all_records:
                lbl_empty = QLabel("暂无数据")
                lbl_empty.setAlignment(Qt.AlignCenter)
                lbl_empty.setStyleSheet("color: #9CA3AF; font-size: 14px; margin-top: 50px; border: none;")
                self.card_layout.addWidget(lbl_empty)
                self._update_footer_ui()
                sb.setValue(0)  # 空数据也回顶
                return

            self._load_more()

            # ★ 核心：重建后强制回到顶部
            sb.setValue(0)

        finally:
            # 解除信号屏蔽
            del blocker

    def _on_scroll(self, value):
        if self.is_loading or not self.all_records:
            return
        sb = self.scroll.verticalScrollBar()
        if sb.maximum() - value < 300 and self.loaded_count < len(self.all_records):
            self.load_timer.start()

    def _load_more(self):
        if self.loaded_count >= len(self.all_records):
            self._update_footer_ui()
            return

        self.is_loading = True
        start_idx = self.loaded_count
        end_idx = min(start_idx + self.chunk_size, len(self.all_records))
        page_records = self.all_records[start_idx:end_idx]

        # ★ 删掉了 is_near_bottom 的判定和底部的平滑滚动逻辑

        self.anim_groups = getattr(self, 'anim_groups', [])

        for i, r in enumerate(page_records):
            global_idx = start_idx + i + 1
            card = DataCard(global_idx, r, self.mode, self.parent_panel)

            card.setMaximumHeight(1)
            card.opacity_effect = QGraphicsOpacityEffect(card)
            card.setGraphicsEffect(card.opacity_effect)
            card.opacity_effect.setOpacity(0.0)

            self.card_layout.addWidget(card)

            anim_height = QPropertyAnimation(card, b"maximumHeight")
            anim_height.setDuration(400)
            anim_height.setStartValue(1)
            anim_height.setEndValue(card.height()) # ★ 原来是 185，现在动态读取
            anim_height.setEasingCurve(QEasingCurve.OutCubic)

            anim_opacity = QPropertyAnimation(card.opacity_effect, b"opacity")
            anim_opacity.setDuration(400)
            anim_opacity.setStartValue(0.0)
            anim_opacity.setEndValue(1.0)

            group = QParallelAnimationGroup(card)
            group.addAnimation(anim_height)
            group.addAnimation(anim_opacity)

            group.finished.connect(lambda g=group: self.anim_groups.remove(g) if g in self.anim_groups else None)
            self.anim_groups.append(group)

            QTimer.singleShot(i * 50, group.start)

        self.loaded_count = end_idx
        self.is_loading = False
        self._update_footer_ui()

    def _smooth_scroll_to_bottom(self):
        sb = self.scroll.verticalScrollBar()
        if not hasattr(self, "scroll_anim"):
            self.scroll_anim = QPropertyAnimation(sb, b"value")

        # 先停止之前的滚动动画，防止打架
        if self.scroll_anim.state() == QPropertyAnimation.Running:
            self.scroll_anim.stop()

        self.scroll_anim.setDuration(400)
        self.scroll_anim.setStartValue(sb.value())
        self.scroll_anim.setEndValue(sb.maximum())
        self.scroll_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.scroll_anim.start()

    def _update_footer_ui(self):
        if hasattr(self.parent_panel, "update_pagination_ui"):
            self.parent_panel.update_pagination_ui(self)

    def prev_page(self):
        pass

    def next_page(self):
        pass


# =========================================================================
# 右侧分拣面板
# =========================================================================
class SearchResultPanel(QWidget):
    request_preview = Signal(int, str, str, str, int)
    stats_updated = Signal(dict)

    def __init__(self, main_page, parent=None):
        super().__init__(parent)
        self.main_page = main_page
        self.current_batch_id = ""
        self._cached_tables = {"tab1_todo": [], "tab2_pending": [], "tab3_archive": [], "stats": {}}
        self.current_filter_status = None
        self._is_rendering = False

        self._books_data = {}
        self._load_books()

        self.setup_ui()
        ws_signals.new_data_inserted.connect(self._on_new_data_inserted)
        parser_signals.parse_finished.connect(self._on_parse_done)
        self._init_batch()

    def _load_books(self):
        self._books_data = {}
        conn = get_conn()
        try:
            books = conn.execute("SELECT id, name, pen_name, aliases FROM books ORDER BY name").fetchall()
            for b in books:
                self._books_data[b[0]] = {"name": b[1], "pen_name": b[2], "aliases": b[3]}
        finally:
            conn.close()

    def _init_batch(self):
        self.current_batch_id = "ALL"
        self.refresh_tables()

    @property
    def books_data(self):
        if not self._books_data:
            self._load_books()
        return self._books_data

    def setup_ui(self):
        # ★ 1. 最外层（right_layout）彻底透明，不包任何边框
        right_layout = QVBoxLayout(self)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        self.setStyleSheet("background: transparent;")

        # --- 2. Tab 栏悬浮在最上面 ---
        self.tab_bar = QTabBar()
        self.tab_bar.setUsesScrollButtons(True)
        self.tab_bar.setStyleSheet(
            "QTabBar { background: transparent; } "
            "QTabBar::tab { background: #F3F4F6; padding: 8px 24px; border: 1px solid #E5E7EB; border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 4px; font-size: 14px; font-weight: bold; } "
            "QTabBar::tab:hover { background: #E5E7EB; } "
            "QTabBar::tab:selected { background: white; color: #059669; border-top: 3px solid #059669; border-bottom: 1px solid white; }"
        )
        self.tab_bar.addTab("待处理")
        self.tab_bar.addTab("处理中")
        self.tab_bar.addTab("已归档")

        # ★ 核心修改：将 Tab 栏和新增的解析按钮放进同一水平布局
        tab_layout = QHBoxLayout()
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(10)
        tab_layout.addWidget(self.tab_bar)

        self.btn_parse = QPushButton("解析")
        self.btn_parse.setFixedSize(60, 28)  # 固定大小，防止撑开
        self.btn_parse.setStyleSheet(
            "QPushButton { background: #FEF3C7; color: #D97706; border: 1px solid #F59E0B; border-radius: 4px; font-size: 13px; font-weight: bold; } "
            "QPushButton:hover { background: #FDE68A; } "
            "QPushButton:disabled { background: #F3F4F6; color: #9CA3AF; border-color: #E5E7EB; }"
        )
        self.btn_parse.setCursor(Qt.PointingHandCursor)
        self.btn_parse.hide()  # 默认隐藏，有数据才显示
        self.btn_parse.clicked.connect(self._trigger_manual_parse)
        # 将按钮靠底部对齐，使其与 Tab 标签在视觉上平齐
        tab_layout.addWidget(self.btn_parse, 0, Qt.AlignBottom)

        right_layout.addLayout(tab_layout)

        # --- 3. 内部白底板（包裹标签、列表和底部操作栏） ---
        self.content_frame = QFrame()
        self.content_frame.setObjectName("ContentFrame")
        # ★ 核心：上边框去掉，上圆角为 0，完美贴合上面的 Tab 栏
        self.content_frame.setStyleSheet(
            "#ContentFrame { border: 1px solid #E5E7EB; border-top: none; border-radius: 0px 0px 8px 8px; background: white; }")

        content_layout = QVBoxLayout(self.content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # --- 4. 标签过滤行（横向可滚动） ---
        self.stats_scroll = QScrollArea()
        self.stats_scroll.setWidgetResizable(True)
        self.stats_scroll.setFrameShape(QFrame.NoFrame)
        self.stats_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stats_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.stats_scroll.setFixedHeight(50)  # 标签高 + 横向滚动条，按 DPI 微调
        self.stats_scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:horizontal {
                height: 4px;          /* ← 滚动条“厚度”，嫌太宽/太粗就改这里，比如 6px 或 4px */
                background: transparent;
                margin: 0;
            }
            QScrollBar::handle:horizontal {
                background: #D1D5DB;
                border-radius: 4px;   /* 圆角，高度改小后同步改成 height/2 */
                min-width: 30px;      /* ← 滑块最小“长度”，嫌滑块太长就调小，比如 20px */
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
        """)

        self.stats_container = QWidget()
        self.stats_container.setStyleSheet("background: transparent;")
        self.stats_layout = QHBoxLayout(self.stats_container)
        self.stats_layout.setSpacing(10)
        self.stats_layout.setContentsMargins(10, 5, 10, 5)
        self.stats_scroll.setWidget(self.stats_container)

        content_layout.addWidget(self.stats_scroll)

        # --- 5. 卡片列表区 ---
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("QStackedWidget { background: transparent; }")
        self.list_todo = CardListWidget(self, "actionable")
        self.list_pending = CardListWidget(self, "readonly")
        self.list_archive = CardListWidget(self, "readonly")
        self.stack.addWidget(self.list_todo)
        self.stack.addWidget(self.list_pending)
        self.stack.addWidget(self.list_archive)

        self.tab_bar.currentChanged.connect(self.stack.setCurrentIndex)
        self.tab_bar.currentChanged.connect(self.on_tab_changed)
        content_layout.addWidget(self.stack, 1)

        # --- 6. 底部操作栏 ---
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(10, 8, 10, 8)

        self.btn_manage = QPushButton("永删/白名单列表")
        self.btn_manage.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #4B5563; font-size: 13px; padding: 5px; } QPushButton:hover { color: #111827; }")
        self.btn_manage.setCursor(Qt.PointingHandCursor)
        self.btn_manage.clicked.connect(self.show_list_manager)
        bottom_layout.addWidget(self.btn_manage)

        bottom_layout.addStretch()

        self.lbl_page_info = QLabel("已加载 0 / 0 条")
        self.lbl_page_info.setStyleSheet("color: #6B7280; font-size: 13px; border: none; background: transparent;")
        self.lbl_page_info.setAlignment(Qt.AlignCenter)
        bottom_layout.addWidget(self.lbl_page_info)

        bottom_layout.addStretch()

        self.btn_del_sel = QPushButton("清空本次搜索结果")
        self.btn_del_sel.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #DC2626; font-size: 13px; padding: 5px; } QPushButton:hover { text-decoration: underline; }")
        self.btn_del_sel.setCursor(Qt.PointingHandCursor)
        self.btn_del_sel.clicked.connect(self.delete_selected_drafts)
        bottom_layout.addWidget(self.btn_del_sel)

        content_layout.addLayout(bottom_layout)

        # ★ 7. 把内部白底板加入最外层布局
        right_layout.addWidget(self.content_frame, 1)

    def _on_new_data_inserted(self, count):
        self.refresh_tables()

    def _trigger_manual_parse(self):
        """触发后台解析"""
        if getattr(self.main_page, "_is_background_parsing", False):
            return
        self.btn_parse.setText("解析中...")
        self.btn_parse.setEnabled(False)
        self.main_page._is_background_parsing = True
        # 触发全库解析
        trigger_parse(None, is_exact=False)

    def _on_parse_done(self, success, batch_id=None):
        # ★ 恢复按钮状态
        self.btn_parse.setText("解析")
        self.btn_parse.setEnabled(True)
        self.main_page._is_background_parsing = False

        self.refresh_tables()
        if success:
            if self.tab_bar.currentIndex() != 0:
                self.tab_bar.setCurrentIndex(0)
            ToastLabel(self, "✅ 数据已解析并归入待处理", "#10B981")

    def on_tab_changed(self, index):
        lists = [self.list_todo, self.list_pending, self.list_archive]
        if index < 0 or index >= len(lists):
            return
        self.current_filter_status = None
        self.update_current_tab_only(index)

    def update_pagination_ui(self, current_list_widget):
        if self.stack.currentWidget() != current_list_widget:
            return
        total = len(current_list_widget.all_records)
        loaded = current_list_widget.loaded_count
        self.lbl_page_info.setText(f"已加载 {loaded} / {total} 条")

    def refresh_tables(self):
        if getattr(self, '_is_rendering', False):
            return
        self._is_rendering = True
        try:
            self._load_books()
            ensure_schema()
            self._cached_tables = get_batch_tables("ALL")

            stats = self._cached_tables.get("stats", {})
            total_todo = stats.get("明确资源", 0) + stats.get("未知网站", 0) + stats.get("未命中关键词", 0)
            total_pending = stats.get("待解析", 0) + stats.get("解析失败", 0)
            total_archive = stats.get("已在库", 0) + stats.get("白名单", 0) + stats.get("已永删", 0)

            self.tab_bar.setTabText(0, f"待处理 ({total_todo})")
            self.tab_bar.setTabText(1, f"处理中 ({total_pending})")
            self.tab_bar.setTabText(2, f"已归档 ({total_archive})")

            # ★ 核心修改：根据待解析数量控制按钮显示
            pending_count = stats.get("待解析", 0)
            if pending_count > 0:
                self.btn_parse.show()
            else:
                self.btn_parse.hide()

            self.update_current_tab_only(self.tab_bar.currentIndex())
        finally:
            self._is_rendering = False

    def update_current_tab_only(self, tab_index):
        if tab_index < 0:
            return
        tables = self._cached_tables
        stats = tables.get("stats", {})

        if tab_index == 0:
            data = self._filter_records(tables.get("tab1_todo", []))
            self.list_todo.set_data(data)
            self.update_pagination_ui(self.list_todo)
        elif tab_index == 1:
            data = self._filter_records(tables.get("tab2_pending", []))
            self.list_pending.set_data(data)
            self.update_pagination_ui(self.list_pending)
        elif tab_index == 2:
            data = self._filter_records(tables.get("tab3_archive", []))
            self.list_archive.set_data(data)
            self.update_pagination_ui(self.list_archive)

        self._render_tags(stats)
        self.stats_updated.emit(stats)

    def _filter_records(self, records):
        if not self.current_filter_status:
            return records
        if self.current_filter_status == "手动拾取":
            return [r for r in records if len(r) > 13 and r[13] == "手动获取"]
        return [r for r in records if get_card_status(r) == self.current_filter_status]

    def _render_tags(self, stats):
        while self.stats_layout.count():
            item = self.stats_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.spacerItem():
                del item

        tooltip_map = {"明确资源": "建议入库", "未知网站": "待分类", "未命中关键词": "建议永删"}

        if self.tab_bar.currentIndex() == 0:
            tags = [
                ("全部", None, "#F3F4F6", "#374151"),
                ("明确资源", "明确资源", "#ECFDF5", "#065F46"),
                ("未知网站", "未知网站", "#FEF3C7", "#92400E"),
                ("未命中关键词", "未命中关键词", "#F3F4F6", "#374151"),
                ("手动拾取", "手动拾取", "#FDF4FF", "#C026D3"),
            ]
            total_count = stats.get("明确资源", 0) + stats.get("未知网站", 0) + stats.get("未命中关键词", 0)
        elif self.tab_bar.currentIndex() == 1:
            tags = [
                ("全部", None, "#F3F4F6", "#374151"),
                ("待解析", "待解析", "#EFF6FF", "#1E3A8A"),
                ("解析失败", "解析失败", "#F9FAFB", "#374151"),
            ]
            total_count = stats.get("待解析", 0) + stats.get("解析失败", 0)
        else:
            tags = [
                ("全部", None, "#F3F4F6", "#374151"),
                ("已在库", "已在库", "#ECFDF5", "#064E3B"),
                ("白名单", "白名单", "#F0F9FF", "#0369A1"),
                ("已永删", "已永删", "#FEF2F2", "#7F1D1D"),
            ]
            total_count = stats.get("已在库", 0) + stats.get("白名单", 0) + stats.get("已永删", 0)

        if not hasattr(self, 'tag_group'):
            self.tag_group = QButtonGroup(self)
            self.tag_group.setExclusive(True)

        for text, status_key, bg, color in tags:
            count = stats.get(status_key, 0) if status_key else total_count
            lbl = QPushButton(f"{text} ({count})")
            if text in tooltip_map:
                lbl.setToolTip(tooltip_map[text])
            lbl.setCheckable(True)
            lbl.setCursor(Qt.PointingHandCursor)
            lbl.setStyleSheet(f"""
                QPushButton {{ background: {bg}; color: {color}; padding: 4px 12px; border-radius: 12px; font-size: 13px; border: 1px solid transparent; }}
                QPushButton:hover, QPushButton:checked {{ border: 1px solid {color}; }}
            """)
            lbl.clicked.connect(lambda checked, s=status_key: self.on_filter_clicked(s))
            if self.current_filter_status == status_key:
                lbl.setChecked(True)
            self.tag_group.addButton(lbl)
            self.stats_layout.addWidget(lbl)

        self.stats_layout.addStretch()

    def on_filter_clicked(self, status_key):
        self.current_filter_status = status_key
        self.update_current_tab_only(self.tab_bar.currentIndex())

    def _card_action(self, card_widget, db_id, action_data, action_type):
        if action_type == "keep":
            category = action_data.split(":")[1] if action_data and ":" in action_data else "其他"
            inserted, _, _ = archive_batch_by_ids([(db_id, category)], [], [])
            if inserted > 0:
                ToastLabel(self, "✅ 已成功入库", "#10B981")
            else:
                ToastLabel(self, "ℹ️ 该链接已在库，已跳过", "#6B7280")
        elif action_type == "black":
            archive_batch_by_ids([], [], [db_id])
            ToastLabel(self, "🚫 已加入黑名单", "#EF4444")
        elif action_type == "drop":
            archive_batch_by_ids([], [db_id], [])
            ToastLabel(self, "⏭️ 已忽略", "#6B7280")

        # 让被点击的卡片执行折叠动画，下面的会自动顶上
        card_widget.smooth_disappear()

        # 延迟 310 毫秒（等动画播完）再去刷新左侧的统计数据
        QTimer.singleShot(310, self.refresh_tables)

        if hasattr(self.main_page, 'main_window') and self.main_page.main_window:
            if hasattr(self.main_page.main_window, 'links_page'):
                self.main_page.main_window.links_page.refresh_table()

    def _preview_card_url(self, db_id, raw_url, category, book_name="", is_mobile=0):
        self.request_preview.emit(db_id, raw_url, category, book_name, is_mobile)

    def delete_selected_drafts(self):
        """清空本次搜索结果里所有 tab 的 draft（不区分 tab）"""
        # 重新拉一次，避免用 _cached_tables 里可能已过期的数据
        tables = get_batch_tables(self.current_batch_id)

        ids_to_delete = []
        for key in ("tab1_todo", "tab2_pending", "tab3_archive"):
            for r in tables.get(key, []):
                ids_to_delete.append(r[0])

        if not ids_to_delete:
            QMessageBox.information(self, "提示", "本次搜索结果没有可清空的数据。")
            return

        reply = QMessageBox.question(
            self, "确认清空本次搜索结果",
            f"本次搜索结果共 {len(ids_to_delete)} 条记录，确定全部清空吗？\n\n"
            f"（仅从草稿箱移除，不影响已入库的资源和黑名单）",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # 传全量 id 给底层，底层只处理 draft 表里的行；
        # tab3 里的"已在库/白名单/已永删"本就是归档状态，不会受影响
        clear_draft_category(ids_to_delete)

        # 回到"待处理" tab，方便用户直观看到已清空
        self.tab_bar.setCurrentIndex(0)
        self.refresh_tables()

    def show_list_manager(self):
        from list_manager_dialog import ListManagerDialog
        dlg = ListManagerDialog(self)
        dlg.exec()

    # ★ 新增：刷新所有卡片的高亮状态
    def update_preview_highlight(self):
        active_id = getattr(self.main_page, "active_preview_db_id", None)
        # 遍历三个列表下所有的 DataCard
        for lst in [self.list_todo, self.list_pending, self.list_archive]:
            for card in lst.findChildren(DataCard):
                card.set_highlight(card.db_id == active_id)