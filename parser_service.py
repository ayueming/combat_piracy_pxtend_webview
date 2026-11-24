# parser_service.py
import threading
import queue
from PySide6.QtCore import QObject, Signal

from link_ingest_service import organize_pending_links
from db_config import get_conn
from app_data.crawl_status_consts import CRAWL_STATUS_PENDING
from ws_server import ws_signals   # ★ 用于单条完成后通知 UI 刷新


class ParserSignals(QObject):
    parse_started = Signal(str, int)
    parse_finished = Signal(bool, str)              # 批量完成，第二参数是 batch_id
    parse_single_finished = Signal(bool, int)       # ★ 新增：单条完成，第二参数是 db_id


parser_signals = ParserSignals()

# 队列元素：(batch_id, is_exact, only_manual, target_db_id)
#   - 批量：batch_id 有值，target_db_id=None
#   - 单条：batch_id=None，target_db_id 有值
_parse_queue = queue.Queue()
_pending_batches = set()    # 存 key：批量为 batch_id，单条为 f"single:{db_id}"
_lock = threading.Lock()


def _worker():
    while True:
        batch_id, is_exact, only_manual, target_db_id = _parse_queue.get()

        key = f"single:{target_db_id}" if target_db_id is not None else batch_id
        with _lock:
            _pending_batches.discard(key)

        # ============ 单条路径 ============
        if target_db_id is not None:
            try:
                organize_pending_links(
                    is_exact_match=is_exact,
                    batch_id=None,
                    only_manual=True,
                    target_db_id=target_db_id,
                )
                print(f"✅ 单条 db_id={target_db_id} 解析完成")
                parser_signals.parse_single_finished.emit(True, int(target_db_id))
                ws_signals.new_data_inserted.emit(1)   # 通知 UI 刷新
            except Exception as e:
                print(f"❌ 单条 db_id={target_db_id} 解析失败: {e}")
                parser_signals.parse_single_finished.emit(False, int(target_db_id))
                ws_signals.new_data_inserted.emit(1)
            finally:
                _parse_queue.task_done()
            continue

        # ============ 批量路径（原逻辑不变） ============
        print(f"🛠️ 开始解析队列任务，批次: {batch_id}")
        try:
            conn = get_conn()
            try:
                total = conn.execute(
                    "SELECT COUNT(*) FROM draft_links WHERE batch_id = ? AND crawl_status = ?",
                    (batch_id, CRAWL_STATUS_PENDING)
                ).fetchone()[0]
            finally:
                conn.close()
            if total > 0 and not only_manual:
                parser_signals.parse_started.emit(batch_id, total)
        except Exception as e:
            print(f"⚠️ [解析] 统计待解析数量失败: {e}")

        try:
            while True:
                organize_pending_links(
                    is_exact_match=is_exact,
                    batch_id=batch_id,
                    process_events_func=None,
                    only_manual=only_manual,
                )
                conn = get_conn()
                try:
                    remaining = conn.execute(
                        "SELECT COUNT(*) FROM draft_links WHERE batch_id = ? AND crawl_status = ?",
                        (batch_id, CRAWL_STATUS_PENDING)
                    ).fetchone()[0]
                finally:
                    conn.close()
                if remaining > 0:
                    print(f"🔄 batch={batch_id} 发现 {remaining} 条新入库数据，继续解析...")
                else:
                    break
            print(f"✅ batch={batch_id} 解析完成！")
            parser_signals.parse_finished.emit(True, batch_id)
        except Exception as e:
            print(f"❌ batch={batch_id} 解析失败: {e}")
            parser_signals.parse_finished.emit(False, batch_id)
        finally:
            _parse_queue.task_done()


_worker_thread = threading.Thread(target=_worker, daemon=True, name="ParserWorker")
_worker_thread.start()


def trigger_parse(batch_id, is_exact=False, only_manual=False):
    """外部触发批量解析"""
    with _lock:
        if batch_id in _pending_batches:
            print(f"⏭️ batch={batch_id} 已在解析队列中，忽略重复触发")
            return
        _pending_batches.add(batch_id)
    print(f"📥 收到批量解析请求，批次: {batch_id}")
    _parse_queue.put((batch_id, is_exact, only_manual, None))


def trigger_organize_single(db_id, is_exact=False):
    """★ 新增：单条解析。供 picker 非短链自动分流使用"""
    key = f"single:{db_id}"
    with _lock:
        if key in _pending_batches:
            print(f"⏭️ 单条 db_id={db_id} 已在解析队列中，忽略重复触发")
            return
        _pending_batches.add(key)
    print(f"📥 收到单条解析请求，db_id={db_id}")
    _parse_queue.put((None, is_exact, True, db_id))