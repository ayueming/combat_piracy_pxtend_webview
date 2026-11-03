# app_data/search_templates.py
"""
检索入口模板表。
"""
from urllib.parse import quote
from app_data import ALL_SUB_PLATFORMS

# =========================================================
# ★ 自定义搜索引擎下拉框的展示顺序 (按此列表的先后顺序)
# =========================================================
SEARCH_PLATFORM_PRIORITY = [
    "百度搜索(pc)",
    "百度搜索(移动)",
    "夸克搜索",
    "百度知道",
    "百度贴吧",
    # "uc搜索",  还没开发，先注释掉
]

SEARCH_TEMPLATES = {
    "百度搜索(pc)": {
        "template": "https://www.baidu.com/s?wd={kw}",
        "mode": "direct",
        "exact_support": "quote",
        "real_platform": "百度搜索",  # ★ 核心：底层认的还是百度搜索
        "is_mobile": False,
        "note": "百度PC端",
    },
    "百度搜索(移动)": {
        "template": "https://m.baidu.com/s?word={kw}", # 注意这里是 word= 和 m.
        "mode": "direct",
        "exact_support": "quote",
        "real_platform": "百度搜索",  # ★ 核心：底层统一认百度搜索
        "is_mobile": True,
        "note": "百度移动端",
    },
    "百度知道": {
        "template": "https://zhidao.baidu.com/search?word={kw}",
        "mode": "direct",
        "exact_support": "none",
        "real_platform": "百度知道",
        "is_mobile": False,
        "note": "百度知道站内搜索",
    },
    "百度贴吧": {
        "template": "https://tieba.baidu.com/f/search/res?ie=utf-8&tab=thread&qw={kw}",
        "mode": "direct",
        "exact_support": "none",
        "real_platform": "百度贴吧",
        "is_mobile": False,
        "note": "贴吧全站搜索(仅贴子)",
    },
    "夸克搜索": {
        "template": "https://www.quark.cn/s/?q={kw}",
        "mode": "direct",
        "exact_support": "none",
        "real_platform": "夸克搜索",
        "is_mobile": False,
        "note": "",
    },
    "爱网盘": {
        "template": "https://iwangpan.cc/search?q={kw}&exact=true",
        "mode": "direct",
        "exact_support": "none",
        "real_platform": "爱网盘",
        "is_mobile": False,
        "note": "网盘聚合搜索",
    },
    # "uc搜索": {
        # "template": "https://so.m.sm.cn/s?q={kw}",
        # "mode": "direct",
        # "exact_support": "none",
        # "real_platform": "uc搜索",
        # "is_mobile": False,
        # "note": "神马搜索",
    # },
    # "短剧搜": {
        # "template": "https://www.duanjuso.cc/search?q={kw}",
        # "mode": "direct",
        # "exact_support": "none",
        # "real_platform": "其他",
        # "is_mobile": False,
        # "note": "神马搜索",
    # },
}


def get_search_url(platform, keyword, exact=False):
    """
    根据平台、关键词拼出搜索 URL。
    只要平台支持双引号，就强制加上，以达到尽量准确的目的。
    """
    if platform not in SEARCH_TEMPLATES:
        raise ValueError(f"平台「{platform}」没有配置搜索模板")

    cfg = SEARCH_TEMPLATES[platform]
    kw = keyword.strip()

    # 核心逻辑：支持就加双引号
    if cfg["exact_support"] == "quote":
        kw = f'"{kw}"'

    return cfg["template"].format(kw=quote(kw))


def get_available_platforms():
    """检索控制台可选的平台列表，按自定义优先级排序。"""
    # ★ 核心：依然依赖全局字典 ALL_SUB_PLATFORMS 做白名单，
    # 但是判断标准从“键名”变成了“real_platform”（真名）
    available = [
        p for p, cfg in SEARCH_TEMPLATES.items()
        if cfg.get("real_platform", p) in ALL_SUB_PLATFORMS
    ]

    # 2. 按照自定义优先级排序
    def sort_key(platform):
        if platform in SEARCH_PLATFORM_PRIORITY:
            return SEARCH_PLATFORM_PRIORITY.index(platform)
        return 999

    return sorted(available, key=sort_key)


# 启动时校验：所有模板的 real_platform（真实入库名）必须是合法平台名
_invalid = set()
for platform, cfg in SEARCH_TEMPLATES.items():
    real_name = cfg.get("real_platform", platform)
    if real_name not in ALL_SUB_PLATFORMS:
        _invalid.add(real_name)

if _invalid:
    raise RuntimeError(
        f"search_templates.py 中的 real_platform 存在未在 PLATFORM_CONFIG 注册的平台名：{_invalid}"
    )