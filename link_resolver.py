# link_resolver.py
import urllib.request
import time
from browser_thread import browser_thread # ★ 引入你的排队线程

class LinkResolver:
    def __init__(self, browser_manager=None):
        self.bm = browser_manager

    def _is_short_url(self, url):
        if not url: return False
        return "baidu.com/link?url=" in url

    def resolve_single(self, url):
        if not self._is_short_url(url):
            return url

        # 1. urllib 极速尝试
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                real_url = response.geturl()
                if real_url and not self._is_short_url(real_url):
                    return real_url
        except Exception:
            pass

        # 2. Playwright 兜底（必须通过 browser_thread 排队提交）
        if self.bm:
            def _do_browser_resolve():
                try:
                    self.bm._ensure_started_impl()
                    context = self.bm._browser.new_context()
                    page = context.new_page()
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=8000)
                        page.wait_for_timeout(500)
                        return page.url
                    finally:
                        context.close()
                except Exception as e:
                    print(f"⚠️ Playwright 解析异常: {e}")
                    return url

            try:
                # ★ 提交给单线程队列执行，排队等待结果
                real_url = browser_thread.submit(_do_browser_resolve)
                if real_url and not self._is_short_url(real_url):
                    return real_url
            except Exception as e:
                print(f"⚠️ 浏览器排队解析失败: {e}")

        return url # 解析失败，原样返回

    def resolve_batch(self, links):
        # 这里暂时保留批量解析，但后续我们会把它从前端剥离
        if not links: return links
        print(f"🔗 [UrlResolver] 开始解析 {len(links)} 条短链...")
        for link in links:
            original_url = link.get("url", "")
            if self._is_short_url(original_url):
                link["url"] = self.resolve_single(original_url)
        return links