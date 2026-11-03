# app_data/ext_search_strategies.py
"""
插件模式（browser extension）专用搜索抓取策略。

★ strategy 结构：
  - mode: "click" | "scroll"
      click  -> 找到 nextBtnSelector 并点击
      scroll -> 向下滚动触发瀑布流加载
  - scanner: "generic" | "tieba"
      generic -> 按 selector 提取（resultSelector -> titleSelector/bodySelector/sourceSelector）
      tieba   -> 走贴吧专用扫描（卡片结构特殊，selector 不通用）
  - resultSelector  结果容器（每条结果的外层包裹）
  - titleSelector   标题元素（a 标签，从此读 href 和 innerText）
  - bodySelector    正文摘要（可选）
  - sourceSelector  来源网站（可选）
  - nextBtnSelector 翻页按钮（仅 click 模式）
  - urlExtract      ★ 一阶段：URL 提取规则（按顺序尝试，命中即停）
  - base_url        ★ 核心：如果抓到的链接是以 / 开头的相对路径，自动拼接此域名
  - detailExtract   ★ 二阶段：详情页剥壳规则（仅针对盘站等需要进详情页的场景）
"""

DEFAULT_EXT_STRATEGY = {
    "mode": "click",
    "scanner": "generic",
    "resultSelector": "",
    "titleSelector": "",
    "bodySelector": "",
    "sourceSelector": "",
    "nextBtnSelector": "",
    "urlExtract": {"rules": [], "blacklist_patterns": []},
    "base_url": "",  # ★ 新增默认空
    "detailExtract": {"rules": [], "blacklist_patterns": []} # ★ 新增默认空
}

EXT_SEARCH_STRATEGIES = {
    "百度搜索(pc)": {
        "mode": "click",
        "scanner": "generic",
        "resultSelector": "#content_left .result, #content_left .c-container",
        "titleSelector": "h3 a",
        "bodySelector": ".c-abstract, [data-module='abstract']",
        "sourceSelector": ".cosc-source-text, .c-showurl",
        "nextBtnSelector": "a.n, .page-box a, a[href*='pn=']",
        "urlExtract": {
            "rules": [
                {"selector": ".tts-button", "attr": "data-url"},
                {"selector": ".cosc-feedback", "attr": "data-feedback", "json_path": "url"},
                {"selector": "h3 a", "attr": "data-url"},
                {"selector": "h3 a", "attr": "href"}
            ],
            "blacklist_patterns": ["baidu.com/s?wd=", "baidu.com/s?word="]
        }
    },
    # ★ 百度搜索（移动端）
    "百度搜索(移动)": {
        "mode": "click",
        "scanner": "generic",
        "resultSelector": ".c-result, .cosc-card",
        "titleSelector": "h3 a",
        "bodySelector": ".c-abstract, .common-content_6I4X7",
        "sourceSelector": ".cosc-source-text",
        "nextBtnSelector": "[class*='new-nextpage']",
        "urlExtract": {
            "rules": [
                {"selector": "h3 a", "attr": "data-url"},
                {"on_self": True, "attr": "rl-link-data-url"},
                {"on_self": True, "attr": "rl-link-data-ivk", "json_path": "control.dataUrl"},
                {"selector": "h3 a", "attr": "href"},
                {"on_self": True, "attr": "rl-link-href"}
            ],
            "blacklist_patterns": ["baidu.com/s?word="]
        }
    },
    "百度知道": {
        "mode": "click",
        "scanner": "generic",
        "resultSelector": "dl.dl",
        "titleSelector": "dt.dt a.ti",
        "bodySelector": "dd.answer",
        "sourceSelector": "dd.explain a.f-lighter",
        "nextBtnSelector": "a.pager-next",
        "urlExtract": {
            "rules": [
                {"selector": "dt.dt a.ti", "attr": "href"}
            ],
            "blacklist_patterns": []
        }
    },
    "百度贴吧": {
        "mode": "scroll",
        "scanner": "tieba",
        # 贴吧卡片结构特殊，selector 在 content.js 的 scanTieba() 内部处理
        "urlExtract": {
            "rules": [
                {"selector": "a[href*='/p/']", "attr": "href"}
            ],
            "blacklist_patterns": []
        }
    },
    "夸克搜索": {
        "mode": "scroll",
        "scanner": "generic",
        "resultSelector": ".pc-s-grid-content-left .qk-card",
        "titleSelector": ".qk-title-content a",
        "bodySelector": ".qk-paragraph-text",
        "sourceSelector": ".qk-source-item",
        "nextBtnSelector": None,
        "urlExtract": {
            "rules": [
                {"selector": ".qk-title-content a", "attr": "href"}
            ],
            "blacklist_patterns": []
        }
    },
    # ★ 新增：爱网盘（盘站搜索）
    "爱网盘": {
        "mode": "click",
        "scanner": "generic",
        "base_url": "https://www.iwangpan.cc",  # ★ 相对路径自动补全
        "resultSelector": "article._search-item_816kt_65",
        "titleSelector": "._search-item-title_816kt_112",
        "bodySelector": "._search-item-info_816kt_115",
        "sourceSelector": "._search-item-logo_816kt_150",
        "nextBtnSelector": "li.semi-page-next:not(.semi-page-item-disabled)", # 过滤掉禁用的上一页/下一页按钮
        "urlExtract": {
            "rules": [
                {"selector": "._search-item-title_816kt_112", "attr": "href"}
            ],
            "blacklist_patterns": []
        },
        # ★ 核心：二阶段详情页剥壳配置
        "detailExtract": {
            "wait_selector": "a._jump-link_hcpgj_231",
            "rules": [
                {"selector": "a._jump-link_hcpgj_231", "attr": "href"}
            ],
            "blacklist_patterns": []
        }
    },
}


def get_ext_strategy(platform):
    """获取插件抓取策略，找不到则回退到默认策略。"""
    return EXT_SEARCH_STRATEGIES.get(platform, DEFAULT_EXT_STRATEGY)