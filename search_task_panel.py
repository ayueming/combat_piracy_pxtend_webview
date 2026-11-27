# search_task_panel.py

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QFrame, QMessageBox,
                               QComboBox)
from PySide6.QtCore import Qt
from search_task_queue import task_queue, queue_signals


class TaskRow(QFrame):
    def __init__(self, data, index):
        super().__init__()
        self.data = data
        self.setObjectName("TaskRow")

        # 获取上次执行时间和状态
        status = data.get("status", "ready")
        last_run = data.get("last_run_at") or ""

        # 纯文本结果映射，无 emoji
        result_text = ""
        result_color = "#6B7280"  # 默认灰
        if status == "failed":
            result_text = "超时"
            result_color = "#EF4444"  # 红色
        elif status == "stopped":
            result_text = "中断"
            result_color = "#F59E0B"  # 橙色
        elif status == "ready" and last_run:
            result_text = "成功"
            result_color = "#10B981"  # 绿色

        # 左侧边框颜色（保留 running 的蓝色提示）
        border_color = "#E5E7EB"
        if status == "running":
            border_color = "#3B82F6"

        self.setStyleSheet(
            f"#TaskRow {{ background: #FFF; border: 1px solid #E5E7EB; border-left: 3px solid {border_color}; border-radius: 4px; }}")
        self.setFixedHeight(85)  # ★ 加高以容纳三行

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)

        # ===== 第一行：主关键词 + 平台页数 + 按钮 =====
        top = QHBoxLayout()
        top.setSpacing(4)

        lbl_index = QLabel(f"{index}.")
        lbl_index.setStyleSheet("color: #9CA3AF; font-size: 12px; font-weight: bold; border: none;")
        top.addWidget(lbl_index)

        # ★ 去掉了书名，改为显示主关键词
        lbl_title = QLabel(f"{data['main_keyword']}  |  {data['platform']} {data['max_pages']}页")
        lbl_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #374151; border: none;")
        top.addWidget(lbl_title)
        top.addStretch()

        btn_run = QPushButton("▶")
        btn_run.setFixedSize(24, 24)
        btn_run.setStyleSheet(
            "QPushButton { background: #ECFDF5; color: #059669; border: 1px solid #A7F3D0; border-radius: 4px; font-weight: bold; } QPushButton:hover { background: #D1FAE5; }")
        btn_run.setCursor(Qt.PointingHandCursor)
        btn_run.clicked.connect(self._on_run)

        btn_del = QPushButton("×")
        btn_del.setFixedSize(24, 24)
        btn_del.setStyleSheet(
            "QPushButton { background: transparent; color: #9CA3AF; border: none; font-size: 18px; font-weight: bold; } QPushButton:hover { color: #EF4444; }")
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.clicked.connect(self._on_del)

        top.addWidget(btn_run)
        top.addWidget(btn_del)
        lay.addLayout(top)

        # ===== 第二行：副关键词 =====
        sub_text = " ".join(data["sub_keywords"]) if data["sub_keywords"] else "无"
        lbl_sub = QLabel(f"副关键词：{sub_text}")
        lbl_sub.setStyleSheet("color: #6B7280; font-size: 11px; border: none; margin-left: 16px;")
        lbl_sub.setToolTip(lbl_sub.text())
        lay.addWidget(lbl_sub)

        # ===== 第三行：上次执行时间和执行结果 =====
        bottom = QHBoxLayout()
        bottom.setContentsMargins(16, 0, 0, 0)  # 左边距对齐

        # 只有跑过（有 last_run）才显示，没跑过留空
        if last_run and result_text:
            lbl_time = QLabel(last_run)
            lbl_time.setStyleSheet("color: #9CA3AF; font-size: 11px; border: none;")
            bottom.addWidget(lbl_time)

            lbl_result = QLabel(result_text)
            lbl_result.setStyleSheet(
                f"color: {result_color}; font-size: 11px; font-weight: bold; border: none; margin-left: 8px;")
            bottom.addWidget(lbl_result)

        bottom.addStretch()
        lay.addLayout(bottom)

    def _on_run(self):
        if self.data["status"] == "running":
            QMessageBox.information(
                self, "提示",
                "该任务当前正在执行中，请勿重复点击。\n如果已卡死，请点击上方的「停止」按钮。"
            )
            return

        task = task_queue.get_task_by_id(self.data["id"])
        if not task:
            QMessageBox.warning(self, "提示", "任务已不存在，请刷新队列。")
            return

        task_queue.start_one(task)  # 内部做 is_running 和草稿检查，失败自己弹窗

    def _on_del(self):
        if self.data["status"] == "running":
            QMessageBox.warning(self, "提示", "该任务正在执行中，无法删除。请先点击「停止」。")
            return
        success = task_queue.remove_task(self.data["id"])
        if not success:
            QMessageBox.warning(self, "提示", "队列正被锁定使用中，无法删除该任务。")


class SearchTaskPanel(QWidget):
    def __init__(self, main_page):
        super().__init__()
        self.main_page = main_page
        self.setup_ui()

        queue_signals.queue_updated.connect(self.refresh_list)
        queue_signals.batch_switched.connect(self._on_batch_switched)
        self.refresh_list()

    def setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self.lbl_queue_title = QLabel("📋 搜索队列 (0)")
        self.lbl_queue_title.setStyleSheet("font-weight: bold; color: #374151; font-size: 13px;")
        header.addWidget(self.lbl_queue_title)

        # ★ 作品筛选下拉
        self.book_filter_combo = QComboBox()
        self.book_filter_combo.setFixedWidth(120)
        self.book_filter_combo.setStyleSheet("""
            QComboBox { border: 1px solid #E5E7EB; border-radius: 4px;
                        padding: 2px 8px; background: #FFF;
                        font-size: 12px; color: #374151; }
            QComboBox:hover { border: 1px solid #D1D5DB; }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox::down-arrow {
                image: none;
                border-left: 3px solid transparent;
                border-right: 3px solid transparent;
                border-top: 4px solid #6B7280;
                width: 0; height: 0; margin-right: 4px;
            }
        """)
        self.book_filter_combo.addItem("全部作品", None)  # None 代表"全部"
        self.book_filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        header.addWidget(self.book_filter_combo)

        header.addStretch()

        self.btn_add = QPushButton("＋ 添加")
        self.btn_add.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #3B82F6; font-size: 12px; font-weight: bold; padding: 0; } QPushButton:hover { color: #2563EB; }")
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.clicked.connect(self._add_current)

        self.lbl_div = QLabel("|")
        self.lbl_div.setStyleSheet("color: #D1D5DB; font-size: 12px;")

        self.btn_run_all = QPushButton("▶ 执行全部")
        self.btn_run_all.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #10B981; font-size: 12px; font-weight: bold; padding: 0; } QPushButton:hover { color: #059669; }")
        self.btn_run_all.setCursor(Qt.PointingHandCursor)
        self.btn_run_all.clicked.connect(self._run_all)

        self.btn_stop = QPushButton("⏸ 强制停止")
        self.btn_stop.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #EF4444; font-size: 12px; font-weight: bold; padding: 0; } QPushButton:hover { color: #DC2626; }")
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.clicked.connect(lambda: task_queue.stop("user"))
        self.btn_stop.hide()

        header.addWidget(self.btn_add)
        header.addWidget(self.lbl_div)
        header.addWidget(self.btn_run_all)
        header.addWidget(self.btn_stop)

        lay.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)

        self.scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { border: none; background: transparent; width: 6px; margin: 0px; }
            QScrollBar::handle:vertical { background: #D1D5DB; min-height: 20px; border-radius: 3px; }
            QScrollBar::handle:vertical:hover { background: #9CA3AF; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
        """)

        self.list_container = QWidget()
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.setAlignment(Qt.AlignTop)

        self.scroll.setWidget(self.list_container)
        lay.addWidget(self.scroll, 1)

    def _on_filter_changed(self, _idx):
        self.refresh_list()

    def _rebuild_book_filter(self, tasks):
        """根据当前队列重建下拉，保留原选中项；选中的作品已被清空则回落到“全部”。"""
        current = self.book_filter_combo.currentData()
        self.book_filter_combo.blockSignals(True)
        self.book_filter_combo.clear()
        self.book_filter_combo.addItem("全部作品", None)
        seen = set()
        for t in tasks:
            bid = t.get("book_id")
            if bid in seen:
                continue
            seen.add(bid)
            self.book_filter_combo.addItem(t.get("book_name") or f"作品{bid}", bid)
        idx = self.book_filter_combo.findData(current)
        self.book_filter_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.book_filter_combo.blockSignals(False)

    def refresh_list(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_tasks = task_queue.get_tasks()

        # ★ 重建下拉（保留用户当前选择）
        self._rebuild_book_filter(all_tasks)

        # ★ 应用筛选
        selected_bid = self.book_filter_combo.currentData()  # None = 全部
        if selected_bid is None:
            tasks = all_tasks
            self.lbl_queue_title.setText(f"📋 搜索队列 ({len(all_tasks)})")
        else:
            tasks = [t for t in all_tasks if t.get("book_id") == selected_bid]
            self.lbl_queue_title.setText(f"📋 搜索队列 ({len(tasks)}/{len(all_tasks)})")

        if not tasks:
            lbl = QLabel("当前筛选下没有任务" if selected_bid is not None
                         else "队列为空，请配置搜索项后点击「添加」")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color: #9CA3AF; font-size: 12px; margin-top: 20px;")
            self.list_layout.addWidget(lbl)
        else:
            for i, t in enumerate(tasks):
                self.list_layout.addWidget(TaskRow(t, i + 1))

        if task_queue.is_running:
            self.btn_run_all.hide()
            self.btn_add.hide()
            self.lbl_div.hide()
            self.btn_stop.show()
            self.book_filter_combo.setEnabled(False)  # ★ 运行时禁用，避免误改
        else:
            self.btn_run_all.show()
            self.btn_stop.hide()
            self.btn_add.show()
            self.lbl_div.show()
            self.book_filter_combo.setEnabled(True)

    def _add_current(self):
        if not self.main_page: return
        book_id = self.main_page.input_panel.book_combo.currentData()
        book_name = self.main_page.input_panel.book_combo.currentText()
        platform = self.main_page.input_panel.platform_combo.currentText()
        main_kw = self.main_page.input_panel.main_kw_input.text().strip()

        if not book_name or not main_kw:
            QMessageBox.warning(self, "提示", "请先在主界面选择作品和主关键词。")
            return

        sub_kws = list(self.main_page.input_panel.selected_sub_kws)
        max_pages = int(self.main_page.input_panel.page_limit_combo.currentText().replace("页", ""))

        success = task_queue.add_task(book_id, book_name, platform, main_kw, sub_kws, max_pages)
        if not success:
            QMessageBox.warning(
                self, "无法添加",
                "队列中已存在完全相同的检索任务（同书籍、同平台、同关键词）。\n\n"
                "为防止频繁请求触发搜索引擎风控，请勿重复添加。\n"
                "如需更改页数，请点击该任务右侧的删除键（×），重新添加即可。"
            )

    def _run_all(self):
        bid = self.book_filter_combo.currentData()  # None = 全部
        if bid is None:
            task_queue.start_all()
            return

        # 先校验筛选结果非空，给用户明确反馈
        visible = [t for t in task_queue.get_tasks() if t.get("book_id") == bid]
        if not visible:
            QMessageBox.information(self, "提示", "当前筛选下没有可执行的任务。")
            return

        task_queue.start_all(task_filter=lambda t: t.get("book_id") == bid)

    def _on_batch_switched(self, batch_id, book_name):
        if not self.main_page: return
        idx = self.main_page.input_panel.book_combo.findText(book_name)
        if idx >= 0:
            self.main_page.input_panel.book_combo.setCurrentIndex(idx)
        if hasattr(self.main_page, "result_panel"):
            self.main_page.result_panel.current_batch_id = batch_id
            self.main_page.result_panel.refresh_tables()