# links_repository.py

"""盗版链接页的数据库访问层：本页所有 sites / books 的 SQL 只在这里出现。

设计约定：
- 只做「取数 / 写数」，不含任何界面逻辑，也不 import PySide6；
- 对外返回纯 dict / tuple，页面直接交给 links_row_builder 建行；
- 连接一律 get_conn() → finally close，不缓存长连接。
"""

import sqlite3

from db_config import get_conn

# 链接行查询：书籍名用 JOIN 解析，避免页面里再遍历 book_list 做 O(n*m) 匹配。
# 规则与页面原逻辑一致：有 book_id 时只认 books.name（查不到就是空），
# 没有 book_id 时才退回 sites.book_name。
_LINK_SELECT = """
    SELECT s.id, s.target_platform, s.site_url, s.name, s.attr, s.feature_code,
           s.complaint_time, s.resource_status, s.book_id, s.book_name,
           s.crawl_status, s.picker_status, s.original_title, s.handle_status,
           CASE WHEN s.book_id IS NOT NULL THEN COALESCE(b.name, '')
                ELSE COALESCE(s.book_name, '') END AS book_display
    FROM sites s
    LEFT JOIN books b ON b.id = s.book_id
"""

_INSERT_SQL = """
    INSERT INTO sites
        (category, target_platform, site_url, name, attr, feature_code,
         complaint_time, resource_status, handle_status, book_id, book_name,
         crawl_status, picker_status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_UPDATE_SQL = """
    UPDATE sites
       SET category = ?, target_platform = ?, site_url = ?, name = ?, attr = ?,
           feature_code = ?, complaint_time = ?, resource_status = ?, handle_status = ?,
           book_id = ?, book_name = ?, crawl_status = ?, picker_status = ?
     WHERE id = ?
"""


def _to_record(row):
    return {
        "db_id": row[0],
        "category": row[1] or "",
        "site_url": row[2] or "",
        "source_site": row[3] or "",
        "attr": row[4] or "",
        "feature_code": row[5] or "",
        "complaint_time": row[6] or "",
        "resource_status": row[7] or "",
        "book_name": row[14] or "",
        "crawl_status": row[10] or "",
        "picker_status": row[11] or "",
        "name": row[12] or "",
        "handle_status": row[13],
    }


def fetch_books():
    """[(id, name, platform_id, evidence_path)]，供作品下拉框与书名解析使用。"""
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT id, name, platform_id, evidence_path FROM books").fetchall()
    finally:
        conn.close()


def fetch_links(target_cats=None):
    """target_cats=None 表示全部；否则按 target_platform 过滤（空分类始终包含）。"""
    conn = get_conn()
    try:
        if target_cats:
            placeholders = ",".join("?" * len(target_cats))
            sql = (f"{_LINK_SELECT} WHERE (s.target_platform IN ({placeholders}) "
                   f"OR s.target_platform IS NULL OR s.target_platform = '') "
                   f"ORDER BY s.id ASC")
            rows = conn.execute(sql, list(target_cats)).fetchall()
        else:
            rows = conn.execute(f"{_LINK_SELECT} ORDER BY s.id ASC").fetchall()
    finally:
        conn.close()
    return [_to_record(r) for r in rows]


def fetch_feature_counts():
    """{feature_code: 出现次数}，用于列表里标红重复链接。"""
    conn = get_conn()
    try:
        return dict(conn.execute(
            "SELECT feature_code, COUNT(*) FROM sites GROUP BY feature_code").fetchall())
    finally:
        conn.close()


def fetch_book_platform_ids():
    """{书名: 晋江书号}，导出表格时补「晋江书号」列用（同名书取第一条）。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT name, platform_id FROM books "
            "WHERE platform_id IS NOT NULL AND platform_id != ''").fetchall()
    finally:
        conn.close()
    mapping = {}
    for name, platform_id in rows:
        if name:
            mapping.setdefault(name, str(platform_id or ""))
    return mapping


def has_duplicate_feature(feature_code, exclude_id=None):
    """除自己以外是否已有相同特征码。"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM sites WHERE feature_code = ? AND id != ?",
            (feature_code, exclude_id or "0")).fetchone()
        return row[0] > 0
    finally:
        conn.close()


def save_link(record, db_id=None):
    """插入（db_id 为空）或更新一条链接。

    返回写入后的主键字符串；唯一约束冲突时返回 None（不抛给界面）。
    """
    params = (
        record.get("category") or "", record.get("category") or "",
        record.get("site_url") or "", record.get("source_site") or "",
        record.get("attr") or "", record.get("feature_code") or "",
        record.get("complaint_time") or "", record.get("resource_status"),
        record.get("handle_status"), record.get("book_id"),
        record.get("book_name") or "", record.get("crawl_status") or "",
        record.get("picker_status") or "",
    )
    conn = get_conn()
    try:
        cursor = conn.cursor()
        if db_id:
            cursor.execute(_UPDATE_SQL, params + (db_id,))
            conn.commit()
            return str(db_id)
        cursor.execute(_INSERT_SQL, params)
        conn.commit()
        return str(cursor.lastrowid)
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def delete_link(db_id):
    """删除单条（页面上把整行内容清空时用）。"""
    if not db_id:
        return 0
    conn = get_conn()
    try:
        cursor = conn.execute("DELETE FROM sites WHERE id=?", (db_id,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def delete_links(ids):
    """批量删除；一次事务，返回实际删除行数。"""
    ids = [i for i in ids if i]
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    conn = get_conn()
    try:
        cursor = conn.execute(f"DELETE FROM sites WHERE id IN ({placeholders})", ids)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
