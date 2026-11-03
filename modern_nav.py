# modern_nav.py
from PySide6.QtWidgets import (QWidget, QPushButton, QVBoxLayout, QFrame)
from PySide6.QtCore import Qt, QPoint, QTimer, QEvent, Signal


class HoverMenuPopup(QWidget):
    itemClicked = Signal(str)

    def __init__(self, platforms, target_width, parent=None):
        super().__init__(parent)
        # 无边框、工具窗口、置顶
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)

        # ★ 核心控制：强制固定宽度，与主按钮一模一样
        self.setFixedWidth(target_width)

        self.container = QFrame(self)
        self.container.setStyleSheet("""
            QFrame {
                background-color: white;
                border: 1px solid #DCDCDC;
                border-radius: 6px;
            }
        """)

        # 外层布局无边距，让 container 充满整个弹窗
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.container)

        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(4, 6, 4, 6)  # 上下留点空隙，左右留一点点
        container_layout.setSpacing(2)

        for pf in platforms:
            btn = QPushButton(pf)
            btn.setCursor(Qt.PointingHandCursor)
            # ★ 文本居中对齐，字号稍微改小一点(12px)防止5个字挤不下
            btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent; color: #374151; border: none;
                    border-radius: 4px; padding: 8px 0px; text-align: center;
                    font-size: 12px;
                }
                QPushButton:hover { background-color: #F3F4F6; color: #111827; }
                QPushButton:pressed { background-color: #E5E7EB; }
            """)
            btn.clicked.connect(lambda checked, text=pf: self._on_item_click(text))
            container_layout.addWidget(btn)

    def _on_item_click(self, text):
        self.itemClicked.emit(text)
        self.hide()

    def enterEvent(self, event):
        self.parent().on_popup_enter()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.parent().on_popup_leave()
        super().leaveEvent(event)


class HoverMenuButton(QPushButton):
    subItemSelected = Signal(str)

    def __init__(self, text, platforms, parent=None):
        super().__init__(text, parent)
        self.platforms = platforms
        self.setCursor(Qt.PointingHandCursor)

        # ★ 主按钮宽度固定为 86px（大概能容纳5个中文字+padding），保证排列整齐
        self.setFixedWidth(86)
        self.setStyleSheet("""
            QPushButton {
                background-color: transparent; border: none; color: #4B5563;
                font-size: 14px; padding: 8px 0px; border-radius: 6px;
            }
            QPushButton:hover { background-color: #F3F4F6; color: #111827; }
        """)

        if not self.platforms:
            return

        self.popup = None
        self.show_timer = QTimer(self)
        self.show_timer.setSingleShot(True)
        self.show_timer.timeout.connect(self._show_popup)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._hide_popup)

        self.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self:
            if event.type() == QEvent.Enter:
                self.hide_timer.stop()
                if not self.popup or not self.popup.isVisible():
                    self.show_timer.start(150)
            elif event.type() == QEvent.Leave:
                self.show_timer.stop()
                self.hide_timer.start(200)
        return super().eventFilter(obj, event)

    def on_popup_enter(self):
        self.hide_timer.stop()

    def on_popup_leave(self):
        self.hide_timer.start(150)

    def _show_popup(self):
        if not self.popup:
            # 传入固定宽度 self.width()
            self.popup = HoverMenuPopup(self.platforms, self.width(), self)
            self.popup.itemClicked.connect(self.subItemSelected.emit)

        if not self.popup.isVisible():
            # 坐标计算：左边缘对齐(0)，紧贴按钮底部
            global_pos = self.mapToGlobal(QPoint(0, self.height() + 2))
            self.popup.move(global_pos.x(), global_pos.y())
            self.popup.show()

    def _hide_popup(self):
        if self.popup and self.popup.isVisible():
            self.popup.hide()