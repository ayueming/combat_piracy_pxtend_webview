# picker_injector.py
"""把 picker 注入页面的 JS 构建逻辑抽出来，供 focus / spider 共用。

只做字符串拼接，不依赖任何 Qt / 数据库对象。
"""
import json


def build_picker_js(ctx: dict) -> str:
    """
    ctx 结构：
        book_name: str               当前选中的书（下拉框初始值）
        category: str                当前选中的分类（下拉框初始值）
        keyword: str                 搜索关键词（spider 传 ""）
        batch_id: str                保存本批时传给后端的批号
        books: list[str]             书籍下拉选项
        categories: list[str]        分类下拉选项
        is_mobile: int               0/1，UA 模式
        allow_change_book: bool      是否允许用户改书
        allow_change_category: bool  是否允许用户改分类
    """
    ctx_json = json.dumps(ctx, ensure_ascii=False)

    return f"""
    (function() {{
        if (window.__webview_picker_injected) return;
        window.__webview_picker_injected = true;

        new QWebChannel(qt.webChannelTransport, function(channel) {{
            window.pyBridge = channel.objects.pyBridge;
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
                .ctx-row select:disabled {{ background: #f6f8fa; color: #8b949e; cursor: not-allowed; }}
                .ctx-kw {{ color: #57606a; word-break: break-all; }}
                .ctx-kw b {{ color: #24292f; }}
                .actions {{ display: flex; flex-direction: column; gap: 6px; padding: 8px 10px; }}
                button {{ padding: 6px 0; border-radius: 5px; cursor: pointer !important; font-size: 12px; font-weight: 600; border: 1px solid transparent; font-family: inherit; pointer-events: auto !important; }}
                .btn-save-page {{ background: #f6f8fa; color: #24292f; border-color: #d0d7de; }}
                .btn-save-page:hover {{ background: #eaeef2; }}
                .btn-pick {{ background: #f6f8fa; color: #24292f; border-color: #d0d7de; }}
                .btn-pick.active {{ background: #fff8c5; border-color: #d4a72c; color: #7d4e00; }}
                .btn-ua-toggle {{ background: #f6f8fa; color: #24292f; border-color: #d0d7de; margin-top: 4px; }}
                .btn-ua-toggle:hover {{ background: #eaeef2; }}
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
                    <button class="btn-ua-toggle" id="btn-ua-toggle"></button>
                    <button class="btn-save-batch" id="btn-save-batch">💾 解析本次拾取</button>
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
            if (!ctx.allow_change_book) selBook.disabled = true;

            ctx.categories.forEach(name => {{
                const opt = document.createElement("option");
                opt.value = name; opt.textContent = name;
                if (name === ctx.category) opt.selected = true;
                selCat.appendChild(opt);
            }});
            if (!ctx.allow_change_category) selCat.disabled = true;

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
            const btnUAToggle = shadow.getElementById("btn-ua-toggle");
            const countEl = shadow.getElementById("apt-count");

            // ★ 核心修改：向 Python 请求真实的数据库 Pending 数量
            function refreshPendingCount() {{
                if (window.pyBridge) {{
                    window.pyBridge.get_pending_count(function(count) {{
                        countEl.textContent = count;
                    }});
                }}
            }}
            // 注入时拉取一次（翻页后重新注入也能拿到全库真实数据）
            refreshPendingCount();

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
                    // ★ 拾取后，向 Python 询问最新的数量
                    setTimeout(refreshPendingCount, 300);
                }}
            }}, true);

            btnSavePage.addEventListener("click", () => {{
                if (window.pyBridge) {{
                    window.pyBridge.save_page(window.location.href, document.title, document.body.innerText.substring(0, 500), selBook.value, selCat.value);
                    // ★ 仅保存当前网址后，数量也会增加，刷新一下
                    setTimeout(refreshPendingCount, 300);
                }}
            }});

            btnSaveBatch.addEventListener("click", () => {{
                if (window.pyBridge) {{
                    window.pyBridge.save_batch(ctx.batch_id);
                    // ★ 保存后，数量会归零或减少
                    setTimeout(refreshPendingCount, 500); 
                }}
            }});

            btnUAToggle.textContent = ctx.is_mobile
                ? "🖥️ 切换到桌面 UA"
                : "📱 切换到移动 UA";
            btnUAToggle.addEventListener("click", () => {{
                if (!window.pyBridge) return;
                const target = btnUAToggle.textContent.includes("桌面") ? "0" : "1";
                window.pyBridge.switch_ua(target);
            }});
        }}
    }})();
    """