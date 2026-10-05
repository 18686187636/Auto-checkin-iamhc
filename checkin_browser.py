#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
iamhc 纯浏览器自动签到 v6
精确匹配"立即签到"按钮 → 点击 → 无条件处理 CF → 等 15 秒 → 读结果
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
# 浏览器流程
# ===========================================================================
def browser_checkin():
    from seleniumbase import SB

    result = {
        "logged_in": False,
        "checkin_clicked": False,
        "button_before": "",
        "button_after": "",
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
            # 1. 登录
            # ===========================================================
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

            # ===========================================================
            # 2. 打开个人资料页
            # ===========================================================
            print("📄 打开个人资料页面…")
            sb.open(PROFILE_URL)
            sb.sleep(4)
            cur = sb.get_current_url()
            print(f"  当前页面: {cur}")
            if "sign-in" in cur or "login" in cur:
                result["error"] = "登录状态失效"
                return result

            # ===========================================================
            # 3. 精确匹配"立即签到"按钮
            # ===========================================================
            print("🔍 查找'立即签到'按钮（精确匹配）…")
            btn_info = sb.execute_script("""
                (function() {
                    const elems = document.querySelectorAll('button');
                    let found = null;
                    // 优先精确匹配
                    for (let e of elems) {
                        const t = (e.textContent || '').trim();
                        if (t === '立即签到') {
                            found = e;
                            break;
                        }
                    }
                    // 其次匹配以"立即签到"开头（防止空格）
                    if (!found) {
                        for (let e of elems) {
                            const t = (e.textContent || '').trim();
                            if (t.startsWith('立即签到')) {
                                found = e;
                                break;
                            }
                        }
                    }
                    if (found) {
                        found.setAttribute('data-checkin-target', '1');
                        return JSON.stringify({
                            ok: true,
                            text: (found.textContent || '').trim(),
                            tag: found.tagName,
                            disabled: found.disabled
                        });
                    }
                    // 返回所有 button 文字用于排查
                    return JSON.stringify({
                        ok: false,
                        all_buttons: Array.from(elems).map(e => (e.textContent || '').trim()).filter(Boolean)
                    });
                })()
            """)
            print(f"  按钮查找结果: {btn_info}")

            try:
                info = json.loads(btn_info)
            except Exception:
                info = {"ok": False}

            if not info.get("ok"):
                print("⚠️ 未找到'立即签到'按钮")
                result["error"] = "未找到立即签到按钮"
                sb.save_screenshot("no_checkin_button.png")
                return result

            result["button_before"] = info.get("text", "")
            print(f"  ✅ 找到按钮: '{info.get('text')}' (tag={info.get('tag')})")

            # ===========================================================
            # 4. 点击签到按钮
            # ===========================================================
            print("🖱️ 点击'立即签到'…")
            sb.uc_click("[data-checkin-target='1']", reconnect_time=2)
            result["checkin_clicked"] = True
            print("✅ 已点击")

            # ===========================================================
            # 5. 等 CF 弹窗出现（3 秒）
            # ===========================================================
            print("⏳ 等待 CF 弹窗渲染（3 秒）…")
            sb.sleep(3)

            # ===========================================================
            # 6. 无条件调用 uc_gui_click_captcha（处理 CF 弹窗）
            # ===========================================================
            print("🔐 尝试处理 CF 验证…")
            for attempt in range(3):
                try:
                    sb.uc_gui_click_captcha()
                    print(f"  第 {attempt+1} 次 uc_gui_click_captcha 执行")
                except Exception as e:
                    print(f"  第 {attempt+1} 次异常: {e}")
                sb.sleep(3)

            # 检测 Turnstile token（可能已经在某个隐藏 input 里）
            token_len = sb.execute_script("""
                (function() {
                    const inputs = document.querySelectorAll('[name="cf-turnstile-response"]');
                    let max = 0;
                    for (let inp of inputs) {
                        if (inp.value && inp.value.length > max) max = inp.value.length;
                    }
                    return max;
                })()
            """)
            print(f"  Turnstile token 长度: {token_len}")

            # ===========================================================
            # 7. 等签到请求完成（重要：CF 通过后请求才发出）
            # ===========================================================
            print("⏳ 等待签到请求完成（15 秒）…")
            sb.sleep(15)

            # ===========================================================
            # 8. 读取结果
            # ===========================================================
            print("📢 读取页面提示…")
            toast = sb.execute_script("""
                (function() {
                    const sels = [
                        '[class*="toast"]', '[class*="alert"]', '[class*="message"]',
                        '[role="alert"]', '[role="status"]', '[class*="notification"]',
                        '.Toastify__toast', '[class*="Message"]', '[class*="Notice"]',
                        '[data-sonner-toast]', '[data-slot="toast"]',
                        '[aria-live="polite"]', '[aria-live="assertive"]'
                    ].join(',');
                    const texts = Array.from(document.querySelectorAll(sels))
                        .map(t => (t.textContent || '').trim())
                        .filter(t => t && t.length < 200);
                    return JSON.stringify(texts);
                })()
            """)
            print(f"  页面提示: {toast}")
            result["toast"] = toast

            # 检查按钮文字是否变化
            print("🔍 检查'签到'按钮状态…")
            btn_state = sb.execute_script("""
                (function() {
                    const elems = document.querySelectorAll('button');
                    for (let e of elems) {
                        const t = (e.textContent || '').trim();
                        if (t === '立即签到' || t.startsWith('立即签到') ||
                            t === '已签到' || t.includes('已签到') ||
                            t.includes('今日已签到')) {
                            return t;
                        }
                    }
                    return '';
                })()
            """)
            print(f"  当前按钮文字: '{btn_state}'")
            result["button_after"] = btn_state

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

    # 判断成功：toast 含成功词 或 按钮变为"已签到"
    toast_str = str(r.get("toast", ""))
    btn_before = str(r.get("button_before", ""))
    btn_after = str(r.get("button_after", ""))

    success = (
        any(k in toast_str for k in ("成功", "获得", "已签到", "重复"))
        or "已签到" in btn_after
        or "今日已签到" in btn_after
    )

    if success:
        msg = (f"🎁 iamhc 签到通知\n\n"
               f"✅ 签到成功\n"
               f"📢 页面提示: {toast_str[:200]}\n"
               f"🖱️ 按钮: {btn_before} → {btn_after}\n"
               f"⏱️ {now}\n{BASE_URL}")
    else:
        msg = (f"🎁 iamhc 签到通知\n\n"
               f"⚠️ 已点击签到，结果未确认\n"
               f"📢 页面提示: {toast_str[:200]}\n"
               f"🖱️ 按钮: {btn_before} → {btn_after}\n"
               f"⏱️ {now}\n{BASE_URL}")

    print(msg)
    send_notification(msg)


if __name__ == "__main__":
    main()
