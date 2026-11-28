# url_normalize.py
"""入链规整（用户填入链接时）的统一编排层。

为什么需要这一层：
  page_links 现在会碰到两类“链接不合规”，原先各走各的弹窗：
    1) 标准格式：百度知道等平台有固定标准链接（离线拼装）；
    2) 短链 / 中转：链接本身是入口或中间站，真实地址藏在跳转里。
  两者在用户眼里是同一件事（“要不要换成另一条链接”），但底层数据、
  能否离线完成、失败后果都不同。本层只负责“按固定顺序检查、给出建议”，
  让 UI 只面对一份 issue 清单，而不是两套互不知情的流程。

数据来源（这里不重复定义任何规则）：
  - 标准格式：app_data/url_canonical_rules.py（引擎 url_canonical.py）
  - 短链 / 中转页 / 参数式中转：app_data/crawl_consts.py

检查顺序：
  ① 参数式中转（纯离线，可多级还原，如微博 sinaurl?u=...）
  ② 标准格式（纯离线，基于①还原后的地址判断）
  ③ 短链 / 中转页（离线解不了，必须联网，交给 link_resolver）

对外 API：
    inspect_url(url)        -> list[UrlIssue]   # 全量问题清单（含联网类）
    apply_offline(url)      -> str              # 依次应用所有离线修复
    needs_online_unwrap(url)-> bool             # 是否还需要联网展开
"""
from dataclasses import dataclass

from app_data.crawl_consts import (
    is_short_url,
    is_intermediate_url,
    try_extract_param_url,
)
from url_canonical import canonicalize


@dataclass
class UrlIssue:
    """一条“链接不合规”问题。

    offline=True 时 suggested 一定可用，UI 可以直接问“是否替换”；
    offline=False 时表示必须联网，suggested 为空。
    """
    kind: str          # param_intermediate / canonical / short / intermediate
    label: str
    offline: bool
    current: str
    suggested: str = ""

    def __str__(self):
        return f"[{self.kind}] {self.label}"


def inspect_url(url: str) -> list:
    """返回这条链接的全部问题，按处理顺序排列。"""
    raw = (url or "").strip()
    if not raw:
        return []

    issues = []
    cur = raw

    # ① 参数式中转：真实地址就在 query 里，纯离线可解（可能嵌套）
    for _ in range(3):
        real = try_extract_param_url(cur)
        if not real or real == cur:
            break
        issues.append(UrlIssue(
            kind="param_intermediate",
            label="还原参数式中转链接（微博等 u= 参数）",
            offline=True,
            current=cur,
            suggested=real,
        ))
        cur = real

    # ② 标准格式：用还原后的地址判断，避免“先问格式、后还原”白折腾
    result = canonicalize(cur)
    if result.needs_fix:
        issues.append(UrlIssue(
            kind="canonical",
            label=f"转换为【{result.platform}】标准链接",
            offline=True,
            current=cur,
            suggested=result.canonical,
        ))
        cur = result.canonical

    # ③ 短链 / 中转页：只能联网展开（离线参数式已在①处理完）
    if is_short_url(cur):
        issues.append(UrlIssue(
            kind="short",
            label="短链，需要联网展开真实地址",
            offline=False,
            current=cur,
        ))
    elif is_intermediate_url(cur):
        issues.append(UrlIssue(
            kind="intermediate",
            label="中转页，需要联网展开真实地址",
            offline=False,
            current=cur,
        ))

    return issues


def apply_offline(url: str) -> str:
    """依次应用所有离线修复（参数式还原 + 标准格式），返回最终地址。

    用户拒绝替换时不要调用本函数；本函数不做询问、不联网。
    """
    cur = (url or "").strip()
    if not cur:
        return cur

    for _ in range(3):
        real = try_extract_param_url(cur)
        if real and real != cur:
            cur = real
            continue

        result = canonicalize(cur)
        if result.needs_fix:
            cur = result.canonical
            continue

        break
    return cur


def needs_online_unwrap(url: str) -> bool:
    """是否还需要联网展开（短链 ∪ 中转页；参数式中转不算）。"""
    u = (url or "").strip()
    if not u:
        return False
    return is_short_url(u) or is_intermediate_url(u)
