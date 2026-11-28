# app_data/url_canonical_rules.py
"""site_url 标准格式（canonical）规则 —— 纯数据，不含逻辑。

引擎：url_canonical.py（离线拼装，不联网）

约定：
  - key 必须与 complaint_platform_consts.PLATFORM_CONFIG 里的 display_name 一致；
  - 域名不在这里重复写，统一从 PLATFORM_DOMAINS 派生
    （即“域名命中百度知道规则”这件事只有一个事实源）；
  - 特征值不在这里重复写，统一由
    app_data/complaint_link_features.py FEATURE_PATTERNS 提取
    （feature_extractor.extract_pure_feature）；
  - 本文件只声明“标准链接长什么样”以及“特征值必须满足什么形状”。

字段说明：
  template        标准链接模板，{feature} 会被特征值替换
  feature_pattern 特征值必须匹配的正则；不匹配 = 无法规范化（不询问用户）
  note            给人看的说明
"""

URL_CANONICAL_RULES = {
    "百度知道": {
        # 实际入库可能长这样：
        #   https://zhidao.baidu.com/question/1461922784676485020/answer/5100372371.html
        #   https://zhidao.baidu.com/question/2217566074808803348.html?fr=search&word=xxx
        # 都应统一为：
        #   https://zhidao.baidu.com/question/2217566074808803348.html
        #
        # 特征值 = 问题 ID（纯数字），由 complaint_link_features 的
        #   r'zhidao\.baidu\.com/question/(\d+)'
        # 提取；answer/xxx、?fr=... 等后缀全部丢弃。
        "template": "https://zhidao.baidu.com/question/{feature}.html",
        "feature_pattern": r"^\d+$",
        "note": "只保留 question/<问题ID>.html；answer 后缀、查询参数、协议和域名大小写一律收敛",
    },
}
