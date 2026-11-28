# app_data/resource_status_consts.py
"""资源状态（sites.resource_status）词汇表 —— 纯数据，不含逻辑。

这里是「资源状态」的唯一事实源：
  - 落库的文字
  - 下拉框选项
  - 判断器结果 → 资源状态的映射
  - 给样式层用的语义角色（只给角色名，不给颜色）

不在这里：
  - 颜色 / 加粗 → ui_styles.STATUS_TEXT_COLORS / STATUS_BOLD_ROLES
  - 「处理状况」handle_status（另一套由资源状态推导出的只读状态）

以后加一个资源状态，只改本文件 + ui_styles 里补一个角色颜色即可，
ui_components 和 page_links 都不需要动。
"""

STATUS_INVALID = "已失效"
STATUS_WEBSITE = "网站类"
STATUS_VALID = "有效"
STATUS_UNKNOWN = "未知"
STATUS_FAILED = "失败"
STATUS_PENDING = "暂无判断"

# 下拉框顺序，也是写库时允许的取值集合
# ★ 改这里的顺序后必须重启程序：模块常量在进程启动时只求值一次，
#   运行中的进程不会热更新（表头筛选/单元格下拉都读它）。
ALL_RESOURCE_STATUSES = (
    STATUS_VALID,
    STATUS_UNKNOWN,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_INVALID,
    STATUS_WEBSITE,
)

# 语义角色：样式层按角色取色，绝不按中文文字判断
STATUS_ROLE = {
    STATUS_INVALID: "invalid",
    STATUS_WEBSITE: "website",
    STATUS_VALID: "valid",
    STATUS_UNKNOWN: "unknown",
    STATUS_FAILED: "failed",
    STATUS_PENDING: "pending",
}

# 下拉框选项：(显示文字, 实际数据)
STATUS_OPTIONS = (("（空白）", ""),) + tuple((s, s) for s in ALL_RESOURCE_STATUSES)

# 判断器结果 → 资源状态（url_invalid_judge / url_invalid_batch 共用）
CHECKER_STATUS_TO_RESOURCE = {
    "invalid": STATUS_INVALID,   # 已确认失效
    "website": STATUS_WEBSITE,   # search / other / pansite 模块，直接标网站类
    "valid": STATUS_VALID,
    "unknown": STATUS_UNKNOWN,
    "blocked": STATUS_FAILED,
    "error": STATUS_FAILED,
    "no_rule": STATUS_PENDING,   # 无规则 / skip / spa，未执行判断
}
