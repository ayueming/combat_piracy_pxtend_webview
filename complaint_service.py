# complaint_service.py
import os
from collections import defaultdict
from PySide6.QtCore import Qt
from db_config import get_conn

from app_data import (
    CAT_TO_CODE_MAP, CAT_TO_STANDARD_NAME, COMPLAINT_BATCH_SIZE,
    PLATFORM_OTHER, MODULE_OTHER, MODULE_SEARCH,
)
from app_data.complaint_consts import (
    SINGLE_ITEM_PLATFORMS,
    DEFAULT_BOOK_EVIDENCE_TYPES,
    PLATFORM_SPECIFIC_BOOK_EVIDENCES,
    HOLDER_EVIDENCE_TYPES
)

from app_data.link_columns import (
    COL_CHK, COL_BOOK, COL_CAT, COL_URL, COL_PUBLISHER, DUP_URL_SUFFIX,
)


# =========================================================
# 扩展投诉：把 search 类记录纯复制到其他所有搜索引擎平台
# 不做任何 URL 改写，其余字段原样共享
# =========================================================
def _get_search_platform_cats():
    """返回所有 search 类平台的标准显示名（自动跳过别名）。"""
    cats = []
    for name, info in CAT_TO_CODE_MAP.items():
        if info["module"] != MODULE_SEARCH:
            continue
        if CAT_TO_STANDARD_NAME.get(name) != name:  # 跳过别名
            continue
        cats.append(name)
    return cats


def _expand_search_items(checked_data):
    """
    把 search 类投诉记录横向复制到所有搜索引擎平台：
    - 原记录保留
    - 为其他每个 search 平台各复制一条（URL 原样不动）
    - 非 search 记录原样保留
    """
    search_cats = _get_search_platform_cats()
    if len(search_cats) <= 1:
        return checked_data

    result = []
    for item in checked_data:
        result.append(item)  # 原记录始终保留

        if item.get("module_code") != MODULE_SEARCH:
            continue

        original_cat = item.get("cat", "")
        for target_cat in search_cats:
            if target_cat == original_cat:
                continue
            target_info = CAT_TO_CODE_MAP.get(target_cat)
            if not target_info:
                continue

            new_item = dict(item)  # 浅拷贝，URL / id / 证据 / 权利人全部共享
            new_item["cat"] = target_cat
            new_item["platform_code"] = target_info["platform"]
            new_item["module_code"] = target_info["module"]
            new_item["web_text"] = target_info.get("web_text", "")

            result.append(new_item)

    return result


_ROW_CONTENT_COLS = (COL_BOOK, COL_CAT, COL_URL, COL_PUBLISHER)


def _is_empty_row(model, row_idx, chk_item):
    """既没入库、也没填任何内容的行（表格底部空白录入行），跳过而不是报错。"""
    if chk_item.data(Qt.UserRole):
        return False
    for col in _ROW_CONTENT_COLS:
        item = model.item(row_idx, col)
        if item and item.text().strip():
            return False
    return True


def prepare_complaints_data(model, book_list, expand_search=True, target_rows=None):
    """把链接打包成投诉批次，返回 (batches, error_report)。

    target_rows: 需要处理的行号集合（源模型下标）。
                 传 None = 老行为，只处理勾选行；
                 传集合 = 只处理这些行（页面把「勾选 ∪ 选中」传进来）。
    expand_search: 勾选后把 search 类投诉横向复制到所有搜索引擎。
    """
    checked_data = []
    error_report = []

    conn = get_conn()

    # 1. 查库：书籍基础信息
    book_details = {
        str(row[0]): {
            "name": row[1], "aliases": row[2], "platform_id": row[3],
            "evidence_path": row[4], "rights_holder": row[5], "baidu_id": row[6]
        }
        for row in conn.execute(
            "SELECT id, name, aliases, platform_id, evidence_path, rights_holder, baidu_id FROM books").fetchall()
    }

    # 2. 查库：所有证据材料
    book_evs = defaultdict(dict)  # {book_id: {ev_type: path}}
    holder_evs = defaultdict(dict)  # {holder_name: {ev_type: path}}

    rows = conn.execute("SELECT owner_type, owner_key, ev_type, file_path FROM evidences").fetchall()
    for o_type, o_key, e_type, f_path in rows:
        if not f_path: continue
        if o_type == 'book':
            book_evs[str(o_key)][e_type] = f_path
        elif o_type == 'holder':
            holder_evs[str(o_key)][e_type] = f_path

    # 2.5 查库：作者隐私信息（按姓名关联）
    author_map = {
        row[0]: {"phone": row[1] or "", "id_card": row[2] or "", "email": row[3] or ""}
        for row in conn.execute(
            "SELECT name, phone, id_card, email FROM authors"
        ).fetchall()
    }

    conn.close()

    # 3. 遍历目标行，执行校验
    if target_rows is None:
        row_indexes = range(model.rowCount())
    else:
        row_indexes = sorted(target_rows)

    for row_idx in row_indexes:
        if not (0 <= row_idx < model.rowCount()):
            continue
        chk_item = model.item(row_idx, COL_CHK)
        if not chk_item:
            continue
        # 老行为（未传 target_rows）：只管勾选行；新入口由 target_rows 决定范围
        if target_rows is None and chk_item.checkState() != Qt.Checked:
            continue
        if _is_empty_row(model, row_idx, chk_item):
            continue

        book_name = model.item(row_idx, COL_BOOK).text().strip()
        raw_cat = model.item(row_idx, COL_CAT).text().strip()
        raw_url = model.item(row_idx, COL_URL).text().replace(DUP_URL_SUFFIX, "").strip()
        db_id = chk_item.data(Qt.UserRole)

        if not book_name or not raw_cat or not raw_url:
            error_report.append(f"第 {row_idx + 1} 行：关联作品、资源分类或链接为空。")
            continue

        code_info = CAT_TO_CODE_MAP.get(raw_cat, {"platform": PLATFORM_OTHER, "module": MODULE_OTHER})
        platform_code = code_info.get("platform")

        if platform_code == PLATFORM_OTHER:
            continue

        b_id = next((str(b[0]) for b in book_list if b[1] == book_name), None)
        if not b_id or b_id not in book_details:
            error_report.append(f"第 {row_idx + 1} 行《{book_name}》：作品库中找不到。")
            continue

        book_info = book_details[b_id]
        if not book_info["platform_id"]:
            error_report.append(f"第 {row_idx + 1} 行《{book_name}》：缺少【平台作品ID】。")
            continue

        # ==========================================
        # ★ 总质检：校验书籍材料
        # ==========================================
        required_book_types = PLATFORM_SPECIFIC_BOOK_EVIDENCES.get(
            platform_code, DEFAULT_BOOK_EVIDENCE_TYPES
        )
        current_book_files = []
        missing_book_types = []
        for ev_type in required_book_types:
            path = book_evs.get(b_id, {}).get(ev_type)
            if path and os.path.exists(path):
                current_book_files.append(path)
            else:
                missing_book_types.append(ev_type)

        if missing_book_types:
            error_report.append(f"第 {row_idx + 1} 行《{book_name}》：缺少书籍材料 {missing_book_types}（不需要智能体投诉的，可上传风景照）。")
            continue

        # ==========================================
        # ★ 总质检：校验权利人身份证材料
        # ==========================================
        holder_name = book_info["rights_holder"]
        current_holder_files = []
        missing_holder_types = []
        for ev_type in HOLDER_EVIDENCE_TYPES:
            path = holder_evs.get(holder_name, {}).get(ev_type)
            if path and os.path.exists(path):
                current_holder_files.append(path)
            else:
                missing_holder_types.append(ev_type)

        if missing_holder_types:
            error_report.append(
                f"第 {row_idx + 1} 行《{book_name}》：权利人【{holder_name}】缺少身份证材料 {missing_holder_types}（不需要智能体投诉的，可上传风景照）。")
            continue

        # 组装合规数据
        url_str = f"https://www.jjwxc.net/onebook.php?novelid={book_info['platform_id']}"
        clean_book = book_name.strip("《》").strip()
        clean_aliases = book_info["aliases"].strip("《》").strip() if book_info["aliases"] else ""
        name_str = f"《{clean_book}（原名：{clean_aliases}）》" if clean_aliases else f"《{clean_book}》"
        standard_cat = CAT_TO_STANDARD_NAME.get(raw_cat, raw_cat)

        author_info = author_map.get(holder_name)
        if not author_info or not author_info["id_card"]:
            error_report.append(
                f"第 {row_idx + 1} 行《{book_name}》：权利人【{holder_name}】"
                f"未在【作者信息】页建档或缺少身份证号，请先补全。"
            )
            continue

        checked_data.append({
            "id": db_id,
            "book": book_name,
            "aliases": book_info["aliases"],
            "platform_id": book_info["platform_id"],
            "baidu_id": book_info["baidu_id"],
            "rights_holder": holder_name,
            "cat": standard_cat,
            "url": raw_url,
            "name": model.item(row_idx, COL_PUBLISHER).text(),
            "url_str": url_str,
            "name_str": name_str,
            "platform_code": platform_code,
            "module_code": code_info["module"],
            "web_text": code_info.get("web_text", ""),

            # ★ 把查好的两波文件都传给下游
            "book_files": current_book_files,  # 书的 4 张图 / PDF
            "holder_files": current_holder_files,  # 人的身份证正反面

            # ★★ 本次唯一改动：主材料路径（evidences 表已校验存在）
            #     顺序严格对应 required_book_types[0]
            #     夸克/UC/迅雷 → auth_pdf；wenxin 不读本字段
            "evidence_path": current_book_files[0] if current_book_files else "",

            "holder_phone": author_info["phone"],  # ★ 新增
            "holder_id_card": author_info["id_card"],  # ★ 新增
            "holder_email": author_info["email"],  # ★ 新增
        })

    # ★ 新增：search 类横向复制（必须在分组切片之前）
    if expand_search:
        checked_data = _expand_search_items(checked_data)

    # 4. 按 (分类, 书籍) 分组切片 (逻辑不变)
    grouped_data = defaultdict(list)
    for item in checked_data:
        grouped_data[(item["cat"], item["book"])].append(item)

    batches = []
    for (cat, book), items in grouped_data.items():
        platform_code = items[0].get("platform_code", PLATFORM_OTHER)
        if platform_code in SINGLE_ITEM_PLATFORMS:
            for item in items:
                batches.append([item])
        else:
            for i in range(0, len(items), COMPLAINT_BATCH_SIZE):
                batches.append(items[i:i + COMPLAINT_BATCH_SIZE])

    return batches, error_report