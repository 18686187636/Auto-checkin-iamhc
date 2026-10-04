#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
iamhc 自动签到脚本（SeleniumBase UC 模式版）
使用 SeleniumBase UC 模式 + uc_open_with_reconnect 通过 Cloudflare Turnstile，
提取 cookies 后调用签到 API。
"""

import os, sys, time, json, requests
from datetime import datetime, timezone, timedelta

EMAIL        = os.environ.get("EMAIL") or ""
PASSWORD     = os.environ.get("PASSWORD") or ""
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000
TZ_CN = timezone(timedelta(hours=8))

LOGIN_URL  = f"{BASE_URL}/sign-in"
LOGIN_PATH = "/sign-in"


def get_cookies_via_browser():
    from seleniumbase import SB

    print("🚀 启动 UC 浏览器…")
    cookies = None

    with SB(
        uc=True,
        test=True,          # 让 seleniumbase 自动管理 xvfb
        headed=True,
        incognito=True,
        locale_code="zh-CN",
        window_size="1920,1080",
    ) as sb:
        try:
            # ---------------------------------------------------------
            # 1. 用 uc_open_with_reconnect 打开登录页（绕过 Cloudflare 关键）
            # ---------------------------------------------------------
            print(f"🌐 打开登录页（uc_open_with_reconnect）: {LOGIN_URL}")
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            sb.sleep(3)

            # ---------------------------------------------------------
            # 2. 等登录表单出现
            # ---------------------------------------------------------
            print("⏳ 等待登录表单…")
            sb.wait_for_element_visible("input[name='username']", timeout=30)
            sb.wait_for_element_visible("input[name='password']", timeout=10)
            print("✅ 登录表单已加载")

            # ---------------------------------------------------------
            # 3. 等待 Turnstile 组件渲染（先等出现）
            # ---------------------------------------------------------
            print("⏳ 等待 Turnstile 组件渲染…")
            for i in range(30):
                if (sb.is_element_present("iframe[src*='challenges.cloudflare.com']")
                        or sb.is_element_present("div.cf-turnstile")):
                    print(f"✅ Turnstile 组件已出现（第 {i+1} 次检查）")
                    break
                sb.sleep(1)
            else:
                print("⚠️ 未检测到 Turnstile iframe，可能已通过或渲染较慢")

            # ---------------------------------------------------------
            # 4. 填写登录凭证（UC 模式标准 type，触发 React 事件）
            # ---------------------------------------------------------
            print("✍️ 填写登录凭证…")
            sb.type("input[name='username']", EMAIL)
            sb.sleep(0.5)
            sb.type("input[name='password']", PASSWORD)
            sb.sleep(0.5)

            u = sb.get_value("input[name='username']")
            p = sb.get_value("input[name='password']")
            print(f"  用户名: '{u}'  密码长度: {len(p) if p else 0}")
            if not u or not p:
                print("❌ 输入框为空")
                return None

            # ---------------------------------------------------------
            # 5. 勾选法律同意复选框
            # ---------------------------------------------------------
            print("☑️ 勾选法律同意复选框…")
            consent = "span[role='checkbox'][aria-labelledby='legal-consent-label']"
            try:
                sb.wait_for_element_present(consent, timeout=10)
                checked = sb.get_attribute(consent, "aria-checked")
                if checked != "true":
                    sb.click(consent)
                    sb.sleep(0.6)
                    checked = sb.get_attribute(consent, "aria-checked")
                print(f"  复选框 aria-checked = {checked}")
            except Exception as e:
                print(f"⚠️ 勾选复选框异常: {e}")

            # ---------------------------------------------------------
            # 6. 等待 Turnstile token 真正生成（关键！）
            # ---------------------------------------------------------
            print("⏳ 等待 Turnstile token 生成…")
            token = ""
            for i in range(60):
                token = sb.execute_script(
                    'return (document.querySelector(\'[name="cf-turnstile-response"]\') || {}).value || "";'
                )
                if token:
                    print(f"✅ Turnstile token 已生成 | 长度: {len(token)} | 前50字符: {token[:50]}...")
                    break
                sb.sleep(1)
            if not token:
                print("⚠️ 等待 60 秒仍未获取 token，继续尝试提交…")

            # ---------------------------------------------------------
            # 7. 点击登录按钮
            # ---------------------------------------------------------
            print("🖱️ 点击登录按钮…")
            sb.wait_for_element_visible("button[type='submit']", timeout=10)
            sb.click("button[type='submit']")

            # ---------------------------------------------------------
            # 8. 等待登录成功（URL 变化）
            # ---------------------------------------------------------
            print("⏳ 等待登录完成…")
            login_ok = False
            for i in range(30):
                sb.sleep(2)
                cur = sb.get_current_url()
                if LOGIN_PATH not in cur:
                    login_ok = True
                    print(f"✅ 登录成功，当前 URL: {cur}")
                    break
            if not login_ok:
                print("⚠️ 未检测到 URL 变化，尝试提取 cookies…")
                try:
                    sb.save_screenshot("login_failed.png")
                except Exception:
                    pass

            # ---------------------------------------------------------
            # 9. 提取 cookies
            # ---------------------------------------------------------
            print("🍪 提取 cookies…")
            all_cookies = sb.get_all_cookies()
            cookies = {c["name"]: c["value"] for c in all_cookies}
            print(f"✅ 提取到 {len(cookies)} 个 cookies: {list(cookies.keys())}")
            return cookies

        except Exception as e:
            print(f"❌ 浏览器自动化异常: {e}")
            try:
                sb.save_screenshot("error_screenshot.png")
            except Exception:
                pass
            return None


# ===========================================================================
# 签到逻辑
# ===========================================================================
def quota_to_dollar(q): return q / QUOTA_PER_UNIT
def fmt_usd(v):         return str(round(v))


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
    for k, v in cookies.items():
        s.cookies.set(k, v, domain="api.hcnsec.cn")
    return s


def get_user_info(session):
    r = session.get(f"{BASE_URL}/api/user/self", timeout=20)
    try:
        d = r.json()
    except ValueError:
        print(f"用户信息响应非 JSON: {r.status_code} {r.text[:200]}")
        return None
    if not d.get("success"):
        print("获取用户信息失败:", d.get("message", ""))
        return None
    ud = d.get("data") or {}
    if isinstance(ud, dict) and "user" in ud and isinstance(ud["user"], dict):
        ud = ud["user"]
    return ud


def checkin(session):
    r = session.post(f"{BASE_URL}/api/user/checkin", json={}, timeout=20)
    try:
        return r.json()
    except ValueError:
        return {"success": False, "message": f"签到接口异常 HTTP {r.status_code}"}


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


def main():
    if not EMAIL or not PASSWORD:
        print("请设置 EMAIL 和 PASSWORD")
        sys.exit(1)

    cookies = get_cookies_via_browser()
    if not cookies:
        msg = "❌ iamhc 浏览器登录失败，未获取到 cookies"
        print(msg)
        send_notification(msg)
        sys.exit(1)

    session = make_api_session(cookies)
    info = get_user_info(session)
    if not info:
        msg = "⚠️ iamhc cookies 无效"
        print(msg)
        send_notification(msg)
        sys.exit(1)

    uid      = info.get("id") or info.get("user_id")
    username = info.get("username", str(uid))
    bal_before = quota_to_dollar(info.get("quota", 0))
    print(f"👤 {username} | ID: {uid} | 余额: {fmt_usd(bal_before)}$")

    ck = checkin(session)
    info2 = get_user_info(session)
    bal_after = quota_to_dollar(info2.get("quota", 0)) if info2 else bal_before

    now = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    ok = ck.get("success", False)
    msg = str(ck.get("message", "") or "")

    if ok:
        awarded = (ck.get("data") or {}).get("quota_awarded", 0) or 0
        awarded_d = quota_to_dollar(awarded) if awarded else (bal_after - bal_before)
        print(f"✅ 签到成功 | 获得 {fmt_usd(awarded_d)}$")
        message = (f"🎁 iamhc 签到通知\n\n✅ 签到成功，获得 {fmt_usd(awarded_d)}$\n"
                   f"👤 {username}\n💰 昨日: {fmt_usd(bal_before)}$\n"
                   f"💰 当前: {fmt_usd(bal_after)}$\n⏱️ {now}\n{BASE_URL}")
    elif any(k in msg for k in ("已签到", "重复签到", "今天已签到")):
        print(f"✅ 今日已签到 | 余额: {fmt_usd(bal_after)}$")
        message = (f"🎁 iamhc 签到通知\n\n✅ 今日已签到\n"
                   f"👤 {username}\n💰 昨日: {fmt_usd(bal_before)}$\n"
                   f"💰 当前: {fmt_usd(bal_after)}$\n⏱️ {now}\n{BASE_URL}")
    else:
        print(f"❌ 签到失败 | {msg}")
        message = (f"🎁 iamhc 签到通知\n\n❌ 签到失败: {msg}\n"
                   f"👤 {username}\n💰 昨日: {fmt_usd(bal_before)}$\n"
                   f"💰 当前: {fmt_usd(bal_after)}$\n⏱️ {now}\n{BASE_URL}")

    send_notification(message)


if __name__ == "__main__":
    main()
