# auto_complaint_wenxin.py
import os
import time
from urllib.parse import urlparse

from complaint_base import BaseComplaintRunner
from complaint_common import DESC_TEMPLATE
from app_data import PLATFORM_WENXIN
from app_paths import DIR_TEMP
from db_config import get_conn
from feature_extractor import extract_pure_feature
from link_resolver import LinkResolver
from browser_manager import BrowserManager

# ★ 网盘域名映射表
_NETDISK_HOST_MAP = {
    "pan.baidu.com": "百度网盘", "yun.baidu.com": "百度网盘",
    "pan.quark.cn": "夸克网盘",
    "www.aliyundrive.com": "阿里云盘", "www.alipan.com": "阿里云盘",
    "cloud.189.cn": "天翼云盘", "115.com": "115网盘", "115cdn.com": "115网盘",
    "www.123pan.com": "123云盘", "caiyun.139.com": "移动云盘",
    "mega.nz": "MEGA", "drive.google.com": "Google Drive",
}
_NETDISK_FALLBACK_CAT = "其他网盘"


def _resolve_netdisk_category(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return _NETDISK_FALLBACK_CAT
    if not host: return _NETDISK_FALLBACK_CAT
    if host in _NETDISK_HOST_MAP: return _NETDISK_HOST_MAP[host]
    if "lanzou" in host or "lanzt" in host: return "蓝奏云盘"
    return _NETDISK_FALLBACK_CAT


def _is_netdisk_url(url: str) -> bool:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return False
    if not host: return False
    if host in _NETDISK_HOST_MAP: return True
    if "lanzou" in host or "lanzt" in host: return True
    return False


class WenxinAgentComplaintRunner(BaseComplaintRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.platform_code = PLATFORM_WENXIN
        self.platform_logo = "文心智能体"

    def check_platform_login(self) -> bool:
        return True

    # =========================================
    # 1. 扫描并提取页面上的网盘链接（批内去重）
    # =========================================
    def extract_netdisk_urls(self, page) -> list:
        results = []
        seen_codes = set()
        try:
            frame = page.frame_locator('iframe[name="webswan-slave"]')
            paragraphs = frame.locator('p.marklang-paragraph')
            if paragraphs.count() == 0:
                paragraphs = frame.locator('p')

            for i in range(paragraphs.count()):
                p = paragraphs.nth(i)
                try:
                    body = (p.text_content() or "").strip()
                except Exception:
                    body = ""
                anchors = p.locator('a[href]')
                for j in range(anchors.count()):
                    a = anchors.nth(j)
                    try:
                        href = (a.get_attribute('href') or "").strip()
                        text = (a.text_content() or "").strip()
                    except Exception:
                        continue
                    if not href or not _is_netdisk_url(href):
                        continue
                    code = extract_pure_feature(href)
                    if code in seen_codes:
                        continue
                    seen_codes.add(code)
                    results.append({"url": href, "title": text or href, "body": body})
        except Exception as e:
            self.log(f"⚠️ 拾取网盘链接失败（不影响投诉主流程）: {e}")
        return results

    # =========================================
    # 2. 直接入库 sites，不弹窗，只返回统计结果
    # =========================================
    def _save_picked_to_sites(self, item) -> dict:
        picked = self.extract_netdisk_urls(self.page)
        total = len(picked)
        saved = 0
        failed = []
        if total == 0:
            return {"total": 0, "saved": 0, "failed": []}

        self.log(f"📥 本次拾取 {total} 条网盘链接（批内已去重）")

        resolver = LinkResolver(BrowserManager.instance())

        book_name = item.get("book", "")
        target_url = item.get("url", "")  # 父链
        book_id = None
        try:
            conn = get_conn()
            row = conn.execute("SELECT id FROM books WHERE name=?", (book_name,)).fetchone()
            if row: book_id = row[0]
            conn.close()
        except Exception:
            pass

        conn = get_conn()
        try:
            p_code = extract_pure_feature(target_url)
            if p_code:
                conn.execute("UPDATE sites SET picker_status = '已拾取' WHERE feature_code = ?", (p_code,))

            for lk in picked:
                url = lk["url"]
                try:
                    final_url = resolver.resolve_single(url, is_mobile=0) or url
                    code = extract_pure_feature(final_url)
                    if not code:
                        failed.append({"url": url, "reason": "空指纹"})
                        continue

                    cat = _resolve_netdisk_category(final_url)
                    conn.execute(
                        '''INSERT INTO sites
                           (category, target_platform, site_url, feature_code,
                            book_id, book_name, name, attr, parent_url)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',  # ★ 去掉 crawl_status 列
                        (cat, cat, final_url, code,
                         book_id, book_name, lk["title"], "文心自动拾取",
                         target_url),  # ★ 去掉 CRAWL_STATUS_PICKED
                    )
                    saved += 1
                    self.log(f"    ✅ 入库: {lk['title'][:30]} → {final_url[:60]}  (父: {target_url[:50]})")
                except Exception as e:
                    err = str(e)
                    reason = "库中已存在（严格去重）" if "UNIQUE" in err or "IntegrityError" in type(
                        e).__name__ else f"入库失败: {err[:80]}"
                    failed.append({"url": url, "reason": reason})
                    self.log(f"    ⚠️ 跳过: {url[:60]} — {reason}")
            conn.commit()
        finally:
            conn.close()

        return {"total": total, "saved": saved, "failed": failed}

    def get_holder_info(self):
        item = self.complaints_data[0]
        return {
            "name": item.get("rights_holder", ""),
            "phone": item.get("holder_phone", ""),
            "id_card": item.get("holder_id_card", ""),
            "email": item.get("holder_email", ""),
        }

    def execute_flow(self):
        item = self.complaints_data[0]
        target_url = item["url"]
        upload_errors = []

        # 1. 打开页面并穿透 iframe
        self.log(f"正在打开智能体对话页: {target_url}")
        self.page.goto(target_url, wait_until="domcontentloaded", timeout=30000)

        self.log("⏳ 正在等待对话内容渲染...")
        chat_frame = None
        try:
            self.page.wait_for_selector('iframe[name="webswan-slave"]', state="attached", timeout=15000)
            chat_frame = self.page.frame_locator('iframe[name="webswan-slave"]')
            chat_frame.locator(".answer-regenerate-wrapper").wait_for(state="attached", timeout=15000)
            self.log("✅ 渲染完成，找到了【重答】的容器")
        except Exception as e:
            self.log(f"⚠️ 等待对话内容渲染超时，强行往下走... ({e})")
            if not chat_frame:
                chat_frame = self.page.frame_locator('iframe[name="webswan-slave"]')

        self.page.wait_for_timeout(1000)

        # 2. 截图
        screenshot_path = os.path.join(self.user_dir, DIR_TEMP, f"wenxin_shot_{int(time.time())}.png")
        try:
            self.page.screenshot(path=screenshot_path, full_page=True)
            self.temp_files.append(screenshot_path)
        except:
            screenshot_path = ""

        # ======================================================
        # ★ 核心新增：静默拾取并入库（绝不弹窗，结果塞进 buffer）
        # ======================================================
        netdisk_url = ""
        try:
            stats = self._save_picked_to_sites(item)
            stats["book"] = item.get("book", "")
            self.log(f"📊 本批拾取汇总: 共 {stats['total']} 条，入库 {stats['saved']} 条，失败 {len(stats['failed'])} 条")
            if hasattr(self.signals, "wenxin_pick_stats_buffer"):
                self.signals.wenxin_pick_stats_buffer.append(stats)

            # 如果拾取到了，把第一条填到表单里
            if stats["total"] > 0:
                picked = self.extract_netdisk_urls(self.page)
                if picked: netdisk_url = picked[0]["url"]
        except Exception as e:
            self.log(f"⚠️ 拾取/入库环节异常（不影响投诉）: {e}")
            if hasattr(self.signals, "wenxin_pick_stats_buffer"):
                self.signals.wenxin_pick_stats_buffer.append({
                    "book": item.get("book", ""), "total": 0, "saved": 0,
                    "failed": [{"url": target_url, "reason": f"拾取异常: {e}"}],
                })

        # 3. 找到【踩】并点击
        self.log("正在定位并点击【踩】按钮...")
        try:
            thumb_down = chat_frame.locator("div.answer-thumb-container .icon-wrap").last
            thumb_down.scroll_into_view_if_needed()
            self.page.wait_for_timeout(1000)
            thumb_down.evaluate("node => node.click()")
            self.log("✅ 已点击【踩】图标")
            self.page.wait_for_timeout(2000)
        except Exception as e:
            raise RuntimeError(f"点击【踩】失败: {e}")

        # 4. 点击【侵权投诉】
        self.log("正在点击【侵权投诉】...")
        try:
            complaint_btn = chat_frame.locator("div.pc-complaint-btn")
            complaint_btn.wait_for(state="attached", timeout=5000)
            complaint_btn.evaluate("node => node.click()")
            self.log("✅ 已点击【侵权投诉】")
            self.page.wait_for_timeout(3000)
        except Exception as e:
            raise RuntimeError(f"点击【侵权投诉】失败: {e}")

        # 5. 处理可能的新标签页
        if len(self.page.context.pages) > 1:
            self.page = self.page.context.pages[-1]
            self.log("✅ 已切换到投诉表单页")
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(2000)

        # 6. 填写投诉表单...
        self.log("正在填写投诉表单...")
        try:
            self.log("正在设置反馈类型为【著作权】...")
            self.page.locator('.cos-select-entry').click()
            self.page.wait_for_timeout(500)
            option = self.page.locator('.cos-select-option', has_text='侵犯我的著作权')
            option.wait_for(state="visible", timeout=3000)
            option.click()
            self.page.wait_for_timeout(2000)
            self.log("✅ 已设置反馈类型")
        except Exception as e:
            self.log(f"⚠️ 设置反馈类型失败: {e}")

        if netdisk_url:
            try:
                self.page.locator('input[placeholder="请填写侵权内容链接"]').fill(netdisk_url)
                self.log("✅ 已填入侵权内容链接")
            except Exception as e:
                self.log(f"⚠️ 填写侵权内容链接失败: {e}")
        else:
            self.log("⏭️ 未提取到网盘链接，跳过侵权内容链接填写")

        if screenshot_path and os.path.exists(screenshot_path):
            try:
                block = self.page.locator('.form-item').filter(has_text='投诉问题截图')
                block.wait_for(state="attached", timeout=5000)
                with self.page.expect_file_chooser(timeout=10000) as fc_info:
                    block.locator('.upload-container').first.click(force=True)
                fc_info.value.set_files(screenshot_path)
                self.log("✅ 已上传投诉问题截图")
                self.page.wait_for_timeout(1500)
            except Exception as e:
                self.log(f"⚠️ 上传投诉问题截图失败: {e}")
                upload_errors.append(f"投诉问题截图: {e}")

        try:
            name_input = self.page.locator('input[placeholder="请填写被侵权作品名称"]')
            name_input.wait_for(state="visible", timeout=5000)
            name_input.fill(item["book"])
            self.log("✅ 已填入作品名称")
        except Exception as e:
            self.log(f"⚠️ 填写作品名称失败: {e}")

        try:
            desc_text = DESC_TEMPLATE.format(name_str=item["name_str"], url_str=item["url_str"])
            desc_input = self.page.locator('textarea[placeholder="请在投诉描述中说明你的投诉理由"]')
            desc_input.wait_for(state="visible", timeout=5000)
            desc_input.fill(desc_text)
            self.log("✅ 已填入投诉描述")
        except Exception as e:
            self.log(f"⚠️ 填写投诉描述失败: {e}")

        book_files = item.get("book_files", [])
        if book_files:
            try:
                evidence_block = self.page.locator('.form-item').filter(has_text='权属证明材料')
                evidence_block.wait_for(state="attached", timeout=5000)
                base_count = evidence_block.locator('.swiper-item').count()
                uploaded = 0
                for idx, img_path in enumerate(book_files):
                    upload_item = evidence_block.locator('.swiper-upload-item')
                    if upload_item.count() == 0: break
                    upload_container = upload_item.last.locator('.upload-container')
                    upload_container.scroll_into_view_if_needed(timeout=5000)
                    self.page.wait_for_timeout(600)
                    with self.page.expect_file_chooser(timeout=10000) as fc_info:
                        upload_container.evaluate("node => node.click()")
                    fc_info.value.set_files(os.path.abspath(img_path))
                    uploaded += 1
                    self.log(f"✅ 正在上传书籍证明材料 ({uploaded}/{len(book_files)})...")
                    target = base_count + uploaded
                    evidence_block.locator('.swiper-item').nth(target - 1).wait_for(state="attached", timeout=10000)
                    evidence_block.locator('.swiper-item img.swiper-image').nth(target - 1).wait_for(state="attached",
                                                                                                     timeout=5000)
                    self.page.wait_for_timeout(800)
                self.log(f"✅ 已上传书籍证明材料 ({uploaded}张)")
                if uploaded < len(book_files):
                    upload_errors.append(f"书籍材料：需传 {len(book_files)} 张，实际只传了 {uploaded} 张")
            except Exception as e:
                self.log(f"⚠️ 上传书籍证明材料失败: {e}")
                upload_errors.append(f"书籍材料: {e}")

        holder = self.get_holder_info()
        try:
            self.page.locator('input[placeholder="请填写权利人姓名"]').fill(holder["name"])
            self.page.locator('input[placeholder="请填写权利人身份证号码"]').fill(holder["id_card"])
            self.page.locator('input[placeholder="请填写权利人邮箱"]').fill(holder["email"])
            if holder.get("phone"):
                self.page.locator('input[placeholder="请填写权利人联系手机"]').fill(holder["phone"])
            self.log("✅ 已填入隐私信息")
        except Exception as e:
            self.log(f"⚠️ 填写隐私信息失败: {e}")

        holder_files = item.get("holder_files", [])
        if holder_files:
            try:
                id_card_block = self.page.locator('.form-item').filter(has_text='身份证照片')
                id_card_block.wait_for(state="attached", timeout=5000)
                base_count = id_card_block.locator('.swiper-item').count()
                uploaded = 0
                for idx, img_path in enumerate(holder_files):
                    upload_item = id_card_block.locator('.swiper-upload-item')
                    if upload_item.count() == 0: break
                    upload_container = upload_item.last.locator('.upload-container')
                    upload_container.scroll_into_view_if_needed(timeout=5000)
                    self.page.wait_for_timeout(600)
                    with self.page.expect_file_chooser(timeout=10000) as fc_info:
                        upload_container.evaluate("node => node.click()")
                    fc_info.value.set_files(os.path.abspath(img_path))
                    uploaded += 1
                    self.log(f"✅ 正在上传身份证照片 ({uploaded}/{len(holder_files)})...")
                    target = base_count + uploaded
                    id_card_block.locator('.swiper-item').nth(target - 1).wait_for(state="attached", timeout=10000)
                    id_card_block.locator('.swiper-item img.swiper-image').nth(target - 1).wait_for(state="attached",
                                                                                                    timeout=5000)
                    self.page.wait_for_timeout(800)
                self.log(f"✅ 已上传身份证照片 ({uploaded}张)")
                if uploaded < len(holder_files):
                    upload_errors.append(f"身份证照片：需传 {len(holder_files)} 张，实际只传了 {uploaded} 张")
            except Exception as e:
                self.log(f"⚠️ 上传身份证照片失败: {e}")
                upload_errors.append(f"身份证照片: {e}")

        if upload_errors:
            raise RuntimeError(f"存在上传失败项，终止提交：{upload_errors}")

        try:
            self.page.locator(".cos-checkbox").click(force=True)
            self.log("✅ 已勾选承诺")
        except Exception as e:
            self.log(f"⚠️ 勾选承诺失败: {e}")

        self.log("正在点击【提交】...")
        submit_btn = self.page.locator("button.cos-button-primary:has-text('提交')")
        submit_btn.scroll_into_view_if_needed()
        self.page.wait_for_timeout(800)
        cls = submit_btn.get_attribute("class") or ""
        if "cos-disabled" in cls:
            raise RuntimeError("提交按钮仍为禁用状态，说明有必填项未填完整")
        submit_btn.click(force=True)
        self.log("✅ 已点击提交，等待【提交成功】弹窗...")

        self.wait_for_success_dialog("提交成功", timeout=20000, auto_close=True, required=True)
        self.log("✅ 投诉提交成功")


def run_auto_complaint_wenxin(complaints_data, signals=None, is_all=False, stop_event=None, login_event=None,
                              dialog_signal=None):
    runner = WenxinAgentComplaintRunner(complaints_data, signals, is_all, stop_event, login_event, dialog_signal)
    return runner.run()