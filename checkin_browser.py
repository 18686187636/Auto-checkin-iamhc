#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# v11-hcnsec: 多层屏蔽 s0-greate.net + JS 错误捕获 + window.turnstile 检查

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


def apply_cdp_block(sb):
    """用 CDP 屏蔽 s0-greate.net（可重复调用）"""
    try:
        sb.driver.execute_cdp_cmd("Network.enable", {})
        sb.driver.execute_cdp_cmd("Network.setBlockedURLs", {
            "urls": [f"*{BLOCKED_HOST}*"]
        })
        return True
    except Exception as e:
        print(f"   CDP 屏蔽失败: {e}")
        return False


def clean_overlays(sb):
    """移除页面上的广告覆盖层（s0-greate.net 可能注入的透明层）"""
    try:
        removed = sb.execute_script("""
            var removed = 0;
            var all = document.querySelectorAll('div, iframe, ins, section');
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                var s = getComputedStyle(el);
                if (s.position !== 'fixed' && s.position !== 'absolute') continue;
                var zi = parseInt(s.zIndex) || 0;
                if (zi < 1000) continue;
                var r = el.getBoundingClientRect();
                var area = r.width * r.height;
                var screenArea = window.innerWidth * window.innerHeight;
                if (area < screenArea * 0.4) continue;
                // 不删包含表单元素或 Turnstile 的
                if (el.querySelector('input, textarea, button[type="submit"], .cf-turnstile, iframe[src*="cloudflare"]')) continue;
                // 不删我们自己的元素
                if (el.hasAttribute('data-checkin-target')) continue;
                el.remove();
                removed++;
            }
            return removed;
        """)
        return removed
    except Exception as e:
        return -1


def get_js_errors(sb):
    """获取页面注册的 JS 错误"""
    try:
        return sb.execute_script("""
            return (window.__captured_errors || []).join('\\n');
        """)
    except Exception:
        return ""


def list_all_buttons(sb):
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
                    visible: !!(b.offsetWidth || b.offsetHeight)
                });
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
                    if not el.is_enabled():
                        continue
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
        chromium_arg=(
            "--no-sandbox,--disable-dev-shm-usage,--disable-gpu,"
            "--disable-blink-features=AutomationControlled"
        ),
    )
    if CHROME_PROXY:
        print(f"🌐 浏览器代理: {CHROME_PROXY}")
        sb_kwargs["proxy"] = CHROME_PROXY
    if HTTPS_PROXY:
        print(f"🌐 requests 代理: {HTTPS_PROXY}")

    with SB(**sb_kwargs) as sb:
        try:
            # ★ Step 1: 页面前先 CDP 屏蔽 + 注入 JS 错误捕获
            print("🛡️ 预配置：CDP 屏蔽 + JS 错误捕获注入…")
            apply_cdp_block(sb)
            try:
                sb.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                    "source": """
                        window.__captured_errors = [];
                        window.addEventListener('error', function(e) {
                            try {
                                window.__captured_errors.push(
                                    '[ERROR] ' + (e.message || '') + ' @ ' + (e.filename || '') + ':' + (e.lineno || 0)
                                );
                            } catch(_) {}
                        });
                        window.addEventListener('unhandledrejection', function(e) {
                            try {
                                window.__captured_errors.push(
                                    '[REJECT] ' + String(e.reason).slice(0, 200)
                                );
                            } catch(_) {}
                        });
                    """
                })
                print("   ✅ JS 错误捕获已注入")
            except Exception as e:
                print(f"   JS 捕获注入失败: {e}")

            # Step 2: 打开页面
            print(f"🌐 打开登录页: {LOGIN_URL}")
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            sb.sleep(3)

            # ★ Step 3: 打开后立即再设置 CDP（防 UC reconnect 重置）
            apply_cdp_block(sb)

            # ★ Step 4: 清除覆盖层
            print("🧹 清除页面覆盖层…")
            removed = clean_overlays(sb)
            print(f"   移除 {removed} 个覆盖层")

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
                if i % 3 == 2:
                    clean_overlays(sb)  # 反复清理

            if not react_ready:
                result["error"] = "React 未渲染"
                return result

            print(f"  当前 URL: {sb.get_current_url()}")

            # ---------- ★ 关键诊断 ----------
            print("🔬 关键诊断…")

            # 1. 网络请求（Performance API）
            try:
                actual_requests = sb.execute_script("""
                    var out = [];
                    var entries = performance.getEntriesByType('resource');
                    for (var i = 0; i < entries.length; i++) {
                        if (entries[i].name.indexOf('s0-greate') >= 0) {
                            out.push('LEAKED: ' + entries[i].name.slice(0, 120));
                        }
                    }
                    return out.join('\\n');
                """)
                if actual_requests:
                    print(f"  ⚠️ s0-greate.net 仍发出请求:")
                    for line in actual_requests.split('\n'):
                        print(f"    {line}")
                else:
                    print(f"  ✅ s0-greate.net 未发出请求")
            except Exception as e:
                print(f"  网络请求诊断异常: {e}")

            # 2. Turnstile 状态
            try:
                ts_info = sb.execute_script("""
                    var out = [];
                    out.push('window.turnstile=' + (typeof window.turnstile));
                    out.push('turnstile.render=' + (window.turnstile && typeof window.turnstile.render));
                    out.push('cf-turnstile=' + document.querySelectorAll('.cf-turnstile').length);
                    out.push('data-sitekey=' + document.querySelectorAll('[data-sitekey]').length);
                    var tk = document.querySelector('[name="cf-turnstile-response"]');
                    out.push('token_input=' + (tk ? 'EXISTS(len:' + (tk.value||'').length + ')' : 'NONE'));
                    var iframes = document.querySelectorAll('iframe');
                    out.push('iframe_count=' + iframes.length);
                    for (var j=0; j<iframes.length; j++) {
                        out.push('iframe['+j+']=' + (iframes[j].src||'').slice(0,100));
                    }
                    return out.join('\\n');
                """)
                print("  Turnstile 环境:")
                for line in (ts_info or '').split('\n'):
                    print(f"    {line}")
            except Exception as e:
                print(f"  Turnstile 诊断异常: {e}")

            # 3. JS 错误
            try:
                errors = get_js_errors(sb)
                if errors:
                    print(f"  ⚠️ 页面 JS 错误:")
                    for line in errors.split('\n'):
                        print(f"    {line}")
                else:
                    print(f"  ✅ 无 JS 错误")
            except Exception as e:
                print(f"  JS 错误诊断异常: {e}")

            sb.wait_for_element_visible("input[name='username']", timeout=15)
            sb.wait_for_element_visible("input[name='password']", timeout=10)

            # 输入凭证
            print("✍️ 填写用户名…")
            sb.type("input[name='username']", EMAIL)
            sb.sleep(0.5)
            print("✍️ 填写密码…")
            sb.type("input[name='password']", PASSWORD)
            sb.sleep(0.5)

            # 勾选同意
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

            ok, cur = check_url(sb, "hcnsec", tag="after_consent")
            if not ok:
                result["error"] = f"勾选同意后被劫持: {cur[:80]}"
                return result

            # ---------- 尝试主动渲染 Turnstile ----------
            print("🔧 尝试主动渲染 Turnstile…")
            try:
                render_result = sb.execute_script("""
                    // 找到 sitekey
                    var containers = document.querySelectorAll('[data-sitekey]');
                    var sitekey = containers.length > 0 ? containers[0].getAttribute('data-sitekey') : '';
                    if (!sitekey) {
                        // 从内联脚本里找
                        var html = document.body.innerHTML;
                        var m = html.match(/0x[A-Za-z0-9_-]{15,}/);
                        if (m) sitekey = m[0];
                    }
                    // 从 api.js 加载状态看
                    var state = {
                        api_loaded: typeof window.turnstile !== 'undefined',
                        sitekey: sitekey,
                        called: false
                    };
                    if (state.api_loaded && sitekey) {
                        try {
                            window.turnstile.render('[data-sitekey]', {
                                sitekey: sitekey,
                                callback: function(token) {
                                    console.log('[Turnstile OK]', token);
                                }
                            });
                            state.called = true;
                        } catch(e) { state.error = e.message; }
                    }
                    return JSON.stringify(state);
                """)
                print(f"   渲染结果: {render_result}")
            except Exception as e:
                print(f"   渲染异常: {e}")

            sb.sleep(3)

            # ---------- 等待登录按钮 enabled ----------
            print("⏳ 等待登录按钮 enabled…")
            login_sel, login_text = None, None
            for i in range(20):
                sb.sleep(1)
                login_sel, login_text = find_login_button(sb)
                if login_sel:
                    print(f"  ✅ 第 {i}s 按钮已 enabled: '{login_text}'")
                    break
                if i % 5 == 4:
                    # 再清一次覆盖层
                    r = clean_overlays(sb)
                    if r > 0:
                        print(f"    移除 {r} 个覆盖层")

            if not login_sel:
                print("❌ 20s 后按钮仍 disabled")
                # 列出所有按钮看状态
                buttons = list_all_buttons(sb)
                for j, b in enumerate(buttons):
                    print(f"    [{j}] <{b['tag']}> text='{b['text']}' disabled={b['disabled']}")

                # 尝试强制调用 React 的 onSubmit
                print("🔧 尝试直接调用表单 submit…")
                try:
                    submit_result = sb.execute_script("""
                        var form = document.querySelector('form');
                        if (form) {
                            form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
                            return 'form dispatched';
                        }
                        // 找不到 form 就找 submit 按钮强点
                        var btn = document.querySelector("button[type='submit']");
                        if (btn) {
                            btn.removeAttribute('disabled');
                            btn.disabled = false;
                            btn.click();
                            return 'button forced click';
                        }
                        return 'NO_FORM_NO_BUTTON';
                    """)
                    print(f"   结果: {submit_result}")
                    sb.sleep(5)
                    cur = sb.get_current_url()
                    print(f"   当前 URL: {cur}")
                    if "hcnsec" in cur and LOGIN_PATH not in cur:
                        print(f"   ✅ 强制提交成功跳转")
                        login_sel = "FORCED"
                except Exception as e:
                    print(f"   强制提交异常: {e}")

                if not login_sel:
                    sb.save_screenshot("button_disabled.png")
                    result["error"] = "登录按钮一直 disabled"
                    return result

            # ---------- 点登录 ----------
            if login_sel != "FORCED":
                print("🖱️ 点击登录按钮…")
                sb.execute_script("""
                    var btn = document.querySelector("button[type='submit']");
                    if (btn && !btn.disabled) btn.click();
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
            clean_overlays(sb)  # 先清覆盖层
            sb.open(PROFILE_URL)
            sb.sleep(6)
            clean_overlays(sb)
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
