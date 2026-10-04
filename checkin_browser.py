#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
iamhc 自动签到脚本
认证：COOKIES → refresh → access_token
签到：API 优先，遇 Turnstile 则切换浏览器自动签到
"""

import os, sys, time, json, requests
from datetime import datetime, timezone, timedelta

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
# 工具函数
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
# 刷新 access_token（端点已修正）
# ===========================================================================
def refresh_access_token(session):
    url = f"{BASE_URL}/api/user/auth/refresh"
    try:
        r = session.post(url, json={}, timeout=20)
        print(f"    POST {url} -> HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"      响应: {r.text[:200]}")
            return None, None
        try:
            d = r.json()
        except ValueError:
            return None, None
        if not d.get("success"):
            print(f"      业务失败: {d.get('message', '')}")
            return None, None
        payload = d.get("data") or {}
        token = payload.get("access_token") or payload.get("token") or ""
        ud = payload.get("user") or payload
        uid = ud.get("id") or ud.get("user_id") or ud.get("uid")
        if token and uid:
            print(f"    ✅ 刷新成功 | token 长度 {len(token)} | USER_ID={uid}")
            return token, uid
        print(f"      成功但缺少字段: {list(payload.keys())}")
        return None, None
    except Exception as e:
        print(f"    {url} 异常: {e}")
        return None, None


# ===========================================================================
# 浏览器自动签到（新增：签到接口需要 Turnstile）
# ===========================================================================
def browser_auto_checkin(cookies_dict):
    """
    用浏览器打开签到页，注入 cookies 保持登录态，
    找到签到按钮点击，自动过 Turnstile，返回页面提示信息。
    """
    from seleniumbase import SB

    print("  🚀 启动浏览器自动签到…")

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
            # 1. 打开站点
            print("  🌐 打开站点首页…")
            sb.uc_open_with_reconnect(BASE_URL, reconnect_time=4)
            sb.sleep(2)

            # 2. 注入 cookies 保持登录态
            if cookies_dict:
                print(f"  🍪 注入 {len(cookies_dict)} 个 cookies…")
                for name, value in cookies_dict.items():
                    try:
                        cookie = {
                            "name": name,
                            "value": value,
                            "domain": "api.hcnsec.cn",
                            "path": "/",
                            "secure": False,
                        }
                        if name == "new_api_refresh":
                            cookie["path"] = "/api/user/auth"
                        if name in ("session", "new_api_refresh"):
                            cookie["httpOnly"] = True
                        sb.add_cookie(cookie)
                    except Exception as e:
                        print(f"    注入 {name} 失败: {e}")

            # 3. 打开签到页面
            print("  📄 打开签到页面…")
            page_ok = False
            for path in ["/console", "/dashboard", "/dashboard/overview", "/console/personal"]:
                try:
                    sb.open(f"{BASE_URL}{path}")
                    sb.sleep(3)
                    cur = sb.get_current_url()
                    if "sign-in" not in cur and "login" not in cur:
                        print(f"    已打开: {cur}")
                        page_ok = True
                        break
                except Exception as e:
                    print(f"    {path} 打开失败: {e}")

            if not page_ok:
                print("  ❌ 无法打开签到页面")
                return None

            # 4. 列出所有按钮文字（帮你排查）
            print("  🔍 扫描页面按钮…")
            btns = sb.execute_script("""
                return JSON.stringify(
                    Array.from(document.querySelectorAll('button, a, [role="button"]'))
                        .map(b => (b.textContent || '').trim())
                        .filter(t => t && t.length < 30)
                );
            """)
            print(f"    按钮列表: {btns}")

            # 5. 点击含"签到"的按钮
            clicked = sb.execute_script("""
                const elems = document.querySelectorAll('button, a, [role="button"]');
                for (let i = 0; i < elems.length; i++) {
                    const t = (elems[i].textContent || '').trim();
                    if (t && t.length < 20 && (t === '签到' || t === '每日签到' || t.includes('签到'))) {
                        elems[i].setAttribute('data-chk', '1');
                        elems[i].click();
                        return t;
                    }
                }
                return '';
            """)

            if not clicked:
                print("  ⚠️ 未找到签到按钮，请从上面日志确认按钮文字")
                return None

            print(f"  ✅ 已点击: '{clicked}'")
            sb.sleep(5)

            # 6. 处理可能弹出的 Turnstile
            try:
                sb.uc_gui_click_captcha()
            except Exception:
                pass
            sb.sleep(3)

            # 7. 读取提示信息
            result = sb.execute_script("""
                const sels = '[class*="toast"], [class*="alert"], [class*="message"], [role="alert"], [class*="notification"], .Toastify__toast';
                const toasts = document.querySelectorAll(sels);
                return JSON.stringify(
                    Array.from(toasts).map(t => (t.textContent || '').trim()).filter(Boolean)
                );
            """)
            print(f"  📢 页面提示: {result}")

            # 8. 打印更新后的 cookies（可能被刷新）
            try:
                raw = sb.driver.get_cookies()
                if raw:
                    new_cookies = {c["name"]: c["value"] for c in raw}
                    cookie_str = "; ".join([f"{k}={v}" for k, v in new_cookies.items()])
                    print("\n  📋 更新后的 cookies（如需替换 Secrets）：")
                    print(f"  {cookie_str}\n")
            except Exception:
                pass

            return result
        except Exception as e:
            print(f"  ❌ 浏览器签到异常: {e}")
            try:
                sb.save_screenshot("browser_checkin_error.png")
            except Exception:
                pass
            return None


# ===========================================================================
# 浏览器登录（兜底，cookies 完全失效时）
# ===========================================================================
def get_auth_via_browser():
    from seleniumbase import SB

    print("🚀 启动浏览器登录…")
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
                    sb.sleep(0.6)
            except Exception as e:
                print(f"⚠️ 勾选异常: {e}")

            print("⏳ 等待 Turnstile…")
            sb.sleep(5)
            try:
                sb.uc_gui_click_captcha()
            except Exception as e:
                print(f"⚠️ click_captcha 异常: {e}")

            for i in range(60):
                token = sb.execute_script(
                    'return (document.querySelector(\'[name="cf-turnstile-response"]\') || {}).value || "";'
                )
                if token:
                    print(f"✅ Turnstile token 长度: {len(token)}")
                    break
                sb.sleep(1)

            print("🖱️ 点击登录…")
            sb.wait_for_element_visible("button[type='submit']", timeout=10)
            sb.click("button[type='submit']")

            print("⏳ 等待登录完成…")
            for i in range(30):
                sb.sleep(2)
                if LOGIN_PATH not in sb.get_current_url():
                    print(f"✅ 登录成功: {sb.get_current_url()}")
                    break

            sb.sleep(5)

            cookies = {}
            try:
                raw = sb.driver.get_cookies()
                if raw:
                    cookies = {c["name"]: c["value"] for c in raw}
                    print(f"🍪 提取 cookies: {list(cookies.keys())}")
            except Exception as e:
                print(f"  cookies 提取失败: {e}")

            if cookies:
                cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
                print("\n" + "=" * 70)
                print("📋 请复制到 GitHub Secrets 的 COOKIES：")
                print(cookie_str)
                print("=" * 70 + "\n")

            return cookies
        except Exception as e:
            print(f"❌ 浏览器登录异常: {e}")
            try:
                sb.save_screenshot("login_error.png")
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
    cookies_dict = {}

    # ---------- 优先级 1：ACCESS_TOKEN ----------
    if ACCESS_TOKEN and USER_ID:
        print(f"🔑 尝试 ACCESS_TOKEN（长度 {len(ACCESS_TOKEN)}）")
        session = make_session_with_token(ACCESS_TOKEN, USER_ID)
        info = get_user_info(session)
        if info:
            print(f"✅ ACCESS_TOKEN 有效 | 用户: {info.get('username', '')}")
        else:
            print("⚠️ ACCESS_TOKEN 失效")
            session = None

    # ---------- 优先级 2：COOKIES ----------
    if session is None and COOKIES:
        print("🔑 尝试 COOKIES…")
        cookies_dict = parse_cookie_string(COOKIES)
        print(f"  解析到 {len(cookies_dict)} 个 cookie: {list(cookies_dict.keys())}")
        session = make_session_with_cookies(cookies_dict)
        info = get_user_info(session)

        if not info and "new_api_refresh" in cookies_dict:
            print("  直接调用失败，用 new_api_refresh 刷新 access_token…")
            token, uid = refresh_access_token(session)
            if token and uid:
                session = make_session_with_token(token, uid)
                info = get_user_info(session)

        if info:
            print(f"✅ COOKIES 有效 | 用户: {info.get('username', '')}")
        else:
            print("⚠️ COOKIES 已失效")
            session = None

    # ---------- 优先级 3：浏览器登录兜底 ----------
    if session is None:
        if not EMAIL or not PASSWORD:
            msg = "❌ 无法回退：未配置 EMAIL / PASSWORD"
            print(msg)
            send_notification(msg)
            sys.exit(1)
        print("🌐 回退到浏览器登录…")
        new_cookies = get_auth_via_browser()
        if new_cookies:
            cookies_dict = new_cookies
            session = make_session_with_cookies(new_cookies)
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

    print("📡 尝试 API 签到…")
    ck = checkin(session)
    print(f"  响应: {ck}")

    now = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    msg = str(ck.get("message", "") or "")

    # API 签到成功
    if ck.get("success"):
        info2 = get_user_info(session)
        bal_after = quota_to_dollar(info2.get("quota", 0)) if info2 else bal_before
        awarded = (ck.get("data") or {}).get("quota_awarded", 0) or 0
        awarded_d = quota_to_dollar(awarded) if awarded else (bal_after - bal_before)
        print(f"✅ 签到成功 | 获得 {fmt_usd(awarded_d)}$")
        message = (f"🎁 iamhc 签到通知\n\n✅ 签到成功，获得 {fmt_usd(awarded_d)}$\n"
                   f"👤 {username}\n💰 昨日: {fmt_usd(bal_before)}$\n"
                   f"💰 当前: {fmt_usd(bal_after)}$\n⏱️ {now}\n{BASE_URL}")
        send_notification(message)
        return

    # 已签到
    if any(k in msg for k in ("已签到", "重复签到", "今天已签到")):
        print(f"✅ 今日已签到")
        message = (f"🎁 iamhc 签到通知\n\n✅ 今日已签到\n"
                   f"👤 {username}\n💰 余额: {fmt_usd(bal_before)}$\n"
                   f"⏱️ {now}\n{BASE_URL}")
        send_notification(message)
        return

    # 需要 Turnstile → 浏览器自动签到
    if "Turnstile" in msg or "turnstile" in msg:
        print("⚠️ 签到接口需要 Turnstile token，切换浏览器自动签到…")
        result = browser_auto_checkin(cookies_dict)
        print(f"  浏览器签到结果: {result}")

        # 浏览器签到后，用 API 复查余额
        try:
            info2 = get_user_info(session)
            bal_after = quota_to_dollar(info2.get("quota", 0)) if info2 else bal_before
        except Exception:
            bal_after = bal_before

        success_browser = False
        result_str = str(result or "")
        if any(k in result_str for k in ("成功", "获得", "已签到", "重复", "success")):
            success_browser = True

        if success_browser:
            message = (f"🎁 iamhc 签到通知\n\n✅ 浏览器签到完成\n"
                       f"📢 页面提示: {result_str[:200]}\n"
                       f"👤 {username}\n💰 昨日: {fmt_usd(bal_before)}$\n"
                       f"💰 当前: {fmt_usd(bal_after)}$\n⏱️ {now}\n{BASE_URL}")
        else:
            message = (f"🎁 iamhc 签到通知\n\n⚠️ 浏览器签到返回异常\n"
                       f"📢 页面提示: {result_str[:200]}\n"
                       f"👤 {username}\n💰 余额: {fmt_usd(bal_before)}$\n"
                       f"⏱️ {now}\n{BASE_URL}")
        send_notification(message)
        return

    # 其他失败
    print(f"❌ 签到失败 | {msg}")
    message = (f"🎁 iamhc 签到通知\n\n❌ 签到失败: {msg}\n"
               f"👤 {username}\n💰 余额: {fmt_usd(bal_before)}$\n"
               f"⏱️ {now}\n{BASE_URL}")
    send_notification(message)


if __name__ == "__main__":
    main()
