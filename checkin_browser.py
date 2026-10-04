#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
iamhc 自动签到脚本（浏览器自动化版）
通过 SeleniumBase CDP Mode 启动真实浏览器，自动通过 Cloudflare Turnstile，
完成登录后提取 cookies，再调用签到 API。
"""

import os, sys, time, json, requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

# ---- 环境变量 ----
EMAIL        = os.environ.get("EMAIL") or ""
PASSWORD     = os.environ.get("PASSWORD") or ""
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000
TZ_CN = timezone(timedelta(hours=8))

# ---- 浏览器自动化配置 ----
LOGIN_URL   = f"{BASE_URL}/sign-in"
LOGIN_PATH  = "/sign-in"          # 登录成功后 URL 不再包含此路径即视为成功
MAX_WAIT_S  = 120                 # Turnstile + 登录总超时（秒）


# ===========================================================================
# 浏览器自动化：通过 Turnstile 并登录，提取 cookies
# ===========================================================================
def get_cookies_via_browser():
    """
    启动真实 Chromium，访问登录页，等待 Turnstile 自动通过，
    填写账号密码登录，返回 cookies 字典。失败返回 None。
    """
    from seleniumbase import SB

    print("🚀 启动浏览器（SeleniumBase CDP Mode）…")

    cookies = None

    # uc=True 启用未检测模式；headed=True + xvfb=True 在无显示器环境中模拟有头浏览器
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
            print(f"🌐 打开登录页: {LOGIN_URL}")
            sb.activate_cdp_mode(LOGIN_URL)
            sb.sleep(5)  # 等待页面初步加载

            # ---- 等待 Turnstile 组件出现（最多 60 秒）----
            print("⏳ 等待 Turnstile 验证组件…")
            turnstile_found = False
            for i in range(30):
                # Turnstile 通常在 iframe 中，或页面有 cf-turnstile 容器
                if sb.is_element_present("iframe[src*='challenges.cloudflare.com']") \
                   or sb.is_element_present("div.cf-turnstile") \
                   or sb.is_element_present("#turnstile-captcha"):
                    turnstile_found = True
                    print(f"✅ 检测到 Turnstile 组件（第 {i+1} 次检查）")
                    break
                sb.sleep(2)

            if not turnstile_found:
                print("⚠️ 未检测到 Turnstile 组件，可能已自动通过或页面结构变化")

            # ---- 等待 Turnstile 自动完成（或手动点击）----
            print("⏳ 等待 Turnstile 验证自动通过…")
            turnstile_passed = False
            for i in range(40):
                sb.sleep(3)
                # 常见成功标志：Turnstile iframe 消失、出现成功提示、或页面开始跳转
                if not sb.is_element_present("iframe[src*='challenges.cloudflare.com']"):
                    turnstile_passed = True
                    print(f"✅ Turnstile 已通过（第 {(i+1)*3} 秒）")
                    break
                # 尝试主动点击 checkbox（如果可见）
                try:
                    if sb.is_element_visible("div.cf-turnstile input[type='checkbox']"):
                        sb.cdp.gui_click_element("div.cf-turnstile input[type='checkbox']")
                        print("🖱️ 已点击 Turnstile checkbox")
                except Exception:
                    pass

            if not turnstile_passed:
                print("⚠️ Turnstile 等待超时，继续尝试登录…")

            # ---- 填写登录表单 ----
            print("✍️ 填写登录凭证…")
            # 尝试多种常见选择器
            username_selectors = [
                "input[name='username']",
                "input[type='email']",
                "input[placeholder*='邮箱']",
                "input[placeholder*='用户']",
                "#username",
                "#email",
            ]
            password_selectors = [
                "input[name='password']",
                "input[type='password']",
                "input[placeholder*='密码']",
                "#password",
            ]

            filled_user = False
            for sel in username_selectors:
                if sb.is_element_present(sel):
                    sb.cdp.type(sel, EMAIL)
                    filled_user = True
                    print(f"  用户名选择器: {sel}")
                    break
            if not filled_user:
                print("❌ 未找到用户名输入框")
                return None

            filled_pass = False
            for sel in password_selectors:
                if sb.is_element_present(sel):
                    sb.cdp.type(sel, PASSWORD)
                    filled_pass = True
                    print(f"  密码选择器: {sel}")
                    break
            if not filled_pass:
                print("❌ 未找到密码输入框")
                return None

            # ---- 点击登录按钮 ----
            print("🖱️ 点击登录按钮…")
            submit_selectors = [
                "button[type='submit']",
                "button:contains('登录')",
                "button:contains('Login')",
                "button:contains('Sign in')",
                "input[type='submit']",
                ".login-button",
            ]
            clicked = False
            for sel in submit_selectors:
                try:
                    if sb.is_element_present(sel):
                        sb.cdp.click(sel)
                        clicked = True
                        print(f"  登录按钮选择器: {sel}")
                        break
                except Exception:
                    pass
            if not clicked:
                # 兜底：按 Enter
                sb.cdp.press_keyboard_key(password_selectors[0] if filled_pass else "body", "Enter")
                print("  未找到登录按钮，尝试按 Enter 提交")

            # ---- 等待登录成功（URL 变化 / 页面元素出现）----
            print("⏳ 等待登录完成…")
            login_ok = False
            for i in range(40):
                sb.sleep(2)
                current_url = sb.get_current_url()
                if LOGIN_PATH not in current_url:
                    login_ok = True
                    print(f"✅ 登录成功，当前 URL: {current_url}")
                    break
                # 检查是否有错误提示
                if sb.is_element_present(".error, .alert-danger, [class*='error']"):
                    err_text = sb.get_text(".error, .alert-danger, [class*='error']")
                    if err_text and len(err_text) < 200:
                        print(f"⚠️ 页面错误提示: {err_text}")

            if not login_ok:
                print("⚠️ 登录可能未成功，尝试继续提取 cookies…")

            # ---- 提取 cookies ----
            print("🍪 提取 cookies…")
            all_cookies = sb.get_all_cookies()
            cookies = {}
            for c in all_cookies:
                cookies[c["name"]] = c["value"]

            print(f"✅ 提取到 {len(cookies)} 个 cookies: {list(cookies.keys())}")
            return cookies

        except Exception as e:
            print(f"❌ 浏览器自动化异常: {e}")
            try:
                sb.save_screenshot("error_screenshot.png")
                print("📸 错误截图已保存")
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
    """用浏览器提取的 cookies 创建 requests.Session"""
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


# ===========================================================================
# 主流程
# ===========================================================================
def main():
    if not EMAIL or not PASSWORD:
        print("请设置 EMAIL 和 PASSWORD 环境变量")
        sys.exit(1)

    # 1. 浏览器登录，获取 cookies
    cookies = get_cookies_via_browser()
    if not cookies:
        msg = "❌ iamhc 浏览器登录失败，未能获取 cookies"
        print(msg)
        send_notification(msg)
        sys.exit(1)

    # 检查关键 cookie
    has_session = any("session" in k.lower() or "token" in k.lower() for k in cookies.keys())
    if not has_session:
        print("⚠️ cookies 中未发现明显的会话标识，仍尝试调用 API…")

    # 2. 用 cookies 调用签到 API
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
