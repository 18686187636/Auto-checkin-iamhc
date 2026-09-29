#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, json, requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

EMAIL         = os.environ.get("EMAIL") or ""
PASSWORD      = os.environ.get("PASSWORD") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""
PROXY_URL     = os.environ.get("PROXY_URL") or ""   # 例如 http://127.0.0.1:1081

BASE_URL = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000
TZ_CN = timezone(timedelta(hours=8))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def fmt_usd(v):
    return str(round(v))


def send_notification(message):
    print("\n" + "=" * 25)
    print(message)
    print("=" * 25)
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": message},
                timeout=10,
            )
            print("Telegram:", r.status_code)
        except Exception as e:
            print("Telegram 失败:", e)


def main():
    if not EMAIL or not PASSWORD:
        print("请先设置 EMAIL 和 PASSWORD")
        sys.exit(1)

    now = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    result_msg = ""

    with sync_playwright() as p:
        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]
        launch_opts = {
            "headless": False,      # xvfb 下为“有头”，能过 Turnstile
            "args": launch_args,
        }
        if PROXY_URL:
            launch_opts["proxy"] = {"server": PROXY_URL}

        browser = p.chromium.launch(**launch_opts)
        context = browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 768},
            locale="zh-CN",
        )
        # 抹掉 webdriver 痕迹
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
        """)

        page = context.new_page()

        print("→ 打开登录页")
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=60000)

        # 等待页面渲染出输入框
        page.wait_for_selector('input', timeout=30000)

        # 填邮箱
        print("→ 填写账号")
        # 尽量兼容不同 selector
        for sel in ['input[type="email"]', 'input[name="username"]', 'input[placeholder*="邮箱"]', 'input[placeholder*="用户"]']:
            try:
                page.fill(sel, EMAIL, timeout=3000)
                break
            except PWTimeout:
                continue

        # 填密码
        for sel in ['input[type="password"]', 'input[name="password"]']:
            try:
                page.fill(sel, PASSWORD, timeout=3000)
                break
            except PWTimeout:
                continue

        print("→ 等待 Turnstile（最多 60s）")
        # Turnstile 通过后，页面里会出现一个 hidden input[name="cf-turnstile-response"] 或类似
        # 我们也可能直接看到登录按钮可点
        time.sleep(3)

        # 点登录按钮
        print("→ 点击登录")
        clicked = False
        for sel in ['button[type="submit"]', 'button:has-text("登录")', 'button:has-text("Login")']:
            try:
                page.click(sel, timeout=5000)
                clicked = True
                break
            except PWTimeout:
                continue
        if not clicked:
            print("❌ 未找到登录按钮")
            browser.close()
            sys.exit(1)

        # 等待跳转到 dashboard
        try:
            page.wait_for_url("**/dashboard/**", timeout=60000)
            print("✅ 登录成功，已进入 dashboard")
        except PWTimeout:
            print("❌ 登录超时，可能 Turnstile 未通过或密码错误")
            # 截图方便排查（可选）
            try:
                page.screenshot(path="login_fail.png")
                print("已保存截图 login_fail.png")
            except Exception:
                pass
            browser.close()
            sys.exit(1)

        time.sleep(2)

        # 在浏览器里直接 fetch 签到接口
        print("→ 调用签到接口")
        checkin_result = page.evaluate("""async () => {
            try {
                const r = await fetch('/api/user/checkin', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: '{}'
                });
                return await r.json();
            } catch(e) {
                return {success: false, message: String(e)};
            }
        }""")

        # 获取用户信息（余额）
        user_info = page.evaluate("""async () => {
            try {
                const r = await fetch('/api/user/self', {credentials: 'include'});
                return await r.json();
            } catch(e) {
                return {success: false, message: String(e)};
            }
        }""")

        browser.close()

    # ---- 解析结果 ----
    success = checkin_result.get("success", False)
    msg = str(checkin_result.get("message", "") or "")

    # 提取余额
    username = "shenlan"
    balance = 0
    try:
        ud = user_info.get("data") or {}
        if isinstance(ud, dict) and "user" in ud:
            ud = ud["user"]
        username = ud.get("username") or username
        balance = (ud.get("quota") or 0) / QUOTA_PER_UNIT
    except Exception:
        pass

    awarded = 0
    try:
        awarded_quota = (checkin_result.get("data") or {}).get("quota_awarded", 0) or 0
        awarded = awarded_quota / QUOTA_PER_UNIT
    except Exception:
        pass

    if success:
        result_msg = (
            f"🎁 iamhc 签到通知\n\n"
            f"✅ 签到成功，获得 {fmt_usd(awarded)}$\n"
            f"👤 账户: {username}\n"
            f"💰 当前余额: {fmt_usd(balance)}$\n"
            f"⏱️ {now}\n{BASE_URL}"
        )
    elif any(k in msg for k in ("已签到", "重复签到", "今天已签到")):
        result_msg = (
            f"🎁 iamhc 签到通知\n\n"
            f"✅ 今日已签到\n"
            f"👤 账户: {username}\n"
            f"💰 当前余额: {fmt_usd(balance)}$\n"
            f"⏱️ {now}\n{BASE_URL}"
        )
    else:
        result_msg = (
            f"🎁 iamhc 签到通知\n\n"
            f"❌ 签到失败: {msg}\n"
            f"👤 账户: {username}\n"
            f"💰 当前余额: {fmt_usd(balance)}$\n"
            f"⏱️ {now}\n{BASE_URL}"
        )

    send_notification(result_msg)

    # 失败时退出码非 0，让 workflow 标红
    if not success and not any(k in msg for k in ("已签到", "重复签到", "今天已签到")):
        sys.exit(1)


if __name__ == "__main__":
    main()
