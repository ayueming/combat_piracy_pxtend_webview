# app_data/crawl_consts.py
"""
抓取和解析相关参数配置
"""

# 最大抓取页数
CRAWL_MAX_PAGES = 3

# 页面首屏等待时间（毫秒），应对动态渲染
CRAWL_FIRST_WAIT_MS = 3000

# 翻页后的等待时间区间（毫秒），随机取值以反爬
CRAWL_PAGING_WAIT_MS_RANGE = (3500, 6000)

# 页面加载超时时间（毫秒）
CRAWL_PAGE_TIMEOUT_MS = 15000

# 百度跳转链接解析超时时间（毫秒）
CRAWL_REDIRECT_TIMEOUT_MS = 8000

# 短链判定规则（命中任意一条即视为短链，需要解析）
# 新增短链类型时，往这里追加正则即可；UI 层与解析层共用同一份规则
SHORT_URL_PATTERNS = [
    r"baidu\.com/link\?url=",
    r"m\.baidu\.com/from=0",
    r"mapp\.api\.weibo\.cn/fx/",   # 微博中转链接
]


# 中转页判定规则（命中即视为"跳转未完成"，继续交给 Playwright 兜底）
# 与 SHORT_URL_PATTERNS 语义不同：短链是入口，中转页是跳转过程中的中间站
INTERMEDIATE_URL_PATTERNS = [
    r"visitor\.passport\.weibo\.cn",   # 微博 visitor 中转页（200 + JS 跳转）
]
