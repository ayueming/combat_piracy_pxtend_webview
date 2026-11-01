# focus_preview_window.py
import os
import json
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QApplication, QMessageBox
from PySide6.QtCore import Qt, QUrl, QObject, Slot, QFile, QIODevice
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
from PySide6.QtWebChannel import QWebChannel

from ws_server import set_app_mode, check_and_lock, current_context, ws_signals
from app_paths import get_user_dir
from db_config import get_current_user
from link_ingest_service import save_picked_link
from app_data import ALL_SUB_PLATFORMS


# ==========================================
# 1. 通信桥梁：接收网页 JS 发来的指令
# ==========================================
class JsBridge(QObject):
    def __init__(self, main_page):
        super().__init__()
        self.main_page = main_page

    @Slot(str, str, str, str, str)
    def manual_pick(self, url, title, body, book_name, category):
        print(f"📥 [WebView] 收到手动拾取: {title}")
        try:
            ctx = {
                "book_name": book_name or current_context.get("book_name", ""),
                "keyword": current_context.get("keyword", ""),
                "batch_id": current_context.get("batch_id", ""),
                "pick_origin": "手动获取",
                "source_platform": category or "WebView预览",
            }

            source_site = ""
            try:
                from urllib.parse import urlparse
                source_site = urlparse(url).netloc.replace("www.", "")
            except:
                pass

            record, is_dup, is_new = save_picked_link(
                url=url, title=title, ctx=ctx, body=body,
                source_site=source_site, is_manual=True, category=category
            )
            if is_new:
                ws_signals.new_data_inserted.emit(1)
                print(f"✅ [WebView] 拾取入库成功！")
            else:
                print(f"♻️ [WebView] 数据已存在，跳过。")
        except Exception as e:
            print(f"❌ [WebView] 拾取入库失败: {e}")

    @Slot(str, str, str, str, str)
    def save_page(self, url, title, body, book_name, category):
        print(f"📥 [WebView] 仅保存当前网址: {url}")
        self.manual_pick(url, title, body, book_name, category)

    @Slot(str)
    def save_batch(self, batch_id):
        print(f"💾 [WebView] 点击保存本批: {batch_id}")
        from parser_service import trigger_parse
        trigger_parse(batch_id or current_context.get("batch_id", ""), only_manual=True)


# ==========================================
# 2. 预览窗口 (基于 QWebEngineView)
# ==========================================
class BaseFocusWindow(QWidget):
    def __init__(self, main_page, workbench_widget, preview_url="", title="专注预览 (按 Esc 退出)"):
        super().__init__()
        self.main_page = main_page
        self.workbench_widget = workbench_widget
        self.preview_url = preview_url
        self._closed_handled = False

        self.setWindowTitle(title)
        self.setStyleSheet("QWidget { background-color: #F8FAFC; }")
        self.setFocusPolicy(Qt.StrongFocus)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---------- 左侧：WebView 容器 ----------
        web_container = QWidget()
        web_layout = QVBoxLayout(web_container)
        web_layout.setContentsMargins(0, 0, 0, 0)

        self.web_view = QWebEngineView()
        self._setup_persistent_profile()
        web_layout.addWidget(self.web_view)
        main_layout.addWidget(web_container, 65)

        # ---------- 右侧：外部传入的工作台 ----------
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(8)

        btn_close = QPushButton("🛑 退出分屏 (Esc)")
        btn_close.setStyleSheet(
            "QPushButton { border: 1px solid #FECACA; border-radius: 4px; background: #FEF2F2; color: #DC2626; font-size: 12px; font-weight: bold; padding: 6px; } "
            "QPushButton:hover { background: #FEE2E2; }")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.close)
        right_layout.addWidget(btn_close)

        if self.workbench_widget is not None:
            right_layout.addWidget(self.workbench_widget, 1)

        main_layout.addWidget(right_panel, 35)

        # 初始化通信桥梁
        self.channel = QWebChannel()
        self.bridge = JsBridge(main_page)
        self.channel.registerObject("pyBridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        # 监听页面加载完成，注入拾取 JS
        self.web_view.loadFinished.connect(self._on_load_finished)

    def _setup_persistent_profile(self):
        """配置持久化存储，保存登录状态和 Cookie"""
        user_id = get_current_user()
        base_dir = get_user_dir(user_id) if user_id else os.path.expanduser("~")
        profile_dir = os.path.join(base_dir, "webview_profile")
        os.makedirs(profile_dir, exist_ok=True)

        self.profile = QWebEngineProfile("preview_profile", self.web_view)
        self.profile.setPersistentStoragePath(os.path.join(profile_dir, "storage"))
        self.profile.setCachePath(os.path.join(profile_dir, "cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        self.profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

        page = QWebEnginePage(self.profile, self.web_view)
        self.web_view.setPage(page)

    def _on_load_finished(self, ok):
        if not ok: return

        # ★ 修复核心：先加载 QWebChannel 的底层库！
        file = QFile(":/qtwebchannel/qwebchannel.js")
        if file.open(QIODevice.ReadOnly):
            js_channel = bytes(file.readAll()).decode('utf-8')
            file.close()
            self.web_view.page().runJavaScript(js_channel)
        else:
            print("❌ 无法加载 qwebchannel.js")
            return

        # 收集当前上下文，准备传给 JS
        books = []
        if hasattr(self.main_page, "input_panel") and hasattr(self.main_page.input_panel, "books_data"):
            books = [b["name"] for b in self.main_page.input_panel.books_data.values()]

        ctx_data = {
            "book_name": current_context.get("book_name", ""),
            "keyword": current_context.get("keyword", ""),
            "batch_id": current_context.get("batch_id", ""),
            "books": books,
            "categories": list(ALL_SUB_PLATFORMS)
        }

        # 注入完整版拾取器
        js_code = self._get_full_picker_js(ctx_data)
        self.web_view.page().runJavaScript(js_code)

    def _get_full_picker_js(self, ctx_data):
        """返回给网页注入的完整拾取器脚本"""
        ctx_json = json.dumps(ctx_data, ensure_ascii=False)

        return f"""
        (function() {{
            if (window.__webview_picker_injected) return;
            window.__webview_picker_injected = true;

            // 现在 QWebChannel 已经加载好了，可以建立连接
            new QWebChannel(qt.webChannelTransport, function(channel) {{
                window.pyBridge = channel.objects.pyBridge;

                // 初始化 UI
                initPicker({ctx_json});
            }});

            function initPicker(ctx) {{
                const host = document.createElement("div");
                host.id = "__apt_picker_host__";
                host.style.cssText = "all: initial; position:fixed; right:20px; bottom:20px; z-index:2147483647; font-family:Microsoft YaHei,Arial,sans-serif;";

                const parent = document.body || document.documentElement;
                parent.appendChild(host);

                const shadow = host.attachShadow({{ mode: "open" }});

                const style = document.createElement("style");
                style.textContent = `
                    :host, * {{ box-sizing: border-box; }}
                    .panel {{ width: 240px; background: #ffffff; border: 1px solid #d0d7de; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,.12); font-size: 12px; color: #24292f; overflow: hidden; pointer-events: auto !important; }}
                    .head {{ background: #f6f8fa; padding: 8px 10px; font-weight: 600; border-bottom: 1px solid #eaeef2; display: flex; justify-content: space-between; align-items: center; cursor: move; user-select: none; }}
                    .ctx-area {{ padding: 8px 10px; border-bottom: 1px solid #eaeef2; display: flex; flex-direction: column; gap: 6px; }}
                    .ctx-row {{ display: flex; align-items: center; justify-content: space-between; }}
                    .ctx-row label {{ color: #57606a; font-weight: 600; }}
                    .ctx-row select {{ flex: 1; margin-left: 8px; padding: 2px 4px; border: 1px solid #d0d7de; border-radius: 4px; font-size: 12px; background: white; }}
                    .ctx-kw {{ color: #57606a; word-break: break-all; }}
                    .ctx-kw b {{ color: #24292f; }}
                    .actions {{ display: flex; flex-direction: column; gap: 6px; padding: 8px 10px; }}
                    button {{ padding: 6px 0; border-radius: 5px; cursor: pointer !important; font-size: 12px; font-weight: 600; border: 1px solid transparent; font-family: inherit; pointer-events: auto !important; }}
                    .btn-save-page {{ background: #f6f8fa; color: #24292f; border-color: #d0d7de; }}
                    .btn-save-page:hover {{ background: #eaeef2; }}
                    .btn-pick {{ background: #f6f8fa; color: #24292f; border-color: #d0d7de; }}
                    .btn-pick.active {{ background: #fff8c5; border-color: #d4a72c; color: #7d4e00; }}
                    .btn-save-batch {{ background: #10B981; color: #fff; margin-top: 4px; }}
                    .btn-save-batch:hover {{ background: #059669; }}
                    .status {{ padding: 6px 10px; background: #f6f8fa; color: #57606a; font-size: 11px; }}
                `;
                shadow.appendChild(style);

                const panel = document.createElement("div");
                panel.className = "panel";
                panel.innerHTML = `
                    <div class="head" id="apt-head"><span>反盗版助手 · 拾取</span></div>
                    <div class="ctx-area">
                        <div class="ctx-row"><label>作品:</label><select id="apt-book"></select></div>
                        <div class="ctx-row"><label>分类:</label><select id="apt-category"></select></div>
                        <div class="ctx-kw">关键词: <b id="apt-kw">-</b></div>
                    </div>
                    <div class="actions">
                        <button class="btn-save-page" id="btn-save-page">仅保存当前网址</button>
                        <button class="btn-pick" id="btn-pick">手动拾取链接（或按住ALT）</button>
                        <button class="btn-save-batch" id="btn-save-batch">💾 保存本批</button>
                    </div>
                    <div class="status">待保存 <b id="apt-count">0</b> 条</div>
                `;
                shadow.appendChild(panel);

                const mask = document.createElement("div");
                mask.style.cssText = "all: initial; position:fixed; top:0; left:0; width:100vw; height:100vh; z-index:2147483645; background:transparent; display:none; cursor:crosshair;";
                document.body.appendChild(mask);

                const overlay = document.createElement("div");
                overlay.style.cssText = "all: initial; position:fixed; pointer-events:none; border:2px solid #1f6feb; background:rgba(31,111,235,.12); border-radius:3px; display:none; z-index:2147483646; transition:all .08s ease;";
                document.body.appendChild(overlay);

                const selBook = shadow.getElementById("apt-book");
                const selCat = shadow.getElementById("apt-category");
                const kwEl = shadow.getElementById("apt-kw");
                kwEl.textContent = ctx.keyword || "-";

                ctx.books.forEach(name => {{
                    const opt = document.createElement("option");
                    opt.value = name; opt.textContent = name;
                    if (name === ctx.book_name) opt.selected = true;
                    selBook.appendChild(opt);
                }});

                ctx.categories.forEach(name => {{
                    const opt = document.createElement("option");
                    opt.value = name; opt.textContent = name;
                    selCat.appendChild(opt);
                }});

                let isDragging = false, dragStartX, dragStartY, initialLeft, initialTop;
                const head = shadow.getElementById("apt-head");
                head.addEventListener("mousedown", (e) => {{
                    isDragging = true;
                    dragStartX = e.clientX; dragStartY = e.clientY;
                    const rect = host.getBoundingClientRect();
                    initialLeft = rect.left; initialTop = rect.top;
                    host.style.left = initialLeft + "px"; host.style.top = initialTop + "px";
                    host.style.right = "auto"; host.style.bottom = "auto";
                    e.preventDefault();
                }});
                document.addEventListener("mousemove", (e) => {{
                    if (!isDragging) return;
                    host.style.left = (initialLeft + e.clientX - dragStartX) + "px";
                    host.style.top = (initialTop + e.clientY - dragStartY) + "px";
                }});
                document.addEventListener("mouseup", () => isDragging = false);

                let isPicking = false, hoverEl = null;
                const btnPick = shadow.getElementById("btn-pick");
                const btnSavePage = shadow.getElementById("btn-save-page");
                const btnSaveBatch = shadow.getElementById("btn-save-batch");
                const countEl = shadow.getElementById("apt-count");
                let savedCount = 0;

                function enterPicking() {{
                    isPicking = true; btnPick.classList.add("active");
                    btnPick.textContent = "🔴 拾取中（点击退出）";
                    mask.style.display = "block"; host.style.borderLeft = "4px solid #EF4444";
                }}
                function exitPicking() {{
                    isPicking = false; btnPick.classList.remove("active");
                    btnPick.textContent = "手动拾取链接（或按住ALT）";
                    mask.style.display = "none"; overlay.style.display = "none";
                    host.style.borderLeft = ""; hoverEl = null;
                }}

                btnPick.addEventListener("click", () => isPicking ? exitPicking() : enterPicking());

                document.addEventListener("keydown", (e) => {{
                    if (e.key === "Alt" && !e.repeat) {{
                        if (!isPicking) enterPicking();
                    }}
                }}, true);
                document.addEventListener("keyup", (e) => {{
                    if (e.key === "Alt") exitPicking();
                }}, true);

                mask.addEventListener("mousemove", (e) => {{
                    if (!isPicking) return;
                    mask.style.pointerEvents = "none";
                    const realTarget = document.elementFromPoint(e.clientX, e.clientY);
                    mask.style.pointerEvents = "auto";
                    if (!realTarget) {{ overlay.style.display = "none"; return; }}

                    if (realTarget !== hoverEl) {{
                        hoverEl = realTarget;
                        const r = hoverEl.getBoundingClientRect();
                        overlay.style.left = r.left + "px"; overlay.style.top = r.top + "px";
                        overlay.style.width = r.width + "px"; overlay.style.height = r.height + "px";
                        overlay.style.display = "block";
                    }}
                }});

                mask.addEventListener("mousedown", (e) => {{
                    if (!isPicking) return;
                    mask.style.pointerEvents = "none";
                    const realTarget = document.elementFromPoint(e.clientX, e.clientY);
                    mask.style.pointerEvents = "auto";
                    if (!realTarget) return;
                    e.preventDefault(); e.stopPropagation();

                    let finalUrl = "";
                    let curNode = realTarget;
                    while (curNode && curNode !== document.documentElement) {{
                        if (curNode.tagName === "A" && curNode.href) {{ finalUrl = curNode.href; break; }}
                        curNode = curNode.parentElement;
                    }}
                    let bodyText = (realTarget.innerText || realTarget.textContent || "").trim();
                    if (!finalUrl) {{
                        const match = bodyText.match(/https?:\\/\\/[^\\s]+/);
                        if (match) finalUrl = match[0];
                    }}
                    if (!finalUrl) return;

                    let titleText = document.title || "未命名网页";
                    if (bodyText.length > 0 && bodyText.length < 50) titleText = bodyText;

                    if (window.pyBridge) {{
                        window.pyBridge.manual_pick(finalUrl, titleText, bodyText, selBook.value, selCat.value);
                        savedCount++; countEl.textContent = savedCount;
                    }}
                }}, true);

                btnSavePage.addEventListener("click", () => {{
                    if (window.pyBridge) {{
                        window.pyBridge.save_page(window.location.href, document.title, document.body.innerText.substring(0, 500), selBook.value, selCat.value);
                        savedCount++; countEl.textContent = savedCount;
                    }}
                }});

                btnSaveBatch.addEventListener("click", () => {{
                    if (window.pyBridge) {{
                        window.pyBridge.save_batch(ctx.batch_id);
                        savedCount = 0; countEl.textContent = "0";
                    }}
                }});
            }}
        }})();
        """

    @classmethod
    def launch(cls, main_page, workbench_widget, preview_url, title="专注预览"):
        if not preview_url: return None
        if not check_and_lock("previewing"):
            QMessageBox.warning(main_page, "提示", "后台正在执行自动任务，请等待完成后再预览。")
            return None

        if getattr(main_page, 'focus_window', None):
            main_page.focus_window.close()

        win = cls(main_page, workbench_widget, preview_url, title)
        main_page.focus_window = win

        screen_rect = QApplication.primaryScreen().availableGeometry()
        win.setGeometry(screen_rect)
        win.show()
        win.setFocus()

        win.web_view.load(QUrl(preview_url))
        return win

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if self._closed_handled:
            event.accept()
            return
        self._closed_handled = True

        self.web_view.stop()
        self.web_view.setUrl(QUrl("about:blank"))

        if self.workbench_widget is not None:
            self.workbench_widget.setParent(None)

        try:
            self.main_page.window().show()
        except Exception:
            pass

        if hasattr(self.main_page, 'on_focus_window_closed'):
            try:
                self.main_page.on_focus_window_closed()
            except Exception as e:
                print(f"[FocusWindow] on_focus_window_closed 出错: {e}")

        set_app_mode("idle")
        event.accept()
