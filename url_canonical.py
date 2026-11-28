# url_canonical.py
"""site_url 标准格式（canonical）引擎 —— 纯离线，不联网。

职责：
  - 判断一条链接是否属于“有固定标准格式”的平台；
  - 用标准模板重新拼出规范链接；
  - 告诉调用方：已合规 / 可修复 / 平台不适用 / 无法识别特征值。

不做什么：
  - 不联网判断链接是否失效（那是 url_invalid_judge.py 的职责）；
  - 不处理短链/中转页（入链时的统一编排在 url_normalize.py，
    真正的联网展开在 link_resolver.py）；
  - 不弹窗、不写库（询问与写库由 page_links 负责）。

数据来源：
  - 平台域名：app_data/complaint_platform_consts.py 的 PLATFORM_DOMAINS
  - 标准格式：app_data/url_canonical_rules.py
  - 特征值  ：app_data/complaint_link_features.py（经 feature_extractor 提取）

对外 API：
    canonicalize(url)        -> CanonicalResult
    canonical_url(url)       -> str      # 已合规/可修复时返回标准链接，否则 ""
    is_canonical(url)        -> bool
    canonicalize_many(urls)  -> list[CanonicalResult]
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app_data.complaint_platform_consts import PLATFORM_DOMAINS
from app_data.url_canonical_rules import URL_CANONICAL_RULES
from feature_extractor import extract_pure_feature


# =========================================================
# 统一结果
# =========================================================
@dataclass
class CanonicalResult:
    url: str
    platform: str = ""
    canonical: str = ""
    feature: str = ""
    status: str = "no_rule"   # ok / fixable / unparsable / no_rule
    reason: str = ""

    @property
    def needs_fix(self) -> bool:
        return self.status == "fixable"

    @property
    def is_canonical(self) -> bool:
        return self.status == "ok"

    def __str__(self):
        return f"[{self.platform or '未知':6s}] {self.status:9s} {self.reason:24s} {self.url[:70]}"


# =========================================================
# 启动校验 + 域名索引
# =========================================================
_missing = [p for p in URL_CANONICAL_RULES if p not in PLATFORM_DOMAINS]
if _missing:
    raise RuntimeError(
        "url_canonical_rules.py 中的平台未在 "
        f"complaint_platform_consts.PLATFORM_DOMAINS 登记：{_missing}"
    )

_DOMAIN_INDEX = {}
for _platform in URL_CANONICAL_RULES:
    for _domain in PLATFORM_DOMAINS.get(_platform, []):
        _domain = _domain.lower()
        if "." not in _domain:      # "agent_id" 之类的占位符，跳过
            continue
        _DOMAIN_INDEX[_domain] = _platform

# 长域名优先，避免短域名抢占（例如 m.xxx.com 与 xxx.com）
_DOMAINS_BY_LEN = sorted(_DOMAIN_INDEX, key=len, reverse=True)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def find_platform(url: str) -> str:
    """返回命中的平台 display_name；未命中返回空串。"""
    host = _host(url)
    if not host:
        return ""
    for domain in _DOMAINS_BY_LEN:
        if host == domain or host.endswith("." + domain):
            return _DOMAIN_INDEX[domain]
    return ""


def _lower_host_only(url: str) -> str:
    """只把协议 + 域名转小写，路径/查询原样保留（路径可能区分大小写）。"""
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*://)([^/?#]*)(.*)$", url)
    if not m:
        return url
    return m.group(1) + m.group(2).lower() + m.group(3)


# =========================================================
# 主入口
# =========================================================
def canonicalize(url: str) -> CanonicalResult:
    """按规则表判断并拼装标准链接。不联网、不抛异常。"""
    raw = (url or "").strip()
    res = CanonicalResult(url=raw)
    if not raw:
        res.reason = "空链接"
        return res

    # 允许用户只填 zhidao.baidu.com/question/xxx.html 这种没有协议的写法
    probe = _lower_host_only(raw if "://" in raw else "https://" + raw)
    platform = find_platform(probe)
    if not platform:
        res.reason = "该平台没有标准格式规则"
        return res

    res.platform = platform
    rule = URL_CANONICAL_RULES[platform]

    feature = (extract_pure_feature(probe) or "").strip()
    pattern = rule.get("feature_pattern") or r".+"

    # extract_pure_feature 未命中任何规则时会原样返回整条链接，必须校验形状
    if not feature or feature == probe or not re.fullmatch(pattern, feature):
        res.status = "unparsable"
        res.reason = "命中平台，但无法提取有效特征值"
        return res

    res.feature = feature
    res.canonical = rule["template"].format(feature=feature)

    if res.canonical == raw:
        res.status = "ok"
        res.reason = "已是标准链接"
    else:
        res.status = "fixable"
        res.reason = "可替换为标准链接"
    return res


def canonical_url(url: str) -> str:
    """已合规或可修复时返回标准链接；其它情况返回空串。"""
    result = canonicalize(url)
    if result.status in ("ok", "fixable"):
        return result.canonical
    return ""


def is_canonical(url: str) -> bool:
    return canonicalize(url).is_canonical


def canonicalize_many(urls: list) -> list:
    return [canonicalize(u) for u in (urls or [])]
