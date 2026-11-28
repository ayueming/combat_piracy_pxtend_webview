# app_data/url_invalid_rules.py
"""链接失效判断规则 —— 纯数据，不含逻辑。

- key 必须与 complaint_platform_consts.PLATFORM_CONFIG 中的 display_name 一致
  （大小写不敏感匹配，但仍然建议一致，避免启动警告）
- 域名默认从 PLATFORM_DOMAINS 派生；特殊情况用 domains_override
"""

PLATFORM_CHECK_RULES = {
    # ---------- 百度系（反爬强化）----------
    "百度网盘": {
    "checker": "http",
    "invalid_status_codes": [404, 410],
    "invalid_final_url_keywords": ["/wap/error", "errortype="],
    "invalid_keywords": [
        "error-reason",
        "此链接分享内容可能因为涉及侵权",
        "分享的文件已经被取消",
        "你访问的页面不存在",
        "链接中有违规或违法内容无法查看",
    ],
    "valid_keywords": ["分享的文件", "提取码"],
    },
    "百度贴吧": {
        # ★ 已 SPA 化 + 强反爬：
        #   - requests 直接 HTTP 403「百度安全验证」，连页面壳都拿不到；
        #   - 失效提示「贴子可能已被删除」是 JS 渲染的，初始 HTML 里没有；
        #   → 只能走 BrowserManager 接管的真实浏览器（url_browser_check.py）。
        "checker": "browser",
        "note": "失效页为 <div class='frs-error-wrap'> + 「贴子可能已被删除」；有效页含 回复/楼主/收藏",
        "browser": {
            "timeout_ms": 20000,     # 单页加载超时
            "max_wait_ms": 12000,    # 等特征出现的最长时间
            "settle_ms": 1200,       # 命中后再观察一会儿，防止"先渲染正常、随后切失效"
            # ---- 判定特征（实测自失效 DOM）----
            "invalid_selectors": [".frs-error-img", ".frs-error-container"],
            "invalid_texts": [
                "贴子可能已被删除",
                "该贴已被删除",
                "帖子已被删除",
                "贴子不存在",
                "该主题已被删除",
            ],
            "valid_texts": ["回复", "楼主", "收藏"],
            "blocked_texts": ["百度安全验证", "请完成下方验证", "请向右滑动完成拼图"],
        },
    },
    "百度知道": {
    "checker": "http",
    "prewarm": ["https://www.baidu.com/", "https://zhidao.baidu.com/"],
    "spa_path_keywords": ["/pages/question/"],   # smartapps 的 swan 页
    "invalid_keywords": ["知道宝贝找不到问题了", "该问题可能已经失效"],
    "valid_keywords": ["最佳答案", "我来答"],
    "blocked_keywords": ["百度安全验证", "请完成下方验证", "网络不给力"],
    },
    "智能体": {
        "checker": "http",
        "domains_override": ["smartapps.baidu.com"],
        "invalid_keywords": ["swan-web-error-title", "当前页面不支持访问"],
        "valid_keywords": [],
    },

    # ---------- 夸克 ----------
    "夸克网盘": {
    "checker": "quark_api",
    "invalid_codes": [41004, 41005, 41010, 41011, 41012],
    "valid_code": 0,
    },

    # ---------- uc（注意小写，与 PLATFORM_DOMAINS 一致）----------
    "uc网盘": {
        "checker": "uc_api",
        "api_host": "pc-api.uc.cn",
        "prewarm": "https://drive.uc.cn/",
        "invalid_codes": [41011, 41027],
        "valid_code": 0,
    },

    # ---------- 迅雷 ----------
    "迅雷网盘": {
        "checker": "http",
        "invalid_keywords": ["Share__fontMini", "该分享内容可能因为涉及侵权"],
        "valid_keywords": ["分享的文件"],
    },

    # ---------- 暂不接 ----------
    # ★ 判定优先级（边界规则，务必遵守）：
    #   collect_checkable_tasks 先按 module 分流，再查本表。
    #   ① MODULE_SEARCH / MODULE_OTHER / MODULE_PANSITE → 直接判「网站类」，不查本表；
    #   ② 其余模块（netdisk / forum 等）→ 查本表：查不到 →「暂无判断」，skip →「暂无判断」。
    #   所以本表只保留「能走到第 ② 步」的平台，否则就是同一行为两套结论（边界不明）。
    #   · 爱网盘 / 爱搜 / 影盘社 / 短剧搜 属于 MODULE_PANSITE，已在 ① 被「网站类」接走，
    #     原先的 skip 声明已删除。
    #   · 微博 属于 MODULE_FORUM，能走到本表，skip 仍然生效 →「暂无判断」灰色。
    "微博":   {"checker": "skip"},
}

DEFAULT_HTTP_TIMEOUT = 15
DEFAULT_API_TIMEOUT = 10
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)