# link_resolver.py
import os
import re
import time
import random
import urllib.request

from app_data.crawl_consts import SHORT_URL_PATTERNS, INTERMEDIATE_URL_PATTERNS
from rate_limiter import global_limiter  # ★ 引入全局节拍器


def is_short_url(url) -> bool:
    """命中短链白名单即返回 True（百度短链 / 百度移动端 / 微博中转）"""
    if not url:
        return False
    return any(re.search(p, url) for p in SHORT_URL_PATTERNS)


def is_intermediate_url(url) -> bool:
    """命中中转页白名单即返回 True（如微博 visitor 中转页）。
    中转页不是终点：它是 200 + JS 跳转，urllib 无法继续，需要 Playwright 执行 JS。"""
    if not url:
        return False
    return any(re.search(p, url) for p in INTERMEDIATE_URL_PATTERNS)


class LinkResolver:
    def __init__(self, browser_manager=None):
        self.bm = browser_manager

    def _is_short_url(self, url):
        return is_short_url(url)

    def _is_intermediate_url(self, url):
        return is_intermediate_url(url)

    def _is_unresolved(self, url):
        """URL 仍未解析完成：是短链，或停在中转页"""
        return is_short_url(url) or is_intermediate_url(url)

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
                # ★ 新增：停在中转页（如 visitor）也算未完成，交给 Playwright
                if real_url and not self._is_unresolved(real_url):
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

                    # ★ 一阶段核心：等待 URL 发生变化（既不是短链，也不是中转页）
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=8000)
                        # 最长等 5 秒，一旦离开短链+中转页，立刻打断等待
                        page.wait_for_url(
                            lambda u: not self._is_unresolved(u),
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
                real_url = self.bm.run_in_browser_thread(_do_browser_resolve)
                # ★ 新增：Playwright 结果同样要排除中转页
                if real_url and not self._is_unresolved(real_url):
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
            real_url = self.bm.run_in_browser_thread(_do_browser_resolve)
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