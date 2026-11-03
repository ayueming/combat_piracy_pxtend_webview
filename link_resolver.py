# link_resolver.py
import os
import time
import random
import urllib.request
from browser_thread import browser_thread  # ★ 排队线程
from rate_limiter import global_limiter  # ★ 引入全局节拍器


class LinkResolver:
    def __init__(self, browser_manager=None):
        self.bm = browser_manager

    def _is_short_url(self, url):
        if not url: return False
        if "baidu.com/link?url=" in url:
            return True
        if "m.baidu.com/from=0" in url:
            return True
        return False

    def resolve_single(self, url, is_mobile=0):
        if not self._is_short_url(url):
            return url

        # 1. 决定 urllib 的 UA
        if is_mobile:
            ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
        else:
            ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

        # 2. urllib 极速尝试
        try:
            headers = {'User-Agent': ua}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                real_url = response.geturl()
                if real_url and not self._is_short_url(real_url):
                    return real_url
        except Exception:
            pass

        # 3. Playwright 兜底
        if self.bm:
            def _do_browser_resolve():
                context = None
                try:
                    self.bm._ensure_started_impl()
                    global_limiter.wait()

                    if is_mobile:
                        context = self.bm._browser.new_context(
                            user_agent=ua,
                            viewport={"width": 390, "height": 844},
                            is_mobile=True,
                            has_touch=True
                        )
                    else:
                        context = self.bm._browser.new_context(user_agent=ua)

                    page = context.new_page()

                    # ★ 一阶段核心：等待 URL 发生变化
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=8000)
                        # 最长等 5 秒，只要不再是百度短链，立刻打断等待
                        page.wait_for_url(
                            lambda u: "baidu.com/link?url=" not in u and "m.baidu.com/from=0" not in u,
                            timeout=5000
                        )
                        # 成功跳转后，拟人停留 800~1500 ms
                        page.wait_for_timeout(random.randint(800, 1500))
                        return page.url

                    except Exception as wait_err:
                        # 如果是等超时了（死链），或者页面崩了，就直接放弃，不拟人等待
                        # print(f"⏳ [一阶段] URL未发生变化或超时: {wait_err}")
                        return url
                    finally:
                        if context:
                            context.close()

                except Exception as e:
                    print(f"⚠️ Playwright 解析异常: {e}")
                    global_limiter.penalize(2.0)
                    if context:
                        context.close()
                    return url

            try:
                real_url = browser_thread.submit(_do_browser_resolve)
                if real_url and not self._is_short_url(real_url):
                    return real_url
            except Exception as e:
                print(f"⚠️ 浏览器排队解析失败: {e}")

        return url

    def resolve_detail(self, detail_url, strategy, is_mobile=0):
        """
        二阶段：进入详情页，根据 detailExtract 规则提取最终真实链接。
        """
        if not self.bm:
            return detail_url

        def _do_browser_resolve():
            context = None
            try:
                self.bm._ensure_started_impl()
                global_limiter.wait()

                if is_mobile:
                    ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
                    context = self.bm._browser.new_context(
                        user_agent=ua,
                        viewport={"width": 390, "height": 844},
                        is_mobile=True,
                        has_touch=True
                    )
                else:
                    context = self.bm._browser.new_context()

                page = context.new_page()

                print(f"🔍 [二阶段] 正在进入详情页: {detail_url}")

                try:
                    page.goto(detail_url, wait_until="domcontentloaded", timeout=10000)

                    # ★ 二阶段核心：智能等待特定元素出现
                    wait_selector = strategy.get("detailExtract", {}).get("wait_selector")
                    if wait_selector:
                        # 最长等 5 秒，元素一出现立刻继续
                        page.wait_for_selector(wait_selector, state="visible", timeout=5000)
                    else:
                        # 兜底：如果配置忘写了，就等 1.5 秒
                        page.wait_for_timeout(1500)

                    # 动态加载 JS 扫描脚本
                    js_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search_scanner_strategies.js")
                    with open(js_path, "r", encoding="utf-8") as f:
                        scanner_js = f.read()
                    page.add_script_tag(content=scanner_js)
                    page.wait_for_timeout(200)

                    # 执行提取
                    real_url = page.evaluate(
                        "(strategy) => window.__apt_scanner_strategies.extractUrl(document, strategy.detailExtract, strategy.base_url)",
                        strategy
                    )

                    if real_url:
                        print(f"✅ [二阶段] 成功提取到真实链接: {real_url}")
                        # 成功抓取后，拟人停留 800~1500 ms
                        page.wait_for_timeout(random.randint(800, 1500))
                        return real_url

                    return None

                except Exception as wait_err:
                    # 如果等元素超时，或者页面报错
                    print(f"⚠️ [二阶段] 页面解析或等待超时: {wait_err}")
                    return None
                finally:
                    if context:
                        context.close()

            except Exception as e:
                print(f"⚠️ [二阶段] Playwright 解析异常: {e}")
                global_limiter.penalize(2.0)
                if context:
                    context.close()
                return None

        try:
            real_url = browser_thread.submit(_do_browser_resolve)
            return real_url
        except Exception as e:
            print(f"⚠️ [二阶段] 浏览器排队解析失败: {e}")
            return None

    def resolve_batch(self, links):
        if not links: return links
        print(f"🔗 [UrlResolver] 开始解析 {len(links)} 条短链...")
        for link in links:
            original_url = link.get("url", "")
            if self._is_short_url(original_url):
                link["url"] = self.resolve_single(original_url, link.get("is_mobile", 0))
        return links