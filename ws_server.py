# ws_server.py
"""WebSocket 服务 + 消息路由。

职责：
  - 接收插件端 WS 消息，解析 action 并分派
  - 对外提供 start_ws_server() 启动入口

不做什么：
  - 不写状态机（在 app_state）
  - 不编排搜索（在 search_dispatch_service）
  - 不定义信号（在 app_signals）

★ 顶部 re-export 是为了兼容旧代码：
    from ws_server import current_context, set_app_mode, get_app_mode,
                          dispatch_search_task, dispatch_close_preview,
                          dispatch_open_preview, ws_signals
  旧文件可以一行不改继续用。
"""
import asyncio
import json
import logging
import threading

import websockets

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


# ★ 兼容：老代码 from ws_server import current_context
#   这个引用和 app_state._context 是同一个 dict 对象
current_context = app_state.current_context


def _on_plugin_disconnected_ws():
    print("🔌 [WS] 插件已断开")
    if not app_state.is_busy():
        print("🔌 强制清除空闲状态")
        app_state.busy_end("plugin_disconnected")
        app_state.set_mode("idle")
        ws_signals.scan_state_changed.emit("idle")
    else:
        print("⚠️ 插件断开，但当前有 Playwright 抓取任务正在进行，忽略此事件")


bridge_signals.plugin_disconnected.connect(_on_plugin_disconnected_ws)


async def echo(websocket):
    bridge.set_websocket(websocket)
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                action = data.get("action")

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
        bridge.clear_websocket()


async def _handle_manual_pick(websocket, data):
    ctx_live = app_state.current_context
    book_name = data.get("book_name") or ctx_live.get("book_name", "")
    category = data.get("category") or ""

    ctx = {
        "book_name": book_name,
        "keyword": ctx_live.get("keyword", ""),
        "batch_id": data.get("batch_id") or ctx_live.get("batch_id", ""),
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
    async with websockets.serve(echo, "127.0.0.1", 8080):
        await asyncio.Future()


def run_loop():
    asyncio.run(main())


def start_ws_server():
    t = threading.Thread(target=run_loop, daemon=True)
    t.start()