# db_schema.py
"""数据库结构定义与初始化。

职责：
  - 所有 CREATE TABLE / ALTER TABLE / 索引 / 初始数据填充
  - 对外只暴露一个入口 ensure_schema()
  - 内部自动检测用户切换并重跑

与 db_config 的分工：
  - db_config：管连哪个库（用户身份 / 路径 / 连接 / 事务）
  - db_schema：管库里长什么样（表 / 列 / 索引 / 约束）

依赖方向：db_schema → db_config（单向，无环）
"""
import os
import re
import time
import sqlite3
import threading

from db_config import get_conn, get_current_user
from app_paths import get_user_dir


# ============================================================
# 表结构常量
# ============================================================
_ALNUM_CHECK_SQL = "NOT GLOB '*[^A-Za-z0-9]*'"

BOOKS_COLUMNS_SQL = f"""(
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform_id   TEXT CHECK (platform_id IS NULL OR platform_id {_ALNUM_CHECK_SQL}),
    name          TEXT UNIQUE NOT NULL,
    pen_name      TEXT,
    aliases       TEXT,
    evidence_path TEXT,
    rights_holder TEXT,
    baidu_id      TEXT CHECK (baidu_id IS NULL OR baidu_id {_ALNUM_CHECK_SQL})
)"""

EVIDENCES_COLUMNS_SQL = """(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_type TEXT NOT NULL,
    owner_key  TEXT NOT NULL,
    ev_type    TEXT,
    file_path  TEXT,
    UNIQUE (owner_type, owner_key, ev_type)
)"""

# 28 列 = 原 db_config 的 26 列 + parent_url + parent_title
DRAFT_LINKS_COLUMNS_SQL = """(
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id            TEXT,
    category            TEXT,
    site_url            TEXT,
    name                TEXT,
    body_text           TEXT,
    source_site         TEXT,
    attr                TEXT,
    feature_code        TEXT,
    complaint_time      TEXT,
    resource_status     TEXT,
    book_id             INTEGER,
    book_name           TEXT,
    crawl_status        TEXT,
    source_platform     TEXT DEFAULT '',
    source_keyword      TEXT DEFAULT '',
    source_url          TEXT DEFAULT '',
    fetch_time          TEXT DEFAULT '',
    target_platform     TEXT DEFAULT '',
    match_status        TEXT DEFAULT '',
    domain_matched      INTEGER DEFAULT 0,
    relevance           REAL DEFAULT 0,
    relevance_hits      TEXT,
    duplicate_of_id     INTEGER,
    duplicate_of_table  TEXT,
    has_pan             INTEGER DEFAULT 0,
    parent_url          TEXT,
    parent_title        TEXT,
    is_mobile           INTEGER DEFAULT 0,
    first_url           TEXT,
    second_url          TEXT
)"""

# 26 列并集：init_db 版 ∪ ensure_db_columns 版
# 注意：site_url 保留 UNIQUE（archive_batch_by_ids 依赖它防重复）
SITES_COLUMNS_SQL = """(
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    category            TEXT,
    site_url            TEXT UNIQUE,
    name                TEXT,
    body_text           TEXT,
    source_site         TEXT,
    attr                TEXT,
    feature_code        TEXT,
    complaint_time      TEXT,
    resource_status     TEXT,
    book_id             INTEGER,
    book_name           TEXT,
    crawl_status        TEXT,
    source_platform     TEXT DEFAULT '',
    source_keyword      TEXT DEFAULT '',
    source_url          TEXT DEFAULT '',
    fetch_time          TEXT DEFAULT '',
    target_platform     TEXT DEFAULT '',
    match_status        TEXT DEFAULT '',
    batch_id            TEXT,
    domain_matched      INTEGER DEFAULT 0,
    relevance           REAL DEFAULT 0,
    relevance_hits      TEXT,
    original_title      TEXT,
    parent_url          TEXT,
    parent_title        TEXT,
    is_mobile           INTEGER DEFAULT 0,
    first_url           TEXT,          -- ★ 补
    second_url          TEXT,          -- ★ 补
    picker_status       TEXT  -- ★ 新增这一行
)"""


# ============================================================
# 幂等控制
# ============================================================
_schema_checked = False
_schema_user_id = None
_schema_lock = threading.Lock()


def reset_schema_check():
    """强制重置幂等标志（测试用；正常运行靠 user_id 比较自动触发）"""
    global _schema_checked, _schema_user_id
    with _schema_lock:
        _schema_checked = False
        _schema_user_id = None


# ============================================================
# 工具函数
# ============================================================
def _add_column_if_missing(cursor, table, col_name, col_type):
    """安全新增列：只吞'列已存在'，其余 OperationalError 重新抛出"""
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise


def _parse_columns(columns_sql):
    """从建表 SQL 的列定义块解析出 [(col_name, col_def), ...]，跳过表级约束。

    以 *COLUMNS_SQL 为唯一事实来源，新增列只需改建表常量。
    """
    body = columns_sql.strip()
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1]
    cols = []
    for raw in body.splitlines():
        # 去掉行内 -- 注释、行尾逗号
        line = re.sub(r"--.*$", "", raw).strip().rstrip(",").strip()
        if not line:
            continue
        # 跳过表级约束
        if line.upper().startswith(("UNIQUE", "PRIMARY KEY", "FOREIGN KEY", "CHECK", "CONSTRAINT")):
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        col_name = parts[0].strip('"`[]')
        cols.append((col_name, parts[1].strip()))
    return cols


def _alter_safe_col_def(col_def):
    """清洗列定义，去掉 ALTER TABLE ADD COLUMN 不支持的约束。"""
    d = re.sub(r"\bPRIMARY\s+KEY\b", "", col_def, flags=re.I)
    d = re.sub(r"\bAUTOINCREMENT\b", "", d, flags=re.I)
    d = re.sub(r"\bUNIQUE\b", "", d, flags=re.I)
    d = re.sub(r"\s+", " ", d).strip()
    # NOT NULL 无 DEFAULT 时，ALTER 会失败，降级为可空
    if re.search(r"\bNOT\s+NULL\b", d, flags=re.I) and not re.search(r"\bDEFAULT\b", d, flags=re.I):
        d = re.sub(r"\bNOT\s+NULL\b", "", d, flags=re.I)
        d = re.sub(r"\s+", " ", d).strip()
    return d


def _sync_table_columns(cursor, table, columns_sql):
    """以建表 SQL 为准，自动补齐老库缺失的列（幂等）。"""
    existing = {r[1] for r in cursor.execute(f"PRAGMA table_info({table})").fetchall()}
    added = []
    for col_name, col_def in _parse_columns(columns_sql):
        if col_name in existing:
            continue
        safe_def = _alter_safe_col_def(col_def)
        try:
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col_name}" {safe_def}')
            added.append(col_name)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    if added:
        print(f"[DB迁移] {table} 自动补列：{added}")


def _books_has_alnum_check(cursor):
    row = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='books'"
    ).fetchone()
    if not row or not row[0]:
        return False
    return "[^A-Za-z0-9]" in row[0]


def _clean_invalid_book_ids(cursor):
    bad = []
    for col in ("platform_id", "baidu_id"):
        rows = cursor.execute(
            f"SELECT id, name, {col} FROM books "
            f"WHERE {col} IS NOT NULL AND {col} != '' "
            f"AND {col} GLOB '*[^A-Za-z0-9]*'"
        ).fetchall()
        for r in rows:
            bad.append({"id": r[0], "name": r[1], "field": col, "value": r[2]})
        if rows:
            cursor.execute(
                f"UPDATE books SET {col}=NULL "
                f"WHERE {col} IS NOT NULL AND {col} != '' "
                f"AND {col} GLOB '*[^A-Za-z0-9]*'"
            )
    return bad


def _log_migration(records):
    if not records:
        return
    uid = get_current_user()
    if not uid:
        return
    log_dir = os.path.join(get_user_dir(uid), "logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"db_migrate_{ts}.log")
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("以下记录因【晋江书号 / 百度ID】包含非字母数字字符，已被置为 NULL：\n")
            f.write("如需保留，请手工修正后重新填写。\n\n")
            for r in records:
                f.write(f"  id={r['id']}  作品={r['name']}  字段={r['field']}  原值={r['value']!r}\n")
        print(f"[DB迁移] 已清洗 {len(records)} 条非法ID记录，日志：{log_path}")
    except Exception as e:
        print(f"[DB迁移] 写日志失败: {e}")


def _migrate_books_add_check(conn, cursor):
    """重建 books 表以加 CHECK 约束（事务化，防止半迁移状态）"""
    bad_records = _clean_invalid_book_ids(cursor)
    conn.commit()

    old_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor.execute(f"CREATE TABLE books_new {BOOKS_COLUMNS_SQL}")
                cursor.execute('''
                               INSERT INTO books_new
                               (id, platform_id, name, pen_name, aliases, evidence_path, rights_holder, baidu_id)
                               SELECT id, platform_id, name, pen_name, aliases, evidence_path, rights_holder, baidu_id
                               FROM books
                               ''')
                cursor.execute("DROP TABLE books")
                cursor.execute("ALTER TABLE books_new RENAME TO books")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        print("[DB迁移] books 表已重建，platform_id / baidu_id 已加 CHECK 约束")
    finally:
        conn.isolation_level = old_isolation

    _log_migration(bad_records)


# ============================================================
# 各表建表 / 补列
# ============================================================
def _ensure_books(conn, cursor):
    cursor.execute(f"CREATE TABLE IF NOT EXISTS books {BOOKS_COLUMNS_SQL}")
    _sync_table_columns(cursor, "books", BOOKS_COLUMNS_SQL)
    conn.commit()
    if not _books_has_alnum_check(cursor):
        _migrate_books_add_check(conn, cursor)


def _ensure_sites(conn, cursor):
    cursor.execute(f"CREATE TABLE IF NOT EXISTS sites {SITES_COLUMNS_SQL}")
    _sync_table_columns(cursor, "sites", SITES_COLUMNS_SQL)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sites_first_url    ON sites(first_url)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sites_second_url   ON sites(second_url)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sites_feature_code ON sites(feature_code)")
    conn.commit()

def _ensure_draft_links(conn, cursor):
    cursor.execute(f"CREATE TABLE IF NOT EXISTS draft_links {DRAFT_LINKS_COLUMNS_SQL}")
    _sync_table_columns(cursor, "draft_links", DRAFT_LINKS_COLUMNS_SQL)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_draft_links_batch   ON draft_links(batch_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_draft_links_status  ON draft_links(crawl_status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_draft_links_feature ON draft_links(feature_code)")
    conn.commit()


def _ensure_records(conn, cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS records
                      (id           INTEGER PRIMARY KEY AUTOINCREMENT,
                       date         TEXT,
                       attr         TEXT,
                       platform_id  TEXT,
                       book_name    TEXT,
                       url          TEXT UNIQUE)''')
    conn.commit()


def _ensure_evidences(conn, cursor):
    ev_exists = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='evidences'"
    ).fetchone()

    need_migration = False
    if ev_exists:
        cols = [r[1] for r in cursor.execute("PRAGMA table_info(evidences)").fetchall()]
        if "book_id" in cols and "owner_type" not in cols:
            cursor.execute("ALTER TABLE evidences RENAME TO evidences_old")
            need_migration = True

    cursor.execute(f"CREATE TABLE IF NOT EXISTS evidences {EVIDENCES_COLUMNS_SQL}")

    if need_migration:
        for b_id, ev_type, f_path in cursor.execute(
                "SELECT book_id, ev_type, file_path FROM evidences_old"
        ).fetchall():
            if b_id == 0:
                cursor.execute(
                    "INSERT OR IGNORE INTO evidences (owner_type, owner_key, ev_type, file_path) "
                    "VALUES ('holder', '__default__', ?, ?)", (ev_type, f_path))
            else:
                cursor.execute(
                    "INSERT OR IGNORE INTO evidences (owner_type, owner_key, ev_type, file_path) "
                    "VALUES ('book', ?, ?, ?)", (str(b_id), ev_type, f_path))
        cursor.execute("DROP TABLE evidences_old")
    conn.commit()


def _ensure_ignored_links(conn, cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS ignored_links
                      (
                          id
                          INTEGER
                          PRIMARY
                          KEY
                          AUTOINCREMENT,
                          site_url
                          TEXT
                          UNIQUE,
                          add_time
                          TEXT
                      )''')
    _add_column_if_missing(cursor, "ignored_links", "title", "TEXT")
    _add_column_if_missing(cursor, "ignored_links", "first_url", "TEXT")  # ★ 新增
    _add_column_if_missing(cursor, "ignored_links", "second_url", "TEXT")  # ★ 新增
    _add_column_if_missing(cursor, "ignored_links", "feature_code", "TEXT")

    # ★ 新增：为了 OR 查询走索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ignored_first_url  ON ignored_links(first_url)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ignored_second_url ON ignored_links(second_url)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ignored_feature_code ON ignored_links(feature_code)")
    conn.commit()

    # ★ 自动补全老数据的特征值（兼容历史记录）
    rows = cursor.execute(
        "SELECT id, site_url, first_url, second_url FROM ignored_links WHERE feature_code IS NULL OR feature_code = ''"
    ).fetchall()
    if rows:
        try:
            from feature_extractor import extract_pure_feature
            for r in rows:
                # ★ 修复优先级：优先 site_url，其次 second_url，最后 first_url
                u = r[1] or r[3] or r[2]
                if u:
                    code = extract_pure_feature(u)
                    if code:
                        cursor.execute("UPDATE ignored_links SET feature_code = ? WHERE id = ?", (code, r[0]))
            conn.commit()
            print(f"[DB迁移] ✅ 已为 {len(rows)} 条老黑名单记录自动补全特征值。")
        except Exception as e:
            print(f"[DB迁移] ⚠️ 补全黑名单特征值失败: {e}")


def _ensure_user_signals(conn, cursor):
    # 权威版本：带 weight 列（organize_pending_links 依赖它计算相关度）
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_signals
                      (id       INTEGER PRIMARY KEY AUTOINCREMENT,
                       word     TEXT UNIQUE NOT NULL,
                       weight   REAL DEFAULT 1.0,
                       add_time TEXT)''')
    # 老库补齐 weight 列
    _add_column_if_missing(cursor, "user_signals", "weight", "REAL DEFAULT 1.0")
    conn.commit()


def _ensure_search_tasks(conn, cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS search_tasks
                      (id             INTEGER PRIMARY KEY AUTOINCREMENT,
                       book_id        INTEGER,
                       book_name      TEXT NOT NULL,
                       platform       TEXT NOT NULL,
                       main_keyword   TEXT NOT NULL,
                       sub_keywords   TEXT,
                       max_pages      INTEGER DEFAULT 3,
                       status         TEXT DEFAULT 'ready',
                       last_batch_id  TEXT,
                       created_at     TEXT,
                       last_run_at    TEXT)''')
    conn.commit()


def _ensure_search_logs(conn, cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS search_logs
                      (id             INTEGER PRIMARY KEY AUTOINCREMENT,
                       book_id        INTEGER,
                       search_keyword TEXT,
                       keyword_type   TEXT,
                       search_time    TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()


def _ensure_book_keywords(conn, cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS book_keywords
                      (id           INTEGER PRIMARY KEY AUTOINCREMENT,
                       book_id      INTEGER NOT NULL,
                       keyword      TEXT NOT NULL,
                       keyword_type TEXT NOT NULL,
                       UNIQUE (book_id, keyword, keyword_type),
                       FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE)''')
    _sync_book_keywords(conn, cursor)


def _sync_book_keywords(conn, cursor):
    books = cursor.execute("SELECT id, name, pen_name, aliases FROM books").fetchall()
    for b_id, name, pen_name, aliases in books:
        if name:
            cursor.execute("INSERT OR IGNORE INTO book_keywords (book_id, keyword, keyword_type) VALUES (?, ?, 'name')",
                           (b_id, name.strip()))
        if pen_name:
            cursor.execute("INSERT OR IGNORE INTO book_keywords (book_id, keyword, keyword_type) VALUES (?, ?, 'pen_name')",
                           (b_id, pen_name.strip()))
        if aliases:
            for a in re.split(r'[,，/\\|、\s]+', aliases):
                a = a.strip()
                if a:
                    cursor.execute("INSERT OR IGNORE INTO book_keywords (book_id, keyword, keyword_type) VALUES (?, ?, 'alias')",
                                   (b_id, a))
    conn.commit()


def _ensure_site_rules(conn, cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS site_rules
                      (id          INTEGER PRIMARY KEY AUTOINCREMENT,
                       domain      TEXT UNIQUE NOT NULL,
                       category    TEXT,
                       strategy    TEXT,
                       description TEXT)''')
    count = cursor.execute("SELECT COUNT(*) FROM site_rules").fetchone()[0]
    if count == 0:
        cursor.executemany(
            "INSERT INTO site_rules (domain, category, strategy, description) VALUES (?, ?, ?, ?)",
            [
                ("jjwxc", "白名单", "ignore", "晋江正版，直接跳过"),
                ("qidian.com", "白名单", "ignore", "起点正版，直接跳过"),
                ("zongheng.com", "白名单", "ignore", "纵横正版，直接跳过"),
                ("pan.baidu.com", "网盘", "netdisk", "百度网盘直链"),
                ("pan.quark.cn", "网盘", "netdisk", "夸克网盘直链"),
                ("aliyundrive.com", "网盘", "netdisk", "阿里云盘直链"),
                ("alipan.com", "网盘", "netdisk", "阿里云盘直链"),
            ]
        )
        print("[DB初始化] 已写入默认网站规则。")
    conn.commit()


def _ensure_authors(conn, cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS authors
                      (id         INTEGER PRIMARY KEY AUTOINCREMENT,
                       name       TEXT UNIQUE NOT NULL,
                       phone      TEXT,
                       id_card    TEXT UNIQUE,
                       email      TEXT,
                       updated_at TEXT)''')
    conn.commit()


# ============================================================
# 自检
# ============================================================
def _self_check(conn):
    """校验关键表/列齐全，只报错不抛错。"""
    def missing_of_sql(table, columns_sql):
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        return [f"{table}.{c}" for c, _ in _parse_columns(columns_sql) if c not in existing]

    missing = []
    missing += missing_of_sql("books", BOOKS_COLUMNS_SQL)
    missing += missing_of_sql("sites", SITES_COLUMNS_SQL)
    missing += missing_of_sql("draft_links", DRAFT_LINKS_COLUMNS_SQL)

    # 内联建表的表保留手写检查
    def missing_of(table, cols):
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        return [f"{table}.{c}" for c in cols if c not in existing]

    missing += missing_of("user_signals", ["weight"])

    ev = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='authors'"
    ).fetchone()
    if not ev:
        missing.append("authors 表不存在")
    else:
        missing += missing_of("authors", ["name", "phone", "id_card", "email"])

    if missing:
        print(f"[DB自检] ⚠️ 缺列/缺表：{missing}")
    else:
        print("[DB自检] ✅ 关键列齐全")


# ============================================================
# 主入口
# ============================================================
def ensure_schema(force=False):
    """确保当前用户的库结构完整（幂等）。

    - 首次调用：建表 + 迁移 + 初始数据
    - 同用户再次调用：直接 return
    - 切换用户后：自动重跑（user_id 比较）
    - force=True：强制重跑
    """
    global _schema_checked, _schema_user_id

    uid = get_current_user()
    if not uid:
        raise Exception("未设置当前登录用户，无法初始化数据库")

    with _schema_lock:
        if not force and _schema_checked and _schema_user_id == uid:
            return

        conn = get_conn()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            cursor = conn.cursor()

            _ensure_books(conn, cursor)
            _ensure_authors(conn, cursor)
            _ensure_sites(conn, cursor)
            _ensure_draft_links(conn, cursor)
            _ensure_records(conn, cursor)
            _ensure_evidences(conn, cursor)
            _ensure_ignored_links(conn, cursor)
            _ensure_user_signals(conn, cursor)
            _ensure_search_tasks(conn, cursor)
            _ensure_search_logs(conn, cursor)
            _ensure_book_keywords(conn, cursor)
            _ensure_site_rules(conn, cursor)

            _self_check(conn)
            conn.commit()

            _schema_checked = True
            _schema_user_id = uid
            print(f"[DB] ensure_schema 完成，用户={uid}")
        finally:
            conn.close()