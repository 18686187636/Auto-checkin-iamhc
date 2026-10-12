#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# v12-hcnsec: sing-box 层屏蔽 + 深度搜 sitekey + React 原生 fill

import os, sys, time, json, re, requests
from datetime import datetime, timezone, timedelta

EMAIL        = os.environ.get("EMAIL") or ""
PASSWORD     = os.environ.get("PASSWORD") or ""
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

CHROME_PROXY = (os.environ.get("CHROME_PROXY") or "").strip()
HTTPS_PROXY  = (os.environ.get("HTTPS_PROXY") or
                os.environ.get("https_proxy") or "").strip()

BASE_URL = "https://api.hcnsec.cn"
TZ_CN = timezone(timedelta(hours=8))

LOGIN_URL  = f"{BASE_URL}/sign-in"
LOGIN_PATH = "/sign-in"
PROFILE_URL = f"{BASE_URL}/profile"


def _requests_proxies():
    if not HTTPS_PROXY:
        return None
    return {"http": HTTPS_PROXY, "https": HTTPS_PROXY}


def send_notification(message):
    print("\n" + "=" * 30)
    print(message)
    print("=" * 30)
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": message},
                timeout=15,
                proxies=_requests_proxies(),
            )
            print("Telegram:", "成功" if r.status_code == 200 else f"失败 {r.status_code}")
        except Exception as e:
            print("Telegram 异常:", e)


def check_url(sb, expected_contains="hcnsec", tag=""):
    cur = sb.get_current_url()
    if expected_contains not in cur:
        print(f"❌ [{tag}] URL 被劫持到: {cur}")
        try:
            sb.save_screenshot(f"hijacked_{tag}.png")
        except Exception:
            pass
        return False, cur
    return True, cur


def search_sitekey(sb):
    """深度搜索页面里的 turnstile sitekey"""
    try:
        raw = sb.execute_script(r"""
            var found = [];

            // 1. 搜 HTML 静态内容
            var html = document.documentElement.outerHTML;
            var m = html.match(/0x[A-Za-z0-9_-]{15,}/g);
            if (m) found.push('HTML:' + m.join(','));

            // 2. 搜 script 标签内容
            document.querySelectorAll('script').forEach(function(s) {
                var t = s.textContent || '';
                var mm = t.match(/0x[A-Za-z0-9_-]{15,}/g);
                if (mm) found.push('SCRIPT:' + mm.join(','));
            });

            // 3. 搜 window 对象
            try {
                for (var k in window) {
                    try {
                        var v = window[k];
                        if (typeof v === 'string' && v.length > 15 && v.length < 200) {
                            if (v.indexOf('0x4') === 0 || v.indexOf('0x0') === 0) {
                                found.push('WINDOW.' + k + '=' + v);
                            }
                        }
                    } catch(e) {}
                }
            } catch(e) {}

            // 4. 搜常见 config 变量
            ['__CONFIG__', 'APP_CONFIG', 'config', '__NEXT_DATA__', 'ENV',
             'TURNSTILE_SITEKEY', 'turnstileSitekey'].forEach(function(k) {
                try {
                    var v = window[k];
                    if (v) found.push('VAR.' + k + '=' + JSON.stringify(v).slice(0, 200));
                } catch(e) {}
            });

            // 5. 搜 meta 标签
            document.querySelectorAll('meta').forEach(function(m) {
                var n = m.name || m.getAttribute('property') || '';
                if (n.toLowerCase().indexOf('turnstile') >= 0) {
                    found.push('META[' + n + ']=' + m.content);
                }
            });

            return found.join('\n');
        """)
        return raw or ""
    except Exception as e:
        return f"搜索异常: {e}"


def list_all_buttons(sb):
    try:
        raw = sb.execute_script("""
            var out = [];
            document.querySelectorAll('button, [role="button"], input[type="submit"], a').forEach(function(b) {
                var txt = (b.textContent || b.value || b.getAttribute('aria-label') || '').trim();
                if (txt.length > 60) txt = txt.slice(0, 60) + '...';
                out.push({ tag: b.tagName, type: b.getAttribute('type') || '',
                    text: txt,
                    disabled: b.disabled || b.getAttribute('aria-disabled') === 'true',
                    visible: !!(b.offsetWidth || b.offsetHeight) });
            });
            return JSON.stringify(out);
        """)
        return json.loads(raw) if raw else []
    except Exception:
        return []


def find_login_button(sb):
    candidates = [
        "button[type='submit']",
        "//button[contains(., '登录')]",
        "//button[contains(., 'Sign in')]",
        "//button[contains(., 'Login')]",
    ]
    exclude_keywords = ("搜索", "Search", "订阅", "Subscribe",
                        "注册", "Sign up", "Register", "Passkey")
    for sel in candidates:
        try:
            for el in sb.find_elements(sel):
                try:
                    if not el.is_displayed(): continue
                    if not el.is_enabled(): continue
                except Exception:
                    continue
                txt = (el.text or "").strip()
                if any(k in txt for k in exclude_keywords): continue
                return sel, txt
        except Exception:
            continue
    return None, None


def read_award_amount(sb):
    debug_info = sb.execute_script("""
        (function() {
            const results = [];
            document.querySelectorAll('p').forEach(function(p) {
                const t = (p.textContent || '').trim();
                if (t.includes('今天') && t.includes('¥')) {
                    let parent = p.parentElement;
                    let cardTitle = '';
                    let depth = 0;
                    while (parent && depth < 6) {
                        const h = parent.querySelector('h3');
                        if (h) { cardTitle = h.textContent.trim(); break; }
                        parent = parent.parentElement;
                        depth++;
                    }
                    results.push({ tag: p.tagName, cls: p.className || '',
                        text: t, cardTitle: cardTitle });
                }
            });
            return JSON.stringify(results);
        })()
    """)
    print(f"    🔍 诊断：页面所有 '今天 +¥' 元素")
    try:
        candidates = json.loads(debug_info) if debug_info else []
    except Exception:
        candidates = []
    for i, c in enumerate(candidates):
        print(f"      [{i}] <{c['tag']}> card='{c['cardTitle']}' text='{c['text']}'")
    award_text = ""
    for c in candidates:
        if c.get("cardTitle") == "每日签到":
            award_text = c["text"]; break
    if not award_text:
        for c in candidates:
            if "line-clamp-2" in c.get("cls", "") and "text-muted-foreground" in c.get("cls", ""):
                award_text = c["text"]; break
    if not award_text and candidates:
        award_text = candidates[0]["text"]
    amount = ""
    m = re.search(r'[+＋]\s*¥\s*([\d.]+)', award_text or "")
    if m: amount = m.group(1)
    return award_text, amount


def find_checkin_button(sb):
    raw = sb.execute_script("""
        (function() {
            const allElems = document.querySelectorAll('button, a, div, span');
            let bestImmediate = null;
            let bestDone = null;
            for (let e of allElems) {
                const t = (e.textContent || '').trim();
                if (t.length > 60) continue;
                const isBtn = (e.tagName === 'BUTTON');
                if (t.includes('立即签到')) {
                    if (!bestImmediate) { bestImmediate = {el: e, text: t, isButton: isBtn}; }
                    else {
                        const cb = (isBtn && !bestImmediate.isButton) ||
                            (isBtn === bestImmediate.isButton && t.length < bestImmediate.text.length);
                        if (cb) bestImmediate = {el: e, text: t, isButton: isBtn};
                    }
                }
                if (t.includes('已签到')) {
                    if (!bestDone) { bestDone = {el: e, text: t, isButton: isBtn}; }
                    else {
                        const cb = (isBtn && !bestDone.isButton) ||
                            (isBtn === bestDone.isButton && t.length < bestDone.text.length);
                        if (cb) bestDone = {el: e, text: t, isButton: isBtn};
                    }
                }
            }
            if (bestImmediate) {
                bestImmediate.el.setAttribute('data-checkin-target', '1');
                return JSON.stringify({ state: 'ready', text: bestImmediate.text,
                    tag: bestImmediate.el.tagName, is_button: bestImmediate.isButton });
            }
            if (bestDone) {
                return JSON.stringify({ state: 'already', text: bestDone.text,
                    tag: bestDone.el.tagName });
            }
            return JSON.stringify({ state: 'not_found', checkin_elems: [] });
        })()
    """)
    try: return json.loads(raw)
    except Exception: return {"state": "not_found"}


def browser_checkin():
    from seleniumbase import SB

    result = {
        "logged_in": False, "already_done": False, "checkin_clicked": False,
        "button_before": "", "button_after": "", "toast": "",
        "award_text": "", "award_amount": "", "error": "",
    }

    print("🚀 启动浏览器（UC 模式）…")

    sb_kwargs = dict(
        uc=True, headed=True, xvfb=True, incognito=True,
        locale_code="zh-CN", window_size="1920,1080",
        chromium_arg="--no-sandbox,--disable-dev-shm-usage,--disable-gpu,--disable-blink-features=AutomationControlled",
    )
    if CHROME_PROXY:
        print(f"🌐 浏览器代理: {CHROME_PROXY}")
        sb_kwargs["proxy"] = CHROME_PROXY
    if HTTPS_PROXY:
        print(f"🌐 requests 代理: {HTTPS_PROXY}")

    with SB(**sb_kwargs) as sb:
        try:
            print(f"🌐 打开登录页: {LOGIN_URL}")
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            sb.sleep(3)

            # 等 React 渲染
            print("⏳ 等待 React 渲染完成…")
            for i in range(20):
                ok, cur = check_url(sb, "hcnsec", tag="open")
                if not ok:
                    result["error"] = f"打开页面被劫持: {cur[:80]}"
                    return result
                if sb.execute_script("return document.querySelector(\"input[name='username']\") ? 1 : 0;"):
                    print(f"  ✅ React 已渲染 (等待 {i}s)")
                    break
                sb.sleep(1)
            else:
                result["error"] = "React 未渲染"
                return result

            print(f"  当前 URL: {sb.get_current_url()}")

            # ---------- 诊断 1: s0-greate.net 是否加载 ----------
            print("\n🔬 诊断 1: 检查 s0-greate.net 网络请求")
            try:
                reqs = sb.execute_script("""
                    var out = [];
                    performance.getEntriesByType('resource').forEach(function(e) {
                        if (e.name.indexOf('s0-greate') >= 0) out.push('LEAKED: ' + e.name.slice(0,120));
                    });
                    return out.join('\\n');
                """)
                if reqs:
                    print(f"  ⚠️ s0-greate.net 仍加载:\n    {reqs}")
                else:
                    print(f"  ✅ s0-greate.net 未加载（sing-box block 生效）")
            except Exception as e:
                print(f"  诊断异常: {e}")

            # ---------- 诊断 2: 搜 sitekey ----------
            print("\n🔬 诊断 2: 深度搜索 sitekey")
            sk = search_sitekey(sb)
            if sk:
                for line in sk.split('\n'):
                    print(f"    {line}")
            else:
                print("    ⚠️ 未找到任何 sitekey")

            # ---------- 诊断 3: Turnstile 环境 ----------
            print("\n🔬 诊断 3: Turnstile 环境")
            try:
                ts = sb.execute_script("""
                    var out = [];
                    out.push('turnstile=' + typeof window.turnstile);
                    out.push('turnstile.render=' + (window.turnstile ? typeof window.turnstile.render : 'N/A'));
                    out.push('turnstile.ready=' + (window.turnstile ? typeof window.turnstile.ready : 'N/A'));
                    out.push('cf-turnstile=' + document.querySelectorAll('.cf-turnstile').length);
                    out.push('data-sitekey=' + document.querySelectorAll('[data-sitekey]').length);
                    return out.join(' | ');
                """)
                print(f"    {ts}")
            except Exception as e:
                print(f"    诊断异常: {e}")

            # ---------- 输入凭证（React 原生 fill）----------
            print("\n✍️ 用 React 原生方式填写表单…")

            def react_fill(name, value):
                payload = json.dumps({"name": name, "value": value})
                return sb.execute_script("""
                    var p = JSON.parse(arguments[0]);
                    var el = document.querySelector("input[name='" + p.name + "']");
                    if (!el) return false;
                    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, p.value);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                    return true;
                """, payload)

            ok = react_fill("username", EMAIL)
            print(f"  username fill: {ok}")
            sb.sleep(0.5)
            ok = react_fill("password", PASSWORD)
            print(f"  password fill: {ok}")
            sb.sleep(0.5)

            ok, cur = check_url(sb, "hcnsec", tag="after_fill")
            if not ok:
                result["error"] = f"填表后被劫持: {cur[:80]}"
                return result

            # ---------- 勾选同意 ----------
            print("☑️ 勾选法律同意…")
            consent_sel = "span[role='checkbox'][aria-labelledby='legal-consent-label']"
            try:
                sb.wait_for_element_present(consent_sel, timeout=10)
                if sb.get_attribute(consent_sel, "aria-checked") != "true":
                    sb.execute_script("""
                        var el = document.querySelector("span[role='checkbox'][aria-labelledby='legal-consent-label']");
                        if (el && el.getAttribute('aria-checked') !== 'true') el.click();
                    """)
                    sb.sleep(1.5)
            except Exception as e:
                print(f"⚠️ 勾选异常: {e}")

            # 检查按钮状态
            print("\n🔍 勾选后立即检查登录按钮状态…")
            btn_info = sb.execute_script("""
                var btn = document.querySelector("button[type='submit']");
                if (!btn) return 'NOT_FOUND';
                var html = btn.outerHTML.slice(0, 300);
                return JSON.stringify({
                    disabled: btn.disabled,
                    ariaDisabled: btn.getAttribute('aria-disabled'),
                    innerHTML: html
                });
            """)
            print(f"    {btn_info}")

            # 等待按钮 enabled
            print("⏳ 等待按钮 enabled（最多 30s）…")
            login_sel, login_text = None, None
            for i in range(30):
                sb.sleep(1)
                login_sel, login_text = find_login_button(sb)
                if login_sel:
                    print(f"  ✅ 第 {i}s 按钮 enabled: '{login_text}'")
                    break
                if i == 14:
                    print("  ℹ️ 15s 时再列出所有按钮:")
                    for j, b in enumerate(list_all_buttons(sb)):
                        print(f"    [{j}] {b['tag']} '{b['text']}' disabled={b['disabled']}")

            if not login_sel:
                print("❌ 30s 后按钮仍 disabled")
                sb.save_screenshot("button_still_disabled.png")

                try:
                    with open("login_page_source.html", "w", encoding="utf-8") as f:
                        f.write(sb.get_page_source())
                    print("  📄 已保存 login_page_source.html")
                except Exception:
                    pass

                result["error"] = "登录按钮一直 disabled"
                return result

            # ---------- 点登录 ----------
            print("🖱️ 点击登录按钮…")
            sb.execute_script("""
                var btn = document.querySelector("button[type='submit']");
                if (btn && !btn.disabled) btn.click();
            """)
            sb.sleep(3)

            ok, cur = check_url(sb, "hcnsec", tag="after_login_click")
            if not ok:
                result["error"] = f"登录后跳到异常域名: {cur[:80]}"
                return result

            print(f"  点击后 URL: {cur}")

            # ---------- 等待登录结果 ----------
            print("⏳ 等待登录结果…")
            logged_in = False
            for i in range(60):
                sb.sleep(1)
                cur = sb.get_current_url()
                if "hcnsec" not in cur:
                    result["error"] = f"等待过程中被劫持: {cur[:80]}"
                    return result
                if "hcnsec" in cur and LOGIN_PATH not in cur and "/login" not in cur:
                    logged_in = True
                    print(f"✅ 登录成功: {cur}")
                    break
                if i % 10 == 9:
                    print(f"  ⏳ 已等待 {i+1}s，URL: {cur}")

            if not logged_in:
                print("❌ 登录未成功")
                sb.save_screenshot("login_failed.png")
                result["error"] = "登录失败"
                return result

            result["logged_in"] = True
            sb.sleep(3)

            # ---------- 打开 profile ----------
            print("📄 打开个人资料页面…")
            sb.open(PROFILE_URL)
            sb.sleep(6)
            ok, cur = check_url(sb, "hcnsec", tag="profile")
            if not ok:
                result["error"] = f"打开 profile 跳到异常域名: {cur[:80]}"
                return result
            if "sign-in" in cur or "/login" in cur:
                result["error"] = "登录状态失效"
                return result

            print("🔍 检测签到按钮状态…")
            info = find_checkin_button(sb)
            print(f"  按钮状态: {json.dumps(info, ensure_ascii=False)}")
            state = info.get("state", "not_found")
            result["button_before"] = info.get("text", "")

            if state == "already":
                print(f"✅ 今日已签到")
                result["already_done"] = True
                result["checkin_clicked"] = True
                result["button_after"] = info.get("text", "")
                award_text, award_amount = read_award_amount(sb)
                result["award_text"] = award_text
                result["award_amount"] = award_amount
                try: sb.save_screenshot("checkin_result.png")
                except Exception: pass
                return result

            if state == "not_found":
                result["error"] = "未找到签到按钮"
                sb.save_screenshot("no_checkin_button.png")
                return result

            print(f"  ✅ 找到'立即签到'按钮")
            sb.execute_script("""
                var el = document.querySelector("[data-checkin-target='1']");
                if (!el) return false;
                var t = el;
                if (el.tagName !== 'BUTTON') {
                    var b = el.querySelector('button');
                    if (b) t = b;
                }
                t.click();
            """)
            result["checkin_clicked"] = True
            sb.sleep(15)

            award_text, award_amount = read_award_amount(sb)
            result["award_text"] = award_text
            result["award_amount"] = award_amount
            print(f"  金额文字: '{award_text}' | 提取: +¥{award_amount}")

            final_info = find_checkin_button(sb)
            btn_state = final_info.get("text", "")
            print(f"  按钮文字: '{btn_state}'")
            result["button_after"] = btn_state

            try: sb.save_screenshot("checkin_result.png")
            except Exception: pass
            return result

        except Exception as e:
            print(f"❌ 浏览器异常: {e}")
            result["error"] = str(e)
            try: sb.save_screenshot("browser_error.png")
            except Exception: pass
            return result


def main():
    if not EMAIL or not PASSWORD:
        print("❌ 请设置 EMAIL 和 PASSWORD 环境变量")
        sys.exit(1)

    print("=" * 50)
    print("⚙️  配置检查")
    print("=" * 50)
    print(f"   EMAIL         : {'✅ 已设置' if EMAIL else '❌ 空'}")
    print(f"   PASSWORD      : {'✅ 已设置' if PASSWORD else '❌ 空'}")
    print(f"   TG 通知       : {'✅ 已启用' if (TG_BOT_TOKEN and TG_CHAT_ID) else '⚪ 未启用'}")
    print(f"   CHROME_PROXY  : {CHROME_PROXY or '⚪ 未设置'}")
    print(f"   HTTPS_PROXY   : {HTTPS_PROXY or '⚪ 未设置'}")
    print("=" * 50)

    now = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    r = browser_checkin()

    if not r.get("logged_in"):
        msg = f"❌ iamhc 登录失败\n📌 {r.get('error', '')}\n⏱️ {now}"
        print(msg); send_notification(msg); sys.exit(1)

    if not r.get("checkin_clicked"):
        msg = f"⚠️ 登录成功但未找到签到按钮\n📌 {r.get('error', '')}\n⏱️ {now}"
        print(msg); send_notification(msg); sys.exit(1)

    already = r.get("already_done", False)
    btn_after = str(r.get("button_after", ""))
    amount = r.get("award_amount", "")
    award_text = r.get("award_text", "")

    if amount:
        amount_line = f"💰 今日奖励: +¥{amount}\n"
    elif award_text:
        amount_line = f"💰 奖励信息: {award_text}\n"
    else:
        amount_line = ""

    success = already or ("已签到" in btn_after)
    if success:
        title = "✅ 今日已签到" if already else "✅ 签到成功"
        msg = f"🎁 iamhc 签到通知\n\n{title}\n{amount_line}🖱️ 按钮状态: {btn_after}\n⏱️ {now}\n{BASE_URL}"
    else:
        msg = f"🎁 iamhc 签到通知\n\n⚠️ 签到结果未确认\n{amount_line}🖱️ 按钮状态: {btn_after}\n⏱️ {now}\n{BASE_URL}"

    print(msg); send_notification(msg)


if __name__ == "__main__":
    main()
