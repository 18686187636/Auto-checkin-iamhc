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
TURNSTILE_SITEKEY = "0x4AAAAAAFIovBqwE9xMkrm_"
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
                "--disable-features=IsolateOrigins,site-per-process",
                "--lang=zh-CN",
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
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        )
        # 更完整的反检测脚本
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
            Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
            window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {} };
            const origQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (params) => (
                params.name === 'notifications'
                    ? Promise.resolve({state: Notification.permission})
                    : origQuery(params)
            );
            Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
                get: function() { return window; }
            });
        """)

        page = context.new_page()

        def on_response(r):
            u = r.url
            if ("hcnsec" in u) or ("cloudflare" in u) or ("turnstile" in u):
                log(f"  [net] {r.status} {u[:160]}")
        page.on("response", on_response)

        log(f"→ 打开 {BASE_URL}/login")
        try:
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=60000)
            log("  ✅ 页面已加载")
        except Exception as e:
            log(f"  ⚠️ {e}")

        # 等 turnstile JS 加载
        log("→ 等待 window.turnstile 就绪...")
        for _ in range(30):
            has = page.evaluate("() => typeof window.turnstile !== 'undefined' && !!window.turnstile.render")
            if has:
                log("  ✅ window.turnstile.render 可用")
                break
            time.sleep(1)
        else:
            log("  ❌ 30s 内 turnstile 未就绪")
            browser.close()
            sys.exit(1)

        time.sleep(2)

        # === 关键：手动创建容器 + 渲染 Turnstile ===
        log("→ 手动创建 Turnstile 容器并 render")
        render_res = page.evaluate("""(sitekey) => {
            window.__ts_token = null;
            window.__ts_error = null;
            const old = document.getElementById('__manual_ts');
            if (old) old.remove();
            const div = document.createElement('div');
            div.id = '__manual_ts';
            div.style.cssText = 'position:fixed;top:20px;left:20px;z-index:2147483647;background:#fff;padding:8px;border:2px solid red;';
            document.body.appendChild(div);
            try {
                const wid = window.turnstile.render(div, {
                    sitekey: sitekey,
                    callback: (token) => { window.__ts_token = token; console.log('TS_CB_OK'); },
                    'error-callback': (err) => { window.__ts_error = 'error: ' + String(err); console.log('TS_ERR', err); },
                    'timeout-callback': () => { window.__ts_error = 'timeout'; console.log('TS_TIMEOUT'); },
                    'before-interactive-callback': () => { console.log('TS_BEFORE_INTERACTIVE'); window.__ts_interactive = true; },
                    'after-interactive-callback': () => { console.log('TS_AFTER_INTERACTIVE'); },
                    'unsupported-callback': () => { window.__ts_error = 'unsupported'; },
                });
                window.__ts_wid = wid;
                return {ok: true, wid: wid};
            } catch(e) {
                window.__ts_error = 'render exception: ' + e.message;
                return {ok: false, err: e.message};
            }
        }""", TURNSTILE_SITEKEY)
        log(f"  render 结果: {render_res}")

        time.sleep(3)

        # 检查容器是否渲染出 iframe
        check1 = page.evaluate("""() => ({
            div_exists: !!document.getElementById('__manual_ts'),
            div_html_len: document.getElementById('__manual_ts') ? document.getElementById('__manual_ts').innerHTML.length : 0,
            iframes_in_div: document.getElementById('__manual_ts') ? document.getElementById('__manual_ts').querySelectorAll('iframe').length : 0,
            iframe_srcs: document.getElementById('__manual_ts') ? Array.from(document.getElementById('__manual_ts').querySelectorAll('iframe')).map(f => f.src.slice(0, 100)) : [],
        })""")
        log(f"  容器状态: {check1}")

        # === 尝试点击 Turnstile 里的"我是人"框（如果有） ===
        try:
            # 找到 turnstile iframe 并尝试点击左上角
            frames = page.frames
            for f in frames:
                if "challenges.cloudflare.com" in f.url:
                    log(f"  发现 Turnstile iframe: {f.url[:100]}")
                    try:
                        # Turnstile 的 checkbox 通常在整个 iframe 的左侧
                        # 尝试点击坐标
                        box = f.frame_element().bounding_box() if hasattr(f, 'frame_element') else None
                    except Exception:
                        pass
                    break
        except Exception:
            pass

        log("→ 等待 Turnstile token（最多 180s）...")
        token = None
        for i in range(180):
            state = page.evaluate("""() => ({
                token: window.__ts_token,
                error: window.__ts_error,
                interactive: !!window.__ts_interactive,
            })""")
            if state.get("token"):
                token = state["token"]
                log(f"  ✅ 拿到 token: {token[:50]}...")
                break
            if state.get("error"):
                log(f"  ❌ Turnstile 报错: {state['error']}")
                break
            if i % 15 == 0:
                if state.get("interactive"):
                    log(f"  ...等待中 {i}s (需要人工交互，尝试自动点击)")
                    # 尝试点击 turnstile iframe
                    try:
                        for fr in page.frames:
                            if "challenges.cloudflare.com" in fr.url:
                                try:
                                    el = fr.frame_element()
                                    box = el.bounding_box()
                                    if box:
                                        # 点击左侧 30px 处（checkbox 位置）
                                        page.mouse.click(box["x"] + 20, box["y"] + box["height"] / 2)
                                        log(f"  点击坐标: ({box['x']+20}, {box['y']+box['height']/2})")
                                except Exception as e:
                                    log(f"  点击失败: {e}")
                                break
                    except Exception:
                        pass
                else:
                    log(f"  ...等待中 {i}s")
            time.sleep(1)

        if not token:
            log("❌ 未拿到 Turnstile token")
            try:
                page.screenshot(path="login_fail.png", full_page=True)
                # 单独截 turnstile 区域
                el = page.query_selector('#__manual_ts')
                if el:
                    el.screenshot(path="turnstile_widget.png")
                log("已保存截图")
            except Exception:
                pass
            browser.close()
            sys.exit(1)

        # === 用 token 登录 ===
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
