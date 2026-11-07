# page_links.py
import sqlite3
import os
import subprocess
import json
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QHeaderView, QMessageBox, QButtonGroup, QAbstractItemView,
                               QFileDialog, QTableView, QDialog, QGroupBox,
                               QStyledItemDelegate, QStyle, QScrollArea, QFrame)
from PySide6.QtCore import (
    Qt, QSettings, QItemSelectionModel, QEvent, QTimer,
    QObject, Signal, QRunnable, QThreadPool,
)
from PySide6.QtGui import QColor, QFont, QStandardItemModel, QStandardItem, QPainter, QBrush

from link_resolver import is_short_url

from db_config import get_conn
from feature_extractor import extract_pure_feature, get_platform_category
from data_importer import import_sites_from_file

from complaint_service import prepare_complaints_data

from app_data import PLATFORM_CONFIG, ALL_SUB_PLATFORMS, CAT_TO_CODE_MAP, PLATFORM_OTHER
from app_data.complaint_platform_consts import HIDDEN_MAIN_CATEGORIES, HIDDEN_SUB_PLATFORMS
from app_data.crawl_status_consts import CRAWL_STATUS_DONE

from ui_components import (
    ExcelFilterProxyModel,
    FilterHeaderView,
    CenteredCheckBoxDelegate,
    ComboCellDelegate,
    StatusComboDelegate,
    HintButton,
    ColumnWidthPersister,
    apply_required_style,
    handle_check_column_click,  # ★ 新增
    SelectionCheckSync,         # ★ 新增
)

from ui_styles import (
    BTN_SECONDARY,
    BTN_SECONDARY_DARK,
    BTN_PRIMARY,
    BTN_DANGER,
    BTN_DANGER_OUTLINE,
    BTN_TOGGLE,
    BTN_TOGGLE_MAIN,
    TABLE_BASE,
    TABLE_HEADER_BASE,        # ★ 新增
)

from app_signals import signals

# ★ 列索引
COL_CHK = 0
COL_BOOK = 1
COL_CAT = 2
COL_URL = 3
COL_PUBLISHER = 4  # 网站域名（来源名，可编辑）
COL_ATTR = 5  # 属性（手动获取/机器人获取）
COL_CODE = 6
COL_CRAWL_STATUS = 7
COL_COMP_TIME = 8
COL_RES_STATUS = 9
COL_TITLE = 10  # 标题（只读）

TABLE_HEADERS = [
    "勾选", "关联作品(必填)", "资源分类(必填)", "资源链接(必填)", "网站域名",
    "属性", "特征码", "抓取状态", "投诉时间", "资源状态", "标题",
]


class LinksTableView(QTableView):
    def mousePressEvent(self, event):
        # ★ 调用公共拦截函数
        if handle_check_column_click(self, event, chk_col=COL_CHK):
            event.accept()
            return

        index = self.indexAt(event.pos())
        if index.isValid():
            if index.column() in (COL_BOOK, COL_CAT, COL_RES_STATUS):
                model = self.model()
                if index.column() == COL_RES_STATUS:
                    comp_time = model.index(index.row(), COL_COMP_TIME).data()
                    if comp_time and str(comp_time).strip():
                        return

                if self.selectionModel().isRowSelected(index.row(), index.parent()):
                    self.edit(index)
                else:
                    self.setCurrentIndex(index)
                event.accept()
                return

        super().mousePressEvent(event)


class ImportLinksDialog(QDialog):
    def __init__(self, parent_page):
        super().__init__(parent_page)
        self.parent_page = parent_page
        self.setWindowTitle("批量导入盗版链接")
        self.setFixedSize(480, 300)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        group_template = QGroupBox("第一步：下载标准模板")
        group_template.setStyleSheet("QGroupBox { font-weight: bold; color: #333; }")
        layout_template = QVBoxLayout(group_template)

        lbl_tip1 = QLabel("为了确保数据能正确导入，请先下载模板，并按照表头格式填写您的链接信息。")
        lbl_tip1.setStyleSheet("color: #666; font-weight: normal;")
        lbl_tip1.setWordWrap(True)
        layout_template.addWidget(lbl_tip1)

        btn_download = QPushButton("下载 Excel 模板")
        btn_download.setStyleSheet(BTN_SECONDARY_DARK + "QPushButton { padding: 8px 12px; }")
        btn_download.clicked.connect(self.download_template)
        layout_template.addWidget(btn_download)

        layout.addWidget(group_template)

        group_upload = QGroupBox("第二步：上传已填写的文件")
        group_upload.setStyleSheet("QGroupBox { font-weight: bold; color: #333; }")
        layout_upload = QVBoxLayout(group_upload)

        lbl_tip2 = QLabel("请选择填写好的 Excel (.xlsx) 或 CSV (.csv) 文件进行导入。如果特征码已存在，系统会自动跳过。")
        lbl_tip2.setStyleSheet("color: #666; font-weight: normal;")
        lbl_tip2.setWordWrap(True)
        layout_upload.addWidget(lbl_tip2)

        btn_upload = QPushButton("选择文件并导入")
        btn_upload.setStyleSheet(BTN_PRIMARY + "QPushButton { padding: 8px 12px; }")
        btn_upload.clicked.connect(self.upload_file)
        layout_upload.addWidget(btn_upload)

        layout.addWidget(group_upload)
        layout.addStretch()

    def download_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存链接导入模板", "链接导入模板.xlsx", "Excel 文件 (*.xlsx)")
        if not path: return
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "盗版链接"

            headers = ["关联作品(必填)", "资源分类", "资源链接(必填)", "网站域名", "属性", "投诉时间", "状态"]
            for col, header in enumerate(headers, 1):
                ws.cell(row=1, column=col, value=header)

            for col in range(1, 8):
                ws.column_dimensions[chr(64 + col)].width = 20

            wb.save(path)
            QMessageBox.information(self, "下载成功", f"标准模板已保存至：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "失败", f"保存模板失败：{e}")

    def upload_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择导入文件", "",
                                              "表格文件 (*.csv *.xlsx);;CSV 文件 (*.csv);;Excel 文件 (*.xlsx)")
        if not path: return
        try:
            success, duplicate, errors = import_sites_from_file(path)
            self.parent_page.refresh_table()

            msg = f"成功导入了 {success} 条新记录！\n(自动跳过了 {duplicate} 条重复)"
            if errors:
                msg += f"\n\n⚠️ 有 {len(errors)} 条记录因校验失败被跳过：\n\n"
                preview = errors[:10]
                msg += "\n".join(preview)
                if len(errors) > 10:
                    msg += f"\n...（还有 {len(errors) - 10} 条未展示）"

            QMessageBox.information(self, "导入完成", msg)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))


class _ShortUrlResolveSignals(QObject):
    finished = Signal(int, str, str)   # row, old_url, new_url
    failed = Signal(int, str, str)     # row, old_url, err_msg


class _ShortUrlResolveWorker(QRunnable):
    def __init__(self, row, url, signals):
        super().__init__()
        self.row = row
        self.url = url
        self.signals = signals

    def run(self):
        try:
            from browser_manager import BrowserManager
            from link_resolver import LinkResolver
            try:
                bm = BrowserManager.instance()
            except Exception:
                bm = None
            resolver = LinkResolver(browser_manager=bm)
            new_url = resolver.resolve_single(self.url, 1)   # 移动端
            self.signals.finished.emit(self.row, self.url, new_url or "")
        except Exception as e:
            self.signals.failed.emit(self.row, self.url, str(e))


class LinksPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.settings = QSettings("AuthorTools", "AntiPiracy")

        self.cat_map = {"全部链接": ["全部资源"]}
        for main_cat, sub_list in PLATFORM_CONFIG.items():
            sub_names = [sub[0] for sub in sub_list]
            self.cat_map[main_cat] = sub_names
            for sub_name in sub_names:
                self.cat_map[sub_name] = [sub_name]

        self.book_list = []
        self._is_updating = False
        self._current_cat = "全部链接"
        self.filters_by_cat = {}

        # ★ 短链检测状态（必须在 setup_ui 之前，因为 setup_ui 内部会 refresh_table）
        self._short_url_timer = QTimer(self)
        self._short_url_timer.setSingleShot(True)
        self._short_url_timer.setInterval(600)
        self._short_url_timer.timeout.connect(self._flush_short_url_check)
        self._pending_short_rows = {}  # {row: url}
        self._short_url_resolving = set()  # {row}
        self._declined_short_urls = set()  # 用户拒绝解析的短链（会话内）
        self._suppress_short_url_check = False
        self._short_url_pool = QThreadPool.globalInstance()

        self.setup_ui()

        signals.link_saved.connect(self.on_link_saved_signal, Qt.UniqueConnection)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_books()
        self.refresh_table()

    def on_link_saved_signal(self, record, duplicate):
        self.append_site_row(record, duplicate)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self.cat_group = QButtonGroup(self)
        self.cat_group.setExclusive(True)

        filter_v_layout = QVBoxLayout()
        filter_v_layout.setSpacing(8)

        # ------------------ 快捷分组 ------------------
        main_row = QHBoxLayout()
        main_row.setSpacing(8)
        main_row.setContentsMargins(0, 0, 0, 0)

        lbl_main = QLabel("快捷分组:")
        lbl_main.setStyleSheet("color: #666; font-weight: bold; font-size: 13px;")
        lbl_main.setFixedWidth(65)
        main_row.addWidget(lbl_main)

        main_scroll = QScrollArea()
        main_scroll.setWidgetResizable(True)
        main_scroll.setFrameShape(QFrame.NoFrame)
        main_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        main_scroll.setFixedHeight(45)
        main_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        main_scroll.viewport().setStyleSheet("background: transparent;")

        main_widget = QWidget()
        main_widget.setStyleSheet("background: transparent;")
        btn_layout_main = QHBoxLayout(main_widget)
        btn_layout_main.setContentsMargins(0, 0, 0, 0)
        btn_layout_main.setSpacing(8)

        all_btn = QPushButton("全部链接")
        all_btn.setCheckable(True)
        all_btn.setStyleSheet(BTN_TOGGLE_MAIN)
        all_btn.setCursor(Qt.PointingHandCursor)
        all_btn.setFixedSize(85, 32)
        self.cat_group.addButton(all_btn)
        btn_layout_main.addWidget(all_btn)
        all_btn.setChecked(True)

        for main_cat in PLATFORM_CONFIG.keys():
            if main_cat in HIDDEN_MAIN_CATEGORIES:
                continue
            btn = QPushButton(main_cat)
            btn.setCheckable(True)
            btn.setStyleSheet(BTN_TOGGLE_MAIN)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedSize(85, 32)
            self.cat_group.addButton(btn)
            btn_layout_main.addWidget(btn)

        btn_layout_main.addStretch()

        main_scroll.setWidget(main_widget)
        main_row.addWidget(main_scroll, 1)

        filter_v_layout.addLayout(main_row)

        # ------------------ 具体平台 ------------------
        sub_row = QHBoxLayout()
        sub_row.setSpacing(8)
        sub_row.setContentsMargins(0, 0, 0, 0)

        lbl_sub = QLabel("具体平台:")
        lbl_sub.setStyleSheet("color: #666; font-weight: bold; font-size: 13px;")
        lbl_sub.setFixedWidth(65)
        sub_row.addWidget(lbl_sub)

        sub_scroll = QScrollArea()
        sub_scroll.setWidgetResizable(True)
        sub_scroll.setFrameShape(QFrame.NoFrame)
        sub_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sub_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        sub_scroll.setFixedHeight(45)
        sub_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        sub_scroll.viewport().setStyleSheet("background: transparent;")

        sub_widget = QWidget()
        sub_widget.setStyleSheet("background: transparent;")
        btn_layout_sub = QHBoxLayout(sub_widget)
        btn_layout_sub.setContentsMargins(0, 0, 0, 0)
        btn_layout_sub.setSpacing(8)

        for sub_name in ALL_SUB_PLATFORMS:
            if sub_name in HIDDEN_SUB_PLATFORMS:
                continue
            btn = QPushButton(sub_name)
            btn.setCheckable(True)
            btn.setStyleSheet(BTN_TOGGLE)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedSize(85, 32)
            self.cat_group.addButton(btn)
            btn_layout_sub.addWidget(btn)

        btn_layout_sub.addStretch()

        sub_scroll.setWidget(sub_widget)
        sub_row.addWidget(sub_scroll, 1)

        filter_v_layout.addLayout(sub_row)

        layout.addLayout(filter_v_layout)

        # ------------------ 操作按钮 ------------------
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addWidget(HintButton("带有淡红底色的单元格为必填项未填写。点击资源状态列可展开修改。"))

        action_layout.addStretch()

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #666; font-size: 13px;")
        action_layout.addWidget(self.stats_label)

        self.report_btn = QPushButton(" 一键发起投诉 (勾选)")
        self.report_btn.setStyleSheet(BTN_DANGER)
        self.report_btn.clicked.connect(self.send_to_complaints)
        action_layout.addWidget(self.report_btn)

        import_btn = QPushButton("批量导入表格")
        import_btn.setStyleSheet(BTN_SECONDARY)
        import_btn.clicked.connect(self.open_import_dialog)
        action_layout.addWidget(import_btn)

        export_btn = QPushButton("导出勾选数据")
        export_btn.setStyleSheet(BTN_SECONDARY)
        export_btn.clicked.connect(self.export_to_excel)
        action_layout.addWidget(export_btn)

        del_btn = QPushButton("🗑️ 删除选中")
        del_btn.setStyleSheet(BTN_DANGER_OUTLINE)
        del_btn.clicked.connect(self.delete_site)
        action_layout.addWidget(del_btn)

        action_widget = QWidget()
        action_widget.setStyleSheet("background: transparent;")
        action_widget.setLayout(action_layout)

        action_scroll = QScrollArea()
        action_scroll.setWidgetResizable(True)
        action_scroll.setFrameShape(QFrame.NoFrame)
        action_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        action_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        action_scroll.setFixedHeight(50)
        action_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        action_scroll.viewport().setStyleSheet("background: transparent;")
        action_scroll.setWidget(action_widget)
        layout.addWidget(action_scroll)

        # ------------------ 表格 ------------------
        self.model = QStandardItemModel(0, len(TABLE_HEADERS), self)
        self.model.setHorizontalHeaderLabels(TABLE_HEADERS)
        self.model.itemChanged.connect(self.on_item_changed)

        self.proxy_model = ExcelFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.filterChanged.connect(self._on_filter_changed)

        self.table = LinksTableView()
        self.table.setModel(self.proxy_model)

        custom_header = FilterHeaderView(Qt.Horizontal, self.table)
        self.table.setHorizontalHeader(custom_header)

        header = self.table.horizontalHeader()
        header.setFixedHeight(35)
        header.setSectionResizeMode(QHeaderView.Interactive)

        # ★ 新增：固定勾选列，关闭尾部拉伸
        header.setSectionResizeMode(COL_CHK, QHeaderView.Fixed)
        header.setStretchLastSection(False)

        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.table.verticalHeader().setFixedWidth(35)

        # ★ 公共骨架 + transparent（FilterHeaderView 的 fillRect 负责画灰底/筛选橙）
        self.table.setStyleSheet(
            TABLE_BASE
            + TABLE_HEADER_BASE
            + "QHeaderView::section:horizontal { background: transparent; }"
        )

        self.table.setColumnWidth(COL_CHK, 40)
        self.table.setColumnWidth(COL_BOOK, 120)
        self.table.setColumnWidth(COL_CAT, 100)
        self.table.setColumnWidth(COL_URL, 400)
        self.table.setColumnWidth(COL_PUBLISHER, 120)
        self.table.setColumnWidth(COL_ATTR, 100)
        self.table.setColumnWidth(COL_CODE, 120)
        self.table.setColumnWidth(COL_CRAWL_STATUS, 100)
        self.table.setColumnWidth(COL_COMP_TIME, 120)
        self.table.setColumnWidth(COL_RES_STATUS, 100)
        self.table.setColumnWidth(COL_TITLE, 250)

        self.table.setItemDelegateForColumn(COL_CHK, CenteredCheckBoxDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_BOOK, ComboCellDelegate(
            options=lambda: [(name, name) for _, name, _, _ in self.book_list],
            parent=self.table,
        ))
        self.table.setItemDelegateForColumn(COL_CAT, ComboCellDelegate(
            options=[(c, c) for c in ALL_SUB_PLATFORMS],
            parent=self.table,
        ))
        self.table.setItemDelegateForColumn(COL_RES_STATUS, StatusComboDelegate(self.table))

        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked | QAbstractItemView.EditKeyPressed)

        self.table.setMouseTracking(True)
        # ★ 使用公共同步组件替换原来的 on_selection_changed
        self.check_sync = SelectionCheckSync(self.table, proxy_model=self.proxy_model)

        # ★ 加回把表格放入界面的代码（之前不小心删了）
        layout.addWidget(self.table, 1)

        self.cat_group.buttonClicked.connect(self._on_cat_clicked)

        self.col_persister = ColumnWidthPersister(
            view=self.table,
            settings=self.settings,
            key="links_col_widths",
            exclude_cols=()
        )
        self.col_persister.attach()

        # ★ 正确的初始化顺序：
        self.refresh_books()  # 1. 加载书籍列表
        self._load_ui_state()  # 2. 恢复上次选中的分类和筛选器
        self.refresh_table()  # 3. 根据恢复的分类渲染表格

    def _load_ui_state(self):
        self._current_cat = self.settings.value("links_current_cat", "全部链接")
        for btn in self.cat_group.buttons():
            if btn.text() == self._current_cat:
                btn.setChecked(True)
                break
        else:
            first_btn = self.cat_group.buttons()[0]
            first_btn.setChecked(True)
            self._current_cat = first_btn.text()
            self.settings.setValue("links_current_cat", self._current_cat)

        raw = self.settings.value("links_filters_by_cat", "")
        if raw:
            try:
                self.filters_by_cat = json.loads(raw)
            except Exception:
                self.filters_by_cat = {}

        self._apply_filters_to_proxy()

    def _on_cat_clicked(self, btn):
        new_cat = btn.text()
        if new_cat == self._current_cat:
            return

        self._current_cat = new_cat
        self._apply_filters_to_proxy()
        self.settings.setValue("links_current_cat", self._current_cat)
        self.refresh_table()

    def _apply_filters_to_proxy(self):
        self._is_updating = True
        self.proxy_model.filter_dict.clear()

        cat_filter = self.filters_by_cat.get(self._current_cat, {})
        for col_str, allowed_list in cat_filter.items():
            self.proxy_model.filter_dict[int(col_str)] = set(allowed_list)

        self.proxy_model.invalidateFilter()
        self.table.horizontalHeader().viewport().update()
        self._is_updating = False

    def _on_filter_changed(self):
        if self._is_updating:
            return

        current_filter = {}
        for col, allowed_set in self.proxy_model.filter_dict.items():
            current_filter[str(col)] = list(allowed_set)

        self.filters_by_cat[self._current_cat] = current_filter
        self.settings.setValue("links_filters_by_cat", json.dumps(self.filters_by_cat))
        self._update_stats()

    def _update_stats(self):
        total_count = max(0, self.model.rowCount() - 15)
        visible_count = max(0, self.proxy_model.rowCount() - 15)
        has_filter = bool(self.proxy_model.filter_dict)

        if not has_filter:
            self.stats_label.setText(f"共 {total_count} 条链接")
            self.stats_label.setStyleSheet("color: #666; font-size: 13px;")
        elif visible_count == 0:
            self.stats_label.setText(f"⚠️ 当前筛选无结果 (共 {total_count} 条)")
            self.stats_label.setStyleSheet("color: #E74C3C; font-size: 13px; font-weight: bold;")
        else:
            self.stats_label.setText(f"筛选结果: {visible_count} / {total_count} 条")
            self.stats_label.setStyleSheet("color: #10B981; font-size: 13px; font-weight: bold;")

    def open_import_dialog(self):
        dialog = ImportLinksDialog(self)
        dialog.exec()

    def export_to_excel(self):
        checked_rows = set()
        for row in range(self.model.rowCount()):
            chk_item = self.model.item(row, COL_CHK)
            if chk_item and chk_item.checkState() == Qt.Checked:
                url_item = self.model.item(row, COL_URL)
                if url_item and url_item.text().strip():
                    checked_rows.add(row)

        for proxy_idx in self.table.selectionModel().selectedRows():
            src_idx = self.proxy_model.mapToSource(proxy_idx)
            if src_idx.isValid():
                url_item = self.model.item(src_idx.row(), COL_URL)
                if url_item and url_item.text().strip():
                    checked_rows.add(src_idx.row())

        if not checked_rows:
            QMessageBox.warning(self, "提示", "请先勾选或选中需要导出的数据！")
            return

        current_display = self._current_cat
        default_name = f"盗版链接导出_{current_display}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "保存Excel文件", default_name, "Excel 文件 (*.xlsx)")
        if not path: return

        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment

            wb = Workbook()
            ws = wb.active
            ws.title = "盗版链接"

            headers = ["关联作品(必填)", "资源分类", "资源链接(必填)", "网站域名", "属性", "投诉时间", "状态"]
            for col_idx, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_idx, value=header)
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal="center")

            for row_idx, r in enumerate(sorted(list(checked_rows)), start=2):
                book_name = self.model.item(r, COL_BOOK).text().strip()
                cat = self.model.item(r, COL_CAT).text().strip()
                url = self.model.item(r, COL_URL).text().replace(" (重复资源链接)", "").strip()
                publisher = self.model.item(r, COL_PUBLISHER).text().strip()
                attr = self.model.item(r, COL_ATTR).text().strip()
                comp_time = self.model.item(r, COL_COMP_TIME).text().strip()
                res_status = self.model.item(r, COL_RES_STATUS).text().strip()

                ws.cell(row=row_idx, column=1, value=book_name)
                ws.cell(row=row_idx, column=2, value=cat)
                ws.cell(row=row_idx, column=3, value=url)
                ws.cell(row=row_idx, column=4, value=publisher)
                ws.cell(row=row_idx, column=5, value=attr)
                ws.cell(row=row_idx, column=6, value=comp_time)
                ws.cell(row=row_idx, column=7, value=res_status)

            ws.column_dimensions['A'].width = 20
            ws.column_dimensions['B'].width = 15
            ws.column_dimensions['C'].width = 50
            ws.column_dimensions['D'].width = 20
            ws.column_dimensions['E'].width = 20
            ws.column_dimensions['F'].width = 20
            ws.column_dimensions['G'].width = 15

            wb.save(path)
            QMessageBox.information(self, "导出成功", f"已成功导出 {len(checked_rows)} 条勾选数据！")
            folder_path = os.path.dirname(path)
            subprocess.Popen(f'explorer "{folder_path}"')
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"生成 Excel 时出错：{e}")

    def send_to_complaints(self):
        target_rows = set()
        for row in range(self.model.rowCount()):
            chk_item = self.model.item(row, COL_CHK)
            if chk_item and chk_item.checkState() == Qt.Checked:
                target_rows.add(row)
        for proxy_idx in self.table.selectionModel().selectedRows():
            src_idx = self.proxy_model.mapToSource(proxy_idx)
            if src_idx.isValid():
                target_rows.add(src_idx.row())

        if not target_rows:
            QMessageBox.warning(self, "提示", "请先勾选或选中需要投诉的条目！")
            return

        # ★ 核心：直接交给 Service 去校验和打包
        batches, error_report = prepare_complaints_data(self.model, self.book_list)

        # ★ 统一拦截：如果有任何错误，直接把错误报告扔给用户
        if error_report:
            err_msg = "\n".join(error_report[:10])
            if len(error_report) > 10:
                err_msg += f"\n...（还有 {len(error_report) - 10} 条错误未展示）"

            QMessageBox.critical(
                self, "⛔ 拦截：数据校验未通过",
                f"检测到以下问题，请修正后再发起投诉：\n\n{err_msg}"
            )
            return

        if not batches:
            QMessageBox.warning(self, "提示", "未能生成有效投诉数据，请检查勾选情况！")
            return

        # 校验通过，进入投诉页
        self.main_window.switchTo(self.main_window.complaint_page)
        self.main_window.complaint_page.load_batches(batches)

    def refresh_books(self):
        conn = get_conn()
        self.book_list = conn.execute("SELECT id, name, platform_id, evidence_path FROM books").fetchall()
        conn.close()

    def on_item_changed(self, item):
        if self._is_updating: return

        row = item.row()
        col = item.column()

        self.update_row_style(row)

        if col == COL_CHK:
            self.table.horizontalHeader().viewport().update()
            return

        if col == COL_URL:
            raw_url = item.text().strip()

            current_display = self._current_cat
            auto_cat = get_platform_category(raw_url)

            strict_cats = [name for name, info in CAT_TO_CODE_MAP.items() if info["module"] == "netdisk"]
            strict_cats.extend(["百度贴吧", "百度知道"])

            if current_display in ALL_SUB_PLATFORMS:
                if current_display in strict_cats and auto_cat != current_display:
                    msg = f"系统识别该链接可能不属于【{current_display}】。"
                    if auto_cat:
                        msg += f"（系统识别为【{auto_cat}】）"

                    reply = QMessageBox.question(
                        self, "⚠️ 分类不匹配提示",
                        f"{msg}\n\n请核对链接，是否强制将其保留在当前分类下？",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                    )

                    if reply != QMessageBox.Yes:
                        self._is_updating = True
                        item.setText("")
                        self.model.item(row, COL_CAT).setText("")
                        self._is_updating = False
                        self.update_row_style(row)
                        self.save_row_to_db(row)
                        return

                self._is_updating = True
                self.model.item(row, COL_CAT).setText(current_display)

            elif current_display == "其他":
                self._is_updating = True
                self.model.item(row, COL_CAT).setText("其他")
            else:
                self._is_updating = True
                if auto_cat and not self.model.item(row, COL_CAT).text().strip():
                    self.model.item(row, COL_CAT).setText(auto_cat)

            self._is_updating = True
            pure_code = extract_pure_feature(raw_url)
            self.model.item(row, COL_CODE).setText(pure_code)

            db_id = self.model.item(row, COL_CHK).data(Qt.UserRole)
            conn = get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sites WHERE feature_code = ? AND id != ?", (pure_code, db_id or "0"))
            is_duplicate = cursor.fetchone()[0] > 0
            conn.close()

            if is_duplicate:
                base_url = raw_url.replace(" (重复资源链接)", "")
                item.setText(f"{base_url} (重复资源链接)")
                item.setForeground(QColor("red"))
                item.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
            else:
                item.setForeground(QColor("#000000"))
                item.setFont(QFont("Microsoft YaHei", 9, QFont.Normal))
            self._is_updating = False

            # ★ 短链检测调度（不阻断现有流程，用原始 raw_url 判断）
            self._maybe_schedule_short_url_check(row, raw_url)

        self.save_row_to_db(row)

        # ★ 核心修复：只要数据发生改变，立刻通知代理模型全局重新过滤
        # 这样 A 行消失后，模型会重新计算一遍 B 行，确保 B 行正确显示
        # self.proxy_model.invalidateFilter()
        # self._update_stats()  # 顺便更新一下右上角的统计数字

        QTimer.singleShot(0, self._safe_refresh_proxy)

    def _safe_refresh_proxy(self):
        """异步安全刷新代理模型，避免在编辑单元格时同步过滤引发死锁或错乱"""
        self.proxy_model.invalidateFilter()
        self._update_stats()

    # ================= 短链检测与解析 =================
    YELLOW_BG = QColor("#FFF3CD")

    def _maybe_schedule_short_url_check(self, row, url):
        """短链检测入口。黄色由 update_row_style 统一维护，此处只负责清残留 + 调度弹窗。"""
        if self._suppress_short_url_check:
            return
        item = self.model.item(row, COL_URL)
        if not item:
            return

        # ★ 去掉可能携带的后缀，用纯 URL 判断
        url = (url or "").replace(" (重复资源链接)", "").strip()

        if not url or not is_short_url(url):
            # 非短链：清掉可能残留的黄色（静默，不触发 itemChanged）
            if item.background().color() == self.YELLOW_BG:
                self._is_updating = True
                try:
                    item.setBackground(QBrush())
                finally:
                    self._is_updating = False
            return

        # 已拒绝过的短链：不再弹窗（黄色由 update_row_style 负责贴）
        if url in self._declined_short_urls:
            return

        self._pending_short_rows[row] = url
        self._short_url_timer.start()

    def _flush_short_url_check(self):
        if not self._pending_short_rows:
            return
        # 编辑中：顺延
        if self.table.state() == QAbstractItemView.EditingState:
            self._short_url_timer.start()
            return

        pending = self._pending_short_rows
        self._pending_short_rows = {}

        for row, url in pending.items():
            item = self.model.item(row, COL_URL)
            if not item:
                continue
            cur = item.text().replace(" (重复资源链接)", "").strip()
            if cur != url:
                continue
            if row in self._short_url_resolving:
                continue

            reply = QMessageBox.question(
                self.window(), "短链提示",
                f"该链接为短链，是否需要启动浏览器解析？\n\n{url}",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                self._declined_short_urls.add(url)
                self._is_updating = True
                try:
                    item.setBackground(self.YELLOW_BG)
                finally:
                    self._is_updating = False
                continue

            self._start_short_url_resolve(row, url)

    def _start_short_url_resolve(self, row, url):
        self._short_url_resolving.add(row)
        signals = _ShortUrlResolveSignals()
        signals.finished.connect(self._on_short_url_resolved)
        signals.failed.connect(self._on_short_url_resolve_failed)
        self._short_url_pool.start(_ShortUrlResolveWorker(row, url, signals))

    def _on_short_url_resolved(self, row, old_url, new_url):
        # ★ 若解析期间 refresh_table 已清空集合，说明 row 已失效
        if row not in self._short_url_resolving:
            return
        self._short_url_resolving.discard(row)

        if not new_url or new_url == old_url:
            QMessageBox.information(self.window(), "解析结果", "未能解析出更短的链接，已保留原值。")
            return

        item = self.model.item(row, COL_URL)
        if not item:
            return
        cur = item.text().replace(" (重复资源链接)", "").strip()
        if cur != old_url:
            return  # 解析期间用户改过，不覆盖

        self._declined_short_urls.discard(old_url)

        # ★ 先静默清黄色，再 setText 让它单独跑一遍完整流程
        self._suppress_short_url_check = True
        try:
            self._is_updating = True
            try:
                item.setBackground(QBrush())
            finally:
                self._is_updating = False
            item.setText(new_url)   # 触发 on_item_changed，走 link 页完整流程
        finally:
            self._suppress_short_url_check = False

        QMessageBox.information(self.window(), "解析成功", f"已替换为真实链接：\n{new_url}")

    def _on_short_url_resolve_failed(self, row, old_url, err_msg):
        if row not in self._short_url_resolving:
            return
        self._short_url_resolving.discard(row)
        QMessageBox.warning(self.window(), "解析失败", f"短链解析失败：\n{err_msg}\n\n已保留原链接。")

    def update_row_style(self, row):
        items = [self.model.item(row, c) for c in [COL_BOOK, COL_CAT, COL_URL]]
        if not all(items): return

        texts = [it.text().replace(" (重复资源链接)", "").strip() for it in items]
        db_id = self.model.item(row, COL_CHK).data(Qt.UserRole)

        is_blank_new_row = all(not t for t in texts) and not db_id

        self._is_updating = True
        for i, item in enumerate(items):
            is_missing = not is_blank_new_row and not texts[i]
            apply_required_style(item, is_missing)  # ★ 调用统一工具函数

        # ★ 已拒绝的短链覆盖黄色（在 _is_updating 内，不触发 itemChanged）
        if texts[2] in self._declined_short_urls:
            items[2].setBackground(self.YELLOW_BG)

        self._is_updating = False

    def save_row_to_db(self, row):
        self._is_updating = True
        db_id = self.model.item(row, COL_CHK).data(Qt.UserRole)
        book_name = self.model.item(row, COL_BOOK).text().strip()
        cat = self.model.item(row, COL_CAT).text().strip()
        url = self.model.item(row, COL_URL).text().replace(" (重复资源链接)", "").strip()
        name = self.model.item(row, COL_TITLE).text().strip()  # 标题 → sites.original_title
        source_name = self.model.item(row, COL_PUBLISHER).text().strip()  # 网站域名 → sites.name
        attr = self.model.item(row, COL_ATTR).text().strip()

        code = self.model.item(row, COL_CODE).text().strip()
        crawl_status = self.model.item(row, COL_CRAWL_STATUS).text().strip()
        comp_time = self.model.item(row, COL_COMP_TIME).text().strip()
        display_status = self.model.item(row, COL_RES_STATUS).text().strip()

        res_status = "已失效" if display_status == "已失效" else ""

        if not url and not book_name and not cat and not name and not attr and not comp_time and not source_name:
            if db_id:
                conn = get_conn()
                conn.execute("DELETE FROM sites WHERE id=?", (db_id,))
                conn.commit()
                conn.close()

                self.model.item(row, COL_CHK).setData(None, Qt.UserRole)
                self.model.item(row, COL_CODE).setText("")
                self.update_row_style(row)

            self._is_updating = False
            return

        book_id = None
        if book_name:
            for b_id, b_n, _, _ in self.book_list:
                if b_n == book_name:
                    book_id = b_id
                    break

        conn = get_conn()
        cursor = conn.cursor()
        try:
            if not db_id:
                cursor.execute('''INSERT INTO sites
                                  (category, target_platform, site_url, name, attr, feature_code, complaint_time,
                                   resource_status, book_id, book_name, crawl_status)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                               (cat, cat, url, source_name, attr, code, comp_time, res_status,
                                book_id, book_name, crawl_status))
                conn.commit()
                self.model.item(row, COL_CHK).setData(str(cursor.lastrowid), Qt.UserRole)
            else:
                cursor.execute('''UPDATE sites
                                  SET category=?,
                                      target_platform=?,
                                      site_url=?,
                                      name=?,
                                      attr=?,
                                      feature_code=?,
                                      complaint_time=?,
                                      resource_status=?,
                                      book_id=?,
                                      book_name=?,
                                      crawl_status=?
                                  WHERE id = ?''',
                               (cat, cat, url, source_name, attr, code, comp_time, res_status,
                                book_id, book_name, crawl_status, db_id))
                conn.commit()
        except sqlite3.IntegrityError:
            pass
        finally:
            conn.close()
            self._is_updating = False

    def delete_site(self):
        selected_rows = set()
        for index in self.table.selectionModel().selectedRows():
            selected_rows.add(index.row())
        if not selected_rows: return

        reply = QMessageBox.question(self, "确认删除", f"确定要删除选中的 {len(selected_rows)} 行记录吗？",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes: return

        conn = get_conn()
        for proxy_row in sorted(list(selected_rows), reverse=True):
            proxy_idx = self.proxy_model.index(proxy_row, COL_CHK)
            source_idx = self.proxy_model.mapToSource(proxy_idx)
            real_row = source_idx.row()
            db_id = self.model.item(real_row, COL_CHK).data(Qt.UserRole)
            if db_id:
                conn.execute("DELETE FROM sites WHERE id=?", (db_id,))
        conn.commit()
        conn.close()
        self.refresh_table()

    def refresh_table(self):
        self._is_updating = True
        # ★ refresh 后 row 序号会变，清空解析中/待弹窗集合，避免写到错误行
        self._short_url_resolving.clear()
        self._pending_short_rows.clear()

        self.model.removeRows(0, self.model.rowCount())

        target_cats = self.cat_map.get(self._current_cat, ["全部资源"])

        self.report_btn.show()

        conn = get_conn()
        cursor = conn.cursor()

        # ★ 第 4 位 sites.name（网站域名），第 12 位 sites.original_title（标题）
        query_base = ("SELECT id, target_platform, site_url, name, attr, feature_code, "
                      "complaint_time, resource_status, book_id, book_name, crawl_status, original_title FROM sites")

        if target_cats == ["全部资源"]:
            records = cursor.execute(f"{query_base} ORDER BY id ASC").fetchall()
        else:
            placeholders = ','.join(['?'] * len(target_cats))
            query = f"{query_base} WHERE (target_platform IN ({placeholders}) OR target_platform IS NULL OR target_platform = '') ORDER BY id ASC"
            params = list(target_cats)
            records = cursor.execute(query, params).fetchall()

        all_features_query = "SELECT feature_code, COUNT(*) FROM sites GROUP BY feature_code"
        feature_counts = dict(cursor.execute(all_features_query).fetchall())

        for row_data in records:
            (db_id, cat, url, source_name, attr, code, comp_time, res_status,
             b_id, b_name, crawl_status, title) = row_data

            book_display = ""
            if b_id:
                for bid, bname, _, _ in self.book_list:
                    if bid == b_id:
                        book_display = bname
                        break
            elif b_name:
                book_display = b_name

            chk_item = QStandardItem()
            chk_item.setCheckable(True)
            chk_item.setCheckState(Qt.Unchecked)
            chk_item.setData(str(db_id), Qt.UserRole)
            chk_item.setData(True, Qt.UserRole + 1)

            item_book = QStandardItem(book_display)
            item_cat = QStandardItem(cat or "")
            url_item = QStandardItem(url or "")

            # ★ 网站域名（来源名，可编辑）→ sites.name
            item_publisher = QStandardItem(source_name or "")
            item_publisher.setEditable(True)

            item_attr = QStandardItem(attr or "")

            if feature_counts.get(code, 0) > 1:
                url_item.setText(f"{url} (重复资源链接)")
                url_item.setForeground(QColor("red"))
                url_item.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))

            display_status = ""
            if not (comp_time and str(comp_time).strip()):
                display_status = "已失效" if res_status == "已失效" else ""

            item_res_status = QStandardItem(display_status)

            # ★ 标题只读 → sites.original_title
            item_title = QStandardItem(title or "")
            item_title.setEditable(False)

            self.model.appendRow([
                chk_item, item_book, item_cat, url_item, item_publisher,
                item_attr, QStandardItem(code or ""), QStandardItem(crawl_status or ""),
                QStandardItem(comp_time or ""), item_res_status, item_title,
            ])

            # ★ 黄色由 update_row_style 统一判断（基于 _declined_short_urls）
            self.update_row_style(self.model.rowCount() - 1)

        conn.close()

        for _ in range(15):
            chk_item = QStandardItem()
            chk_item.setCheckable(True)
            chk_item.setCheckState(Qt.Unchecked)
            chk_item.setData(None, Qt.UserRole)
            self.model.appendRow([chk_item] + [QStandardItem("") for _ in range(len(TABLE_HEADERS) - 1)])

        if not getattr(self, '_initialized_filter', False):
            self._apply_filters_to_proxy()
            self._initialized_filter = True

        self.proxy_model.invalidateFilter()
        self.table.clearSelection()
        self._is_updating = False

        self._update_stats()

    def append_site_row(self, record, duplicate):
        prev = self._is_updating
        self._is_updating = True
        try:
            insert_pos = max(0, self.model.rowCount() - 15)

            chk_item = QStandardItem()
            chk_item.setCheckable(True)
            chk_item.setCheckState(Qt.Unchecked)
            chk_item.setData(str(record.get("db_id") or ""), Qt.UserRole)
            chk_item.setData(True, Qt.UserRole + 1)

            url_text = record.get("site_url") or ""
            url_item = QStandardItem(url_text)
            if duplicate:
                url_item.setText(f"{url_text} (重复资源链接)")
                url_item.setForeground(QColor("red"))
                url_item.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))

            # ★ 网站域名（来源名，可编辑）← draft.source_site
            item_publisher = QStandardItem(record.get("source_site") or "")
            item_publisher.setEditable(True)

            # ★ 标题 ← draft.name（网页标题）
            item_title = QStandardItem(record.get("name") or "")
            item_title.setEditable(False)

            row_items = [
                chk_item,
                QStandardItem(record.get("book_name") or ""),
                QStandardItem(record.get("category") or ""),
                url_item,
                item_publisher,
                QStandardItem(record.get("attr") or ""),
                QStandardItem(record.get("feature_code") or ""),
                QStandardItem(record.get("crawl_status") or ""),
                QStandardItem(record.get("complaint_time") or ""),
                QStandardItem(record.get("resource_status") or ""),
                item_title,
            ]
            self.model.insertRow(insert_pos, row_items)

            # ★ 黄色由 update_row_style 统一判断
            self.update_row_style(insert_pos)
            self._is_updating = True

            self.proxy_model.invalidateFilter()
            self._update_stats()
        finally:
            self._is_updating = prev