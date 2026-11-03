# link_ingest_service.py

import sqlite3
import time
import random
import json
import re
import threading
from datetime import datetime

from db_config import get_conn
from db_schema import ensure_schema
from feature_extractor import extract_pure_feature
from app_data.crawl_status_consts import (
    CRAWL_STATUS_PENDING, CRAWL_STATUS_ORGANIZED, CRAWL_STATUS_DONE,
    CRAWL_STATUS_PARSE_FAILED, CRAWL_STATUS_UNMATCHED,
    CRAWL_STATUS_WHITELIST, CRAWL_STATUS_IGNORED, CRAWL_STATUS_DUPLICATE
)
from app_data.copyright_whitelist_consts import (
    COPYRIGHT_WHITELIST_DOMAINS,
    COPYRIGHT_WHITELIST_KEYWORDS,
    COPYRIGHT_WHITELIST_SOURCE_ALIASES,
)
from app_data.crawl_consts import CRAWL_REDIRECT_TIMEOUT_MS
from app_data.complaint_platform_consts import PLATFORM_DOMAINS, ACTIVE_NETDISK_DOMAINS

from app_data.ext_search_strategies import get_ext_strategy

from search_relevance_scorer import score as score_relevance
from app_data.relevance_signal_consts import RELEVANCE_SIGNALS


def _hit_whitelist(title, source_site, url=""):
    """白名单匹配：只看 source_site 和 url，绝对不看 title"""
    s = source_site or ""
    u = (url or "").lower()

    # 1. 来源站点名称包含白名单关键词
    for kw in COPYRIGHT_WHITELIST_KEYWORDS:
        if kw in s:
            return True
    # 2. URL 包含白名单域名
    for d in COPYRIGHT_WHITELIST_DOMAINS:
        if d in u:
            return True
    # 3. 来源站点完全等于简称
    for alias in COPYRIGHT_WHITELIST_SOURCE_ALIASES:
        if s.strip() == alias:
            return True

    return False


# ============================================================
# organize_pending_links 的阶段辅助函数
# 拆分原则：纯策略/纯查询 → 抽函数；写库（UPDATE/DELETE/commit）留主循环
# ============================================================

def _clean_text_for_match(t):
    """关键词匹配前的归一化：去掉所有空白和特殊符号"""
    return re.sub(r'[\s\W]+', '', t or "")


def _resolve_url(raw_url, resolver, is_mobile=0):
    if not resolver._is_short_url(raw_url):
        return raw_url
    try:
        # 把 is_mobile 传给工人
        parsed = resolver.resolve_single(raw_url, is_mobile)
        if parsed and not resolver._is_short_url(parsed):
            return parsed
        return None
    except Exception:
        return None


def _is_blacklisted(cursor, url, stage="first"):
    """查询 ignored_links，OR site_url 兜底历史数据"""
    col = f"{stage}_url"
    return cursor.execute(
        f"SELECT id FROM ignored_links WHERE {col} = ? OR site_url = ?",
        (url, url)
    ).fetchone() is not None


def _find_duplicate_by_stage(cursor, url, stage, feature_code):
    col = f"{stage}_url"

    # ★ 新增：如果是爱网盘链接，截取 /doc/ 后面的唯一 ID
    like_url = ""
    if "iwangpan.cc/doc/" in url:
        doc_id = url.split("iwangpan.cc/doc/")[1].split("?")[0].split("#")[0].strip()
        like_url = f"%{doc_id}%"

    sql = f"SELECT id FROM sites WHERE {col} = ? OR site_url = ? OR (feature_code = ? AND feature_code != '')"
    params = [url, url, feature_code or ""]

    if like_url:
        sql += f" OR {col} LIKE ?"
        params.append(like_url)

    sql += " LIMIT 1"
    row = cursor.execute(sql, params).fetchone()
    return row[0] if row else None


def _should_block_by_keyword(title, body, keyword):
    """按关键词规则判断是否应拦截该条数据。

    返回 True  = 拦截（主循环会标 UNMATCHED）
    返回 False = 放行（无关键词，或有关键词且命中）

    语义说明：无关键词时视为放行，与"手动拾取靠 is_manual 跳过"是同一种放行语义。
    """
    if not keyword:
        return False
    clean_kw = keyword.split()[0]           # 与原代码一致
    if not clean_kw:
        return False
    kw_clean = _clean_text_for_match(clean_kw)
    t_clean = _clean_text_for_match(title)
    b_clean = _clean_text_for_match(body)
    return not (kw_clean in t_clean or kw_clean in b_clean)


def _classify_platform(url, fallback_platform):
    """扫描 PLATFORM_DOMAINS 识别目标平台。返回 (平台名, 是否命中)。"""
    for plat_name, domains in PLATFORM_DOMAINS.items():
        if any(d in url for d in domains):
            return plat_name, 1
    return (fallback_platform or "其他"), 0


def _calc_relevance(title, body, source_site, signals):
    """计算加权相关度并序列化命中词。返回 (分数, hits_json 或 None)。"""
    val, hits_list = score_relevance(title, body, source_site, signals)
    hits_json = json.dumps(hits_list, ensure_ascii=False) if hits_list else None
    return val, hits_json


def _detect_netdisk(title, body):
    """检测标题/正文是否含网盘链接。返回 1/0。"""
    check_text = ((title or "") + " " + (body or "")).lower()
    return 1 if any(d in check_text for d in ACTIVE_NETDISK_DOMAINS) else 0


def save_picked_link(url, title, ctx, body="", source_site="", parent_url="", parent_title="", is_manual=False,
                     category=None):
    """
    极简入库：只清洗 URL，原样全量存入 draft_links，状态一律 pending。
    不查重、不查白名单、不匹配关键词。
    """
    ensure_schema()

    # 极简清洗
    url = (url or "").replace(" ", "").replace("\n", "").replace("\r", "")
    if not url.lower().startswith(("http://", "https://")):
        return None, False, False

    ctx = ctx or {}
    source_platform = (ctx.get("source_platform") or "").strip()
    source_keyword = (ctx.get("keyword") or "").strip()
    source_url = (ctx.get("source_url") or "").strip()
    book_name = (ctx.get("book_name") or "").strip()
    book_id = ctx.get("book_id")
    batch_id = (ctx.get("batch_id") or "").strip()
    pick_origin = (ctx.get("pick_origin") or "").strip()
    is_mobile = 1 if ctx.get("is_mobile") else 0

    if not source_url and parent_url:
        source_url = parent_url

    if book_id in (None, "", 0):
        book_id = None
    elif isinstance(book_id, str) and book_id.isdigit():
        book_id = int(book_id)

    if not category: category = source_platform or "其他"

    feature_code = extract_pure_feature(url)
    page_title = (title or "").strip()[:200]
    source_site_text = (source_site or "").strip()
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    bg_text = ""
    if parent_title: bg_text += f"[来源页面: {parent_title}] "
    if body: bg_text += body
    body_text = bg_text.strip()

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO draft_links (first_url, second_url, site_url, name, body_text, source_site, feature_code, "
            "source_platform, source_keyword, source_url, attr, book_id, book_name, category, "
            "crawl_status, fetch_time, batch_id, parent_url, parent_title, is_mobile) "
            "VALUES (?, NULL, NULL, ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",  # ★ 末尾多一个 ?
            (url, page_title, body_text, source_site_text, feature_code,
             source_platform, source_keyword, source_url, pick_origin,
             book_id, book_name or None, category, CRAWL_STATUS_PENDING, fetch_time,
             batch_id, parent_url or None, parent_title or None, is_mobile)
        )
        conn.commit()
        db_id = str(cursor.lastrowid)
    finally:
        conn.close()

    record = {
        "db_id": db_id, "book_name": book_name, "category": category, "site_url": url,
        "name": page_title, "body_text": body_text, "source_site": source_site_text,
        "attr": pick_origin, "feature_code": feature_code, "crawl_status": CRAWL_STATUS_PENDING,
        "complaint_time": "", "resource_status": "", "source_platform": source_platform,
        "source_keyword": source_keyword, "source_url": source_url, "fetch_time": fetch_time,
        "batch_id": batch_id, "domain_matched": 0, "is_mobile": is_mobile
    }
    return record, False, True


def organize_pending_links(is_exact_match=False, work_page=None, batch_id=None, process_events_func=None,
                           only_manual=False):
    ensure_schema()
    from link_resolver import LinkResolver
    from browser_manager import BrowserManager

    conn = get_conn()
    cursor = conn.cursor()

    # ★ 核心修改：读 first_url
    _sel = ("SELECT id, first_url, source_platform, name, source_keyword, body_text, source_site, attr, is_mobile "
            "FROM draft_links WHERE crawl_status = ?")

    if batch_id == "LEGACY_BATCH":
        cond = " AND (batch_id IS NULL OR batch_id = '')"
        params = [CRAWL_STATUS_PENDING]
        if only_manual: cond += " AND attr = '手动获取'"
    elif batch_id:
        cond = " AND batch_id = ?"
        params = [CRAWL_STATUS_PENDING, batch_id]
        if only_manual: cond += " AND attr = '手动获取'"
    else:
        cond = ""
        params = [CRAWL_STATUS_PENDING]
        if only_manual: cond += " AND attr = '手动获取'"

    records = cursor.execute(_sel + cond, params).fetchall()
    total = len(records)
    if total == 0:
        conn.close()
        return 0, 0, 0, 0, 0, 0, 0, 0, 0

    success_count, failed_count, unmatched_count = 0, 0, 0
    whitelist_skipped, blacklist_skipped = 0, 0
    matched_count, unknown_count, duplicate_count = 0, 0, 0

    user_sigs_rows = cursor.execute("SELECT word, weight FROM user_signals").fetchall()
    user_signals = [{"word": r[0], "weight": r[1]} for r in user_sigs_rows]
    all_signals = RELEVANCE_SIGNALS + user_signals

    bm = BrowserManager.instance()
    resolver = LinkResolver(bm)

    processed_first_urls = set()   # 阶段 1 批内去重
    processed_second_urls = set()  # 阶段 2 批内去重

    try:
        for (db_id, first_url_raw, source_platform, page_title, source_keyword,
             body_text, source_site_text, attr, is_mobile) in records:

            is_manual = (attr == "手动获取")
            strategy = get_ext_strategy(source_platform)

            # ================= 阶段 1 =================
            first_url = _resolve_url(first_url_raw, resolver, is_mobile)
            if first_url is None:
                cursor.execute("UPDATE draft_links SET crawl_status = ?, match_status = ? WHERE id = ?",
                               (CRAWL_STATUS_PARSE_FAILED, "parse_failed", db_id))
                conn.commit()
                failed_count += 1
                continue

            if first_url in processed_first_urls:
                cursor.execute("DELETE FROM draft_links WHERE id = ?", (db_id,))
                conn.commit()
                continue
            processed_first_urls.add(first_url)

            if _hit_whitelist(page_title, source_site_text, first_url):
                cursor.execute("UPDATE draft_links SET first_url = ?, site_url = ?, crawl_status = ? WHERE id = ?",
                               (first_url, first_url, CRAWL_STATUS_WHITELIST, db_id))
                conn.commit()
                whitelist_skipped += 1
                continue

            if _is_blacklisted(cursor, first_url, "first"):
                cursor.execute("UPDATE draft_links SET first_url = ?, site_url = ?, crawl_status = ? WHERE id = ?",
                               (first_url, first_url, CRAWL_STATUS_IGNORED, db_id))
                conn.commit()
                blacklist_skipped += 1
                continue

            feature_code_1 = extract_pure_feature(first_url)
            dup_id_1 = _find_duplicate_by_stage(cursor, first_url, "first", feature_code_1)
            if dup_id_1:
                cursor.execute(
                    "UPDATE draft_links SET first_url = ?, site_url = ?, feature_code = ?, crawl_status = ?, "
                    "duplicate_of_id = ?, duplicate_of_table = 'sites' WHERE id = ?",
                    (first_url, first_url, feature_code_1, CRAWL_STATUS_DUPLICATE, dup_id_1, db_id)
                )
                conn.commit()
                duplicate_count += 1
                continue

            # ================= 阶段 2 =================
            final_url = first_url
            second_url = None
            final_feature_code = feature_code_1

            if strategy.get("detailExtract") and strategy["detailExtract"].get("rules"):
                second_url = resolver.resolve_detail(first_url, strategy, is_mobile)
                if second_url is None:
                    # 剥壳失败，直接标解析失败
                    cursor.execute("UPDATE draft_links SET first_url = ?, site_url = NULL, crawl_status = ?, match_status = 'parse_failed' WHERE id = ?",
                                   (first_url, CRAWL_STATUS_PARSE_FAILED, db_id))
                    conn.commit()
                    failed_count += 1
                    continue

                final_url = second_url
                final_feature_code = extract_pure_feature(second_url)

                if second_url in processed_second_urls:
                    cursor.execute("DELETE FROM draft_links WHERE id = ?", (db_id,))
                    conn.commit()
                    continue
                processed_second_urls.add(second_url)

                if _hit_whitelist(page_title, source_site_text, second_url):
                    cursor.execute("UPDATE draft_links SET first_url = ?, second_url = ?, site_url = ?, crawl_status = ? WHERE id = ?",
                                   (first_url, second_url, second_url, CRAWL_STATUS_WHITELIST, db_id))
                    conn.commit()
                    whitelist_skipped += 1
                    continue

                if _is_blacklisted(cursor, second_url, "second"):
                    cursor.execute("UPDATE draft_links SET first_url = ?, second_url = ?, site_url = ?, crawl_status = ? WHERE id = ?",
                                   (first_url, second_url, second_url, CRAWL_STATUS_IGNORED, db_id))
                    conn.commit()
                    blacklist_skipped += 1
                    continue

                dup_id_2 = _find_duplicate_by_stage(cursor, second_url, "second", final_feature_code)

                if dup_id_2:
                    # ★ 1. 识别真实网盘分类（只为了 UI 显示正确）
                    real_platform, _ = _classify_platform(second_url, source_platform)

                    # ★ 2. 极简修复老数据：如果老数据的核心字段是空的，就把新的补进去
                    #    使用 COALESCE 和 NULLIF，意思是：如果老字段是 NULL 或空字符串，就用新值，否则保留老值。
                    cursor.execute(
                        "UPDATE sites SET "
                        "first_url = COALESCE(NULLIF(first_url, ''), ?), "
                        "second_url = COALESCE(NULLIF(second_url, ''), ?), "
                        "site_url = COALESCE(NULLIF(site_url, ''), ?), "
                        "feature_code = COALESCE(NULLIF(feature_code, ''), ?) "
                        "WHERE id = ?",
                        (first_url, second_url, second_url, final_feature_code, dup_id_2)
                    )

                    # ★ 3. 更新草稿状态为已在库，并修正分类，让 UI 不显示错乱
                    cursor.execute(
                        "UPDATE draft_links SET first_url = ?, second_url = ?, site_url = ?, feature_code = ?, crawl_status = ?, "
                        "category = ?, target_platform = ?, "
                        "duplicate_of_id = ?, duplicate_of_table = 'sites' WHERE id = ?",
                        (first_url, second_url, second_url, final_feature_code, CRAWL_STATUS_DUPLICATE,
                         real_platform, real_platform,
                         dup_id_2, db_id)
                    )
                    conn.commit()
                    duplicate_count += 1
                    continue

            # ================= 终态处理 =================
            if not is_manual and _should_block_by_keyword(page_title, body_text, source_keyword):
                cursor.execute(
                    "UPDATE draft_links SET first_url = ?, second_url = ?, site_url = ?, feature_code = ?, crawl_status = ? WHERE id = ?",
                    (first_url, second_url, final_url, final_feature_code, CRAWL_STATUS_UNMATCHED, db_id)
                )
                conn.commit()
                unmatched_count += 1
                continue

            target_platform, domain_matched = _classify_platform(final_url, source_platform)
            if domain_matched == 0:
                relevance_val, relevance_hits_json = _calc_relevance(page_title, body_text, source_site_text, all_signals)
            else:
                relevance_val, relevance_hits_json = 0.0, None
            has_pan = _detect_netdisk(page_title, body_text)

            cursor.execute(
                "UPDATE draft_links SET first_url = ?, second_url = ?, site_url = ?, category = ?, target_platform = ?, "
                "match_status = 'matched', feature_code = ?, crawl_status = ?, "
                "domain_matched = ?, relevance = ?, relevance_hits = ?, has_pan = ? WHERE id = ?",
                (first_url, second_url, final_url, target_platform, target_platform, final_feature_code,
                 CRAWL_STATUS_ORGANIZED, domain_matched, relevance_val, relevance_hits_json, has_pan, db_id)
            )
            conn.commit()
            success_count += 1
            if domain_matched == 1:
                matched_count += 1
            else:
                unknown_count += 1

    finally:
        conn.close()

    return total, success_count, failed_count, unmatched_count, whitelist_skipped, \
        blacklist_skipped, matched_count, unknown_count, duplicate_count


def write_organized_links():
    return 0


def archive_batch_by_ids(keep_items, drop_ids, blacklist_ids):
    if not keep_items and not drop_ids and not blacklist_ids:
        return 0, 0, 0

    conn = get_conn()
    try:
        cursor = conn.cursor()
        inserted_count, dropped_count, black_count = 0, 0, 0

        if keep_items:
            for db_id, category in keep_items:
                row = cursor.execute(
                    "SELECT site_url, name, body_text, source_site, feature_code, "
                    "  source_platform, source_keyword, source_url, attr, "
                    "  book_id, book_name, target_platform, fetch_time, batch_id, "
                    "  parent_url, parent_title, is_mobile, first_url, second_url "  # ★ 增加
                    "FROM draft_links WHERE id = ?", (db_id,)
                ).fetchone()

                if not row: continue

                (url, name, body, source_site, code,
                 plat, kw, src_url, attr,
                 book_id, book_name, target_platform, fetch_time, batch_id,
                 parent_url, parent_title, is_mobile, first_url, second_url) = row  # ★ 增加解包

                try:
                    cursor.execute(
                        "INSERT INTO sites (category, target_platform, site_url, name, original_title, body_text, "
                        "  attr, feature_code, book_id, book_name, "
                        "  source_platform, source_keyword, source_url, fetch_time, "
                        "  batch_id, domain_matched, parent_url, parent_title, is_mobile, first_url, second_url) "  # ★ 增加
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",  # ★ 改为 21 个问号
                        (category, target_platform or category, url,
                         source_site or "未知来源", name, body, attr, code, book_id, book_name,
                         plat, kw, src_url, fetch_time, batch_id,
                         1 if target_platform else 0, parent_url, parent_title, is_mobile,
                         first_url, second_url)  # ★ 增加变量
                    )
                    inserted_count += 1
                except sqlite3.IntegrityError:
                    pass
                cursor.execute("DELETE FROM draft_links WHERE id = ?", (db_id,))

        if blacklist_ids:
            placeholders = ','.join('?' * len(blacklist_ids))
            # ★ 增加 first_url, second_url
            rows = cursor.execute(
                f"SELECT id, site_url, name, first_url, second_url FROM draft_links WHERE id IN ({placeholders})",
                blacklist_ids).fetchall()
            for r in rows:
                # ★ 增加写入
                cursor.execute(
                    "INSERT OR IGNORE INTO ignored_links (site_url, title, add_time, first_url, second_url) VALUES (?, ?, ?, ?, ?)",
                    (r[1], r[2], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), r[3], r[4]))
                cursor.execute("DELETE FROM draft_links WHERE id = ?", (r[0],))
                black_count += 1

        if drop_ids:
            placeholders = ','.join('?' * len(drop_ids))
            cursor.execute(f"DELETE FROM draft_links WHERE id IN ({placeholders})", drop_ids)
            dropped_count = cursor.rowcount

        conn.commit()
        return inserted_count, dropped_count, black_count
    finally:
        conn.close()


def change_draft_book(db_id, book_name):
    conn = get_conn()
    try:
        conn.execute("UPDATE draft_links SET book_name = ? WHERE id = ?", (book_name, db_id))
        conn.commit()
    finally:
        conn.close()


def clear_draft_category(ids):
    if not ids:
        return
    conn = get_conn()
    try:
        placeholders = ','.join('?' * len(ids))
        conn.execute(f"DELETE FROM draft_links WHERE id IN ({placeholders})", ids)
        conn.commit()
    finally:
        conn.close()


def check_drafts_exist():
    conn = get_conn()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM draft_links WHERE crawl_status IN (?, ?, ?, ?)",
            (CRAWL_STATUS_PENDING, CRAWL_STATUS_UNMATCHED, CRAWL_STATUS_PARSE_FAILED, CRAWL_STATUS_ORGANIZED)
        ).fetchone()[0]
        return count
    finally:
        conn.close()


def clear_all_drafts():
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM draft_links WHERE crawl_status IN (?, ?, ?, ?)",
            (CRAWL_STATUS_PENDING, CRAWL_STATUS_UNMATCHED, CRAWL_STATUS_PARSE_FAILED, CRAWL_STATUS_ORGANIZED)
        )
        conn.commit()
        print("[批次] 已彻底清空全库历史草稿（draft_links）。")
    finally:
        conn.close()