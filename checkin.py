#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

EMAIL         = os.environ.get("EMAIL") or ""
PASSWORD      = os.environ.get("PASSWORD") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""

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


def auth_headers(access_token, user_id=None, json_body=False):
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0",
        "Origin": BASE_URL,
        "Referer": BASE_URL,
        "Authorization": f"Bearer {access_token}",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    if user_id:
        headers["New-Api-User"] = str(user_id)
    return headers


# ---------------------------------------------------------------------------
# 业务逻辑
# ---------------------------------------------------------------------------
def login(session: requests.Session):
    """登录并返回 id / username / access_token"""
    login_url = f"{BASE_URL}/api/user/login?turnstile={quote(TURNSTILE_TOKEN)}"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/login",
    }

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
    return {"id": user_id, "username": username, "access_token": access_token}


def get_user_info(session: requests.Session, user_id, access_token):
    url = f"{BASE_URL}/api/user/self"
    headers = auth_headers(access_token, user_id)

    resp = session.get(url, headers=headers, timeout=20)
    data = safe_json(resp)
    if not data:
        return None
    if not data.get("success"):
        print("获取用户信息失败:", data.get("message", ""))
        return None

    ud = data.get("data") or {}
    if isinstance(ud, dict) and "user" in ud and isinstance(ud["user"], dict):
        ud = ud["user"]
    return ud


def checkin(session: requests.Session, user_id, access_token):
    url = f"{BASE_URL}/api/user/checkin"
    headers = auth_headers(access_token, user_id, json_body=True)

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
    if not EMAIL or not PASSWORD:
        print("请先设置 EMAIL 和 PASSWORD 环境变量")
        sys.exit(1)

    session = make_session()

    user = login(session)
    if not user:
        print("\n登录失败，无法继续签到")
        sys.exit(1)

    user_id      = user["id"]
    username     = user.get("username", str(user_id))
    access_token = user["access_token"]

    # 签到前余额
    info_before = get_user_info(session, user_id, access_token)
    if not info_before:
        print("获取用户信息失败")
        sys.exit(1)
    balance_before = quota_to_dollar(info_before.get("quota", 0))

    # 签到
    checkin_data = checkin(session, user_id, access_token)

    # 签到后余额
    info_after = get_user_info(session, user_id, access_token)
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
