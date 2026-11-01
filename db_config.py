# db_config.py
"""数据库连接层。

职责边界：
  - 管"连哪个库"：用户身份、路径解析、连接获取、事务上下文
  - 不管"库里长什么样"：所有 DDL / 迁移 / 初始数据已移至 db_schema.py

对外 API：
  set_current_user(user_id) / get_current_user()
  get_db_path() / get_conn() / db()
"""
import sqlite3
import os
from contextlib import contextmanager

from app_paths import get_user_dir, FILE_DB


# 全局变量，记住当前是谁在登录
_current_user_id = None


def set_current_user(user_id):
    """登录成功后，必须调用此函数，告诉数据库现在是谁在使用"""
    global _current_user_id
    _current_user_id = str(user_id)
    # 确保该用户的文件夹存在
    get_user_dir(_current_user_id)


def get_current_user():
    return _current_user_id


def get_db_path():
    """动态获取当前用户的数据库完整路径"""
    if not _current_user_id:
        raise Exception("系统异常：未设置当前登录用户，无法连接数据库。")
    return os.path.join(get_user_dir(_current_user_id), FILE_DB)


def get_conn():
    """获取当前用户的数据库连接。

    - foreign_keys=ON：PRAGMA foreign_keys 是"每连接生效"的，之前只在 init_db
      设过一次，导致后续所有连接的外键实际都是关闭的（book_keywords 的
      ON DELETE CASCADE 从未生效）。
    - timeout=5.0（等价 busy_timeout=5000ms）：给后台线程和 UI 线程争锁时
      留出等待窗口，避免 database is locked。
    """
    conn = sqlite3.connect(get_db_path(), timeout=5.0)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    """统一的连接获取方式：自动 close，异常时 rollback。

    用法：
        with db() as conn:
            conn.execute(...)
            conn.commit()
    """
    conn = get_conn()
    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()