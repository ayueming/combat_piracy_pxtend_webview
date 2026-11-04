# complaint_service.py
import os
from collections import defaultdict
from PySide6.QtCore import Qt
from db_config import get_conn
from app_data import (
    CAT_TO_CODE_MAP, CAT_TO_STANDARD_NAME, COMPLAINT_BATCH_SIZE,
    PLATFORM_OTHER, MODULE_OTHER,
)
from app_data.complaint_consts import (
    SINGLE_ITEM_PLATFORMS,
    DEFAULT_BOOK_EVIDENCE_TYPES,
    PLATFORM_SPECIFIC_BOOK_EVIDENCES,
    HOLDER_EVIDENCE_TYPES
)


def prepare_complaints_data(model, book_list):
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

    # 3. 遍历模型，执行校验
    for row_idx in range(model.rowCount()):
        chk_item = model.item(row_idx, 0)
        if not chk_item or chk_item.checkState() != Qt.Checked:
            continue

        book_name = model.item(row_idx, 1).text().strip()
        raw_cat = model.item(row_idx, 2).text().strip()
        raw_url = model.item(row_idx, 3).text().replace(" (重复资源链接)", "").strip()
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
            "name": model.item(row_idx, 4).text(),
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