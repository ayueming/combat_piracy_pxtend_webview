# app_data/complaint_link_features.py
# 特征规则集中地：声明式正则 + 命令式提取函数都放这里
# feature_extractor.py 只负责遍历调度，不判断平台

import json
import re
from urllib.parse import urlparse, parse_qs, unquote


# =========================================================
# 平台特征提取函数（命令式规则）
# =========================================================

def _extract_baidu_pan_feature(url):
    """
    百度网盘：提取分享码，并对历史上多带的一个 1 做兼容。

    去 1 条件（缺一不可）：
      1. 链接命中百度网盘规则；
      2. 提取出的特征值以 '1' 开头；
      3. 特征值长度 >= 20。
    注意：判断的是“特征值长度”，不是“链接长度”。
    """
    m = re.search(r'(?:pan\.baidu\.com/s/|surl=)([a-zA-Z0-9_-]+)', url)
    if not m:
        return ""
    code = m.group(1)
    if code.startswith("1") and len(code) >= 20:
        code = code[1:]
    return code


def _extract_smartapp_feature(url):
    """
    文心智能体：解析 _chatParams，返回 smartapps:{agent_id}:{content_build_id}。
    缺 content_build_id 时退回 smartapps:{agent_id}。
    不使用 token / searchid / lid / order，保证同智能体不同链接归并。
    """
    try:
        qs = parse_qs(urlparse(url).query)
        raw = qs.get("_chatParams", [""])[0]
        if not raw:
            return ""
        params = json.loads(unquote(raw))
        agent_id = params.get("agent_id") or ""
        build_id = params.get("content_build_id") or ""
        if not agent_id:
            return ""
        return f"smartapps:{agent_id}:{build_id}" if build_id else f"smartapps:{agent_id}"
    except Exception:
        return ""


def _extract_weibo_feature(url):
    """
    微博特征提取：只提取帖子纯数字 MID，不拼用户 ID。
    - 数字帖子链接：直接保留数字
    - PC 短码帖子链接：Base62 解码为数字
    - 其他无规律链接：返回原链接
    """
    m = re.search(
        r'(?:weibo\.com|m\.weibo\.cn)/(?:\d+|detail|status)/([a-zA-Z0-9]+)',
        url
    )
    if not m:
        return url

    mid_string = m.group(1)

    if mid_string.isdigit():
        return mid_string

    alphabet = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'

    parts = []
    for i in range(len(mid_string), 0, -4):
        chunk = mid_string[max(0, i - 4):i]
        num = 0
        for ch in chunk:
            if ch not in alphabet:
                return url
            num = num * 62 + alphabet.index(ch)
        parts.append(str(num))

    result = parts[-1] + ''.join(p.zfill(7) for p in reversed(parts[:-1]))
    return result.lstrip("0") or "0"


# =========================================================
# 特征规则表
# 有 extractor 的走函数；没有的走正则 group(1)
# =========================================================

FEATURE_PATTERNS = [
    {
        "platform": "百度网盘",
        "pattern": r'(?:pan\.baidu\.com/s/|surl=)([a-zA-Z0-9_-]+)',
        "extractor": _extract_baidu_pan_feature,
    },
    {
        "platform": "智能体",
        "pattern": r'smartapps\.baidu\.com',
        "extractor": _extract_smartapp_feature,
    },
    {
        "platform": "夸克网盘",
        "pattern": r'pan\.quark\.cn/s/([a-zA-Z0-9]+)',
    },
    {
        "platform": "UC网盘",
        "pattern": r'drive\.uc\.cn/s/([a-zA-Z0-9]+)',
    },
    {
        "platform": "迅雷网盘",
        "pattern": r'pan\.xunlei\.com/s/([a-zA-Z0-9_-]+)',
    },
    {
        "platform": "百度知道",
        "pattern": r'zhidao\.baidu\.com/question/(\d+)',
    },
    {
        "platform": "百度贴吧",
        "pattern": r'tieba\.baidu\.com/p/(\d+)',
    },
    {
        "platform": "百家号",
        "pattern": r"baijiahao\.baidu\.com/[^#\s]*[?&]id=(\d+)",
    },
    {
        "platform": "微博",
        "pattern": r'(weibo\.com|m\.weibo\.cn)',
        "extractor": _extract_weibo_feature,
    },
]