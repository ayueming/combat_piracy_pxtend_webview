# browser_manager.py
import os
import time
import threading
import atexit

from playwright.sync_api import sync_playwright

from app_paths import get_user_dir, DIR_BROWSER_PROFILE
from app_data.browser_consts import CDP_PORT, CDP_READY_TIMEOUT
from app_data.mobile_consts import MOBILE_UA
from db_config import get_current_user
from browser_thread import BrowserThread

_MOBILE_CTX_DEFAULTS = dict(
    user_agent=MOBILE_UA,
    viewport={"width": 390, "height": 844},
    is_mobile=True,
    has_touch=True,
)

class BrowserManager:
    _instance = None
    _init_lock = threading.Lock()

    @classmethod
    def instance(cls):
        if cls._instance is None or not cls._instance._bt.is_alive():
            with cls._init_lock:
                if cls._instance is None or not cls._instance._bt.is_alive():
                    user_id = get_current_user()
                    if not user_id:
                        raise Exception("未登录")
                    profile_dir = os.path.join(get_user_dir(user_id), DIR_BROWSER_PROFILE)

                    old = cls._instance
                    old_alive = old._bt.is_alive() if old else None
                    old_bt_tid = old._bt._tid if old else None

                    cls._instance = cls(profile_dir)

                    print(f"[BM.instance] ⚠️ 发生实例替换！"
                          f"thread={threading.get_ident()} "
                          f"old={id(old)} old_bt_tid={old_bt_tid} old_alive={old_alive} "
                          f"new={id(cls._instance)} new_bt_tid={cls._instance._bt._tid}")

                    atexit.register(cls._instance.shutdown)
        return cls._instance

    def __init__(self, profile_dir):
        self.profile_dir = profile_dir

        self._bt = BrowserThread()
        self._bt.start()

        self._pw = None
        self._browser = None
        self._context = None

        self._pw_thread_id = None
        self._browser_thread_id = None
        self._context_thread_id = None

        self._shutdown_lock = threading.Lock()
        self.is_shutting_down = False
        self._picker_injected = False

    def reset_playwright(self):
        print(f"[BM.reset_playwright] ⚠️ 被调用 thread={threading.get_ident()} "
              f"bt_tid={self._bt._tid} 一致={threading.get_ident() == self._bt._tid}")
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._browser = None
        self._context = None
        self._pw_thread_id = None  # 新增
        self._browser_thread_id = None  # 新增
        self._context_thread_id = None  # 新增
        self._picker_injected = False

    def shutdown(self, force=False):
        with self._shutdown_lock:
            if self.is_shutting_down:
                return
            self.is_shutting_down = True

        print(f"[BM.shutdown] 被调用 thread={threading.get_ident()} "
              f"bt_tid={self._bt._tid} 一致={threading.get_ident() == self._bt._tid}")
        try:
            if self._bt.is_alive():
                self._bt.submit(self._shutdown_impl, block=True, timeout=20)
        except Exception as e:
            print(f"[BrowserManager] shutdown 投递失败: {e}")
        finally:
            try:
                self._bt.stop()
            except Exception:
                pass
            self.is_shutting_down = False

    def run_in_browser_thread(self, fn, *args, block=True, timeout=None, **kwargs):
        return self._bt.submit(fn, *args, block=block, timeout=timeout, **kwargs)

    def ensure_started(self, minimized=False):
        try:
            self._bt.submit(self._ensure_started_impl, minimized)
        except Exception as e:
            print(f"[BrowserManager] ensure_started 失败: {e}")

    def new_page(self, url=None, force_new=False, activate=True):
        return self._bt.submit(self._new_page_impl, url, force_new, activate)

    # ================= 核心接管逻辑 =================


    def _ensure_started_impl(self, minimized=False):
        # ↓↓↓ 入口：身份快照 ↓↓↓
        print(f"[BM._ensure] enter thread={threading.get_ident()} bt_tid={self._bt._tid} "
              f"pw={id(self._pw) if self._pw else None} pw_tid={self._pw_thread_id} "
              f"browser={id(self._browser) if self._browser else None} browser_tid={self._browser_thread_id} "
              f"ctx={id(self._context) if self._context else None} ctx_tid={self._context_thread_id}")
        # ↑↑↑

        if self._browser:
            try:
                if not self._browser.is_connected():
                    print("⚠️ [BrowserManager] 发现 Playwright CDP 连接已断开，准备重连...")
                    self._browser = None
                    self._context = None
                    self._picker_injected = False
                    # ↓↓↓ 新增 ↓↓↓
                    self._browser_thread_id = None
                    self._context_thread_id = None
                    # ↑↑↑
            except Exception:
                self._browser = None
                self._context = None
                self._picker_injected = False
                self._browser_thread_id = None
                self._context_thread_id = None

        if self._browser and self._browser.is_connected():
            # ↓↓↓ 新增：这里是最容易跨线程返回的漏洞点 ↓↓↓
            print(f"[BM._ensure] 快速返回(已连接) thread={threading.get_ident()} "
                  f"browser_tid={self._browser_thread_id} ctx_tid={self._context_thread_id} "
                  f"⚠️线程一致={threading.get_ident() == self._context_thread_id}")
            # ↑↑↑
            return

        try:
            from plugin_bridge import ensure_ready, restart_extension_browser
            if not ensure_ready(business_name="投诉接管"):
                raise RuntimeError("无法通过 plugin_bridge 启动专用浏览器")
        except ImportError:
            raise RuntimeError("未找到 plugin_bridge 模块")

        print("🔗 [BrowserManager] 正在通过 CDP 接管浏览器...")
        if not self._pw:
            self._pw = sync_playwright().start()
            # ↓↓↓ 新增：记录 _pw 出生线程 ↓↓↓
            self._pw_thread_id = threading.get_ident()
            print(f"[BM._ensure] 新建 _pw id={id(self._pw)} tid={self._pw_thread_id}")
            # ↑↑↑

        try:
            self._browser = self._pw.chromium.connect_over_cdp(
                f"http://127.0.0.1:{CDP_PORT}"
            )
            self._browser_thread_id = threading.get_ident()  # 新增
            self._context = self._browser.contexts[0]
            self._context_thread_id = threading.get_ident()  # 新增

            if not self._picker_injected:
                self._context.add_init_script(self._get_stealth_js())
                self._picker_injected = True

            print(f"✅ [BrowserManager] 成功接管插件浏览器！"
                  f"browser_tid={self._browser_thread_id} ctx_tid={self._context_thread_id} "
                  f"thread={threading.get_ident()}")

        except Exception as e:
            print(f"❌ [BrowserManager] CDP 接管失败，尝试强制重启浏览器... ({e})")

            # ★ R3：先彻底清空本地的旧 Playwright 状态
            try:
                if self._pw:
                    self._pw.stop()
            except Exception:
                pass
            self._browser = None
            self._context = None
            self._pw = None
            self._pw_thread_id = None
            self._browser_thread_id = None
            self._context_thread_id = None
            self._picker_injected = False

            from plugin_bridge import restart_extension_browser
            restart_extension_browser(business_name="投诉接管重试")

            try:
                # ★ R3：重启后必须重新 start playwright，不能复用旧的 _pw
                self._pw = sync_playwright().start()
                self._pw_thread_id = threading.get_ident()
                self._browser = self._pw.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{CDP_PORT}"
                )
                self._browser_thread_id = threading.get_ident()
                self._context = self._browser.contexts[0]
                self._context_thread_id = threading.get_ident()
                if not self._picker_injected:
                    self._context.add_init_script(self._get_stealth_js())
                    self._picker_injected = True
                print("✅ [BrowserManager] 重启后成功接管浏览器！")
            except Exception as retry_e:
                print(f"❌ [BrowserManager] 重启后仍然接管失败: {retry_e}")
                try:
                    if self._pw:
                        self._pw.stop()
                except Exception:
                    pass
                self._pw = None
                self._pw_thread_id = None
                raise

    def _patch_page(self, page):
        try:
            # ★ 已打过补丁就跳过
            if getattr(page, "_bm_patched", False):
                return
            try:
                page.on("dialog", lambda dialog: dialog.dismiss())
            except Exception:
                pass
            try:
                page.add_init_script("""
                    window.open = function() { return null; };
                    window.alert = function() { return; };
                    window.confirm = function() { return false; };
                    window.prompt = function() { return null; };
                """)
            except Exception:
                pass
            try:
                page._bm_patched = True
            except Exception:
                pass
        except Exception as e:
            print(f"⚠️ [BM] 注入防弹窗脚本失败: {e}")

    def _safe_new_page(self):
        print(f"[BM._safe_new_page] enter thread={threading.get_ident()} "
              f"bt_tid={self._bt._tid} ctx_tid={self._context_thread_id} "
              f"⚠️线程一致={threading.get_ident() == self._context_thread_id}")
        try:
            page = self._context.new_page()
            self._patch_page(page)  # ★ 新增：生成后立刻打补丁
            return page
        except Exception as e:
            err_msg = str(e)
            print(f"[BM._safe_new_page] 异常: {err_msg}")
            if ("Target.createTarget" in err_msg
                    or "Target closed" in err_msg
                    or "Connection closed" in err_msg):
                print(f"[BrowserManager] 浏览器连接已断开，正在尝试重连... ({err_msg})")
                try:
                    if self._pw:
                        self._pw.stop()
                except Exception:
                    pass
                self._browser = None
                self._context = None
                self._pw = None
                self._pw_thread_id = None
                self._browser_thread_id = None
                self._context_thread_id = None
                self._picker_injected = False
                self._ensure_started_impl(minimized=False)

                page = self._context.new_page()
                self._patch_page(page)  # ★ 新增：重连后也打补丁
                return page
            raise

    def _new_page_impl(self, url=None, force_new=False, activate=True):
        self._ensure_started_impl(minimized=False)
        page = None
        if not force_new:
            for p in self._context.pages:
                try:
                    if p.url == "about:blank":
                        page = p
                        break
                except Exception:
                    pass
        if page is None:
            page = self._safe_new_page()
        else:
            self._patch_page(page)
        if activate:
            self._force_activate(page)
        if url:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception as e:
                print(f"页面加载超时: {e}")
        return page

    def close_page(self, page):
        """线程安全地关闭指定页；已关闭/已失效则静默返回"""
        return self._bt.submit(self._close_page_impl, page)

    def _close_page_impl(self, page):
        if not page:
            return
        try:
            if not page.is_closed():
                page.close()
        except Exception as e:
            print(f"[BM.close_page] 关闭失败: {e}")

    def close_pages(self, pages):
        """批量关闭（一次投递，减少线程切换）"""
        return self._bt.submit(self._close_pages_impl, list(pages or []))

    def _close_pages_impl(self, pages):
        if not pages:
            return
        # 先过滤掉已关闭的
        alive = [p for p in pages if p and not self._is_page_closed(p)]
        if not alive:
            return

        # context 现有存活页面
        current = [p for p in self._context.pages if not self._is_page_closed(p)]
        # 如果关闭后一个都不剩 → 保留一个根页面，改为 goto about:blank
        if len(current) - len([p for p in alive if p in current]) <= 0:
            keeper = alive[0]
            others = alive[1:]
            try:
                keeper.goto("about:blank", timeout=5000)
            except Exception as e:
                print(f"[BM.close_pages] 根页面 reset 失败: {e}")
            for p in others:
                self._close_page_impl(p)
            print("[BM.close_pages] 已保留一个根页面（goto about:blank），避免关闭窗口")
            return

        # 正常关闭
        for p in alive:
            self._close_page_impl(p)

    @staticmethod
    def _is_page_closed(p):
        try:
            return p.is_closed()
        except Exception:
            return True

    def _force_activate(self, page, os_level=False):
        """Playwright 层激活（轻量，无副作用）"""
        try:
            page.bring_to_front()
        except Exception:
            pass
        try:
            cdp = self._context.new_cdp_session(page)
            try:
                cdp.send("Page.bringToFront")
            finally:
                cdp.detach()
        except Exception:
            pass
        try:
            page.evaluate("window.focus()")
        except Exception:
            pass

        if not os_level:
            return

        # ↓↓↓ OS 级（只在显式要求时执行）↓↓↓
        try:
            import sys
            if sys.platform == "win32":
                import ctypes
                # 经典 Alt 键释放前台锁，然后找 Chrome/Edge 窗口提到最前
                ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt down
                ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # Alt up
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                # 前台已经是浏览器就不折腾
                buf = ctypes.create_unicode_buffer(512)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
                if not any(k in buf.value for k in ("Chrome", "Edge", "Chromium")):
                    # 枚举窗口找浏览器
                    from ctypes import wintypes
                    found = []

                    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                    def _cb(h, _):
                        if not ctypes.windll.user32.IsWindowVisible(h):
                            return True
                        l = ctypes.windll.user32.GetWindowTextLengthW(h)
                        if l == 0:
                            return True
                        b = ctypes.create_unicode_buffer(l + 1)
                        ctypes.windll.user32.GetWindowTextW(h, b, l + 1)
                        if any(k in b.value for k in ("Chrome", "Edge", "Chromium")):
                            found.append(h)
                        return True

                    ctypes.windll.user32.EnumWindows(_cb, 0)
                    for h in found:
                        ctypes.windll.user32.ShowWindow(h, 9)  # SW_RESTORE
                        ctypes.windll.user32.SetForegroundWindow(h)
                        break
        except Exception:
            pass

    def _get_stealth_js(self):
        return """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin' },
                    { name: 'Chrome PDF Viewer' },
                    { name: 'Native Client' }
                ]
            });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel(R) UHD Graphics 620';
                return getParameter.apply(this, arguments);
            };
        """

    def _shutdown_impl(self):
        """只断开 Playwright 的 CDP 连接，绝对不杀浏览器"""
        try:
            if self._browser:
                try:
                    self._browser.close()
                except Exception:
                    pass
        finally:
            try:
                if self._pw:
                    self._pw.stop()
            except Exception:
                pass

            self._browser = None
            self._context = None
            self._pw = None
            self._picker_injected = False
            print("🔌 [BrowserManager] 已断开 CDP 连接，浏览器进程予以保留。")

    def open_isolated_mobile_page(self):
        """新建隔离 context + 完整移动端模拟，供 4/5/6 调用。

        - 只能在 BrowserThread 内调用（调用方通常已通过 run_in_browser_thread 投递）
        - 返回 (page, cleanup)。cleanup() 关闭整个 context，连带关闭 page。
        - 不调 _patch_page、不调 _force_activate，和 link_resolver 现有行为完全一致
        - 不碰 context0，不影响搜索/投诉/桌面端
        """
        return self._bt.submit(self._open_isolated_mobile_page_impl)

    def _open_isolated_mobile_page_impl(self):
        self._ensure_started_impl(minimized=False)
        ctx = self._browser.new_context(**_MOBILE_CTX_DEFAULTS)
        try:
            page = ctx.new_page()
        except Exception:
            # ★ 防泄漏：new_page 失败时立刻关 context
            try:
                ctx.close()
            except Exception:
                pass
            raise

        def cleanup():
            try:
                ctx.close()
            except Exception as e:
                print(f"[BM.open_isolated_mobile_page] 关闭 context 失败: {e}")

        return page, cleanup

    def bring_to_front(self, page=None):
        """
        把浏览器窗口/标签页抢到前台（线程安全，可在任意线程调用）。
        - page 为 None 时，激活 context 下所有页面（择一即激活窗口）
        - 失败不抛异常，仅打印日志
        """
        try:
            return self._bt.submit(self._bring_to_front_impl, page)
        except Exception as e:
            print(f"[BM.bring_to_front] 投递失败: {e}")
            return None

    def _bring_to_front_impl(self, page=None):
        try:
            self._ensure_started_impl(minimized=False)
        except Exception:
            return
        if self._context is None:
            return

        # ★ 只激活一个页面就够，不要循环
        target = None
        if page is not None:
            if page and not self._is_page_closed(page):
                target = page
        else:
            for p in self._context.pages:
                if not self._is_page_closed(p):
                    target = p
                    break

        if target:
            self._force_activate(target, os_level=True)  # ★ 只有显式 bring_to_front 才打 OS