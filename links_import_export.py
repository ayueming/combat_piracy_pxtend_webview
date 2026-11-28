# links_import_export.py

"""盗版链接的表格导入 / 导出（与界面解耦，便于复用和单独测试）。

从 page_links.py 搬出来的三块：
- write_import_template(path)      生成「批量导入」用的空模板
- export_sites_to_excel(rows, path) 把勾选数据导出成 xlsx
- ImportLinksDialog                模板下载 + 上传导入对话框

导入校验逻辑仍在 data_importer_links.import_sites_from_file，本模块只负责文件与界面。
"""

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QGroupBox, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)
from openpyxl.utils import get_column_letter

from data_importer_links import import_sites_from_file
from ui_styles import BTN_PRIMARY, BTN_SECONDARY_DARK

SHEET_TITLE = "盗版链接"

# 导入模板与导出文件共用同一套表头，保证导出的表能原样再导回去。
# ★ 必须包含「晋江书号」：data_importer_links 会严格校验 书号 ↔ 书名 是否匹配，
#   缺了书号列整张表都会被判为「书号不存在」。
HEADERS = [
    "晋江书号(必填)", "关联作品(必填)", "资源分类(必填)", "资源链接(必填)",
    "发布方", "属性", "投诉时间", "状态",
]

# 导出时的列宽；模板用统一宽度
_EXPORT_COLUMN_WIDTHS = [15, 20, 15, 50, 20, 20, 20, 15]
_TEMPLATE_COLUMN_WIDTH = 20


def write_import_template(path):
    """写出一份只有表头的标准导入模板。异常交给调用方处理。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_TITLE
    for col_idx, header in enumerate(HEADERS, 1):
        ws.cell(row=1, column=col_idx, value=header)
    for col_idx in range(1, len(HEADERS) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = _TEMPLATE_COLUMN_WIDTH
    wb.save(path)


def export_sites_to_excel(rows, path):
    """rows: [{platform_id, book_name, category, site_url, source_site, attr,
               complaint_time, resource_status}]"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_TITLE

    for col_idx, header in enumerate(HEADERS, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    for row_idx, row in enumerate(rows, start=2):
        values = [
            row.get("platform_id") or "",
            row.get("book_name") or "",
            row.get("category") or "",
            row.get("site_url") or "",
            row.get("source_site") or "",
            row.get("attr") or "",
            row.get("complaint_time") or "",
            row.get("resource_status") or "",
        ]
        for col_idx, value in enumerate(values, 1):
            ws.cell(row=row_idx, column=col_idx, value=value)

    for col_idx, width in enumerate(_EXPORT_COLUMN_WIDTHS, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    wb.save(path)


class ImportLinksDialog(QDialog):
    """两步式导入对话框：下载模板 → 选择文件导入。

    on_imported: 导入成功后回调（用于让页面刷新表格），可为 None。
    """

    def __init__(self, parent=None, on_imported=None):
        super().__init__(parent)
        self._on_imported = on_imported
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
        path, _ = QFileDialog.getSaveFileName(
            self, "保存链接导入模板", "链接导入模板.xlsx", "Excel 文件 (*.xlsx)")
        if not path:
            return
        try:
            write_import_template(path)
            QMessageBox.information(self, "下载成功", f"标准模板已保存至：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "失败", f"保存模板失败：{e}")

    def upload_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择导入文件", "",
            "表格文件 (*.csv *.xlsx);;CSV 文件 (*.csv);;Excel 文件 (*.xlsx)")
        if not path:
            return
        try:
            success, duplicate, errors = import_sites_from_file(path)

            msg = f"成功导入了 {success} 条新记录！\n(自动跳过了 {duplicate} 条重复)"
            if errors:
                msg += f"\n\n⚠️ 有 {len(errors)} 条记录因校验失败被跳过：\n\n"
                preview = errors[:10]
                msg += "\n".join(preview)
                if len(errors) > 10:
                    msg += f"\n...（还有 {len(errors) - 10} 条未展示）"

            QMessageBox.information(self, "导入完成", msg)
            if self._on_imported is not None:
                self._on_imported()
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))
