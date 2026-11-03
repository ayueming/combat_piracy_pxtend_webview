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
# 卡片样式
# =========================================================================
CARD_STYLES = {
    "明确资源": {"color": "#10B981", "bg": "#ECFDF5", "text": "#065F46", "border": "4px solid #10B981"},
    "未知网站": {"color": "#F59E0B", "bg": "#FEF3C7", "text": "#92400E", "border": "4px solid #F59E0B"},
    "关键词未命中": {"color": "#9CA3AF", "bg": "#F3F4F6", "text": "#374151", "border": "4px solid #9CA3AF"},
    "待解析": {"color": "#3B82F6", "bg": "#EFF6FF", "text": "#1E3A8A", "border": "4px solid #3B82F6"},
    "解析失败": {"color": "#4B5563", "bg": "#F9FAFB", "text": "#374151", "border": "4px solid #4B5563"},
    "已在库": {"color": "#047857", "bg": "#ECFDF5", "text": "#064E3B", "border": "4px solid #047857"},
    "白名单": {"color": "#0EA5E9", "bg": "#F0F9FF", "text": "#0369A1", "border": "4px solid #0EA5E9"},
    "已永删": {"color": "#991B1B", "bg": "#FEF2F2", "text": "#7F1D1D", "border": "4px solid #991B1B"},
}


def get_card_status(record):
    crawl_status = record[11] if len(record) > 11 else 0
    domain_matched = record[12] if len(record) > 12 else 0
    if crawl_status == CRAWL_STATUS_ORGANIZED:
        return "明确资源" if domain_matched == 1 else "未知网站"
    elif crawl_status == CRAWL_STATUS_UNMATCHED:
        return "关键词未命中"
    elif crawl_status == CRAWL_STATUS_PENDING:
        return "待解析"
    elif crawl_status == CRAWL_STATUS_PARSE_FAILED:
        return "解析失败"
    elif crawl_status == CRAWL_STATUS_DUPLICATE:
        return "已在库"
    elif crawl_status == CRAWL_STATUS_WHITELIST:
        return "白名单"
    elif crawl_status == CRAWL_STATUS_IGNORED:
        return "已永删"
    return "未知网站"


# =========================================================================
# 数据卡片
# =========================================================================
class DataCard(QFrame):
    def __init__(self, row_idx, record, mode, parent_panel):
        super().__init__()
        self.record = record
        self.mode = mode
        self.parent_panel = parent_panel
        self.db_id = record[0]
        self.title = record[1] or "无标题"
        self.body_text = record[2] or ""
        self.raw_url = record[3] or (record[15] if len(record) > 15 else "") or ""
        self.book_name = record[4] or ""
        self.original_category = record[5] or ""
        self.target_platform = record[6] or ""
        self.fetch_time = record[7] or ""
        self.source_site = record[8] if len(record) > 8 else ""
        self.pick_origin = record[13] if len(record) > 13 else ""
        self.has_pan = record[14] if len(record) > 14 else 0
        self.is_mobile = record[17] if len(record) > 17 else 0

        self.status = get_card_status(record)
        self.is_manual = (self.pick_origin == "手动获取")
        style = CARD_STYLES.get(self.status, CARD_STYLES["未知网站"])

        self.setObjectName("DataCard")
        self.setCursor(Qt.ArrowCursor)
        self.setFixedHeight(185)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            f"#DataCard {{ background-color: #FFFFFF; border: 1px solid #E5E7EB; border-left: {style['border']}; border-radius: 6px; }}"
            f"#DataCard:hover {{ background-color: #F8FAFC; border: 1px solid #93C5FD; border-left: {style['border']}; }}"
        )
        self.display_url = "⏳ 等待解析真实链接..." if "baidu.com/link?url=" in self.raw_url else self.raw_url

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(6)

        # ===== 第一行：标签、时间 =====
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)  # 控制三个标签之间的横向距离

        # 1. 主状态标签（明确资源、未知网站等）
        lbl_status = QLabel(self.status)
        lbl_status.setStyleSheet(
            f"background: {style['bg']}; color: {style['text']}; "
            f"padding: 0px 4px; margin: 0px; "
            f"border-radius: 3px; font-size: 12px; font-weight: bold; border: none;"
        )
        lbl_status.setFixedHeight(22)  # 强制高度 18
        lbl_status.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)  # 只包裹文字
        top_row.addWidget(lbl_status)

        # 2. 手动拾取标签
        if self.is_manual:
            lbl_manual = QLabel("📌 手动拾取")
            lbl_manual.setStyleSheet(
                "background: #FDF4FF; color: #C026D3; border: 1px solid #F0ABFC; "
                "padding: 0px 4px; margin: 0px; border-radius: 3px; font-size: 12px; font-weight: bold;"
            )
            lbl_manual.setFixedHeight(22)  # 强制高度 18，和主状态齐平
            lbl_manual.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            top_row.addWidget(lbl_manual)

        # 3. 含网盘标签
        if self.has_pan:
            lbl_pan = QLabel("📦 含网盘")
            lbl_pan.setStyleSheet(
                "background: #FEF2F2; color: #DC2626; border: 1px solid #FECACA; "
                "padding: 0px 4px; margin: 0px; border-radius: 3px; font-size: 12px; font-weight: bold;"
            )
            lbl_pan.setFixedHeight(22)  # 强制高度 18，和主状态齐平
            lbl_pan.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            top_row.addWidget(lbl_pan)

        top_row.addStretch()
        lbl_time = QLabel(self.fetch_time)
        lbl_time.setStyleSheet("color: #9CA3AF; font-size: 12px; border: none;")
        top_row.addWidget(lbl_time)
        layout.addLayout(top_row)

        # ===== 第二行：标题 =====
        lbl_title = QLabel(self.title)
        lbl_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #111827; border: none;")
        lbl_title.setWordWrap(False)
        lbl_title.setToolTip(self.title)
        lbl_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lbl_title.setFixedHeight(24)
        layout.addWidget(lbl_title)

        # ===== 第三行：正文 =====
        if self.body_text:
            # ★ 修改 1：直接使用完整的原始文本，不做任何截断
            lbl_body = QLabel(self.body_text)
            lbl_body.setStyleSheet("color: #4B5563; font-size: 13px; border: none;")
            lbl_body.setWordWrap(True)
            lbl_body.setToolTip(self.body_text)  # 鼠标悬浮依然可以看全部
            lbl_body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

            # ★ 修改 2：固定高度为 40（刚好容纳两行字）
            lbl_body.setFixedHeight(40)
            # ★ 修改 3：强制文字从顶部开始对齐
            # 这样如果文字很多排成了三行，第三行会超出 40 的高度，直接被裁掉不显示
            # 并且保证绝对不会有任何一行挤压重叠
            lbl_body.setAlignment(Qt.AlignTop | Qt.AlignLeft)

            layout.addWidget(lbl_body)
        else:
            lbl_empty_body = QLabel("")
            lbl_empty_body.setFixedHeight(40)
            layout.addWidget(lbl_empty_body)

        # ===== 第四行：链接 =====
        display_text = self.display_url
        if len(display_text) > 65:
            display_text = display_text[:62] + "..."
        lbl_url = QLabel(display_text)
        lbl_url.setStyleSheet("color: #2563EB; font-size: 12px; border: none;")
        lbl_url.setToolTip("双击跳转预览：" + self.raw_url)
        lbl_url.setCursor(Qt.PointingHandCursor)
        lbl_url.setFixedHeight(20)
        lbl_url.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lbl_url.mouseDoubleClickEvent = lambda e: self.parent_panel._preview_card_url(
            self.db_id, self.raw_url, self.original_category, self.book_name, self.is_mobile
        )
        layout.addWidget(lbl_url)

        # ===== 第五行：底部操作区 =====
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        src_text = self.source_site if self.source_site else "未知来源"
        lbl_src = QLabel(src_text)
        if len(src_text) > 12:
            lbl_src.setText(src_text[:12] + "...")
        lbl_src.setStyleSheet("color: #9CA3AF; font-size: 12px; border: none;")
        lbl_src.setToolTip(f"来源: {src_text}")
        lbl_src.setMaximumWidth(140)
        bottom_row.addWidget(lbl_src)

        bottom_row.addStretch()

        if self.mode == "actionable":
            self.combo_book = NoWheelComboBox()
            self.combo_book.setStyleSheet(
                "QComboBox { padding: 2px 5px; border: 1px solid #DCDCDC; border-radius: 4px; font-size: 12px; background: #FFF; }")
            self.combo_book.setMaximumWidth(140)

            books = [b["name"] for b in self.parent_panel.books_data.values()]
            if self.book_name and self.book_name not in books:
                self.combo_book.addItem(self.book_name)
            self.combo_book.addItems(books)

            if self.book_name:
                self.combo_book.setCurrentText(self.book_name)
            elif books:
                self.combo_book.setCurrentIndex(0)

            self.combo_book.currentTextChanged.connect(self._on_book_changed)
            bottom_row.addWidget(self.combo_book)

            self.combo_cat = NoWheelComboBox()
            self.combo_cat.setStyleSheet(
                "QComboBox { padding: 2px 5px; border: 1px solid #DCDCDC; border-radius: 4px; font-size: 12px; background: #FFF; font-weight: bold; color: #059669; }")
            self.combo_cat.setMaximumWidth(120)
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
            bottom_row.addWidget(self.combo_cat)

            btn_ok = QPushButton("入库")
            btn_ok.setStyleSheet(
                "background: #ECFDF5; color: #059669; border: 1px solid #A7F3D0; border-radius: 4px; padding: 3px 10px; font-size: 12px;")
            btn_ok.setCursor(Qt.PointingHandCursor)
            # ★ 修复：把 self 传给 parent_panel._card_action
            btn_ok.clicked.connect(
                lambda: self.parent_panel._card_action(self, self.db_id, self.combo_cat.currentData(), "keep"))

            btn_black = QPushButton("永删")
            btn_black.setStyleSheet(
                "background: #FEF2F2; color: #DC2626; border: 1px solid #FECACA; border-radius: 4px; padding: 3px 10px; font-size: 12px;")
            btn_black.setCursor(Qt.PointingHandCursor)
            btn_black.clicked.connect(lambda: self.parent_panel._card_action(self, self.db_id, "", "black"))

            btn_drop = QPushButton("忽略")
            btn_drop.setStyleSheet(
                "background: #F9FAFB; color: #6B7280; border: 1px solid #E5E7EB; border-radius: 4px; padding: 3px 10px; font-size: 12px;")
            btn_drop.setCursor(Qt.PointingHandCursor)
            btn_drop.clicked.connect(lambda: self.parent_panel._card_action(self, self.db_id, "", "drop"))

            bottom_row.addWidget(btn_ok)
            bottom_row.addWidget(btn_black)
            bottom_row.addWidget(btn_drop)
        else:
            lbl_cat = QLabel(f"分类: {self.original_category or '无'}")
            lbl_cat.setStyleSheet("color: #4B5563; font-weight: bold; border: none;")
            bottom_row.addWidget(lbl_cat)

        layout.addLayout(bottom_row)

    # ★ 新增：平滑折叠消失动画
    def smooth_disappear(self):
        # 1. 锁死当前高度
        self.setMinimumHeight(0)
        self.setMaximumHeight(self.height())

        # （★ 删除了隐藏子控件的逻辑，让内容随高度自然裁剪）

        # 2. 高度从 185 缩到 0
        self.anim_height = QPropertyAnimation(self, b"maximumHeight")
        self.anim_height.setDuration(300)
        self.anim_height.setStartValue(self.height())
        self.anim_height.setEndValue(0)
        self.anim_height.setEasingCurve(QEasingCurve.OutQuad)

        # 3. 透明度从 1 到 0
        if not self.graphicsEffect():
            self.opacity_effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(self.opacity_effect)
        else:
            self.opacity_effect = self.graphicsEffect()

        self.anim_opacity = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim_opacity.setDuration(300)
        self.anim_opacity.setStartValue(1.0)
        self.anim_opacity.setEndValue(0.0)

        # 4. 并行执行
        self.anim_group = QParallelAnimationGroup(self)
        self.anim_group.addAnimation(self.anim_height)
        self.anim_group.addAnimation(self.anim_opacity)

        # 5. 动画结束销毁自己
        self.anim_group.finished.connect(self.deleteLater)
        self.anim_group.start()

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
            anim_height.setEndValue(185)
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
        try:
            latest = find_latest_draft_batch()
            if latest:
                self.current_batch_id = latest
        except Exception:
            pass
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
            # 注意这里：选中时有 1px 的白色底边，用来和下方的白底无缝拼接
            "QTabBar::tab:selected { background: white; color: #059669; border-top: 3px solid #059669; border-bottom: 1px solid white; }"
        )
        self.tab_bar.addTab("⚡ 待处理")
        self.tab_bar.addTab("⏳ 处理中")
        self.tab_bar.addTab("🗄️ 已归档")
        right_layout.addWidget(self.tab_bar)

        # --- 3. 内部白底板（包裹标签、列表和底部操作栏） ---
        self.content_frame = QFrame()
        self.content_frame.setObjectName("ContentFrame")
        # ★ 核心：上边框去掉，上圆角为 0，完美贴合上面的 Tab 栏
        self.content_frame.setStyleSheet(
            "#ContentFrame { border: 1px solid #E5E7EB; border-top: none; border-radius: 0px 0px 8px 8px; background: white; }")

        content_layout = QVBoxLayout(self.content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # --- 4. 标签过滤行 ---
        self.stats_layout = QHBoxLayout()
        self.stats_layout.setSpacing(10)
        self.stats_layout.setContentsMargins(10, 10, 10, 5)
        content_layout.addLayout(self.stats_layout)

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

        self.btn_del_sel = QPushButton("清空当前分类")
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

    def _on_parse_done(self, success):
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
            if not self.current_batch_id:
                latest = find_latest_draft_batch()
                if latest: self.current_batch_id = latest

            self._cached_tables = get_batch_tables(self.current_batch_id)

            stats = self._cached_tables.get("stats", {})
            total_todo = stats.get("明确资源", 0) + stats.get("未知网站", 0) + stats.get("关键词未命中", 0)
            total_pending = stats.get("待解析", 0) + stats.get("解析失败", 0)
            total_archive = stats.get("已在库", 0) + stats.get("白名单", 0) + stats.get("已永删", 0)

            self.tab_bar.setTabText(0, f"⚡ 待处理 ({total_todo})")
            self.tab_bar.setTabText(1, f"⏳ 处理中 ({total_pending})")
            self.tab_bar.setTabText(2, f"🗄️ 已归档 ({total_archive})")

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

        tooltip_map = {"明确资源": "建议入库", "未知网站": "待分类", "关键词未命中": "建议永删"}

        if self.tab_bar.currentIndex() == 0:
            tags = [
                ("全部", None, "#F3F4F6", "#374151"),
                ("明确资源", "明确资源", "#ECFDF5", "#065F46"),
                ("未知网站", "未知网站", "#FEF3C7", "#92400E"),
                ("关键词未命中", "关键词未命中", "#F3F4F6", "#374151"),
                ("手动拾取", "手动拾取", "#FDF4FF", "#C026D3"),
            ]
            total_count = stats.get("明确资源", 0) + stats.get("未知网站", 0) + stats.get("关键词未命中", 0)
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
            archive_batch_by_ids([(db_id, category)], [], [])
            ToastLabel(self, "✅ 已成功入库", "#10B981")
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
        current_index = self.tab_bar.currentIndex()
        current_list = [self.list_todo, self.list_pending, self.list_archive][current_index]
        if not current_list.all_records:
            QMessageBox.information(self, "提示", "当前分类下没有数据。")
            return
        reply = QMessageBox.question(
            self, "确认清空",
            f"当前分类共有 {len(current_list.all_records)} 条草稿，确定全部删除吗？\n\n"
            f"（此操作仅从草稿箱移除，不影响已入库的资源和黑名单）",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        ids_to_delete = [r[0] for r in current_list.all_records]
        if ids_to_delete:
            clear_draft_category(ids_to_delete)
            self.refresh_tables()

    def show_list_manager(self):
        # ★ 看黑白名单的功能，被误删了，暂时不处理，
        # 因为 page_search.py 里根本没有 ListDialog，直接在这里临时造一个，防止崩溃
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton

        class ListDialog(QDialog):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.setWindowTitle("列表管理")
                self.resize(400, 300)
                layout = QVBoxLayout(self)
                layout.addWidget(QLabel("这里原本应该是 永删/白名单 列表。\n（由于原代码丢失，目前是占位提示）"))
                btn = QPushButton("关闭")
                btn.clicked.connect(self.accept)
                layout.addWidget(btn)

        dlg = ListDialog(self)
        dlg.exec()