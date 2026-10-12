#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# v9-hcnsec: 屏蔽 s0-greate.net + 删除 uc_gui_click_captcha + 先点登录再处理 Turnstile

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

# ★ 只屏蔽一个域名，避免 SeleniumBase 逗号拆分问题
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
            with open(f"hijacked_{tag}.html", "w", encoding="utf-8") as f:
                f.write(sb.get_page_source())
        except Exception:
            pass
        return False, cur
    return True, cur


def find_login_button(sb):
    candidates = [
        "//form//button[@type='submit']",
        "//form//button[contains(., '登录')]",
        "//form//button[contains(., 'Sign in')]",
        "//form//button[contains(., 'Sign In')]",
        "//form//button[contains(., 'Login')]",
        "//button[@type='submit' and contains(., '登录')]",
        "//button[@type='submit' and contains(., 'Sign in')]",
        "//button[@type='submit' and contains(., 'Sign In')]",
        "//button[@type='submit' and contains(., 'Login')]",
        "//button[contains(., '登录')]",
        "//button[contains(., 'Sign in')]",
        "//button[contains(., 'Sign In')]",
        "//button[contains(., 'Login')]",
    ]
    exclude_keywords = ("搜索", "Search", "订阅", "Subscribe",
                        "注册", "Sign up", "Sign Up", "Register")
    for sel in candidates:
        try:
            elems = sb.find_elements(sel)
            for el in elems:
                try:
                    if not el.is_displayed() or not el.is_enabled():
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
                    results.push({
                        tag: p.tagName,
                        cls: p.className || '',
                        text: t,
                        cardTitle: cardTitle
                    });
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
        print(f"      [{i}] <{c['tag']}> class='{c['cls'][:80]}...' "
              f"card='{c['cardTitle']}' text='{c['text']}'")

    award_text = ""
    for c in candidates:
        if c.get("cardTitle") == "每日签到":
            award_text = c["text"]
            break
    if not award_text:
        for c in candidates:
            if "line-clamp-2" in c.get("cls", "") and "text-muted-foreground" in c.get("cls", ""):
                award_text = c["text"]
                break
    if not award_text and candidates:
        award_text = candidates[0]["text"]

    amount = ""
    m = re.search(r'[+＋]\s*¥\s*([\d.]+)', award_text or "")
    if m:
        amount = m.group(1)

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
                    if (!bestImmediate) {
                        bestImmediate = {el: e, text: t, isButton: isBtn};
                    } else {
                        const curBetter =
                            (isBtn && !bestImmediate.isButton) ||
                            (isBtn === bestImmediate.isButton && t.length < bestImmediate.text.length);
                        if (curBetter) {
                            bestImmediate = {el: e, text: t, isButton: isBtn};
                        }
                    }
                }
                if (t.includes('已签到')) {
                    if (!bestDone) {
                        bestDone = {el: e, text: t, isButton: isBtn};
                    } else {
                        const curBetter =
                            (isBtn && !bestDone.isButton) ||
                            (isBtn === bestDone.isButton && t.length < bestDone.text.length);
                        if (curBetter) {
                            bestDone = {el: e, text: t, isButton: isBtn};
                        }
                    }
                }
            }

            if (bestImmediate) {
                bestImmediate.el.setAttribute('data-checkin-target', '1');
                return JSON.stringify({
                    state: 'ready',
                    text: bestImmediate.text,
                    tag: bestImmediate.el.tagName,
                    is_button: bestImmediate.isButton
                });
            }
            if (bestDone) {
                return JSON.stringify({
                    state: 'already',
                    text: bestDone.text,
                    tag: bestDone.el.tagName
                });
            }

            const checkinElems = [];
            allElems.forEach(e => {
                const t = (e.textContent || '').trim();
                if (t.includes('签到') && t.length < 100) {
                    checkinElems.push({
                        tag: e.tagName,
                        text: t,
                        cls: (e.className || '').substring(0, 80)
                    });
                }
            });
            return JSON.stringify({
                state: 'not_found',
                checkin_elems: checkinElems
            });
        })()
    """)

    try:
        return json.loads(raw)
    except Exception:
        return {"state": "not_found"}


def browser_checkin():
    from seleniumbase import SB

    result = {
        "logged_in": False,
        "already_done": False,
        "checkin_clicked": False,
        "button_before": "",
        "button_after": "",
        "toast": "",
        "award_text": "",
        "award_amount": "",
        "error": "",
    }

    print("🚀 启动浏览器（UC 模式）…")

    # ★ v9: 单域名 host-resolver-rules（不含逗号，SeleniumBase 不会拆）
    resolver_rule = f"MAP {BLOCKED_HOST} 0.0.0.0"

    sb_kwargs = dict(
        uc=True,
        headed=True,
        xvfb=True,
        incognito=True,
        locale_code="zh-CN",
        window_size="1920,1080",
        chromium_arg=(
            "--no-sandbox,--disable-dev-shm-usage,--disable-gpu,"
            "--disable-blink-features=AutomationControlled,"
            f"--host-resolver-rules={resolver_rule}"
        ),
    )
    if CHROME_PROXY:
        print(f"🌐 浏览器代理: {CHROME_PROXY}")
        sb_kwargs["proxy"] = CHROME_PROXY
    else:
        print("🌐 浏览器代理: 未启用（直连）")

    if HTTPS_PROXY:
        print(f"🌐 requests 代理: {HTTPS_PROXY}")
    else:
        print("🌐 requests 代理: 未启用（直连）")

    print(f"🚫 DNS 屏蔽: {BLOCKED_HOST} → 0.0.0.0")

    with SB(**sb_kwargs) as sb:
        try:
            # ---------- 打开登录页 ----------
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
                print("❌ React 20s 内未渲染完成")
                result["error"] = "React 未渲染"
                return result

            print(f"  当前 URL: {sb.get_current_url()}")
            print(f"  页面标题: {sb.get_title()}")

            # ---------- 诊断已加载脚本（关键） ----------
            try:
                loaded_scripts = sb.execute_script("""
                    var out = [];
                    var scripts = document.querySelectorAll('script[src]');
                    for (var i=0; i<scripts.length; i++) {
                        out.push(scripts[i].src.slice(0, 120));
                    }
                    return out.join('\\n');
                """)
                print("  🔍 已加载脚本:")
                if loaded_scripts:
                    for line in loaded_scripts.split('\n'):
                        print(f"    {line}")
                else:
                    print("    (无外部脚本)")

                leaked = sb.execute_script(f"""
                    var out = [];
                    var scripts = document.querySelectorAll('script[src]');
                    for (var i=0; i<scripts.length; i++) {{
                        if (scripts[i].src.indexOf('{BLOCKED_HOST}') >= 0) {{
                            out.push('LEAKED: ' + scripts[i].src.slice(0, 100));
                        }}
                    }}
                    return out.join('\\n');
                """)
                if leaked:
                    print(f"  ⚠️ 被屏蔽的脚本仍加载了:")
                    for line in leaked.split('\n'):
                        print(f"    {line}")
                else:
                    print(f"  ✅ {BLOCKED_HOST} 已被成功屏蔽")
            except Exception as e:
                print(f"  脚本诊断异常: {e}")

            sb.wait_for_element_visible("input[name='username']", timeout=15)
            sb.wait_for_element_visible("input[name='password']", timeout=10)

            # ---------- 输入用户名 ----------
            print("✍️ 填写用户名…")
            sb.type("input[name='username']", EMAIL)
            sb.sleep(0.5)
            ok, cur = check_url(sb, "hcnsec", tag="after_username")
            if not ok:
                result["error"] = f"输入用户名后被劫持: {cur[:80]}"
                return result

            # ---------- 输入密码 ----------
            print("✍️ 填写密码…")
            sb.type("input[name='password']", PASSWORD)
            sb.sleep(0.5)
            ok, cur = check_url(sb, "hcnsec", tag="after_password")
            if not ok:
                result["error"] = f"输入密码后被劫持: {cur[:80]}"
                return result

            # ---------- 勾选同意（JS click） ----------
            print("☑️ 勾选法律同意 (JS 原生 click)…")
            consent_sel = "span[role='checkbox'][aria-labelledby='legal-consent-label']"
            try:
                sb.wait_for_element_present(consent_sel, timeout=10)
                checked = sb.get_attribute(consent_sel, "aria-checked")
                if checked != "true":
                    sb.execute_script("""
                        var el = document.querySelector("span[role='checkbox'][aria-labelledby='legal-consent-label']");
                        if (el && el.getAttribute('aria-checked') !== 'true') el.click();
                    """)
                    sb.sleep(0.9)
            except Exception as e:
                print(f"⚠️ 勾选异常: {e}")

            ok, cur = check_url(sb, "hcnsec", tag="after_consent")
            if not ok:
                result["error"] = f"勾选同意后被劫持: {cur[:80]}"
                return result

            # 保存源码
            try:
                with open("login_page_source.html", "w", encoding="utf-8") as f:
                    f.write(sb.get_page_source())
            except Exception:
                pass

            # ---------- 出口 IP ----------
            try:
                ip_info = sb.execute_script("""
                    try {
                        var xhr = new XMLHttpRequest();
                        xhr.open('GET', 'https://api.ipify.org?format=json', false);
                        xhr.send();
                        return xhr.responseText;
                    } catch(e) { return 'ERR:' + e.message; }
                """)
                print(f"  🌐 浏览器出口 IP: {ip_info}")
            except Exception as e:
                print(f"  IP 检测失败: {e}")

            # ---------- ★★★ 关键改动：直接点登录按钮 ----------
            # 不再调用 uc_gui_click_captcha（它乱点屏幕）
            # 直接点登录按钮，观察 Turnstile 是否会自动渲染
            print("🖱️ 定位登录按钮…")
            login_sel, login_text = find_login_button(sb)
            if not login_sel:
                print("❌ 未找到登录按钮")
                try:
                    sb.save_screenshot("no_login_button.png")
                except Exception:
                    pass
                result["error"] = "未找到登录按钮"
                return result

            print(f"  已定位: {login_sel}  文字='{login_text}'")

            # 点击前先检查是否已经有 Turnstile
            pre_turnstile = sb.execute_script("""
                var out = [];
                out.push('iframe=' + document.querySelectorAll('iframe').length);
                out.push('cf-turnstile=' + document.querySelectorAll('.cf-turnstile').length);
                var tk = document.querySelector('[name="cf-turnstile-response"]');
                out.push('resp=' + (tk ? ('len:' + (tk.value||'').length) : 'NONE'));
                return out.join('|');
            """)
            print(f"  点击登录前 Turnstile 状态: {pre_turnstile}")

            # 用 JS 原生 click 提交
            print("🖱️ 点击登录按钮 (JS native click)…")
            sb.execute_script("""
                var btn = document.querySelector("button[type='submit']");
                if (btn) btn.click();
            """)
            sb.sleep(3)

            # 检查 URL 是否被劫持
            ok, cur = check_url(sb, "hcnsec", tag="after_login_click")
            if not ok:
                result["error"] = f"登录按钮点击后跳到异常域名: {cur[:80]}"
                return result

            print(f"  点击后 URL: {cur}")

            # 检查 Turnstile 是否渲染
            post_turnstile = sb.execute_script("""
                var out = [];
                out.push('iframe=' + document.querySelectorAll('iframe').length);
                out.push('cf-turnstile=' + document.querySelectorAll('.cf-turnstile').length);
                var tk = document.querySelector('[name="cf-turnstile-response"]');
                out.push('resp=' + (tk ? ('len:' + (tk.value||'').length) : 'NONE'));
                var ifr = document.querySelectorAll('iframe');
                for (var j=0; j<ifr.length; j++) {
                    out.push('iframe['+j+']=' + (ifr[j].src||'').slice(0,100));
                }
                return out.join('|');
            """)
            print(f"  点击登录后 Turnstile 状态: {post_turnstile}")

            try:
                sb.save_screenshot("after_login_click.png")
            except Exception:
                pass

            # ---------- 等待登录跳转或 Turnstile 完成 ----------
            print("⏳ 等待登录结果…")
            logged_in = False
            for i in range(60):
                sb.sleep(1)
                cur = sb.get_current_url()

                # 被劫持
                if "hcnsec" not in cur:
                    print(f"❌ 等待过程中跳到异常域名: {cur}")
                    try:
                        sb.save_screenshot("jumped_away.png")
                    except Exception:
                        pass
                    result["error"] = f"等待过程中被劫持: {cur[:80]}"
                    return result

                # 成功
                if "hcnsec" in cur and LOGIN_PATH not in cur and "/login" not in cur:
                    logged_in = True
                    print(f"✅ 登录成功: {cur}")
                    break

                # 检查 Turnstile token（如果出现）
                tk = sb.execute_script("""
                    var el = document.querySelector('[name="cf-turnstile-response"]');
                    return (el && el.value) ? el.value.length : 0;
                """)
                if tk and tk > 20:
                    print(f"  ✅ Turnstile token 出现 (长度={tk})，继续等待跳转…")

                if i % 10 == 9:
                    print(f"  ⏳ 已等待 {i+1}s，当前 URL: {cur}")

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
                result["error"] = "登录状态失效（打开 profile 被重定向回登录页）"
                return result

            # ---------- 检测签到按钮状态 ----------
            print("🔍 检测签到按钮状态…")
            info = find_checkin_button(sb)
            print(f"  按钮状态: {json.dumps(info, ensure_ascii=False)}")

            state = info.get("state", "not_found")
            result["button_before"] = info.get("text", "")

            if state == "already":
                print(f"✅ 今日已签到 | 按钮: '{info.get('text')}'")
                result["already_done"] = True
                result["checkin_clicked"] = True
                result["button_after"] = info.get("text", "")

                award_text, award_amount = read_award_amount(sb)
                result["award_text"] = award_text
                result["award_amount"] = award_amount
                print(f"  金额文字: '{award_text}' | 提取: +¥{award_amount}")

                try:
                    sb.save_screenshot("checkin_result.png")
                except Exception:
                    pass
                return result

            if state == "not_found":
                print(f"⚠️ 未找到签到按钮")
                print(f"  含“签到”的元素: {info.get('checkin_elems')}")
                result["error"] = "未找到签到按钮"
                sb.save_screenshot("no_checkin_button.png")
                try:
                    with open("no_checkin_button.html", "w", encoding="utf-8") as f:
                        f.write(sb.get_page_source())
                except Exception:
                    pass
                return result

            # ---------- 执行签到 ----------
            print(f"  ✅ 找到'立即签到'按钮，开始签到…")

            # 用 JS 原生 click
            sb.execute_script("""
                var el = document.querySelector("[data-checkin-target='1']");
                if (!el) return false;
                var target = el;
                if (el.tagName !== 'BUTTON') {
                    var innerBtn = el.querySelector('button');
                    if (innerBtn) target = innerBtn;
                }
                target.click();
            """)
            result["checkin_clicked"] = True
            print("✅ 已点击 (JS native click)")

            print("⏳ 等待签到请求完成（15 秒）…")
            sb.sleep(15)

            print("💰 读取签到奖励金额…")
            award_text, award_amount = read_award_amount(sb)
            result["award_text"] = award_text
            result["award_amount"] = award_amount
            print(f"  金额文字: '{award_text}' | 提取: +¥{award_amount}")

            print("🔍 检查按钮状态…")
            final_info = find_checkin_button(sb)
            btn_state = final_info.get("text", "")
            print(f"  按钮文字: '{btn_state}'")
            result["button_after"] = btn_state

            try:
                sb.save_screenshot("checkin_result.png")
            except Exception:
                pass

            return result

        except Exception as e:
            print(f"❌ 浏览器异常: {e}")
            result["error"] = str(e)
            try:
                sb.save_screenshot("browser_error.png")
            except Exception:
                pass
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
    print(f"   CHROME_PROXY  : {CHROME_PROXY or '⚪ 未设置（直连）'}")
    print(f"   HTTPS_PROXY   : {HTTPS_PROXY or '⚪ 未设置（直连）'}")
    print("=" * 50)

    now = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    r = browser_checkin()

    if not r.get("logged_in"):
        msg = f"❌ iamhc 登录失败\n📌 {r.get('error', '')}\n⏱️ {now}"
        print(msg)
        send_notification(msg)
        sys.exit(1)

    if not r.get("checkin_clicked"):
        msg = (f"⚠️ iamhc 登录成功但未找到签到按钮\n"
               f"📌 {r.get('error', '')}\n⏱️ {now}\n{BASE_URL}")
        print(msg)
        send_notification(msg)
        sys.exit(1)

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
        msg = (f"🎁 iamhc 签到通知\n\n"
               f"{title}\n"
               f"{amount_line}"
               f"🖱️ 按钮状态: {btn_after}\n"
               f"⏱️ {now}\n{BASE_URL}")
    else:
        msg = (f"🎁 iamhc 签到通知\n\n"
               f"⚠️ 签到结果未确认\n"
               f"{amount_line}"
               f"🖱️ 按钮状态: {btn_after}\n"
               f"⏱️ {now}\n{BASE_URL}")

    print(msg)
    send_notification(msg)


if __name__ == "__main__":
    main()
