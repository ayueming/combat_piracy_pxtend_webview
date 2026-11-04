# app_data/mobile_consts.py

DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "Version/16.6 Mobile/15E148 Safari/604.1")

# 移动端子域名第一个 label 的精确匹配集合。
# 新增前缀只改这里，mobile_url_utils.url_is_mobile 自动生效。
# 宁可漏判（用户手动切 UA），不要误判（PC 页面用移动 UA 加载会排版错乱）。
MOBILE_HOST_PREFIXES = (
    "m",        # m.jd.com / m.taobao.com
    "mobile",
    "wap",      # 早期 WAP 版，如 wap.qidian.com
    "3g",
    "mobi",
    "touch",    # 触屏版
    "h5",       # H5 移动阅读页
)