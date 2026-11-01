// content.js
console.log("🚀 插件执行端已注入（纯净版：仅保留手动拾取与预览）");

// ==========================================
// 1. 浮窗拾取器 UI（带拖拽 + 下拉框）
// ==========================================
let pickerState = {
    injected: false,
    picking: false,
    hoverEl: null,
    savedCount: 0,
    ctx: null,
    host: null,
    shadow: null
};

function initPicker(ctx) {
    if (pickerState.injected) {
        applyPickerContext(ctx);
        return;
    }
    pickerState.injected = true;
    pickerState.ctx = ctx;

    // 记录历史栈用于溯源
    let navHistory = [];
    try {
        const saved = sessionStorage.getItem("__aptNavHistory");
        if (saved) navHistory = JSON.parse(saved);
    } catch(e) {}
    const curPage = { url: location.href, title: document.title || "" };
    if (navHistory.length === 0 || navHistory[navHistory.length - 1].url !== curPage.url) {
        navHistory.push(curPage);
        if (navHistory.length > 10) navHistory.shift();
        try { sessionStorage.setItem("__aptNavHistory", JSON.stringify(navHistory)); } catch(e) {}
    }

    const host = document.createElement("div");
    host.id = "__apt_picker_host__";
    // 初始定位在右下角
    host.style.cssText = "all: initial; position:fixed; right:20px; bottom:20px; z-index:2147483647; font-family:Microsoft YaHei,Arial,sans-serif;";

    const parent = document.body || document.documentElement;
    if (!parent) { setTimeout(() => initPicker(ctx), 50); return; }
    parent.appendChild(host);

    const shadow = host.attachShadow({ mode: "closed" });
    pickerState.host = host;
    pickerState.shadow = shadow;

    // 注入 CSS
    const style = document.createElement("style");
    style.textContent = `
        :host, * { box-sizing: border-box; }
        .panel { width: 240px; background: #ffffff; border: 1px solid #d0d7de; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,.12); font-size: 12px; color: #24292f; overflow: hidden; pointer-events: auto !important; }
        .head { background: #f6f8fa; padding: 8px 10px; font-weight: 600; border-bottom: 1px solid #eaeef2; display: flex; justify-content: space-between; align-items: center; cursor: move; user-select: none; }
        .head span { cursor: move; }
        .ctx-area { padding: 8px 10px; border-bottom: 1px solid #eaeef2; display: flex; flex-direction: column; gap: 6px; }
        .ctx-row { display: flex; align-items: center; justify-content: space-between; }
        .ctx-row label { color: #57606a; font-weight: 600; }
        .ctx-row select { flex: 1; margin-left: 8px; padding: 2px 4px; border: 1px solid #d0d7de; border-radius: 4px; font-size: 12px; background: white; }
        .ctx-kw { color: #57606a; word-break: break-all; }
        .ctx-kw b { color: #24292f; }
        .actions { display: flex; flex-direction: column; gap: 6px; padding: 8px 10px; }
        button { padding: 6px 0; border-radius: 5px; cursor: pointer !important; font-size: 12px; font-weight: 600; border: 1px solid transparent; font-family: inherit; pointer-events: auto !important; }
        .btn-save-page { background: #f6f8fa; color: #24292f; border-color: #d0d7de; }
        .btn-save-page:hover { background: #eaeef2; }
        .btn-pick { background: #f6f8fa; color: #24292f; border-color: #d0d7de; }
        .btn-pick:hover { background: #eaeef2; }
        .btn-pick.active { background: #fff8c5; border-color: #d4a72c; color: #7d4e00; }
        .btn-save-batch { background: #10B981; color: #fff; margin-top: 4px; }
        .btn-save-batch:hover { background: #059669; }
        .status { padding: 6px 10px; background: #f6f8fa; color: #57606a; font-size: 11px; }
    `;
    shadow.appendChild(style);

    const panel = document.createElement("div");
    panel.className = "panel";

    // 头部（拖拽把手）
    const head = document.createElement("div");
    head.className = "head";
    head.innerHTML = `<span>反盗版助手 · 拾取</span>`;
    panel.appendChild(head);

    // 上下文区（带下拉框）
    const ctxArea = document.createElement("div");
    ctxArea.className = "ctx-area";
    ctxArea.innerHTML = `
        <div class="ctx-row">
            <label>作品:</label>
            <select id="apt-book"></select>
        </div>
        <!-- ★ 加上隐藏样式，保留 DOM 结构 -->
        <div class="ctx-row" style="display: none;">
            <label>分类:</label>
            <select id="apt-category"></select>
        </div>
        <div class="ctx-kw">关键词: <b id="apt-kw">-</b></div>
    `;
    panel.appendChild(ctxArea);

    // 按钮区
    const actions = document.createElement("div");
    actions.className = "actions";

    const btnSavePage = document.createElement("button");
    btnSavePage.className = "btn-save-page";
    btnSavePage.textContent = "仅保存当前网址";

    const btnPick = document.createElement("button");
    btnPick.className = "btn-pick";
    btnPick.textContent = "手动拾取链接（或按住ALT）";

    const btnSaveBatch = document.createElement("button");
    btnSaveBatch.className = "btn-save-batch";
    btnSaveBatch.textContent = "💾 保存本批";

    actions.appendChild(btnSavePage);
    actions.appendChild(btnPick);
    actions.appendChild(btnSaveBatch);
    panel.appendChild(actions);

    // 状态区
    const status = document.createElement("div");
    status.className = "status";
    status.innerHTML = `待保存 <b id="apt-count">0</b> 条`;
    panel.appendChild(status);

    shadow.appendChild(panel);

    // 遮罩层
    const mask = document.createElement("div");
    mask.style.cssText = "all: initial; position:fixed; top:0; left:0; width:100vw; height:100vh; z-index:2147483645; background:transparent; display:none; cursor:crosshair;";
    document.body.appendChild(mask);

    // 高亮框
    const overlay = document.createElement("div");
    overlay.style.cssText = "all: initial; position:fixed; pointer-events:none; border:2px solid #1f6feb; background:rgba(31,111,235,.12); border-radius:3px; display:none; z-index:2147483646; transition:all .08s ease;";
    document.body.appendChild(overlay);

    // 应用上下文
    applyPickerContext(ctx);

    // ===== 拖拽逻辑 =====
    let isDragging = false;
    let dragStartX, dragStartY, initialLeft, initialTop;

    head.addEventListener("mousedown", (e) => {
        if (e.target.tagName === "BUTTON") return;
        isDragging = true;
        dragStartX = e.clientX;
        dragStartY = e.clientY;
        const rect = host.getBoundingClientRect();
        initialLeft = rect.left;
        initialTop = rect.top;
        // 固定转换为绝对定位
        host.style.left = initialLeft + "px";
        host.style.top = initialTop + "px";
        host.style.right = "auto";
        host.style.bottom = "auto";
        e.preventDefault();
    });

    document.addEventListener("mousemove", (e) => {
        if (!isDragging) return;
        const dx = e.clientX - dragStartX;
        const dy = e.clientY - dragStartY;
        host.style.left = (initialLeft + dx) + "px";
        host.style.top = (initialTop + dy) + "px";
    });

    document.addEventListener("mouseup", () => {
        isDragging = false;
    });

    // ===== 拾取逻辑 =====
    function enterPicking(source) {
        pickerState.picking = true;
        btnPick.classList.add("active");
        btnPick.textContent = "🔴 拾取中（点击退出）";
        mask.style.display = "block";
        host.style.borderLeft = "4px solid #EF4444";
    }

    function exitPicking() {
        pickerState.picking = false;
        btnPick.classList.remove("active");
        btnPick.textContent = "手动拾取链接（或按住ALT）";
        mask.style.display = "none";
        overlay.style.display = "none";
        host.style.borderLeft = "";
        pickerState.hoverEl = null;
    }

    btnPick.addEventListener("click", (e) => {
        e.preventDefault(); e.stopPropagation();
        if (pickerState.picking) exitPicking(); else enterPicking();
    });

    btnSavePage.addEventListener("click", (e) => {
        e.preventDefault(); e.stopPropagation();
        sendManualPick({
            url: location.href,
            title: document.title || "未命名网页",
            body: "",
            parent_url: "",
            parent_title: ""
        });

        const selBook = pickerState.shadow?.getElementById("apt-book");
        const currentBookName = selBook ? selBook.value : (pickerState.ctx?.book_name || "未选择作品");

        let displayUrl = location.href;
        if (displayUrl.length > 30) {
            displayUrl = displayUrl.substring(0, 30) + "...";
        }

        showToast(`✅ 已拾取《${currentBookName}》 - ${displayUrl}`);
    }, true);

    btnSaveBatch.addEventListener("click", (e) => {
        e.preventDefault(); e.stopPropagation();
        chrome.runtime.sendMessage({
            action: "send_to_python",
            data: { action: "save_manual_batch", batch_id: pickerState.ctx?.batch_id || "" }
        });

        pickerState.savedCount = 0;
        const countEl = pickerState.shadow?.getElementById("apt-count");
        if (countEl) countEl.textContent = "0";

        showToast("正在保存本批并解析...");
    });

    function isEditable(el) { const t = el.tagName; return t === "INPUT" || t === "TEXTAREA" || el.isContentEditable; }

    function findLink(el) {
        let cur = el;
        while (cur && cur !== document.documentElement) {
            if (cur.tagName === "A" && cur.href) return cur;
            cur = cur.parentElement;
        }
        let txt = (el.innerText || el.textContent || "").trim();
        if (/https?:\/\//.test(txt)) return el;
        if (location.hostname === "tieba.baidu.com") {
            const card = el.closest(".threadcardclass.index-feed-cards, .thread-content-box");
            if (card) {
                const postLink = card.querySelector("a[href*='/p/']");
                if (postLink) return postLink;
            }
        }
        return null;
    }

    // Alt 瞬态拾取
    let altHoldTimer = null;
    document.addEventListener("keydown", (e) => {
        if (e.key !== "Alt" || e.repeat || isEditable(e.target)) return;
        if (pickerState.picking || altHoldTimer) return;
        altHoldTimer = setTimeout(() => {
            altHoldTimer = null;
            enterPicking();
        }, 120);
    }, true);
    document.addEventListener("keyup", (e) => {
        if (e.key === "Alt") {
            if (altHoldTimer) { clearTimeout(altHoldTimer); altHoldTimer = null; }
            exitPicking();
        }
    }, true);

    mask.addEventListener("mousemove", (e) => {
        if (!pickerState.picking) return;
        mask.style.pointerEvents = "none";
        const realTarget = document.elementFromPoint(e.clientX, e.clientY);
        mask.style.pointerEvents = "auto";
        if (!realTarget) { overlay.style.display = "none"; return; }

        const link = findLink(realTarget);
        if (!link) { overlay.style.display = "none"; pickerState.hoverEl = null; return; }

        if (link !== pickerState.hoverEl) {
            pickerState.hoverEl = link;
            const r = link.getBoundingClientRect();
            overlay.style.left = r.left + "px"; overlay.style.top = r.top + "px";
            overlay.style.width = r.width + "px"; overlay.style.height = r.height + "px";
            overlay.style.display = "block";
        }
    }, true);

    mask.addEventListener("mousedown", (e) => {
        if (!pickerState.picking) return;
        mask.style.pointerEvents = "none";
        const realTarget = document.elementFromPoint(e.clientX, e.clientY);
        mask.style.pointerEvents = "auto";
        if (!realTarget) return;

        e.preventDefault(); e.stopPropagation();

        let finalUrl = "";
        let curNode = realTarget;
        while (curNode && curNode !== document.documentElement) {
            if (curNode.tagName === "A" && curNode.href) {
                finalUrl = curNode.href;
                break;
            }
            curNode = curNode.parentElement;
        }

        let targetForText = realTarget;
        for (let i = 0; i < 3; i++) {
            if (targetForText.parentElement && targetForText.parentElement.innerText.length < 1000) {
                targetForText = targetForText.parentElement;
            } else {
                break;
            }
        }
        let bodyText = (targetForText.innerText || targetForText.textContent || "").trim();

        if (!finalUrl) {
            const match = bodyText.match(/https?:\/\/[a-zA-Z0-9\-\._~:\/?#\[\]@!\$&'\(\)\*\+,;=%]+/);
            if (match) {
                finalUrl = match[0];
            }
        }

        if (!finalUrl || !/^https?:\/\//i.test(finalUrl)) {
            showToast("❌ 未识别到有效 URL，请重新拾取");
            return;
        }

        let titleText = document.title || "未命名网页";
        if (bodyText.length > 0 && bodyText.length < 50) {
            titleText = bodyText;
        }

        let p_url = "";
        let p_title = "";
        if (document.referrer) {
            p_url = document.referrer;
        } else {
            let parentNode = navHistory.length >= 2 ? navHistory[navHistory.length - 2] : null;
            if (parentNode) { p_url = parentNode.url; p_title = parentNode.title; }
            else { p_url = window.location.href; p_title = document.title || ""; }
        }

        sendManualPick({
            url: finalUrl,
            title: titleText,
            body: bodyText,
            parent_url: p_url,
            parent_title: p_title
        });

        showToast("✅ 已拾取关联作品 URL");
    }, true);
}

// 更新上下文（书籍、分类、关键词）
function applyPickerContext(ctx) {
    pickerState.ctx = ctx || {};
    if (!pickerState.shadow) return;

    const selBook = pickerState.shadow.getElementById("apt-book");
    const selCat = pickerState.shadow.getElementById("apt-category");
    const kwEl = pickerState.shadow.getElementById("apt-kw");

    if (kwEl) kwEl.textContent = ctx.keyword || "-";

    if (selBook) {
        selBook.innerHTML = "";
        (ctx.available_books || []).forEach(name => {
            const opt = document.createElement("option");
            opt.value = name;
            opt.textContent = name;
            if (name === ctx.book_name) opt.selected = true;
            selBook.appendChild(opt);
        });
        if (!ctx.available_books?.length) {
            const opt = document.createElement("option");
            opt.value = ctx.book_name || "未选择";
            opt.textContent = ctx.book_name || "未选择";
            selBook.appendChild(opt);
        }
    }

    if (selCat) {
        selCat.innerHTML = "";
        (ctx.available_categories || []).forEach(name => {
            const opt = document.createElement("option");
            opt.value = name;
            opt.textContent = name;
            if (name === ctx.category) opt.selected = true;
            selCat.appendChild(opt);
        });
        if (!ctx.available_categories?.length) {
            const opt = document.createElement("option");
            opt.value = ctx.category || "其他";
            opt.textContent = ctx.category || "其他";
            selCat.appendChild(opt);
        }
    }
}

// 弹出 Toast 提示
function showToast(msg) {
    if (!pickerState.shadow) return;
    let toast = pickerState.shadow.getElementById("__apt_toast__");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "__apt_toast__";
        toast.style.cssText = "position:fixed; right:20px; bottom:180px; z-index:2147483647; padding:8px 12px; background:rgba(0,0,0,.82); color:#fff; border-radius:6px; font-size:12px; display:none; max-width:260px; font-family:Microsoft YaHei,Arial,sans-serif;";
        pickerState.shadow.appendChild(toast);
    }
    toast.textContent = msg;
    toast.style.display = "block";
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { toast.style.display = "none"; }, 2500);
}

// 发送拾取数据到 Python
function sendManualPick(link) {
    const selBook = pickerState.shadow?.getElementById("apt-book");
    const selCat = pickerState.shadow?.getElementById("apt-category");

    let finalCategory = "";
    if (selCat && selCat.value) {
        finalCategory = selCat.value;
    } else if (pickerState.ctx && pickerState.ctx.category) {
        finalCategory = pickerState.ctx.category;
    }

    chrome.runtime.sendMessage({
        action: "send_to_python",
        data: {
            action: "manual_pick",
            url: link.url,
            title: link.title,
            body: link.body || "",
            source_site: link.source_site || "",
            parent_url: link.parent_url || "",
            parent_title: link.parent_title || "",
            book_name: selBook ? selBook.value : "",
            category: finalCategory
        }
    });

    pickerState.savedCount++;
    const countEl = pickerState.shadow?.getElementById("apt-count");
    if (countEl) countEl.textContent = String(pickerState.savedCount);
}


// ==========================================
// 2. 消息路由
// ==========================================
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    // 拾取模式启动（由 background.js 在预览 Tab 打开时下发）
    if (request.action === "start_picking_mode") {
        const ctx = request.context || {};
        // 保存一份到 sessionStorage，防止页面刷新后丢失
        try {
            sessionStorage.setItem("__aptPreviewCtx", JSON.stringify(ctx));
            sessionStorage.setItem("__aptIsPreview", "1");
        } catch(e) {}

        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", () => initPicker(ctx));
        } else {
            initPicker(ctx);
        }
        sendResponse({ ok: true });
    }

    // 上下文更新（可选）
    if (request.action === "update_context") {
        applyPickerContext(request.data || {});
        sendResponse({ ok: true });
    }
});

// ==========================================
// 3. 页面加载时：自动检测是否是预览页
// ==========================================
(function autoRecoverPicker() {
    try {
        if (sessionStorage.getItem("__aptIsPreview") === "1") {
            const ctxStr = sessionStorage.getItem("__aptPreviewCtx");
            if (ctxStr) {
                const ctx = JSON.parse(ctxStr);
                if (document.readyState === "loading") {
                    document.addEventListener("DOMContentLoaded", () => initPicker(ctx));
                } else {
                    initPicker(ctx);
                }
            }
        }
    } catch(e) {}
})();