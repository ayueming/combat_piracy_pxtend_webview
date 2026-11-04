# mobile_url_utils.py
from urllib.parse import urlparse

from app_data.mobile_consts import MOBILE_HOST_PREFIXES


def url_is_mobile(url: str) -> bool:
    """按 host 判断是否移动端。
    规则：先剥一层 www（只剥一层），再精确匹配第一个 label。
    """
    if not url:
        return False
    try:
        host = urlparse(url).hostname  # 自动 lower、去端口
    except Exception:
        return False
    if not host:
        return False

    labels = host.split(".")
    if labels and labels[0] == "www":
        labels = labels[1:]
    if not labels:
        return False

    return labels[0] in MOBILE_HOST_PREFIXES


def resolve_is_mobile(explicit_flag, url: str) -> int:
    """显式 true 优先；否则按 URL 判断；返回 0/1，绝不返回 None。"""
    if explicit_flag:
        return 1
    return 1 if url_is_mobile(url) else 0