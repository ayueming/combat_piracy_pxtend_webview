# links_url_guard.py

"""链接录入守卫：离线规整询问 + 短链/中转页联网展开。

从 page_links.py 搬出来的两段「编辑链接 → 弹窗确认 → 改写单元格」流程。两者结构相同，
原先在页面里各有一套 timer / pending 字典 / 状态集合，现在合并为一个控制器：

    编辑提交 ──┬─ 离线可修（inspect_url 里有 offline 问题）──> 50ms 合并 ──> 询问是否替换
               └─ 需要联网（短链/中转页）──────────────────> 600ms 合并 ──> 询问是否展开 ──> 线程池解析

与页面之间只通过 4 个回调交互，不含 SQL、不直接操作 model/table：
    item_provider(row) -> QStandardItem | None   取该行的「资源链接」单元格
    url_applier(row, url)                        写回单元格（页面据此重跑完整录入流程）
    suspend()                                    上下文管理器：期间页面不响应 itemChanged
    is_editing() -> bool                         表格是否在编辑态（编辑中则顺延弹窗）

注意：不能在本模块的定时器回调里同步弹模态框之前动单元格，否则会打断 editor 的
commit 过程（表现为“回车后没反应”）——所以一律先合并到定时器、再询问、最后改写。
"""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QMessageBox

from app_data.link_columns import DUP_URL_SUFFIX
from url_normalize import apply_offline, inspect_url, needs_online_unwrap

# 用户拒绝联网展开的短链/中转页标黄，提示「这条还没拿到真实地址」
SHORT_URL_YELLOW = QColor("#FFF3CD")

_CANONICAL_DELAY_MS = 50    # 离线规整：等编辑器 commit 完
_SHORT_URL_DELAY_MS = 600   # 短链：合并连续编辑，避免逐个弹窗


class _ShortUrlResolveSignals(QObject):
    finished = Signal(int, str, str)   # row, old_url, new_url
    failed = Signal(int, str, str)     # row, old_url, err_msg


class _ShortUrlResolveWorker(QRunnable):
    def __init__(self, row, url, signals):
        super().__init__()
        self.row = row
        self.url = url
        self.signals = signals

    def run(self):
        try:
            from browser_manager import BrowserManager
            from link_resolver import LinkResolver
            try:
                bm = BrowserManager.instance()
            except Exception:
                bm = None
            resolver = LinkResolver(browser_manager=bm)
            new_url = resolver.resolve_single(self.url, 1)   # 移动端
            self.signals.finished.emit(self.row, self.url, new_url or "")
        except Exception as e:
            self.signals.failed.emit(self.row, self.url, str(e))


def _clean(text):
    """去掉重复标记后缀，得到纯 URL。"""
    return (text or "").replace(DUP_URL_SUFFIX, "").strip()


class LinksUrlGuard(QObject):
    def __init__(self, parent, *, item_provider, url_applier, suspend, is_editing):
        super().__init__(parent)
        self._item_provider = item_provider
        self._url_applier = url_applier
        self._suspend = suspend
        self._is_editing = is_editing

        # 离线规整：合并同一批编辑，50ms 后统一询问
        self._canonical_timer = QTimer(self)
        self._canonical_timer.setSingleShot(True)
        self._canonical_timer.setInterval(_CANONICAL_DELAY_MS)
        self._canonical_timer.timeout.connect(self._flush_canonical)
        self._pending_canonical = {}      # {row: (raw_url, target, issues)}

        # 短链 / 中转页：合并连续编辑，600ms 后统一询问
        self._short_timer = QTimer(self)
        self._short_timer.setSingleShot(True)
        self._short_timer.setInterval(_SHORT_URL_DELAY_MS)
        self._short_timer.timeout.connect(self._flush_short_url)
        self._pending_short = {}          # {row: url}
        self._resolving = set()           # {row}
        self._declined = set()            # 本次会话内用户拒绝展开的 url
        self._muted = False               # 守卫自己改写单元格时，不再重复调度检测
        self._pool = QThreadPool.globalInstance()

    # ================= 对外接口 =================
    def is_declined(self, url):
        """页面画黄色底时用：这条是否被用户拒绝过展开。"""
        return bool(url) and url in self._declined

    def reset(self):
        """表格整体刷新后行号会变，清空待办与解析中状态，避免写错行。"""
        self._pending_short.clear()
        self._resolving.clear()
        self._short_timer.stop()
        self._canonical_timer.stop()
        self._pending_canonical.clear()

    def schedule_offline_fix(self, row, raw_url):
        """有离线可修的问题时排一次询问；返回是否排上了。"""
        raw_url = (raw_url or "").strip()
        if not raw_url:
            return False

        offline_issues = [i for i in inspect_url(raw_url) if i.offline]
        if not offline_issues:
            return False

        target = apply_offline(raw_url)
        if not target or target == raw_url:
            return False

        self._pending_canonical[row] = (raw_url, target, offline_issues)
        if not self._canonical_timer.isActive():
            self._canonical_timer.start()
        return True

    def schedule_short_url_check(self, row, url):
        """短链/中转页检测入口；黄色底由页面 update_row_style 统一维护。"""
        if self._muted:
            return
        item = self._item(row)
        if item is None:
            return

        url = (url or "").strip()
        if not url or not needs_online_unwrap(url):
            self._paint(item, False)     # 非短链：清掉可能残留的黄色
            return

        if url in self._declined:
            return

        self._pending_short[row] = url
        self._short_timer.start()

    # ================= 内部实现 =================
    def _item(self, row):
        return self._item_provider(row)

    def _dialog_parent(self):
        parent = self.parent()
        return parent if parent is not None else None

    def _paint(self, item, yellow):
        """改背景色（在页面挂起状态下进行，避免触发 itemChanged 连锁）。"""
        if yellow:
            with self._suspend():
                item.setBackground(SHORT_URL_YELLOW)
        elif item.background().color() == SHORT_URL_YELLOW:
            with self._suspend():
                item.setBackground(QBrush())

    def _apply(self, row, url, *, mute):
        """清黄后把 URL 写回单元格，让页面按新链接重跑一遍完整录入流程。

        mute=True 时期间不再调度短链检测：短链解析回来的地址不应再次询问展开；
        离线规整则保持 mute=False，让规整后的地址继续按原逻辑走短链/中转检测。
        """
        item = self._item(row)
        if item is None:
            return
        prev = self._muted
        self._muted = mute
        try:
            self._paint(item, False)
            self._url_applier(row, url)
        finally:
            self._muted = prev

    # ---------- 离线规整 ----------
    def _flush_canonical(self):
        if not self._pending_canonical:
            return
        pending = self._pending_canonical
        self._pending_canonical = {}
        for row, (raw_url, target, issues) in pending.items():
            self._ask_offline_fix(row, raw_url, target, issues)

    def _ask_offline_fix(self, row, raw_url, target, offline_issues):
        item = self._item(row)
        if item is None:
            return
        if _clean(item.text()) != raw_url:
            return   # 询问期间用户又改了，放弃本次

        steps = "\n".join(f"· {i.label}" for i in offline_issues)
        reply = QMessageBox.question(
            self._dialog_parent(), "链接规整提示",
            f"该链接需要先做离线规整：\n\n{steps}\n\n"
            f"当前：\n{raw_url}\n\n"
            f"规整后：\n{target}\n\n"
            f"是否替换？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return
        # 不 mute：规整后的地址仍可能命中短链/中转，交给页面继续检测
        self._apply(row, target, mute=False)

    # ---------- 短链 / 中转页 ----------
    def _flush_short_url(self):
        if not self._pending_short:
            return
        if self._is_editing():
            self._short_timer.start()   # 还在编辑：顺延，别抢焦点
            return

        pending = self._pending_short
        self._pending_short = {}

        for row, url in pending.items():
            item = self._item(row)
            if item is None:
                continue
            if _clean(item.text()) != url:
                continue
            if row in self._resolving:
                continue

            reply = QMessageBox.question(
                self._dialog_parent(), "链接展开提示",
                f"该链接是短链或中转页，需要联网展开真实地址。\n\n{url}\n\n是否继续？",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                self._declined.add(url)
                self._paint(item, True)
                continue

            self._start_resolve(row, url)

    def _start_resolve(self, row, url):
        self._resolving.add(row)
        signals = _ShortUrlResolveSignals()
        signals.finished.connect(self._on_resolved)
        signals.failed.connect(self._on_failed)
        self._pool.start(_ShortUrlResolveWorker(row, url, signals))

    def _on_resolved(self, row, old_url, new_url):
        if row not in self._resolving:
            return          # 解析期间 refresh_table 已清空集合，说明 row 已失效
        self._resolving.discard(row)

        if not new_url or new_url == old_url:
            QMessageBox.information(
                self._dialog_parent(), "解析结果", "未能解析出真实地址，已保留原值。")
            return

        item = self._item(row)
        if item is None:
            return
        if _clean(item.text()) != old_url:
            return          # 解析期间用户改过，不覆盖

        self._declined.discard(old_url)
        self._apply(row, new_url, mute=True)
        QMessageBox.information(
            self._dialog_parent(), "解析成功", f"已替换为真实链接：\n{new_url}")

    def _on_failed(self, row, old_url, err_msg):
        if row not in self._resolving:
            return
        self._resolving.discard(row)
        QMessageBox.warning(
            self._dialog_parent(), "展开失败",
            f"链接展开失败：\n{err_msg}\n\n已保留原链接。")
