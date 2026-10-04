#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
iamhc 自动签到脚本
认证优先级：COOKIES（自动刷新 access_token） > 浏览器登录兜底
"""

import os, sys, time, json, requests
from datetime import datetime, timezone, timedelta

# ---- 环境变量 ----
EMAIL        = os.environ.get("EMAIL") or ""
PASSWORD     = os.environ.get("PASSWORD") or ""
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN") or ""
USER_ID      = os.environ.get("USER_ID") or ""
COOKIES      = os.environ.get("COOKIES") or ""
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000
TZ_CN = timezone(timedelta(hours=8))

LOGIN_URL  = f"{BASE_URL}/sign-in"
LOGIN_PATH = "/sign-in"


# ===========================================================================
# 工具
# ===========================================================================
def parse_cookie_string(s):
    cookies = {}
    for kv in s.split(";"):
        kv = kv.strip()
        if kv and "=" in kv:
            k, v = kv.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def _common_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/154.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/console",
    }


def make_session_with_token(access_token, user_id):
    s = requests.Session()
    h = _common_headers()
    h["Authorization"] = f"Bearer {access_token}"
    h["New-Api-User"] = str(user_id)
    s.headers.update(h)
    return s


def make_session_with_cookies(cookies):
    s = requests.Session()
    s.headers.update(_common_headers())
    for k, v in cookies.items():
        s.cookies.set(k, v, domain="api.hcnsec.cn")
    return s


def get_user_info(session):
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
    r = session.post(f"{BASE_URL}/api/user/checkin", json={}, timeout=20)
    try:
        return r.json()
    except ValueError:
        return {"success": False, "message": f"签到接口异常 HTTP {r.status_code}"}


def quota_to_dollar(q): return q / QUOTA_PER_UNIT
def fmt_usd(v):         return str(round(v))


# ===========================================================================
# 用 new_api_refresh cookie 换 access_token（关键新增）
# ===========================================================================
def refresh_access_token(session):
    """
    使用 new_api_refresh cookie 调 /api/user/auth，
    返回 (access_token, user_id)，失败返回 (None, None)。
    """
    endpoints = [
        f"{BASE_URL}/api/user/auth",
        f"{BASE_URL}/api/user/refresh",
        f"{BASE_URL}/api/user/token",
    ]
    for url in endpoints:
        try:
            r = session.post(url, json={}, timeout=20)
            print(f"    POST {url} -> HTTP {r.status_code}")
            if r.status_code == 200:
                try:
                    d = r.json()
                except ValueError:
                    print(f"      响应非 JSON: {r.text[:150]}")
                    continue
                if d.get("success"):
                    payload = d.get("data") or {}
                    token = (payload.get("access_token")
                             or payload.get("token")
                             or "")
                    ud = payload.get("user") or {}
                    uid = (ud.get("id") or ud.get("user_id") or ud.get("uid"))
                    if not uid and payload.get("id"):
                        uid = payload.get("id")
                    if token and uid:
                        print(f"    ✅ 刷新成功 | token 长度 {len(token)} | USER_ID={uid}")
                        return token, uid
                    print(f"      成功但缺少字段: {list(payload.keys())}")
        except Exception as e:
            print(f"    {url} 异常: {e}")
    return None, None


# ===========================================================================
# 浏览器登录（最终兜底）
# ===========================================================================
def get_auth_via_browser():
    """启动浏览器登录，返回 (access_token, user_id, cookies)。"""
    from seleniumbase import SB

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
            print(f"🌐 打开登录页: {LOGIN_URL}")
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            sb.sleep(3)

            print("⏳ 等待登录表单…")
            sb.wait_for_element_visible("input[name='username']", timeout=30)
            sb.wait_for_element_visible("input[name='password']", timeout=10)
            print("✅ 登录表单已加载")

            print("✍️ 填写登录凭证…")
            sb.type("input[name='username']", EMAIL)
            sb.sleep(0.5)
            sb.type("input[name='password']", PASSWORD)
            sb.sleep(0.5)

            print("☑️ 勾选法律同意复选框…")
            consent = "span[role='checkbox'][aria-labelledby='legal-consent-label']"
            try:
                sb.wait_for_element_present(consent, timeout=10)
                if sb.get_attribute(consent, "aria-checked") != "true":
                    sb.click(consent)
                    sb.sleep(0.6)
            except Exception as e:
                print(f"⚠️ 勾选复选框异常: {e}")

            print("⏳ 等待 Turnstile…")
            sb.sleep(5)
            try:
                sb.uc_gui_click_captcha()
            except Exception as e:
                print(f"⚠️ uc_gui_click_captcha 异常: {e}")

            print("⏳ 等待 Turnstile token…")
            for i in range(60):
                token = sb.execute_script(
                    'return (document.querySelector(\'[name="cf-turnstile-response"]\') || {}).value || "";'
                )
                if token:
                    print(f"✅ Turnstile token 已生成 | 长度: {len(token)}")
                    break
                sb.sleep(1)

            print("🖱️ 点击登录按钮…")
            sb.wait_for_element_visible("button[type='submit']", timeout=10)
            sb.click("button[type='submit']")

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
                sb.save_screenshot("login_failed.png")

            # 提取 cookies
            print("🍪 提取 cookies…")
            cookies = {}
            try:
                raw = sb.driver.get_cookies()
                if raw:
                    cookies = {c["name"]: c["value"] for c in raw}
                    print(f"  driver.get_cookies: {len(cookies)} 个")
            except Exception as e:
                print(f"  driver.get_cookies 失败: {e}")

            if not cookies:
                try:
                    raw = sb.execute_cdp_cmd("Network.getAllCookies", {})
                    clist = raw.get("cookies", [])
                    if clist:
                        cookies = {c["name"]: c["value"] for c in clist}
                        print(f"  CDP getAllCookies: {len(cookies)} 个")
                except Exception as e:
                    print(f"  CDP getAllCookies 失败: {e}")

            # 从 localStorage 提取 access_token
            access_token = ""
            user_id = None
            try:
                auth_json = sb.execute_script("""
                    try {
                        let userStr = localStorage.getItem('user');
                        if (userStr) {
                            const u = JSON.parse(userStr);
                            if (u && u.access_token) {
                                return JSON.stringify({
                                    token: u.access_token,
                                    uid: u.id || u.user_id || u.uid || null
                                });
                            }
                        }
                        for (let i = 0; i < localStorage.length; i++) {
                            const val = localStorage.getItem(localStorage.key(i));
                            if (val && val.startsWith('eyJ') && val.length > 100) {
                                return JSON.stringify({token: val, uid: null});
                            }
                        }
                        return '';
                    } catch(e) { return ''; }
                """)
                if auth_json:
                    d = json.loads(auth_json)
                    access_token = d.get("token", "")
                    user_id = d.get("uid")
            except Exception as e:
                print(f"  localStorage 提取失败: {e}")

            # 打印结果
            print("\n" + "=" * 70)
            if cookies:
                cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
                print("📋 请复制以下内容到 GitHub Secrets 的 COOKIES：")
                print("=" * 70)
                print(cookie_str)
                print("=" * 70)
            if access_token:
                print("📋 可选：ACCESS_TOKEN / USER_ID")
                print(f"ACCESS_TOKEN = {access_token}")
                print(f"USER_ID      = {user_id}")
                print("=" * 70 + "\n")

            return access_token, user_id, cookies

        except Exception as e:
            print(f"❌ 浏览器自动化异常: {e}")
            try:
                sb.save_screenshot("error_screenshot.png")
            except Exception:
                pass
            return None, None, None


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

    # ---------- 优先级 1：ACCESS_TOKEN + USER_ID ----------
    if ACCESS_TOKEN and USER_ID:
        print(f"🔑 尝试 ACCESS_TOKEN（长度 {len(ACCESS_TOKEN)}）+ USER_ID={USER_ID}")
        session = make_session_with_token(ACCESS_TOKEN, USER_ID)
        info = get_user_info(session)
        if info:
            print(f"✅ ACCESS_TOKEN 有效 | 用户: {info.get('username', '')}")
        else:
            print("⚠️ ACCESS_TOKEN 失效，继续下一路径")
            session = None
    else:
        print("ℹ️ 未配置 ACCESS_TOKEN/USER_ID")

    # ---------- 优先级 2：COOKIES（含自动刷新） ----------
    if session is None and COOKIES:
        print("🔑 尝试 COOKIES…")
        cookie_dict = parse_cookie_string(COOKIES)
        print(f"  解析到 {len(cookie_dict)} 个 cookie: {list(cookie_dict.keys())}")

        # 先用 cookies 直接试 /api/user/self
        session = make_session_with_cookies(cookie_dict)
        info = get_user_info(session)

        # 失败 → 用 new_api_refresh 换 access_token
        if not info and "new_api_refresh" in cookie_dict:
            print("  直接调用失败，用 new_api_refresh 刷新 access_token…")
            token, uid = refresh_access_token(session)
            if token and uid:
                session = make_session_with_token(token, uid)
                info = get_user_info(session)

        if info:
            print(f"✅ COOKIES 有效 | 用户: {info.get('username', '')}")
        else:
            print("⚠️ COOKIES 已失效，回退浏览器登录")
            session = None

    # ---------- 优先级 3：浏览器登录兜底 ----------
    if session is None:
        if not EMAIL or not PASSWORD:
            print("❌ 无法回退：未配置 EMAIL 和 PASSWORD")
            send_notification("❌ iamhc 所有认证方式失败：COOKIES 已失效且未配置 EMAIL/PASSWORD")
            sys.exit(1)
        print("🌐 回退到浏览器登录…")
        access_token, user_id, cookies = get_auth_via_browser()

        if access_token and user_id:
            session = make_session_with_token(access_token, user_id)
            info = get_user_info(session)

        if (not info) and cookies:
            # 尝试用新 cookies 调 refresh
            s2 = make_session_with_cookies(cookies)
            token, uid = refresh_access_token(s2)
            if token and uid:
                session = make_session_with_token(token, uid)
                info = get_user_info(session)

        if not info:
            msg = "❌ iamhc 所有认证方式均失败"
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
