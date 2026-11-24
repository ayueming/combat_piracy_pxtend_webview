# ui_card_base.py
"""卡片基类：只负责展示外壳 + 高亮 + 折叠动画。

子类通过覆写 build_action_widgets() 决定底部右侧的操作区。
"""
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
    QGraphicsOpacityEffect
)
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup

from app_data.crawl_status_consts import (
    CRAWL_STATUS_PENDING, CRAWL_STATUS_UNMATCHED, CRAWL_STATUS_DONE,
    CRAWL_STATUS_ORGANIZED, CRAWL_STATUS_WHITELIST, CRAWL_STATUS_IGNORED,
    CRAWL_STATUS_DUPLICATE, CRAWL_STATUS_PARSE_FAILED
)


CARD_STYLES = {
    "明确资源": {"color": "#10B981", "bg": "#ECFDF5", "text": "#065F46", "border": "4px solid #10B981"},
    "未知网站": {"color": "#F59E0B", "bg": "#FEF3C7", "text": "#92400E", "border": "4px solid #F59E0B"},
    "未命中关键词": {"color": "#9CA3AF", "bg": "#F3F4F6", "text": "#374151", "border": "4px solid #9CA3AF"},
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
        return "未命中关键词"
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


class BaseCard(QFrame):
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

        # ★ objectName 保持 "DataCard"，QSS 里的 #DataCard 选择器对两种卡片通用
        self.setObjectName("DataCard")
        self.setCursor(Qt.ArrowCursor)
        # 默认 185，如果子类有多行操作区，子类在自己的 __init__ 里覆写 setFixedHeight(230)
        self.setFixedHeight(185)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        is_active = False
        if hasattr(parent_panel.main_page, "active_preview_db_id"):
            is_active = (parent_panel.main_page.active_preview_db_id == self.db_id)
        self.set_highlight(is_active)

        self.display_url = "⏳ 等待解析真实链接..." if "baidu.com/link?url=" in self.raw_url else self.raw_url

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(6)

        # ===== 第一行：标签、时间 =====
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        lbl_status = QLabel(self.status)
        lbl_status.setStyleSheet(
            f"background: {style['bg']}; color: {style['text']};"
            f"padding: 0px 4px; margin: 0px; "
            f"border-radius: 3px; font-size: 12px; font-weight: bold;"
        )
        lbl_status.setFixedHeight(22)
        lbl_status.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        top_row.addWidget(lbl_status)

        if self.is_manual:
            lbl_manual = QLabel("手动拾取")
            lbl_manual.setStyleSheet(
                "background: #FDF4FF; color: #C026D3; border: 1px solid #F0ABFC; "
                "padding: 0px 4px; margin: 0px; "
                "border-radius: 3px; font-size: 12px; font-weight: bold;"
            )
            lbl_manual.setFixedHeight(22)
            lbl_manual.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            top_row.addWidget(lbl_manual)

        if self.has_pan:
            lbl_pan = QLabel("含网盘")
            lbl_pan.setStyleSheet(
                "background: #FEF2F2; color: #DC2626; border: 1px solid #FECACA; "
                "padding: 0px 4px; margin: 0px; "
                "border-radius: 3px; font-size: 12px; font-weight: bold;"
            )
            lbl_pan.setFixedHeight(22)
            lbl_pan.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            top_row.addWidget(lbl_pan)

        top_row.addStretch()

        # 把控件（关联作品 / 分类下拉框）挂到 stretch 之后
        right_widgets = self.build_top_right_widgets()
        if right_widgets:
            for w in right_widgets:
                top_row.addWidget(w)

        layout.addLayout(top_row)
        layout.addSpacing(7)

        # ===== 第二行：标题 =====
        lbl_title = QLabel(self.title)
        lbl_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #111827; border: none;")
        lbl_title.setWordWrap(False)
        lbl_title.setToolTip(self.title)
        lbl_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lbl_title.setFixedHeight(24)
        layout.addWidget(lbl_title)

        # ===== 第三行：正文 =====
        if self.body_text:
            lbl_body = QLabel(self.body_text)
            lbl_body.setStyleSheet("color: #4B5563; font-size: 13px; border: none;")
            lbl_body.setWordWrap(True)
            lbl_body.setToolTip(self.body_text)
            lbl_body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lbl_body.setFixedHeight(40)
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

        # ===== 第五行：底部 =====
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        bottom_row.setAlignment(Qt.AlignBottom)

        # 1. 来源标签
        src_text = self.source_site if self.source_site else "未知来源"
        lbl_src = QLabel(src_text[:12] + "..." if len(src_text) > 12 else src_text)
        lbl_src.setStyleSheet("color: #9CA3AF; font-size: 12px; border: none;")
        lbl_src.setToolTip(f"来源: {src_text}")
        lbl_src.setMaximumWidth(140)
        lbl_src.setFixedHeight(20)
        lbl_src.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        bottom_row.addWidget(lbl_src)

        # ★ 删除时间标签，及其前面的 15px 间距

        # 2. 弹簧（把操作区推到最右侧）
        bottom_row.addStretch()

        # ★ 新增：操作按钮左侧的控件（子类挂载，如分类下拉框）
        left_widgets = self.build_action_left_widgets()
        if left_widgets:
            for w in left_widgets:
                bottom_row.addWidget(w)

        # 3. 右侧操作区（子类挂载的按钮）
        action_layout = self.build_action_widgets()
        if action_layout is not None:
            bottom_row.addLayout(action_layout)

        layout.addLayout(bottom_row)


    def build_top_right_widgets(self):
        """子类覆写：返回要放在右上角的控件列表（如关联作品 / 分类下拉框）。"""
        return None

    def build_action_left_widgets(self):
        """子类覆写：返回要放在底部操作按钮左侧的控件列表。"""
        return None

    def build_action_widgets(self):
        """子类覆写：返回 QHBoxLayout 或 None。"""
        return None

    def set_highlight(self, is_active):
        style = CARD_STYLES.get(self.status, CARD_STYLES["未知网站"])
        self.setProperty("active", "true" if is_active else "false")
        self.setStyleSheet(
            f"#DataCard {{"
            f"  background-color: #FFFFFF;"
            f"  border: 2px solid #E5E7EB;"
            f"  border-left: {style['border']};"
            f"  border-radius: 6px;"
            f"}}"
            f"#DataCard:hover {{"
            f"  border: 2px solid #93C5FD;"
            f"  border-left: {style['border']};"
            f"}}"
            f"#DataCard[active=\"true\"] {{"
            f"  background-color: #EFF6FF;"
            f"}}"
        )
        self.style().unpolish(self)
        self.style().polish(self)

    def smooth_disappear(self):
        self.setMinimumHeight(0)
        self.setMaximumHeight(self.height())

        self.anim_height = QPropertyAnimation(self, b"maximumHeight")
        self.anim_height.setDuration(300)
        self.anim_height.setStartValue(self.height())
        self.anim_height.setEndValue(0)
        self.anim_height.setEasingCurve(QEasingCurve.OutQuad)

        if not self.graphicsEffect():
            self.opacity_effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(self.opacity_effect)
        else:
            self.opacity_effect = self.graphicsEffect()

        self.anim_opacity = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim_opacity.setDuration(300)
        self.anim_opacity.setStartValue(1.0)
        self.anim_opacity.setEndValue(0.0)

        self.anim_group = QParallelAnimationGroup(self)
        self.anim_group.addAnimation(self.anim_height)
        self.anim_group.addAnimation(self.anim_opacity)
        self.anim_group.finished.connect(self.deleteLater)
        self.anim_group.start()