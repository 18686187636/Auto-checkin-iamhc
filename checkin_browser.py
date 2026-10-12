#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# v26-hcnsec: sitekey 多级提取 + 硬编码 fallback

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

# ★ 已知 sitekey（从前面的登录日志提取到）
KNOWN_SITEKEY = "0x4AAAAAAFIovBqwE9xMkrm_"


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


def safe_goto(sb, url, tag=""):
    print(f"  跳转 {url} ({tag})")
    try:
        sb.uc_open_with_reconnect(url, reconnect_time=4)
        return True
    except Exception as e:
        print(f"  ⚠️ uc_open_with_reconnect 失败: {e}")
    try:
        sb.driver.get(url)
        return True
    except Exception as e:
        print(f"  ⚠️ driver.get 失败: {e}")
    try:
        sb.execute_script(f"window.location.href = '{url}';")
        return True
    except Exception as e:
        print(f"  ⚠️ js location 失败: {e}")
    return False


def get_turnstile_state(sb):
    try:
        raw = sb.execute_script("""
            var tk = document.querySelector('[name="cf-turnstile-response"]');
            return 'turnstile=' + typeof window.turnstile +
                ' | token_len=' + (tk ? (tk.value || '').length : -1);
        """)
        return raw or ""
    except Exception as e:
        return f"err:{e}"


# ============================================================
# ★★★ v26: sitekey 多级提取
# ============================================================

def extract_sitekey(sb):
    """从多个来源提取 sitekey"""
    print("    🔍 尝试提取 sitekey…")

    # 方法 1: Performance entries
    try:
        sk = sb.execute_script(r"""
            var urls = performance.getEntriesByType('resource');
            for (var i = 0; i < urls.length; i++) {
                var m = urls[i].name.match(/\/(0x[A-Za-z0-9_-]{10,})\//);
                if (m) return 'PERF:' + m[1];
            }
            return '';
        """)
        if sk:
            print(f"      方法1(Performance): {sk}")
            return sk.split(":", 1)[1]
    except Exception as e:
        print(f"      方法1 异常: {e}")

    # 方法 2: HTML 内容
    try:
        sk = sb.execute_script(r"""
            var html = document.documentElement.outerHTML;
            var m = html.match(/0x[A-Za-z0-9_-]{15,}/);
            return m ? 'HTML:' + m[0] : '';
        """)
        if sk:
            print(f"      方法2(HTML): {sk}")
            return sk.split(":", 1)[1]
    except Exception as e:
        print(f"      方法2 异常: {e}")

    # 方法 3: script 标签内容
    try:
        sk = sb.execute_script(r"""
            var scripts = document.querySelectorAll('script');
            for (var i = 0; i < scripts.length; i++) {
                var t = scripts[i].textContent || '';
                var m = t.match(/0x[A-Za-z0-9_-]{15,}/);
                if (m) return 'SCRIPT:' + m[0];
            }
            return '';
        """)
        if sk:
            print(f"      方法3(Script): {sk}")
            return sk.split(":", 1)[1]
    except Exception as e:
        print(f"      方法3 异常: {e}")

    # 方法 4: window 变量扫描
    try:
        sk = sb.execute_script(r"""
            var keys = ['__TURNSTILE_SITEKEY', 'TURNSTILE_SITEKEY', 'TURNSTILE_KEY',
                        '__SITE_KEY', 'SITE_KEY', 'SITEKEY'];
            for (var i = 0; i < keys.length; i++) {
                var v = window[keys[i]];
                if (typeof v === 'string' && v.indexOf('0x') === 0) {
                    return 'WIN.' + keys[i] + ':' + v;
                }
            }
            return '';
        """)
        if sk:
            print(f"      方法4(window): {sk}")
            return sk.split(":", 1)[1]
    except Exception as e:
        print(f"      方法4 异常: {e}")

    # 方法 5: 用硬编码的 sitekey（登录时提取到的）
    print(f"      方法5(硬编码 fallback): {KNOWN_SITEKEY}")
    return KNOWN_SITEKEY


def ensure_turnstile_token(sb):
    """主动渲染 Turnstile widget 并获取 token"""
    # 1. 检查当前是否已有 token
    try:
        tk_len = sb.execute_script("""
            var el = document.querySelector('[name="cf-turnstile-response"]');
            return el ? (el.value || '').length : 0;
        """)
        if tk_len and tk_len > 20:
            print(f"    已有 token 长度={tk_len}")
            return True
    except Exception:
        pass

    # 2. 提取 sitekey
    sitekey = extract_sitekey(sb)
    if not sitekey:
        print("    ❌ 无法提取 sitekey")
        return False
    print(f"    ✅ 使用 sitekey={sitekey}")

    # 3. 主动渲染
    print("    主动渲染 Turnstile widget…")
    try:
        r = sb.execute_script("""
            var sk = arguments[0];
            var old = document.getElementById('__checkin_ts_container');
            if (old) old.remove();
            var div = document.createElement('div');
            div.id = '__checkin_ts_container';
            div.style.cssText = 'position: fixed; top: 100px; right: 100px; z-index: 999999; background: white; padding: 8px; border: 2px solid red;';
            document.body.appendChild(div);
            window.__checkin_token = '';
            try {
                var widgetId = window.turnstile.render('#__checkin_ts_container', {
                    sitekey: sk,
                    callback: function(t) {
                        window.__checkin_token = t;
                        console.log('CHECKIN_TOKEN_READY:' + t.length);
                    },
                    'error-callback': function(e) {
                        window.__checkin_token = 'ERR:' + e;
                    }
                });
                return 'RENDERED:' + widgetId;
            } catch(e) {
                return 'ERR:' + e.message;
            }
        """, sitekey)
        print(f"    渲染结果: {r}")
    except Exception as e:
        print(f"    渲染异常: {e}")
        return False

    # 4. 等 token
    print("    等待 token（最多 40s）…")
    for i in range(40):
        sb.sleep(1)
        try:
            tok_info = sb.execute_script("""
                var tk = window.__checkin_token || '';
                var iframes = document.querySelectorAll('#__checkin_ts_container iframe');
                return JSON.stringify({
                    len: tk.length,
                    err: tk.indexOf('ERR:') === 0 ? tk : '',
                    iframes: iframes.length
                });
            """)
            d = json.loads(tok_info) if tok_info else {}
            if d.get("len", 0) > 20:
                print(f"    ✅ token 就绪！长度={d['len']} iframes={d['iframes']}")
                # 把 token 塞进隐藏 input
                try:
                    sb.execute_script("""
                        var token = window.__checkin_token;
                        var el = document.querySelector('[name="cf-turnstile-response"]');
                        if (!el) {
                            el = document.createElement('input');
                            el.type = 'hidden';
                            el.name = 'cf-turnstile-response';
                            el.id = '__checkin_ts_input';
                            document.body.appendChild(el);
                        }
                        var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        setter.call(el, token);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    """)
                    print(f"    ✅ token 已写入 input")
                except Exception as e:
                    print(f"    写入 input 异常: {e}")
                return True
            if d.get("err"):
                print(f"    ⚠️ widget 报错: {d['err'][:100]}")
                return False
            if i % 10 == 9:
                print(f"    ⏳ {i+1}s: iframes={d.get('iframes')} token_len={d.get('len')}")
        except Exception as e:
            print(f"    检查异常: {e}")
    return False


def direct_fetch_checkin(sb, token):
    """直接用 fetch 调用签到 API"""
    print("    📮 直接用 fetch 调签到 API…")
    try:
        r = sb.execute_script("""
            var token = arguments[0];
            var xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/user/checkin', false);
            xhr.setRequestHeader('Content-Type', 'application/json');
            xhr.withCredentials = true;
            try {
                xhr.send(JSON.stringify({turnstile_token: token}));
                return JSON.stringify({
                    status: xhr.status,
                    body: (xhr.responseText || '').slice(0, 500)
                });
            } catch(e) {
                return 'ERR:' + e.message;
            }
        """, token)
        print(f"    API 响应: {r}")
        return r
    except Exception as e:
        print(f"    fetch 异常: {e}")
        return None


# ============================================================
# 按钮检测
# ============================================================

def find_checkin_state(sb):
    try:
        els = sb.find_elements("//button[normalize-space(text())='立即签到']")
        for el in els:
            try:
                if el.is_displayed():
                    return {"state": "ready", "el": el, "text": "立即签到"}
            except Exception:
                continue
    except Exception:
        pass

    try:
        els = sb.find_elements("//button[contains(., '立即签到')]")
        for el in els:
            try:
                if el.is_displayed():
                    return {"state": "ready", "el": el, "text": el.text[:30]}
            except Exception:
                continue
    except Exception:
        pass

    try:
        els = sb.find_elements("//button[contains(., '已签到')]")
        for el in els:
            try:
                if el.is_displayed():
                    return {"state": "already", "el": el, "text": el.text[:30]}
            except Exception:
                continue
    except Exception:
        pass

    return {"state": "not_found"}


def read_amount_via_xpath(sb):
    try:
        xpaths = [
            "//p[contains(text(), '今天') and contains(text(), '¥')]",
            "//*[contains(text(), '今天') and contains(text(), '¥') and string-length(text()) < 50]",
        ]
        for xp in xpaths:
            els = sb.find_elements(xp)
            for el in els:
                try:
                    txt = (el.text or "").strip()
                    m = re.search(r'[+＋]\s*¥\s*([\d.]+)', txt)
                    if m:
                        return txt, m.group(1)
                except Exception:
                    continue
    except Exception:
        pass

    try:
        body_text = sb.get_text("body")
        m = re.search(r'今天\s*[+＋]\s*¥\s*([\d.]+)', body_text)
        if m:
            return f"今天 +¥{m.group(1)}", m.group(1)
    except Exception:
        pass

    return "", ""


def list_all_buttons_safe(sb):
    out = []
    try:
        els = sb.find_elements("//button")
        for i, el in enumerate(els[:80]):
            try:
                txt = (el.text or "").strip()[:60]
                out.append(f"[{i}] BUTTON '{txt}' enabled={el.is_enabled()}")
            except Exception:
                continue
    except Exception as e:
        out.append(f"列按钮异常: {e}")
    return out


def wait_for_checkin_button(sb, max_wait=40):
    print(f"🔍 等待签到按钮出现（最多 {max_wait}s）…")
    info = {"state": "not_found"}
    for i in range(max_wait // 2):
        sb.sleep(2)
        cur = sb.get_current_url()
        if "hcnsec" not in cur:
            print(f"  ❌ 页面被劫持到: {cur}")
            return {"state": "not_found", "hijacked": cur}

        info = find_checkin_state(sb)
        st = info.get("state", "not_found")
        if st != "not_found":
            print(f"  ✅ 第 {i*2}s 找到按钮: state={st}")
            return info
    print(f"  ⚠️ {max_wait}s 内未找到按钮")
    return info


def click_checkin_button(sb):
    try:
        sb.execute_script("""
            var arr = document.querySelectorAll("button");
            for (var i = 0; i < arr.length; i++) {
                if ((arr[i].textContent || '').trim() === '立即签到') {
                    arr[i].scrollIntoView({block: 'center', behavior: 'instant'});
                    return 'SCROLLED';
                }
            }
            return 'NOT_FOUND';
        """)
        sb.sleep(1)
    except Exception:
        pass

    try:
        r = sb.execute_script("""
            var arr = document.querySelectorAll('button');
            for (var i = 0; i < arr.length; i++) {
                if ((arr[i].textContent || '').trim() === '立即签到') {
                    arr[i].click();
                    return 'CLICKED';
                }
            }
            return 'NOT_FOUND';
        """)
        print(f"    JS click: {r}")
        return r == "CLICKED"
    except Exception as e:
        print(f"    JS click 异常: {str(e)[:80]}")
    return False


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
            sb.sleep(5)

            print("⏳ 等待 React 渲染完成…")
            for i in range(20):
                ok, cur = check_url(sb, "hcnsec", tag="open")
                if not ok:
                    result["error"] = f"打开页面被劫持: {cur[:80]}"
                    return result
                if sb.is_element_visible("input[name='username']"):
                    print(f"  ✅ React 已渲染 (等待 {i}s)")
                    break
                sb.sleep(1)
            else:
                result["error"] = "React 未渲染"
                return result

            print("\n⏳ 等待 Turnstile api.js 初始化…")
            for i in range(15):
                st = get_turnstile_state(sb)
                if "turnstile=object" in st:
                    print(f"  ✅ {st}")
                    break
                sb.sleep(1)

            print("\n✍️ 填写表单…")
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

            print(f"  username fill: {react_fill('username', EMAIL)}")
            sb.sleep(0.5)
            print(f"  password fill: {react_fill('password', PASSWORD)}")
            sb.sleep(0.5)

            print("☑️ 勾选法律同意…")
            consent_sel = "span[role='checkbox'][aria-labelledby='legal-consent-label']"
            try:
                sb.wait_for_element_present(consent_sel, timeout=10)
                if sb.get_attribute(consent_sel, "aria-checked") != "true":
                    sb.click(consent_sel)
                    sb.sleep(1.5)
            except Exception as e:
                print(f"⚠️ 勾选异常: {e}")

            print("\n🎯 点击登录 Turnstile…")
            token_ok = False
            for attempt in range(5):
                try:
                    sb.uc_gui_click_captcha()
                    print(f"  第 {attempt+1} 次点击")
                except Exception as e:
                    print(f"  第 {attempt+1} 次异常: {str(e)[:100]}")
                sb.sleep(3)

                ok, cur = check_url(sb, "hcnsec", tag=f"after_captcha_{attempt+1}")
                if not ok:
                    result["error"] = f"点击 Turnstile 后被劫持: {cur[:80]}"
                    return result

                try:
                    tk = sb.execute_script("""
                        var el = document.querySelector('[name="cf-turnstile-response"]');
                        return el ? (el.value || '').length : 0;
                    """)
                    if tk and tk > 20:
                        print(f"  ✅ Token 出现！长度={tk}")
                        token_ok = True
                        break
                except Exception:
                    pass

            if not token_ok:
                print("❌ Token 未出现")
                sb.save_screenshot("token_timeout.png")
                result["error"] = "Turnstile token 未出现"
                return result

            print("\n🖱️ 点击登录按钮…")
            try:
                login_btn = sb.find_element("button[type='submit']")
                try:
                    login_btn.click()
                except Exception:
                    sb.execute_script("""
                        var b = document.querySelector("button[type='submit']");
                        if (b) { b.disabled = false; b.removeAttribute('disabled'); b.click(); }
                    """)
            except Exception as e:
                print(f"  ⚠️ 点击异常: {e}")
            sb.sleep(3)

            ok, cur = check_url(sb, "hcnsec", tag="after_login_click")
            if not ok:
                result["error"] = f"登录后跳到异常域名: {cur[:80]}"
                return result

            print("⏳ 等待登录结果…")
            logged_in = False
            for i in range(30):
                sb.sleep(2)
                cur = sb.get_current_url()
                if "hcnsec" not in cur:
                    result["error"] = f"被劫持: {cur[:80]}"
                    return result
                if "hcnsec" in cur and LOGIN_PATH not in cur and "/login" not in cur:
                    logged_in = True
                    print(f"✅ 登录成功: {cur}")
                    break

            if not logged_in:
                sb.save_screenshot("login_failed.png")
                result["error"] = "登录失败"
                return result

            result["logged_in"] = True
            sb.sleep(3)

            print("📄 打开个人资料页面…")
            safe_goto(sb, PROFILE_URL, tag="profile")
            sb.sleep(3)

            ok, cur = check_url(sb, "hcnsec", tag="profile")
            if not ok:
                result["error"] = f"打开 profile 跳到异常域名: {cur[:80]}"
                return result

            print(f"  当前页面: {cur}")

            info = wait_for_checkin_button(sb, max_wait=40)
            if info.get("hijacked"):
                result["error"] = f"等待按钮时被劫持: {info['hijacked'][:80]}"
                return result

            state = info.get("state", "not_found")
            result["button_before"] = info.get("text", "")
            print(f"  最终按钮状态: state={state} text='{info.get('text','')}'")

            if state == "already":
                print(f"✅ 今日已签到")
                result["already_done"] = True
                result["checkin_clicked"] = True
                result["button_after"] = info.get("text", "")
                award_text, award_amount = read_amount_via_xpath(sb)
                result["award_text"] = award_text
                result["award_amount"] = award_amount
                print(f"  金额文字: '{award_text}' | 提取: +¥{award_amount}")
                try: sb.save_screenshot("checkin_result.png")
                except Exception: pass
                return result

            if state == "not_found":
                print("❌ 未找到签到按钮")
                for line in list_all_buttons_safe(sb):
                    print(f"    {line}")
                sb.save_screenshot("no_checkin_button.png")
                result["error"] = "未找到签到按钮"
                return result

            print(f"  ✅ 找到'立即签到'按钮")

            # ============================================================
            # ★ v26: 主动渲染 Turnstile → 点击 → fetch 兜底
            # ============================================================
            print("\n🎯 步骤 1: 主动渲染签到 Turnstile…")
            token_ok = ensure_turnstile_token(sb)

            if not token_ok:
                print("❌ 签到 Turnstile token 获取失败")
                sb.save_screenshot("checkin_token_failed.png")
                result["error"] = "签到 Turnstile token 获取失败"
                return result

            print("\n🎯 步骤 2: 点击签到按钮…")
            click_ok = click_checkin_button(sb)
            result["checkin_clicked"] = True
            print(f"  点击结果: {click_ok}")

            print("\n⏳ 步骤 3: 等待 5s…")
            sb.sleep(5)

            new_state = find_checkin_state(sb)
            st = new_state.get("state", "?")
            print(f"  按钮状态: {st}")

            # 如果按钮没变，用 API 直接调用
            if st != "already":
                print("\n🎯 步骤 4: 按钮未变，直接用 fetch 调 API…")
                try:
                    token = sb.execute_script("return window.__checkin_token || '';")
                    if token and len(token) > 20:
                        direct_fetch_checkin(sb, token)
                        sb.sleep(3)
                        print("  重新加载 profile 页面…")
                        safe_goto(sb, PROFILE_URL, tag="profile-reload")
                        sb.sleep(5)
                    else:
                        print(f"  ⚠️ 无 token 可用 (len={len(token) if token else 0})")
                except Exception as e:
                    print(f"  fetch 异常: {e}")

            print("\n🔍 检查最终状态…")
            new_state = find_checkin_state(sb)
            result["button_after"] = new_state.get("text", "")
            print(f"  最终按钮: state={new_state.get('state')} text='{new_state.get('text','')}'")

            award_text, award_amount = read_amount_via_xpath(sb)
            result["award_text"] = award_text
            result["award_amount"] = award_amount
            print(f"  金额文字: '{award_text}' | 提取: +¥{award_amount}")

            try: sb.save_screenshot("checkin_result.png")
            except Exception: pass
            return result

        except Exception as e:
            print(f"❌ 浏览器异常: {e}")
            import traceback
            traceback.print_exc()
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
