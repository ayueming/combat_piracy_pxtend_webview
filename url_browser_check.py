# url_browser_check.py
"""SPA 页面检查器（当前主要是百度贴吧）—— 复用 BrowserManager 的真实浏览器。

为什么必须用浏览器（已实测）：
    1. requests 直接访问贴吧帖页 → HTTP 403，页面是「百度安全验证」，
       连 SPA 的壳都拿不到，更别提失效特征；
    2. 失效提示「贴子可能已被删除」是 JS 渲染出来的：
          <div class="frs-error-wrap">
            <img class="frs-error-img" ...>
            <div class="prompt-text">贴子可能已被删除</div>
          </div>
       初始 HTML 里没有这段；
    3. 即使用 Playwright 新起一个无痕浏览器，也会被验证码拦住
       （实测 headless Chrome 打开同一链接，title = 百度安全验证）。
    所以只能复用 BrowserManager 通过 CDP 接管的那个「有真实登录态/指纹」的浏览器。

线程模型：
    BrowserManager 的所有 Playwright 对象都锁在 BrowserThread 里，
    本模块只通过 bm.run_in_browser_thread(...) 投递，页面操作全部在浏览器线程内完成。

页面生命周期（统一入口，本模块不直接碰 _context / 不自己 new_page）：
    - 打开：bm.new_page(force_new=False, activate=False)
            → 走 BrowserManager 的 about:blank 复用逻辑；
    - 关闭：bm.close_pages([page])
            → 复用它"关完若一个不剩就保留根页面并 reset 到 about:blank"的保护；
    - 导航 / 取内容：依然在本模块内调用 page.goto / inner_text，
      因为需要 response 状态与自定义等待策略；但 page 对象本身完全由 BrowserManager 创建与回收。
"""
import time
import threading

from url_invalid_judge import register_browser_fetcher

# ---------- 默认判定特征（可在各平台 rule["browser"] 里覆盖）----------
DEFAULT_INVALID_SELECTORS = (".frs-error-img", ".frs-error-container")
DEFAULT_INVALID_TEXTS = (
    "贴子可能已被删除",
    "该贴已被删除",
    "帖子已被删除",
    "贴子不存在",
    "该主题已被删除",
    "您访问的贴子不存在",
)
DEFAULT_VALID_TEXTS = ("回复", "楼主", "收藏", "只看楼主")
DEFAULT_BLOCKED_TEXTS = ("百度安全验证", "请完成下方验证", "请向右滑动完成拼图")

DEFAULT_TIMEOUT_MS = 20000      # 单页 goto 超时
DEFAULT_MAX_WAIT_MS = 12000     # 等特征出现的最长时间
DEFAULT_SETTLE_MS = 1200        # 命中后的稳定观察窗（防止"先正常后失效"的二次渲染）
DEFAULT_BODY_LIMIT = 400_000    # 回传 HTML 上限，避免超大页面拖慢判定


def _cfg(rule, key, default):
    browser_cfg = rule.get("browser") or {}
    value = browser_cfg.get(key)
    return default if value is None else value


def _safe_text(page):
    try:
        return page.inner_text("body") or ""
    except Exception:
        return ""


def _safe_title(page):
    try:
        return page.title() or ""
    except Exception:
        return ""


def _safe_content(page):
    try:
        return page.content() or ""
    except Exception:
        return ""


def _has_selector(page, selectors):
    for sel in selectors:
        try:
            if page.query_selector(sel):
                return True
        except Exception:
            continue
    return False


def _fetch_impl(bm, url, rule, debug=False):
    """在 BrowserThread 内执行：打开页面 → 等特征 → 返回标准化结果。"""
    timeout_ms = int(_cfg(rule, "timeout_ms", DEFAULT_TIMEOUT_MS))
    max_wait_ms = int(_cfg(rule, "max_wait_ms", DEFAULT_MAX_WAIT_MS))
    settle_ms = int(_cfg(rule, "settle_ms", DEFAULT_SETTLE_MS))
    invalid_selectors = _cfg(rule, "invalid_selectors", DEFAULT_INVALID_SELECTORS)
    invalid_texts = _cfg(rule, "invalid_texts", DEFAULT_INVALID_TEXTS)
    valid_texts = _cfg(rule, "valid_texts", DEFAULT_VALID_TEXTS)
    blocked_texts = _cfg(rule, "blocked_texts", DEFAULT_BLOCKED_TEXTS)

    # ★ 统一入口：页面一律由 BrowserManager 提供（force_new=False 走它的 about:blank 复用逻辑）。
    #   本函数整体跑在 BrowserThread 内，new_page 之后紧接着 goto，
    #   中间不会让出浏览器线程，所以复用到的 blank 页会被立刻消费掉，不会被别的任务抢走。
    page = bm.new_page(force_new=False, activate=False)
    try:
        status_code = None
        goto_error = ""
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            status_code = resp.status if resp else None
        except Exception as e:
            goto_error = str(e)[:200]

        # ---------- 等 invalid / valid 任一特征出现 ----------
        start = time.time()
        seen_any = False
        while (time.time() - start) * 1000 < max_wait_ms:
            body = _safe_text(page)
            if _has_selector(page, invalid_selectors) or any(t in body for t in invalid_texts):
                seen_any = True
                break
            if any(t in body for t in valid_texts):
                seen_any = True
                break
            page.wait_for_timeout(300)

        # ---------- 稳定观察窗：贴吧常见"先渲染正常结构、随后才切到失效 DOM" ----------
        if seen_any and settle_ms > 0:
            page.wait_for_timeout(settle_ms)

        body = _safe_text(page)
        html = _safe_content(page)
        final_url = ""
        try:
            final_url = page.url
        except Exception:
            pass
        title = _safe_title(page)

        invalid_hit = _has_selector(page, invalid_selectors) or any(t in body for t in invalid_texts)
        blocked_hit = any(t in body for t in blocked_texts) or any(t in title for t in blocked_texts)
        valid_hit = (not invalid_hit) and (not blocked_hit) and any(t in body for t in valid_texts)

        reason = ""
        if invalid_hit:
            hit = next((t for t in invalid_texts if t in body), "")
            reason = f"命中失效特征: {hit}" if hit else "命中失效 DOM 特征"
        elif blocked_hit:
            reason = "百度安全验证"
        elif valid_hit:
            hit = next((t for t in valid_texts if t in body), "")
            reason = f"命中有效特征: {hit}"
        elif goto_error:
            reason = f"页面加载异常: {goto_error}"
        else:
            reason = "未命中任何特征"

        return {
            "final_url": final_url,
            "status_code": status_code,
            "text": html[:DEFAULT_BODY_LIMIT],
            "title": title,
            "invalid_hit": invalid_hit,
            "valid_hit": valid_hit,
            "blocked_hit": blocked_hit,
            "reason": reason,
            "body_head": body[:200],
        }
    finally:
        # ★ 统一入口：close_pages 带"关完若一个不剩就保留根页面并 reset 到 about:blank"的保护，
        #   比 close_page 更安全，留下的 blank 页也能被下一次检查复用。
        try:
            bm.close_pages([page])
        except Exception:
            pass


_installed = False
# 浏览器检查串行执行：BrowserThread 本来就是单线程串行，
# 这里加锁是为了让 submit 的 timeout 只覆盖"自己这一条"的执行时间，
# 而不是把排队时间也算进去（否则并发批检时后面的链接会被误判超时）。
_serial_lock = threading.Lock()


def browser_fetch(url, rule):
    """供 url_invalid_judge.register_browser_fetcher 注入的抓取函数。

    Returns: {"final_url", "status_code", "text", "invalid_hit", "valid_hit", "blocked_hit", "reason"}
    """
    from browser_manager import BrowserManager

    bm = BrowserManager.instance()
    # 整段页面操作投递到浏览器线程；超时略大于内部 goto + 等待 + 稳定窗
    with _serial_lock:
        return bm.run_in_browser_thread(
            _fetch_impl, bm, url, rule,
            block=True,
            timeout=(DEFAULT_TIMEOUT_MS + DEFAULT_MAX_WAIT_MS + DEFAULT_SETTLE_MS) / 1000 + 15,
        )


def install():
    """注册浏览器检查器（幂等）。"""
    global _installed
    if _installed:
        return True
    register_browser_fetcher(browser_fetch)
    _installed = True
    return True


def uninstall():
    global _installed
    register_browser_fetcher(None)
    _installed = False
