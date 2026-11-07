# page_author.py
import sqlite3
from datetime import datetime

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QHeaderView, QAbstractItemView, QMessageBox, QSizePolicy)
from PySide6.QtCore import Qt

from db_config import get_conn
from ui_styles import (
    BTN_PRIMARY_OUTLINE, BTN_DANGER_OUTLINE,
    TABLE_BASE, TABLE_HEADER_BASE, HEADER_BG_COLOR, TEXT_DARK,
)


class AuthorPage(QWidget):
    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window
        self._is_updating = False
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title_layout = QHBoxLayout()

        # ★ 1. 把提示文字放在最左边
        tip = QLabel(
            "提示：姓名是关联键，保存后不可修改；如需改名请删除后重建。身份证号不能重复。若不需要智能体投诉功能，可以乱填，但请不要留空。")
        tip.setStyleSheet("color: #999; font-size: 12px;")
        title_layout.addWidget(tip)

        # ★ 2. 加一个弹簧，把后面的按钮挤到右边去
        title_layout.addStretch()

        # 3. 添加按钮（保持原样）
        add_btn = QPushButton("➕ 新增作者")
        add_btn.setStyleSheet(BTN_PRIMARY_OUTLINE)
        add_btn.clicked.connect(self.add_new_row)
        title_layout.addWidget(add_btn)

        del_btn = QPushButton("🗑️ 删除选中")
        del_btn.setStyleSheet(BTN_DANGER_OUTLINE)
        del_btn.clicked.connect(self.delete_author)
        title_layout.addWidget(del_btn)

        layout.addLayout(title_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["权利人姓名", "电话", "身份证号", "邮箱"])
        header = self.table.horizontalHeader()
        header.setFixedHeight(35)
        header.setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setStyleSheet(
            TABLE_BASE + TABLE_HEADER_BASE
            + f"QHeaderView::section:horizontal {{ background-color: {HEADER_BG_COLOR}; }}"
        )
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)
        self.table.itemChanged.connect(self.on_item_changed)
        self.table.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        layout.addWidget(self.table, 1)

        self.refresh_table()

    def refresh_table(self):
        self._is_updating = True
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        conn = get_conn()
        rows = conn.execute(
            "SELECT id, name, phone, id_card, email FROM authors ORDER BY id"
        ).fetchall()
        conn.close()

        for db_id, name, phone, id_card, email in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            name_item = QTableWidgetItem(name or "")
            name_item.setData(Qt.UserRole, db_id)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)  # 姓名只读
            self.table.setItem(row, 0, name_item)

            self.table.setItem(row, 1, QTableWidgetItem(phone or ""))
            self.table.setItem(row, 2, QTableWidgetItem(id_card or ""))
            self.table.setItem(row, 3, QTableWidgetItem(email or ""))

        self.add_blank_row()

        self.table.blockSignals(False)
        self._is_updating = False

    def add_blank_row(self, set_focus=False):
        prev = self._is_updating
        self._is_updating = True
        self.table.blockSignals(True)
        try:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = QTableWidgetItem("")
            name_item.setData(Qt.UserRole, "")
            self.table.setItem(row, 0, name_item)
            for c in range(1, 4):
                self.table.setItem(row, c, QTableWidgetItem(""))
            if set_focus:
                self.table.setCurrentCell(row, 0)
        finally:
            self.table.blockSignals(False)
            self._is_updating = prev

    def add_new_row(self):
        last = self.table.rowCount() - 1
        if last >= 0:
            item = self.table.item(last, 0)
            if item and not item.data(Qt.UserRole) and not item.text().strip():
                self.table.setCurrentCell(last, 0)
                return
        self.add_blank_row(set_focus=True)
        self.table.scrollToBottom()

    def on_item_changed(self, item):
        if self._is_updating:
            return
        row = item.row()
        name_item = self.table.item(row, 0)
        if not name_item:
            return
        name = name_item.text().strip()
        if not name:
            return

        phone = self.table.item(row, 1).text().strip() if self.table.item(row, 1) else ""
        id_card = (self.table.item(row, 2).text().strip() if self.table.item(row, 2) else "") or None
        email = self.table.item(row, 3).text().strip() if self.table.item(row, 3) else ""

        db_id = name_item.data(Qt.UserRole)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = get_conn()
        try:
            if not db_id:
                cur = conn.execute(
                    "INSERT INTO authors (name, phone, id_card, email, updated_at) VALUES (?,?,?,?,?)",
                    (name, phone, id_card, email, now))
                conn.commit()
                self._is_updating = True
                name_item.setData(Qt.UserRole, cur.lastrowid)
                self._is_updating = False

                # 最后一行已不再是空行，补一行新的
                last = self.table.rowCount() - 1
                if last < 0 or self.table.item(last, 0).text().strip():
                    self.add_blank_row()
            else:
                conn.execute(
                    "UPDATE authors SET phone=?, id_card=?, email=?, updated_at=? WHERE id=?",
                    (phone, id_card, email, now, db_id))
                conn.commit()
        except sqlite3.IntegrityError as e:
            msg = str(e).lower()
            if "id_card" in msg:
                QMessageBox.warning(self, "冲突", "身份证号已存在，请检查是否重复录入。")
            elif "name" in msg:
                QMessageBox.warning(self, "冲突", "该姓名已存在。")
            else:
                QMessageBox.warning(self, "保存失败", str(e))
            self.refresh_table()
        finally:
            conn.close()

    def delete_author(self):
        rows = []
        for idx in self.table.selectionModel().selectedRows():
            r = idx.row()
            item = self.table.item(r, 0)
            if item and item.data(Qt.UserRole):
                rows.append((r, item.data(Qt.UserRole), item.text()))
        if not rows:
            QMessageBox.information(self, "提示", "请先选中要删除的作者（已有记录的行）。")
            return

        conn = get_conn()
        try:
            # ★ 先查引用，避免"确认后才发现删不了"
            used = []
            for _, _, name in rows:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM books WHERE rights_holder=?", (name,)
                ).fetchone()[0]
                if cnt:
                    used.append(f"{name}（{cnt} 部作品）")
            if used:
                QMessageBox.warning(
                    self, "无法删除",
                    "以下作者仍被作品引用，请先在【作品库】修改权利人：\n\n"
                    + "\n".join(used)
                )
                return

            names = "、".join(n for _, _, n in rows)
            reply = QMessageBox.question(
                self, "确认删除",
                f"确定删除以下作者？\n{names}\n\n"
                f"注意：删除后该作者在【权利人材料】页的身份证等材料将不可见"
                f"（文件仍保留在硬盘，补录同名作者即可恢复）。",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

            for _, db_id, _ in rows:
                conn.execute("DELETE FROM authors WHERE id=?", (db_id,))
            conn.commit()
        finally:
            conn.close()

        self.refresh_table()