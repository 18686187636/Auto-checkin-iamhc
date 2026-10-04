#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
iamhc 自动签到脚本（浏览器自动化版 - CDP 原生输入修正）
通过 SeleniumBase CDP Mode 启动真实浏览器，使用 CDP 原生 type 方法输入凭证，
自动勾选法律同意复选框，通过 Cloudflare Turnstile，完成登录后提取 cookies，
再调用签到 API。
"""

import os, sys, time, json, requests
from datetime import datetime, timezone, timedelta

# ---- 环境变量 ----
EMAIL        = os.environ.get("EMAIL") or ""
PASSWORD     = os.environ.get("PASSWORD") or ""
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000
TZ_CN = timezone(timedelta(hours=8))

LOGIN_URL   = f"{BASE_URL}/sign-in"
LOGIN_PATH  = "/sign-in"


def get_cookies_via_browser():
    """启动真实 Chromium，完成登录，返回 cookies 字典。失败返回 None。"""
    from seleniumbase import SB

    print("🚀 启动浏览器（SeleniumBase CDP Mode）…")
    cookies = None

    with SB(
        uc=True,
        headed=True,
        xvfb=True,
        xvfb_metrics="1920,1080",
        incognito=True,
        locale_code="zh-CN",
        window_size="1920,1080",
    ) as sb:
        try:
            # -----------------------------------------------------------
            # 1. 打开登录页
            # -----------------------------------------------------------
            print(f"🌐 打开登录页: {LOGIN_URL}")
            sb.activate_cdp_mode(LOGIN_URL)
            sb.sleep(5)

            # -----------------------------------------------------------
            # 2. 等待 Turnstile 组件出现（仅检测）
            # -----------------------------------------------------------
            print("⏳ 等待 Turnstile 组件加载…")
            for i in range(30):
                if (sb.is_element_present("iframe[src*='challenges.cloudflare.com']")
                        or sb.is_element_present("div.cf-turnstile")
                        or sb.is_element_present("#turnstile-captcha")):
                    print(f"✅ 检测到 Turnstile 组件（第 {i+1} 次检查）")
                    break
                sb.sleep(2)
            else:
                print("⚠️ 未检测到 Turnstile 组件，可能已自动通过或页面结构变化")

            # -----------------------------------------------------------
            # 3. 填写登录凭证（CDP 原生输入方法）
            # -----------------------------------------------------------
            print("✍️ 填写登录凭证（CDP 原生输入）…")
            sb.wait_for_element_visible("input[name='username']", timeout=15)
            sb.wait_for_element_visible("input[name='password']", timeout=15)

            sb.cdp.type("input[name='username']", EMAIL, timeout=1)
            sb.sleep(0.5)
            sb.cdp.type("input[name='password']", PASSWORD, timeout=1)
            sb.sleep(0.5)

            username_val = sb.get_value("input[name='username']")
            password_val = sb.get_value("input[name='password']")
            print(f"  用户名值: '{username_val}'")
            print(f"  密码值: '{'*' * len(password_val) if password_val else ''}'")
            if not username_val or not password_val:
                print("❌ 输入框内容为空")
                return None
            print("✅ 输入框内容已确认写入")

            # -----------------------------------------------------------
            # 4. 勾选法律同意复选框
            # -----------------------------------------------------------
            print("☑️ 勾选法律同意复选框…")
            consent_selector = "span[role='checkbox'][aria-labelledby='legal-consent-label']"
            try:
                sb.wait_for_element_present(consent_selector, timeout=10)
                aria_checked = sb.get_attribute(consent_selector, "aria-checked")
                if aria_checked != "true":
                    sb.cdp.gui_click_element(consent_selector)
                    sb.sleep(1.0)
                    aria_checked = sb.get_attribute(consent_selector, "aria-checked")
                print(f"  复选框 aria-checked = {aria_checked}")
            except Exception as e:
                print(f"⚠️ 勾选复选框异常: {e}")

            # -----------------------------------------------------------
            # 5. 等待 Turnstile token 生成
            # -----------------------------------------------------------
            print("⏳ 等待 Turnstile 验证完成…")
            for i in range(20):
                sb.sleep(2)
                token_exists = sb.execute_script("""
                    return !!document.querySelector('input[name="cf-turnstile-response"]')?.value
                """)
                if token_exists:
                    print(f"✅ Turnstile token 已生成（第 {(i+1)*2} 秒）")
                    break
            else:
                print("⚠️ 未检测到 Turnstile token，继续尝试登录…")

            # -----------------------------------------------------------
            # 6. 点击登录按钮
            # -----------------------------------------------------------
            print("🖱️ 点击登录按钮…")
            sb.wait_for_element_visible("button[type='submit']", timeout=10)
            sb.click("button[type='submit']")

            # -----------------------------------------------------------
            # 7. 等待登录成功
            # -----------------------------------------------------------
            print("⏳ 等待登录完成…")
            login_ok = False
            last_err = ""
            for i in range(40):
                sb.sleep(2)
                current_url = sb.get_current_url()
                if LOGIN_PATH not in current_url:
                    login_ok = True
                    print(f"✅ 登录成功，当前 URL: {current_url}")
                    break
                if sb.is_element_present(".error, .alert-danger, [class*='error']"):
                    err_text = sb.get_text(".error, .alert-danger, [class*='error']")
                    if err_text and len(err_text) < 200 and err_text != last_err:
                        last_err = err_text
                        print(f"⚠️ 页面错误提示: {err_text}")

            if not login_ok:
                print("⚠️ 登录可能未成功，尝试继续提取 cookies…")
                try:
                    sb.save_screenshot("login_failed.png")
                    print("📸 登录失败截图已保存: login_failed.png")
                except Exception:
                    pass

            # -----------------------------------------------------------
            # 8. 提取 cookies
            # -----------------------------------------------------------
            print("🍪 提取 cookies…")
            all_cookies = sb.get_all_cookies()
            cookies = {c["name"]: c["value"] for c in all_cookies}
            print(f"✅ 提取到 {len(cookies)} 个 cookies: {list(cookies.keys())}")
            return cookies

        except Exception as e:
            print(f"❌ 浏览器自动化异常: {e}")
            try:
                sb.save_screenshot("error_screenshot.png")
                print("📸 错误截图已保存: error_screenshot.png")
            except Exception:
                pass
            return None


# ===========================================================================
# 签到逻辑（使用 cookies 调用 API）
# ===========================================================================
def quota_to_dollar(quota):
    return quota / QUOTA_PER_UNIT


def fmt_usd(v):
    return str(round(v))


def make_api_session(cookies):
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/154.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": BASE_URL,
        "Referer": BASE_URL,
    })
    for name, value in cookies.items():
        s.cookies.set(name, value, domain="api.hcnsec.cn")
    return s


def get_user_info(session):
    url = f"{BASE_URL}/api/user/self"
    resp = session.get(url, timeout=20)
    try:
        data = resp.json()
    except ValueError:
        print(f"获取用户信息响应非 JSON: {resp.status_code} {resp.text[:200]}")
        return None
    if not data.get("success"):
        print("获取用户信息失败:", data.get("message", ""))
        return None
    ud = data.get("data") or {}
    if isinstance(ud, dict) and "user" in ud and isinstance(ud["user"], dict):
        ud = ud["user"]
    return ud


def checkin(session):
    url = f"{BASE_URL}/api/user/checkin"
    resp = session.post(url, json={}, timeout=20)
    try:
        return resp.json()
    except ValueError:
        return {"success": False, "message": f"签到接口异常 HTTP {resp.status_code}"}


def send_notification(message):
    print("\n" + "=" * 30)
    print(message)
    print("=" * 30)
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": message},
                timeout=10,
            )
            print("Telegram 通知:", "成功" if r.status_code == 200 else f"失败 {r.status_code}")
        except Exception as e:
            print("Telegram 通知异常:", e)
    else:
        print("未配置 TG_BOT_TOKEN / TG_CHAT_ID，跳过 Telegram 推送")


# ===========================================================================
# 主流程
# ===========================================================================
def main():
    if not EMAIL or not PASSWORD:
        print("请设置 EMAIL 和 PASSWORD 环境变量")
        sys.exit(1)

    cookies = get_cookies_via_browser()
    if not cookies:
        msg = "❌ iamhc 浏览器登录失败，未能获取 cookies"
        print(msg)
        send_notification(msg)
        sys.exit(1)

    has_session = any("session" in k.lower() or "token" in k.lower() for k in cookies.keys())
    if not has_session:
        print("⚠️ cookies 中未发现明显的会话标识，仍尝试调用 API…")

    session = make_api_session(cookies)

    info_before = get_user_info(session)
    if not info_before:
        msg = "⚠️ iamhc 获取用户信息失败，cookies 可能无效"
        print(msg)
        send_notification(msg)
        sys.exit(1)

    user_id = info_before.get("id") or info_before.get("user_id")
    username = info_before.get("username", str(user_id))
    balance_before = quota_to_dollar(info_before.get("quota", 0))
    print(f"👤 账户: {username} | ID: {user_id} | 当前余额: {fmt_usd(balance_before)}$")

    checkin_data = checkin(session)

    info_after = get_user_info(session)
    balance_after = quota_to_dollar(info_after.get("quota", 0)) if info_after else balance_before

    now = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    success = checkin_data.get("success", False)
    msg = str(checkin_data.get("message", "") or "")

    if success:
        awarded_quota = (checkin_data.get("data") or {}).get("quota_awarded", 0) or 0
        awarded_dollar = quota_to_dollar(awarded_quota) if awarded_quota else (balance_after - balance_before)
        print(f"✅ 签到成功 | 获得: {fmt_usd(awarded_dollar)}$")
        message = (
            f"🎁 iamhc 签到通知\n\n"
            f"✅ 签到成功,本次签到获得 {fmt_usd(awarded_dollar)}$\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {fmt_usd(balance_before)}$\n"
            f"💰 当前余额: {fmt_usd(balance_after)}$\n"
            f"⏱️ 签到时间: {now}\n{BASE_URL}"
        )
    elif any(k in msg for k in ("已签到", "重复签到", "今天已签到")):
        print(f"✅ 今日已签到 | 当前余额: {fmt_usd(balance_after)}$")
        message = (
            f"🎁 iamhc 签到通知\n\n"
            f"✅ 今日你已经签到过了！\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {fmt_usd(balance_before)}$\n"
            f"💰 当前余额: {fmt_usd(balance_after)}$\n"
            f"⏱️ 签到时间: {now}\n{BASE_URL}"
        )
    else:
        print(f"❌ 签到失败 | {msg}")
        message = (
            f"🎁 iamhc 签到通知\n\n"
            f"❌ 签到失败: {msg}\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {fmt_usd(balance_before)}$\n"
            f"💰 当前余额: {fmt_usd(balance_after)}$\n"
            f"⏱️ 签到时间: {now}\n{BASE_URL}"
        )

    send_notification(message)


if __name__ == "__main__":
    main()
