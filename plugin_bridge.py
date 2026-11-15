# plugin_bridge.py
import asyncio
import json
import threading
import subprocess
import os
import shutil
import psutil
import socket
import sys

try:
    import winreg
    import win32gui
    import win32process
    import win32con
except ImportError:
    winreg = None

from PySide6.QtCore import QObject, Signal

from app_data.browser_consts import CDP_PORT, CDP_READY_TIMEOUT


class PluginBridgeSignals(QObject):
    plugin_timeout = Signal(str)
    plugin_connected = Signal()
    plugin_disconnected = Signal()


bridge_signals = PluginBridgeSignals()


class PluginBridge:
    def __init__(self):
        self._ws_list = []  # ★ 改用列表存多个连接
        self._loop = None
        self._connected_event = threading.Event()
        self._lock = threading.Lock()

    def set_websocket(self, ws):
        with self._lock:
            if ws not in self._ws_list:
                self._ws_list.append(ws)
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None
            self._connected_event.set()
        print(f"🔌 插件连接已建立 (当前在线页面: {len(self._ws_list)})")
        bridge_signals.plugin_connected.emit()

    def clear_websocket(self, ws=None):
        with self._lock:
            if ws is None:
                # ★ R1：无参 = 强制清空全部（restart / shutdown / 用户切换专用）
                self._ws_list.clear()
            elif ws in self._ws_list:
                self._ws_list.remove(ws)
            if not self._ws_list:
                self._connected_event.clear()
        print(f"🔌 插件连接已断开 (剩余在线页面: {len(self._ws_list)})")
        if not self._ws_list:
            bridge_signals.plugin_disconnected.emit()

    def is_connected(self):
        with self._lock:
            return len(self._ws_list) > 0

    def wait_connected(self, timeout=8.0):
        return self._connected_event.wait(timeout)

    def send_sync(self, payload):
        with self._lock:
            ws_list = list(self._ws_list)
            loop = self._loop
        if not ws_list or not loop:
            return False

        text_data = json.dumps(payload, ensure_ascii=False)
        success = False
        for ws in ws_list:
            try:
                asyncio.run_coroutine_threadsafe(ws.send(text_data), loop)
                success = True
            except Exception as e:
                print(f"[Bridge] 发送失败: {e}")
        return success


bridge = PluginBridge()

# ===================== 专用浏览器管理 =====================
EXTENSION_DEV_MODE = True

_browser_proc = None
_browser_proc_user_id = None
_browser_lock = threading.Lock()
_ready_lock = threading.Lock()


def _browser_identity(exe_path):
    low = exe_path.lower()
    name = os.path.basename(low)
    if "playwright" in low:
        return "chromium"
    if "msedge" in name:
        return "edge"
    if "chromium" in name:
        return "chromium"
    if "chrome" in name:
        return "chrome"
    import hashlib
    return "custom_" + hashlib.md5(low.encode()).hexdigest()[:8]


def _get_extension_profile_dir(user_id, exe_path):
    from app_paths import get_user_dir
    if not user_id:
        raise RuntimeError("未登录，拒绝启动专用浏览器")
    ident = _browser_identity(exe_path)
    d = os.path.join(get_user_dir(user_id), f"extension_profile_{ident}")
    os.makedirs(d, exist_ok=True)
    return d


def _force_kill_browser_by_profile(profile_dir):
    if not profile_dir or not os.path.exists(profile_dir):
        return
    target_profile = profile_dir.lower()
    killed = False
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = proc.info.get('name', '').lower()
            if 'msedge' in name or 'chrome' in name:
                cmdline = proc.cmdline()                      # ★ 只在 name 命中后才读 cmdline
                if cmdline and target_profile in " ".join(cmdline).lower():
                    proc.kill()
                    killed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if killed:
        print("[Bridge] 发现残留的旧浏览器进程，已强制物理超度！")
        import time
        time.sleep(0.5)


def _cleanup_lock_files(profile_dir):
    from app_paths import BROWSER_LOCK_FILES
    for fname in BROWSER_LOCK_FILES:
        p = os.path.join(profile_dir, fname)
        try:
            if os.path.islink(p) or os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


# =====================================================================
# ★ 核心修改：智能查找插件路径
# =====================================================================
def _get_plugin_dir():
    """兼容开发环境、PyInstaller打包(_internal)和Inno Setup安装(同级目录)"""
    if getattr(sys, 'frozen', False):
        # 1. 优先找 exe 同级目录 (对应 Inno Setup 单独复制插件)
        d = os.path.join(os.path.dirname(sys.executable), "my_extension")
        if os.path.exists(d) and os.path.exists(os.path.join(d, "manifest.json")):
            return d
        # 2. 如果没找到，去 PyInstaller 打包的内部目录里找 (对应 .spec 打包)
        if hasattr(sys, '_MEIPASS'):
            d = os.path.join(sys._MEIPASS, "my_extension")
            if os.path.exists(d) and os.path.exists(os.path.join(d, "manifest.json")):
                return d
    else:
        # 开发环境
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_extension")
        if os.path.exists(d):
            return d
    return ""


def _get_wakeup_url():
    plugin_dir = _get_plugin_dir()
    if not plugin_dir:
        return ""
    wakeup_file = os.path.join(plugin_dir, "wakeup.html")
    return f"file:///{wakeup_file.replace(os.sep, '/')}"


def _build_browser_cmd(exe, wakeup_url, profile_dir):
    cmd = [
        exe,
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        # ===== Safe Browsing 全关 =====
        "--disable-features=SafeBrowsing,"
                           "ClientSideDetection,"
                           "ClientSidePhishingDetection,"
                           "SafeBrowsingEnhancedProtection,"
                           "SafeBrowsingRealTimeUrlLookup,"
                           "InsecureDownloadWarnings,"
                           "HttpsUpgrades,"
                           "HttpsFirstBalancedModeAutoEnable,"
                           "HttpsFirstModeV2,"
                           "HttpsFirstModeIncognito",
        "--safebrowsing-disable-auto-update",
        "--safebrowsing-disable-download-protection",
        "--disable-client-side-phishing-detection",
        "--disable-component-update",
        "--disable-background-networking",
        "--disable-domain-reliability",
        "--no-pings",
        # ===== 证书 / 混合内容（盗文站常见自签/过期证书）=====
        "--ignore-certificate-errors",
        "--allow-running-insecure-content",
        "--allow-insecure-localhost",
    ]
    # 下面保持你原来的扩展加载逻辑
    if EXTENSION_DEV_MODE:
        plugin_dir = _get_plugin_dir()
        if plugin_dir:
            cmd.append(f"--load-extension={plugin_dir}")
            cmd.append(f"--disable-extensions-except={plugin_dir}")
    if wakeup_url:
        cmd.append(wakeup_url)
    return cmd


def get_browser_path():
    edge_in_path = shutil.which("msedge.exe")
    if edge_in_path: return edge_in_path
    chrome_in_path = shutil.which("chrome.exe")
    if chrome_in_path: return chrome_in_path
    paths = [
        os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("LocalAppData", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LocalAppData", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for p in paths:
        if p and os.path.exists(p): return p
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe")
        path, _ = winreg.QueryValueEx(key, "")
        if os.path.exists(path): return path
    except Exception:
        pass
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
        path, _ = winreg.QueryValueEx(key, "")
        if os.path.exists(path): return path
    except Exception:
        pass
    return None


def _is_cdp_alive():
    """探测 CDP 端口是否真的在监听（WS 通不等于 CDP 通）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        ok = s.connect_ex(("127.0.0.1", CDP_PORT)) == 0
        s.close()
        return ok
    except Exception:
        return False


def ensure_ready(business_name="业务", timeout=CDP_READY_TIMEOUT):
    import time as _time

    # =========================================================
    # ★ 第一阶段：物理探测优先。不要被内存里已经死掉的 WS 骗了
    # =========================================================
    if _is_cdp_alive():
        return True

    # 如果 9333 还没通，给个 1.5 秒的宽限期（防止刚启动正在监听中）
    deadline = _time.time() + 1.5
    while _time.time() < deadline:
        if _is_cdp_alive():
            return True
        _time.sleep(0.1)

    # 如果宽限期过了还不通，说明此时绝对没有可用的 CDP 浏览器
    # 如果此时 bridge 里还有连接，绝对是僵尸连接，立刻清理掉，免得干扰后续判断
    if bridge.is_connected():
        print(f"[Bridge] 发现陈旧的 WS 连接但 CDP 未监听，主动清理僵尸状态...")
        bridge.clear_websocket()

    # =========================================================
    # ★ 第二阶段：抢锁并启动
    # =========================================================
    if not _ready_lock.acquire(blocking=False):
        print(f"[Bridge] 已有线程在等待连接，跳过本次 {business_name} 的等待")
        # 稍微等一下，让正在启动的线程把浏览器拉起来，然后再探一次端口
        _time.sleep(1.0)
        return _is_cdp_alive()

    try:
        from db_config import get_current_user
        user_id = get_current_user()
        if not user_id:
            print(f"❌ [Bridge] 未登录，拒绝为 {business_name} 启动浏览器")
            bridge_signals.plugin_timeout.emit(business_name)
            return False

        exe = get_browser_path()
        if not exe:
            print("❌ 未找到 Edge 或 Chrome，无法启动浏览器")
            bridge_signals.plugin_timeout.emit(business_name)
            return False

        wakeup_url = _get_wakeup_url()
        profile_dir = _get_extension_profile_dir(user_id, exe)

        global _browser_proc, _browser_proc_user_id
        with _browser_lock:
            need_launch = _browser_proc is None or _browser_proc.poll() is not None

            if not need_launch and _browser_proc_user_id != user_id:
                print(f"[Bridge] 检测到用户切换 ({_browser_proc_user_id} -> {user_id})，正在重启浏览器...")
                try:
                    _browser_proc.kill()
                    _browser_proc.wait(timeout=3)
                except Exception:
                    pass
                _browser_proc = None
                _browser_proc_user_id = None
                bridge.clear_websocket()
                need_launch = True

            # 如果 Playwright 进程句柄还在，但 CDP 没开（比如被外部干扰了）
            if not need_launch:
                if not _is_cdp_alive():
                    print("[Bridge] 检测到现存浏览器未开启调试端口，准备重启以支持 CDP...")
                    _browser_proc = None
                    _browser_proc_user_id = None
                    need_launch = True

            if need_launch:
                _force_kill_browser_by_profile(profile_dir)
                _cleanup_lock_files(profile_dir)

                cmd = _build_browser_cmd(exe, wakeup_url, profile_dir)
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                try:
                    _browser_proc = subprocess.Popen(cmd, creationflags=creationflags)
                    _browser_proc_user_id = user_id
                    print(f"[Bridge] 启动专用浏览器 PID={_browser_proc.pid} profile={profile_dir}")
                except Exception as e:
                    print(f"[Bridge] 启动浏览器失败: {e}")
                    bridge_signals.plugin_timeout.emit(business_name)
                    return False
            else:
                print(f"[Bridge] 专用浏览器已在运行 PID={_browser_proc.pid}，等待扩展连接...")

        print(f"⏳ 等待插件连接（最长 {timeout} 秒）...")
        if not bridge.wait_connected(timeout):
            print(f"⚠️ {business_name}：插件 {timeout} 秒内未连接")
            bridge_signals.plugin_timeout.emit(business_name)
            return False

        # ★ R4：WS 连上了，再等 CDP 端口真正监听（最多 3 秒）
        deadline = _time.time() + 3.0
        while _time.time() < deadline:
            if _is_cdp_alive():
                print("✅ 插件已连接，CDP 端口已监听")
                return True
            _time.sleep(0.1)

        print(f"⚠️ {business_name}：插件已连，但 CDP 端口 {CDP_PORT} 3 秒内未监听")
        return False
    finally:
        _ready_lock.release()


def restart_extension_browser(business_name="调试重启", timeout=CDP_READY_TIMEOUT):
    global _browser_proc, _browser_proc_user_id
    with _browser_lock:
        if _browser_proc and _browser_proc.poll() is None:
            try:
                _browser_proc.kill()
                _browser_proc.wait(timeout=5)
            except Exception:
                pass
        _browser_proc = None
        _browser_proc_user_id = None
    bridge.clear_websocket()
    return ensure_ready(business_name=business_name, timeout=timeout)


def shutdown_extension_browser():
    global _browser_proc, _browser_proc_user_id
    with _browser_lock:
        if _browser_proc and _browser_proc.poll() is None:
            try:
                _browser_proc.kill()
                _browser_proc.wait(timeout=3)
            except Exception:
                pass
        _browser_proc = None
        _browser_proc_user_id = None
    bridge.clear_websocket()
    print("🔌 [Bridge] 专用浏览器已强制关闭")


def get_browser_hwnd():
    global _browser_proc
    target_pids = set()
    if _browser_proc and _browser_proc.poll() is None:
        try:
            parent = psutil.Process(_browser_proc.pid)
            target_pids.add(parent.pid)
            for child in parent.children(recursive=True):
                target_pids.add(child.pid)
        except Exception:
            pass

    target_profile = ""
    try:
        if _browser_proc and _browser_proc.args:
            for a in _browser_proc.args:
                if isinstance(a, str) and a.startswith("--user-data-dir="):
                    target_profile = a[len("--user-data-dir="):].lower()
                    break
    except Exception:
        pass

    if target_profile:
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    name = proc.info.get('name', '').lower()
                    if 'msedge' in name or 'chrome' in name:
                        cmdline = proc.info.get('cmdline')
                        if cmdline and target_profile in " ".join(cmdline).lower():
                            target_pids.add(proc.info['pid'])
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
        except Exception:
            pass

    if not target_pids: return None

    found_hwnds = []

    def enum_windows_callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            class_name = win32gui.GetClassName(hwnd)
            if "Chrome_WidgetWin" in class_name:
                _, process_id = win32process.GetWindowThreadProcessId(hwnd)
                if process_id in target_pids:
                    rect = win32gui.GetWindowRect(hwnd)
                    if rect[2] - rect[0] > 100 and rect[3] - rect[1] > 100:
                        found_hwnds.append((hwnd, rect))
        return True

    win32gui.EnumWindows(enum_windows_callback, None)
    if found_hwnds:
        found_hwnds.sort(key=lambda x: (x[1][2] - x[1][0]) * (x[1][3] - x[1][1]), reverse=True)
        return found_hwnds[0][0]
    return None