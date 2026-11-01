# parser_service.py
import threading
import queue
from PySide6.QtCore import QObject, Signal
from link_ingest_service import organize_pending_links
from db_config import get_conn
from app_data.crawl_status_consts import CRAWL_STATUS_PENDING


class ParserSignals(QObject):
    parse_started = Signal(str, int)
    parse_finished = Signal(bool)


parser_signals = ParserSignals()

# ★ 真正的任务队列（存放 (batch_id, is_exact, only_manual)）
_parse_queue = queue.Queue()
# ★ 排队/处理中的批次集合，用于去重（防止同一个批次被塞进去 100 次）
_pending_batches = set()
_lock = threading.Lock()


def _worker():
    """常驻解析线程，死循环从队列里拿任务"""
    while True:
        batch_id, is_exact, only_manual = _parse_queue.get()
        print(f"🛠️ 开始解析队列任务，批次: {batch_id}")

        # 从待处理集合中移除（允许后续再有新数据触发该批次）
        with _lock:
            _pending_batches.discard(batch_id)

        # ★ 新增：统计本批待解析数量并通知 UI（仅搜索任务弹窗，手动拾取不弹）
        try:
            conn = get_conn()
            try:
                total = conn.execute(
                    "SELECT COUNT(*) FROM draft_links WHERE batch_id = ? AND crawl_status = ?",
                    (batch_id, CRAWL_STATUS_PENDING)
                ).fetchone()[0]
            finally:
                conn.close()
            # ★ only_manual=True（手动拾取）不弹窗，只在控制台留日志
            if total > 0 and not only_manual:
                parser_signals.parse_started.emit(batch_id, total)
        except Exception as e:
            print(f"⚠️ [解析] 统计待解析数量失败: {e}")

        try:
            # ★ 核心：循环解析直到该批次彻底没有 pending
            while True:
                # 调用你的流水线函数
                organize_pending_links(
                    is_exact_match=is_exact,
                    batch_id=batch_id,
                    process_events_func=None,
                    only_manual=only_manual,
                )

                # 检查一下：在这个解析过程中，是不是又有新数据入库了？
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
                    break  # 彻底干净了，跳出循环

            print(f"✅ batch={batch_id} 解析完成！")
            parser_signals.parse_finished.emit(True)

        except Exception as e:
            print(f"❌ batch={batch_id} 解析失败: {e}")
            parser_signals.parse_finished.emit(False)
        finally:
            _parse_queue.task_done()


# 启动唯一的一个常驻工作线程
_worker_thread = threading.Thread(target=_worker, daemon=True, name="ParserWorker")
_worker_thread.start()


def trigger_parse(batch_id, is_exact=False, only_manual=False):
    """外部触发解析，只管往队列里塞"""
    with _lock:
        if batch_id in _pending_batches:
            # 已经在排队了，或者正在解析中，直接忽略，等它跑完自然会去查数据库
            print(f"⏭️ batch={batch_id} 已在解析队列中，忽略重复触发")
            return
        _pending_batches.add(batch_id)

    print(f"📥 收到解析请求，批次: {batch_id}，已加入排队")
    _parse_queue.put((batch_id, is_exact, only_manual))