# search_crawler.py
import os
import random
import time

from rate_limiter import global_limiter


# ★ 开关：False = 不向 UI 发逐条进度信号（只保留控制台 print）
#   原因：跨线程 emit 到 UI 弹窗，在"任务交替"时可能撞上弹窗生命周期问题
#   排查时如需恢复 UI 实时进度，把这里改成 True 即可
_EMIT_PROGRESS_TO_UI = False


class SearchCrawler:
    def __init__(self, browser_manager):
        self.bm = browser_manager
        self._load_js()

    def _load_js(self):
        js_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search_scanner_strategies.js")
        try:
            with open(js_path, "r", encoding="utf-8") as f:
                self.scanner_js = f.read()
        except Exception as e:
            print(f"❌ 无法读取扫描策略文件: {e}")
            self.scanner_js = ""

    def _ensure_scanner_loaded(self, page):
        try:
            if not page.evaluate("typeof window.__apt_scanner_strategies !== 'undefined'"):
                page.add_script_tag(content=self.scanner_js)
                page.wait_for_timeout(300)
        except Exception:
            pass

    def _log(self, text, current=0, maximum=0):
        """统一的进度日志输出：始终打印到控制台；是否发信号给 UI 由开关控制"""
        print(text)  # ★ 控制台日志始终保留，便于排查
        if not _EMIT_PROGRESS_TO_UI:
            return
        try:
            from ws_server import ws_signals
            ws_signals.search_progress.emit(text, current, maximum)
            print(f"📡 [DEBUG] 信号已发出: {text}")
        except Exception:
            pass

    def run_task(self, task):
        if not self.scanner_js: return []

        final_links_map = {}
        for attempt in range(2):
            try:
                self.bm._ensure_started_impl(minimized=False)
                context = self.bm._browser.contexts[0]
                page = None
                try:
                    page = context.new_page()
                    page.set_viewport_size({"width": 1280, "height": 800})

                    self._log(f"🔍 正在打开: {task['url']}")
                    global_limiter.wait()
                    page.goto(task["url"], wait_until="domcontentloaded", timeout=30000)
                    page.add_script_tag(content=self.scanner_js)
                    page.wait_for_timeout(1500)

                    strategy = task.get("strategy", {})
                    scanner_type = strategy.get("scanner", "generic")
                    max_pages = task.get("max_pages", 3)
                    mode = strategy.get("mode", "scroll")

                    # ================= 模式 A：瀑布流滚动 =================
                    if mode == "scroll":
                        self._log("📜 开始模拟滚动抓取...", 0, 0)
                        target_count = max_pages * 10
                        max_scroll_steps = max_pages * 5 + 5
                        empty_scroll_count = 0
                        last_total = 0

                        scroll_js = """
                        (downDist) => {
                            let target = window;
                            let maxScroll = 0;
                            document.querySelectorAll('div').forEach(el => {
                                if (el.scrollHeight > el.clientHeight && el.clientHeight > 300) {
                                    if (el.scrollHeight > maxScroll) {
                                        maxScroll = el.scrollHeight;
                                        target = el;
                                    }
                                }
                            });
                            if (target === window || target === document.documentElement) {
                                window.scrollBy({ top: downDist, behavior: 'smooth' });
                            } else {
                                target.scrollBy({ top: downDist, behavior: 'smooth' });
                            }
                            const wheelEvent = new WheelEvent('wheel', {
                                deltaY: downDist, deltaMode: 0, bubbles: true, cancelable: true
                            });
                            target.dispatchEvent(wheelEvent);
                            document.body.dispatchEvent(wheelEvent);
                            document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'PageDown', keyCode: 34, which: 34, bubbles: true }));
                        }
                        """

                        # 抓首屏
                        try:
                            self._ensure_scanner_loaded(page)
                            if scanner_type == "tieba":
                                current_links = page.evaluate(
                                    "(strategy) => window.__apt_scanner_strategies.scanTieba(strategy)", strategy)
                            else:
                                current_links = page.evaluate(
                                    "(strategy) => window.__apt_scanner_strategies.scanGeneric(strategy)", strategy)
                            if current_links:
                                for link in current_links:
                                    url = link.get("url")
                                    if url and url not in final_links_map:
                                        final_links_map[url] = link
                        except Exception:
                            pass
                        last_total = len(final_links_map)

                        for step in range(max_scroll_steps):
                            if len(final_links_map) >= target_count:
                                self._log(f"🎯 已达到目标数量 ({len(final_links_map)}/{target_count})，结束滚动",
                                          len(final_links_map), target_count)
                                break
                            if empty_scroll_count >= 3:
                                self._log("🛑 连续多次未抓到新数据，准备结束滚动", len(final_links_map), target_count)
                                break

                            down_distance = random.randint(600, 1200)
                            try:
                                page.evaluate(scroll_js, down_distance)
                            except Exception as e:
                                self._log(f"⚠️ 滚动执行出错: {e}")

                            page.wait_for_timeout(random.randint(1500, 2500))

                            try:
                                self._ensure_scanner_loaded(page)
                                if scanner_type == "tieba":
                                    current_links = page.evaluate("(strategy) => window.__apt_scanner_strategies.scanTieba(strategy)", strategy)
                                else:
                                    current_links = page.evaluate(
                                        "(strategy) => window.__apt_scanner_strategies.scanGeneric(strategy)", strategy)

                                if current_links:
                                    for link in current_links:
                                        url = link.get("url")
                                        if url and url not in final_links_map:
                                            final_links_map[url] = link
                            except Exception as e:
                                self._log(f"⚠️ scroll 第 {step + 1} 步扫描失败: {e}")

                            current_total = len(final_links_map)
                            self._log(f"📜 滚动第 {step + 1} 次，当前收集: {current_total}/{target_count} 条",
                                      current_total, target_count)

                            if current_total == last_total:
                                empty_scroll_count += 1
                            else:
                                empty_scroll_count = 0
                                last_total = current_total

                    # ================= 模式 B：点击翻页 =================
                    elif mode == "click":
                        self._log(f"📄 开始点击翻页抓取 (限制 {max_pages} 页)...", 0, max_pages)
                        next_btn_selector = strategy.get("nextBtnSelector", "")
                        base_url = task["url"]  # 用于兜底构造翻页 URL

                        for page_num in range(1, max_pages + 1):
                            if page_num == 1:
                                wait_top = random.uniform(1.5, 3.5)
                                self._log(f"👀 第 {page_num} 页加载完毕，停留 {wait_top:.1f} 秒...", page_num - 1,
                                          max_pages)
                                page.wait_for_timeout(int(wait_top * 1000))
                            else:
                                page.wait_for_timeout(random.randint(500, 1500))

                            # 滚动到底部（原逻辑不变）
                            page.evaluate("window.scrollTo(0, 0);")
                            page.wait_for_timeout(random.randint(300, 600))
                            total_height = page.evaluate("document.body.scrollHeight")
                            view_height = page.evaluate("window.innerHeight")
                            current_y = 0
                            while current_y < total_height - view_height:
                                step = random.randint(300, 800)
                                current_y += step
                                page.evaluate(f"window.scrollTo({{ top: {current_y}, behavior: 'instant' }});")
                                page.wait_for_timeout(random.randint(50, 150))
                                if random.random() < 0.2:
                                    roll_back = random.randint(50, 150)
                                    page.evaluate(f"window.scrollBy(0, -{roll_back});")
                                    page.wait_for_timeout(random.randint(200, 400))
                                    current_y -= roll_back
                                total_height = page.evaluate("document.body.scrollHeight")
                            page.evaluate("window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });")
                            page.wait_for_timeout(random.randint(400, 800))

                            # ===== 抓取（原逻辑不变） =====
                            try:
                                self._ensure_scanner_loaded(page)
                                if scanner_type == "tieba":
                                    current_links = page.evaluate(
                                        "(strategy) => window.__apt_scanner_strategies.scanTieba(strategy)", strategy)
                                else:
                                    current_links = page.evaluate(
                                        "(strategy) => window.__apt_scanner_strategies.scanGeneric(strategy)", strategy)
                                if current_links:
                                    for link in current_links:
                                        url = link.get("url")
                                        if url and url not in final_links_map:
                                            final_links_map[url] = link
                            except Exception as e:
                                self._log(f"⚠️ 第 {page_num} 页扫描失败，保留已抓 {len(final_links_map)} 条后跳出: {e}",
                                          page_num, max_pages)
                                break

                            self._log(f"✅ 第 {page_num} 页抓取完毕，累计 {len(final_links_map)} 条", page_num, max_pages)

                            # ===== 翻页（改这里） =====
                            if page_num < max_pages:
                                try:
                                    hesitate_ms = random.randint(800, 1800)
                                    self._log(f"🤔 浏览完毕，犹豫 {hesitate_ms / 1000:.1f} 秒准备翻页...", page_num,
                                              max_pages)
                                    page.wait_for_timeout(hesitate_ms)

                                    url_before = page.url
                                    navigated = False

                                    # ① 优先：找按钮拿 href，直接 goto（最稳）
                                    next_btn = None
                                    if page.locator("a.pager-next").count() > 0:
                                        next_btn = page.locator("a.pager-next").first
                                    if not next_btn:
                                        for sel in ["a.next", ".pager a:has-text('下一页')",
                                                    ".pagination a:has-text('下一页')", "a:has-text('下一页')",
                                                    "a:has-text('下页')"]:
                                            if page.locator(sel).count() > 0:
                                                target = page.locator(sel).filter(
                                                    has_not=page.locator(".disabled, [class*='prev']")).first
                                                if target.count() > 0 and target.is_visible():
                                                    next_btn = target
                                                    break
                                    if not next_btn and next_btn_selector:
                                        target = page.locator(next_btn_selector).filter(
                                            has_not=page.locator(".disabled, [class*='prev']")).first
                                        if target.count() > 0 and target.is_visible():
                                            next_btn = target

                                    if next_btn and next_btn.is_visible():
                                        href = next_btn.get_attribute("href")
                                        if href:
                                            # 相对路径补全成绝对路径
                                            from urllib.parse import urljoin
                                            abs_href = urljoin(url_before, href)
                                            if abs_href.startswith("http"):
                                                self._log(f"🔗 直接跳转下一页: {abs_href[:60]}...", page_num, max_pages)
                                                global_limiter.wait()
                                                page.goto(abs_href, wait_until="domcontentloaded", timeout=15000)
                                                navigated = True

                                        if not navigated:
                                            # ② 兜底：坐标点击
                                            box = next_btn.bounding_box()
                                            if box:
                                                click_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
                                                click_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                                                page.mouse.move(click_x, click_y, steps=random.randint(5, 10))
                                                page.wait_for_timeout(random.randint(100, 300))
                                                page.mouse.click(click_x, click_y)
                                            else:
                                                next_btn.click(timeout=5000)

                                            # ★ 关键：等 URL 真的变化，最多等 5 秒
                                            try:
                                                page.wait_for_function(
                                                    f"window.location.href !== {repr(url_before)}",
                                                    timeout=5000
                                                )
                                                navigated = True
                                            except Exception:
                                                # 可能是 SPA 未变 URL，但内容变了，继续看 URL 是否一致
                                                pass

                                        # ★ 兜底：如果 URL 还是没变，构造百度 pn 参数直接跳
                                        if page.url == url_before:
                                            if "baidu.com" in url_before and "pn=" in url_before:
                                                import re as _re
                                                new_url = _re.sub(r"[&?]pn=\d+", f"&pn={page_num * 10}", url_before)
                                                if "pn=" not in new_url:
                                                    sep = "&" if "?" in new_url else "?"
                                                    new_url = f"{new_url}{sep}pn={page_num * 10}"
                                                self._log(f"🔧 URL 未变化，按 pn 参数兜底跳转: {new_url[:60]}...",
                                                          page_num, max_pages)
                                                global_limiter.wait()
                                                page.goto(new_url, wait_until="domcontentloaded", timeout=15000)
                                                navigated = True
                                            elif "baidu.com" in url_before and "pn=" not in url_before:
                                                sep = "&" if "?" in url_before else "?"
                                                new_url = f"{url_before}{sep}pn={page_num * 10}"
                                                self._log(f"🔧 URL 未变化，按 pn 参数兜底跳转: {new_url[:60]}...",
                                                          page_num, max_pages)
                                                global_limiter.wait()
                                                page.goto(new_url, wait_until="domcontentloaded", timeout=15000)
                                                navigated = True

                                        if not navigated:
                                            self._log(f"⚠️ 翻页失败，URL 仍为 {page.url}，提前结束", page_num, max_pages)
                                            break

                                        # ★ 关键：翻页成功后再等 1.5–2.5 秒让 DOM 稳定
                                        page.wait_for_timeout(random.randint(1500, 2500))
                                        self._ensure_scanner_loaded(page)
                                        self._log(f"🌐 已进入第 {page_num + 1} 页，当前 URL: {page.url[:80]}", page_num,
                                                  max_pages)

                                    else:
                                        self._log("⚠️ 未找到可用的下一页按钮，提前结束翻页", page_num, max_pages)
                                        break
                                except Exception as e:
                                    self._log(f"⚠️ 点击下一页失败: {e}", page_num, max_pages)
                                    break

                        self._log(f"✅ 抓取完成，共提取 {len(final_links_map)} 条", max_pages, max_pages)
                        return list(final_links_map.values())
                finally:
                    if page:
                        try:
                            page.close()
                        except Exception:
                            pass
            except Exception as e:
                err_msg = str(e).lower()
                if (
                        "connection closed" in err_msg or "target closed" in err_msg or "browser closed" in err_msg) and attempt == 0:
                    self._log(f"⚠️ 检测到浏览器连接断开 ({e})，准备重置连接并重试...")
                    self.bm.reset_playwright();
                    time.sleep(2);
                    continue

                self._log(f"❌ 搜索执行异常: {e}")
                if final_links_map: return list(final_links_map.values())
                raise e
        return list(final_links_map.values()) if final_links_map else []