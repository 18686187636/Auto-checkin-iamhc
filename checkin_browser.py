#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# v10-hcnsec: 精确诊断 + 放宽登录按钮匹配 + 等待按钮出现

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

BLOCKED_HOST = "s0-greate.net"


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
            proxies = _requests_proxies()
            r = requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": message},
                timeout=15,
                proxies=proxies,
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


def list_all_buttons(sb):
    """列出页面上所有按钮，用于诊断"""
    try:
        raw = sb.execute_script("""
            var out = [];
            document.querySelectorAll('button, [role="button"], input[type="submit"], a').forEach(function(b) {
                var txt = (b.textContent || b.value || b.getAttribute('aria-label') || '').trim();
                if (txt.length > 60) txt = txt.slice(0, 60) + '...';
                out.push({
                    tag: b.tagName,
                    type: b.getAttribute('type') || '',
                    text: txt,
                    disabled: b.disabled || b.getAttribute('aria-disabled') === 'true',
                    visible: !!(b.offsetWidth || b.offsetHeight),
                    cls: (b.className || '').slice(0, 50)
                });
            });
            return JSON.stringify(out);
        """)
        return json.loads(raw) if raw else []
    except Exception as e:
        print(f"  列按钮异常: {e}")
        return []


def find_login_button(sb):
    """放宽匹配：不用 //form//，直接找任何 submit 按钮 + 登录文本"""
    candidates = [
        "button[type='submit']",
        "//button[contains(., '登录')]",
        "//button[contains(., 'Sign in')]",
        "//button[contains(., 'Sign In')]",
        "//button[contains(., 'Login')]",
        "//button[contains(., 'Log in')]",
        "//button[contains(., 'Sign In / Sign Up')]",
        "//button[contains(., 'Continue')]",
    ]
    exclude_keywords = ("搜索", "Search", "订阅", "Subscribe",
                        "注册", "Sign up", "Sign Up", "Register")
    for sel in candidates:
        try:
            elems = sb.find_elements(sel)
            for el in elems:
                try:
                    if not el.is_displayed():
                        continue
                    if not el.is_enabled():
                        print(f"  跳过 disabled 按钮: '{el.text}'")
                        continue
                except Exception:
                    continue
                txt = (el.text or "").strip()
                if any(k in txt for k in exclude_keywords):
                    continue
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
    if not candidates:
        print("      ⚠️ 未找到任何 '今天 +¥' 元素")
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
                        const curBetter = (isBtn && !bestImmediate.isButton) ||
                            (isBtn === bestImmediate.isButton && t.length < bestImmediate.text.length);
                        if (curBetter) bestImmediate = {el: e, text: t, isButton: isBtn};
                    }
                }
                if (t.includes('已签到')) {
                    if (!bestDone) { bestDone = {el: e, text: t, isButton: isBtn}; }
                    else {
                        const curBetter = (isBtn && !bestDone.isButton) ||
                            (isBtn === bestDone.isButton && t.length < bestDone.text.length);
                        if (curBetter) bestDone = {el: e, text: t, isButton: isBtn};
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

    # ★ v10: 用 ~NOTFOUND 让 DNS 立即返回 NXDOMAIN（比 0.0.0.0 快）
    resolver_rule = f"MAP {BLOCKED_HOST} ~NOTFOUND"

    sb_kwargs = dict(
        uc=True, headed=True, xvfb=True, incognito=True,
        locale_code="zh-CN", window_size="1920,1080",
        chromium_arg=(
            "--no-sandbox,--disable-dev-shm-usage,--disable-gpu,"
            "--disable-blink-features=AutomationControlled,"
            f"--host-resolver-rules={resolver_rule}"
        ),
    )
    if CHROME_PROXY:
        print(f"🌐 浏览器代理: {CHROME_PROXY}")
        sb_kwargs["proxy"] = CHROME_PROXY
    if HTTPS_PROXY:
        print(f"🌐 requests 代理: {HTTPS_PROXY}")

    print(f"🚫 DNS 屏蔽: {BLOCKED_HOST} → NXDOMAIN")

    with SB(**sb_kwargs) as sb:
        try:
            print(f"🌐 打开登录页: {LOGIN_URL}")
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            sb.sleep(3)

            # ---------- 等 React 渲染 ----------
            print("⏳ 等待 React 渲染完成…")
            react_ready = False
            for i in range(20):
                ok, cur = check_url(sb, "hcnsec", tag="open")
                if not ok:
                    result["error"] = f"打开页面被劫持: {cur[:80]}"
                    return result
                has_user = sb.execute_script(
                    "return document.querySelector(\"input[name='username']\") ? 1 : 0;"
                )
                if has_user:
                    print(f"  ✅ React 已渲染 (等待 {i}s)")
                    react_ready = True
                    break
                sb.sleep(1)

            if not react_ready:
                result["error"] = "React 未渲染"
                return result

            print(f"  当前 URL: {sb.get_current_url()}")

            # ---------- ★ 精确诊断：Performance API 检查实际网络请求 ----------
            try:
                actual_requests = sb.execute_script("""
                    var out = [];
                    var entries = performance.getEntriesByType('resource');
                    for (var i = 0; i < entries.length; i++) {
                        var n = entries[i].name;
                        if (n.indexOf('s0-greate') >= 0) {
                            out.push('LEAKED_REQ: ' + n.slice(0, 120));
                        }
                    }
                    return out.join('\\n');
                """)
                if actual_requests:
                    print(f"  ⚠️ 实际发出请求:")
                    for line in actual_requests.split('\n'):
                        print(f"    {line}")
                else:
                    print(f"  ✅ {BLOCKED_HOST} 未发出任何请求（屏蔽成功）")

                # 静态标签还在，但看看 script 的 onerror 是否触发
                static_tags = sb.execute_script(f"""
                    var out = [];
                    document.querySelectorAll('script[src]').forEach(function(s) {{
                        if (s.src.indexOf('{BLOCKED_HOST}') >= 0) {{
                            out.push('STATIC_TAG: ' + s.src.slice(0, 100));
                        }}
                    }});
                    return out.join('\\n');
                """)
                if static_tags:
                    print(f"  ℹ️ 静态标签（不代表加载成功）:")
                    for line in static_tags.split('\n'):
                        print(f"    {line}")
            except Exception as e:
                print(f"  诊断异常: {e}")

            sb.wait_for_element_visible("input[name='username']", timeout=15)
            sb.wait_for_element_visible("input[name='password']", timeout=10)

            # ---------- 输入凭证 ----------
            print("✍️ 填写用户名…")
            sb.type("input[name='username']", EMAIL)
            sb.sleep(0.5)
            ok, cur = check_url(sb, "hcnsec", tag="after_username")
            if not ok:
                result["error"] = f"输入用户名后被劫持: {cur[:80]}"
                return result

            print("✍️ 填写密码…")
            sb.type("input[name='password']", PASSWORD)
            sb.sleep(0.5)
            ok, cur = check_url(sb, "hcnsec", tag="after_password")
            if not ok:
                result["error"] = f"输入密码后被劫持: {cur[:80]}"
                return result

            # ---------- 勾选同意 ----------
            print("☑️ 勾选法律同意…")
            consent_sel = "span[role='checkbox'][aria-labelledby='legal-consent-label']"
            try:
                sb.wait_for_element_present(consent_sel, timeout=10)
                checked = sb.get_attribute(consent_sel, "aria-checked")
                if checked != "true":
                    sb.execute_script("""
                        var el = document.querySelector("span[role='checkbox'][aria-labelledby='legal-consent-label']");
                        if (el && el.getAttribute('aria-checked') !== 'true') el.click();
                    """)
                    sb.sleep(1.5)
            except Exception as e:
                print(f"⚠️ 勾选异常: {e}")

            ok, cur = check_url(sb, "hcnsec", tag="after_consent")
            if not ok:
                result["error"] = f"勾选同意后被劫持: {cur[:80]}"
                return result

            # ---------- ★ 诊断：列出所有按钮 ----------
            print("🔍 列出页面上所有按钮…")
            buttons = list_all_buttons(sb)
            for i, b in enumerate(buttons):
                print(f"  [{i}] <{b['tag']}> type='{b['type']}' text='{b['text']}' "
                      f"disabled={b['disabled']} visible={b['visible']} cls='{b['cls']}'")

            # ---------- 等待登录按钮可点击 ----------
            print("⏳ 等待登录按钮出现并可点击…")
            login_sel, login_text = None, None
            for i in range(15):
                sb.sleep(1)
                login_sel, login_text = find_login_button(sb)
                if login_sel:
                    print(f"  ✅ 第 {i}s 找到: {login_sel}  文字='{login_text}'")
                    break
                if i == 4:
                    print("  ℹ️ 5s 后仍无按钮，可能需等待 React 状态更新…")
                if i == 9:
                    print("  ℹ️ 10s 后仍无按钮，重新列出按钮…")
                    buttons = list_all_buttons(sb)
                    for j, b in enumerate(buttons):
                        print(f"    [{j}] <{b['tag']}> text='{b['text']}' disabled={b['disabled']}")

            if not login_sel:
                print("❌ 15s 内未找到登录按钮")
                try:
                    sb.save_screenshot("no_login_button.png")
                    with open("no_login_button.html", "w", encoding="utf-8") as f:
                        f.write(sb.get_page_source())
                except Exception:
                    pass
                result["error"] = "未找到登录按钮"
                return result

            # ---------- 点击前诊断 Turnstile ----------
            pre = sb.execute_script("""
                var out = [];
                out.push('iframe=' + document.querySelectorAll('iframe').length);
                out.push('cf-turnstile=' + document.querySelectorAll('.cf-turnstile').length);
                var tk = document.querySelector('[name="cf-turnstile-response"]');
                out.push('resp=' + (tk ? ('len:' + (tk.value||'').length) : 'NONE'));
                return out.join('|');
            """)
            print(f"  点击登录前 Turnstile 状态: {pre}")

            # ---------- 点登录 ----------
            print("🖱️ 点击登录按钮 (JS native click)…")
            sb.execute_script("""
                var btn = document.querySelector("button[type='submit']");
                if (btn) btn.click();
            """)
            sb.sleep(3)

            ok, cur = check_url(sb, "hcnsec", tag="after_login_click")
            if not ok:
                result["error"] = f"登录按钮点击后跳到异常域名: {cur[:80]}"
                return result

            print(f"  点击后 URL: {cur}")

            # ---------- 等待登录结果 ----------
            print("⏳ 等待登录结果…")
            logged_in = False
            for i in range(60):
                sb.sleep(1)
                cur = sb.get_current_url()
                if "hcnsec" not in cur:
                    print(f"❌ 等待过程中跳到异常域名: {cur}")
                    try:
                        sb.save_screenshot("jumped_away.png")
                    except Exception:
                        pass
                    result["error"] = f"等待过程中被劫持: {cur[:80]}"
                    return result
                if "hcnsec" in cur and LOGIN_PATH not in cur and "/login" not in cur:
                    logged_in = True
                    print(f"✅ 登录成功: {cur}")
                    break
                tk = sb.execute_script("""
                    var el = document.querySelector('[name="cf-turnstile-response"]');
                    return (el && el.value) ? el.value.length : 0;
                """)
                if tk and tk > 20 and i % 5 == 0:
                    print(f"  ✅ Turnstile token 出现 (长度={tk})，等待跳转…")
                if i % 10 == 9:
                    print(f"  ⏳ 已等待 {i+1}s，URL: {cur}")

            if not logged_in:
                print("❌ 登录未成功")
                try:
                    sb.save_screenshot("login_failed.png")
                except Exception:
                    pass
                result["error"] = "登录失败"
                return result

            result["logged_in"] = True
            sb.sleep(3)

            # ---------- 打开个人资料页 ----------
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
                print(f"⚠️ 未找到签到按钮")
                print(f"  含“签到”的元素: {info.get('checkin_elems')}")
                result["error"] = "未找到签到按钮"
                sb.save_screenshot("no_checkin_button.png")
                return result

            # 执行签到
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
            print("✅ 已点击 (JS native click)")
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
