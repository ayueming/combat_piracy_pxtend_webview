# cdp_control.py
"""通过 CDP HTTP 端点关闭业务 tab，不经过 Playwright，可从任意线程调用。"""
import json
import urllib.request

from app_data.browser_consts import CDP_PORT


def close_search_tabs(timeout=1):
    """关闭所有业务 tab，保留插件唤醒页和扩展页。返回关闭数量。"""
    try:
        url = f"http://127.0.0.1:{CDP_PORT}/json/list"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            targets = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[CDP] 获取 targets 失败: {e}")
        return 0

    closed = 0
    for t in targets:
        if t.get("type") != "page":
            continue
        u = t.get("url", "")
        # ★ 保留插件唤醒页和扩展页面，否则插件会掉
        if ("wakeup.html" in u
                or u.startswith("chrome-extension://")
                or u.startswith("edge-extension://")):
            continue
        tid = t.get("id")
        if not tid:
            continue
        try:
            close_url = f"http://127.0.0.1:{CDP_PORT}/json/close/{tid}"
            urllib.request.urlopen(close_url, timeout=timeout).read()
            closed += 1
            print(f"[CDP] 已关闭 tab: {u[:60]}")
        except Exception as e:
            print(f"[CDP] 关闭 tab {tid} 失败: {e}")
    return closed