# link_handle_status.py
"""处理状况 —— 由「资源状态 + 投诉时间」推导出来的只读列，落库到 sites.handle_status。

判定顺序（与需求确认一致）：
    1. 未投诉（投诉时间为空）                                  → 待投诉
    2. 已投诉 + 资源状态 = 已失效 / 网站类                      → 已完成
    3. 已投诉 + 资源状态 = 暂无判断                             → 等待结果
       （skip / spa / 无规则，等后续接入 Playwright 才会变）
    4. 已投诉 + 资源状态 = 失败 / 未知（判断失败、没结论）        → 异常（立即）
    5. 已投诉 + 资源状态 = 有效 + 7 天内                        → 等待结果
    6. 已投诉 + 资源状态 = 有效 + 超过 7 天                     → 异常
    7. 已投诉 + 资源状态为空（还没判断过）                      → 等待结果

设计说明：
    - 结果写入 sites.handle_status（老库由 db_schema 自动补列）；
    - 增量重算：已经是「已完成」的行不再参与，其余才重算（见 recalculate_handle_statuses）；
    - 在表格里手工改「资源状态 / 投诉时间」时，会立刻重算该行并写回；
    - 投诉时间格式兼容 "YYYY-MM-DD HH:MM:SS" / "YYYY-MM-DD" 等常见写法；
      写了内容但解析不出时间的，按"已投诉"处理，但不判超期（宁可等待结果，不误报异常）。
"""
from datetime import datetime, timedelta

from app_data.resource_status_consts import (
    STATUS_INVALID, STATUS_WEBSITE, STATUS_VALID,
    STATUS_UNKNOWN, STATUS_FAILED, STATUS_PENDING,
)

# 四种展示文本（也是过滤下拉框的取值）
HANDLE_PENDING = "待投诉"
HANDLE_DONE = "已完成"
HANDLE_WAITING = "等待结果"
HANDLE_ABNORMAL = "异常"

HANDLE_ALL = (HANDLE_PENDING, HANDLE_DONE, HANDLE_WAITING, HANDLE_ABNORMAL)

# 下拉框 / 筛选顺序：(显示文字, 实际数据)，与资源状态 STATUS_OPTIONS 同款
HANDLE_OPTIONS = tuple((s, s) for s in HANDLE_ALL)

# 语义角色：样式层按角色取色 / 取加粗，不认中文
HANDLE_ROLE = {
    HANDLE_PENDING: "pending",
    HANDLE_DONE: "done",
    HANDLE_WAITING: "waiting",
    HANDLE_ABNORMAL: "abnormal",
}

# 已投诉后可以直接"完成"的资源状态
DEAD_STATUSES = (STATUS_INVALID, STATUS_WEBSITE)
# 已投诉后仍在等结果的资源状态（只有「有效」还要看 7 天窗口；
# 失败 / 未知 表示判断没结论，已投诉就立刻算异常）
RESOLVING_STATUSES = (STATUS_VALID,)
# 明确"没判"的状态，不区分时间，永远是等待结果
SKIP_STATUS = STATUS_PENDING

WAIT_DAYS = 7

_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
)

# 展示用颜色已移到 ui_styles.HANDLE_STATUS_TEXT_COLORS（按 HANDLE_ROLE 索引）


def parse_complaint_time(value):
    """把投诉时间字符串解析成 datetime；解析失败返回 None。"""
    text = (value or "").strip()
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def compute_handle_status(resource_status, complaint_time, now=None):
    """返回「处理状况」四值之一。"""
    complaint_text = (complaint_time or "").strip()
    if not complaint_text:
        return HANDLE_PENDING

    status = (resource_status or "").strip()

    if status in DEAD_STATUSES:
        return HANDLE_DONE
    if status == SKIP_STATUS:
        return HANDLE_WAITING

    # ★ 失败 / 未知：判断本身没得出结论，已投诉就立刻算异常，不再等 7 天
    if status in (STATUS_UNKNOWN, STATUS_FAILED):
        return HANDLE_ABNORMAL

    # 走到这里：已投诉，且状态是 有效（或空/未识别）
    complaint_dt = parse_complaint_time(complaint_text)
    if complaint_dt is None:
        # 有时间文字但认不出格式 → 当作刚投诉，不判异常
        return HANDLE_WAITING

    now = now or datetime.now()
    try:
        expired = (now - complaint_dt) > timedelta(days=WAIT_DAYS)
    except (OverflowError, TypeError):
        expired = False

    if status in RESOLVING_STATUSES:
        return HANDLE_ABNORMAL if expired else HANDLE_WAITING

    # 空状态 / 未识别的状态（例如手工输入了别的词）→ 还没结论，先等
    return HANDLE_WAITING


def summarize_handle_status(rows, now=None):
    """统计处理状况分布。rows: 可迭代的 (resource_status, complaint_time)。"""
    counts = {key: 0 for key in HANDLE_ALL}
    now = now or datetime.now()
    for resource_status, complaint_time in rows:
        counts[compute_handle_status(resource_status, complaint_time, now)] += 1
    return counts


def recalculate_handle_statuses():
    """增量重算并写库：已是「已完成」的行直接跳过，其余按最新状态重算。

    Returns:
        (updated, scanned, summary)
        updated: 实际发生变化的行数
        scanned: 参与重算的行数（不含被跳过的已完成）
        summary: 全库 {处理状况: 条数}，含被跳过的已完成
    """
    from db_config import get_conn

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, resource_status, complaint_time, handle_status FROM sites "
            "WHERE COALESCE(handle_status, '') <> ?",
            (HANDLE_DONE,),
        ).fetchall()

        now = datetime.now()
        updates = []
        for db_id, res_status, comp_time, old_status in rows:
            new_status = compute_handle_status(res_status, comp_time, now)
            if (old_status or "") != new_status:
                updates.append((new_status, db_id))

        if updates:
            conn.executemany("UPDATE sites SET handle_status=? WHERE id=?", updates)
            conn.commit()

        summary = {key: 0 for key in HANDLE_ALL}
        for text, count in conn.execute(
            "SELECT handle_status, COUNT(*) FROM sites GROUP BY handle_status"
        ).fetchall():
            if text in summary:
                summary[text] += count

        return len(updates), len(rows), summary
    finally:
        conn.close()
