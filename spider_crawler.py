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

        def log(msg):
            print(f"[CRAWL] {msg}", flush=True)

        log(">>> bm._ensure_started_impl start")
        bm._ensure_started_impl(minimized=False)
        log(">>> bm._ensure_started_impl done")

        context = bm._browser.contexts[0]
        log(f">>> got context, pages={len(context.pages)}")

        for db_id, book_name, cat, url, src_name, src_attr in rows_to_crawl:
            domain = urlparse(url).netloc or url
            log(f"=== 开始 {domain} ===")
            if on_progress:
                on_progress(f"加载中: {domain}")

            page = None
            html_content = None
            page_url_snapshot = url
            n = 0
            status = "ok"
            try:
                log(f"step1: global_limiter.wait()")
                global_limiter.wait()

                log(f"step2: context.new_page()")
                page = context.new_page()
                log(f"step2: new_page OK")

                is_mobile = resolve_is_mobile(0, url)
                log(f"step3: set_extra_http_headers (is_mobile={is_mobile})")
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
                log(f"step3: OK")

                log(f"step4: goto start")
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                log(f"step4: goto done")

                log(f"step5: wait_for_timeout(2000)")
                page.wait_for_timeout(2000)
                log(f"step5: OK")

                log(f"step6: content() start")
                html_content = page.content()
                page_url_snapshot = page.url
                log(f"step6: content OK, len={len(html_content)}, final_url={page_url_snapshot[:80]}")

                # ★ 核心：content 后立刻关页，不跨 CPU 密集区
                log(f"step7: page.close() start")
                try:
                    page.close(run_before_unload=False)
                    log(f"step7: page.close OK")
                except Exception as e:
                    log(f"step7: page.close 异常(已忽略): {e}")
                page = None

                log(f"step8: BeautifulSoup 解析 start")
                soup = BeautifulSoup(html_content, "html.parser")
                log(f"step8: soup OK")

                log(f"step9: extract_chapters start")
                seen_codes = set()
                c_count, i_count, results = extract_chapters(
                    soup, page_url_snapshot, book_name, cat,
                    src_name, db_id, seen_codes,
                )
                log(f"step9: extract OK c={c_count} i={i_count} n={len(results)}")

                if c_count == 0 and i_count == 0:
                    log(f"step10: 0 条命中，DOM 解剖")
                    found_a = False
                    for a_tag in soup.find_all("a"):
                        if "第1章" in a_tag.text:
                            log(f"找到 <a> 含 '第1章': {str(a_tag)[:200]}")
                            log(f"  href = {a_tag.get('href')}")
                            found_a = True
                            break
                    if not found_a:
                        log("整个页面无 <a> 含 '第1章'")

                total_chapters += c_count
                total_intros += i_count
                all_results.extend(results)
                n = len(results)
                log(f"step11: 累计完成 n={n}")

            except Exception as e:
                status = "failed"
                n = 0
                import traceback
                log(f"❌ 抓取异常: {e}")
                print(traceback.format_exc(), flush=True)
                global_limiter.penalize(2.0)
            finally:
                if page is not None:
                    try:
                        log(f"finally: 兜底 close")
                        page.close()
                        log(f"finally: close OK")
                    except Exception as e:
                        log(f"finally: close 异常(已忽略): {e}")
                stats.append((book_name or "未关联", cat, domain, n, status))
                log(f"=== 完成 {domain} status={status} n={n} ===\n")

        log(">>> 全部 rows 处理完毕，准备返回")

    try:
        bm.run_in_browser_thread(_do_crawl_in_browser)
    except Exception as e:
        print(f"⚠️ [SpiderCrawler] 浏览器排队解析失败: {e}")
        for db_id, book_name, cat, url, src_name, src_attr in rows_to_crawl:
            domain = urlparse(url).netloc or url
            stats.append((book_name or "未关联", cat, domain, 0, "failed"))

    return total_chapters, total_intros, failed_sites, all_results, stats