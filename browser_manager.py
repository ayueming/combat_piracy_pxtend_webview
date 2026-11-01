# browser_manager.py
import os
import time
import threading
import atexit

from playwright.sync_api import sync_playwright

from app_paths import get_user_dir, DIR_BROWSER_PROFILE
from db_config import get_current_user
from browser_thread import BrowserThread


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
                    cls._instance = cls(profile_dir)
                    atexit.register(cls._instance.shutdown)
        return cls._instance

    def __init__(self, profile_dir):
        self.profile_dir = profile_dir

        self._bt = BrowserThread()
        self._bt.start()

        self._pw = None
        self._browser = None
        self._context = None

        self._shutdown_lock = threading.Lock()
        self.is_shutting_down = False
        self._picker_injected = False

    def reset_playwright(self):
        """强制重置底层的 Playwright 连接"""
        print("⚠️ [BrowserManager] 检测到连接异常，正在重置底层状态...")
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
        self._picker_injected = False

    def run_in_browser_thread(self, fn, *args, block=True, timeout=None, **kwargs):
        return self._bt.submit(fn, *args, block=block, timeout=timeout, **kwargs)

    def ensure_started(self, minimized=False):
        try:
            self._bt.submit(self._ensure_started_impl, minimized)
        except Exception as e:
            print(f"[BrowserManager] ensure_started 失败: {e}")

    def new_page(self, url=None, force_new=False):
        return self._bt.submit(self._new_page_impl, url, force_new)

    def shutdown(self, force=False):
        with self._shutdown_lock:
            if self.is_shutting_down:
                return
            self.is_shutting_down = True

        try:
            if self._bt.is_alive():
                # 只断开 Playwright 的连接，不杀浏览器进程（留给 plugin_bridge 管）
                self._bt.submit(self._shutdown_impl, block=True, timeout=20)
        except Exception as e:
            print(f"[BrowserManager] shutdown 投递失败: {e}")
        finally:
            try:
                self._bt.stop()
            except Exception:
                pass
            self.is_shutting_down = False

    # ================= 核心接管逻辑 =================

    def _ensure_started_impl(self, minimized=False):
        if self._browser:
            try:
                if not self._browser.is_connected():
                    print("⚠️ [BrowserManager] 发现 Playwright CDP 连接已断开，准备重连...")
                    self._browser = None
                    self._context = None
                    self._picker_injected = False
            except Exception:
                self._browser = None
                self._context = None
                self._picker_injected = False

        if self._browser and self._browser.is_connected():
            return

        try:
            from plugin_bridge import ensure_ready, restart_extension_browser
            if not ensure_ready(business_name="投诉接管", timeout=8.0):
                raise RuntimeError("无法通过 plugin_bridge 启动专用浏览器")
        except ImportError:
            raise RuntimeError("未找到 plugin_bridge 模块")

        print("🔗 [BrowserManager] 正在通过 CDP 接管浏览器...")
        if not self._pw:
            self._pw = sync_playwright().start()

        try:
            self._browser = self._pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
            self._context = self._browser.contexts[0]

            if not self._picker_injected:
                self._context.add_init_script(self._get_stealth_js())
                self._picker_injected = True

            print("✅ [BrowserManager] 成功接管插件浏览器！")
        except Exception as e:
            print(f"❌ [BrowserManager] CDP 接管失败，尝试强制重启浏览器... ({e})")
            from plugin_bridge import restart_extension_browser
            restart_extension_browser(business_name="投诉接管重试")

            try:
                self._browser = self._pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
                self._context = self._browser.contexts[0]
                if not self._picker_injected:
                    self._context.add_init_script(self._get_stealth_js())
                    self._picker_injected = True
                print("✅ [BrowserManager] 重启后成功接管浏览器！")
            except Exception as retry_e:
                print(f"❌ [BrowserManager] 重启后仍然接管失败: {retry_e}")
                self._pw.stop()
                self._pw = None
                raise

    def _safe_new_page(self):
        try:
            return self._context.new_page()
        except Exception as e:
            err_msg = str(e)
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
                self._picker_injected = False
                self._ensure_started_impl(minimized=False)
                return self._context.new_page()
            raise

    def _new_page_impl(self, url=None, force_new=False):
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

        self._force_activate(page)
        if url:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception as e:
                print(f"页面加载超时: {e}")
        return page

    def _force_activate(self, page):
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