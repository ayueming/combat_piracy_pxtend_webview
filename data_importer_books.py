# data_importer_books.py
"""
作品（books）表格导入逻辑。形态与 data_importer.py 对等：
不直接操作 UI，返回统计与错误明细，由调用方决定如何展示。
"""
import csv
import os
import sqlite3

from db_config import get_conn

# 与 page_books.py 共用同一套校验规则，避免两处判断漂移
from ui_components import is_alnum_only


def import_books_from_file(file_path):
    """
    严格校验模式，逐行导入作品。
    返回 (success_count, duplicate_count, invalid_count, error_details)
      - invalid_count: 平台ID / 百度CP号不满足 is_alnum_only，或触发 DB CHECK
      - error_details: 逐行可读的错误描述，供调用方按需展示
    """
    ext = os.path.splitext(file_path)[1].lower()
    rows = []

    # 1. 读文件
    if ext == '.csv':
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            rows = list(csv.reader(f))
    elif ext == '.xlsx':
        try:
            import openpyxl
        except ImportError:
            raise ImportError("读取 Excel 需要 openpyxl 模块。\n请在终端运行：pip install openpyxl")

        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            if any(row):
                rows.append([str(cell) if cell is not None else "" for cell in row])
    else:
        raise ValueError("不支持的文件格式，仅支持 .csv 或 .xlsx")

    if not rows or len(rows) <= 1:
        raise ValueError("文件为空或没有有效数据（必须包含表头且至少一行数据）。")

    # 2. 智能识别表头
    #    顺序有意如此：“百度”放在最前，避免“百度CP号”被“平台/书号”分支误吞。
    header = rows[0]
    col_idx = {
        "platform_id": 0, "name": 1, "aliases": 2,
        "pen_name": 3, "rights_holder": 4, "baidu_id": 5,
    }
    for i, h in enumerate(header):
        h_str = str(h).strip().lower()
        if "百度" in h_str:
            col_idx["baidu_id"] = i
        elif "平台" in h_str or "书号" in h_str:
            col_idx["platform_id"] = i
        elif "曾用名" in h_str or "别名" in h_str:
            col_idx["aliases"] = i
        elif "笔名" in h_str:
            col_idx["pen_name"] = i
        elif "权利" in h_str:
            col_idx["rights_holder"] = i
        elif "作品名" in h_str or "书名" in h_str:
            col_idx["name"] = i

    # 3. 逐行校验并入库
    conn = get_conn()
    cursor = conn.cursor()

    success_count = 0
    duplicate_count = 0
    invalid_count = 0
    error_details = []

    for row_idx, row in enumerate(rows[1:], start=2):
        if len(row) <= col_idx["name"]:
            error_details.append(f"第 {row_idx} 行：字段太少，缺少作品名列。")
            continue

        name = str(row[col_idx["name"]]).strip()
        if not name or name == "None":
            continue

        platform_id = str(row[col_idx["platform_id"]]).strip() if len(row) > col_idx["platform_id"] else ""
        baidu_id = str(row[col_idx["baidu_id"]]).strip() if len(row) > col_idx["baidu_id"] else ""
        aliases = str(row[col_idx["aliases"]]).strip() if len(row) > col_idx["aliases"] else ""
        pen_name = str(row[col_idx["pen_name"]]).strip() if len(row) > col_idx["pen_name"] else ""
        rights = str(row[col_idx["rights_holder"]]).strip() if len(row) > col_idx["rights_holder"] else ""

        if not is_alnum_only(platform_id) or not is_alnum_only(baidu_id):
            invalid_count += 1
            error_details.append(
                f"第 {row_idx} 行：平台ID / 百度CP号含非法字符（仅支持英文字母和数字）。"
            )
            continue

        try:
            cursor.execute(
                "INSERT INTO books "
                "(platform_id, baidu_id, name, pen_name, aliases, rights_holder) "
                "VALUES (?,?,?,?,?,?)",
                (platform_id, baidu_id, name, pen_name, aliases, rights))
            success_count += 1
        except sqlite3.IntegrityError as e:
            if "CHECK" in str(e).upper():
                invalid_count += 1
                error_details.append(
                    f"第 {row_idx} 行：平台ID / 百度CP号未通过数据库 CHECK 约束。"
                )
            else:
                duplicate_count += 1
            continue

    conn.commit()
    conn.close()

    return success_count, duplicate_count, invalid_count, error_details