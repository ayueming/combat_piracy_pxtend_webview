# app_data/crawl_consts.py
"""
抓取和解析相关参数配置
"""

import re

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


def is_short_url(url: str) -> bool:
    """命中短链规则即视为短链。规则与 SHORT_URL_PATTERNS 共用一份数据源。"""
    if not url:
        return False
    return any(re.search(p, url) for p in SHORT_URL_PATTERNS)


def is_intermediate_url(url: str) -> bool:
    """命中中转页规则即视为中转页（跳转未完成）。"""
    if not url:
        return False
    return any(re.search(p, url) for p in INTERMEDIATE_URL_PATTERNS)


# ====== 参数式中转链接（真实 URL 编码在 query 里，纯离线可解，无需网络）======
# {host+path 片段（小写子串）: 参数名}
PARAM_INTERMEDIATE_RULES = {
    "weibo.cn/sinaurl":  "u",
    "weibo.com/sinaurl": "u",
}

def is_param_intermediate_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return any(host in u for host in PARAM_INTERMEDIATE_RULES)

def try_extract_param_url(url: str):
    """从参数式中转 URL 里解出真实 URL。纯字符串操作，无网络。失败返回 None。"""
    from urllib.parse import urlparse, unquote
    u = url or ""
    u_lower = u.lower()

    # 1. 先按 host 规则找到对应参数名，避免误伤任意 ?u= 的普通 URL
    param_name = None
    for host, pname in PARAM_INTERMEDIATE_RULES.items():
        if host in u_lower:
            param_name = pname
            break
    if not param_name:
        return None

    query = urlparse(u).query
    if not query:
        return None

    # 2. 手动 split('&')，不用 parse_qs——后者会把 '+' 解成空格，base64 场景会踩坑
    for part in query.split("&"):
        k, _, v = part.partition("=")
        if unquote(k) != param_name:
            continue
        real = unquote(v)
        # 3. 协议白名单，防 javascript:/data:/file:
        if not real.lower().startswith(("http://", "https://")):
            return None
        return real
    return None

