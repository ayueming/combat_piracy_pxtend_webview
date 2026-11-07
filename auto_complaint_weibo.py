# auto_complaint_weibo.py
import time
import os
from complaint_base import BaseComplaintRunner
from app_data import PLATFORM_WEIBO
from feature_extractor import extract_pure_feature


class WeiboComplaintRunner(BaseComplaintRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.platform_code = PLATFORM_WEIBO
        self.platform_logo = "微博"

    def check_platform_login(self) -> bool:
        """微博专属登录检测：通过 URL 和 页面元素判断"""
        try:
            current_url = self.page.url
            # 1. 如果 URL 包含 newlogin，绝对没登录
            if "newlogin" in current_url:
                return False

            # 2. 检查是否有登录框（微博经常会弹出一个悬浮登录框）
            if self.page.locator("input[placeholder*='手机号'], input[placeholder*='账号']").count() > 0:
                if self.page.locator("input[placeholder*='手机号'], input[placeholder*='账号']").first.is_visible():
                    return False

            # 3. 检查是否已经进入业务页面（有投诉相关的 DOM）
            if "service.account.weibo.com" in current_url:
                if self.page.locator("#pl_report_complaint_h5").count() > 0:
                    return True

            return False
        except Exception:
            return False

    def _wait_for_user_handle(self, error_msg=""):
        """人工接管挂起逻辑（仅用于应对易盾滑块）"""
        self.log(f"❌ 运行中断: {error_msg}")
        self.log("🛑 脚本已暂停，未关闭浏览器。请在弹出的窗口中查看当前状态。")
        self.log("👉 您可以在网页上手动完成滑块并提交，提交成功后直接关闭当前标签页即可。")

        self.take_screenshot()

        while True:
            if self.stop_event and self.stop_event.is_set():
                self.log("✅ 检测到用户主动停止信号，任务结束。")
                return
            try:
                if self.page.is_closed():
                    self.log("✅ 检测到当前标签页已关闭，任务结束。")
                    return
            except:
                return
            time.sleep(2)

    def _send_private_message(self, r_uid, item):
        """发送私信（Best-effort，包含原帖链接）"""
        self.log(f"✉️ 尝试给被投诉人 (UID: {r_uid}) 发送私信...")

        book = item.get("book", "").strip("《》")
        aliases = item.get("aliases", "").strip("《》")
        author = item.get("rights_holder", "")
        target_url = item.get("url", "")  # ★ 提取原链接

        # ★ 把原链接拼接到文案最后（加个换行分隔）
        if aliases:
            msg_text = f"你好，我是《{book}》（原名：{aliases}）的作者{author}，以下分享微博，涉及到我的作品，可以麻烦您那边删除吗，删除后烦请回复，方便后续联系，谢谢：\n{target_url}"
        else:
            msg_text = f"你好，我是《{book}》的作者{author}，以下分享微博，涉及到我的作品，可以麻烦您那边删除吗，删除后烦请回复，方便后续联系，谢谢：\n{target_url}"

        original_page = self.page
        dm_page = None
        try:
            # 1. 新建标签页打开用户主页
            dm_page = self.page.context.new_page()
            dm_page.goto(f"https://weibo.com/u/{r_uid}", wait_until="domcontentloaded", timeout=15000)
            dm_page.wait_for_timeout(3000)

            # 2. 找“私信”按钮
            dm_btn = dm_page.locator("span:has-text('私信'), a:has-text('私信')").first
            if dm_btn.count() == 0 or not dm_btn.is_visible():
                self.log(f"⚠️ 主页找不到私信按钮（对方可能设置了拒收），跳过。")
                dm_page.close()
                return

            dm_btn.click()
            dm_page.wait_for_timeout(3000)  # 等私信弹窗/页面加载

            # 3. 定位专属的 textarea 输入框
            msg_box = dm_page.locator("#webchat-textarea")
            msg_box.wait_for(state="visible", timeout=5000)

            # 填入文本：先点击聚焦，再输入
            msg_box.click()
            msg_box.fill(msg_text)

            # 触发前端框架监听
            msg_box.press("Space")
            msg_box.press("Backspace")

            dm_page.wait_for_timeout(500)

            # 4. 按 Enter 发送
            msg_box.press("Enter")
            self.log("✅ 私信发送指令已执行。")
            dm_page.wait_for_timeout(2000)

        except Exception as e:
            self.log(f"⚠️ 私信环节异常（不阻断主任务）: {e}")
        finally:
            if dm_page and not dm_page.is_closed():
                dm_page.close()
            # 切回投诉主页面
            self.page = original_page
            self.page.bring_to_front()

    def execute_flow(self):
        item = self.complaints_data[0]
        target_url = item["url"]

        # 1. 提取纯数字 RID
        rid = extract_pure_feature(target_url)
        if not rid or not rid.isdigit():
            raise RuntimeError(f"无法从链接中提取有效的纯数字 MID: {target_url}")

        self.log(f"✅ 提取到帖子 RID: {rid}")

        # 2. 打开投诉页面
        report_url = f"https://service.account.weibo.com/reportspamobile?rid={rid}&type=1&from=20000"
        self.log("正在打开微博投诉页...")
        self.page.goto(report_url, wait_until="domcontentloaded", timeout=30000)

        # 3. 检查登录状态
        if not self.wait_for_login(timeout_sec=300):
            raise RuntimeError("等待登录超时（5分钟）")

        self.page.wait_for_timeout(2000)

        # 4. 获取被投诉人 UID (用于后续私信)
        r_uid = ""
        try:
            r_uid_el = self.page.locator("#complaint_to_uid")
            if r_uid_el.count() > 0:
                r_uid = r_uid_el.get_attribute("data-value")
        except Exception:
            pass

        # 5. 选择投诉分类（色情低俗 -> 色情导流）
        self.log("正在选择投诉分类：色情低俗 -> 色情导流...")
        try:
            self.page.locator('li[class_id="2"]').click()
            self.page.wait_for_timeout(1000)
            self.page.locator('li[tag_id="201"]').click()
            self.page.wait_for_timeout(1000)
            self.log("✅ 分类选择完成")
        except Exception as e:
            raise RuntimeError(f"选择投诉分类失败: {e}")

        # ======================================================
        # ★ 新增：勾选“我已阅读《微博投诉操作细则》”
        # ======================================================
        self.log("正在勾选《微博投诉操作细则》确认框...")
        try:
            # 定位刚才你发的那段 DOM
            chk_input = self.page.locator('input[node-type="readRoleInput"]')
            chk_input.wait_for(state="attached", timeout=5000)

            # 检查是否已经勾选，如果没有，则强制点击
            if not chk_input.is_checked():
                # 因为微博的勾选框样式是自定义的（隐藏了原生input），直接点 label 或者用 evaluate 点击最有效
                # 方案 1：直接点它外层包裹的 label
                # self.page.locator('dd[node-type="readRoleNode"] label').click()

                # 方案 2：直接执行 JS 点击 input
                chk_input.evaluate("node => node.click()")
                self.page.wait_for_timeout(500)

            self.log("✅ 确认框勾选完成")
        except Exception as e:
            self.log(f"⚠️ 勾选确认框异常（尝试继续提交）: {e}")

        # 6. 提交投诉
        self.log("正在点击提交...")
        try:
            submit_btn = self.page.locator('[node-type="subBtn"]')
            submit_btn.wait_for(state="visible", timeout=5000)

            # ★ 额外小保险：判断提交按钮是不是还是 disabled 状态
            class_attr = submit_btn.get_attribute("class") or ""
            if "m-btn-disabled" in class_attr:
                self.log("⚠️ 提交按钮仍为灰色禁用状态，可能前面有必选项未点中！")

            submit_btn.click()
        except Exception as e:
            raise RuntimeError(f"点击提交按钮失败: {e}")

        # 7. 处理验证与结果
        self.log("⏳ 正在等待提交结果（如果跳出滑块会在此挂起）...")

        success = False
        try:
            # ★ 核心修改：等待你发现的这个成功标识出现，最多等 15 秒
            # 无论是页面跳转还是局部刷新，只要匹配到就立刻通过
            success_tip = self.page.locator('.tipstate:has-text("投诉提交成功")')
            success_tip.wait_for(state="visible", timeout=15000)
            success = True
            self.log("✅ 检测到【投诉提交成功】页面标识！")
        except Exception:
            # 如果没有等到成功页，检查是否有报错弹窗
            if self.page.locator('[node-type="errorNode"]').count() > 0 and self.page.locator(
                    '[node-type="errorNode"]').is_visible():
                err_msg = self.page.locator('[node-type="errorTextNode"]').inner_text()
                raise RuntimeError(f"微博返回错误: {err_msg}")

            # 既没成功也没报错，大概率是弹出了易盾滑块卡住了
            self.log("⚠️ 未检测到成功标识，可能触发了易盾滑块。")

        if not success:
            # 触发滑块，挂起等人工
            return self._wait_for_user_handle("未检测到成功提示，可能触发了滑块验证，请在页面上手动完成。")

        self.log("✅ 微博投诉流程闭环！")

        # 8. 发私信（非强绑定）
        if r_uid:
            self._send_private_message(r_uid, rid, item)
        else:
            self.log("⚠️ 未获取到被投诉人 UID，跳过私信环节。")


# ★ 调度器调用的接口
def run_auto_complaint_weibo(complaints_data, signals=None, is_all=False, stop_event=None, login_event=None,
                             dialog_signal=None):
    runner = WeiboComplaintRunner(complaints_data, signals, is_all, stop_event, login_event, dialog_signal)
    return runner.run()