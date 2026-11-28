# links_row_builder.py

"""把一条链接记录映射成「盗版链接」表格的一行。

原先 page_links.refresh_table（从库里读）与 page_links.append_site_row（拾取后插入）
各写了一遍列映射：列顺序、发布方/标题的读写属性、查重标红、处理状况样式全是两份。
这里统一成唯一实现，改列只改这一处 + app_data/link_columns.py。

record 约定字段（缺省按空处理）：
    db_id            数据库主键（空 = 尚未入库的新行）
    book_name        关联作品
    category         资源分类（对外唯一值，页面读写的都是 sites.category）
    site_url         资源链接
    source_site      发布方 → sites.name
    attr             属性
    feature_code     特征码
    complaint_time   投诉时间
    resource_status  资源状态（只认下拉框里的值，其余显示为空）
    crawl_status     抓取状态
    picker_status    拾取状态
    name             标题 → sites.original_title
    handle_status    处理状况（库里已算好的值；为空时按资源状态+投诉时间即时推导）
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QStandardItem

from app_data.link_columns import TABLE_HEADERS
from app_data.resource_status_consts import ALL_RESOURCE_STATUSES
from ui_styles import HANDLE_STATUS_BOLD_ROLES, HANDLE_STATUS_TEXT_COLORS
from link_handle_status import HANDLE_ROLE, compute_handle_status

_UI_FONT_FAMILY = "Microsoft YaHei"
_UI_FONT_SIZE = 9
_URL_COLOR_NORMAL = QColor("#000000")
_URL_COLOR_DUPLICATE = QColor("red")
_HANDLE_COLOR_FALLBACK = "#333333"


def apply_handle_style(item, text):
    """处理状况单元格：颜色/加粗按语义角色，样式常量统一在 ui_styles。"""
    role = HANDLE_ROLE.get(text)
    item.setForeground(QColor(HANDLE_STATUS_TEXT_COLORS.get(role, _HANDLE_COLOR_FALLBACK)))
    font = item.font()
    font.setBold(role in HANDLE_STATUS_BOLD_ROLES)
    item.setFont(font)


def set_url_duplicate_style(item, duplicate):
    """资源链接单元格：重复链标红加粗，否则恢复常规黑色。"""
    if duplicate:
        item.setForeground(_URL_COLOR_DUPLICATE)
        item.setFont(QFont(_UI_FONT_FAMILY, _UI_FONT_SIZE, QFont.Bold))
    else:
        item.setForeground(_URL_COLOR_NORMAL)
        item.setFont(QFont(_UI_FONT_FAMILY, _UI_FONT_SIZE, QFont.Normal))


def _text(record, key):
    return (record.get(key) or "")


def build_link_row(record, *, duplicate=False):
    """record(dict) → list[QStandardItem]，顺序与 TABLE_HEADERS 一致。"""
    db_id = record.get("db_id")
    url_text = _text(record, "site_url")
    comp_time = _text(record, "complaint_time")

    chk_item = QStandardItem()
    chk_item.setCheckable(True)
    chk_item.setCheckState(Qt.Unchecked)
    chk_item.setData(str(db_id) if db_id else "", Qt.UserRole)
    chk_item.setData(True, Qt.UserRole + 1)

    url_item = QStandardItem(url_text)
    if duplicate:
        # 非重复行不设显式前景色/字体，保持表格默认渲染（选中态交给样式表）
        set_url_duplicate_style(url_item, True)

    item_publisher = QStandardItem(_text(record, "source_site"))
    item_publisher.setEditable(True)

    raw_status = _text(record, "resource_status")
    display_status = raw_status if raw_status in ALL_RESOURCE_STATUSES else ""
    item_res_status = QStandardItem(display_status)

    handle_text = (record.get("handle_status") or "").strip() or compute_handle_status(
        display_status, comp_time)
    item_handle = QStandardItem(handle_text)
    item_handle.setEditable(False)
    apply_handle_style(item_handle, handle_text)

    item_title = QStandardItem(_text(record, "name"))
    item_title.setEditable(False)

    row = [
        chk_item,
        QStandardItem(_text(record, "book_name")),
        QStandardItem(_text(record, "category")),
        url_item,
        item_publisher,
        QStandardItem(_text(record, "attr")),
        QStandardItem(_text(record, "feature_code")),
        item_handle,
        item_res_status,
        QStandardItem(comp_time),
        QStandardItem(_text(record, "crawl_status")),
        QStandardItem(_text(record, "picker_status")),
        item_title,
    ]
    # 列数与表头漂移时尽早炸掉，别等到界面错位才发现
    assert len(row) == len(TABLE_HEADERS), "build_link_row 与 TABLE_HEADERS 列数不一致"
    return row


def build_blank_row():
    """表格底部用于新建录入的空白占位行。"""
    chk_item = QStandardItem()
    chk_item.setCheckable(True)
    chk_item.setCheckState(Qt.Unchecked)
    chk_item.setData(None, Qt.UserRole)
    return [chk_item] + [QStandardItem("") for _ in range(len(TABLE_HEADERS) - 1)]
