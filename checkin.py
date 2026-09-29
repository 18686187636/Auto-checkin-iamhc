#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, json, requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

EMAIL         = os.environ.get("EMAIL") or ""
PASSWORD      = os.environ.get("PASSWORD") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""
PROXY_URL     = os.environ.get("PROXY_URL") or ""

BASE_URL = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000
TZ_CN = timezone(timedelta(hours=8))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def fmt_usd(v): return str(round(v))


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

    with sync_playwright() as p:
        launch_opts = {
            "headless": False,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if PROXY_URL:
            launch_opts["proxy"] = {"server": PROXY_URL}

        browser = p.chromium.launch(**launch_opts)
        context = browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 768},
            locale="zh-CN",
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
        """)

        page = context.new_page()
        page.on("response", lambda r: print(f"  [net] {r.status} {r.url}") if "hcnsec" in r.url else None)

        print("→ 打开登录页")
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=60000)

        # 等表单渲染
        page.wait_for_selector('input', timeout=30000)

        # 填邮箱
        print("→ 填写账号")
        for sel in ['input[type="email"]', 'input[name="username"]',
                    'input[placeholder*="邮箱"]', 'input[placeholder*="用户"]']:
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

        # 勾选用户协议
        print("→ 勾选用户协议")
        agreed = False
        for sel in [
            'input[type="checkbox"]',
            '[role="checkbox"]',
            'button[role="checkbox"]',
            '[data-state="unchecked"]',
        ]:
            try:
                els = page.query_selector_all(sel)
                for el in els:
                    try:
                        el.click(force=True, timeout=3000)
                        agreed = True
                        break
                    except Exception:
                        continue
                if agreed:
                    break
            except Exception:
                continue

        if not agreed:
            try:
                span = page.query_selector('span:has-text("我已阅读并同意")')
                if span:
                    box = span.evaluate_handle("""el => {
                        let n = el.previousElementSibling;
                        if (n) return n;
                        return el.parentElement ? el.parentElement.querySelector('[role="checkbox"], input[type="checkbox"], button') : null;
                    }""")
                    if box and box.as_element():
                        box.as_element().click(force=True, timeout=3000)
                        agreed = True
            except Exception as e:
                print(f"  ⚠️ 备用方式失败: {e}")

        print("  ✅ 已勾选" if agreed else "  ⚠️ 未勾选成功")
        time.sleep(1)

        # 等 Turnstile
        print("→ 等待 Turnstile widget 加载...")
        try:
            page.wait_for_selector(
                'iframe[src*="challenges.cloudflare.com"], .cf-turnstile, [class*="turnstile"]',
                timeout=30000,
            )
            print("  ✅ Turnstile widget 已加载")
        except PWTimeout:
            print("  ⚠️ 未检测到 Turnstile，继续...")

        print("→ 等待 Turnstile 自动通过（最多 60s）...")
        token = None
        for i in range(60):
            token = page.evaluate("""() => {
                if (window.turnstile && window.turnstile.getResponse) {
                    const els = document.querySelectorAll('.cf-turnstile, [data-sitekey]');
                    for (const el of els) {
                        try {
                            const r = window.turnstile.getResponse(el);
                            if (r && r.length > 20) return r;
                        } catch(e) {}
                    }
                }
                const inp = document.querySelector(
                    'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]'
                );
                if (inp && inp.value && inp.value.length > 20) return inp.value;
                return null;
            }""")
            if token:
                print(f"  ✅ 拿到 Turnstile token: {token[:40]}...")
                break
            time.sleep(1)

        if not token:
            print("❌ 60s 内未拿到 Turnstile token")
            try:
                page.screenshot(path="login_fail.png", full_page=True)
            except Exception:
                pass
            browser.close()
            sys.exit(1)

        # 直接 fetch 登录
        print("→ 调用登录接口")
        login_result = page.evaluate("""async ({turnstile, username, password}) => {
            try {
                const r = await fetch('/api/user/login?turnstile=' + encodeURIComponent(turnstile), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json, text/plain, */*',
                    },
                    credentials: 'include',
                    body: JSON.stringify({username, password})
                });
                return await r.json();
            } catch(e) {
                return {success: false, message: 'fetch error: ' + String(e)};
            }
        }""", {"turnstile": token, "username": EMAIL, "password": PASSWORD})

        print(f"  登录返回: success={login_result.get('success')} msg={login_result.get('message','')}")

        # 兜底：点按钮
        if not login_result.get("success"):
            print("→ fetch 登录失败，尝试点击登录按钮...")
            clicked = False
            for sel in [
                'button[type="submit"]:not([disabled])',
                'button:has-text("登录"):not([disabled])',
                'button:has-text("Login"):not([disabled])',
            ]:
                try:
                    page.click(sel, timeout=5000, force=True)
                    clicked = True
                    print(f"  ✅ 点击 {sel}")
                    break
                except PWTimeout:
                    continue

            if clicked:
                try:
                    page.wait_for_url("**/dashboard/**", timeout=30000)
                except PWTimeout:
                    pass
                time.sleep(2)
                check = page.evaluate("""async () => {
                    try {
                        const r = await fetch('/api/user/self', {credentials: 'include'});
                        return await r.json();
                    } catch(e) { return {success: false}; }
                }""")
                if check.get("success"):
                    print("  ✅ UI 登录成功")
                    login_result = {"success": True}

        if not login_result.get("success"):
            print(f"❌ 登录失败: {login_result.get('message','')}")
            try:
                page.screenshot(path="login_fail.png", full_page=True)
            except Exception:
                pass
            browser.close()
            sys.exit(1)

        print("✅ 登录成功")

        # 签到
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
            } catch(e) { return {success: false, message: String(e)}; }
        }""")

        user_info = page.evaluate("""async () => {
            try {
                const r = await fetch('/api/user/self', {credentials: 'include'});
                return await r.json();
            } catch(e) { return {success: false}; }
        }""")

        browser.close()

    # ---- 解析 ----
    success = checkin_result.get("success", False)
    msg = str(checkin_result.get("message", "") or "")

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
        result_msg = (f"🎁 iamhc 签到通知\n\n✅ 签到成功，获得 {fmt_usd(awarded)}$\n"
                      f"👤 账户: {username}\n💰 当前余额: {fmt_usd(balance)}$\n"
                      f"⏱️ {now}\n{BASE_URL}")
    elif any(k in msg for k in ("已签到", "重复签到", "今天已签到")):
        result_msg = (f"🎁 iamhc 签到通知\n\n✅ 今日已签到\n"
                      f"👤 账户: {username}\n💰 当前余额: {fmt_usd(balance)}$\n"
                      f"⏱️ {now}\n{BASE_URL}")
    else:
        result_msg = (f"🎁 iamhc 签到通知\n\n❌ 签到失败: {msg}\n"
                      f"👤 账户: {username}\n💰 当前余额: {fmt_usd(balance)}$\n"
                      f"⏱️ {now}\n{BASE_URL}")

    send_notification(result_msg)

    if not success and not any(k in msg for k in ("已签到", "重复签到", "今天已签到")):
        sys.exit(1)


if __name__ == "__main__":
    main()
