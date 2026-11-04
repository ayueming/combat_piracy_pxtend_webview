# search_batch_queries.py
"""
检索控制台批次查询。
已全面切换至全局工作台模式：无视 batch_id，所有草稿统一展示。
"""

from db_config import get_conn
from app_data.crawl_status_consts import (
    CRAWL_STATUS_PENDING,
    CRAWL_STATUS_ORGANIZED,
    CRAWL_STATUS_UNMATCHED,
    CRAWL_STATUS_PARSE_FAILED,
    CRAWL_STATUS_WHITELIST,
    CRAWL_STATUS_IGNORED,
    CRAWL_STATUS_DONE,
    CRAWL_STATUS_DUPLICATE,
)

_SELECT_FIELDS = ("id, name, body_text, site_url, book_name, category, target_platform, "
                  "fetch_time, source_site, relevance, relevance_hits, "
                  "crawl_status, domain_matched, attr, has_pan, "
                  "first_url, second_url, is_mobile")

_SELECT_FIELDS_SITES = ("id, name, body_text, site_url, book_name, category, target_platform, "
                        "fetch_time, source_site, relevance, relevance_hits, "
                        "crawl_status, domain_matched, attr, original_title, "
                        "first_url, second_url, is_mobile")

def find_latest_draft_batch():
    """兼容旧接口：现在全局工作台已不需要查最新批次了，直接返回 'ALL'"""
    return "ALL"

def get_batch_tables(batch_id=None):
    """
    ★ 核心修改：无视传入的 batch_id，永远查询全局草稿数据。
    """
    empty = {
        "pending": [], "confirmed": [], "unknown": [],
        "unmatched": [], "failed": [], "duplicate": [],
        "whitelist": [], "ignored": [], "history": [],
        "tab1_todo": [], "tab2_pending": [], "tab3_archive": [],
        "stats": {}
    }

    conn = get_conn()
    try:
        # 不再拼装 batch_id 条件，直接查全表
        def q_draft(extra_cond, extra_params, order="ORDER BY id DESC"):
            sql = f"SELECT {_SELECT_FIELDS} FROM draft_links WHERE {extra_cond} {order}"
            return conn.execute(sql, extra_params).fetchall()

        history_records = conn.execute(f"SELECT {_SELECT_FIELDS_SITES} FROM sites ORDER BY id DESC").fetchall()

        # 获取各自的数据
        res_pending = q_draft("crawl_status = ?", [CRAWL_STATUS_PENDING])
        res_confirmed = q_draft("crawl_status = ? AND domain_matched = 1", [CRAWL_STATUS_ORGANIZED])
        res_unknown = q_draft("crawl_status = ? AND domain_matched = 0", [CRAWL_STATUS_ORGANIZED], order="ORDER BY relevance DESC, id DESC")
        res_unmatched = q_draft("crawl_status = ?", [CRAWL_STATUS_UNMATCHED])
        res_failed = q_draft("crawl_status = ?", [CRAWL_STATUS_PARSE_FAILED])
        res_duplicate = q_draft("crawl_status = ?", [CRAWL_STATUS_DUPLICATE])
        res_whitelist = q_draft("crawl_status = ?", [CRAWL_STATUS_WHITELIST])
        res_ignored = q_draft("crawl_status = ?", [CRAWL_STATUS_IGNORED])

        # 合并为 3 个大 Tab 的数据
        tab1_todo = res_confirmed + res_unknown + res_unmatched
        tab2_pending = res_pending + res_failed
        tab3_archive = res_duplicate + res_whitelist + res_ignored

        # 统计数据
        manual_count = conn.execute("SELECT COUNT(*) FROM draft_links WHERE attr = '手动获取'").fetchone()[0]
        stats = {
            "明确资源": len(res_confirmed),
            "未知网站": len(res_unknown),
            "关键词未命中": len(res_unmatched),
            "手动拾取": manual_count,
            "待解析": len(res_pending),
            "解析失败": len(res_failed),
            "已在库": len(res_duplicate),
            "白名单": len(res_whitelist),
            "已永删": len(res_ignored),
        }

        return {
            "pending": res_pending, "confirmed": res_confirmed, "unknown": res_unknown,
            "unmatched": res_unmatched, "failed": res_failed, "duplicate": res_duplicate,
            "whitelist": res_whitelist, "ignored": res_ignored, "history": history_records,
            "tab1_todo": tab1_todo,
            "tab2_pending": tab2_pending,
            "tab3_archive": tab3_archive,
            "stats": stats
        }
    finally:
        conn.close()