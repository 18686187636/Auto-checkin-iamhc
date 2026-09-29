#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

EMAIL          = os.environ.get("EMAIL") or ""
PASSWORD       = os.environ.get("PASSWORD") or ""
SESSION_COOKIE = os.environ.get("SESSION_COOKIE") or ""
TG_CHAT_ID     = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN   = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000          # 500000 quota = 1$
TURNSTILE_TOKEN = ""

TZ_CN = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://",  HTTPAdapter(max_retries=retry))
    return s


def safe_json(resp):
    try:
        return resp.json()
    except ValueError:
        print(f"响应非 JSON | HTTP {resp.status_code} | {resp.text[:200]}")
        return None


def quota_to_dollar(quota):
    """quota -> 美元（float，保留精度）"""
    return quota / QUOTA_PER_UNIT


def fmt_usd(v):
    """金额格式化为整数（四舍五入），用于余额和签到奖励展示"""
    return str(round(v))


def base_headers():
    return {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0",
        "Origin": BASE_URL,
        "Referer": BASE_URL,
    }


def auth_headers(user_id=None, json_body=False, cookie=None):
    headers = base_headers()
    if cookie:
        headers["Cookie"] = cookie
    if json_body:
        headers["Content-Type"] = "application/json"
    if user_id:
        headers["New-Api-User"] = str(user_id)
    return headers


def unwrap_user(data):
    """兼容 {data: {...}} 与 {data: {user: {...}}}"""
    ud = data.get("data") or {}
    if isinstance(ud, dict) and "user" in ud and isinstance(ud["user"], dict):
        ud = ud["user"]
    return ud if isinstance(ud, dict) else {}


# ---------------------------------------------------------------------------
# 登录方式 1：SESSION_COOKIE（推荐，长期有效）
# ---------------------------------------------------------------------------
def load_user_from_cookie(session: requests.Session):
    """用 session cookie 鉴权，跳过登录与 Turnstile"""
    if not SESSION_COOKIE:
        return None

    # 用 header 形式发送 cookie，避免 requests 的 domain 匹配问题
    cookie_header = f"session={SESSION_COOKIE}; new_api_has_session=1"

    url = f"{BASE_URL}/api/user/self"
    resp = session.get(url, headers=auth_headers(cookie=cookie_header), timeout=20)
    data = safe_json(resp)

    if not data:
        print("❌ 使用 SESSION_COOKIE 获取用户信息失败（响应非 JSON）")
        return None
    if not data.get("success"):
        msg = data.get("message", "")
        print(f"❌ SESSION_COOKIE 校验失败: {msg}")
        print("   可能原因：cookie 已过期 / 被服务端清理 / 复制不完整")
        return None

    ud = unwrap_user(data)
    user_id  = ud.get("id") or ud.get("user_id") or ud.get("uid")
    username = ud.get("username") or ""
    if not user_id:
        print("❌ cookie 有效但拿不到 user_id，user_data keys =", list(ud.keys()))
        return None

    # 后续请求统一带 Cookie 和 New-Api-User
    session.headers.update({
        "Cookie": cookie_header,
        "New-Api-User": str(user_id),
    })

    print(f"✅ 使用 SESSION_COOKIE 登录成功 | 账户: {username} | ID: {user_id}")
    return {"id": user_id, "username": username, "cookie": cookie_header}


# ---------------------------------------------------------------------------
# 登录方式 2：账号密码（兜底；需 Turnstile token，通常不可用）
# ---------------------------------------------------------------------------
def login(session: requests.Session):
    if not EMAIL or not PASSWORD:
        return None

    login_url = f"{BASE_URL}/api/user/login?turnstile={quote(TURNSTILE_TOKEN)}"
    headers = base_headers()
    headers["Content-Type"] = "application/json"
    headers["Referer"] = f"{BASE_URL}/login"

    resp = session.post(
        login_url,
        headers=headers,
        json={"username": EMAIL, "password": PASSWORD},
        timeout=20,
    )

    if resp.status_code != 200:
        print("登录请求失败:", resp.status_code, resp.text[:200])
        return None

    data = safe_json(resp)
    if not data:
        return None
    if not data.get("success"):
        print("登录失败:", data.get("message", ""))
        return None

    payload      = data.get("data") or {}
    access_token = payload.get("access_token") or ""
    user_data    = payload.get("user") or {}

    user_id  = user_data.get("id") or user_data.get("user_id") or user_data.get("uid")
    username = user_data.get("username", "") or ""

    if not user_id:
        print("登录成功但未获取到用户 ID，user_data keys =", list(user_data.keys()))
        return None
    if not access_token:
        print("登录成功但未获取到 access_token")
        return None

    session.headers.update({
        "Authorization": f"Bearer {access_token}",
        "New-Api-User":  str(user_id),
    })

    print(f"✅ 登录成功 | 账户: {username} | ID: {user_id}")
    return {"id": user_id, "username": username, "cookie": None}


# ---------------------------------------------------------------------------
# 业务接口
# ---------------------------------------------------------------------------
def get_user_info(session: requests.Session, user_id, cookie=None):
    url = f"{BASE_URL}/api/user/self"
    headers = auth_headers(user_id, cookie=cookie)

    resp = session.get(url, headers=headers, timeout=20)
    data = safe_json(resp)
    if not data:
        return None
    if not data.get("success"):
        print("获取用户信息失败:", data.get("message", ""))
        return None

    return unwrap_user(data)


def checkin(session: requests.Session, user_id, cookie=None):
    url = f"{BASE_URL}/api/user/checkin"
    headers = auth_headers(user_id, json_body=True, cookie=cookie)

    resp = session.post(url, headers=headers, json={}, timeout=20)
    data = safe_json(resp)
    return data or {"success": False, "message": f"签到接口异常 HTTP {resp.status_code}"}


def send_notification(message):
    print("\n" + "=" * 25)
    print(message)
    print("=" * 25)

    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            tg_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            resp = requests.post(
                tg_url,
                json={"chat_id": TG_CHAT_ID, "text": message},
                timeout=10,
            )
            if resp.status_code == 200:
                print("Telegram 通知发送成功")
            else:
                print(f"Telegram 通知发送失败: {resp.status_code} {resp.text}")
        except Exception as e:
            print("Telegram 通知发送失败:", e)
    else:
        print("未配置 TG_BOT_TOKEN / TG_CHAT_ID，跳过 Telegram 推送")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    session = make_session()

    # 优先 SESSION_COOKIE
    user = load_user_from_cookie(session)
    if not user:
        if not EMAIL or not PASSWORD:
            print("❌ SESSION_COOKIE 失效，且未配置 EMAIL/PASSWORD，无法登录")
            sys.exit(1)
        print("\n⚠️ SESSION_COOKIE 方式失败，尝试账号密码登录作为兜底...")
        user = login(session)

    if not user:
        print("\n登录失败，无法继续签到")
        sys.exit(1)

    user_id  = user["id"]
    username = user.get("username", str(user_id))
    cookie   = user.get("cookie")

    # 签到前余额
    info_before = get_user_info(session, user_id, cookie)
    if not info_before:
        print("获取用户信息失败")
        sys.exit(1)
    balance_before = quota_to_dollar(info_before.get("quota", 0))

    # 签到
    checkin_data = checkin(session, user_id, cookie)

    # 签到后余额
    info_after = get_user_info(session, user_id, cookie)
    if not info_after:
        print("获取签到后用户信息失败")
        sys.exit(1)
    balance_after = quota_to_dollar(info_after.get("quota", 0))

    now = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    success = checkin_data.get("success", False)
    msg = str(checkin_data.get("message", "") or "")

    if success:
        awarded_data = checkin_data.get("data") or {}
        awarded_quota = awarded_data.get("quota_awarded", 0) or 0
        awarded_dollar = quota_to_dollar(awarded_quota) if awarded_quota else (balance_after - balance_before)

        print(f"✅ 签到成功 | 获得: {fmt_usd(awarded_dollar)}$")

        message = (
            f"🎁 iamhc 签到通知\n\n"
            f"✅ 签到成功,本次签到获得 {fmt_usd(awarded_dollar)}$\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {fmt_usd(balance_before)}$\n"
            f"💰 当前余额: {fmt_usd(balance_after)}$\n"
            f"⏱️ 签到时间: {now}\n"
            f"{BASE_URL}"
        )

    elif any(k in msg for k in ("已签到", "重复签到", "今天已签到")):
        print(f"✅ 今日已签到 | 当前余额: {fmt_usd(balance_after)}$")

        message = (
            f"🎁 iamhc 签到通知\n\n"
            f"✅ 今日你已经签到过了！\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {fmt_usd(balance_before)}$\n"
            f"💰 当前余额: {fmt_usd(balance_after)}$\n"
            f"⏱️ 签到时间: {now}\n"
            f"{BASE_URL}"
        )

    else:
        print(f"❌ 签到失败 | {msg}")

        message = (
            f"🎁 iamhc 签到通知\n\n"
            f"❌ 签到失败: {msg}\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {fmt_usd(balance_before)}$\n"
            f"💰 当前余额: {fmt_usd(balance_after)}$\n"
            f"⏱️ 签到时间: {now}\n"
            f"{BASE_URL}"
        )

    send_notification(message)


if __name__ == "__main__":
    main()
