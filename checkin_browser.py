#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
iamhc 自动签到脚本
优先使用 COOKIES 环境变量；无效时用浏览器登录（账号密码）兜底。
登录成功后自动打印 cookie 字符串，方便更新 GitHub Secrets。
"""

import os, sys, time, json, requests
from datetime import datetime, timezone, timedelta

# ---- 环境变量 ----
EMAIL        = os.environ.get("EMAIL") or ""
PASSWORD     = os.environ.get("PASSWORD") or ""
COOKIES      = os.environ.get("COOKIES") or ""
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000
TZ_CN = timezone(timedelta(hours=8))

LOGIN_URL  = f"{BASE_URL}/sign-in"
LOGIN_PATH = "/sign-in"


# ===========================================================================
# 工具函数
# ===========================================================================
def parse_cookie_string(s):
    """把 "k1=v1; k2=v2" 解析成 dict"""
    cookies = {}
    for kv in s.split(";"):
        kv = kv.strip()
        if kv and "=" in kv:
            k, v = kv.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def make_api_session(cookies):
    """构造带 cookie 的 requests.Session"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/154.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/console",
    })
    for k, v in cookies.items():
        s.cookies.set(k, v, domain="api.hcnsec.cn")
    return s


def get_user_info(session):
    """获取用户信息，成功返回 dict，失败返回 None"""
    r = session.get(f"{BASE_URL}/api/user/self", timeout=20)
    try:
        d = r.json()
    except ValueError:
        print(f"  用户信息响应非 JSON: {r.status_code} {r.text[:200]}")
        return None
    if not d.get("success"):
        print(f"  获取用户信息失败: {d.get('message', '')}")
        return None
    ud = d.get("data") or {}
    if isinstance(ud, dict) and "user" in ud and isinstance(ud["user"], dict):
        ud = ud["user"]
    return ud


def checkin(session):
    """签到"""
    r = session.post(f"{BASE_URL}/api/user/checkin", json={}, timeout=20)
    try:
        return r.json()
    except ValueError:
        return {"success": False, "message": f"签到接口异常 HTTP {r.status_code}"}


def quota_to_dollar(q): return q / QUOTA_PER_UNIT
def fmt_usd(v):         return str(round(v))


# ===========================================================================
# 浏览器登录（兜底方案）
# ===========================================================================
def get_cookies_via_browser():
    """启动真实 Chromium 登录，返回 cookies 字典。失败返回 None。"""
    from seleniumbase import SB

    print("🚀 启动浏览器（UC 模式）…")
    cookies = None

    with SB(
        uc=True,
        headed=True,
        xvfb=False,              # 外部 xvfb-run 已提供 DISPLAY
        incognito=True,
        locale_code="zh-CN",
        window_size="1920,1080",
        chromium_arg="--no-sandbox,--disable-dev-shm-usage,--disable-gpu",
    ) as sb:
        try:
            # 1. 打开登录页
            print(f"🌐 打开登录页: {LOGIN_URL}")
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            sb.sleep(3)

            # 2. 等表单
            print("⏳ 等待登录表单…")
            sb.wait_for_element_visible("input[name='username']", timeout=30)
            sb.wait_for_element_visible("input[name='password']", timeout=10)
            print("✅ 登录表单已加载")

            # 3. 填凭证
            print("✍️ 填写登录凭证…")
            sb.type("input[name='username']", EMAIL)
            sb.sleep(0.5)
            sb.type("input[name='password']", PASSWORD)
            sb.sleep(0.5)

            u = sb.get_value("input[name='username']")
            p = sb.get_value("input[name='password']")
            print(f"  用户名: '{u}'  密码长度: {len(p) if p else 0}")

            # 4. 勾选法律同意
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

            # 5. 等待 Turnstile 加载
            print("⏳ 等待 Turnstile 组件加载…")
            sb.sleep(5)

            # 6. 自动点击 Turnstile
            print("🖱️ 尝试自动点击 Turnstile…")
            try:
                sb.uc_gui_click_captcha()
                print("✅ uc_gui_click_captcha 执行完成")
            except Exception as e:
                print(f"⚠️ uc_gui_click_captcha 异常: {e}")

            # 7. 等 token 生成
            print("⏳ 等待 Turnstile token 生成…")
            token = ""
            for i in range(60):
                token = sb.execute_script(
                    'return (document.querySelector(\'[name="cf-turnstile-response"]\') || {}).value || "";'
                )
                if token:
                    print(f"✅ Turnstile token 已生成 | 长度: {len(token)}")
                    break
                sb.sleep(1)
            if not token:
                print("⚠️ 等待 60 秒仍未获取 token，继续尝试提交…")

            # 8. 提交
            print("🖱️ 点击登录按钮…")
            sb.wait_for_element_visible("button[type='submit']", timeout=10)
            sb.click("button[type='submit']")

            # 9. 等待跳转
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
                print("⚠️ 未检测到 URL 变化")
                try:
                    sb.save_screenshot("login_failed.png")
                except Exception:
                    pass

            # 10. 多方式提取 cookies
            print("🍪 提取 cookies…")
            cookies = {}

            # 方式 1：Selenium 标准 driver.get_cookies() —— 最可靠
            try:
                raw = sb.driver.get_cookies()
                if raw:
                    cookies = {c["name"]: c["value"] for c in raw}
                    print(f"  方式1 driver.get_cookies: {len(cookies)} 个")
            except Exception as e:
                print(f"  方式1 失败: {e}")

            # 方式 2：SeleniumBase get_all_cookies()
            if not cookies:
                try:
                    raw = sb.get_all_cookies()
                    if raw:
                        cookies = {c["name"]: c["value"] for c in raw}
                        print(f"  方式2 get_all_cookies: {len(cookies)} 个")
                except Exception as e:
                    print(f"  方式2 失败: {e}")

            # 方式 3：CDP Network.getAllCookies —— 包含 HttpOnly
            if not cookies:
                try:
                    raw = sb.execute_cdp_cmd("Network.getAllCookies", {})
                    clist = raw.get("cookies", [])
                    if clist:
                        cookies = {c["name"]: c["value"] for c in clist}
                        print(f"  方式3 Network.getAllCookies: {len(cookies)} 个")
                except Exception as e:
                    print(f"  方式3 失败: {e}")

            # 方式 4：CDP Network.getCookies 限定 URL
            if not cookies:
                try:
                    raw = sb.execute_cdp_cmd("Network.getCookies", {"urls": [BASE_URL, LOGIN_URL]})
                    clist = raw.get("cookies", [])
                    if clist:
                        cookies = {c["name"]: c["value"] for c in clist}
                        print(f"  方式4 Network.getCookies: {len(cookies)} 个")
                except Exception as e:
                    print(f"  方式4 失败: {e}")

            # 方式 5：document.cookie（只能拿非 HttpOnly）
            if not cookies:
                try:
                    raw = sb.execute_script("return document.cookie")
                    if raw:
                        for kv in raw.split(";"):
                            kv = kv.strip()
                            if "=" in kv:
                                k, v = kv.split("=", 1)
                                cookies[k.strip()] = v.strip()
                        print(f"  方式5 document.cookie: {len(cookies)} 个")
                except Exception as e:
                    print(f"  方式5 失败: {e}")

            # 打印成一行，方便复制到 GitHub Secrets
            if cookies:
                cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
                print("\n" + "=" * 70)
                print("📋 请把以下内容完整复制到 GitHub Secrets 的 COOKIES 变量：")
                print("=" * 70)
                print(cookie_str)
                print("=" * 70 + "\n")

            print(f"✅ 最终提取到 {len(cookies)} 个 cookies: {list(cookies.keys())}")
            return cookies if cookies else None

        except Exception as e:
            print(f"❌ 浏览器自动化异常: {e}")
            try:
                sb.save_screenshot("error_screenshot.png")
            except Exception:
                pass
            return None


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
# 主流程
# ===========================================================================
def main():
    session = None
    info = None

    # ---------- 路径 1：优先用 COOKIES 环境变量 ----------
    if COOKIES:
        print("🔑 使用环境变量 COOKIES 构造会话…")
        cookie_dict = parse_cookie_string(COOKIES)
        print(f"  解析到 {len(cookie_dict)} 个 cookie: {list(cookie_dict.keys())}")
        session = make_api_session(cookie_dict)
        info = get_user_info(session)
        if info:
            print(f"✅ COOKIES 有效 | 用户: {info.get('username', '')}")
        else:
            print("⚠️ COOKIES 已失效，将回退到浏览器登录")
            session = None
    else:
        print("ℹ️ 未配置 COOKIES 环境变量")

    # ---------- 路径 2：浏览器登录兜底 ----------
    if session is None:
        if not EMAIL or not PASSWORD:
            print("❌ 无法回退：未配置 EMAIL 和 PASSWORD")
            sys.exit(1)
        print("🌐 回退到浏览器登录…")
        cookies = get_cookies_via_browser()
        if not cookies:
            msg = "❌ iamhc 浏览器登录失败，未获取到 cookies"
            print(msg)
            send_notification(msg)
            sys.exit(1)
        session = make_api_session(cookies)
        info = get_user_info(session)
        if not info:
            msg = "❌ iamhc 浏览器登录成功但 cookies 无效"
            print(msg)
            send_notification(msg)
            sys.exit(1)

    # ---------- 签到 ----------
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
