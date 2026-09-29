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
            "channel": "chrome",           # 用真实 Chrome，比 Chromium 指纹更真
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--start-maximized",
            ],
            "viewport": {"width": 1920, "height": 1080},
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        if PROXY_URL:
            launch_opts["proxy"] = {"server": PROXY_URL}
            log(f"→ 浏览器将走代理: {PROXY_URL}")

        log("→ 启动浏览器 (patchright + chrome + persistent)...")
        try:
            context = p.chromium.launch_persistent_context(**launch_opts)
        except Exception as e:
            log(f"  ❌ 启动失败: {e}")
            sys.exit(1)
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

        # === 等待 turnstile 脚本就绪（最长 90s） ===
        log("→ 等待 window.turnstile 就绪（最多 90s）...")
        ready = False
        for i in range(90):
            info = page.evaluate("""() => ({
                ts_type: typeof window.turnstile,
                render_type: (typeof window.turnstile !== 'undefined') ? typeof window.turnstile.render : 'n/a',
                readyState: document.readyState,
            })""")
            if info.get("render_type") == "function":
                log(f"  ✅ turnstile 就绪（等待 {i}s）")
                ready = True
                break
            if i % 5 == 0:
                log(f"  ...{i}s  ts={info.get('ts_type')}  render={info.get('render_type')}  rs={info.get('readyState')}")
            time.sleep(1)

        if not ready:
            log("  ⚠️ 90s 未就绪，尝试主动注入 api.js")
            try:
                page.add_script_tag(
                    url="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"
                )
                time.sleep(5)
                ok = page.evaluate("() => typeof window.turnstile !== 'undefined' && !!window.turnstile.render")
                if ok:
                    log("  ✅ 主动注入成功")
                    ready = True
            except Exception as e:
                log(f"  ❌ 主动注入失败: {e}")

        if not ready:
            log("❌ turnstile 无法就绪，退出")
            try:
                page.screenshot(path="login_fail.png", full_page=True)
            except Exception:
                pass
            context.close()
            shutil.rmtree(user_data_dir, ignore_errors=True)
            sys.exit(1)

        time.sleep(2)

        # === 手动 render ===
        log("→ 手动 render Turnstile")
        render_res = page.evaluate("""(sitekey) => {
            window.__ts_token = null;
            window.__ts_error = null;
            window.__ts_interactive = false;
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
                    'before-interactive-callback': () => { window.__ts_interactive = true; },
                    'after-interactive-callback': () => { window.__ts_interactive = false; },
                });
                return {ok: true, wid: wid};
            } catch(e) {
                return {ok: false, err: e.message};
            }
        }""", TURNSTILE_SITEKEY)
        log(f"  render: {render_res}")

        time.sleep(3)
        st = page.evaluate("""() => {
            const d = document.getElementById('__manual_ts');
            return {
                iframes_in_div: d ? d.querySelectorAll('iframe').length : 0,
                inner_html_len: d ? d.innerHTML.length : 0,
            };
        }""")
        log(f"  容器: {st}")

        log("→ 等待 token（最多 180s）...")
        token = None
        for i in range(180):
            state = page.evaluate("""() => ({
                token: window.__ts_token,
                error: window.__ts_error,
                interactive: !!window.__ts_interactive,
            })""")
            if state.get("token"):
                token = state["token"]
                log(f"  ✅ token: {token[:50]}...")
                break
            if state.get("error"):
                log(f"  ❌ CF 报错: {state['error']}")
                break
            if i % 15 == 0:
                log(f"  ...{i}s  interactive={state.get('interactive')}")
                if state.get("interactive"):
                    try:
                        for fr in page.frames:
                            if "challenges.cloudflare.com" in fr.url:
                                try:
                                    el = fr.frame_element()
                                    box = el.bounding_box()
                                    if box and box["width"] > 0:
                                        page.mouse.click(box["x"] + 20, box["y"] + box["height"] / 2)
                                        log(f"  已点击 ({box['x']+20:.0f},{box['y']+box['height']/2:.0f})")
                                except Exception as e:
                                    log(f"  点击失败: {e}")
                                break
                    except Exception:
                        pass
            time.sleep(1)

        if not token:
            log("❌ 未拿到 token")
            try:
                page.screenshot(path="login_fail.png", full_page=True)
                el = page.query_selector('#__manual_ts')
                if el:
                    el.screenshot(path="turnstile_widget.png")
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
