#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from patchright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
EMAIL         = os.environ.get("EMAIL") or ""
PASSWORD      = os.environ.get("PASSWORD") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""
BROWSER_PROXY = os.environ.get("BROWSER_PROXY") or ""

BASE_URL = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000          # 500000 quota = 1$
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
        allowed_methods=["GET"],       # 只重试 GET，避免重复签到
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
    return quota / QUOTA_PER_UNIT


def fmt_usd(v):
    """保留 2 位小数，避免小额奖励显示为 0"""
    return f"{v:.2f}"


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
# 浏览器：获取 Turnstile token
# ---------------------------------------------------------------------------
def get_turnstile_token(max_wait=45):
    """
    启动 Patchright 伪装浏览器，访问登录页，
    等待 Cloudflare Turnstile 自动完成，返回 token。
    """
    launch_kwargs = {
        "headless": False,          # 关键：有头模式（配合 xvfb）
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    }
    if BROWSER_PROXY:
        launch_kwargs["proxy"] = {"server": BROWSER_PROXY}

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()

        try:
            print("🌐 打开登录页，等待 Turnstile...")
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=30000)

            # 等待 Turnstile iframe 出现
            try:
                page.wait_for_selector(
                    "iframe[src*='challenges.cloudflare.com']", timeout=20000
                )
                print("🔒 检测到 Turnstile iframe")
            except Exception:
                print("⚠️ 未检测到 Turnstile iframe，可能站点未启用或已自动通过")

            # 轮询 token
            deadline = time.time() + max_wait
            token = ""
            while time.time() < deadline:
                token = page.evaluate(
                    """() => {
                        const el = document.querySelector('input[name="cf-turnstile-response"]');
                        return el ? el.value : "";
                    }"""
                )
                if token:
                    break

                # 尝试点击 Turnstile 复选框（部分站点需要交互）
                try:
                    frame = page.frame_locator("iframe[src*='challenges.cloudflare.com']")
                    frame.locator("input[type='checkbox'], #challenge-stage").click(timeout=800)
                except Exception:
                    pass

                time.sleep(1)

            if not token:
                # 兜底：有些站点把 token 放在隐藏字段里
                try:
                    token = page.eval_on_selector(
                        "input[name='cf-turnstile-response']",
                        "el => el.value",
                    )
                except Exception:
                    pass

            if not token:
                raise RuntimeError("Turnstile token 获取超时")

            print(f"✅ Turnstile token 获取成功 (长度 {len(token)})")
            return token

        finally:
            browser.close()


# ---------------------------------------------------------------------------
# 业务逻辑
# ---------------------------------------------------------------------------
def login(session: requests.Session, turnstile_token=""):
    """登录并返回 id / username / access_token"""
    login_url = f"{BASE_URL}/api/user/login?turnstile={quote(turnstile_token)}"

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

    if user_id is None:
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
def run():
    if not EMAIL or not PASSWORD:
        print("请先设置 EMAIL 和 PASSWORD 环境变量")
        sys.exit(1)

    # 1. 获取 Turnstile token
    try:
        turnstile_token = get_turnstile_token()
    except Exception as e:
        msg = f"❌ iamhc 签到失败：Turnstile 验证未通过\n{e}"
        print(msg)
        send_notification(msg)
        sys.exit(1)

    session = make_session()

    # 2. 用 token 登录
    user = login(session, turnstile_token)
    if not user:
        msg = "❌ iamhc 登录失败，无法继续签到"
        print(msg)
        send_notification(msg)
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
            f"💰 签到前余额: {fmt_usd(balance_before)}$\n"
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
            f"💰 签到前余额: {fmt_usd(balance_before)}$\n"
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
            f"💰 签到前余额: {fmt_usd(balance_before)}$\n"
            f"💰 当前余额: {fmt_usd(balance_after)}$\n"
            f"⏱️ 签到时间: {now}\n"
            f"{BASE_URL}"
        )

    send_notification(message)


def main():
    try:
        run()
    except Exception as e:
        msg = f"❌ iamhc 签到脚本异常\n{e}"
        print(msg)
        send_notification(msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
