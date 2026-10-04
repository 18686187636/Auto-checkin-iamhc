#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
iamhc 纯浏览器自动签到
流程：浏览器打开登录页 → 填账号密码 → 勾选同意 → 过 Turnstile → 登录
     → 打开控制台 → 点"签到"按钮 → 过 Turnstile → 读取结果
"""

import os, sys, time, json, requests
from datetime import datetime, timezone, timedelta

EMAIL        = os.environ.get("EMAIL") or ""
PASSWORD     = os.environ.get("PASSWORD") or ""
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://api.hcnsec.cn"
TZ_CN = timezone(timedelta(hours=8))

LOGIN_URL  = f"{BASE_URL}/sign-in"
LOGIN_PATH = "/sign-in"


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
# 浏览器流程
# ===========================================================================
def browser_checkin():
    from seleniumbase import SB

    result = {
        "logged_in": False,
        "checkin_clicked": False,
        "button_text": "",
        "toast": "",
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
            # ===========================================================
            # 1. 打开登录页
            # ===========================================================
            print(f"🌐 打开登录页: {LOGIN_URL}")
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            sb.sleep(3)

            # ===========================================================
            # 2. 等待表单
            # ===========================================================
            print("⏳ 等待登录表单…")
            sb.wait_for_element_visible("input[name='username']", timeout=30)
            sb.wait_for_element_visible("input[name='password']", timeout=10)
            print("✅ 表单已加载")

            # ===========================================================
            # 3. 填凭证
            # ===========================================================
            print("✍️ 填写登录凭证…")
            sb.type("input[name='username']", EMAIL)
            sb.sleep(0.5)
            sb.type("input[name='password']", PASSWORD)
            sb.sleep(0.5)

            username_val = sb.get_value("input[name='username']")
            print(f"  用户名: '{username_val}'")

            # ===========================================================
            # 4. 勾选法律同意
            # ===========================================================
            print("☑️ 勾选法律同意…")
            consent = "span[role='checkbox'][aria-labelledby='legal-consent-label']"
            try:
                sb.wait_for_element_present(consent, timeout=10)
                if sb.get_attribute(consent, "aria-checked") != "true":
                    sb.click(consent)
                    sb.sleep(0.8)
                checked_val = sb.get_attribute(consent, "aria-checked")
                print(f"  复选框 aria-checked = {checked_val}")
            except Exception as e:
                print(f"⚠️ 勾选异常: {e}")

            # ===========================================================
            # 5. 等待 Turnstile 并点击
            # ===========================================================
            print("⏳ 等待 Turnstile…")
            sb.sleep(5)
            try:
                sb.uc_gui_click_captcha()
                print("  已尝试点击 Turnstile")
            except Exception as e:
                print(f"  click_captcha 异常: {e}")

            for i in range(60):
                tk = sb.execute_script(
                    'return (document.querySelector(\'[name="cf-turnstile-response"]\') || {}).value || "";'
                )
                if tk:
                    print(f"✅ Turnstile token 长度: {len(tk)}")
                    break
                sb.sleep(1)
            else:
                print("⚠️ Turnstile token 未生成")

            # ===========================================================
            # 6. 提交登录
            # ===========================================================
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

            # ===========================================================
            # 7. 打开控制台
            # ===========================================================
            print("📄 打开控制台…")
            for path in ["/console", "/dashboard", "/dashboard/overview"]:
                try:
                    sb.open(f"{BASE_URL}{path}")
                    sb.sleep(3)
                    cur = sb.get_current_url()
                    if "sign-in" not in cur and "login" not in cur:
                        print(f"  已打开: {cur}")
                        break
                except Exception as e:
                    print(f"  {path} 打开异常: {e}")

            # ===========================================================
            # 8. 扫描并点击签到按钮
            # ===========================================================
            print("🔍 扫描页面按钮…")
            btns = sb.execute_script("""
                return JSON.stringify(
                    Array.from(document.querySelectorAll('button, a, [role="button"]'))
                        .map(b => (b.textContent || '').trim())
                        .filter(t => t && t.length < 40)
                );
            """)
            print(f"  按钮列表: {btns}")

            print("🖱️ 查找签到按钮…")
            clicked = sb.execute_script("""
                const elems = document.querySelectorAll('button, a, [role="button"]');
                for (let i = 0; i < elems.length; i++) {
                    const t = (elems[i].textContent || '').trim();
                    if (t && t.length < 25 &&
                        (t.includes('签到') || t.includes('Check-in') ||
                         t.includes('Check in') || t.includes('Checkin') ||
                         t.includes('Daily'))) {
                        elems[i].scrollIntoView({block: 'center'});
                        elems[i].click();
                        return t;
                    }
                }
                return '';
            """)

            if not clicked:
                print("⚠️ 未找到签到按钮")
                result["error"] = "未找到签到按钮"
                sb.save_screenshot("no_checkin_button.png")
                return result

            result["checkin_clicked"] = True
            result["button_text"] = clicked
            print(f"✅ 已点击按钮: '{clicked}'")

            # ===========================================================
            # 9. 处理可能弹出的 Turnstile
            # ===========================================================
            print("⏳ 等待签到响应…")
            sb.sleep(3)
            try:
                sb.uc_gui_click_captcha()
            except Exception:
                pass

            for i in range(20):
                sb.sleep(1)
                if sb.is_element_present("iframe[src*='challenges.cloudflare.com']"):
                    try:
                        sb.uc_gui_click_captcha()
                    except Exception:
                        pass

            sb.sleep(4)

            # ===========================================================
            # 10. 读取页面提示
            # ===========================================================
            print("📢 读取页面提示…")
            toast = sb.execute_script("""
                const sels = '[class*="toast"], [class*="alert"], [class*="message"], [role="alert"], [class*="notification"], .Toastify__toast, [class*="Message"], [class*="Notice"]';
                const texts = Array.from(document.querySelectorAll(sels))
                    .map(t => (t.textContent || '').trim())
                    .filter(Boolean);
                return JSON.stringify(texts);
            """)
            print(f"  页面提示: {toast}")
            result["toast"] = toast

            try:
                sb.save_screenshot("checkin_result.png")
                print("  📸 截图已保存: checkin_result.png")
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

    toast_str = str(r.get("toast", ""))
    success = any(k in toast_str for k in ("成功", "获得", "已签到", "重复", "success", "Success"))

    if success:
        msg = (f"🎁 iamhc 签到通知\n\n"
               f"✅ 签到成功\n"
               f"📢 页面提示: {toast_str[:200]}\n"
               f"🖱️ 按钮: {r.get('button_text', '')}\n"
               f"⏱️ {now}\n{BASE_URL}")
    else:
        msg = (f"🎁 iamhc 签到通知\n\n"
               f"⚠️ 已点击签到，但结果未识别\n"
               f"📢 页面提示: {toast_str[:200]}\n"
               f"🖱️ 按钮: {r.get('button_text', '')}\n"
               f"⏱️ {now}\n{BASE_URL}")

    print(msg)
    send_notification(msg)


if __name__ == "__main__":
    main()
