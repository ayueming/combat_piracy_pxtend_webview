# picker_js_bridge.py
"""Picker 的 JS ↔ Python 通信桥。"""
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Signal, Slot

from app_data.complaint_platform_consts import PLATFORM_DOMAINS

from link_ingest_service import save_picked_link
from parser_service import trigger_parse, trigger_organize_single
from link_resolver import LinkResolver, is_short_url          # ★ 转发自 crawl_consts
from ws_server import ws_signals                 # ★ 拾取后通知 UI 刷新


class PickerJsBridge(QObject):
    picked      = Signal(dict)
    saved_page  = Signal(dict)
    batch_saved = Signal(str)
    ua_switched = Signal(int)

    def __init__(self, context_provider, parent=None):
        super().__init__(parent)
        self._context_provider = context_provider
        self.resolver = LinkResolver()  # ★ 新增：实例化解析器

    def _build_ctx(self, book_name, category):
        base = dict(self._context_provider() or {})
        base["book_name"] = book_name or base.get("book_name", "")
        base["category"] = category or base.get("category", "")
        base["pick_origin"] = "手动获取"
        if not base.get("source_platform"):
            base["source_platform"] = base["category"]
        return base

    def _save_and_emit(self, url, title, body, book_name, category, signal_name, auto_route=False):
        try:
            # ★ 第一步：优先尝试离线解析（如微博 sinaurl 中转）
            if self.resolver.needs_resolve(url):
                offline_url = self.resolver._try_offline_resolve(url)
                if offline_url:
                    url = offline_url  # 替换为真实的夸克链接

                    # ★ 第二步：根据转译后的真实 URL 重新匹配分类（如自动变成"夸克网盘"）
                    for plat_name, domains in PLATFORM_DOMAINS.items():
                        if any(d in url for d in domains):
                            category = plat_name
                            break

                    print(f"✨ [Picker] 离线解析成功，自动纠正分类为: {category}")

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
                ws_signals.new_data_inserted.emit(1)

                # 此时因为入库的就是夸克链接，is_short_url 肯定为 False
                if auto_route and not is_short_url(url):
                    try:
                        trigger_organize_single(int(record["db_id"]))
                        print(f"🚀 [Picker] 非短链自动进入流水线: {url[:60]}")
                    except (ValueError, KeyError, TypeError) as e:
                        print(f"⚠️ [Picker] 自动流水线触发失败: {e}")
                elif auto_route and is_short_url(url):
                    print(f"✅ [Picker] 已拾取（短链，等待手动解析）: {(title or '')[:40]}")
                else:
                    print(f"📥 [Picker] 已拾取（仅保存，等待手动触发解析）: {(title or '')[:40]}")
            else:
                print(f"♻️ [Picker] 重复链接跳过: {url[:60]}")
        except Exception as e:
            print(f"❌ [Picker] 拾取失败: {e}")

    @Slot(str, str, str, str, str)
    def manual_pick(self, url, title, body, book_name, category):
        # ★ 手动拾取：非短链自动流水线
        self._save_and_emit(url, title, body, book_name, category, "picked", auto_route=True)

    @Slot(str, str, str, str, str)
    def save_page(self, url, title, body, book_name, category):
        # 非短链也自动进流水线；短链仍然留 pending 等批量解析
        self._save_and_emit(url, title, body, book_name, category, "saved_page", auto_route=True)

    @Slot(str)
    def save_batch(self, batch_id):
        bid = batch_id if batch_id else "LEGACY_BATCH"
        trigger_parse(bid, only_manual=True)
        self.batch_saved.emit(bid)
        print(f"📦 [Picker] 已触发批量解析: batch_id={bid}")

    @Slot(str)
    def switch_ua(self, mode):
        try:
            target = int(mode)
        except (TypeError, ValueError):
            return
        self.ua_switched.emit(target)

    @Slot(result=int)
    def get_pending_count(self):
        """保留（未来可能复用），网页端不再调用。"""
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