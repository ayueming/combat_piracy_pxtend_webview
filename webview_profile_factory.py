# webview_profile_factory.py
import os

from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript

from app_paths import get_user_dir
from db_config import get_current_user
from app_data.mobile_consts import DESKTOP_UA, MOBILE_UA

# =========================================================
# 全局字典保活：防止 Profile 被 GC 提前释放
# =========================================================
_GLOBAL_PROFILES = {}

# ★ 与 mobile_consts._CR_VER 严格一致（实测引擎版本）
_CHROMIUM_VER = "140.0.0.0"
_CHROMIUM_MAJOR = "140"


def _build_stealth_js() -> str:
    """构造 stealth 脚本，版本号注入后返回 JS 字符串"""
    js = r"""
(() => {
    // ---------- 1. webdriver ----------
    try {
        Object.defineProperty(navigator, 'webdriver', { get: () => false, configurable: true });
    } catch (e) {}

    // ---------- 2. window.chrome（百度系必查） ----------
    try {
        if (!window.chrome) window.chrome = {};
        if (!window.chrome.runtime) window.chrome.runtime = {};
        if (!window.chrome.loadTimes) window.chrome.loadTimes = function(){ return {}; };
        if (!window.chrome.csi)       window.chrome.csi       = function(){ return {}; };
        if (!window.chrome.app)       window.chrome.app       = { isInstalled: false };
    } catch (e) {}

    // ---------- 3. userAgentData（仅当存在时 patch，移动 UA 下自然跳过） ----------
    try {
        if (navigator.userAgentData) {
            const brands = [
                { brand: "Not=A?Brand",   version: "99" },
                { brand: "Chromium",      version: "__MAJOR__" },
                { brand: "Google Chrome", version: "__MAJOR__" }
            ];
            const fullVersionList = [
                { brand: "Not=A?Brand",   version: "99.0.0.0" },
                { brand: "Chromium",      version: "__VER__" },
                { brand: "Google Chrome", version: "__VER__" }
            ];

            Object.defineProperty(navigator.userAgentData, 'brands', {
                get: () => brands, configurable: true
            });

            const origGHEV = navigator.userAgentData.getHighEntropyValues.bind(navigator.userAgentData);
            navigator.userAgentData.getHighEntropyValues = async (hints) => {
                const real = await origGHEV(hints);
                real.brands = brands;
                real.fullVersionList = fullVersionList;
                real.uaFullVersion = "__VER__";
                real.platform = "Windows";
                real.platformVersion = "10.0.0";
                real.architecture = "x86";
                real.bitness = "64";
                real.model = "";
                real.mobile = false;
                real.wow64 = false;
                return real;
            };
        }
    } catch (e) {}

    // ---------- 4. plugins / mimeTypes ----------
    try {
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: "PDF Viewer",              filename: "internal-pdf-viewer" },
                { name: "Chrome PDF Viewer",       filename: "internal-pdf-viewer" },
                { name: "Chromium PDF Viewer",     filename: "internal-pdf-viewer" },
                { name: "Microsoft Edge PDF Viewer", filename: "internal-pdf-viewer" },
                { name: "WebKit built-in PDF",     filename: "internal-pdf-viewer" }
            ],
            configurable: true
        });
        Object.defineProperty(navigator, 'mimeTypes', {
            get: () => [{ type: "application/pdf" }, { type: "text/pdf" }],
            configurable: true
        });
    } catch (e) {}

    // ---------- 5. languages ----------
    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en'],
            configurable: true
        });
    } catch (e) {}

    // ---------- 6. WebGL renderer（仅在走 SwiftShader 时兜底，见下方说明） ----------
    try {
        const patchGL = (proto) => {
            const orig = proto.getParameter;
            proto.getParameter = function(p) {
                const r = orig.apply(this, arguments);
                if (p === 0x9245) return 'Google Inc. (Intel)';
                if (p === 0x9246) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)';
                return r;
            };
        };
        if (window.WebGLRenderingContext)  patchGL(WebGLRenderingContext.prototype);
        if (window.WebGL2RenderingContext) patchGL(WebGL2RenderingContext.prototype);
    } catch (e) {}
})();
"""
    return (js.replace("__MAJOR__", _CHROMIUM_MAJOR)
              .replace("__VER__", _CHROMIUM_VER))


def _install_stealth(profile: QWebEngineProfile):
    """在 profile 上挂 stealth 脚本，DocumentCreation 时机、MainWorld 世界"""
    # 兼容不同 PySide6 小版本的枚举名
    def _enum(cls, *names):
        for n in names:
            if hasattr(cls, n):
                return getattr(cls, n)
        raise AttributeError(f"找不到枚举：{names}")

    script = QWebEngineScript()
    script.setName("stealth_patch")
    script.setSourceCode(_build_stealth_js())
    script.setInjectionPoint(_enum(QWebEngineScript, "DocumentCreation",
                                   "InjectionPoint.DocumentCreation"))
    script.setWorldId(_enum(QWebEngineScript, "MainWorld",
                            "ScriptWorldId.MainWorld"))
    script.setRunsOnSubFrames(True)
    profile.scripts().insert(script)


def create_preview_profile(parent, is_mobile):
    user_id = get_current_user()
    base_dir = get_user_dir(user_id) if user_id else os.path.expanduser("~")
    profile_dir = os.path.join(base_dir, "webview_profile")
    os.makedirs(profile_dir, exist_ok=True)

    profile_name = f"preview_profile_{user_id}"

    if profile_name in _GLOBAL_PROFILES:
        profile = _GLOBAL_PROFILES[profile_name]
        # 动态切换桌面/移动 UA
        profile.setHttpUserAgent(MOBILE_UA if is_mobile else DESKTOP_UA)
        return profile

    profile = QWebEngineProfile(profile_name, None)

    profile.setPersistentStoragePath(os.path.join(profile_dir, "storage"))
    profile.setCachePath(os.path.join(profile_dir, "cache"))
    profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
    profile.setHttpUserAgent(MOBILE_UA if is_mobile else DESKTOP_UA)

    # ★ 只在首次创建时挂 stealth 脚本（避免重复插入）
    _install_stealth(profile)

    _GLOBAL_PROFILES[profile_name] = profile
    print(f"[Profile] 已初始化持久化上下文: {profile_name}")
    return profile