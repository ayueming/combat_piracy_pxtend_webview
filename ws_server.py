# ws_server.py
"""WebSocket 服务 + 消息路由（Qt 桌面版）。

职责：
  - 接收插件端 WS 消息，解析 action 并分派
  - 对外提供 start_ws_server() 启动入口

不做什么：
  - 不写状态机（在 app_state）
  - 不编排搜索（在 search_dispatch_service）
  - 不定义信号（在 app_signals）
  - 不含 RPC / bridges / ws_events（那些是 Web 版资产，Qt 路线不用）

★ 顶部 import 兼作 re-export，兼容旧代码：
    from ws_server import set_app_mode, get_app_mode,
                          dispatch_search_task, dispatch_close_preview,
                          dispatch_open_preview, ws_signals
"""
import asyncio
import json
import logging
import threading
from datetime import datetime

import websockets

import plugin_bridge

from app_state import app_state, get_app_mode, set_app_mode, check_and_lock
from app_signals import ws_signals
from search_dispatch_service import (
    dispatch_search_task,
    dispatch_close_preview,
    dispatch_open_preview,
)
from link_ingest_service import save_picked_link
from parser_service import trigger_parse
from plugin_bridge import bridge, ensure_ready, bridge_signals


_disconnect_timer = None
_disconnect_lock = threading.Lock()


def _on_plugin_disconnected_ws():
    global _disconnect_timer
    with _disconnect_lock:
        if _disconnect_timer:
            _disconnect_timer.cancel()

        print("⚠️ [WS] 前端页面断开，3 秒内若无新连接，将终止任务...")

        def _force_kill():
            with _disconnect_lock:
                if bridge.is_connected():
                    print("✅ [WS] 宽限期内已重连，取消终止")
                    return
                # 浏览器仍在运行（Python 启动的浏览器进程存活），不杀搜索
                proc = getattr(plugin_bridge, "_browser_proc", None)
                if proc is not None and proc.poll() is None:
                    print("⏳ [WS] 插件断开但浏览器仍在运行，不终止搜索")
                    return
                print("❌ [WS] 宽限期结束，确认无连接，终止任务")
                from search_task_queue import task_queue
                task_queue.stop()
                app_state.busy_end("plugin_disconnected")
                app_state.set_mode("idle")
                ws_signals.scan_state_changed.emit("idle")

        _disconnect_timer = threading.Timer(3.0, _force_kill)
        _disconnect_timer.start()


bridge_signals.plugin_disconnected.connect(_on_plugin_disconnected_ws)


async def echo(websocket):
    bridge.set_websocket(websocket)
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                action = data.get("action")

                if action == "ping":
                    continue

                if action == "request_context":
                    ctx = app_state.current_context
                    print(f"🔗 插件请求当前上下文，已发送：{ctx['book_name']}")
                    await websocket.send(json.dumps({
                        "action": "set_context",
                        "book_name": ctx["book_name"],
                        "keyword": ctx["keyword"],
                    }))

                elif action == "manual_pick":
                    await _handle_manual_pick(websocket, data)

                elif action == "save_manual_batch":
                    batch_id = data.get("batch_id") or app_state.current_context.get("batch_id", "")
                    if batch_id:
                        print(f"💾 用户点击保存本批: {batch_id}")
                        trigger_parse(batch_id, only_manual=True)

            except Exception as e:
                print(f"处理数据出错: {e}")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        bridge.clear_websocket(websocket)


async def _handle_manual_pick(websocket, data):
    ctx_live = app_state.current_context
    book_name = data.get("book_name") or ctx_live.get("book_name", "")
    category = data.get("category") or ""

    batch_id = data.get("batch_id") or ctx_live.get("batch_id", "")
    if not batch_id:
        batch_id = f"MANUAL_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    ctx = {
        "book_name": book_name,
        "keyword": ctx_live.get("keyword", ""),
        "batch_id": batch_id,
        "pick_origin": "手动获取",
        "source_platform": category,
    }

    try:
        _, _, is_new = save_picked_link(
            url=data["url"],
            title=data.get("title", ""),
            ctx=ctx,
            body=data.get("body", ""),
            source_site=data.get("source_site", ""),
            parent_url=data.get("parent_url", ""),
            parent_title=data.get("parent_title", ""),
            is_manual=True,
            category=category or None,
        )

        if is_new:
            print(f"📥 手动拾取入库待处理: {data['url'][:60]}...")
            trigger_parse(ctx["batch_id"], only_manual=True)
        else:
            print(f"♻️ 手动拾取记录重复，忽略: {data['url'][:60]}...")

    except Exception as e:
        print(f"❌ [手动拾取] 入库失败: {e}")


async def main():
    print("🚀 WebSocket 后台控制台已启动，监听 8080 端口...")
    logging.getLogger('websockets').setLevel(logging.CRITICAL)
    try:
        async with websockets.serve(echo, "127.0.0.1", 8080, max_size=100 * 1024 * 1024):
            await asyncio.Future()
    except OSError as e:
        if e.errno == 10048:
            print("❌ [致命错误] 端口 8080 被占用，请检查是否已打开本软件，或重启电脑！")
        else:
            raise


def run_loop():
    asyncio.run(main())


def start_ws_server():
    t = threading.Thread(target=run_loop, daemon=True)
    t.start()