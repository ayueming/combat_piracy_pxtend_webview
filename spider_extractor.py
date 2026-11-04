# spider_extractor.py
import re
from urllib.parse import urljoin
from feature_extractor import extract_pure_feature
from app_data.spider_rule_consts import SUPER_CHAPTER_PATTERN, NAVIGATION_BLACKLIST


def _extract_url_fingerprint(url):
    """第一板斧：从 URL 提取 4 位以上的连续数字作为指纹（如 874399631）"""
    if not url:
        return None
    match = re.search(r'(\d{4,})', url)
    return match.group(1) if match else None


def extract_chapters(soup, base_url, book_name, base_cat,
                     src_name, source_id, seen_codes):
    """从 BeautifulSoup 文档中提取章节/简介链接（指纹强过滤版）"""
    chapter_found = 0
    intro_found = 0
    results = []

    # 1. 提取指纹
    fingerprint = _extract_url_fingerprint(base_url)

    # ★ 核心修改：直接扫全页面，彻底抛弃 _find_max_container
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        title = a.get_text(strip=True)

        if not title:
            continue

        if href.startswith("javascript:") or href.startswith("#"):
            continue

        # 2. 指纹强过滤：只要拿到了指纹，但当前链接不包含指纹，直接踢掉（过滤广告和其他书）
        if fingerprint and fingerprint not in href:
            continue

        # 3. 黑名单过滤
        if any(kw in title for kw in NAVIGATION_BLACKLIST):
            continue

        # 4. 判断是否包含数字/中文数字
        is_chapter = bool(re.search(SUPER_CHAPTER_PATTERN, title))

        # 5. 判断是否为简介
        is_intro = False
        if any(kw in title for kw in ["简介", "内容简介", "作品简介", "小说简介"]):
            is_intro = True

        if not (is_chapter or is_intro):
            continue

        # 6. 本次任务内部去重
        code = extract_pure_feature(href)
        if code in seen_codes:
            continue
        seen_codes.add(code)

        if is_intro:
            intro_found += 1
            attr_text = "抓取 内容简介"
        else:
            chapter_found += 1
            attr_text = f"抓取 {title}"

        results.append({
            "book_name": book_name,
            "url": href,
            "category": base_cat,
            "title": title,
            "name": src_name,
            "attr": attr_text,
            "source_id": source_id,
        })

    return chapter_found, intro_found, results