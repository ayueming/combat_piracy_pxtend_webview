# auto_complaint_xunlei.py
import time
import os
import datetime
import calendar
from complaint_base import BaseComplaintRunner, AutoComplaintSignals
from complaint_common import DESC_TEMPLATE
from app_paths import DIR_TEMPLATES, FILE_AUTHORIZATION_PDF

# ★ 平台代号常量
from app_data import PLATFORM_XUNLEI


class XunleiComplaintRunner(BaseComplaintRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.platform_code = PLATFORM_XUNLEI
        self.platform_logo = "迅雷"

    def check_platform_login(self) -> bool:
        """迅雷专属登录检测：通过 URL 判断"""
        try:
            return "login" not in self.page.url
        except Exception:
            return False

    def _wait_for_user_handle(self, error_msg=""):
        """迅雷专属：人工接管挂起逻辑"""
        self.log(f"❌ 运行中断: {error_msg}")
        self.log("🛑 脚本已暂停，未关闭浏览器。请在弹出的窗口中查看当前状态。")
        self.log("👉 您可以手动接管完成后续操作，或者直接点击小窗右上角的 X，程序会自动结束。")

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

    # ==================== ★ 新增：第一步资料检测 ====================
    def _check_info_filled(self) -> bool:
        """纯粹检测第一步的必填项是否都有值"""
        required_inputs = [
            "#contact_user_name",  # 姓名
            "#contact_certificate_number",  # 身份证
            "#contact_phone_number",  # 手机号
            "#contact_email",  # 邮箱
            "#contact_address"  # 地址
        ]

        # 1. 检查 5 个必填文本输入框是否为空
        for selector in required_inputs:
            try:
                val = self.page.locator(selector).input_value()
                if not val or not val.strip():
                    return False
            except Exception:
                return False

        # 2. 检查身份证扫描件是否已存在（看有没有 img.Upload-img 标签）
        try:
            if self.page.locator("#contact_id_card img.Upload-img").count() == 0:
                return False
        except Exception:
            return False

        return True

    # =================================================================

    def execute_flow(self):
        """迅雷核心业务逻辑"""
        first_item = self.complaints_data[0]
        book_name = first_item.get("book", "未知作品").strip("《》")
        proof_file_path = first_item.get("evidence_path", "")

        self.log("正在打开迅雷版权投诉页...")
        self.page.goto("https://copyright.xunlei.com/legal/#/", wait_until="domcontentloaded", timeout=30000)
        self.page.wait_for_timeout(3000)

        # 1. 登录检测
        if not self.wait_for_login(timeout_sec=300):
            return self._wait_for_user_handle("等待登录超时（5分钟）")

        # 2. 进入表单
        self.log("正在点击【同意并发起投诉】...")
        try:
            start_btn = self.page.locator("button:has-text('同意并发起投诉'):visible").first
            start_btn.wait_for(state="visible", timeout=10000)
            start_btn.click()
            self.page.wait_for_url("**/appeal**", timeout=15000)
            self.page.wait_for_timeout(2000)
        except Exception as e:
            return self._wait_for_user_handle(f"无法进入投诉填表页: {e}")

        # ==================== ★ 修改：处理第一步表单 ====================
        self.log("正在检查【第一步：投诉人资料】是否齐全...")

        # 调用基类的通用等待方法，等待信息就绪
        if not self.wait_for_info_ready(self._check_info_filled, timeout_sec=300):
            return self._wait_for_user_handle("等待用户补全投诉人资料超时")

        # 既然走到了这里，说明信息百分之百齐全了，强制点下一步
        self.log("正在点击【下一步】进入填表页...")
        try:
            # 使用你提供的精准 DOM 定位
            next_btn = self.page.locator(".buttons button[type='submit']")
            next_btn.wait_for(state="visible", timeout=5000)
            next_btn.click()

            # 必须等“作品名称”输入框出来（换成精准的 id）
            self.page.locator("#work_name").wait_for(state="visible", timeout=10000)
            self.log("✅ 已进入第二步，开始填写作品信息。")
        except Exception as e:
            return self._wait_for_user_handle(f"点击下一步或等待跳转失败: {e}")
        # =============================================================

        # 3. 填写作品名称
        self.log("正在填写投诉表单...")
        # ★ 修复1：改用精准的 id 定位，替代之前的 placeholder
        self.page.locator("#work_name").fill(book_name)
        self.page.wait_for_timeout(500)

        self.log("-> 选择单选框...")
        # ★ 修复2：利用 Ant Design 的特性，在对应的 id 容器下通过文字精确点击
        self.page.locator("#work_type label:has-text('视听作品')").click()
        self.page.locator("#right_range label:has-text('信息网络传播权')").click()
        self.page.locator("#right_get_method label:has-text('原始取得')").click()

        # 4. 选择服务类型
        web_text = first_item.get("web_text", "")
        if not web_text:
            return self._wait_for_user_handle("web_text 为空，无法确定服务类型")
        try:
            # ★ 修复3：限定在 #illegal_service_type 容器内寻找
            self.page.locator(f"#illegal_service_type label:has-text('{web_text}')").click(timeout=5000)
            self.log(f"✅ 已选择服务类型: {web_text}")
        except Exception as e:
            return self._wait_for_user_handle(f"选择服务类型失败: {e}")

        self.page.wait_for_timeout(500)

        # 5. 上传证明文件
        if proof_file_path and os.path.exists(proof_file_path):
            self.log(f"正在上传权利证明文件: {os.path.basename(proof_file_path)}...")
            try:
                # 定位文件输入框并注入文件
                file_input = self.page.locator("#right_certificate input[type='file']")
                file_input.set_input_files(proof_file_path)

                self.log("等待文件上传完成（观察 PDF 图标出现）...")

                # ★ 修复2：显式等待上传成功的标志（图2里的 PDF 图标）
                # 只要页面上出现了代表 pdf 成功的图标，就说明传完了
                self.page.locator("#right_certificate i.anticon-file-pdf").wait_for(
                    state="visible",
                    timeout=20000  # 给20秒上传时间，防止网络慢
                )
                self.log("✅ 证明文件上传成功并渲染！")

            except Exception as e:
                return self._wait_for_user_handle(f"文件上传超时或失败，请手动检查: {e}")
        else:
            # ★ 修复3：本地没文件时的兜底逻辑
            self.log("⚠️ 本地未找到该作品的证明文件，正在检查网页是否有历史文件...")
            if self.page.locator("#right_certificate i.anticon-file-pdf").count() == 0:
                return self._wait_for_user_handle(
                    f"未找到维权书文件 (路径: {proof_file_path})，且网页无历史文件。请手动上传后继续。")
            else:
                self.log("✅ 网页已保留历史证明文件，跳过上传。")

        # 6. 选择权利保护期
        self.log("正在选择权利保护期...")
        # （严格按照你的要求，这段日期代码完全不动！）
        try:
            today = datetime.date.today()
            next_month = today.month % 12 + 1
            next_year = today.year if today.month < 12 else today.year + 1
            last_day = calendar.monthrange(next_year, next_month)[1]
            end_date_str = f"{next_year}年{next_month}月{last_day}日"

            self.page.locator("span.ant-calendar-picker-input").click()
            self.page.wait_for_selector(".ant-calendar-range", state="visible")
            self.page.locator(".ant-calendar-range-left .ant-calendar-today").click()
            self.page.wait_for_timeout(500)
            self.page.locator(f".ant-calendar-range-right td[title='{end_date_str}']").click()
            self.page.wait_for_timeout(500)
            self.log("✅ 日期选择完成！")
        except Exception as e:
            return self._wait_for_user_handle(f"日期选择失败: {e}")

        # 7. 上传批量链接 Excel
        self.log("正在自动上传批量链接 Excel...")
        try:
            # 这里的逻辑保留，因为它是触发文件选择器，通常比较稳
            with self.page.expect_file_chooser() as fc_info:
                self.page.locator("button:has-text('从Excel导入')").click()
            file_chooser = fc_info.value
            file_chooser.set_files(os.path.abspath(self.temp_excel_path))
            self.log("✅ 文件已上传，等待迅雷解析...")
            self.page.wait_for_timeout(4000)
        except Exception as e:
            return self._wait_for_user_handle(f"自动上传Excel失败: {e}")

        # 8. 填写描述并提交
        self.log("正在填写侵权理由...")
        desc_text = DESC_TEMPLATE.format(name_str=first_item["name_str"], url_str=first_item["url_str"])
        # ★ 修复5：改用精准 id 定位输入框
        self.page.locator("#illegal_reason").fill(desc_text[:800])

        self.log("点击下一步，进入预览页...")
        # ★ 修复6：使用更准确的 type='submit' 定位
        self.page.locator(".buttons button[type='submit']").click()
        self.page.wait_for_timeout(3000)

        self.log("点击确认提交...")
        try:
            submit_btn = self.page.locator("button:has-text('确认提交'):visible").first
            submit_btn.wait_for(state="visible", timeout=10000)
            submit_btn.click()
        except Exception as e:
            return self._wait_for_user_handle(f"找不到确认提交按钮: {e}")

        self.log("等待提交结果...")
        try:
            self.page.wait_for_selector("text='提交成功'", timeout=15000)
            self.log("🎉 迅雷投诉提交成功！本批次完成。")
        except:
            self.log("⚠️ 未能明确检测到成功提示，但已点击提交，请人工确认。")


# ★ 保持调度器调用的接口不变
def run_auto_complaint_xunlei(complaints_data, signals=None, is_all=False, stop_event=None, login_event=None,
                              dialog_signal=None):
    runner = XunleiComplaintRunner(complaints_data, signals, is_all, stop_event, login_event, dialog_signal)
    return runner.run()