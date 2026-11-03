# search_dispatch_service.py
"""搜索任务派发 + 预览派发。

职责：
  - 编排一次完整的 Playwright 搜索（线程 / 浏览器 / 抓取 / 入库 / 触发解析）
  - 通知插件打开/关闭预览

不做什么：
  - 不认识 WS 路由
  - 不认识 UI
"""
import threading
import random
from datetime import datetime

from app_state import app_state
from app_signals import ws_signals
from link_ingest_service import save_picked_link
from parser_service import trigger_parse
from app_data.search_templates import get_search_url, SEARCH_TEMPLATES
from app_data.ext_search_strategies import get_ext_strategy
from plugin_bridge import bridge, ensure_ready


def _make_task_id(platform):
    return f"{platform}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}"


def _handle_scan_finished(data):
    task_id = data.get("task_id", "")
    batch_id = data.get("batch_id", "")
    app_state.pop_task(task_id)
    app_state.busy_end("scan_finished")

    if batch_id:
        ws_signals.search_finished.emit(batch_id)

    app_state.set_mode("idle")

    if batch_id:
        print(f"✅ 扫描完成 task_id={task_id}，立即触发解析 batch={batch_id}")
        trigger_parse(batch_id)

    ws_signals.scan_state_changed.emit("idle")


def dispatch_search_task(platform, keyword, max_pages, sub_kws=None):
    if not app_state.check_and_lock("searching"):
        return "busy"
    if app_state.is_busy():
        return "busy"

    query_for_url = keyword
    if sub_kws:
        query_for_url = f"{keyword} {' '.join(sub_kws)}"
    url = get_search_url(platform, query_for_url)
    strategy = get_ext_strategy(platform)

    config = SEARCH_TEMPLATES.get(platform, {})
    real_platform = config.get("real_platform", platform)
    is_mobile = config.get("is_mobile", False)

    batch_id = app_state.current_context.get("batch_id") or \
        f"{platform}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    task = {
        "action": "do_search",
        "task_id": _make_task_id(platform),
        "batch_id": batch_id,
        "platform": platform,
        "real_platform": real_platform,
        "is_mobile": is_mobile,
        "keyword": keyword,
        "book_name": app_state.current_context.get("book_name", ""),
        "max_pages": max_pages,
        "url": url,
        "strategy": strategy,
    }

    app_state.set_context(
        book_name=task["book_name"],
        keyword=task["keyword"],
        batch_id=task["batch_id"],
        source_platform=real_platform,
    )

    ws_signals.scan_state_changed.emit("scanning")

    def _on_timeout():
        ws_signals.scan_state_changed.emit("idle")

    app_state.busy_begin(task["task_id"], timeout_sec=300, on_timeout=_on_timeout)
    app_state.add_task(task["task_id"], task)

    def _run_playwright():
        try:
            if not ensure_ready(business_name="搜索抓取", timeout=8.0):
                raise RuntimeError("浏览器启动或 CDP 接管失败")

            from browser_manager import BrowserManager
            from search_crawler import SearchCrawler

            bm = BrowserManager.instance()
            crawler = SearchCrawler(bm)

            links = bm.run_in_browser_thread(crawler.run_task, task)

            if links:
                new_count = 0
                ctx = {
                    "source_platform": real_platform,
                    "is_mobile": is_mobile,
                    "keyword": task["keyword"],
                    "book_name": task["book_name"],
                    "batch_id": task["batch_id"],
                    "pick_origin": "机器人获取",
                }
                for link in links:
                    try:
                        _, _, is_new = save_picked_link(
                            url=link["url"], title=link.get("title", ""), ctx=ctx,
                            body=link.get("body", ""), source_site=link.get("source_site", ""),
                            parent_url="", parent_title="", is_manual=False,
                        )
                        if is_new:
                            new_count += 1
                    except Exception as e:
                        print(f"单条入库失败: {e}")

                if new_count > 0:
                    print(f"✅ [Playwright] 任务入库待处理 {new_count} 条")

            _handle_scan_finished({
                "task_id": task["task_id"],
                "batch_id": task["batch_id"],
            })

        except Exception as e:
            print(f"❌ [Playwright] 任务执行崩溃: {e}")
            app_state.busy_end("playwright_error")
            app_state.set_mode("idle")
            ws_signals.search_finished.emit(f"ERROR_{task['batch_id']}")
            ws_signals.scan_state_changed.emit("idle")

    threading.Thread(target=_run_playwright, daemon=True).start()
    return "success"


def dispatch_close_preview():
    if bridge.is_connected():
        bridge.send_sync({"action": "close_preview"})


def dispatch_open_preview(url, book_name="", keyword="", batch_id="",
                          source_db_id=None, category="",
                          available_books=None, available_categories=None):
    def _run():
        if not bridge.is_connected():
            ensure_ready(business_name="预览", timeout=8.0)
        bridge.send_sync({
            "action": "open_preview",
            "url": url,
            "book_name": book_name,
            "keyword": keyword,
            "batch_id": batch_id,
            "source_db_id": source_db_id,
            "category": category,
            "available_books": available_books or [],
            "available_categories": available_categories or [],
        })

    threading.Thread(target=_run, daemon=True).start()