# url_invalid_judge.py
"""链接失效判断统一入口。

对外 API：
    check_url(url) -> CheckResult
    is_invalid(url) -> bool            # True 表示确认失效
    batch_check(urls) -> list[CheckResult]
"""
import html as _html
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

from app_data.complaint_platform_consts import PLATFORM_DOMAINS
from app_data.url_invalid_rules import (
    PLATFORM_CHECK_RULES,
    DEFAULT_UA,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_API_TIMEOUT,
)


_RULE_INDEX = {k.lower(): v for k, v in PLATFORM_CHECK_RULES.items()}


# =========================================================
# 统一结果
# =========================================================
@dataclass
class CheckResult:
    url: str
    platform: str = "未知"
    status: str = "unknown"   # invalid / valid / unknown / blocked / error
    reason: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def is_invalid(self) -> bool:
        return self.status == "invalid"

    def __str__(self):
        return f"[{self.platform:8s}] {self.status:8s} {self.reason:30s} {self.url[:70]}"


# =========================================================
# 域名索引
# =========================================================
def _build_domain_index() -> dict:
    index = {}
    for display_name, rule in PLATFORM_CHECK_RULES.items():
        if rule.get("checker") == "skip":
            continue
        domains = rule.get("domains_override") or PLATFORM_DOMAINS.get(display_name, [])
        for d in domains:
            if "." not in d:   # "agent_id" 占位符
                continue
            index[d.lower()] = (display_name, rule)
    return index

_DOMAIN_INDEX = _build_domain_index()


def _match(url: str):
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return None, None
    for d in sorted(_DOMAIN_INDEX, key=len, reverse=True):
        if host == d or host.endswith("." + d):
            return _DOMAIN_INDEX[d]
    return None, None


# =========================================================
# 纯文本判定（HTTP / Playwright 共用）
# =========================================================
def _judge_text(text: str, final_url: str, rule: dict) -> dict:
    """对「已渲染的完整 HTML」做特征判定。

    Args:
        text: 页面正文（HTML 字符串）。
        final_url: 最终 URL，用于 final_url 关键词匹配。
        rule: 平台规则。
    """
    # 拦截页必须先判，否则会被 must_have 误判成 invalid
    for kw in rule.get("blocked_keywords", []):
        if kw in text:
            return {"status": "blocked", "reason": f"被拦截: {kw}"}

    for kw in rule.get("invalid_keywords", []):
        if kw in text:
            return {"status": "invalid", "reason": f"命中失效特征: {kw}"}

    # 正向断言（仅用于 100% 能拿到完整 HTML 的平台）
    must_have = rule.get("must_have")
    if must_have and not any(kw in text for kw in must_have):
        return {"status": "invalid",
                "reason": f"缺少有效特征（{len(must_have)} 项均未命中）",
                "extra": {"final_url": final_url, "len": len(text)}}

    for kw in rule.get("valid_keywords", []):
        if kw in text:
            return {"status": "valid", "reason": f"命中有效特征: {kw}"}

    return {"status": "unknown", "reason": "未命中任何特征",
            "extra": {"final_url": final_url, "len": len(text)}}


# =========================================================
# HTTP 检查器（★ 编码修复在这里）
# =========================================================
def _check_http(url, domain, rule):
    s = requests.Session()
    s.headers.update({
        "User-Agent": DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": f"https://{domain}/",
        "Accept-Encoding": "identity",
    })
    try:
        prewarm = rule.get("prewarm")
        if prewarm:
            if isinstance(prewarm, str):
                prewarm = [prewarm]
            for p in prewarm:
                try:
                    s.get(p, timeout=10)
                except Exception:
                    pass
        r = s.get(url, timeout=DEFAULT_HTTP_TIMEOUT, allow_redirects=True)
    except Exception as e:
        return {"status": "error", "reason": f"请求异常: {e}"}

    if any(kw in url for kw in rule.get("spa_path_keywords", [])):
        return {"status": "blocked", "reason": "SPA 渲染页，HTTP 无法判断"}

    if r.status_code in (403, 412):
        return {"status": "blocked", "reason": f"HTTP {r.status_code}"}

    if r.status_code in rule.get("invalid_status_codes", []):  # 新增
        return {"status": "invalid",
                "reason": f"HTTP {r.status_code}",
                "extra": {"final_url": r.url}}

    for kw in rule.get("invalid_final_url_keywords", []):  # 新增
        if kw in r.url:
            return {"status": "invalid", "reason": f"final_url 命中: {kw}",
                    "extra": {"final_url": r.url}}

    # ★ 显式 UTF-8 解码，不依赖 requests 猜测
    try:
        text = r.content.decode("utf-8")
    except UnicodeDecodeError:
        text = r.content.decode("gbk", errors="ignore")
    text = _html.unescape(text)
    return _judge_text(text, r.url, rule)


# =========================================================
# API 检查器
# =========================================================
def _extract_share_id(url: str) -> str:
    m = re.search(r"/s/([0-9a-zA-Z]+)", url)
    return m.group(1) if m else ""


def _check_quark_api(url: str, rule: dict) -> dict:
    share_id = _extract_share_id(url)
    if not share_id:
        return {"status": "error", "reason": "无法提取 share_id"}

    try:
        r = requests.post(
            "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token",
            params={"pr": "ucpro", "fr": "pc", "uc_param_str": ""},
            json={"pwd_id": share_id, "passcode": ""},
            headers={"User-Agent": DEFAULT_UA,
                     "Referer": "https://pan.quark.cn/",
                     "Origin": "https://pan.quark.cn"},
            timeout=DEFAULT_API_TIMEOUT,
        )
        data = r.json()
    except Exception as e:
        return {"status": "error", "reason": f"请求异常: {e}"}

    return _judge_api(data, rule)


def _check_uc_api(url: str, rule: dict) -> dict:
    share_id = _extract_share_id(url)
    if not share_id:
        return {"status": "error", "reason": "无法提取 share_id"}

    s = requests.Session()
    s.headers.update({"User-Agent": DEFAULT_UA})
    try:
        prewarm = rule.get("prewarm")
        if prewarm:
            if isinstance(prewarm, str):
                prewarm = [prewarm]
            for p in prewarm:
                try:
                    s.get(p, timeout=10)
                except Exception:
                    pass
        r = s.post(
            f"https://{rule['api_host']}/1/clouddrive/share/sharepage/token",
            params={"pr": "ucpro", "fr": "pc", "uc_param_str": ""},
            json={"pwd_id": share_id, "passcode": ""},
            headers={"Referer": "https://drive.uc.cn/",
                     "Origin": "https://drive.uc.cn",
                     "Content-Type": "application/json"},
            timeout=DEFAULT_API_TIMEOUT,
        )
        data = r.json()
    except Exception as e:
        return {"status": "error", "reason": f"请求异常: {e}"}

    return _judge_api(data, rule)


def _judge_api(data: dict, rule: dict) -> dict:
    """夸克 / UC 共用的 API 结果判定。"""
    code = data.get("code")
    payload = data.get("data") or {}   # 防 data 为 None
    if code == rule.get("valid_code", 0) and payload.get("stoken"):
        return {"status": "valid", "reason": "API code=0",
                "extra": {"title": payload.get("title"),
                          "author": (payload.get("author") or {}).get("nick_name")}}
    if code in rule.get("invalid_codes", []):
        return {"status": "invalid", "reason": f"API code={code}",
                "extra": {"message": data.get("message")}}
    return {"status": "unknown", "reason": f"API code={code}: {data.get('message')}"}


# =========================================================
# SPA 检查器（HTTP 不可判，明确返回 blocked）
# =========================================================
def _check_spa(url: str, rule: dict) -> dict:
    return {"status": "blocked",
            "reason": "SPA 页面，HTTP 无法判断，需人工或后续接 API"}


# =========================================================
# 浏览器检查器（Playwright 预留接口）
# =========================================================
# 约定：fetcher(url, rule) -> {"final_url": str, "status_code": int, "text": str} | None
#   - text 必须是浏览器渲染后的完整 HTML（不是 requests 拿到的空壳）；
#   - 其余判定全部交给 _judge_text，规则里的关键词不需要改。
# 接入方式：
#   from url_invalid_judge import register_browser_fetcher
#   register_browser_fetcher(my_playwright_fetch)
#   ！再把目标平台规则的 checker 改成 "browser"（例如百度贴吧）。
#   ！同时要把 url_invalid_batch._SUPPORTED_CHECKERS 加上 "browser"。
_browser_fetcher = None


def register_browser_fetcher(fetcher):
    """注入 Playwright 实现；传 None 表示卸载，回到「未接入」状态。"""
    global _browser_fetcher
    _browser_fetcher = fetcher


def _check_browser(url: str, rule: dict) -> dict:
    if _browser_fetcher is None:
        return {"status": "unknown",
                "reason": "浏览器检查器未接入（Playwright 预留）"}
    try:
        page = _browser_fetcher(url, rule)
    except Exception as e:
        return {"status": "error", "reason": f"浏览器异常: {e}"}
    if not page:
        return {"status": "error", "reason": "浏览器未返回内容"}

    final_url = page.get("final_url") or url
    status_code = page.get("status_code")

    # 浏览器检查器给出的结构化标记（DOM 特征 / 文案特征）
    if page.get("blocked_hit"):
        return {"status": "blocked",
                "reason": page.get("reason") or "命中拦截特征"}
    if page.get("invalid_hit"):
        return {"status": "invalid",
                "reason": page.get("reason") or "命中失效特征",
                "extra": {"final_url": final_url}}
    if page.get("valid_hit"):
        return {"status": "valid",
                "reason": page.get("reason") or "命中有效特征"}

    if status_code in rule.get("invalid_status_codes", []):
        return {"status": "invalid", "reason": f"HTTP {status_code}",
                "extra": {"final_url": final_url}}
    for kw in rule.get("invalid_final_url_keywords", []):
        if kw in final_url:
            return {"status": "invalid", "reason": f"final_url 命中: {kw}",
                    "extra": {"final_url": final_url}}

    return _judge_text(page.get("text") or "", final_url, rule)


# =========================================================
# 分派（只定义一次）
# =========================================================
_CHECKER_MAP = {
    "http": _check_http,
    "quark_api": _check_quark_api,
    "uc_api": _check_uc_api,
    "spa": _check_spa,
    "browser": _check_browser,
}


def check_url(url: str, platform_hint: str | None = None) -> CheckResult:
    if platform_hint:
        rule = _RULE_INDEX.get(platform_hint.lower())
        if rule:
            platform = platform_hint
        else:
            platform, rule = _match(url)
    else:
        platform, rule = _match(url)

    if not rule:
        return CheckResult(url=url, platform="未知", status="unknown",
                           reason="域名未接入判断")

    checker_name = rule.get("checker", "http")
    fn = _CHECKER_MAP.get(checker_name)
    if not fn:
        return CheckResult(url=url, platform=platform, status="unknown",
                           reason=f"未知 checker: {checker_name}")

    if checker_name == "http":
        domain = (urlparse(url).hostname or "").lower()
        r = fn(url, domain, rule)
    else:
        r = fn(url, rule)

    return CheckResult(url=url, platform=platform,
                       status=r.get("status", "unknown"),
                       reason=r.get("reason", ""),
                       extra=r.get("extra", {}))


def is_invalid(url: str) -> bool:
    return check_url(url).is_invalid


def batch_check(urls: list) -> list:
    return [check_url(u) for u in urls]