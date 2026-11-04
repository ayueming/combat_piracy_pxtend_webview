# spider_crawler.py
from urllib.parse import urlparse
from bs4 import BeautifulSoup

from browser_manager import BrowserManager
from rate_limiter import global_limiter
from spider_extractor import extract_chapters
from mobile_url_utils import resolve_is_mobile
from app_data.mobile_consts import MOBILE_UA, DESKTOP_UA


def crawl_sites(rows_to_crawl, on_progress=None):
    """
    抓取网页的入口函数。
    返回: (total_chapters, total_intros, failed_sites, all_results, stats)
      stats: [(book_name, category, domain, n, status), ...]
        - n      = 本链接有效 raw（页内去重后）= results 长度
        - status = "ok" / "failed"
    """
    total_chapters = 0
    total_intros = 0
    failed_sites = []  # 保留字段兼容调用方，不再填充
    all_results = []
    stats = []

    bm = BrowserManager.instance()

    def _do_crawl_in_browser():
        nonlocal total_chapters, total_intros, all_results, stats

        bm._ensure_started_impl(minimized=False)
        context = bm._browser.contexts[0]

        for db_id, book_name, cat, url, src_name, src_attr in rows_to_crawl:
            domain = urlparse(url).netloc or url
            if on_progress:
                on_progress(f"加载中: {domain}")

            page = None
            n = 0
            status = "ok"
            try:
                global_limiter.wait()
                page = context.new_page()

                is_mobile = resolve_is_mobile(0, url)
                if is_mobile:
                    page.set_extra_http_headers({
                        "User-Agent": MOBILE_UA,
                        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
                    })
                    page.set_viewport_size({"width": 390, "height": 844})
                else:
                    page.set_extra_http_headers({
                        "User-Agent": DESKTOP_UA,
                        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
                    })

                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                html_content = page.content()

                print(f"\n🔍 [{domain}] 获取到的 HTML 长度: {len(html_content)}")
                if "第1章" in html_content or "第1节" in html_content:
                    print(f"✅ [{domain}] HTML 中包含了 '第1章' 字样！说明页面加载成功了。")
                else:
                    print(f"❌ [{domain}] HTML 中没有找到 '第1章'，可能被反爬拦截或没加载完。")

                soup = BeautifulSoup(html_content, "html.parser")

                # ★ 方案 A：每条链接独立 seen_codes，仅做页内去重
                seen_codes = set()
                c_count, i_count, results = extract_chapters(
                    soup, url, book_name, cat,
                    src_name, db_id, seen_codes,
                )

                if c_count == 0 and i_count == 0:
                    print(f"\n⚠️ [{domain}] 提取器返回 0 条，开始解剖 DOM...")
                    found_a = False
                    for a_tag in soup.find_all("a"):
                        if "第1章" in a_tag.text:
                            print("🔍 找到了包含 '第1章' 的 <a> 标签，源码如下：")
                            print(str(a_tag))
                            print(f"其 href 属性为: {a_tag.get('href')}")
                            found_a = True
                            break
                    if not found_a:
                        print("❌ 整个页面里没有哪个 <a> 标签包含了 '第1章'。")
                        for tag in soup.find_all(["li", "div", "span", "p"]):
                            if "第1章" in tag.text and len(tag.text) < 50:
                                print("🔍 '第1章' 居然不在 <a> 里，而在以下标签里：")
                                print(str(tag))
                                break

                total_chapters += c_count
                total_intros += i_count
                all_results.extend(results)
                n = len(results)

                # ★ 低命中只打日志，不在 UI 标记
                if n < 5:
                    print(f"[低命中] 《{book_name or '未关联'}》- [{cat}] {domain} 仅 {n} 条")

            except Exception as e:
                status = "failed"
                n = 0
                print(f"抓取异常 [{url}]: {e}")
                global_limiter.penalize(2.0)
            finally:
                if page:
                    try:
                        page.close()
                    except Exception:
                        pass
                stats.append((book_name or "未关联", cat, domain, n, status))

    try:
        bm.run_in_browser_thread(_do_crawl_in_browser)
    except Exception as e:
        print(f"⚠️ [SpiderCrawler] 浏览器排队解析失败: {e}")
        for db_id, book_name, cat, url, src_name, src_attr in rows_to_crawl:
            domain = urlparse(url).netloc or url
            stats.append((book_name or "未关联", cat, domain, 0, "failed"))

    return total_chapters, total_intros, failed_sites, all_results, stats