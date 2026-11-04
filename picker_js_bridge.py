# picker_js_bridge.py
"""Picker 的 JS ↔ Python 通信桥。

不触碰 current_context，也不依赖任何窗口对象。
上下文由 context_provider 在每次 pick 时临时提供。
"""
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Signal, Slot

from link_ingest_service import save_picked_link
from parser_service import trigger_parse


class PickerJsBridge(QObject):
    picked      = Signal(dict)    # save_picked_link 返回的 record
    saved_page  = Signal(dict)    # 同上（"仅保存当前网址"）★ 改名避免与方法名冲突
    batch_saved = Signal(str)     # batch_id 或 "LEGACY_BATCH"
    ua_switched = Signal(int)     # 0/1

    def __init__(self, context_provider, parent=None):
        super().__init__(parent)
        self._context_provider = context_provider

    def _build_ctx(self, book_name, category):
        base = dict(self._context_provider() or {})
        # ★ 用户在 picker 里改的书/分类，以 DOM 值为准
        base["book_name"] = book_name or base.get("book_name", "")
        base["category"] = category or base.get("category", "")
        base["pick_origin"] = "手动获取"
        # source_platform 若 provider 没给，用 category 兜底（focus 场景）
        if not base.get("source_platform"):
            base["source_platform"] = base["category"]
        return base

    def _save_and_emit(self, url, title, body, book_name, category, signal_name):
        try:
            source_site = ""
            try:
                source_site = urlparse(url).netloc.replace("www.", "")
            except Exception:
                pass

            ctx = self._build_ctx(book_name, category)
            record, is_dup, is_new = save_picked_link(
                url=url,
                title=title,
                ctx=ctx,
                body=body,
                source_site=source_site,
                parent_url=ctx.get("parent_url", ""),
                is_manual=True,
                category=ctx["category"],
            )
            if is_new:
                getattr(self, signal_name).emit(record)
                print(f"✅ [Picker] 已拾取: {(title or '')[:40]}")
            else:
                print(f"♻️ [Picker] 重复链接跳过: {url[:60]}")
        except Exception as e:
            print(f"❌ [Picker] 拾取失败: {e}")

    @Slot(str, str, str, str, str)
    def manual_pick(self, url, title, body, book_name, category):
        self._save_and_emit(url, title, body, book_name, category, "picked")

    @Slot(str, str, str, str, str)
    def save_page(self, url, title, body, book_name, category):
        self._save_and_emit(url, title, body, book_name, category, "saved_page")

    @Slot(str)
    def save_batch(self, batch_id):
        # ★ 关键：不 fallback current_context，空批号走 LEGACY_BATCH
        bid = batch_id if batch_id else "LEGACY_BATCH"
        trigger_parse(bid, only_manual=True)
        self.batch_saved.emit(bid)
        print(f"📦 [Picker] 已触发解析: batch_id={bid}")

    @Slot(str)
    def switch_ua(self, mode):
        try:
            target = int(mode)
        except (TypeError, ValueError):
            return
        self.ua_switched.emit(target)

    @Slot(result=int)
    def get_pending_count(self):
        """供 JS 调用：查询当前数据库中所有【手动拾取】的待解析数量"""
        try:
            from db_config import get_conn
            from app_data.crawl_status_consts import CRAWL_STATUS_PENDING
            conn = get_conn()
            count = conn.execute(
                "SELECT COUNT(*) FROM draft_links WHERE crawl_status = ? AND attr = '手动获取'",
                (CRAWL_STATUS_PENDING,)
            ).fetchone()[0]
            conn.close()
            return count
        except Exception as e:
            print(f"❌ [Picker] 查询待解析数量失败: {e}")
            return 0