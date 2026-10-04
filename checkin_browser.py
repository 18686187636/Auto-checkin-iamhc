#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
iamhc 纯浏览器自动签到 v9
- 若按钮是"立即签到" → 点击 → 处理 CF → 读取奖励
- 若按钮已是"已签到" → 直接读奖励，无需点击
- 金额提取优先匹配特定的 <p> 标签，避免抓取到累计金额
"""

import os, sys, time, json, re, requests
from datetime import datetime, timezone, timedelta

EMAIL        = os.environ.get("EMAIL") or ""
PASSWORD     = os.environ.get("PASSWORD") or ""
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://api.hcnsec.cn"
TZ_CN = timezone(timedelta(hours=8))

LOGIN_URL  = f"{BASE_URL}/sign-in"
LOGIN_PATH = "/sign-in"
PROFILE_URL = f"{BASE_URL}/profile"


# ===========================================================================
# 通知
# ===========================================================================
def send_notification(message):
    print("\n" + "=" * 30)
    print(message)
    print("=" * 30)
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
            print("Telegram:", "成功" if r.status_code == 200 else f"失败 {r.status_code}")
        except Exception as e:
            print("Telegram 异常:", e)


# ===========================================================================
# 精确读取奖励金额（修正版）
# ===========================================================================
def read_award_amount(sb):
    """精确读取页面上的'今天 +¥XX.XX'，优先匹配特定的 <p> 标签"""
    award_text = sb.execute_script("""
        (function() {
            // 优先精确匹配用户提供的 <p> 结构
            const precise = document.querySelector(
                'p.text-muted-foreground.mt-1.line-clamp-2, p[class*="line-clamp-2"][class*="text-muted-foreground"]'
            );
            if (precise) {
                const t = (precise.textContent || '').trim();
                if (t.includes('今天') && t.includes('¥')) {
                    return t;
                }
            }
            // 备选：更宽泛的 class 匹配
            const candidates = document.querySelectorAll(
                'p.text-muted-foreground, p[class*="text-muted-foreground"], [class*="line-clamp-2"]'
            );
            for (let el of candidates) {
                const t = (el.textContent || '').trim();
                if (t.includes('今天') && t.includes('¥')) {
                    return t;
                }
            }
            // 最后备选：全页扫描叶子节点
            const all = document.querySelectorAll('p, span, div');
            for (let el of all) {
                if (el.children.length > 0) continue;
                const t = (el.textContent || '').trim();
                if (t.length < 30 && /今天\\s*\\+\\s*¥/.test(t)) {
                    return t;
                }
            }
            return '';
        })()
    """)
    amount = ""
    m = re.search(r'[+＋]\s*¥\s*([\d.]+)', award_text or "")
    if m:
        amount = m.group(1)
    return award_text or "", amount


# ===========================================================================
# 浏览器流程
# ===========================================================================
def browser_checkin():
    from seleniumbase import SB

    result = {
        "logged_in": False,
        "already_done": False,
        "checkin_clicked": False,
        "button_before": "",
        "button_after": "",
        "toast": "",
        "award_text": "",
        "award_amount": "",
        "error": "",
    }

    print("🚀 启动浏览器（UC 模式）…")
    with SB(
        uc=True,
        headed=True,
        xvfb=False,
        incognito=True,
        locale_code="zh-CN",
        window_size="1920,1080",
        chromium_arg="--no-sandbox,--disable-dev-shm-usage,--disable-gpu",
    ) as sb:
        try:
            # -----------------------------------------------------------
            # 1. 登录
            # -----------------------------------------------------------
            print(f"🌐 打开登录页: {LOGIN_URL}")
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            sb.sleep(3)

            print("⏳ 等待登录表单…")
            sb.wait_for_element_visible("input[name='username']", timeout=30)
            sb.wait_for_element_visible("input[name='password']", timeout=10)

            print("✍️ 填写登录凭证…")
            sb.type("input[name='username']", EMAIL)
            sb.sleep(0.5)
            sb.type("input[name='password']", PASSWORD)
            sb.sleep(0.5)

            print("☑️ 勾选法律同意…")
            consent = "span[role='checkbox'][aria-labelledby='legal-consent-label']"
            try:
                sb.wait_for_element_present(consent, timeout=10)
                if sb.get_attribute(consent, "aria-checked") != "true":
                    sb.click(consent)
                    sb.sleep(0.8)
            except Exception as e:
                print(f"⚠️ 勾选异常: {e}")

            print("⏳ 等待登录 Turnstile…")
            sb.sleep(5)
            try:
                sb.uc_gui_click_captcha()
            except Exception as e:
                print(f"  click_captcha 异常: {e}")

            for i in range(60):
                tk = sb.execute_script(
                    'return (document.querySelector(\'[name="cf-turnstile-response"]\') || {}).value || "";'
                )
                if tk:
                    print(f"✅ 登录 Turnstile token 长度: {len(tk)}")
                    break
                sb.sleep(1)

            print("🖱️ 点击登录按钮…")
            sb.wait_for_element_visible("button[type='submit']", timeout=10)
            sb.click("button[type='submit']")

            print("⏳ 等待登录跳转…")
            for i in range(30):
                sb.sleep(2)
                cur = sb.get_current_url()
                if LOGIN_PATH not in cur:
                    result["logged_in"] = True
                    print(f"✅ 登录成功: {cur}")
                    break
            if not result["logged_in"]:
                print("❌ 登录未跳转")
                sb.save_screenshot("login_failed.png")
                result["error"] = "登录失败"
                return result

            sb.sleep(3)

            # -----------------------------------------------------------
            # 2. 打开个人资料页
            # -----------------------------------------------------------
            print("📄 打开个人资料页面…")
            sb.open(PROFILE_URL)
            sb.sleep(4)
            cur = sb.get_current_url()
            print(f"  当前页面: {cur}")
            if "sign-in" in cur or "login" in cur:
                result["error"] = "登录状态失效"
                return result

            # -----------------------------------------------------------
            # 3. 检测按钮当前状态
            # -----------------------------------------------------------
            print("🔍 检测签到按钮状态…")
            btn_info = sb.execute_script("""
                (function() {
                    const elems = document.querySelectorAll('button');
                    let immediate = null;
                    let done = null;
                    for (let e of elems) {
                        const t = (e.textContent || '').trim();
                        if (t === '立即签到' || t.startsWith('立即签到')) {
                            immediate = {el: e, text: t};
                            break;
                        }
                        if (t.includes('已签到') && !done) {
                            done = {el: e, text: t};
                        }
                    }
                    if (immediate) {
                        immediate.el.setAttribute('data-checkin-target', '1');
                        return JSON.stringify({state: 'ready', text: immediate.text});
                    }
                    if (done) {
                        return JSON.stringify({state: 'already', text: done.text});
                    }
                    return JSON.stringify({
                        state: 'not_found',
                        all_buttons: Array.from(elems)
                            .map(e => (e.textContent || '').trim())
                            .filter(t => t && t.length < 50)
                    });
                })()
            """)
            print(f"  按钮状态: {btn_info}")

            try:
                info = json.loads(btn_info)
            except Exception:
                info = {"state": "not_found"}

            state = info.get("state", "not_found")
            result["button_before"] = info.get("text", "")

            # -----------------------------------------------------------
            # 4. 已签到 → 直接读金额
            # -----------------------------------------------------------
            if state == "already":
                print(f"✅ 今日已签到 | 按钮: '{info.get('text')}'")
                result["already_done"] = True
                result["checkin_clicked"] = True
                result["button_after"] = info.get("text", "")

                award_text, award_amount = read_award_amount(sb)
                result["award_text"] = award_text
                result["award_amount"] = award_amount
                print(f"  金额文字: '{award_text}' | 提取: +¥{award_amount}")

                try:
                    sb.save_screenshot("checkin_result.png")
                except Exception:
                    pass
                return result

            # -----------------------------------------------------------
            # 5. 未找到按钮
            # -----------------------------------------------------------
            if state == "not_found":
                print(f"⚠️ 未找到签到按钮")
                print(f"  所有按钮: {info.get('all_buttons')}")
                result["error"] = "未找到签到按钮"
                sb.save_screenshot("no_checkin_button.png")
                return result

            # -----------------------------------------------------------
            # 6. 按钮是"立即签到" → 执行签到
            # -----------------------------------------------------------
            print(f"  ✅ 找到'立即签到'按钮，开始签到…")
            sb.uc_click("[data-checkin-target='1']", reconnect_time=2)
            result["checkin_clicked"] = True
            print("✅ 已点击")

            print("⏳ 等待 CF 弹窗渲染（3 秒）…")
            sb.sleep(3)

            print("🔐 尝试处理 CF 验证…")
            for attempt in range(3):
                try:
                    sb.uc_gui_click_captcha()
                except Exception:
                    pass
                sb.sleep(3)

            print("⏳ 等待签到请求完成（15 秒）…")
            sb.sleep(15)

            # -----------------------------------------------------------
            # 7. 读取金额 + 按钮状态
            # -----------------------------------------------------------
            print("💰 读取签到奖励金额…")
            award_text, award_amount = read_award_amount(sb)
            result["award_text"] = award_text
            result["award_amount"] = award_amount
            print(f"  金额文字: '{award_text}' | 提取: +¥{award_amount}")

            print("🔍 检查按钮状态…")
            btn_state = sb.execute_script("""
                (function() {
                    const elems = document.querySelectorAll('button');
                    for (let e of elems) {
                        const t = (e.textContent || '').trim();
                        if (t.includes('已签到') || t === '立即签到' || t.startsWith('立即签到')) {
                            return t;
                        }
                    }
                    return '';
                })()
            """)
            print(f"  按钮文字: '{btn_state}'")
            result["button_after"] = btn_state

            try:
                sb.save_screenshot("checkin_result.png")
            except Exception:
                pass

            return result

        except Exception as e:
            print(f"❌ 浏览器异常: {e}")
            result["error"] = str(e)
            try:
                sb.save_screenshot("browser_error.png")
            except Exception:
                pass
            return result


# ===========================================================================
# 主流程
# ===========================================================================
def main():
    if not EMAIL or not PASSWORD:
        print("❌ 请设置 EMAIL 和 PASSWORD 环境变量")
        sys.exit(1)

    now = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    r = browser_checkin()

    if not r.get("logged_in"):
        msg = f"❌ iamhc 登录失败\n⏱️ {now}"
        print(msg)
        send_notification(msg)
        sys.exit(1)

    if not r.get("checkin_clicked"):
        msg = (f"⚠️ iamhc 登录成功但未找到签到按钮\n"
               f"📌 {r.get('error', '')}\n⏱️ {now}\n{BASE_URL}")
        print(msg)
        send_notification(msg)
        sys.exit(1)

    already = r.get("already_done", False)
    btn_after = str(r.get("button_after", ""))
    amount = r.get("award_amount", "")
    award_text = r.get("award_text", "")

    if amount:
        amount_line = f"💰 本次奖励: +¥{amount}\n"
    elif award_text:
        amount_line = f"💰 奖励信息: {award_text}\n"
    else:
        amount_line = ""

    success = already or ("已签到" in btn_after)

    if success:
        title = "✅ 今日已签到" if already else "✅ 签到成功"
        msg = (f"🎁 iamhc 签到通知\n\n"
               f"{title}\n"
               f"{amount_line}"
               f"🖱️ 按钮状态: {btn_after}\n"
               f"⏱️ {now}\n{BASE_URL}")
    else:
        msg = (f"🎁 iamhc 签到通知\n\n"
               f"⚠️ 签到结果未确认\n"
               f"{amount_line}"
               f"🖱️ 按钮状态: {btn_after}\n"
               f"⏱️ {now}\n{BASE_URL}")

    print(msg)
    send_notification(msg)


if __name__ == "__main__":
    main()
