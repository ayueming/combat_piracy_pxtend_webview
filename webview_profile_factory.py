# webview_profile_factory.py
import os

from PySide6.QtWebEngineCore import QWebEngineProfile

from app_paths import get_user_dir
from db_config import get_current_user
from app_data.mobile_consts import DESKTOP_UA, MOBILE_UA

# =========================================================
# ★ 核心修复：全局字典保活
# 1. 防止 Profile 被 Python 的垃圾回收（GC）提前释放
# 2. 切断与弹窗 UI (web_view) 的父子关系，防止关弹窗时底层强杀 Profile 导致 0xC0000005 崩溃
# =========================================================
_GLOBAL_PROFILES = {}


def create_preview_profile(parent, is_mobile):
    """focus 预览与 spider 双击预览共用的持久化 profile。
    不同时打开两个预览窗口，同名同路径不会冲突。
    """
    user_id = get_current_user()
    base_dir = get_user_dir(user_id) if user_id else os.path.expanduser("~")
    profile_dir = os.path.join(base_dir, "webview_profile")
    os.makedirs(profile_dir, exist_ok=True)

    # 用 user_id 区分不同账号的 Profile
    profile_name = f"preview_profile_{user_id}"

    # 如果当前账号的 Profile 已经创建过了，直接复用
    if profile_name in _GLOBAL_PROFILES:
        profile = _GLOBAL_PROFILES[profile_name]
        # 每次调用时，动态更新一下 UA 模式（桌面/移动）
        profile.setHttpUserAgent(MOBILE_UA if is_mobile else DESKTOP_UA)
        return profile

    # ★ 关键点：parent 传 None
    # 绝对不要把它挂载到 web_view 下面。让它的生命周期完全由 _GLOBAL_PROFILES 掌控。
    profile = QWebEngineProfile(profile_name, None)

    profile.setPersistentStoragePath(os.path.join(profile_dir, "storage"))
    profile.setCachePath(os.path.join(profile_dir, "cache"))
    profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
    profile.setHttpUserAgent(MOBILE_UA if is_mobile else DESKTOP_UA)

    # 存入全局字典保活，直到软件彻底退出才释放
    _GLOBAL_PROFILES[profile_name] = profile

    print(f"[Profile] 已初始化持久化上下文: {profile_name}")
    return profile