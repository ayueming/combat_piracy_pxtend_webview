# app_data/mobile_consts.py

# ★ 与 Qt 引擎实际 Chromium 版本严格对齐（实测 QtWebEngine/6.140.0 Chrome/140.0.0.0）
_CR_VER = "140.0.0.0"

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{_CR_VER} Safari/537.36"
)

# 移动端保持 iOS Safari UA 不变：
# - iOS Safari 本来就没有 navigator.userAgentData，这反而是"自洽"的
# - 我们的 stealth 脚本会自动跳过 UA-CH 的 patch
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/16.6 Mobile/15E148 Safari/604.1"
)

MOBILE_HOST_PREFIXES = (
    "m", "mobile", "wap", "3g", "mobi", "touch", "h5",
)