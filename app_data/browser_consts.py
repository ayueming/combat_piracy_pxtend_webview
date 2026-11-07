# app_data/browser_consts.py
"""
浏览器基础设施常量。

⚠️ 这个文件只放"浏览器进程级别"的配置：
   - CDP 远程调试端口
   - profile 目录命名
   - 启动超时

不放抓取参数（那是 crawl_consts）和业务规则（那是 spider_rule_consts）。
"""

# CDP 远程调试端口。
#
# 为什么不是默认的 9222：
#   QtWebEngine 在开发调试时也会监听 9222（QTWEBENGINE_REMOTE_DEBUGGING），
#   一旦主程序先抢到 9222，Playwright 的 connect_over_cdp 就会连到 QtWebEngine，
#   报 "Browser.setDownloadBehavior: Browser context management is not supported"。
#   把专用 Edge 挪到非默认端口，与 QtWebEngine 物理隔离。
CDP_PORT = 9333

# 浏览器启动后等待 CDP 端口就绪的最长时间（秒）
CDP_READY_TIMEOUT = 8.0