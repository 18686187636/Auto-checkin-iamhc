#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, json, requests, shutil, tempfile
from datetime import datetime, timezone, timedelta

from patchright.sync_api import sync_playwright, TimeoutError as PWTimeout

EMAIL         = os.environ.get("EMAIL") or ""
PASSWORD      = os.environ.get("PASSWORD") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""
PROXY_URL     = os.environ.get("PROXY_URL") or ""

BASE_URL = "https://api.hcnsec.cn"
TURNSTILE_SITEKEY = "0x4AAAAAAFIovBqwE9xMkrm_"
QUOTA_PER_UNIT = 500000
TZ_CN = timezone(timedelta(hours=8))


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
    user_data_dir = tempfile.mkdtemp(prefix="pw-user-")

    with sync_playwright() as p:
        launch_opts = {
            "user_data_dir": user_data_dir,
            "headless": False,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            "viewport": {"width": 1920, "height": 1080},
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        if PROXY_URL:
            launch_opts["proxy"] = {"server": PROXY_URL}
            log(f"→ 浏览器将走代理: {PROXY_URL}")

        log("→ 启动浏览器 (patchright chromium, 无 init_script)...")
        context = p.chromium.launch_persistent_context(**launch_opts)
        log("  ✅ 浏览器已启动")

        page = context.pages[0] if context.pages else context.new_page()

        def on_response(r):
            u = r.url
            if ("hcnsec" in u) or ("cloudflare" in u) or ("turnstile" in u):
                log(f"  [net] {r.status} {u[:160]}")
        page.on("response", on_response)

        log(f"→ 打开 {BASE_URL}/login")
        try:
            page.goto(f"{BASE_URL}/login", wait_until="load", timeout=90000)
            log("  ✅ 页面已加载")
        except Exception as e:
            log(f"  ⚠️ {e}")

        # === 诊断页面 ===
        time.sleep(3)
        info = page.evaluate("""() => ({
            ts_type: typeof window.turnstile,
            render_type: (typeof window.turnstile !== 'undefined') ? typeof window.turnstile.render : 'n/a',
            cf_elements: document.querySelectorAll('.cf-turnstile').length,
            sitekey_elements: document.querySelectorAll('[data-sitekey]').length,
        })""")
        log(f"→ 初始诊断: {info}")

        # === 等待 turnstile JS 就绪（最多 60s） ===
        log("→ 等待 window.turnstile 就绪（最多 60s）...")
        ready = False
        for i in range(60):
            has = page.evaluate("() => typeof window.turnstile !== 'undefined' && !!window.turnstile.render")
            if has:
                log(f"  ✅ turnstile 就绪（等待 {i}s）")
                ready = True
                break
            if i % 10 == 0:
                log(f"  ...{i}s")
            time.sleep(1)

        if not ready:
            log("❌ turnstile 未就绪，退出")
            try:
                page.screenshot(path="login_fail.png", full_page=True)
            except Exception:
                pass
            context.close()
            shutil.rmtree(user_data_dir, ignore_errors=True)
            sys.exit(1)

        # === 检查页面自己有没有 turnstile 元素 ===
        time.sleep(2)
        info2 = page.evaluate("""() => ({
            cf_elements: document.querySelectorAll('.cf-turnstile').length,
            sitekey_elements: document.querySelectorAll('[data-sitekey]').length,
            cf_iframes: document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]').length,
        })""")
        log(f"→ 二次诊断: {info2}")

        # === 如果页面没渲染，手动创建 ===
        if info2["cf_elements"] == 0 and info2["sitekey_elements"] == 0:
            log("→ 页面未渲染 turnstile，手动创建容器")
            render_res = page.evaluate("""(sitekey) => {
                window.__ts_token = null;
                window.__ts_error = null;
                const old = document.getElementById('__manual_ts');
                if (old) old.remove();
                const div = document.createElement('div');
                div.id = '__manual_ts';
                div.style.cssText = 'position:fixed;top:20px;left:20px;z-index:2147483647;background:#fff;padding:8px;';
                document.body.appendChild(div);
                try {
                    const wid = window.turnstile.render(div, {
                        sitekey: sitekey,
                        callback: (token) => { window.__ts_token = token; },
                        'error-callback': (err) => { window.__ts_error = 'error:' + String(err); },
                        'timeout-callback': () => { window.__ts_error = 'timeout'; },
                    });
                    return {ok: true, wid: wid};
                } catch(e) {
                    return {ok: false, err: e.message};
                }
            }""", TURNSTILE_SITEKEY)
            log(f"  render: {render_res}")
        else:
            log("→ 页面自己已渲染 turnstile，等待自动完成")

        # === 等待 token（180s） ===
        log("→ 等待 token（最多 180s）...")
        token = None
        for i in range(180):
            # 检查多种来源
            state = page.evaluate("""() => {
                const result = {token: null, error: null};
                // 1. 手动 render 的 callback 设置
                if (window.__ts_token) return {token: window.__ts_token};
                if (window.__ts_error) return {error: window.__ts_error};
                // 2. 从 .cf-turnstile 里读
                if (window.turnstile && window.turnstile.getResponse) {
                    const els = document.querySelectorAll('.cf-turnstile, [data-sitekey], #__manual_ts');
                    for (const el of els) {
                        try {
                            const r = window.turnstile.getResponse(el);
                            if (r && r.length > 20) return {token: r};
                        } catch(e) {}
                    }
                }
                // 3. 从主页面 hidden input 读
                const inp = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
                if (inp && inp.value && inp.value.length > 20) return {token: inp.value};
                // 4. 从 iframe 里读
                try {
                    const frames = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
                    for (const fr of frames) {
                        try {
                            const doc = fr.contentDocument || fr.contentWindow.document;
                            const inp2 = doc.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
                            if (inp2 && inp2.value && inp2.value.length > 20) return {token: inp2.value};
                        } catch(e) {}
                    }
                } catch(e) {}
                return result;
            }""")
            if state.get("token"):
                token = state["token"]
                log(f"  ✅ 拿到 token: {token[:50]}...")
                break
            if state.get("error"):
                log(f"  ❌ CF 报错: {state['error']}")
                break
            if i % 15 == 0:
                log(f"  ...{i}s")
            time.sleep(1)

        # === 如果手动没拿到，尝试从 frame 里直接读 ===
        if not token:
            log("→ 尝试从 turnstile frame 直接读 token")
            for fr in page.frames:
                if "challenges.cloudflare.com" in fr.url:
                    try:
                        t = fr.evaluate("""() => {
                            const inp = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
                            if (inp && inp.value) return inp.value;
                            // 尝试从 URL 里读
                            if (window.location.hash) {
                                const m = window.location.hash.match(/[#&]token=([^&]+)/);
                                if (m) return decodeURIComponent(m[1]);
                            }
                            return null;
                        }""")
                        if t and len(t) > 20:
                            token = t
                            log(f"  ✅ 从 frame 拿到 token: {token[:50]}...")
                            break
                    except Exception as e:
                        log(f"  frame 读取失败: {e}")
                        continue

        if not token:
            log("❌ 未拿到 token")
            try:
                page.screenshot(path="login_fail.png", full_page=True)
            except Exception:
                pass
            context.close()
            shutil.rmtree(user_data_dir, ignore_errors=True)
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
            log(f"❌ 登录失败: {login_result.get('message','')}")
            try:
                page.screenshot(path="login_fail.png", full_page=True)
            except Exception:
                pass
            context.close()
            shutil.rmtree(user_data_dir, ignore_errors=True)
            sys.exit(1)

        log("✅ 登录成功")

        # === 签到 ===
        log("→ 签到")
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

        context.close()
        shutil.rmtree(user_data_dir, ignore_errors=True)

    # ---- 结果 ----
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
