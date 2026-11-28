# url_invalid_batch.py
"""批量链接失效判断 —— 后台 Worker + 进度对话框。

用途：
    1. 从 sites 表收集「资源状态为空」且平台规则支持自动判断的链接；
    2. 后台并发调用 url_invalid.check_url；
    3. 按判定结果回写 sites.resource_status。

约束（与需求一致）：
    - 先按 module 分流：search / other / pansite →「网站类」，不查规则、不发网络；
    - 其余平台查 PLATFORM_CHECK_RULES：查不到规则、checker 为 skip / spa →「暂无判断」；
    - 判了就有结论：invalid→已失效 / valid→有效 / unknown→未知 / blocked、error→失败；
    - 终态只有「已失效」「网站类」；有效 / 未知 / 失败 / 暂无判断 下次都会被重新判断。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QTextEdit, QVBoxLayout,
)

from db_config import get_conn
from ui_styles import BTN_PRIMARY, BTN_SECONDARY
from url_invalid_judge import CheckResult, check_url, _DOMAIN_INDEX

from app_data.complaint_platform_consts import (
    CAT_TO_CODE_MAP, MODULE_SEARCH, MODULE_OTHER, MODULE_PANSITE,
)
from app_data.resource_status_consts import (
    CHECKER_STATUS_TO_RESOURCE, STATUS_INVALID, STATUS_WEBSITE,
)
from app_data.url_invalid_rules import PLATFORM_CHECK_RULES


_RULE_INDEX = {k.lower(): v for k, v in PLATFORM_CHECK_RULES.items()}


def _get_rule(category: str):
    if not category:
        return None
    return _RULE_INDEX.get(category.lower())


# 自动可判断的 checker 白名单（跳过 skip / spa）
# ★ "browser" = BrowserManager 接管的真实浏览器（贴吧这类 SPA + 强反爬站点）。
_SUPPORTED_CHECKERS = {"http", "quark_api", "uc_api", "browser"}
_MAX_WORKERS = 6        # 并发请求数
_MAX_LOG_LINES = 500    # 日志显示上限


@dataclass
class CheckTask:
    db_id: str
    url: str
    platform: str = ""
    kind: str = "check"   # "check" | "website" | "no_rule"


# =========================================================
# 数据层：收集 + 回写
# =========================================================
def _normalize_url(url: str) -> str:
    """去掉后缀、补全 scheme。不带 scheme 的 URL 会让 urlparse.hostname 变成 None。"""
    url = (url or "").replace(" (重复资源链接)", "").strip()
    if not url:
        return ""
    low = url.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        url = "https://" + url
    return url


def collect_checkable_tasks():
    stats = {
        "total": 0,
        "has_status": 0,           # 已是终态（网站类/已失效）
        "empty_url": 0,
        "website_marked": 0,
        "no_rule": 0,              # 无规则 → 写"暂无判断"
        "unsupported_checker": 0,  # skip/spa → 写"暂无判断"
        "checkable": 0,
        "domain_index_empty": not bool(_DOMAIN_INDEX),
    }

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, site_url, resource_status, category FROM sites"
        ).fetchall()
    finally:
        conn.close()

    tasks: list[CheckTask] = []
    for db_id, raw_url, res_status, category in rows:
        stats["total"] += 1

        # ★ 终态只有「网站类」「已失效」；有效 / 未知 / 失败 / 暂无判断 下轮都会重判
        #   （未知、失败 天然可自愈，不需要额外的重试队列）
        if (res_status or "").strip() in (STATUS_WEBSITE, STATUS_INVALID):
            stats["has_status"] += 1
            continue

        url = _normalize_url(raw_url)
        if not url:
            stats["empty_url"] += 1
            continue

        category = (category or "").strip()

        # ① search / other / pansite 模块 → "网站类"
        #   pansite（爱网盘/爱搜/影盘社/短剧搜）是"站"，不是内容页，不进入失效判断。
        #   ★ 模块判断必须排在规则查询之前；rules 里不要再给这些平台写 skip，
        #     否则同一行会有两套结论（模块说网站类、规则说跳过）。
        module = (CAT_TO_CODE_MAP.get(category) or {}).get("module")
        if module in (MODULE_SEARCH, MODULE_OTHER, MODULE_PANSITE):
            stats["website_marked"] += 1
            tasks.append(CheckTask(db_id=str(db_id), url=url,
                                   platform=category, kind="website"))
            continue

        # ② 无规则 → "暂无判断"
        rule = _get_rule(category)
        if not rule:
            stats["no_rule"] += 1
            tasks.append(CheckTask(db_id=str(db_id), url=url,
                                   platform=category, kind="no_rule"))
            continue

        # ③ skip / spa → "暂无判断"
        if rule.get("checker") not in _SUPPORTED_CHECKERS:
            stats["unsupported_checker"] += 1
            tasks.append(CheckTask(db_id=str(db_id), url=url,
                                   platform=category, kind="no_rule"))
            continue

        # ④ 真·联网判断
        stats["checkable"] += 1
        tasks.append(CheckTask(db_id=str(db_id), url=url,
                               platform=category, kind="check"))

    return tasks, stats


# 判断器结果 → 资源状态：词汇表统一在 app_data/resource_status_consts.py
_STATUS_MAP = CHECKER_STATUS_TO_RESOURCE

def apply_invalid_results(results) -> dict:
    """按状态分组写入 sites.resource_status，返回 {状态: 数量}。"""
    buckets: dict[str, set] = {}
    for r in results:
        db_id = r.extra.get("db_id")
        if not db_id:
            continue
        target = _STATUS_MAP.get(r.status)
        if not target:
            continue
        buckets.setdefault(target, set()).add(db_id)

    if not buckets:
        return {}

    conn = get_conn()
    try:
        for status_text, ids in buckets.items():
            conn.executemany(
                "UPDATE sites SET resource_status=? WHERE id=?",
                [(status_text, i) for i in ids])
        conn.commit()
    finally:
        conn.close()

    return {k: len(v) for k, v in buckets.items()}


# =========================================================
# 后台 Worker
# =========================================================
class _CheckSignals(QObject):
    progress = Signal(int, int)      # done, total
    one_done = Signal(object)        # CheckResult
    finished = Signal(list)          # list[CheckResult]
    failed = Signal(str)


class _CheckWorker(QRunnable):
    def __init__(self, tasks: list[CheckTask], signals: _CheckSignals):
        super().__init__()
        self.tasks = tasks
        self.signals = signals
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self.tasks)
        results: list[CheckResult] = []

        # ★ 浏览器检查器按需安装（贴吧这类 SPA 平台才会真正用到浏览器）
        try:
            from url_browser_check import install as _install_browser_checker
            _install_browser_checker()
        except Exception as e:
            print(f"[URL检查] 浏览器检查器安装失败: {e}")

        web_tasks = [t for t in self.tasks if t.kind == "website"]
        no_rule_tasks = [t for t in self.tasks if t.kind == "no_rule"]
        check_tasks = [t for t in self.tasks if t.kind == "check"]

        try:
            # ① website
            for t in web_tasks:
                if self._cancelled:
                    break
                r = CheckResult(url=t.url, platform=t.platform,
                                status="website", reason="search/other/pansite 模块")
                r.extra["db_id"] = t.db_id
                results.append(r)
                self.signals.one_done.emit(r)
                self.signals.progress.emit(len(results), total)

            # ② no_rule（无规则 / skip / spa）→ 立即产出 no_rule（不发网络）
            #    用独立 status，别复用 unknown：unknown 现在的含义是「判了，但没命中特征」→ 未知（红）。
            for t in no_rule_tasks:
                if self._cancelled:
                    break
                r = CheckResult(url=t.url, platform=t.platform,
                                status="no_rule",
                                reason="无规则 / skip / spa，未执行判断")
                r.extra["db_id"] = t.db_id
                results.append(r)
                self.signals.one_done.emit(r)
                self.signals.progress.emit(len(results), total)

            if self._cancelled:
                self.signals.finished.emit(results)
                return

            # ③ checker 任务（原逻辑不变）
            pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
            try:
                futures = {pool.submit(check_url, t.url, t.platform or None): t
                           for t in check_tasks}
                for fut in as_completed(futures):
                    if self._cancelled:
                        break
                    task = futures[fut]
                    try:
                        r = fut.result()
                    except Exception as e:
                        r = CheckResult(url=task.url, platform=task.platform,
                                        status="error", reason=str(e))
                    r.extra["db_id"] = task.db_id
                    results.append(r)
                    self.signals.one_done.emit(r)
                    self.signals.progress.emit(len(results), total)
            finally:
                pool.shutdown(wait=not self._cancelled,
                              cancel_futures=self._cancelled)

            self.signals.finished.emit(results)
        except Exception as e:
            self.signals.failed.emit(str(e))


# =========================================================
# 进度对话框
# =========================================================
class UrlInvalidCheckDialog(QDialog):
    def __init__(self, tasks, parent=None):
        super().__init__(parent)

        # 兼容误传 (tasks, stats) tuple 的情况，避免 for 循环里拿到 list 导致 t.url 崩
        if isinstance(tasks, tuple) and len(tasks) == 2 and isinstance(tasks[0], list):
            tasks = tasks[0]

        self.tasks: list[CheckTask] = [t for t in tasks if isinstance(t, CheckTask)]
        # 兜底：过滤后若空，直接退出，别让 worker 在空 list 上 work 一圈
        self.results: list[CheckResult] = []
        self._log_lines = 0
        self._running = True
        self._cancel_requested = False

        self._signals = _CheckSignals()
        self._signals.progress.connect(self._on_progress)
        self._signals.one_done.connect(self._on_one_done)
        self._signals.finished.connect(self._on_finished)
        self._signals.failed.connect(self._on_failed)

        self._worker: _CheckWorker | None = None
        self._pool = QThreadPool.globalInstance()

        self.setWindowTitle("批量判断链接失效")
        self.setMinimumSize(760, 520)
        self._setup_ui()
        if self.tasks:
            self._start()
        else:
            # 防御：告诉用户而不是静默
            self.lbl_status.setText("没有可检查的任务（tasks 为空或类型不匹配）")
            self.btn_cancel.setText("关闭")

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.lbl_status = QLabel(f"准备检查 {len(self.tasks)} 条链接…")
        self.lbl_status.setStyleSheet("font-size: 13px; color: #333;")
        layout.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setRange(0, max(1, len(self.tasks)))
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet(
            "QTextEdit { background: #F8F9FA; border: 1px solid #E5E7EB; "
            "font-family: Consolas, 'Microsoft YaHei'; font-size: 12px; }"
        )
        layout.addWidget(self.log, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setStyleSheet(BTN_SECONDARY)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self.btn_cancel)

        self.btn_apply = QPushButton("应用结果并关闭")
        self.btn_apply.setStyleSheet(BTN_PRIMARY)
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_apply)

        layout.addLayout(btn_row)

    def _start(self):
        self._worker = _CheckWorker(self.tasks, self._signals)
        self._worker.setAutoDelete(False)   # 运行结束后仍允许安全 cancel()
        self._pool.start(self._worker)

    def _append_log(self, text: str):
        if self._log_lines >= _MAX_LOG_LINES:
            return
        self.log.append(text)
        self._log_lines += 1

    def _on_progress(self, done: int, total: int):
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(done)
        if self._running:
            self.lbl_status.setText(f"检查中… {done}/{total}")

    def _on_one_done(self, r: CheckResult):
        if r.status == "invalid":
            self._append_log(f"✗ [已失效] {r.platform}  {r.reason}  {r.url}")
        elif r.status == "website":
            self._append_log(f"★ [网站类] {r.platform}  {r.url}")
        elif r.status == "valid":
            self._append_log(f"✓ [有效] {r.platform}  {r.reason}  {r.url}")
        elif r.status == "unknown":
            self._append_log(f"? [未知] {r.platform}  {r.reason}  {r.url}")
        elif r.status in ("error", "blocked"):
            self._append_log(f"! [失败] {r.platform}  {r.reason}  {r.url}")
        elif r.status in ("no_rule", "spa"):
            self._append_log(f"- [暂无判断] {r.platform}  {r.reason}  {r.url}")

    def _on_finished(self, results):
        self._running = False

        if self._cancel_requested:
            self.results = []
            self.reject()
            return

        self.results = results

        invalid = sum(1 for r in results if r.status == "invalid")
        website = sum(1 for r in results if r.status == "website")
        valid = sum(1 for r in results if r.status == "valid")
        unknown = sum(1 for r in results if r.status == "unknown")
        failed = sum(1 for r in results if r.status in ("blocked", "error"))
        pending = sum(1 for r in results if r.status in ("no_rule", "spa"))

        self.progress.setValue(self.progress.maximum())
        self.lbl_status.setText(
            f"完成：共 {len(results)} 条 | 已失效 {invalid} | 网站类 {website} | "
            f"有效 {valid} | 未知 {unknown} | 失败 {failed} | 暂无判断 {pending}"
        )
        self.btn_cancel.setText("关闭")

        # 只要有任何可回写的状态，就允许「应用」；未知/失败/有效 现在也会写库
        has_apply = any(r.status in _STATUS_MAP for r in results)
        # 始终可点：有结果就是"应用"，没结果就是"关闭"，不要给用户灰按钮的错觉
        self.btn_apply.setEnabled(True)
        self.btn_apply.setText("应用结果并关闭" if has_apply else "无新结果，关闭")

    def _on_failed(self, msg: str):
        self._running = False
        self.lbl_status.setText(f"检查失败：{msg}")
        self.btn_cancel.setText("关闭")
        # 失败时同样不要留一个灰掉的"应用结果并关闭"
        self.btn_apply.setEnabled(True)
        self.btn_apply.setText("关闭")

    def _on_cancel_clicked(self):
        if self._running and self._worker:
            self._cancel_requested = True
            self._worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.btn_apply.setEnabled(False)
            self.lbl_status.setText("正在停止…")
        else:
            self.accept()

    def closeEvent(self, e):
        if self._running and self._worker:
            self._cancel_requested = True
            self._worker.cancel()
        super().closeEvent(e)