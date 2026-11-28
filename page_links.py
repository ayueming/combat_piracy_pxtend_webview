# page_links.py
import os
import subprocess
import json
from contextlib import contextmanager
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QHeaderView, QMessageBox, QButtonGroup, QAbstractItemView,
                               QFileDialog, QTableView, QDialog,
                               QStyledItemDelegate, QStyle, QScrollArea, QFrame,
                               QCheckBox, QMenu, QToolTip, QApplication)
from PySide6.QtCore import (
    Qt, QSettings, QEvent, QTimer, QModelIndex, QPoint, Signal,
)

from PySide6.QtGui import (
    QColor, QStandardItemModel,
    QPainter, QBrush, QPalette,
)

from feature_extractor import extract_pure_feature, get_platform_category

from complaint_service import prepare_complaints_data

from url_invalid_batch import (
    UrlInvalidCheckDialog,
    collect_checkable_tasks,
    apply_invalid_results,
)

from app_data import PLATFORM_CONFIG, ALL_SUB_PLATFORMS, CAT_TO_CODE_MAP, PLATFORM_OTHER
from app_data.complaint_platform_consts import HIDDEN_MAIN_CATEGORIES, HIDDEN_SUB_PLATFORMS
from app_data.resource_status_consts import ALL_RESOURCE_STATUSES
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
    handle_check_column_click,
    SelectionCheckSync,
    StyledCheckBox,
)

from ui_styles import (
    BTN_SECONDARY,
    BTN_SECONDARY_DARK,
    BTN_PRIMARY,
    BTN_PRIMARY_OUTLINE,
    BTN_DANGER,
    BTN_DANGER_OUTLINE,
    BTN_TOGGLE,
    BTN_TOGGLE_MAIN,
    TABLE_BASE,
    TABLE_HEADER_BASE,        # ★ 新增
    MENU_BASE,
)

from app_signals import signals

from app_data.link_columns import (
    TABLE_HEADERS,
    DUP_URL_SUFFIX,
    COL_CHK, COL_BOOK, COL_CAT, COL_URL, COL_PUBLISHER,
    COL_ATTR, COL_HANDLE, COL_CODE, COL_CRAWL_STATUS, COL_PICKER_STATUS,
    COL_COMP_TIME, COL_RES_STATUS, COL_TITLE,
)

from link_handle_status import (
    compute_handle_status,
    recalculate_handle_statuses,
    HANDLE_ALL,
)

from links_row_builder import (
    apply_handle_style,
    build_blank_row,
    build_link_row,
    set_url_duplicate_style,
)
from links_import_export import ImportLinksDialog, export_sites_to_excel
from links_url_guard import LinksUrlGuard, SHORT_URL_YELLOW
from links_repository import (
    delete_link,
    delete_links,
    fetch_book_platform_ids,
    fetch_books,
    fetch_feature_counts,
    fetch_links,
    has_duplicate_feature,
    save_link,
)

# 表格底部常驻的空白占位行，供直接录入新链接
TRAILING_BLANK_ROWS = 15


class KeepForegroundDelegate(QStyledItemDelegate):
    """选中状态下仍保留 item 自定义前景色（如重复链标红），不被 HighlightedText 覆盖。"""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if not (option.state & QStyle.State_Selected):
            return
        fg = index.data(Qt.ForegroundRole)
        if fg is None:
            return
        color = fg.color() if isinstance(fg, QBrush) else fg
        if color.isValid():
            option.palette.setColor(QPalette.HighlightedText, color)


class LinksTableView(QTableView):
    # ★ 右键菜单：请求对当前行做链接操作（视图索引 + 屏幕坐标）
    urlMenuRequested = Signal(QModelIndex, QPoint)

    def contextMenuEvent(self, event):
        index = self.indexAt(event.pos())
        if index.isValid():
            self.urlMenuRequested.emit(index, event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def mousePressEvent(self, event):
        # ★ 调用公共拦截函数
        if handle_check_column_click(self, event, chk_col=COL_CHK):
            event.accept()
            return

        index = self.indexAt(event.pos())
        if index.isValid():
            if index.column() in (COL_BOOK, COL_CAT, COL_RES_STATUS):
                # 防误触：未选中的行第一次点击只选中，再点一次才展开下拉框
                if self.selectionModel().isRowSelected(index.row(), index.parent()):
                    self.edit(index)
                else:
                    self.setCurrentIndex(index)
                event.accept()
                return

        super().mousePressEvent(event)



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

        # ★ 链接录入守卫：离线规整 + 短链展开（必须在 setup_ui 之前，setup_ui 内会 refresh_table）
        self.url_guard = LinksUrlGuard(
            self,
            item_provider=lambda row: self.model.item(row, COL_URL),
            url_applier=self._apply_url_from_guard,
            suspend=self._suspend,
            is_editing=lambda: self.table.state() == QAbstractItemView.EditingState,
        )

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
        action_layout.addWidget(HintButton("带有淡红底色的单元格为必填项未填写。点击资源状态列可展开修改。右键资源行可预览/复制链接。"))

        action_layout.addStretch()

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #666; font-size: 13px;")
        action_layout.addWidget(self.stats_label)

        # 扩展投诉开关
        self.expand_search_cb = StyledCheckBox("扩展投诉")
        self.expand_search_cb.setChecked(True)
        self.expand_search_cb.setToolTip(
            "勾选后，属于搜索类的投诉会自动扩展到所有搜索引擎。\n"
            "例如：勾选一条「百度搜索」记录，会自动生成「夸克搜索」「uc搜索」的投诉批次。"
        )
        action_layout.addWidget(self.expand_search_cb)

        self.report_btn = QPushButton(" 一键发起投诉 (勾选)")
        self.report_btn.setStyleSheet(BTN_DANGER)
        self.report_btn.clicked.connect(self.send_to_complaints)
        action_layout.addWidget(self.report_btn)

        check_invalid_btn = QPushButton("检查投诉结果")
        check_invalid_btn.setStyleSheet(BTN_PRIMARY_OUTLINE)
        check_invalid_btn.setToolTip(
            "自动判断链接是否失效。\n"
            "并结合投诉时间，判断投诉是否成功。"
        )
        check_invalid_btn.clicked.connect(self.check_complaint_status)
        action_layout.addWidget(check_invalid_btn)

        import_btn = QPushButton("批量导入表格")
        import_btn.setStyleSheet(BTN_SECONDARY)
        import_btn.clicked.connect(self.open_import_dialog)
        action_layout.addWidget(import_btn)

        export_btn = QPushButton("导出勾选数据")
        export_btn.setStyleSheet(BTN_SECONDARY)
        export_btn.clicked.connect(self.export_to_excel)
        action_layout.addWidget(export_btn)

        del_btn = QPushButton("删除选中")
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

        custom_header = FilterHeaderView(
            Qt.Horizontal, self.table,
            value_order_by_column={
                COL_RES_STATUS: ALL_RESOURCE_STATUSES,
                COL_HANDLE: HANDLE_ALL,
            },
        )
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
        self.table.setColumnWidth(COL_HANDLE, 90)
        self.table.setColumnWidth(COL_RES_STATUS, 100)
        self.table.setColumnWidth(COL_COMP_TIME, 120)
        self.table.setColumnWidth(COL_CRAWL_STATUS, 100)
        self.table.setColumnWidth(COL_PICKER_STATUS, 100)
        self.table.setColumnWidth(COL_CODE, 120)
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
        self.table.setItemDelegateForColumn(
            COL_RES_STATUS,
            StatusComboDelegate(self.table),
        )
        self.table.setItemDelegateForColumn(COL_URL, KeepForegroundDelegate(self.table))

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
        self.table.urlMenuRequested.connect(self.on_url_menu_requested)
        # ★ 兜底：URL 列的 delegate 提交数据后再检查一次（itemChanged 可能被挡）
        url_delegate = self.table.itemDelegateForColumn(COL_URL)
        if url_delegate is not None:
            url_delegate.commitData.connect(self._on_editor_committed)

        self.col_persister = ColumnWidthPersister(
            view=self.table,
            settings=self.settings,
            key="links_col_widths_v3",
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

        raw = self.settings.value("links_filters_by_cat_v3", "")
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
        self.settings.setValue("links_filters_by_cat_v3", json.dumps(self.filters_by_cat))
        self._update_stats()

    def _update_stats(self):
        total_count = max(0, self.model.rowCount() - TRAILING_BLANK_ROWS)
        visible_count = max(0, self.proxy_model.rowCount() - TRAILING_BLANK_ROWS)
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
        ImportLinksDialog(self, on_imported=self.refresh_table).exec()

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

        default_name = f"盗版链接导出_{self._current_cat}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "保存Excel文件", default_name, "Excel 文件 (*.xlsx)")
        if not path:
            return

        # 导入端强校验「晋江书号 ↔ 书名」，导出必须带上书号才能原样导回
        platform_ids = fetch_book_platform_ids()

        rows = []
        for r in sorted(checked_rows):
            book_name = self.model.item(r, COL_BOOK).text().strip()
            rows.append({
                "platform_id": platform_ids.get(book_name, ""),
                "book_name": book_name,
                "category": self.model.item(r, COL_CAT).text().strip(),
                "site_url": self.model.item(r, COL_URL).text().strip(),
                "source_site": self.model.item(r, COL_PUBLISHER).text().strip(),
                "attr": self.model.item(r, COL_ATTR).text().strip(),
                "complaint_time": self.model.item(r, COL_COMP_TIME).text().strip(),
                "resource_status": self.model.item(r, COL_RES_STATUS).text().strip(),
            })

        try:
            export_sites_to_excel(rows, path)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"生成 Excel 时出错：{e}")
            return

        missing_pid = sum(1 for row in rows if not row["platform_id"])
        msg = f"已成功导出 {len(rows)} 条勾选数据！"
        if missing_pid:
            msg += (f"\n\n⚠️ 其中 {missing_pid} 条没能匹配到「晋江书号」"
                    f"（关联作品为空或不在作品库里），这些行重新导入时会被跳过。")
        QMessageBox.information(self, "导出成功", msg)
        subprocess.Popen(f'explorer "{os.path.dirname(path)}"')

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

        # ★ 核心：把「勾选 ∪ 选中」的行集合与扩展开关一起交给 Service
        expand = self.expand_search_cb.isChecked()
        batches, error_report = prepare_complaints_data(
            self.model, self.book_list,
            expand_search=expand,
            target_rows=target_rows,
        )

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

    def check_complaint_status(self):
        """按钮入口：① 判断资源状态 ② 基于资源状态 + 投诉时间重算处理状况。"""
        tasks, stats = collect_checkable_tasks()
        counts, written = {}, 0

        # ---------- 第一步：资源状态 ----------
        if tasks:
            web_n = sum(1 for t in tasks if t.kind == "website")
            no_rule_n = sum(1 for t in tasks if t.kind == "no_rule")
            chk_n = len(tasks) - web_n - no_rule_n
            reply = QMessageBox.question(
                self, "确认",
                f"即将处理 {len(tasks)} 条链接：\n\n"
                f"  · 联网判断：{chk_n} 条\n"
                # f"  其中百度贴吧走浏览器渲染，单条约 3~15 秒\n"
                f"  · 网站类：{web_n} 条\n"
                f"  · 暂无判断模块：{no_rule_n} 条\n\n"
                f"联网检查会发起网络请求，可能需要几分钟，是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return

            dlg = UrlInvalidCheckDialog(tasks, self)
            if dlg.exec() != QDialog.Accepted:
                return

            counts = apply_invalid_results(dlg.results)
            written = sum(counts.values())

        # ---------- 第二步：处理状况（增量重算：已完成跳过，其余重算并写库） ----------
        updated, scanned, summary = recalculate_handle_statuses()
        self.refresh_table()

        lines = []
        if tasks:
            if written:
                lines.append(f"① 资源状态：已写入 {written} 条")
                lines += [f"     · {label}：{n} 条" for label, n in counts.items() if n]
            else:
                lines.append("① 资源状态：本次没有可写入的结果")
        else:
            lines.append("① 资源状态：没有待判断的链接（已全部处理）")
            lines.append(f"     数据库共 {stats['total']} 条，终态 {stats['has_status']} 条，"
                         f"空链接 {stats['empty_url']} 条")
            if stats["domain_index_empty"]:
                lines.append("     ⚠️ 内部异常：域名索引为空，请检查规则表 key 与域名配置。")

        lines.append("")
        lines.append(f"② 处理状况：重算 {scanned} 条（已完成跳过），更新 {updated} 条")
        lines += [f"     · {label}：{n} 条" for label, n in summary.items()]
        QMessageBox.information(self, "检查完成", "\n".join(lines))

    def refresh_books(self):
        self.book_list = fetch_books()

    def _refresh_handle_cell(self, row):
        """按当前行的资源状态 + 投诉时间，重算只读的「处理状况」单元格。"""
        handle_item = self.model.item(row, COL_HANDLE)
        if not handle_item:
            return
        status_item = self.model.item(row, COL_RES_STATUS)
        time_item = self.model.item(row, COL_COMP_TIME)
        text = compute_handle_status(
            status_item.text().strip() if status_item else "",
            time_item.text().strip() if time_item else "",
        )
        self._is_updating = True
        try:
            handle_item.setText(text)
            apply_handle_style(handle_item, text)
        finally:
            self._is_updating = False

    def on_item_changed(self, item):
        if self._is_updating: return

        row = item.row()
        col = item.column()

        # ★ 资源状态 / 投诉时间改动后，同步重算只读的「处理状况」
        if col in (COL_RES_STATUS, COL_COMP_TIME):
            self._refresh_handle_cell(row)

        self.update_row_style(row)

        if col == COL_CHK:
            self.table.horizontalHeader().viewport().update()
            return

        if col == COL_URL:
            raw_url = item.text().strip()

            # ★ 入链规整（离线部分）：等这次编辑提交完再询问。
            #   不能在 itemChanged / closeEditor 栈里直接弹模态框，
            #   否则编辑器提交过程会被打断，表现为“回车后没反应”。
            self.url_guard.schedule_offline_fix(row, raw_url)

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
            is_duplicate = has_duplicate_feature(pure_code, db_id)

            set_url_duplicate_style(item, is_duplicate)
            self._is_updating = False

            # ★ 短链检测调度（不阻断现有流程，用原始 raw_url 判断）
            self.url_guard.schedule_short_url_check(row, raw_url)

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

    # ================= 资源链接：入链规整 / 双击预览 =================
    def _on_editor_committed(self, _editor):
        """编辑器提交（回车/失焦）后的兜底检查。

        QAbstractItemView.commitData 在 delegate 写回模型之后触发；
        即使 itemChanged 被 _is_updating 挡住，这里也能把规整补上。
        """
        index = self.table.currentIndex()
        if not index.isValid() or index.column() != COL_URL:
            return
        src = self.proxy_model.mapToSource(index)
        item = self.model.item(src.row(), COL_URL)
        if item:
            self.url_guard.schedule_offline_fix(src.row(), item.text().strip())

    def on_url_menu_requested(self, proxy_index, global_pos):
        """右键资源行：预览链接 / 复制链接。"""
        src_index = self.proxy_model.mapToSource(proxy_index)
        if not src_index.isValid():
            return
        row = src_index.row()

        url_item = self.model.item(row, COL_URL)
        url = url_item.text().replace(DUP_URL_SUFFIX, "").strip() if url_item else ""
        if not url:
            return

        menu = QMenu(self)
        # ★ 页面自身的白色底样式会级联进来，导致悬停高亮「白底白字」，必须显式给菜单配色
        menu.setStyleSheet(MENU_BASE)
        act_preview = menu.addAction("预览链接")
        act_copy = menu.addAction("复制链接")
        chosen = menu.exec(global_pos)
        if chosen is act_preview:
            self._open_link_preview(row, url)
        elif chosen is act_copy:
            QApplication.clipboard().setText(url)
            QToolTip.showText(global_pos, "已复制链接")

    def _open_link_preview(self, row, url):
        """打开独立预览窗口（不占用拾取锁）。"""
        book_item = self.model.item(row, COL_BOOK)
        book_name = book_item.text().strip() if book_item else ""

        try:
            from link_preview import LinkPreviewDialog
        except Exception as e:
            QMessageBox.critical(self, "无法打开预览", f"预览组件加载失败：\n{e}")
            return

        LinkPreviewDialog(self, url, book_name=book_name).exec()

    @contextmanager
    def _suspend(self):
        """临时挂起 itemChanged 连锁（改样式/批量回填时用），可安全嵌套。"""
        prev = self._is_updating
        self._is_updating = True
        try:
            yield
        finally:
            self._is_updating = prev

    def _apply_url_from_guard(self, row, url):
        """链接守卫确定要替换后，把新 URL 写回单元格。

        这里只负责「静默清黄 + setText」：setText 会触发 on_item_changed，
        让分类识别 / 特征码 / 查重 / 入库按新链接完整重跑一遍。
        守卫自身会在调用期间 mute 短链检测，避免自我循环。
        """
        item = self.model.item(row, COL_URL)
        if item is None:
            return
        with self._suspend():
            item.setBackground(QBrush())
        item.setText(url)

    def update_row_style(self, row):
        items = [self.model.item(row, c) for c in [COL_BOOK, COL_CAT, COL_URL]]
        if not all(items): return

        texts = [it.text().replace(DUP_URL_SUFFIX, "").strip() for it in items]
        db_id = self.model.item(row, COL_CHK).data(Qt.UserRole)

        is_blank_new_row = all(not t for t in texts) and not db_id

        self._is_updating = True
        for i, item in enumerate(items):
            is_missing = not is_blank_new_row and not texts[i]
            apply_required_style(item, is_missing)  # ★ 调用统一工具函数

        # ★ 已拒绝展开的短链覆盖黄色（在 _is_updating 内，不触发 itemChanged）
        if self.url_guard.is_declined(texts[2]):
            items[2].setBackground(SHORT_URL_YELLOW)

        self._is_updating = False

    def save_row_to_db(self, row):
        self._is_updating = True
        db_id = self.model.item(row, COL_CHK).data(Qt.UserRole)
        book_name = self.model.item(row, COL_BOOK).text().strip()
        cat = self.model.item(row, COL_CAT).text().strip()
        url = self.model.item(row, COL_URL).text().strip()
        name = self.model.item(row, COL_TITLE).text().strip()  # 标题 → sites.original_title
        source_name = self.model.item(row, COL_PUBLISHER).text().strip()  # 发布方 → sites.name
        attr = self.model.item(row, COL_ATTR).text().strip()

        code = self.model.item(row, COL_CODE).text().strip()
        crawl_status = self.model.item(row, COL_CRAWL_STATUS).text().strip()
        picker_status = self.model.item(row, COL_PICKER_STATUS).text().strip()
        comp_time = self.model.item(row, COL_COMP_TIME).text().strip()
        display_status = self.model.item(row, COL_RES_STATUS).text().strip()
        handle_text = self.model.item(row, COL_HANDLE).text().strip() or None

        # 只有下拉框里的状态才写；其它（空 / 用户清空）一律保留 DB 原值
        res_status = display_status if display_status in ALL_RESOURCE_STATUSES else None

        if not url and not book_name and not cat and not name and not attr and not comp_time and not source_name:
            if db_id:
                delete_link(db_id)

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

        try:
            new_id = save_link({
                "category": cat,
                "site_url": url,
                "source_site": source_name,
                "attr": attr,
                "feature_code": code,
                "complaint_time": comp_time,
                "resource_status": res_status,
                "handle_status": handle_text,
                "book_id": book_id,
                "book_name": book_name,
                "crawl_status": crawl_status,
                "picker_status": picker_status,
            }, db_id=db_id)
        finally:
            self._is_updating = False

        # 新入库的行要把主键回填到勾选列，否则下次编辑会被当成新行重复插入
        if not db_id and new_id:
            self.model.item(row, COL_CHK).setData(new_id, Qt.UserRole)

    def delete_site(self):
        selected_rows = set()
        for index in self.table.selectionModel().selectedRows():
            selected_rows.add(index.row())
        if not selected_rows: return

        reply = QMessageBox.question(self, "确认删除", f"确定要删除选中的 {len(selected_rows)} 行记录吗？",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes: return

        ids = []
        for proxy_row in selected_rows:
            proxy_idx = self.proxy_model.index(proxy_row, COL_CHK)
            source_idx = self.proxy_model.mapToSource(proxy_idx)
            db_id = self.model.item(source_idx.row(), COL_CHK).data(Qt.UserRole)
            if db_id:
                ids.append(db_id)
        delete_links(ids)
        self.refresh_table()

    def refresh_table(self):
        self._is_updating = True
        # ★ refresh 后 row 序号会变，清空解析中/待弹窗状态，避免写错行
        self.url_guard.reset()

        self.model.removeRows(0, self.model.rowCount())

        target_cats = self.cat_map.get(self._current_cat, ["全部资源"])

        self.report_btn.show()

        # 全部资源 = 不过滤；其余按具体分类（含空分类）筛选
        records = fetch_links(None if target_cats == ["全部资源"] else target_cats)
        feature_counts = fetch_feature_counts()

        for record in records:
            self.model.appendRow(build_link_row(
                record, duplicate=feature_counts.get(record["feature_code"], 0) > 1))

            # ★ 黄色由 update_row_style 统一判断（基于 url_guard.is_declined）
            self.update_row_style(self.model.rowCount() - 1)

        for _ in range(TRAILING_BLANK_ROWS):
            self.model.appendRow(build_blank_row())

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
            insert_pos = max(0, self.model.rowCount() - TRAILING_BLANK_ROWS)
            self.model.insertRow(insert_pos, build_link_row(record, duplicate=duplicate))

            # ★ 黄色由 update_row_style 统一判断
            self.update_row_style(insert_pos)
            self._is_updating = True

            self.proxy_model.invalidateFilter()
            self._update_stats()
        finally:
            self._is_updating = prev