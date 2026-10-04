#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
# 工具：检测页面上的 Turnstile 弹窗 / token
# ===========================================================================
def check_turnstile_present(sb):
    """检测页面上是否有 CF Turnstile 组件（iframe / container）"""
    return sb.execute_script("""
        (function() {
            // iframe 检测
            const iframes = document.querySelectorAll('iframe');
            for (let f of iframes) {
                const src = f.src || '';
                if (src.includes('challenges.cloudflare.com') ||
                    src.includes('turnstile')) {
                    return true;
                }
            }
            // 容器检测
            if (document.querySelector('div.cf-turnstile')) return true;
            if (document.querySelector('[id*="turnstile"]')) return true;
            if (document.querySelector('[class*="turnstile"]')) return true;
            return false;
        })()
    """)


def check_turnstile_token(sb):
    """检查是否有已生成的 Turnstile token，返回 token 长度（0 表示无）"""
    return sb.execute_script("""
        (function() {
            const inputs = document.querySelectorAll('[name="cf-turnstile-response"]');
            for (let inp of inputs) {
                if (inp.value && inp.value.length > 0) return inp.value.length;
            }
            return 0;
        })()
    """)


# ===========================================================================
# 浏览器流程
# ===========================================================================
def browser_checkin():
    from seleniumbase import SB

    result = {
        "logged_in": False,
        "checkin_clicked": False,
        "turnstile_passed": False,
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
            # 1. 登录
            # ===========================================================
            print(f"🌐 打开登录页: {LOGIN_URL}")
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            sb.sleep(3)

            print("⏳ 等待登录表单…")
            sb.wait_for_element_visible("input[name='username']", timeout=30)
            sb.wait_for_element_visible("input[name='password']", timeout=10)
            print("✅ 表单已加载")

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
            # 3. 定位并点击"立即签到"
            # ===========================================================
            print("🖱️ 定位签到按钮…")
            btn_selector = sb.execute_script("""
                (function() {
                    const elems = document.querySelectorAll('button, a, [role="button"]');
                    for (let i = 0; i < elems.length; i++) {
                        const t = (elems[i].textContent || '').trim();
                        if (t === '立即签到' || t.includes('签到')) {
                            elems[i].setAttribute('data-checkin-btn', '1');
                            return '[data-checkin-btn="1"]';
                        }
                    }
                    return '';
                })()
            """)

            if not btn_selector:
                print("⚠️ 未找到签到按钮")
                result["error"] = "未找到签到按钮"
                sb.save_screenshot("no_checkin_button.png")
                return result

            print("🖱️ 点击签到按钮…")
            sb.uc_click(btn_selector, reconnect_time=2)
            result["checkin_clicked"] = True
            result["button_text"] = "立即签到"
            print("✅ 已点击签到按钮")

            # ===========================================================
            # 4. 等待 CF 弹窗并处理（关键！）
            # ===========================================================
            print("⏳ 等待签到 CF 弹窗出现（最多 10 秒）…")
            sb.sleep(5)  # 先给弹窗 5 秒渲染

            # 阶段一：等弹窗出现
            turnstile_appeared = False
            for i in range(10):
                if check_turnstile_present(sb):
                    turnstile_appeared = True
                    print(f"  ✅ 第 {i+1} 次检测到 CF 弹窗")
                    break
                sb.sleep(1)

            if not turnstile_appeared:
                print("  ⚠️ 未检测到 CF 弹窗（可能已内联通过或直接成功）")

            # 阶段二：尝试点击并等 token
            print("⏳ 处理 CF 验证并等待 token 生成（最多 45 秒）…")
            for i in range(45):
                # 先尝试点击验证
                if check_turnstile_present(sb):
                    try:
                        sb.uc_gui_click_captcha()
                    except Exception:
                        pass

                # 检测 token 是否生成
                token_len = check_turnstile_token(sb)
                if token_len > 0:
                    result["turnstile_passed"] = True
                    print(f"  ✅ Turnstile token 已生成 | 长度: {token_len}")
                    break

                sb.sleep(1)
            else:
                print("  ⚠️ 45 秒内未获取 Turnstile token")

            if not result["turnstile_passed"] and not turnstile_appeared:
                # 没弹窗也当成通过（可能站点直接放行了）
                print("  ℹ️ 未出现 CF 弹窗，视为已通过")
                result["turnstile_passed"] = True

            # 等签到请求真正发出并完成
            print("⏳ 等待签到请求完成…")
            sb.sleep(6)

            # ===========================================================
            # 5. 读取页面提示（多选择器 + 按钮文字变化）
            # ===========================================================
            print("📢 读取页面提示…")
            toast = sb.execute_script("""
                (function() {
                    const sels = [
                        '[class*="toast"]', '[class*="alert"]', '[class*="message"]',
                        '[role="alert"]', '[role="status"]', '[class*="notification"]',
                        '.Toastify__toast', '[class*="Message"]', '[class*="Notice"]',
                        '[class*="modal"]', '[class*="dialog"]', '[class*="popover"]',
                        '[data-sonner-toast]', '[data-slot="toast"]'
                    ].join(',');
                    const texts = Array.from(document.querySelectorAll(sels))
                        .map(t => (t.textContent || '').trim())
                        .filter(t => t && t.length < 200);
                    return JSON.stringify(texts);
                })()
            """)
            print(f"  页面提示: {toast}")
            result["toast"] = toast

            # 检查按钮文字是否变化（"立即签到" → "已签到" 说明成功）
            print("🔍 检查按钮状态…")
            btn_state = sb.execute_script("""
                (function() {
                    const elems = document.querySelectorAll('button, a, [role="button"]');
                    for (let i = 0; i < elems.length; i++) {
                        const t = (elems[i].textContent || '').trim();
                        if (t.includes('签到')) {
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

    # 综合判断成功：
    # 1. 页面提示含"成功/获得/已签到"
    # 2. 或按钮文字变为"已签到"/"重复签到"
    toast_str = str(r.get("toast", ""))
    btn_after = str(r.get("button_after", ""))
    combined = toast_str + btn_after

    success = any(k in combined for k in (
        "成功", "获得", "已签到", "重复", "success", "Success"
    ))

    if success:
        msg = (f"🎁 iamhc 签到通知\n\n"
               f"✅ 签到成功\n"
               f"📢 页面提示: {toast_str[:200]}\n"
               f"🖱️ 按钮状态: {btn_after}\n"
               f"🔒 Turnstile: {'已通过' if r.get('turnstile_passed') else '未确认'}\n"
               f"⏱️ {now}\n{BASE_URL}")
    else:
        msg = (f"🎁 iamhc 签到通知\n\n"
               f"⚠️ 已点击签到，但结果未识别\n"
               f"📢 页面提示: {toast_str[:200]}\n"
               f"🖱️ 按钮状态: {btn_after}\n"
               f"🔒 Turnstile: {'已通过' if r.get('turnstile_passed') else '未确认'}\n"
               f"⏱️ {now}\n{BASE_URL}")

    print(msg)
    send_notification(msg)


if __name__ == "__main__":
    main()
