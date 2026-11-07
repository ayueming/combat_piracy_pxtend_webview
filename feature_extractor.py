# feature_extractor.py
import re
from app_data import DOMAIN_RULES, FEATURE_PATTERNS, ALL_SUB_PLATFORMS


def get_platform_category(url):
    """
    根据链接自动识别平台分类。
    只按显式域名规则匹配，不再做 baidu.com / quark.cn 兜底。
    返回匹配到的标准显示名（必须存在于 ALL_SUB_PLATFORMS 中），识别不到返回空字符串。
    """
    if not url:
        return ""

    url_lower = url.lower()

    for domain, cat_name in DOMAIN_RULES:
        if domain in url_lower:
            if cat_name in ALL_SUB_PLATFORMS:
                return cat_name
            return ""

    return ""


def extract_pure_feature(url):
    """
    智能提取纯净的特征码。
    只负责遍历规则 + 调度，不判断平台、不硬编码去 1。
    """
    url = (url or "").strip()
    if not url:
        return ""

    for rule in FEATURE_PATTERNS:
        if not re.search(rule["pattern"], url):
            continue

        extractor = rule.get("extractor")
        if extractor:
            code = extractor(url)
            if code:
                return code
            continue  # 函数返回空，继续尝试下一条规则

        # 声明式规则：直接取 group(1)，不做任何去 1
        m = re.search(rule["pattern"], url)
        return m.group(1)

    # 没匹配到任何规则，整条 URL 作为特征码
    return url