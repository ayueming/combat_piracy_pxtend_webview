# auto_complaint_weibo.py
import re
import time
from complaint_base import BaseComplaintRunner
from app_data import PLATFORM_WEIBO
from feature_extractor import extract_pure_feature


class WeiboComplaintRunner(BaseComplaintRunner):
    def __init__(self, *args, modes=None, expected_uid=None, expected_nickname=None, **kwargs):
        super().__init__(*args, expected_uid=expected_uid,
                         expected_nickname=expected_nickname, **kwargs)
        self.platform_code = PLATFORM_WEIBO
        self.platform_logo = "微博"
        self.modes = tuple(modes or ("report", "msg"))
        self.result = {}

    # ============ 登录检测（不变） ============
    def check_platform_login(self) -> bool:
        try:
            page = self.page
            cur = (page.url or "").lower()

            # A. 服务号域：有投诉表单即视为已登录（保留）
            if "service.account.weibo.com" in cur:
                try:
                    if page.locator("#pl_report_complaint_h5").count() > 0:
                        return True
                except Exception:
                    pass

            # B. 主站：读 $CONFIG.user
            info = None
            try:
                info = page.evaluate(
                    """() => {
                        const c = window.$CONFIG;
                        if (!c) return null;
                        const u = c.user || null;
                        const uid = (u && (u.id || u.idstr)) || c.uid || '';
                        const name = (u && u.screen_name) || '';
                        return { id: String(uid || ''), name: String(name || '') };
                    }"""
                )
            except Exception:
                info = None

            if info and info.get("id") and info["id"] != "0":
                # ★ 交给 base 做通用校验
                return self._match_expected_user(info["id"], info.get("name", ""))

            # C. 明确登录页 → 未登录
            if "newlogin" in cur or "passport.weibo" in cur or "login.sina" in cur:
                return False

            # D. 兜底（$CONFIG 缺失时）
            try:
                href = page.evaluate(
                    """() => {
                        const a = document.querySelector('a[href^="/u/"]');
                        return a ? a.getAttribute('href') : '';
                    }"""
                )
                m = re.search(r"/u/(\d+)", href or "")
                if m:
                    return self._match_expected_user(m.group(1), "")
            except Exception:
                pass
            return True
        except Exception:
            return False

    # ============ uid 解析：weibo.com + m.weibo.cn 双套 ============
    def _resolve_r_uid(self, item) -> str:
        """新 tab 打开原帖解析博主 uid，不污染主页面。失败返回 ''"""
        url = item.get("url", "")
        tmp = None
        try:
            tmp = self.page.context.new_page()
            tmp.goto(url, wait_until="domcontentloaded", timeout=20000)
            tmp.wait_for_timeout(2500)

            # 1) weibo.com 详情页：header 里作者主页链接
            href = tmp.evaluate("""() => {
                const a = document.querySelector('article header a[href*="weibo.com/u/"]')
                       || document.querySelector('header a[href*="weibo.com/u/"]');
                return a ? a.getAttribute('href') : '';
            }""")
            m = re.search(r"/u/(\d+)", href or "")
            if m:
                return m.group(1)

            # 2) m.weibo.cn：$render_data
            uid = tmp.evaluate("""() => {
                try {
                    const d = window.$render_data;
                    if (d && d[0] && d[0].status && d[0].status.user)
                        return String(d[0].status.user.id);
                } catch(e){}
                return '';
            }""")
            if uid and uid.isdigit():
                return uid

            # 3) m.weibo.cn：header 头像 /profile/{uid}
            href = tmp.evaluate("""() => {
                const a = document.querySelector('header.weibo-top .m-avatar-box a[href^="/profile/"]')
                       || document.querySelector('.weibo-top a[href^="/profile/"]');
                return a ? a.getAttribute('href') : '';
            }""")
            m = re.search(r"/profile/(\d+)", href or "")
            if m:
                return m.group(1)

            return ""
        except Exception as e:
            self.log(f"⚠️ 解析原帖 uid 失败: {e}")
            return ""
        finally:
            if tmp and not tmp.is_closed():
                tmp.close()

    # ============ 阶段一：举报 ============
    def _do_report(self, rid: str):
        report_url = (
            f"https://service.account.weibo.com/reportspamobile"
            f"?rid={rid}&type=1&from=20000"
        )
        self.log("正在打开微博投诉页...")
        self._goto_report(report_url)

        if not self.wait_for_login(timeout_sec=300):
            raise RuntimeError("等待登录超时（5分钟）")

        # ★ 登录后可能被弹到 weibo.com 首页/用户主页，重新回到投诉页
        if not self._ensure_on_report_page(report_url):
            self.log("登录后不在投诉页，重新打开投诉页...")
            self._goto_report(report_url)
            if not self._ensure_on_report_page(report_url):
                self.log("仍不在投诉页，再重试一次...")
                self._goto_report(report_url)

        self.page.wait_for_timeout(2000)

        # 读 uid（兜底）
        uid_from_report = ""
        try:
            el = self.page.locator("#complaint_to_uid")
            if el.count() > 0:
                uid_from_report = el.get_attribute("data-value") or ""
        except Exception:
            pass

        # 选分类：色情低俗 -> 色情导流
        self.log("正在选择投诉分类：色情低俗 -> 色情导流...")
        try:
            # ① 折叠时先点“展开全部分类”
            self._expand_categories_if_needed()

            # ② 一级分类：色情低俗（class_id=2）
            cat = self.page.locator('li[class_id="2"]')
            cat.first.wait_for(state="visible", timeout=8000)
            cat.first.click()
            self.page.wait_for_timeout(800)  # 等子分类渲染

            # ③ 二级分类：色情导流（tag_id=201）—— 子分类也可能折叠，再兜一次
            sub = self.page.locator('li[tag_id="201"]')
            if sub.count() == 0 or not sub.first.is_visible():
                self._expand_categories_if_needed()
            sub.first.wait_for(state="visible", timeout=8000)
            sub.first.click()
            self.page.wait_for_timeout(800)

            self.log("✅ 分类选择完成")
        except Exception as e:
            raise RuntimeError(f"选择投诉分类失败: {e}")

        # 勾选《微博投诉操作细则》
        self.log("正在勾选《微博投诉操作细则》确认框...")
        try:
            chk = self.page.locator('input[node-type="readRoleInput"]')
            chk.wait_for(state="attached", timeout=5000)
            if not chk.is_checked():
                chk.evaluate("node => node.click()")
                self.page.wait_for_timeout(500)
            self.log("✅ 确认框勾选完成")
        except Exception as e:
            self.log(f"⚠️ 勾选确认框异常（尝试继续提交）: {e}")

        # 提交
        self.log("正在点击提交...")
        try:
            submit_btn = self.page.locator('[node-type="subBtn"]')
            submit_btn.wait_for(state="visible", timeout=5000)
            cls = submit_btn.get_attribute("class") or ""
            if "m-btn-disabled" in cls:
                self.log("⚠️ 提交按钮仍为灰色禁用状态，可能前面有必选项未点中！")
            submit_btn.click()
        except Exception as e:
            raise RuntimeError(f"点击提交按钮失败: {e}")

        # ============ 5s 内等三个成功标志 ============
        self.log("⏳ 等待提交结果（5s）...")
        success_sel = (
            '.tipstate:has-text("投诉提交成功"), '
            '.tipstate:has-text("该内容已被投诉"), '
            'h3[node-type="succTextNode"]:has-text("投诉已提交过")'
        )
        try:
            tip = self.page.locator(success_sel).first
            tip.wait_for(state="visible", timeout=5000)
            text = (tip.inner_text() or "").strip()
            self.log(f"✅ 检测到结果标识：{text}")
            status = "ALREADY" if ("已提交过" in text or "已被投诉" in text) else "OK"
            return status, uid_from_report
        except Exception:
            # 5s 没出现 → 看一眼是否有明确错误弹窗
            try:
                err = self.page.locator('[node-type="errorNode"]')
                if err.count() > 0 and err.is_visible():
                    msg = self.page.locator('[node-type="errorTextNode"]').inner_text()
                    self.log(f"❌ 微博返回错误: {msg}")
            except Exception:
                pass
            self.log("⚠️ 阶段一失败：5s 内未出现任何成功/已投诉标识")
            return "FAILED", uid_from_report

    def _goto_report(self, report_url: str):
        self.page.goto(report_url, wait_until="domcontentloaded", timeout=30000)

    def _ensure_on_report_page(self, report_url: str, timeout_ms: int = 8000) -> bool:
        cur = self.page.url or ""
        if report_url.split("?")[0] not in cur:
            return False
        try:
            self.page.locator(
                '#pl_report_complaint_h5, li[class_id="2"]'
            ).first.wait_for(state="visible", timeout=timeout_ms)
            return True
        except Exception:
            return False

    def _expand_categories_if_needed(self, timeout_ms: int = 5000) -> bool:
        """
        检测分类列表是否折叠；若有『展开全部分类』按钮则点击。
        返回 True 表示执行了展开点击。
        """
        page = self.page
        try:
            expand_btn = page.locator(
                '#showAllClassOnce, '
                '[action-type="showAllClass"], '
                'a:has-text("展开全部分类")'
            ).first

            if expand_btn.count() == 0:
                return False
            try:
                if not expand_btn.is_visible():
                    return False
            except Exception:
                return False

            # 已经能看到目标子分类就不用点了
            try:
                target = page.locator('li[tag_id="201"]').first
                if target.count() > 0 and target.is_visible():
                    return False
            except Exception:
                pass

            self.log("检测到分类列表折叠，点击『展开全部分类』...")
            try:
                expand_btn.click()
            except Exception:
                # 兜底：JS 直接触发点击
                expand_btn.evaluate("node => node.click()")

            page.wait_for_timeout(800)  # 等展开动画
            try:
                page.locator('li[class_id="2"]').first.wait_for(
                    state="visible", timeout=timeout_ms
                )
            except Exception:
                pass
            return True
        except Exception as e:
            self.log(f"⚠️ 展开分类异常（继续尝试后续步骤）: {e}")
            return False

    # ============ 阶段二：私信 ============
    def _do_private_message(self, r_uid: str, item) -> str:
        """
        直接拼 api.weibo.com/chat 指定对话，发出去（textarea 清空）即算成功。
        返回 "OK" / "FAILED"
        """
        self.log(f"✉️ 给被投诉人 (UID: {r_uid}) 发送私信...")

        book = item.get("book", "").strip("《》")
        aliases = item.get("aliases", "").strip("《》")
        author = item.get("rights_holder", "")
        target_url = item.get("url", "")

        if aliases:
            msg_text = (
                f"你好，我是《{book}》（原名：{aliases}）的作者{author}，"
                f"以下分享微博，涉及到我的作品，可以麻烦您那边删除吗，"
                f"删除后烦请回复，方便后续联系，谢谢：\n{target_url}"
            )
        else:
            msg_text = (
                f"你好，我是《{book}》的作者{author}，"
                f"以下分享微博，涉及到我的作品，可以麻烦您那边删除吗，"
                f"删除后烦请回复，方便后续联系，谢谢：\n{target_url}"
            )

        original_page = self.page
        dm_page = None
        try:
            dm_page = self.page.context.new_page()
            chat_url = (
                f"https://api.weibo.com/chat/#/chat?to_uid={r_uid}&source_from="
            )
            dm_page.goto(chat_url, wait_until="domcontentloaded", timeout=20000)
            dm_page.wait_for_timeout(3000)

            # 未登录兜底：跳转登录页直接判失败
            cur_url = dm_page.url or ""
            if "newlogin" in cur_url or "passport.weibo" in cur_url:
                self.log("⚠️ 私信页跳转到登录页，说明未登录，私信失败")
                return "FAILED"

            # 等输入框：先等 #webchat-textarea，再降级
            msg_box = dm_page.locator("#webchat-textarea")
            try:
                msg_box.wait_for(state="visible", timeout=10000)
            except Exception:
                for sel in [
                    "textarea.editor",
                    "textarea.textarea-content",
                    "textarea",
                ]:
                    loc = dm_page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        msg_box = loc.first
                        self.log(f"✅ 降级命中私信输入框: {sel}")
                        break
                else:
                    self.log("⚠️ 找不到私信输入框（对方 uid 无效或页面异常）")
                    return "FAILED"

            # 填文本 + 触发 Vue 监听
            msg_box.click()
            msg_box.fill(msg_text)
            try:
                msg_box.press("Space")
                msg_box.press("Backspace")
            except Exception:
                pass
            dm_page.wait_for_timeout(500)

            # Enter 发送（页面提示：按下Enter发送内容）
            msg_box.press("Enter")

            # 验证：轮询 textarea 是否清空（最多 5s）
            sent_ok = False
            for _ in range(10):
                dm_page.wait_for_timeout(500)
                try:
                    val = dm_page.locator("#webchat-textarea").input_value(
                        timeout=1000
                    )
                except Exception:
                    val = ""
                if not val or not val.strip():
                    sent_ok = True
                    break

            if sent_ok:
                self.log("✅ 私信已发送（输入框已清空）")
                return "OK"
            self.log("⚠️ 私信输入框未清空，可能未发送成功")
            return "FAILED"

        except Exception as e:
            self.log(f"⚠️ 私信环节异常: {e}")
            return "FAILED"
        finally:
            if dm_page and not dm_page.is_closed():
                dm_page.close()
            self.page = original_page
            try:
                self.page.bring_to_front()
            except Exception:
                pass

    # ============ 编排 ============
    def execute_flow(self):
        item = self.complaints_data[0]
        target_url = item["url"]

        rid = extract_pure_feature(target_url)
        if not rid or not rid.isdigit():
            raise RuntimeError(f"无法从链接中提取有效的纯数字 MID: {target_url}")
        self.log(f"✅ 提取到帖子 RID: {rid}")

        result = {"rid": rid, "r_uid": "", "report": None, "msg": None}

        # ---- 阶段一：举报 ----
        if "report" in self.modes:
            try:
                status, uid_from_report = self._do_report(rid)
                result["report"] = status
                if uid_from_report:
                    result["r_uid"] = uid_from_report
            except Exception as e:
                self.log(f"❌ 举报阶段异常（不阻断私信）: {e}")
                result["report"] = "FAILED"
            self.signals.stage_result.emit(
                {"platform": "weibo", "stage": "report",
                 "status": result["report"], "rid": rid}
            )

        # ---- 阶段二：私信 ----
        if "msg" in self.modes:
            r_uid = result["r_uid"] or self._resolve_r_uid(item)
            result["r_uid"] = r_uid
            if not r_uid:
                self.log("❌ 无法获取被投诉人 uid，私信阶段失败")
                result["msg"] = "FAILED"
            else:
                result["msg"] = self._do_private_message(r_uid, item)
            self.signals.stage_result.emit(
                {"platform": "weibo", "stage": "msg",
                 "status": result["msg"], "rid": rid, "r_uid": r_uid}
            )

        self.result = result
        self.signals.stage_result.emit({"platform": "weibo", **result})
        self.log(f"📊 本批结果: report={result['report']} msg={result['msg']}")

        # ---- 本批成败判定 ----
        if "msg" in self.modes:
            # 有私信阶段：私信 OK 才算本批成功；report 失败不影响
            return result["msg"] == "OK"
        else:
            # 只举报：举报 OK/ALREADY 才算成功
            return result["report"] in ("OK", "ALREADY")


def run_auto_complaint_weibo(
    complaints_data, signals=None, is_all=False,
    stop_event=None, login_event=None, dialog_signal=None, modes=None,
    expected_uid=None, expected_nickname=None,   # ★ 新增，可留空
):
    runner = WeiboComplaintRunner(
        complaints_data, signals, is_all, stop_event,
        login_event, dialog_signal, modes=modes,
        expected_uid=expected_uid, expected_nickname=expected_nickname,
    )
    return runner.run()