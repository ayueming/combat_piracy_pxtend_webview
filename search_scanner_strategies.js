// search_scanner_strategies.js
// 职责：只负责 DOM 解析和数据提取，不负责滚动，不负责发网络请求。

window.__apt_scanner_strategies = {
    // ================= 通用 URL 提取器 =================
    // ★ 新增了 base_url 参数，用于相对路径补全
    extractUrl: function(container, urlExtract, base_url) {
        if (!urlExtract || !urlExtract.rules) return "";
        let rules = urlExtract.rules;
        let blacklist = urlExtract.blacklist_patterns || [];

        for (let rule of rules) {
            let el = rule.on_self ? container : container.querySelector(rule.selector);
            if (!el) continue;

            // 如果是提取 href，优先用 el.href 拿绝对路径（浏览器底层特性）
            // 其他属性走 getAttribute
            let val = "";
            if (rule.attr === "href" && el.href) {
                val = el.href;
            } else {
                val = el.getAttribute(rule.attr) || "";
            }

            if (!val) continue;

            // 如果配置了 json_path，解析 JSON 拿深层数据
            if (rule.json_path) {
                try {
                    let obj = JSON.parse(val);
                    let keys = rule.json_path.split('.');
                    for (let k of keys) {
                        if (obj && obj[k] !== undefined && obj[k] !== null) {
                            obj = obj[k];
                        } else {
                            obj = null;
                            break;
                        }
                    }
                    val = (typeof obj === 'string') ? obj : "";
                } catch (e) {
                    val = ""; // JSON 解析失败，废弃
                }
            }

            if (!val) continue;

            // ★ 核心：如果是以 / 开头的相对路径，并且配置了 base_url，直接拼接
            if (val.startsWith("/") && base_url) {
                val = base_url + val;
            }

            if (!val.startsWith("http")) continue;

            // 检查是否命中黑名单
            let isBlack = false;
            for (let p of blacklist) {
                if (val.includes(p)) { isBlack = true; break; }
            }
            if (isBlack) continue;

            // 完美命中，直接返回
            return val;
        }
        return "";
    },

    // ================= 通用来源提取器 =================
    extractSourceSite: function(container, strategy, href, title) {
        if (strategy.sourceSelector) {
            let sourceEl = container.querySelector(strategy.sourceSelector);
            if (sourceEl) {
                let txt = sourceEl.innerText.replace(/\s+/g, ' ').trim();
                txt = txt.replace(/^(回答者|提问者|楼主)[:：]?\s*/, '');
                if (txt && !txt.includes("http") && txt.length < 30) return txt;
            }
        }
        // 兜底：提取域名
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
            // 1. 提取链接 (传入 strategy.urlExtract 和 strategy.base_url)
            let href = this.extractUrl(container, strategy.urlExtract, strategy.base_url);
            if (!href) return; // 如果所有规则都失败，整张卡片废弃

            // 2. 寻找标题元素
            let titleEl = container.querySelector(strategy.titleSelector);
            if (!titleEl) titleEl = container.querySelector("h3");
            if (!titleEl) return;

            // 3. 提取标题文本
            let title = (titleEl.innerText || "").replace(/\s+/g, ' ').trim();
            if (!title) return;

            // 4. 提取摘要
            let bodyText = "";
            if (strategy.bodySelector) {
                let bodyEl = container.querySelector(strategy.bodySelector);
                if (bodyEl) {
                    bodyText = bodyEl.innerText.trim();
                    bodyText = bodyText.replace(/^(合|答|回答|最佳回答)[:：]\s*/, '');
                }
            }

            // 5. 提取来源
            let sourceSite = this.extractSourceSite(container, strategy, href, title);

            linksData.push({ url: href, title: title, body: bodyText, source_site: sourceSite });
        });
        return linksData;
    },

    // ================= 贴吧专用扫描策略 =================
    scanTieba: function(strategy) {
        let rawLinks = [];
        const cardRoots = document.querySelectorAll('.threadcardclass.index-feed-cards, .thread-content-box');

        cardRoots.forEach(threadCard => {
            // 1. 提取链接
            let postUrl = this.extractUrl(threadCard, strategy.urlExtract, strategy.base_url);
            if (!postUrl) return;

            // 2. 提取标题
            let title = threadCard.getAttribute('title') || '';
            if (!title) {
                const titleEl = threadCard.querySelector('.title-wrap span');
                if (titleEl) title = (titleEl.innerText || '').trim();
            }
            if (!title) return;

            // 3. 提取正文
            const bodyEl = threadCard.querySelector('.abstract-wrap span');
            let bodyText = bodyEl ? (bodyEl.innerText || "").trim() : "";

            // 4. 提取来源吧名
            const forumEl = threadCard.querySelector('.forum-name-text');
            let sourceSite = forumEl ? (forumEl.innerText || "").trim() : "百度贴吧";

            rawLinks.push({ url: postUrl, title: title, body: bodyText, source_site: sourceSite });
        });
        return rawLinks;
    }
};