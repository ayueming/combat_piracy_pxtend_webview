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


def _resolve_url(raw_url, resolver):
    """短链解析。非短链原样返回；短链解析失败返回 None。"""
    if "baidu.com/link?url=" not in raw_url:
        return raw_url
    try:
        parsed = resolver.resolve_single(raw_url)
        if parsed and not resolver._is_short_url(parsed):
            return parsed
        return None
    except Exception:
        return None


def _is_blacklisted(cursor, url):
    """查询 ignored_links 判断是否已被永删。"""
    return cursor.execute(
        "SELECT id FROM ignored_links WHERE site_url = ?", (url,)
    ).fetchone() is not None


def _find_duplicate_in_sites(cursor, url, feature_code):
    """在 sites 表查重（URL 或特征码任一命中）。返回已存在 id 或 None。"""
    row = cursor.execute(
        "SELECT id FROM sites WHERE site_url = ? "
        "OR (feature_code IS NOT NULL AND feature_code != '' AND feature_code = ?) "
        "LIMIT 1",
        (url, feature_code or "")
    ).fetchone()
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
            "INSERT INTO draft_links (site_url, name, body_text, source_site, feature_code, "
            "source_platform, source_keyword, source_url, attr, book_id, book_name, category, "
            "crawl_status, fetch_time, batch_id, parent_url, parent_title) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (url, page_title, body_text, source_site_text, feature_code,
             source_platform, source_keyword, source_url, pick_origin,
             book_id, book_name or None, category, CRAWL_STATUS_PENDING, fetch_time,
             batch_id, parent_url or None, parent_title or None)
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
        "batch_id": batch_id, "domain_matched": 0
    }
    return record, False, True


def organize_pending_links(is_exact_match=False, work_page=None, batch_id=None, process_events_func=None,
                           only_manual=False):
    """
    流水线：解析 -> 去重 -> 查黑白名单 -> 查库 -> 关键词匹配 -> 分类评级
    """
    ensure_schema()
    from link_resolver import LinkResolver
    from browser_manager import BrowserManager

    conn = get_conn()
    cursor = conn.cursor()

    _sel = ("SELECT id, site_url, source_platform, name, source_keyword, body_text, source_site, attr "
            "FROM draft_links WHERE crawl_status = ?")

    # 构造查询条件
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

    processed_urls = set()  # 当前批次批内去重记录

    try:
        for (db_id, raw_url, source_platform, page_title, source_keyword,
             body_text, source_site_text, attr) in records:

            is_manual = (attr == "手动获取")

            # --- 1. 解析 ---
            real_url = _resolve_url(raw_url, resolver)
            if real_url is None:
                cursor.execute(
                    "UPDATE draft_links SET crawl_status = ?, match_status = ? WHERE id = ?",
                    (CRAWL_STATUS_PARSE_FAILED, "parse_failed", db_id)
                )
                conn.commit()
                failed_count += 1
                continue

            # --- 2. 批内去重 ---
            if real_url in processed_urls:
                cursor.execute("DELETE FROM draft_links WHERE id = ?", (db_id,))
                conn.commit()
                continue
            processed_urls.add(real_url)

            # --- 3. 白名单 ---
            if _hit_whitelist(page_title, source_site_text, real_url):
                cursor.execute(
                    "UPDATE draft_links SET site_url = ?, crawl_status = ? WHERE id = ?",
                    (real_url, CRAWL_STATUS_WHITELIST, db_id)
                )
                conn.commit()
                whitelist_skipped += 1
                continue

            # --- 4. 黑名单 ---
            if _is_blacklisted(cursor, real_url):
                cursor.execute(
                    "UPDATE draft_links SET site_url = ?, crawl_status = ? WHERE id = ?",
                    (real_url, CRAWL_STATUS_IGNORED, db_id)
                )
                conn.commit()
                blacklist_skipped += 1
                continue

            # --- 5. 正式库查重 ---
            new_feature = extract_pure_feature(real_url)
            dup_id = _find_duplicate_in_sites(cursor, real_url, new_feature)
            if dup_id:
                cursor.execute(
                    "UPDATE draft_links SET site_url = ?, feature_code = ?, crawl_status = ?, "
                    "duplicate_of_id = ?, duplicate_of_table = 'sites' WHERE id = ?",
                    (real_url, new_feature, CRAWL_STATUS_DUPLICATE, dup_id, db_id)
                )
                conn.commit()
                duplicate_count += 1
                continue

            # --- 6. 关键词匹配（手动拾取跳过）---
            if not is_manual and _should_block_by_keyword(page_title, body_text, source_keyword):
                cursor.execute(
                    "UPDATE draft_links SET site_url = ?, feature_code = ?, crawl_status = ? WHERE id = ?",
                    (real_url, new_feature, CRAWL_STATUS_UNMATCHED, db_id)
                )
                conn.commit()
                unmatched_count += 1
                continue

            # --- 7. 平台分类 + 评级 + 最终写入 ---
            target_platform, domain_matched = _classify_platform(real_url, source_platform)
            if domain_matched == 0:
                relevance_val, relevance_hits_json = _calc_relevance(
                    page_title, body_text, source_site_text, all_signals
                )
            else:
                relevance_val, relevance_hits_json = 0.0, None
            has_pan = _detect_netdisk(page_title, body_text)

            cursor.execute(
                "UPDATE draft_links SET site_url = ?, category = ?, target_platform = ?, "
                "match_status = 'matched', feature_code = ?, crawl_status = ?, "
                "domain_matched = ?, relevance = ?, relevance_hits = ?, has_pan = ? WHERE id = ?",
                (real_url, target_platform, target_platform, new_feature,
                 CRAWL_STATUS_ORGANIZED, domain_matched, relevance_val,
                 relevance_hits_json, has_pan, db_id)
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
                    "  parent_url, parent_title "
                    "FROM draft_links WHERE id = ?", (db_id,)
                ).fetchone()

                if not row: continue

                (url, name, body, source_site, code,
                 plat, kw, src_url, attr,
                 book_id, book_name, target_platform, fetch_time, batch_id,
                 parent_url, parent_title) = row

                try:
                    cursor.execute(
                        "INSERT INTO sites (category, target_platform, site_url, name, original_title, body_text, "
                        "  attr, feature_code, book_id, book_name, "
                        "  source_platform, source_keyword, source_url, fetch_time, "
                        "  batch_id, domain_matched, parent_url, parent_title) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (category, target_platform or category, url,
                         source_site or "未知来源",  # sites.name
                         name,  # sites.original_title
                         body, attr, code, book_id, book_name,
                         plat, kw, src_url, fetch_time, batch_id,
                         1 if target_platform else 0, parent_url, parent_title)
                    )
                    inserted_count += 1
                except sqlite3.IntegrityError:
                    pass
                cursor.execute("DELETE FROM draft_links WHERE id = ?", (db_id,))

        if blacklist_ids:
            placeholders = ','.join('?' * len(blacklist_ids))
            rows = cursor.execute(f"SELECT id, site_url, name FROM draft_links WHERE id IN ({placeholders})",
                                  blacklist_ids).fetchall()
            for r in rows:
                cursor.execute("INSERT OR IGNORE INTO ignored_links (site_url, title, add_time) VALUES (?, ?, ?)",
                               (r[1], r[2], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
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