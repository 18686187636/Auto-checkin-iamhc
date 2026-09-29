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


def log(*a):
    print(*a, flush=True)


def fmt_usd(v): return str(round(v))


def send_notification(message):
    log("\n" + "=" * 25)
    log(message)
    log("=" * 25)
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": message},
                timeout=10,
            )
            log("Telegram:", r.status_code)
        except Exception as e:
            log("Telegram 失败:", e)


def main():
    if not EMAIL or not PASSWORD:
        log("请先设置 EMAIL 和 PASSWORD")
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
            log(f"→ 浏览器将走代理: {PROXY_URL}")

        log("→ 启动浏览器...")
        browser = p.chromium.launch(**launch_opts)
        log("  ✅ 浏览器已启动")

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

        def on_response(r):
            u = r.url
            if ("hcnsec" in u) or ("cloudflare" in u) or ("turnstile" in u) or ("challenges" in u):
                log(f"  [net] {r.status} {u}")
        page.on("response", on_response)

        def on_request_failed(req):
            log(f"  [FAIL] {req.url} - {req.failure}")
        page.on("requestfailed", on_request_failed)

        log(f"→ 打开登录页 {BASE_URL}/login")
        try:
            page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=60000)
            log("  ✅ 页面已加载")
        except Exception as e:
            log(f"  ⚠️ networkidle 超时，继续: {e}")

        time.sleep(3)

        # === 诊断 Turnstile 环境 ===
        log("→ 诊断 Turnstile 环境")
        env = page.evaluate("""() => {
            const r = {
                has_turnstile_js: typeof window.turnstile !== 'undefined',
                turnstile_keys: window.turnstile ? Object.keys(window.turnstile) : [],
                cf_elements: document.querySelectorAll('.cf-turnstile').length,
                sitekey_elements: document.querySelectorAll('[data-sitekey]').length,
                cf_iframes: document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]').length,
                all_iframes: document.querySelectorAll('iframe').length,
                iframe_srcs: Array.from(document.querySelectorAll('iframe')).map(f => f.src).slice(0, 10),
                scripts: Array.from(document.querySelectorAll('script[src]')).map(s => s.src).filter(s => s.includes('turnstile') || s.includes('cloudflare')),
                sitekeys: Array.from(document.querySelectorAll('[data-sitekey]')).map(el => el.getAttribute('data-sitekey')),
            };
            return r;
        }""")
        log(f"  window.turnstile 存在: {env['has_turnstile_js']}")
        log(f"  turnstile API keys: {env['turnstile_keys']}")
        log(f"  .cf-turnstile 元素数: {env['cf_elements']}")
        log(f"  [data-sitekey] 元素数: {env['sitekey_elements']}")
        log(f"  cloudflare iframe 数: {env['cf_iframes']}")
        log(f"  iframe 总数: {env['all_iframes']}")
        log(f"  iframe srcs: {env['iframe_srcs']}")
        log(f"  turnstile/cf script: {env['scripts']}")
        log(f"  sitekeys: {env['sitekeys']}")

        log("→ 等待表单渲染")
        try:
            page.wait_for_selector('input', timeout=30000)
        except PWTimeout:
            log("  ❌ 30s 内未出现 input 元素")
            try:
                page.screenshot(path="login_fail.png", full_page=True)
            except Exception:
                pass
            browser.close()
            sys.exit(1)

        log("→ 填写账号")
        for sel in ['input[name="username"]', 'input[type="email"]',
                    'input[placeholder*="邮箱"]', 'input[placeholder*="用户"]']:
            try:
                page.fill(sel, EMAIL, timeout=3000)
                log(f"  已填账号: {sel}")
                break
            except PWTimeout:
                continue

        log("→ 填写密码")
        for sel in ['input[type="password"]', 'input[name="password"]']:
            try:
                page.fill(sel, PASSWORD, timeout=3000)
                log(f"  已填密码: {sel}")
                break
            except PWTimeout:
                continue

        log("→ 勾选用户协议")
        agreed = False
        for sel in ['[role="checkbox"]', 'input[type="checkbox"]',
                    'button[role="checkbox"]', '[data-state="unchecked"]']:
            try:
                els = page.query_selector_all(sel)
                for el in els:
                    try:
                        el.click(force=True, timeout=3000)
                        agreed = True
                        log(f"  ✅ 点击 {sel}")
                        break
                    except Exception:
                        continue
                if agreed:
                    break
            except Exception:
                continue
        log("  ✅ 已勾选" if agreed else "  ⚠️ 未勾选成功")
        time.sleep(2)

        # === 勾选后再看一次 Turnstile 环境 ===
        log("→ 勾选后再次诊断 Turnstile")
        env2 = page.evaluate("""() => {
            return {
                has_turnstile_js: typeof window.turnstile !== 'undefined',
                cf_elements: document.querySelectorAll('.cf-turnstile').length,
                sitekey_elements: document.querySelectorAll('[data-sitekey]').length,
                cf_iframes: document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]').length,
                sitekeys: Array.from(document.querySelectorAll('[data-sitekey]')).map(el => el.getAttribute('data-sitekey')),
            };
        }""")
        log(f"  {env2}")

        # === 主动尝试调用 turnstile.execute() ===
        log("→ 主动尝试触发 Turnstile 执行")
        try:
            exec_result = page.evaluate("""() => {
                if (typeof window.turnstile === 'undefined') return 'no turnstile js';
                const els = document.querySelectorAll('.cf-turnstile, [data-sitekey]');
                if (els.length === 0) return 'no container';
                const results = [];
                for (const el of els) {
                    try {
                        let wid = el.getAttribute('data-widget-id');
                        if (!wid) {
                            try {
                                wid = window.turnstile.render(el, {
                                    sitekey: el.getAttribute('data-sitekey'),
                                    callback: (t) => { window.__turnstile_token = t; }
                                });
                                results.push('render:' + wid);
                            } catch(e) {
                                results.push('render-err:' + e.message);
                            }
                        }
                        try {
                            if (wid) {
                                window.turnstile.execute(wid);
                                results.push('execute:' + wid);
                            }
                        } catch(e) {
                            results.push('execute-err:' + e.message);
                        }
                    } catch(e) {
                        results.push('err:' + e.message);
                    }
                }
                return results.join(' | ');
            }""")
            log(f"  执行结果: {exec_result}")
        except Exception as e:
            log(f"  ⚠️ 主动执行失败: {e}")

        log("→ 等待 Turnstile token（最多 120s）...")
        token = None
        for i in range(120):
            token = page.evaluate("""() => {
                if (window.__turnstile_token) return window.__turnstile_token;
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
                log(f"  ✅ 拿到 Turnstile token: {token[:40]}...")
                break
            if i % 10 == 0:
                log(f"  ...等待中 {i}s")
            time.sleep(1)

        if not token:
            log("❌ 120s 内未拿到 Turnstile token")
            try:
                page.screenshot(path="login_fail.png", full_page=True)
                log("已保存截图 login_fail.png")
            except Exception:
                pass
            browser.close()
            sys.exit(1)

        # === 登录 ===
        log("→ 调用登录接口")
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

        log(f"  登录返回: success={login_result.get('success')} msg={login_result.get('message','')}")

        if not login_result.get("success"):
            log("→ fetch 登录失败，尝试点击登录按钮...")
            clicked = False
            for sel in [
                'button[type="submit"]:not([disabled])',
                'button:has-text("登录"):not([disabled])',
                'button:has-text("Login"):not([disabled])',
            ]:
                try:
                    page.click(sel, timeout=5000, force=True)
                    clicked = True
                    log(f"  ✅ 点击 {sel}")
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
                    log("  ✅ UI 登录成功")
                    login_result = {"success": True}

        if not login_result.get("success"):
            log(f"❌ 登录失败: {login_result.get('message','')}")
            try:
                page.screenshot(path="login_fail.png", full_page=True)
                log("已保存截图 login_fail.png")
            except Exception:
                pass
            browser.close()
            sys.exit(1)

        log("✅ 登录成功")

        # === 签到 ===
        log("→ 调用签到接口")
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
        log(f"  签到返回: {json.dumps(checkin_result, ensure_ascii=False)[:300]}")

        log("→ 获取用户信息")
        user_info = page.evaluate("""async () => {
            try {
                const r = await fetch('/api/user/self', {credentials: 'include'});
                return await r.json();
            } catch(e) { return {success: false}; }
        }""")

        browser.close()

    # ---- 解析结果 ----
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
