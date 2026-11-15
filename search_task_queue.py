# search_task_queue.py
import json
from datetime import datetime
from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QMessageBox
from db_config import get_conn
from ws_server import dispatch_search_task, ws_signals
from plugin_bridge import bridge_signals
from parser_service import parser_signals
from rate_limiter import global_limiter
from link_ingest_service import check_drafts_exist   # ★ 新增


class QueueSignals(QObject):
    queue_updated = Signal()
    task_state_changed = Signal(int, str)
    batch_switched = Signal(str, str)


queue_signals = QueueSignals()


class SearchTaskQueue(QObject):
    def __init__(self):
        super().__init__()
        self.is_running = False
        self._current_task_id = None
        self._current_task = None                      # ★ 新增：dict 或 None
        self._running_tasks = []                       # ★ 类型改为 list[dict]

        self._session_batch_id = None
        self._ui_notified_batch = None

        self._is_waiting_for_parse = False
        self._current_parsing_batch_id = None

        self.timeout_timer = QTimer(self)
        self.timeout_timer.setSingleShot(True)
        self.timeout_timer.timeout.connect(self._on_task_timeout)

        self.disconnect_timer = QTimer(self)
        self.disconnect_timer.setSingleShot(True)
        self.disconnect_timer.timeout.connect(self._force_abort_on_disconnect)

        ws_signals.search_finished.connect(self._on_search_finished)
        parser_signals.parse_finished.connect(self._on_parse_finished)

        bridge_signals.plugin_disconnected.connect(self._on_plugin_disconnected)
        bridge_signals.plugin_connected.connect(self._on_plugin_connected)

        self._consecutive_failures = 0

    # ---------------- 插件连接保护 ----------------
    def _on_plugin_disconnected(self):
        from ws_server import get_app_mode
        if get_app_mode() == "searching":
            print("[Queue] ⚠️ 插件断开，但当前有 Playwright 搜索任务，忽略断连清空逻辑。")
            return
        if self.is_running:
            print("[Queue] ⚠️ 检测到插件连接断开！等待 5 秒重连宽限期...")
            self.disconnect_timer.start(5000)

    def _on_plugin_connected(self):
        if self.disconnect_timer.isActive():
            print("[Queue] ✅ 插件已重新连接，取消强制中断")
            self.disconnect_timer.stop()

    def _force_abort_on_disconnect(self):
        if not self.is_running:
            return
        print("[Queue] ❌ 插件断联超时，强制清空当前队列！")
        self.stop(reason="disconnect")                 # ★ 统一走 stop
        QMessageBox.warning(
            None, "任务中断",
            "浏览器或插件连接已断开超过 5 秒，当前搜索队列已被强制清空。\n\n"
            "请确保插件已连接，重新点击执行即可。"
        )

    # ---------------- 队列数据 ----------------
    def _cleanup_orphans(self):
        conn = get_conn()
        try:
            cursor = conn.cursor()
            count = cursor.execute(
                "SELECT COUNT(*) FROM search_tasks WHERE status = 'running'"
            ).fetchone()[0]
            if count > 0:
                print(f"[Queue] 发现 {count} 个上次残留的 running 任务，正在重置为 failed")
                conn.execute("UPDATE search_tasks SET status = 'failed' WHERE status = 'running'")
                conn.commit()
        finally:
            conn.close()

    def init_cleanup(self):
        self._cleanup_orphans()

    def get_tasks(self):
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT id, book_id, book_name, platform, main_keyword, sub_keywords, "
                "max_pages, status, last_batch_id, last_run_at "
                "FROM search_tasks ORDER BY id ASC"
            ).fetchall()
            return [{
                "id": r[0], "book_id": r[1], "book_name": r[2], "platform": r[3],
                "main_keyword": r[4],
                "sub_keywords": json.loads(r[5]) if r[5] else [],
                "max_pages": r[6], "status": r[7],
                "last_batch_id": r[8], "last_run_at": r[9]
            } for r in rows]
        finally:
            conn.close()

    def get_task_by_id(self, task_id):
        """★ 原 _get_task_data 公开。面板单跑时先拿 task dict，再传给 start_one。"""
        conn = get_conn()
        try:
            r = conn.execute(
                "SELECT id, book_id, book_name, platform, main_keyword, sub_keywords, "
                "max_pages, status, last_batch_id, last_run_at "
                "FROM search_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if r:
                return {
                    "id": r[0], "book_id": r[1], "book_name": r[2], "platform": r[3],
                    "main_keyword": r[4],
                    "sub_keywords": json.loads(r[5]) if r[5] else [],
                    "max_pages": r[6], "status": r[7],
                    "last_batch_id": r[8], "last_run_at": r[9]
                }
            return None
        finally:
            conn.close()

    def add_task(self, book_id, book_name, platform, main_keyword, sub_keywords, max_pages):
        """仅"加入队列"用，内部去重。返回 bool。"""
        existing_tasks = self.get_tasks()
        for t in existing_tasks:
            if (t["book_id"] == book_id
                    and t["platform"] == platform
                    and t["main_keyword"] == main_keyword
                    and sorted(t["sub_keywords"]) == sorted(sub_keywords)):
                return False

        conn = get_conn()
        try:
            sub_kws_json = json.dumps(sorted(sub_keywords), ensure_ascii=False)
            conn.execute(
                "INSERT INTO search_tasks (book_id, book_name, platform, main_keyword, "
                "sub_keywords, max_pages, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (book_id, book_name, platform, main_keyword, sub_kws_json, max_pages,
                 'ready', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
            queue_signals.queue_updated.emit()
            return True
        finally:
            conn.close()

    def remove_task(self, task_id):
        if self.is_running and self._current_task_id == task_id:
            return False
        conn = get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM search_tasks WHERE id = ?", (task_id,))
            conn.commit()
            queue_signals.queue_updated.emit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    # ---------------- 草稿检查（两个 start 都走） ----------------
    def _check_drafts_or_warn(self):
        draft_count = check_drafts_exist()
        if draft_count > 0:
            QMessageBox.warning(
                None, "提示",
                f"发现还有 {draft_count} 条草稿尚未处理完毕。\n\n"
                f"请先将右侧的数据处理完（入库 / 永删 / 忽略），\n"
                f"或者直接点击右下角的【清空当前分类】清理掉它们，\n"
                f"然后再进行下一次搜索。"
            )
            return False
        return True

    # ---------------- 启动 ----------------
    def start_all(self):
        """跑队列全部。返回 True 表示已启动，False 表示被拦下（已弹过窗）。"""
        if self.is_running:
            QMessageBox.information(None, "提示", "队列中已有任务正在执行，请等待完成。")
            return False

        tasks = self.get_tasks()
        if not tasks:
            return False

        if not self._check_drafts_or_warn():
            return False

        conn = get_conn()
        try:
            conn.execute("UPDATE search_tasks SET status = 'ready' WHERE status IN ('failed', 'stopped')")
            conn.commit()
        finally:
            conn.close()

        self._consecutive_failures = 0
        self._session_batch_id = f"QUEUE_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._ui_notified_batch = None

        self._running_tasks = tasks                    # ★ list[dict]
        self.is_running = True
        self._execute_next()
        return True

    def start_one(self, task_data, batch_prefix="SINGLE_"):
        """跑单个任务。
        task_data 结构同 get_tasks() 的元素，id 可为 None（输入面板直接搜索）。
        返回 True/False（False 时已弹窗）。
        """
        if self.is_running:
            QMessageBox.information(None, "提示", "队列中已有任务正在执行，请等待完成。")
            return False

        if not task_data:
            return False

        if not self._check_drafts_or_warn():
            return False

        self._consecutive_failures = 0
        self._session_batch_id = f"{batch_prefix}{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._ui_notified_batch = None

        self._running_tasks = [task_data]              # ★ list[dict]
        self.is_running = True
        self._execute_next()
        return True

    # ---------------- 停止（所有异常/用户停止的统一收口） ----------------
    def stop(self, reason="user"):
        """reason: user / dispatch_failed / consecutive_failures / disconnect"""
        batch_id = self._session_batch_id              # ★ 先存
        was_running = self.is_running

        self.is_running = False
        self._running_tasks.clear()
        self._is_waiting_for_parse = False
        self._current_parsing_batch_id = None
        if self.timeout_timer.isActive():
            self.timeout_timer.stop()

        if self._current_task_id:
            status = 'stopped' if reason == 'user' else 'failed'
            self._set_status(self._current_task_id, status)
        self._current_task_id = None
        self._current_task = None

        self._session_batch_id = None
        self._ui_notified_batch = None
        queue_signals.queue_updated.emit()

        if was_running and batch_id:                   # ★ 只在真正跑过时发
            ws_signals.batch_finished.emit(batch_id)

    # ---------------- 调度核心 ----------------
    def _execute_next(self):
        if not self.is_running or not self._running_tasks:
            self.is_running = False
            batch_id = self._session_batch_id
            self._session_batch_id = None
            self._ui_notified_batch = None
            queue_signals.queue_updated.emit()
            if batch_id:                               # ★ 正常跑完，收口
                ws_signals.batch_finished.emit(batch_id)
            return

        task = self._running_tasks.pop(0)              # ★ 直接是 dict
        self._current_task = task
        self._current_task_id = task.get("id")         # 可能 None（ADHOC）

        self._set_status(self._current_task_id, 'running')   # 内部判空
        queue_signals.queue_updated.emit()

        batch_id = self._session_batch_id
        res = dispatch_search_task(
            platform=task["platform"],
            keyword=task["main_keyword"],
            max_pages=task["max_pages"],
            sub_kws=task["sub_keywords"],
            book_name=task["book_name"],
            batch_id=batch_id,
            book_id=task.get("book_id"),               # ★ A 路线依赖
        )

        if self._ui_notified_batch != batch_id:
            queue_signals.batch_switched.emit(batch_id, task["book_name"])
            self._ui_notified_batch = batch_id

        if res in ("success", "opening"):
            self.timeout_timer.start(300000)
        else:
            print(f"[Queue] 任务派发失败，返回状态: {res}")
            self.stop(reason="dispatch_failed")        # ★ 统一收口
            if res == "busy":
                QMessageBox.warning(None, "任务受阻",
                                    "检测到有其他搜索/预览正在运行，队列已暂停。")
            else:
                QMessageBox.warning(None, "任务异常", f"任务派发失败，状态返回: {res}")

    # ---------------- 信号回调 ----------------
    def _on_search_finished(self, batch_id):
        if not self.is_running or self._current_task is None:   # ★ 改判空
            return

        self.timeout_timer.stop()

        if batch_id.startswith("ERROR_"):
            print("[Queue] ❌ 任务执行失败/超时，跳过解析，标记失败。")
            self._consecutive_failures += 1
            self._set_status(self._current_task_id, 'failed')
            self._current_task_id = None
            self._current_task = None

            if self._consecutive_failures >= 3:
                print(f"[Queue] ❌ 连续失败 {self._consecutive_failures} 次，停止队列避免死循环")
                self.stop(reason="consecutive_failures")
                QMessageBox.warning(
                    None, "任务连续失败",
                    "连续 3 次搜索任务失败，队列已自动停止。\n\n"
                    "请检查：\n"
                    "1. 浏览器是否正常运行\n"
                    "2. 9333 端口是否可访问\n"
                    "3. 插件是否已连接\n\n"
                    "处理完后再重新点击执行。"
                )
                return

            self._execute_next()
            return

        self._consecutive_failures = 0
        self._update_batch_id(self._current_task_id, batch_id)  # 内部判空
        self._set_status(self._current_task_id, 'ready')
        self._current_task_id = None

        self._is_waiting_for_parse = True
        self._current_parsing_batch_id = batch_id
        print(f"[Queue] ⏸️ 搜索完成，队列挂起，等待批次 {batch_id} 解析完成...")

    def _on_task_timeout(self):
        if self._current_task is not None:                     # ★ 改判空
            print(f"⚠️ 队列任务 {self._current_task_id} 超时未完成，跳过。")
            self._set_status(self._current_task_id, 'failed')
            self._current_task_id = None
            self._current_task = None
            self._execute_next()

    def _on_parse_finished(self, success, batch_id):
        if not self.is_running or not self._is_waiting_for_parse:
            return
        if batch_id != self._current_parsing_batch_id:
            return

        self._is_waiting_for_parse = False
        self._current_parsing_batch_id = None

        if success:
            print(f"[Queue] ▶️ 批次 {batch_id} 解析完成，队列恢复，准备执行下一个任务...")
        else:
            print(f"[Queue] ⚠️ 批次 {batch_id} 解析失败/异常退出，为防死锁，强行唤醒队列...")

        self._execute_next()

    # ---------------- 工具 ----------------
    def _set_status(self, task_id, status):
        if task_id is None:                            # ★ ADHOC 不写库
            return
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE search_tasks SET status = ?, last_run_at = ? WHERE id = ?",
                (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id)
            )
            conn.commit()
            queue_signals.task_state_changed.emit(task_id, status)
        finally:
            conn.close()

    def _update_batch_id(self, task_id, batch_id):
        if task_id is None:                            # ★ ADHOC 不写库
            return
        conn = get_conn()
        try:
            conn.execute("UPDATE search_tasks SET last_batch_id = ? WHERE id = ?",
                         (batch_id, task_id))
            conn.commit()
        finally:
            conn.close()


task_queue = SearchTaskQueue()