// search_scanner_strategies.js
// 职责：只负责 DOM 解析和数据提取，不负责滚动，不负责发网络请求。

window.__apt_scanner_strategies = {
    // ================= 通用提取器 =================
    extractSourceSite: function(container, strategy, href, title) {
        // 1. 优先使用配置的选择器
        if (strategy.sourceSelector) {
            let sourceEl = container.querySelector(strategy.sourceSelector);
            if (sourceEl) {
                let txt = sourceEl.innerText.replace(/\s+/g, '').trim();
                if (txt && !txt.includes("http")) return txt;
            }
        }

        // 2. 通用智能兜底：抓取所有 class 或 id 里带有 source、site、from 字眼的标签
        let genericSelectors = [
            '[class*="source-item"]', '[class*="source"]',
            '[class*="site"]', '[class*="from"]'
        ];
        for (let sel of genericSelectors) {
            let els = container.querySelectorAll(sel);
            for (let el of els) {
                let txt = el.innerText.replace(/\s+/g, '').trim();
                // ★ 核心修改：去掉 !title.includes(txt)，只要长度合适、不带http，就认定它是来源
                if (txt && txt.length > 0 && txt.length < 20 && !txt.includes("http") && !txt.includes("www.")) {
                    return txt;
                }
            }
        }

        // 3. 彻底找不到，提取域名
        try {
            let host = new URL(href).hostname;
            return host.replace('www.', '');
        } catch(e){ return ""; }
    },

    // ================= 通用扫描策略 =================
    scanGeneric: function(strategy) {
        let linksData = [];
        if (!strategy || !strategy.resultSelector) return linksData;

        document.querySelectorAll(strategy.resultSelector).forEach(container => {
            let titleLink = container.querySelector(strategy.titleSelector);
            if (!titleLink) return;
            let href = titleLink.getAttribute('data-landurl') || titleLink.getAttribute('mu') || titleLink.href;
            if (!href || !href.startsWith("http")) return;

            let title = (titleLink.innerText || "").replace(/\s+/g, ' ').trim();
            if (!title) return;

            let bodyText = "";
            if (strategy.bodySelector) {
                let bodyEl = container.querySelector(strategy.bodySelector);
                if (bodyEl) {
                    bodyText = bodyEl.innerText.trim();
                    bodyText = bodyText.replace(/^(合|答|回答|最佳回答)[:：]\s*/, '');
                }
            }

            let sourceSite = this.extractSourceSite(container, strategy, href, title);

            linksData.push({ url: href, title: title, body: bodyText, source_site: sourceSite });
        });
        return linksData;
    },

    // ================= 贴吧专用扫描策略 =================
    scanTieba: function() {
        let rawLinks = [];
        // 兼容老版(Chrome)与新版(Edge)结构
        const cardRoots = document.querySelectorAll('.threadcardclass.index-feed-cards, .thread-content-box');

        cardRoots.forEach(threadCard => {
            let title = threadCard.getAttribute('title') || '';
            if (!title) {
                const titleEl = threadCard.querySelector('.title-wrap span');
                if (titleEl) title = (titleEl.innerText || '').trim();
            }
            if (!title) return;

            const linkEl = threadCard.querySelector('a[href*="/p/"]');
            if (!linkEl) return;
            let postUrl = linkEl.href;

            const match = postUrl.match(/(https?:\/\/tieba\.baidu\.com\/p\/\d+)/);
            if (match) {
                postUrl = match[1];
            } else {
                return;
            }

            const bodyEl = threadCard.querySelector('.abstract-wrap span');
            let bodyText = bodyEl ? (bodyEl.innerText || "").trim() : "";

            const forumEl = threadCard.querySelector('.forum-name-text');
            let sourceSite = forumEl ? (forumEl.innerText || "").trim() : "百度贴吧";

            rawLinks.push({ url: postUrl, title: title, body: bodyText, source_site: sourceSite });
        });
        return rawLinks;
    }
};