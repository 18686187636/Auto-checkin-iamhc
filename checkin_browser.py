#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# v27-hcnsec: 点签到触发 CF 弹窗 + uc_gui_click_captcha 处理

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


# ============================================================
# ★★★ v27: CF 弹窗检测
# ============================================================

def detect_cf_popup(sb):
    """检测页面上是否出现 Cloudflare Turnstile 弹窗"""
    try:
        info = sb.execute_script("""
            var ifr = document.querySelectorAll('iframe');
            var srcs = [];
            var cf_count = 0;
            for (var i = 0; i < ifr.length; i++) {
                var s = ifr[i].src || '';
                srcs.push(s.slice(0, 100));
                if (s.indexOf('challenges.cloudflare') >= 0 || s.indexOf('turnstile') >= 0) {
                    cf_count++;
                }
            }
            var tk = document.querySelector('[name="cf-turnstile-response"]');
            return JSON.stringify({
                total_iframes: ifr.length,
                cf_iframes: cf_count,
                iframe_srcs: srcs,
                token_len: tk ? (tk.value || '').length : -1
            });
        """)
        return json.loads(info) if info else {}
    except Exception as e:
        return {"error": str(e)}


def wait_cf_popup_and_solve(sb, max_wait=60):
    """等 CF 弹窗出现，然后用 uc_gui_click_captcha 处理"""
    print(f"    🔐 等待 CF 弹窗出现（最多 {max_wait}s）…")
    token_ok = False
    for i in range(max_wait // 2):
        sb.sleep(2)

        # 检测 URL 是否被劫持
        cur = sb.get_current_url()
        if "hcnsec" not in cur:
            print(f"    ❌ 页面被劫持到: {cur}")
            return False

        # 检测 Turnstile token
        try:
            tk_len = sb.execute_script("""
                var el = document.querySelector('[name="cf-turnstile-response"]');
                return el ? (el.value || '').length : 0;
            """)
            if tk_len and tk_len > 20:
                print(f"    ✅ 第 {i*2}s token 就绪！长度={tk_len}")
                token_ok = True
                break
        except Exception:
            pass

        # 检测弹窗
        popup = detect_cf_popup(sb)
        cf_count = popup.get("cf_iframes", 0)
        if cf_count > 0:
            if i % 2 == 0:
                print(f"    第 {i*2}s: 检测到 {cf_count} 个 CF iframe，点击…")
            try:
                sb.uc_gui_click_captcha()
            except Exception:
                pass
        elif i % 5 == 4:
            print(f"    第 {i*2}s: 未检测到 CF 弹窗 (iframes={popup.get('total_iframes',0)})")

    return token_ok


def click_checkin_button(sb):
    """点击签到按钮"""
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

            # ============================================================
            # ★★★ v27 核心：点签到 → 等弹窗 → uc_gui_click_captcha
            # ============================================================
            print(f"  ✅ 找到'立即签到'按钮")

            print("\n🎯 步骤 1: 点击'立即签到'触发 CF 弹窗…")
            click_ok = click_checkin_button(sb)
            result["checkin_clicked"] = True
            print(f"  点击结果: {click_ok}")

            print("\n🎯 步骤 2: 等 CF 弹窗 + 处理…")
            token_ok = wait_cf_popup_and_solve(sb, max_wait=60)

            if token_ok:
                print("  ✅ CF token 已获取")
                try:
                    sb.save_screenshot("after_checkin_token.png")
                except Exception:
                    pass
                # 等几秒让签到请求完成
                print("  ⏳ 等 8s 让签到请求完成…")
                sb.sleep(8)
            else:
                print("  ⚠️ 未获取 CF token，尝试再点一次…")
                try:
                    sb.uc_gui_click_captcha()
                except Exception:
                    pass
                sb.sleep(5)

            # 等按钮状态变化
            print("\n🔍 检查按钮状态…")
            for i in range(15):
                new_state = find_checkin_state(sb)
                st = new_state.get("state", "?")
                txt = new_state.get("text", "")
                if st == "already":
                    print(f"  ✅ 第 {i*2}s 签到成功！")
                    break
                if i % 3 == 2:
                    print(f"  ⏳ {i*2}s: state={st} text='{txt}'")
                    # 每次检查都尝试点一次
                    try:
                        sb.uc_gui_click_captcha()
                    except Exception:
                        pass
                sb.sleep(2)

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
