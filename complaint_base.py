# complaint_base.py
import os
import time
import threading
import traceback
from PySide6.QtCore import QObject, Signal

from app_paths import get_user_dir, DIR_TEMP, TEMP_EXCEL_TPL
from db_config import get_current_user
from browser_manager import BrowserManager
from complaint_excel_utils import generate_platform_excel
from complaint_common import take_error_screenshot


class AutoComplaintSignals(QObject):
    progress = Signal(str)
    stage_result = Signal(dict)  #阶段结果结构化信号

    def __init__(self):
        super().__init__()
        # ★ 专门用来装文心拾取结果的池子（因为是串行执行，不需要加锁）
        self.wenxin_pick_stats_buffer = []


class BaseComplaintRunner:
    def __init__(self, complaints_data, signals=None, is_all=False, stop_event=None,
                 login_event=None, dialog_signal=None,
                 expected_uid=None, expected_nickname=None,
                 keep_page_on_failure=True):
        self.complaints_data = complaints_data
        self.signals = signals or AutoComplaintSignals()
        self.is_all = is_all
        self.stop_event = stop_event
        self.login_event = login_event
        self.dialog_signal = dialog_signal

        # ★ 通用登录账号校验参数（None 表示不校验）
        self.expected_uid = str(expected_uid).strip() if expected_uid else None
        self.expected_nickname = (expected_nickname or "").strip() or None

        # 1. 系统级校验
        self.user_id = get_current_user()
        if not self.user_id:
            raise ValueError("❌ 错误：未获取到当前登录用户")
        self.user_dir = get_user_dir(self.user_id)

        # 2. 运行状态初始化
        self.page = None
        self.temp_excel_path = ""
        self.task_success = False
        self.temp_files = []  # ★ 新增：本次运行产生的所有临时文件，run() 结束时统一清理

        # 3. 子类必须覆盖的标识
        self.platform_code = ""
        self.platform_logo = ""

        self._owned_pages = []  # ★ 本任务持有的所有 page
        # ★ 失败是否保留现场页（默认 False：无论成败都回收，保持与原有 finally 逻辑一致）
        self.keep_page_on_failure = keep_page_on_failure

    def log(self, msg):
        """统一日志输出，自动带上平台前缀"""
        full_msg = f"[{self.platform_logo}] {msg}"
        print(full_msg)
        if self.signals:
            self.signals.progress.emit(full_msg)

    def generate_excel(self):
        """生成临时 Excel，统一存放在 DIR_TEMP"""
        self.temp_excel_path = os.path.join(self.user_dir, DIR_TEMP, TEMP_EXCEL_TPL.format(platform=self.platform_code))
        if os.path.exists(self.temp_excel_path):
            try:
                os.remove(self.temp_excel_path)
            except:
                pass

        self.log("正在生成临时 Excel...")
        if not generate_platform_excel(self.complaints_data, self.platform_code, self.temp_excel_path):
            raise RuntimeError("生成临时 Excel 失败")

    def init_browser(self):
        self.log("正在唤醒统一浏览器...")
        try:
            bm = BrowserManager.instance()
            print(f"[Base.init_browser] thread={threading.get_ident()} "
                  f"bm={id(bm)} bt_tid={bm._bt._tid} "
                  f"pw_tid={bm._pw_thread_id} ctx_tid={bm._context_thread_id}")  # 新增
            self.page = bm.new_page()
            print(f"[Base.init_browser] 拿到 page id={id(self.page)} "
                  f"thread={threading.get_ident()}")  # 新增
            self._owned_pages.append(self.page)
        except Exception as e:
            raise RuntimeError(f"浏览器启动失败: {e}")

    def new_owned_page(self, url=None, activate=False):
        """本任务内开一个临时页（归属本任务，run() 结束统一回收）"""
        bm = BrowserManager.instance()
        p = bm.new_page(url=url, force_new=True, activate=activate)
        self._owned_pages.append(p)
        return p

    def close_owned_page(self, page):
        """即时关闭单个临时页，并从清单中摘除（避免 finally 重复关闭）"""
        try:
            BrowserManager.instance().close_page(page)
        except Exception:
            pass
        try:
            self._owned_pages.remove(page)
        except ValueError:
            pass

    def take_screenshot(self):
        """保存报错截图"""
        if self.page and not self.page.is_closed():
            shot_path = take_error_screenshot(self.page, self.user_dir, self.platform_code)
            if shot_path:
                self.log(f"📸 已保存失败现场截图: {shot_path}")

    def wait_for_success_dialog(self, title_text="提交成功", timeout=20000,
                                auto_close=True, required=True) -> bool:
        """
        等待 TDesign/CoDesign 体系的成功弹窗出现。

        参数:
            title_text  : 弹窗标题需要包含的文字（默认"提交成功"）
            timeout     : 等待毫秒
            auto_close  : 是否自动点击【我知道了】关闭弹窗
            required    : True 时超时 raise（让 run() 判定失败）；
                          False 时超时返回 False 不抛异常（仅用于观测）
        """
        self.log(f"⏳ 等待【{title_text}】弹窗...")

        selector = (
            f'.cos-dialog-container:has(.cos-dialog-title:has-text("{title_text}"))'
        )
        dlg = self.page.locator(selector).first

        try:
            dlg.wait_for(state="visible", timeout=timeout)
            self.log(f"✅ 检测到【{title_text}】弹窗，提交成功")

            if auto_close:
                try:
                    ok_btn = dlg.locator(".cos-dialog-ok")
                    if ok_btn.count() > 0:
                        ok_btn.click(force=True, timeout=3000)
                        self.log("👌 已点击【我知道了】关闭弹窗")
                        self.page.wait_for_timeout(600)
                except Exception as e:
                    self.log(f"⚠️ 关闭成功弹窗失败（忽略，不影响结果）: {e}")
            return True

        except Exception as e:
            # 诊断：把当前页面所有 dialog 标题打出来，方便排查"是不是弹了别的失败框"
            try:
                titles = self.page.locator(".cos-dialog-title")
                cnt = titles.count()
                if cnt > 0:
                    texts = [titles.nth(i).inner_text().strip() for i in range(cnt)]
                    diag = f"提交后出现的弹窗标题：{texts}"
                else:
                    diag = "提交后未出现任何弹窗"
            except Exception:
                diag = "提交后弹窗读取失败"

            if required:
                raise RuntimeError(f"未等到【{title_text}】弹窗，{diag}")
            self.log(f"⚠️ 未等到【{title_text}】弹窗，{diag}")
            return False

    # ==================== 登录等待骨架 ====================
    def check_platform_login(self) -> bool:
        """
        ★ 抽象方法：子类必须实现，返回 True 表示已登录，False 表示未登录/掉线
        """
        raise NotImplementedError("子类必须实现 check_platform_login")

    def _match_expected_user(self, actual_uid="", actual_nickname="") -> bool:
        """
        通用登录账号校验。
        - 两个期望都为空 → 直接 True（老调用不受影响）
        - expected_uid 有值 → uid 必须相等（字符串比较，统一 int/str）
        - expected_nickname 有值 → 仅当 actual_nickname 非空时比对，
          取不到昵称时放行（避免平台拿不到昵称误判）
        """
        if self.expected_uid:
            if str(actual_uid or "").strip() != self.expected_uid:
                self.log(f"⚠️ 登录账号不匹配：实际 uid={actual_uid}，期望 uid={self.expected_uid}")
                return False

        if self.expected_nickname and actual_nickname:
            if actual_nickname.strip() != self.expected_nickname:
                self.log(f"⚠️ 登录昵称不匹配：实际={actual_nickname}，期望={self.expected_nickname}")
                return False

        return True

    def wait_for_login(self, timeout_sec=300, reload_on_event=True):
        self.log("⏳ 正在检查账号登录状态...")
        waited = 0
        while True:
            if self.stop_event and self.stop_event.is_set():
                return False

            if self.check_platform_login():
                self.log("✅ 账号已登录！脚本自动接管。")
                if self.dialog_signal:
                    self.dialog_signal.reset_progress()
                return True

            # ★ 核心修改：加入 5 秒静默宽限期
            if waited == 5:
                self.log("⚠️ 检测到未登录/掉线，脚本已挂起...")
                if self.dialog_signal:
                    self.dialog_signal.show_login_required()
                    self.bring_browser_to_front()  # ★ 立刻把浏览器拉到前台

            if self.login_event:
                self.login_event.wait(timeout=1)  # ★ 改为1秒步长，快速轮询
                self.login_event.clear()
            else:
                time.sleep(1)

            waited += 1
            if waited % 10 == 0:
                self.log(f"⏳ 等待登录中...（已等待 {waited} 秒）")

            if waited >= timeout_sec:
                self.log(f"❌ 等待登录超时（{timeout_sec}秒）")
                return False

            if reload_on_event and self.page and not self.page.is_closed():
                try:
                    self.page.wait_for_load_state("networkidle", timeout=3000)
                except:
                    pass

    # ==================== 检测个人信息是否填写 ====================
    def wait_for_info_ready(self, check_ready_func, timeout_sec=300):
        """
        极简等待骨架：等子类的检测函数返回 True
        """
        self.log("⏳ 正在检查表单信息是否齐全...")
        waited = 0
        while True:
            if self.stop_event and self.stop_event.is_set():
                return False

            # 只要子类返回 True（信息齐了），就直接跳出等待
            if check_ready_func():
                self.log("✅ 信息检查通过！")
                if self.dialog_signal:
                    self.dialog_signal.reset_progress()
                return True

            # 如果没齐，第一次挂起并提示
            if waited == 0:
                self.log("⚠️ 发现信息有缺漏，脚本已挂起，请手动补全...")
                if self.dialog_signal:
                    # 假设你在 UI 层新增了这个方法（复用或新增）
                    self.dialog_signal.show_user_handle_required(
                        "请完善网页上的投诉人资料（含证件扫描件），完成后点击下方【已确认填写】按钮。"
                    )
                    self.bring_browser_to_front()

            # 等待用户点击小窗按钮触发 login_event
            if self.login_event:
                self.login_event.wait(timeout=5)
                self.login_event.clear()
            else:
                time.sleep(5)

            waited += 5
            if waited >= timeout_sec:
                self.log(f"❌ 等待填写超时（{timeout_sec}秒）")
                return False

    def bring_browser_to_front(self):
        """把浏览器窗口抢到前台，供登录、扫码、人工接管等场景使用"""
        try:
            bm = BrowserManager.instance()
            bm.bring_to_front(self.page)
        except Exception as e:
            print(f"[{self.platform_logo}] 抢焦点失败（忽略）: {e}")

    # ==================== 生命周期统一入口 ====================
    def run(self):
        """统一执行入口，负责资源回收和现场保护"""
        try:
            self.generate_excel()
            self.init_browser()

            # 调用子类的业务实现
            self.execute_flow()

            # 如果没抛异常，且没返回 False，则视为成功
            self.task_success = True
            self.log("✅ 任务执行成功！")
            return True

        except Exception as e:
            self.log(f"❌ 运行遇到严重错误：{e}")
            self.log(traceback.format_exc())
            self.take_screenshot()
            self.bring_browser_to_front()  #让用户马上看到出错现场
            return False

        finally:
            # 1. 清理临时文件
            if self.temp_excel_path and os.path.exists(self.temp_excel_path):
                try:
                    os.remove(self.temp_excel_path)
                except:
                    pass

            # 2. ★ 清理登记的所有临时文件（不管成败都删）
            for p in self.temp_files:
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                        print(f"[{self.platform_logo}] 🧹 已清理临时文件: {os.path.basename(p)}")
                except Exception:
                    pass

            # 3.页面生命周期：一次性回收
            user_aborted = bool(self.stop_event and self.stop_event.is_set())
            keep_now = self.keep_page_on_failure or user_aborted  # ★ 中断也保留

            try:
                bm = BrowserManager.instance()
                if self.task_success and not user_aborted:
                    # 正常成功：全部关闭
                    alive = [p for p in self._owned_pages if p and not p.is_closed()]
                    if alive:
                        bm.close_pages(alive)
                    self._owned_pages.clear()
                    self.page = None
                elif keep_now:
                    # 失败 / 用户中断：只保留主页面，临时页清理
                    others = [p for p in self._owned_pages
                              if p and p is not self.page and not p.is_closed()]
                    if others:
                        bm.close_pages(others)
                    self._owned_pages = [self.page] if self.page and not self.page.is_closed() else []
                    print(f"[{self.platform_logo}] 🛠 现场已保留（user_aborted={user_aborted}）")
                else:
                    # 失败但不要求保留：全部关闭（原行为）
                    alive = [p for p in self._owned_pages if p and not p.is_closed()]
                    if alive:
                        bm.close_pages(alive)
                    self._owned_pages.clear()
                    self.page = None
            except Exception as e:
                print(f"[{self.platform_logo}] 页面回收失败: {e}")

    def execute_flow(self):
        """★ 抽象方法：子类必须实现具体的业务逻辑"""
        raise NotImplementedError("子类必须实现 execute_flow 方法")