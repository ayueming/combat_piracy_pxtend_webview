# list_manager_dialog.py
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTableWidget, QTableWidgetItem, QPushButton, QHeaderView, QMessageBox, QLabel, QWidget,
    QStyledItemDelegate, QStyle  # ★ 补上这两个
)
from PySide6.QtCore import Qt, QEvent  # ★ 补上 QEvent
from PySide6.QtGui import QPainter, QColor  # ★ 补上 QPainter 和 QColor

from db_config import get_conn
from app_data.copyright_whitelist_consts import COPYRIGHT_WHITELIST_DOMAINS, COPYRIGHT_WHITELIST_KEYWORDS
from ui_components import draw_checkbox, ROW_HOVER_BG


class DialogCheckDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        # 鼠标悬浮时的底色
        if option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, QColor(ROW_HOVER_BG))

        # 只根据真实的 CheckState 来画，不被选中状态干扰
        checked = (index.data(Qt.CheckStateRole) == Qt.Checked)
        draw_checkbox(painter, option.rect, checked)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        # 拦截鼠标左键释放事件，主动切换勾选状态
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            state = index.data(Qt.CheckStateRole)
            new_state = Qt.Unchecked if state == Qt.Checked else Qt.Checked
            model.setData(index, new_state, Qt.CheckStateRole)
            return True
        return super().editorEvent(event, model, option, index)


class ListManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("永删 / 白名单管理")
        self.resize(800, 600)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #E5E7EB; background: white; border-radius: 4px; }
            QTabBar::tab { background: #F3F4F6; padding: 8px 16px; border: 1px solid #E5E7EB; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; color: #4B5563; font-weight: bold; }
            QTabBar::tab:selected { background: white; color: #059669; border-top: 2px solid #059669; }
        """)

        # 1. 黑名单 Tab
        self.tab_black = QWidget()
        self.setup_black_tab()
        self.tab_widget.addTab(self.tab_black, "🚫 黑名单 (已永删)")

        # 2. 白名单 Tab
        self.tab_white = QWidget()
        self.setup_white_tab()
        self.tab_widget.addTab(self.tab_white, "✅ 白名单 (正版保护)")

        layout.addWidget(self.tab_widget)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.setStyleSheet(
            "padding: 6px 20px; border: 1px solid #D1D5DB; border-radius: 4px; background: #F9FAFB; color: #374151;")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def setup_black_tab(self):
        layout = QVBoxLayout(self.tab_black)
        layout.setContentsMargins(10, 10, 10, 10)

        # 操作栏
        top_bar = QHBoxLayout()
        lbl_tip = QLabel("这里是被手动标记为“永删”的链接。勾选后点击右侧按钮可批量移除。")
        lbl_tip.setStyleSheet("color: #6B7280; font-size: 12px;")
        top_bar.addWidget(lbl_tip)
        top_bar.addStretch()

        btn_remove = QPushButton("❌ 移除选中项")
        btn_remove.setStyleSheet(
            "background: #FEF2F2; color: #DC2626; border: 1px solid #FECACA; border-radius: 4px; padding: 6px 12px; font-weight: bold;")
        btn_remove.setCursor(Qt.PointingHandCursor)
        btn_remove.clicked.connect(self.remove_from_black)
        top_bar.addWidget(btn_remove)

        layout.addLayout(top_bar)

        # 表格
        self.table_black = QTableWidget()
        self.table_black.setColumnCount(5)
        self.table_black.setHorizontalHeaderLabels(["序号", "选择", "标题", "URL", "加入时间"])

        # 列宽分配：标题自适应拉伸，其他可拖动
        header = self.table_black.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)  # 序号固定
        header.setSectionResizeMode(1, QHeaderView.Fixed)  # 选择固定
        header.setSectionResizeMode(2, QHeaderView.Stretch)  # 标题拉伸
        header.setSectionResizeMode(3, QHeaderView.Interactive)  # URL可拉
        header.setSectionResizeMode(4, QHeaderView.Fixed)  # 时间固定

        self.table_black.setColumnWidth(0, 50)
        self.table_black.setColumnWidth(1, 40)
        self.table_black.setColumnWidth(3, 200)
        self.table_black.setColumnWidth(4, 140)

        self.table_black.verticalHeader().setVisible(False)
        self.table_black.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_black.setSelectionMode(QTableWidget.NoSelection)  # 禁用原生选中，用Check框
        self.table_black.setEditTriggers(QTableWidget.NoEditTriggers)

        self.table_black.setItemDelegateForColumn(1, DialogCheckDelegate(self.table_black))

        # 统一表头样式
        self.table_black.setStyleSheet("""
            QTableWidget { border: 1px solid #E5E7EB; gridline-color: #F3F4F6; } 
            QHeaderView::section { 
                background: #F9FAFB; padding: 6px; 
                border: none; border-bottom: 1px solid #E5E7EB; border-right: 1px solid #F3F4F6;
                font-weight: bold; color: #374151;
            }
        """)
        layout.addWidget(self.table_black)

    def setup_white_tab(self):
        layout = QVBoxLayout(self.tab_white)
        layout.setContentsMargins(10, 10, 10, 10)

        lbl_tip = QLabel(
            "这是系统内置的版权方/正版网站白名单，命中这些域名的链接将被自动拦截（标记为白名单）。\n（如需修改，请联系开发人员修改 copyright_whitelist_consts.py）")
        lbl_tip.setStyleSheet("color: #6B7280; font-size: 12px; margin-bottom: 10px;")
        layout.addWidget(lbl_tip)

        self.table_white = QTableWidget()
        self.table_white.setColumnCount(2)
        self.table_white.setHorizontalHeaderLabels(["类型", "域名 / 关键词"])
        self.table_white.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_white.verticalHeader().setVisible(False)
        self.table_white.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_white.setStyleSheet(
            "QTableWidget { border: 1px solid #E5E7EB; gridline-color: #F3F4F6; } QHeaderView::section { background: #F9FAFB; padding: 4px; border: none; border-bottom: 1px solid #E5E7EB; }")

        # 填充常量
        all_white_items = []
        for d in COPYRIGHT_WHITELIST_DOMAINS:
            all_white_items.append(("域名", d))
        for kw in COPYRIGHT_WHITELIST_KEYWORDS:
            all_white_items.append(("关键词", kw))

        self.table_white.setRowCount(len(all_white_items))
        for i, (t, v) in enumerate(all_white_items):
            self.table_white.setItem(i, 0, QTableWidgetItem(t))
            self.table_white.setItem(i, 1, QTableWidgetItem(v))

        layout.addWidget(self.table_white)

    def load_data(self):
        self.load_black_list()

    def load_black_list(self):
        conn = get_conn()
        try:
            rows = conn.execute("SELECT id, site_url, title, add_time FROM ignored_links ORDER BY id DESC").fetchall()
            self.table_black.setRowCount(len(rows))
            for i, r in enumerate(rows):
                # 1. 序号
                item_idx = QTableWidgetItem(str(i + 1))
                item_idx.setTextAlignment(Qt.AlignCenter)
                self.table_black.setItem(i, 0, item_idx)

                # 2. Check 框（将真实数据库 ID 藏在 UserRole 中）
                item_check = QTableWidgetItem()
                item_check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                item_check.setCheckState(Qt.Unchecked)
                item_check.setData(Qt.UserRole, r[0])
                self.table_black.setItem(i, 1, item_check)

                # 3. 标题
                item_title = QTableWidgetItem(r[2] or "无标题")
                item_title.setToolTip(r[2] or "无标题")
                self.table_black.setItem(i, 2, item_title)

                # 4. URL
                url = r[1] or ""
                item_url = QTableWidgetItem(url)
                item_url.setToolTip(url)
                self.table_black.setItem(i, 3, item_url)

                # 5. 加入时间
                item_time = QTableWidgetItem(r[3] or "")
                item_time.setTextAlignment(Qt.AlignCenter)
                self.table_black.setItem(i, 4, item_time)

        except Exception as e:
            print(f"加载黑名单失败: {e}")
        finally:
            conn.close()

    def remove_from_black(self):
        ids_to_remove = []
        for i in range(self.table_black.rowCount()):
            item_check = self.table_black.item(i, 1)
            if item_check.checkState() == Qt.Checked:
                ids_to_remove.append(item_check.data(Qt.UserRole))

        if not ids_to_remove:
            QMessageBox.information(self, "提示", "请先勾选需要移除的记录。")
            return

        reply = QMessageBox.question(
            self, "确认移除",
            f"确定要将勾选的 {len(ids_to_remove)} 条记录从黑名单中移除吗？\n\n移除后，下次搜索到这些链接将不再拦截。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        conn = get_conn()
        try:
            placeholders = ",".join("?" * len(ids_to_remove))
            conn.execute(f"DELETE FROM ignored_links WHERE id IN ({placeholders})", ids_to_remove)
            conn.commit()
            QMessageBox.information(self, "成功", f"已从黑名单移除 {len(ids_to_remove)} 条记录。")
            self.load_black_list()  # 刷新列表
        except Exception as e:
            QMessageBox.warning(self, "错误", f"移除失败: {e}")
        finally:
            conn.close()