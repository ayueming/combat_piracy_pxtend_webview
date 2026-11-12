# link_resolver.py
import os
import re
import time
import random
import urllib.request

from app_data.crawl_consts import SHORT_URL_PATTERNS, INTERMEDIATE_URL_PATTERNS
from app_data.mobile_consts import DESKTOP_UA, MOBILE_UA
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
        ua = MOBILE_UA if is_mobile else DESKTOP_UA

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
        print(f"🚀 [二阶段·入口] resolve_detail 被调用了: {detail_url}")
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

                # ===== 诊断 1 =====
                print(f"🔍 [二阶段·诊断] detail_url = {detail_url}")
                print(f"🔍 [二阶段·诊断] is_mobile = {is_mobile}")
                print(f"🔍 [二阶段·诊断] strategy keys = {list(strategy.keys()) if strategy else None}")
                detail_cfg = (strategy or {}).get("detailExtract") or {}
                print(f"🔍 [二阶段·诊断] detailExtract = {detail_cfg}")
                print(f"🔍 [二阶段·诊断] base_url = {(strategy or {}).get('base_url')!r}")

                ua = MOBILE_UA if is_mobile else DESKTOP_UA
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

                print(f"🔍 [二阶段] 正在进入详情页: {detail_url}")

                try:
                    page.goto(detail_url, wait_until="domcontentloaded", timeout=15000)
                    print(f"🔍 [二阶段·诊断] 落地 URL = {page.url}")
                    print(f"🔍 [二阶段·诊断] 页面标题 = {page.title()}")

                    wait_selector = detail_cfg.get("wait_selector")
                    print(f"🔍 [二阶段·诊断] wait_selector = {wait_selector!r}")

                    if wait_selector:
                        try:
                            page.wait_for_selector(wait_selector, state="attached", timeout=8000)
                            print(f"✅ [二阶段·诊断] wait_selector 命中")
                        except Exception as we:
                            print(f"⚠️ [二阶段·诊断] wait_selector 超时/未命中: {we}")
                    else:
                        page.wait_for_timeout(1500)

                    js_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search_scanner_strategies.js")
                    with open(js_path, "r", encoding="utf-8") as f:
                        scanner_js = f.read()
                    page.add_script_tag(content=scanner_js)
                    page.wait_for_timeout(500)

                    try:
                        mounted = page.evaluate("typeof window.__apt_scanner_strategies !== 'undefined'")
                        print(f"🔍 [二阶段·诊断] scanner 已挂载 = {mounted}")
                    except Exception as e:
                        print(f"⚠️ [二阶段·诊断] 检查 scanner 挂载失败: {e}")

                    try:
                        hit_count = page.evaluate(
                            "(sel) => document.querySelectorAll(sel).length",
                            wait_selector or ""
                        ) if wait_selector else -1
                        print(f"🔍 [二阶段·诊断] querySelectorAll('{wait_selector}') 命中数 = {hit_count}")
                    except Exception as e:
                        print(f"⚠️ [二阶段·诊断] querySelectorAll 执行失败: {e}")

                    real_url = page.evaluate(
                        "(strategy) => window.__apt_scanner_strategies.extractUrl(document, strategy.detailExtract, strategy.base_url)",
                        strategy
                    )
                    print(f"🔍 [二阶段·诊断] extractUrl 返回 = {real_url!r}")

                    if real_url:
                        print(f"✅ [二阶段] 成功提取到真实链接: {real_url}")
                        page.wait_for_timeout(random.randint(800, 1500))
                        return real_url

                    try:
                        fallback_links = page.evaluate("""
                            () => Array.from(document.querySelectorAll("a[href^='http']"))
                                .filter(a => !a.href.includes("iwangpan.cc"))
                                .slice(0, 10)
                                .map(a => ({ cls: a.className, href: a.href }))
                        """)
                        print(f"🔍 [二阶段·诊断] 详情页站外链接 ({len(fallback_links or [])} 条):")
                        for l in (fallback_links or []):
                            print(f"    cls={l['cls']!r} href={l['href']}")
                    except Exception as e:
                        print(f"⚠️ [二阶段·诊断] 兜底抓站外链接失败: {e}")

                    return None

                except Exception as wait_err:
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

        # ★★★ 这段就是之前漏掉的，必须补回来 ★★★
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