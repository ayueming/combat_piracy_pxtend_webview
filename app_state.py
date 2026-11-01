# app_state.py
"""应用级运行时状态。

职责：
  - 模式（idle / searching / previewing）
  - 忙碌标记 + 超时定时器
  - 活跃任务表
  - 当前批次上下文（book_name / keyword / batch_id / source_platform）

不做什么：
  - 不认识信号（发信号由调用方负责）
  - 不认识 WS / 浏览器 / 数据库
"""
import threading
from datetime import datetime


class AppState:
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_state()
        return cls._instance

    def _init_state(self):
        self._lock = threading.Lock()
        self._mode = "idle"
        self._busy = False
        self._busy_timer = None
        self._active_tasks = {}
        # ★ 这个 dict 对象创建后永不重新赋值（只 update），
        #   保证外部 current_context 引用的稳定性
        self._context = {
            "book_name": "未选择作品",
            "keyword": "未选择关键词",
            "batch_id": f"plugin_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "source_platform": ""
        }

    @property
    def current_context(self):
        return self._context

    # ---- 模式 ----
    def get_mode(self):
        with self._lock:
            return self._mode

    def set_mode(self, mode):
        with self._lock:
            self._mode = mode
        print(f"[状态锁] 当前模式切换为: {mode}")

    def check_and_lock(self, mode_name):
        with self._lock:
            if self._mode == "idle":
                self._mode = mode_name
                return True
            return False

    # ---- 上下文 ----
    def set_context(self, **kwargs):
        self._context.update(kwargs)

    # ---- 忙碌 ----
    def is_busy(self):
        with self._lock:
            return self._busy

    def busy_begin(self, task_id, timeout_sec=300, on_timeout=None):
        with self._lock:
            self._busy = True
            self._active_tasks.setdefault(task_id, {})

            def _timeout():
                with self._lock:
                    self._active_tasks.pop(task_id, None)
                self.busy_end("timeout")
                if on_timeout:
                    try:
                        on_timeout()
                    except Exception:
                        pass

            t = threading.Timer(timeout_sec, _timeout)
            t.daemon = True
            t.start()
            self._busy_timer = t

    def busy_end(self, reason=""):
        with self._lock:
            if self._busy_timer:
                try:
                    self._busy_timer.cancel()
                except Exception:
                    pass
                self._busy_timer = None
            was_busy = self._busy
            self._busy = False
        if was_busy:
            print(f"[任务] 已清除 busy 状态 ({reason})")

    # ---- 活跃任务 ----
    def add_task(self, task_id, task):
        with self._lock:
            self._active_tasks[task_id] = task

    def pop_task(self, task_id):
        with self._lock:
            return self._active_tasks.pop(task_id, None)


app_state = AppState()


# ---- 兼容旧 API（原来 ws_server 里的模块函数）----
def get_app_mode():
    return app_state.get_mode()


def set_app_mode(mode):
    app_state.set_mode(mode)


def check_and_lock(mode_name):
    return app_state.check_and_lock(mode_name)