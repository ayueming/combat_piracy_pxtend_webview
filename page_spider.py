# page_spider.py
# ================================================================
# 待抓取页数据来源说明
# ----------------------------------------------------------------
# 本页显示的 sites 数据 = category 属于 MODULE_SEARCH 的记录：
#   当前 = "百度搜索" / "夸克搜索" / "uc搜索"
#
# 其他 module 不进入本页：
#   - MODULE_NETDISK：网盘链接，无需抓章节
#   - MODULE_FORUM  ：未来论坛 tab 处理
#   - MODULE_PANSITE：有内部搜索逻辑
#   - MODULE_OTHER  ：垃圾/兜底，有意不兜底
#
# 新增搜索引擎：只在 complaint_platform_consts.PLATFORM_CONFIG
# 登记 MODULE_SEARCH，本页自动生效，无需改本文件。
# ================================================================
import sqlite3

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
                               QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView,
                               QPushButton, QApplication, QComboBox, QLineEdit, QDialog,
                               QTableView, QFrame, QStyledItemDelegate, QStyle, QMenu,
                               QListWidget, QListWidgetItem, QStylePainter, QStyleOptionComboBox,
                               QSizePolicy)  # ★ 修复：补上 QSizePolicy
from PySide6.QtCore import (Qt, QUrl, QPoint, QRect, QEvent,
                            QItemSelection, QItemSelectionModel, QTimer)
from PySide6.QtGui import (QColor, QFont, QStandardItemModel, QStandardItem,
                           QPainter, QPen, QPainterPath, QPolygon, QCursor)

from app_data.complaint_platform_consts import PLATFORM_CONFIG, MODULE_SEARCH
from app_data import ALL_SUB_PLATFORMS

from spider_crawler import crawl_sites
from feature_extractor import extract_pure_feature
from db_config import get_conn

from picker_web_view import PickerWebView
from app_data.crawl_status_consts import CRAWL_STATUS_ORGANIZED


from ui_components import (
    ExcelFilterProxyModel, FilterHeaderView, CenteredCheckBoxDelegate,
    CheckListDelegate,
    draw_checkbox,
    CHECK_BG_COLOR, CHECK_MARK_COLOR,
    HEADER_BG_COLOR, HEADER_FILTERED_BG_COLOR,
    HEADER_TRIANGLE_COLOR, HEADER_TRIANGLE_ACTIVE_COLOR,
)

from ui_styles import ROW_SELECTED_BG, TABLE_HEADER_BASE, BORDER_LIGHT
# ★ 引入权限管理器
from license_manager import license_mgr


SEARCH_MODULE_CATEGORIES = [
    display_name
    for sub_list in PLATFORM_CONFIG.values()
    for display_name, _p, m_code, _w, _a in sub_list
    if m_code == MODULE_SEARCH
]


# ================= Spider 专属：状态下拉框与代理 =================
class SpiderNoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)

        if option.currentText == "（空白）":
            option.currentText = ""

        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        painter.drawControl(QStyle.CE_ComboBoxLabel, option)


class SpiderStatusComboDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        table = option.widget
        if isinstance(table, QTableWidget):
            chk_item = table.item(index.row(), 0)
            if chk_item:
                data = chk_item.data(Qt.UserRole)
                if data and isinstance(data, dict) and data.get("comp_time", ""):
                    return None  # 有投诉时间，禁止编辑

        combo = SpiderNoWheelComboBox(parent)
        combo.setStyleSheet("QComboBox { border: 1px solid #ccc; padding-left: 5px; background: #fff; }")
        combo.setFocusPolicy(Qt.StrongFocus)

        combo.addItem("（空白）", "")
        combo.addItem("已失效", "已失效")

        QTimer.singleShot(10, combo.showPopup)
        return combo

    def setEditorData(self, editor, index):
        if not editor:
            return
        text = index.data(Qt.DisplayRole)
        if not text or str(text).strip() == "":
            text = "（空白）"
        idx = editor.findText(text)
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        if not editor:
            return
        val = editor.currentData()
        if val is None:
            val = ""
        model.setData(index, val, Qt.EditRole)

        table = editor.parent()
        while table and not isinstance(table, QTableWidget):
            table = table.parent()

        if table:
            chk_item = table.item(index.row(), 0)
            if chk_item:
                data = chk_item.data(Qt.UserRole)
                if data and isinstance(data, dict):
                    db_id = data.get("id")
                    if db_id:
                        conn = get_conn()
                        conn.execute("UPDATE sites SET resource_status=? WHERE id=?", (val, db_id))
                        conn.commit()
                        conn.close()

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(ROW_SELECTED_BG))
        else:
            painter.fillRect(option.rect, QColor("#FFFFFF"))

        val = index.data(Qt.DisplayRole)
        text = str(val).strip() if val else ""

        if text == "已失效":
            painter.setPen(QColor("#10B981"))
            font = option.font
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(option.rect.adjusted(5, 0, -20, 0), Qt.AlignLeft | Qt.AlignVCenter, text)
        elif text:
            painter.setPen(QColor("#333333"))
            painter.drawText(option.rect.adjusted(5, 0, -20, 0), Qt.AlignLeft | Qt.AlignVCenter, text)

        table = option.widget
        chk_item = None
        if isinstance(table, QTableWidget):
            chk_item = table.item(index.row(), 0)

        comp_time = ""
        if chk_item:
            data = chk_item.data(Qt.UserRole)
            if data and isinstance(data, dict):
                comp_time = data.get("comp_time", "")

        if not comp_time:
            x = option.rect.right() - 16
            y = option.rect.center().y() - 2
            points = [QPoint(x, y), QPoint(x + 8, y), QPoint(x + 4, y + 5)]
            painter.setBrush(QColor("#999999"))
            painter.setPen(Qt.NoPen)
            painter.drawPolygon(QPolygon(points))

        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            table = option.widget
            chk_item = None
            if isinstance(table, QTableWidget):
                chk_item = table.item(index.row(), 0)

            if chk_item:
                data = chk_item.data(Qt.UserRole)
                if data and isinstance(data, dict) and data.get("comp_time", ""):
                    return False
            return True
        return super().editorEvent(event, model, option, index)


# ================= 名称筛选弹窗 =================
class NameFilterPopup(QFrame):
    def __init__(self, parent_table, current_filtered, on_apply):
        super().__init__(parent_table, Qt.Popup)
        self.parent_table = parent_table
        self.on_apply = on_apply
        self.filtered_names = list(current_filtered)

        self.setFixedSize(280, 420)
        self.setStyleSheet(
            "QFrame { background-color: #FFFFFF; border: 1px solid #CCCCCC; border-radius: 4px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        top_layout = QHBoxLayout()
        top_layout.setSpacing(2)

        self.btn_select_all = QPushButton("全选")
        btn_invert = QPushButton("反选")
        btn_clear = QPushButton("清空")

        for btn in (self.btn_select_all, btn_invert, btn_clear):
            btn.setStyleSheet(
                "QPushButton { border: none; color: #333; font-size: 12px; "
                "padding: 2px 6px; background: transparent; }"
                "QPushButton:hover { color: #10B981; }"
            )

        self.btn_select_all.clicked.connect(self.select_all)
        btn_invert.clicked.connect(self.invert)
        btn_clear.clicked.connect(self.clear_all)

        top_layout.addWidget(self.btn_select_all)
        top_layout.addWidget(btn_invert)
        top_layout.addWidget(btn_clear)
        top_layout.addStretch()
        layout.addLayout(top_layout)

        self.list_widget = QListWidget()
        self.list_widget.setMouseTracking(True)
        self.list_widget.setSelectionMode(QListWidget.NoSelection)
        self.list_widget.setFocusPolicy(Qt.NoFocus)
        self.list_widget.setItemDelegate(CheckListDelegate(self, self.list_widget))
        self.list_widget.setStyleSheet(
            "QListWidget { border: none; outline: none; background: transparent; }"
        )
        layout.addWidget(self.list_widget)

        names = set()
        for row in range(parent_table.rowCount()):
            it = parent_table.item(row, 4)
            if it and it.text().strip():
                names.add(it.text().strip())
        for name in sorted(names):
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked if name in self.filtered_names else Qt.Checked)
            self.list_widget.addItem(item)
        self.update_select_all_btn_state()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet(
            "padding: 5px 15px; border: 1px solid #ccc; border-radius: 4px; background-color: #fff;"
        )
        btn_cancel.clicked.connect(self.close)

        btn_ok = QPushButton("确定")
        btn_ok.setStyleSheet(
            "background-color: #10B981; color: white; border-radius: 4px; "
            "padding: 5px 20px; font-weight: bold;"
        )
        btn_ok.clicked.connect(self.apply_filter)

        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def update_select_all_btn_state(self):
        total = self.list_widget.count()
        if total == 0:
            self.btn_select_all.setText("全选")
            return
        checked = sum(1 for i in range(total)
                      if self.list_widget.item(i).checkState() == Qt.Checked)
        self.btn_select_all.setText(f"全选 ({checked}/{total})")

    def filter_only_this_row(self, row):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.Checked if i == row else Qt.Unchecked)
        self.update_select_all_btn_state()
        self.list_widget.viewport().update()

    def _set_all(self, state):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(state)
        self.update_select_all_btn_state()
        self.list_widget.viewport().update()

    def select_all(self):
        self._set_all(Qt.Checked)

    def clear_all(self):
        self._set_all(Qt.Unchecked)

    def invert(self):
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)
        self.update_select_all_btn_state()
        self.list_widget.viewport().update()

    def apply_filter(self):
        filtered = []
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.checkState() == Qt.Unchecked:
                filtered.append(it.text())
        self.on_apply(filtered)
        self.close()


# ================= 自定义表头（全选 + 名称筛选） =================
class SpiderHeaderView(QHeaderView):
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.active_popup = None
        self.filtered_names = []

        # ★ 记录按下坐标，用于区分拖拽和单击
        self._press_pos = None

    def paintSection(self, painter, rect, logical_index):
        if logical_index == -1:
            painter.fillRect(rect, QColor(HEADER_BG_COLOR))
            # ★ 补底边和右边线
            painter.setPen(QColor(BORDER_LIGHT))
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
            painter.drawLine(rect.topRight(), rect.bottomRight())
            return

        if logical_index == 0:
            painter.fillRect(rect, QColor(HEADER_BG_COLOR))

            # ★ 完整恢复的全选计算逻辑
            parent_table = self.parent()
            all_checked = True
            has_data = False
            if parent_table:
                for row in range(parent_table.rowCount()):
                    item = parent_table.item(row, 0)
                    if item and item.data(Qt.UserRole) is not None:
                        has_data = True
                        if item.checkState() != Qt.Checked:
                            all_checked = False
                            break
            if not has_data:
                all_checked = False

            draw_checkbox(painter, rect, all_checked)

            # ★ 补底边和右边线
            painter.setPen(QColor(BORDER_LIGHT))
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
            painter.drawLine(rect.topRight(), rect.bottomRight())
            return

        painter.save()
        if logical_index == 4 and self.filtered_names:
            painter.fillRect(rect, QColor(HEADER_FILTERED_BG_COLOR))
        else:
            painter.fillRect(rect, QColor(HEADER_BG_COLOR))
        super().paintSection(painter, rect, logical_index)
        painter.restore()

        if logical_index == 4:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor(
                HEADER_TRIANGLE_ACTIVE_COLOR if self.filtered_names else HEADER_TRIANGLE_COLOR
            ))
            painter.setPen(Qt.NoPen)
            tw, th = 8, 5
            x = rect.right() - 16
            y = rect.top() + (rect.height() - th) / 2
            painter.drawPolygon(QPolygon([QPoint(x, y),
                                          QPoint(x + tw, y),
                                          QPoint(x + tw / 2, y + th)]))
            painter.restore()

    def mousePressEvent(self, event):
        self._press_pos = event.pos()
        super().mousePressEvent(event)

        logical_index = self.logicalIndexAt(event.pos())
        if logical_index == 0:
            self.toggle_all()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)

        if event.button() == Qt.LeftButton and self._press_pos is not None:
            logical_index = self.logicalIndexAt(event.pos())

            # 判断：第 4 列，且没有发生明显拖拽（单击）
            if logical_index == 4 and (event.pos() - self._press_pos).manhattanLength() <= 3:
                self.show_name_filter(event.globalPos())

            self._press_pos = None

    def toggle_all(self):
        parent_table = self.parent()
        if not parent_table:
            return

        model = parent_table.model()
        sel_model = parent_table.selectionModel()

        valid_rows = []
        for r in range(parent_table.rowCount()):
            item = parent_table.item(r, 0)
            if item and item.data(Qt.UserRole) is not None and not parent_table.isRowHidden(r):
                valid_rows.append(r)

        if not valid_rows:
            return

        all_selected = all(
            sel_model.isRowSelected(r, model.index(r, 0).parent())
            for r in valid_rows
        )

        last_col = parent_table.columnCount() - 1
        selection = QItemSelection()
        for r in valid_rows:
            selection.select(model.index(r, 0), model.index(r, last_col))

        if all_selected:
            sel_model.select(selection,
                             QItemSelectionModel.Deselect | QItemSelectionModel.Rows)
        else:
            sel_model.select(selection,
                             QItemSelectionModel.Select | QItemSelectionModel.Rows)

        self.viewport().update()

    def show_name_filter(self, global_pos):
        parent_table = self.parent()
        if not parent_table:
            return
        if self.active_popup:
            self.active_popup.close()

        popup = NameFilterPopup(parent_table, self.filtered_names, self.apply_name_filter)
        self.active_popup = popup

        screen = QApplication.screenAt(global_pos)
        if screen:
            sr = screen.availableGeometry()
            x = global_pos.x() - 100
            y = global_pos.y() + 30
            if x + popup.width() > sr.right():
                x = sr.right() - popup.width() - 10
            if y + popup.height() > sr.bottom():
                y = global_pos.y() - popup.height() - 10
            popup.move(x, y)
        else:
            popup.move(global_pos.x() - 100, global_pos.y() + 30)
        popup.show()

    def apply_name_filter(self, filtered_names):
        self.filtered_names = filtered_names
        parent_table = self.parent()
        if parent_table:
            for row in range(parent_table.rowCount()):
                name_item = parent_table.item(row, 4)
                name_text = name_item.text().strip() if name_item else ""
                parent_table.setRowHidden(row, name_text in self.filtered_names)
        self.viewport().update()


# ================= 待抓取表格 =================
class SpiderSourceTable(QTableWidget):
    def mousePressEvent(self, event):
        index = self.indexAt(event.pos())
        if index.isValid():
            if index.column() == 0:
                sel_model = self.selectionModel()
                is_selected = sel_model.isRowSelected(index.row(), index.parent())
                mods = event.modifiers()

                self.setCurrentIndex(index)

                if mods & (Qt.ControlModifier | Qt.ShiftModifier):
                    if is_selected:
                        sel_model.select(index, QItemSelectionModel.Deselect | QItemSelectionModel.Rows)
                    else:
                        sel_model.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
                else:
                    if is_selected:
                        sel_model.select(index, QItemSelectionModel.Deselect | QItemSelectionModel.Rows)
                    else:
                        sel_model.select(index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)

                event.accept()
                return
            elif index.column() == 8:
                chk_item = self.item(index.row(), 0)
                if chk_item:
                    data = chk_item.data(Qt.UserRole)
                    if data and isinstance(data, dict) and data.get("comp_time", ""):
                        return  # 有投诉时间，拦截点击

                self.setCurrentIndex(index)
                self.edit(index)
                event.accept()
                return

        super().mousePressEvent(event)


class WebPreviewDialog(QDialog):
    def __init__(self, parent, preview_url, book_name, category, is_mobile, books):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle(f"网页预览 - {preview_url}")
        self.resize(1000, 800)

        self._book_name = book_name
        self._category = category
        self._books = books
        self._is_mobile = int(bool(is_mobile))
        self.picker = None

        self._preview_url = preview_url  # ★ 1. 把这一行提前到这里
        self._has_loaded = False  # ★ 顺便把加载标记也初始化一下

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.picker = PickerWebView(
            self,
            context_provider=self._build_ctx,
            lock_mode="previewing",
            allow_change_book=True,
            allow_change_category=True,
            initial_is_mobile=self._is_mobile,
        )

        if not self.picker.is_locked_by_us():
            try:
                self.picker.web_view.stop()
                self.picker.web_view.setUrl(QUrl("about:blank"))
            except Exception:
                pass
            self.picker.deleteLater()
            self.picker = None
            QMessageBox.information(
                self, "暂时无法预览",
                "后台正在执行搜索或解析任务，请等待完成后再预览。\n"
                "（其他预览窗口也会占用此锁，请先关闭）"
            )
            return

        layout.addWidget(self.picker)

        from parser_service import parser_signals
        parser_signals.parse_finished.connect(self._on_parse_finished)
        self.picker.batch_saved.connect(self._on_batch_saved)

    def _build_ctx(self):
        return {
            "book_name": self._book_name,
            "category": self._category,
            "keyword": "",
            "batch_id": "SPIDER_PICK",
            "source_platform": self._category,
            "parent_url": self._preview_url,
            "books": self._books,
            "categories": list(ALL_SUB_PLATFORMS),
        }

    def _on_batch_saved(self, bid):
        try:
            from search_result_panel import ToastLabel
            ToastLabel(self, "📦 已提交解析", "#3B82F6")
        except Exception as e:
            print(f"[Spider Preview] Toast 提示失败: {e}")

    def _on_parse_finished(self, *args):
        # 兼容 parse_finished 可能是 Signal(bool) 或 Signal(bool, str)
        success = args[0] if len(args) >= 1 else False
        try:
            self._spider_post_process()
        except Exception as e:
            print(f"[Spider Preview] 后处理失败: {e}")

        # ★ 核心改动：只加这一段，通知背后的 SpiderPage 刷新表格
        parent_page = self.parent()
        if parent_page and hasattr(parent_page, "refresh_data"):
            parent_page.refresh_data()

        if success:
            try:
                from search_result_panel import ToastLabel
                ToastLabel(self, "✅ 解析完成", "#10B981")
            except Exception:
                pass

    def _spider_post_process(self):
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT id, crawl_status, domain_matched, category "
                "FROM draft_links WHERE batch_id = 'SPIDER_PICK'"
            ).fetchall()

            archive_items, delete_ids = [], []
            for db_id, status, dm, cat in rows:
                if status == CRAWL_STATUS_ORGANIZED:
                    # ★ 核心修改：不管是明确资源(dm=1)还是未知网站(dm=0)，统统直接入库
                    archive_items.append((db_id, cat))
                else:
                    # 重复、白名单、黑名单、解析失败，直接删掉
                    delete_ids.append(db_id)

            if archive_items:
                from link_ingest_service import archive_batch_by_ids
                archive_batch_by_ids(archive_items, [], [])
            if delete_ids:
                ph = ','.join('?' * len(delete_ids))
                conn.execute(f"DELETE FROM draft_links WHERE id IN ({ph})", delete_ids)
                conn.commit()
        finally:
            conn.close()

    def showEvent(self, event):
        super().showEvent(event)
        # ★ 关键：延迟到窗口显示后再加载，防止 QDialog 渲染未就绪导致白屏
        if not getattr(self, "_has_loaded", False) and getattr(self, "_preview_url", None):
            self._has_loaded = True
            self.picker.load_url(self._preview_url)

    def closeEvent(self, event):
        try:
            from parser_service import parser_signals
            parser_signals.parse_finished.disconnect(self._on_parse_finished)
        except Exception:
            pass

        if getattr(self, "picker", None):
            try:
                self.picker.destroy_picker()
            except Exception:
                pass
            self.picker.release_lock()
            # ★ 不要 self.picker = None

        event.accept()


class SpiderPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # # ================= 1. 顶部标题区 =================
        # title_layout = QHBoxLayout()
        # title_vbox = QVBoxLayout()
        # title_vbox.setSpacing(8)
        # main_title = QLabel("【待抓取的页面】")
        # main_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1F1F1F;")
        # sub_title = QLabel("双击链接即可预览网页。")
        # sub_title.setStyleSheet("font-size: 13px; color: #888888;")
        # sub_title.setWordWrap(True) # ★ 加上防撑爆换行
        # title_vbox.addWidget(main_title)
        # title_vbox.addWidget(sub_title)
        # title_layout.addLayout(title_vbox)
        # title_layout.addStretch()
        # layout.addLayout(title_layout)

        # ================= 2. 卡片式工具栏 =================
        tool_frame = QFrame()
        tool_frame.setStyleSheet("""
            QFrame {
                background-color: #F8F9FA;
                border: 1px solid #E9ECEF;
                border-radius: 6px;
            }
        """)
        tool_layout = QHBoxLayout(tool_frame)
        tool_layout.setContentsMargins(15, 10, 15, 10)
        tool_layout.setSpacing(10)

        lbl_status = QLabel("待抓取链接:")
        lbl_status.setStyleSheet("color: #495057; font-weight: bold; border: none;")
        tool_layout.addWidget(lbl_status)

        self.status_filter = QComboBox()
        self.status_filter.addItems(["全部状态", "仅未抓取", "仅已抓取", "已投诉", "已失效"])
        self.status_filter.setCurrentIndex(1)
        self.status_filter.setStyleSheet("""
            QComboBox { border: 1px solid #CCCCCC; border-radius: 4px; padding: 4px 8px; background: #FFF; min-width: 100px; color: #333; }
            QComboBox:hover { border: 1px solid #1F1F1F; }
        """)
        self.status_filter.currentTextChanged.connect(self.refresh_data)
        tool_layout.addWidget(self.status_filter)

        tool_layout.addSpacing(20)

        tool_layout.addStretch()

        self.crawl_btn = QPushButton("🔗 开始抓取/提取")
        self.crawl_btn.setStyleSheet("""
            QPushButton {
                background-color: #10B981;
                color: white;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
                border: none;
            }
            QPushButton:hover { background-color: #059669; }
            QPushButton:disabled { background-color: #9CA3AF; }
        """)
        self.crawl_btn.clicked.connect(self.start_crawl)
        tool_layout.addWidget(self.crawl_btn)

        layout.addWidget(tool_frame)

        # ================= 3. 待抓取表格 =================
        self.source_table = SpiderSourceTable()

        self.source_table.setColumnCount(9)
        self.source_table.setHorizontalHeaderLabels(
            ["勾选", "关联作品", "分类", "页面链接", "名称", "属性", "抓取状态", "投诉时间", "资源状态"])

        self.source_table.verticalHeader().setFixedWidth(35)
        self.source_table.verticalHeader().setDefaultSectionSize(35)

        custom_header = SpiderHeaderView(Qt.Horizontal, self.source_table)
        self.source_table.setHorizontalHeader(custom_header)

        header_src = self.source_table.horizontalHeader()
        header_src.setSectionResizeMode(QHeaderView.Stretch)
        header_src.setSectionResizeMode(0, QHeaderView.Fixed)
        header_src.setSectionResizeMode(8, QHeaderView.Fixed)
        self.source_table.setColumnWidth(0, 45)
        self.source_table.setColumnWidth(8, 100)

        self.source_table.setItemDelegateForColumn(0, CenteredCheckBoxDelegate(self.source_table))
        self.source_table.setItemDelegateForColumn(8, SpiderStatusComboDelegate(self.source_table))

        self.source_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.source_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.source_table.setMaximumHeight(220)
        self.source_table.cellDoubleClicked.connect(self.on_cell_double_clicked)

        self.source_table.selectionModel().selectionChanged.connect(self.on_source_selection_changed)

        self.source_table.setShowGrid(True)
        self.source_table.setStyleSheet(
            f"""
            QTableWidget {{ border: 1px solid #E9ECEF; background-color: #FFFFFF; }}
            QTableWidget::item:selected {{ background-color: {ROW_SELECTED_BG}; color: #333; }}
            """
            + TABLE_HEADER_BASE
            + "QHeaderView::section:horizontal { background: transparent; }"
        )
        self.source_table.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding) # ★ 防撑爆
        layout.addWidget(self.source_table)

        # ================= 4. 提取结果区 =================
        result_title = QLabel("【提取结果】")
        result_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1F1F1F; margin-top: 10px;")
        layout.addWidget(result_title)

        self.result_model = QStandardItemModel(0, 6, self)
        self.result_model.setHorizontalHeaderLabels(
            ["勾选", "关联作品", "提取到的链接", "分类", "名称(可修改)", "属性(可修改)"])

        self.proxy_model = ExcelFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.result_model)

        self.result_view = QTableView()
        self.result_view.setModel(self.proxy_model)

        self.result_view.setShowGrid(True)
        self.result_view.setStyleSheet(
            f"""
            QTableView {{ border: 1px solid #E9ECEF; background-color: #FFFFFF; }}
            QTableView::item:selected {{ background-color: {ROW_SELECTED_BG}; color: #333; }}
            """
            + TABLE_HEADER_BASE
            + "QHeaderView::section:horizontal { background: transparent; }"
        )

        res_header = FilterHeaderView(Qt.Horizontal, self.result_view)
        self.result_view.setHorizontalHeader(res_header)
        self.result_view.horizontalHeader().setFixedHeight(35)

        self.result_view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.result_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.result_view.setColumnWidth(0, 45)

        self.result_view.setItemDelegateForColumn(0, CenteredCheckBoxDelegate(self.result_view))

        self.result_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_view.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)

        self.result_view.verticalHeader().setFixedWidth(35)
        self.result_view.selectionModel().selectionChanged.connect(self.on_result_selection_changed)

        self.result_view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding) # ★ 防撑爆
        layout.addWidget(self.result_view)

        # ================= 5. 保存按钮 =================
        self.save_btn = QPushButton("将勾选结果保存到盗版链接库")
        self.save_btn.setStyleSheet("""
            QPushButton { background-color: #10B981; color: white; padding: 10px; border-radius: 4px; font-weight: bold; font-size: 14px; border: none; }
            QPushButton:hover { background-color: #059669; }
            QPushButton:disabled { background-color: #9CA3AF; }
        """)
        self.save_btn.clicked.connect(self.save_results)
        layout.addWidget(self.save_btn)

        # ★ 权限拦截：初始化时检查是否过期，如果过期直接禁用按钮
        if license_mgr.is_expired:
            tip = "您的会员已过期，无法使用高级功能。请联系管理员续费。"
            self.crawl_btn.setEnabled(False)
            self.crawl_btn.setToolTip(tip)
            self.save_btn.setEnabled(False)
            self.save_btn.setToolTip(tip)

    def on_source_selection_changed(self, selected, deselected):
        selected_rows = set(idx.row() for idx in selected.indexes())
        deselected_rows = set(idx.row() for idx in deselected.indexes())
        deselected_rows -= selected_rows

        self.source_table.blockSignals(True)
        for r in deselected_rows:
            chk = self.source_table.item(r, 0)
            if chk:
                chk.setCheckState(Qt.Unchecked)
        for r in selected_rows:
            chk = self.source_table.item(r, 0)
            if chk:
                chk.setCheckState(Qt.Checked)
        self.source_table.blockSignals(False)
        self.source_table.viewport().update()
        self.source_table.horizontalHeader().viewport().update()

    def on_result_selection_changed(self, selected, deselected):
        selected_rows = set(idx.row() for idx in selected.indexes())
        deselected_rows = set(idx.row() for idx in deselected.indexes())
        deselected_rows -= selected_rows

        for r in deselected_rows:
            src_idx = self.proxy_model.mapToSource(self.proxy_model.index(r, 0))
            item = self.result_model.item(src_idx.row(), 0)
            if item and item.data(Qt.UserRole) is not None:
                if item.checkState() != Qt.Unchecked:
                    item.setCheckState(Qt.Unchecked)

        for r in selected_rows:
            src_idx = self.proxy_model.mapToSource(self.proxy_model.index(r, 0))
            item = self.result_model.item(src_idx.row(), 0)
            if item and item.data(Qt.UserRole) is not None:
                if item.checkState() != Qt.Checked:
                    item.setCheckState(Qt.Checked)
        self.result_view.viewport().update()

    def on_cell_double_clicked(self, row, column):
        if column != 3:
            return
        url = self.source_table.item(row, 3).text()
        if not url:
            return

        data = self.source_table.item(row, 0).data(Qt.UserRole) or {}
        is_mobile = data.get("is_mobile") or 0

        book_name = self.source_table.item(row, 1).text()
        if book_name == "未关联":
            book_name = ""

        category = self.source_table.item(row, 2).text() or "其他"

        conn = get_conn()
        try:
            books = [r[0] for r in conn.execute("SELECT name FROM books ORDER BY name").fetchall()]
        finally:
            conn.close()

        dlg = WebPreviewDialog(
            self, url,
            book_name=book_name,
            category=category,
            is_mobile=is_mobile,
            books=books,
        )
        if dlg.picker:
            dlg.exec()

    def refresh_data(self):
        self.source_table.setRowCount(0)
        conn = get_conn()
        cursor = conn.cursor()

        # ★ 正向筛：只取 MODULE_SEARCH 的分类（百度搜索 / 夸克搜索 / uc搜索）
        placeholders = ",".join("?" * len(SEARCH_MODULE_CATEGORIES))
        query = f'''SELECT s.id,
                           COALESCE(b.name, s.book_name) as real_book_name,
                           s.category,
                           s.site_url,
                           s.name,
                           s.attr,
                           s.crawl_status,
                           s.resource_status,
                           s.complaint_time,
                           s.is_mobile
                    FROM sites s
                    LEFT JOIN books b ON s.book_id = b.id
                    WHERE COALESCE(s.category, '') IN ({placeholders})'''
        params = list(SEARCH_MODULE_CATEGORIES)

        filter_text = self.status_filter.currentText()
        if filter_text == "仅未抓取":
            query += (" AND (s.crawl_status IS NULL OR s.crawl_status = '')"
                      " AND (s.resource_status IS NULL OR s.resource_status = '')"
                      " AND (s.complaint_time IS NULL OR s.complaint_time = '')")
        elif filter_text == "仅已抓取":
            query += " AND s.crawl_status = '已抓取'"
        elif filter_text == "已投诉":
            query += " AND s.complaint_time IS NOT NULL AND s.complaint_time != ''"
        elif filter_text == "已失效":
            query += " AND s.resource_status = '已失效'"

        query += " ORDER BY s.id DESC"
        records = cursor.execute(query, params).fetchall()
        conn.close()

        for row_data in records:
            db_id, book_name, cat, url, name, attr, crawl_status, res_status, comp_time, is_mobile = row_data
            row = self.source_table.rowCount()
            self.source_table.insertRow(row)

            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Unchecked)
            chk.setData(Qt.UserRole, {
                "id": db_id,
                "comp_time": comp_time,
                "is_mobile": is_mobile,
            })
            self.source_table.setItem(row, 0, chk)

            for col_idx, text in enumerate(
                    [book_name or "未关联", cat or "未分类", url, name or "", attr or "", crawl_status or ""],
                    start=1):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

                if col_idx == 3:
                    item.setToolTip("双击此行此列可预览网页")
                    # ★ 新增：把链接列变成网页蓝 + 下划线
                    item.setForeground(QColor("#3B82F6"))  # 这是网页常用的蓝色
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)

                if col_idx == 6 and crawl_status == '已抓取':
                    item.setForeground(QColor("#10B981"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.source_table.setItem(row, col_idx, item)

            comp_time_item = QTableWidgetItem(comp_time or "")
            comp_time_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.source_table.setItem(row, 7, comp_time_item)

            display_status = ""
            if not comp_time and res_status == "已失效":
                display_status = "已失效"

            status_item = QTableWidgetItem(display_status)
            status_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.source_table.setItem(row, 8, status_item)

        self.source_table.horizontalHeader().filtered_names = []
        self.source_table.horizontalHeader().viewport().update()

    def start_crawl(self):
        if license_mgr.is_expired:
            QMessageBox.warning(self, "授权已过期",
                                "您的会员已过期，无法使用网页抓取功能。\n请前往【个人中心】查看详情或联系管理员续费。")
            return

        rows_to_crawl = []
        for r in range(self.source_table.rowCount()):
            if self.source_table.isRowHidden(r):
                continue
            chk_item = self.source_table.item(r, 0)
            if chk_item and chk_item.checkState() == Qt.Checked:
                data = chk_item.data(Qt.UserRole)
                db_id = data.get("id") if isinstance(data, dict) else None
                book_name = self.source_table.item(r, 1).text()
                if book_name == "未关联":
                    book_name = ""
                cat = self.source_table.item(r, 2).text()
                url = self.source_table.item(r, 3).text()
                name = self.source_table.item(r, 4).text()
                attr = self.source_table.item(r, 5).text()
                rows_to_crawl.append((db_id, book_name, cat, url, name, attr))

        if not rows_to_crawl:
            QMessageBox.warning(self, "提示", "请先勾选要抓取的网页！")
            return

        self.crawl_btn.setText("抓取中...")
        self.crawl_btn.setEnabled(False)
        QApplication.processEvents()

        self.result_model.removeRows(0, self.result_model.rowCount())
        self.proxy_model.filter_dict.clear()
        self.proxy_model.invalidateFilter()

        total_chapters = 0
        total_intros = 0
        failed_sites = []
        results = []
        stats = []

        try:
            total_chapters, total_intros, failed_sites, results, stats = crawl_sites(
                rows_to_crawl,
                on_progress=lambda text: (self.crawl_btn.setText(text), QApplication.processEvents()),
            )
            for r in results:
                self.add_result_row(
                    r["book_name"], r["url"], r["category"],
                    title=r["title"], name=r["name"], attr=r["attr"], source_id=r["source_id"],
                )
        finally:
            self.crawl_btn.setText("🔗 开始抓取/提取")
            self.crawl_btn.setEnabled(not license_mgr.is_expired)

            if stats:
                msg = "抓取完成！\n\n"
                for b_name, cat, dom, n, status in stats:
                    if status == "failed":
                        msg += f"《{b_name}》- [{cat}]：失败\n"
                    else:
                        msg += f"《{b_name}》- [{cat}]：成功获取链接 {n} 条\n"
                QMessageBox.information(self, "抓取结果", msg)
            else:
                QMessageBox.warning(self, "未抓取到内容", "所有网页均未加载成功。")

    def add_result_row(self, book_name, url, category, title="", name="", attr="", source_id=None):
        chk_item = QStandardItem()
        chk_item.setCheckable(True)
        chk_item.setCheckState(Qt.Checked)
        chk_item.setData({"url": url, "cat": category, "book_name": book_name, "source_id": source_id},
                         Qt.UserRole)

        col_book = QStandardItem(book_name or "未关联")
        col_url = QStandardItem(url)
        col_cat = QStandardItem(category)

        display_name = name if name else ""
        col_name = QStandardItem(display_name)

        col_attr = QStandardItem(attr)

        self.result_model.appendRow([chk_item, col_book, col_url, col_cat, col_name, col_attr])

    def save_results(self):
        # ★ 逻辑层拦截
        if license_mgr.is_expired:
            QMessageBox.warning(self, "功能受限", "您的会员已过期，无法将抓取结果保存到库中。")
            return

        rows_to_save = []
        for row in range(self.result_model.rowCount()):
            chk_item = self.result_model.item(row, 0)
            if chk_item and chk_item.checkState() == Qt.Checked:
                data = chk_item.data(Qt.UserRole)
                if not data:
                    continue
                data["name"] = self.result_model.item(row, 4).text().strip()
                data["attr"] = self.result_model.item(row, 5).text().strip()
                rows_to_save.append(data)

        if not rows_to_save:
            QMessageBox.warning(self, "提示", "没有勾选任何有效数据！")
            return

        conn = get_conn()
        cursor = conn.cursor()
        saved_count = 0
        duplicate_count = 0
        source_ids_to_mark = set()

        for item_data in rows_to_save:
            url = item_data["url"]
            cat = item_data["cat"]
            book_name = item_data["book_name"]
            name = item_data.get("name", "")
            attr = item_data.get("attr", "")
            source_id = item_data.get("source_id")

            if source_id:
                source_ids_to_mark.add(source_id)

            book_id = None
            if book_name:
                res = cursor.execute("SELECT id FROM books WHERE name=?", (book_name,)).fetchone()
                if res:
                    book_id = res[0]

            code = extract_pure_feature(url)
            if cursor.execute("SELECT id FROM sites WHERE feature_code=?", (code,)).fetchone():
                duplicate_count += 1
                continue

            try:
                # ★ category 与 target_platform 双写，确保在盗版链接页正常显示分类。
                #   page_links.refresh_table() 的资源分类列读的就是 target_platform。
                cursor.execute('''INSERT INTO sites
                                  (category, target_platform, site_url, feature_code, book_id, book_name, name, attr, crawl_status)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                               (cat, cat, url, code, book_id, book_name, name, attr, '已抓取'))
                saved_count += 1
            except sqlite3.IntegrityError:
                pass

        for s_id in source_ids_to_mark:
            cursor.execute("UPDATE sites SET crawl_status='已抓取' WHERE id=?", (s_id,))

        conn.commit()
        conn.close()

        self.result_model.removeRows(0, self.result_model.rowCount())
        self.refresh_data()

        QMessageBox.information(self, "保存成功", f"已向库中新增 {saved_count} 条记录！\n跳过重复 {duplicate_count} 条。")

        if hasattr(self.main_window, 'links_page'):
            self.main_window.links_page.refresh_table()