#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# v2-hcnsec: Turnstile 硬检查 + 精确登录按钮定位 + 域名校验

import os, sys, time, json, re, requests
from datetime import datetime, timezone, timedelta

EMAIL        = os.environ.get("EMAIL") or ""
PASSWORD     = os.environ.get("PASSWORD") or ""
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://api.hcnsec.cn"
TZ_CN = timezone(timedelta(hours=8))

LOGIN_URL  = f"{BASE_URL}/sign-in"
LOGIN_PATH = "/sign-in"
PROFILE_URL = f"{BASE_URL}/profile"


def send_notification(message):
    print("\n" + "=" * 30)
    print(message)
    print("=" * 30)
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
            print("Telegram:", "成功" if r.status_code == 200 else f"失败 {r.status_code}")
        except Exception as e:
            print("Telegram 异常:", e)


# ============================================================
# ★★★ 新增：精确查找登录按钮
# ============================================================
def find_login_button(sb):
    """
    在页面里精确定位登录按钮。返回 (定位符, 元素文本) 或 (None, None)。
    只考虑表单内的 submit 按钮，或文字明确是"登录/Sign in"的按钮。
    排除"搜索/订阅"等明显无关按钮。
    """
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
    # 明确排除的关键词（防止误点搜索/订阅/注册）
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
    """读取“每日签到”卡片内的今日奖励金额，并打印所有候选元素用于诊断"""
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
    """
    在页面中查找签到按钮。
    返回 dict: {"state": "ready"/"already"/"not_found", "text": ..., "diag": ...}
    关键：在多个包含“立即签到”的元素中，选文本最短的（最内层），优先 button 标签。
    """
    raw = sb.execute_script("""
        (function() {
            const allElems = document.querySelectorAll('button, a, div, span');
            let bestImmediate = null;   // {el, text, isButton}
            let bestDone = null;

            for (let e of allElems) {
                const t = (e.textContent || '').trim();
                if (t.length > 60) continue;      // 跳过超长容器

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
    with SB(
        uc=True,
        headed=True,
        xvfb=False,
        incognito=True,
        locale_code="zh-CN",
        window_size="1920,1080",
        chromium_arg="--no-sandbox,--disable-dev-shm-usage,--disable-gpu",
    ) as sb:
        try:
            # ---------- 打开登录页 ----------
            print(f"🌐 打开登录页: {LOGIN_URL}")
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            sb.sleep(3)

            print("⏳ 等待登录表单…")
            sb.wait_for_element_visible("input[name='username']", timeout=30)
            sb.wait_for_element_visible("input[name='password']", timeout=10)

            print("✍️ 填写登录凭证…")
            sb.type("input[name='username']", EMAIL)
            sb.sleep(0.5)
            sb.type("input[name='password']", PASSWORD)
            sb.sleep(0.5)

            print("☑️ 勾选法律同意…")
            consent = "span[role='checkbox'][aria-labelledby='legal-consent-label']"
            try:
                sb.wait_for_element_present(consent, timeout=10)
                if sb.get_attribute(consent, "aria-checked") != "true":
                    sb.click(consent)
                    sb.sleep(0.8)
            except Exception as e:
                print(f"⚠️ 勾选异常: {e}")

            # ---------- 保存登录页源码（诊断用） ----------
            try:
                with open("login_page_source.html", "w", encoding="utf-8") as f:
                    f.write(sb.get_page_source())
                print("  📄 已保存登录页源码到 login_page_source.html")
            except Exception as e:
                print(f"  保存登录页源码失败: {e}")

            # ---------- Turnstile ----------
            print("⏳ 等待登录 Turnstile…")
            sb.sleep(5)
            try:
                sb.uc_gui_click_captcha()
            except Exception as e:
                print(f"  click_captcha 异常: {e}")

            # ★★★ 硬检查：60s 内没拿到 token 就放弃
            turnstile_ok = False
            for i in range(60):
                tk = sb.execute_script(
                    'return (document.querySelector(\'[name="cf-turnstile-response"]\') || {}).value || "";'
                )
                if tk:
                    print(f"✅ 登录 Turnstile token 长度: {len(tk)}")
                    turnstile_ok = True
                    break
                sb.sleep(1)

            if not turnstile_ok:
                print("❌ Turnstile 60s 内未生成 token，放弃登录")
                try:
                    sb.save_screenshot("turnstile_timeout.png")
                except Exception:
                    pass
                result["error"] = "Turnstile 未通过"
                return result

            # ---------- 定位登录按钮 ----------
            print("🖱️ 定位登录按钮…")
            try:
                all_submits = sb.execute_script("""
                    (function() {
                        const out = [];
                        document.querySelectorAll("button[type='submit']").forEach(function(b) {
                            out.push({
                                text: (b.textContent || '').trim().slice(0, 40),
                                visible: !!(b.offsetWidth || b.offsetHeight || b.getClientRects().length),
                                inForm: !!b.closest('form'),
                                cls: (b.className || '').slice(0, 60)
                            });
                        });
                        return JSON.stringify(out);
                    })()
                """)
                print(f"  页面所有 submit 按钮: {all_submits}")
            except Exception as e:
                print(f"  诊断异常: {e}")

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
            sb.click(login_sel)
            sb.sleep(1.5)

            # ---------- 立即校验域名 ----------
            cur_check = sb.get_current_url()
            print(f"  点击后 URL: {cur_check}")
            if "hcnsec" not in cur_check:
                print(f"❌ 登录按钮点击后跳到异常域名: {cur_check}")
                print("   可能是点错了按钮或代理劫持")
                try:
                    sb.save_screenshot("login_wrong_redirect.png")
                except Exception:
                    pass
                result["error"] = f"登录后跳到异常域名: {cur_check[:80]}"
                return result

            # ---------- 等待登录跳转 ----------
            print("⏳ 等待登录跳转…")
            logged_in = False
            for i in range(30):
                sb.sleep(2)
                cur = sb.get_current_url()
                # 必须回到 hcnsec 域名，且不含 sign-in/login
                if "hcnsec" in cur and LOGIN_PATH not in cur and "/login" not in cur:
                    logged_in = True
                    print(f"✅ 登录成功: {cur}")
                    break
                # 跳到其他域名 → 立即中止
                if "hcnsec" not in cur:
                    print(f"❌ 登录过程中跳到异常域名: {cur}")
                    try:
                        sb.save_screenshot("login_jumped_away.png")
                    except Exception:
                        pass
                    break

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
            cur = sb.get_current_url()
            print(f"  当前页面: {cur}")
            if "sign-in" in cur or "/login" in cur:
                result["error"] = "登录状态失效（打开 profile 被重定向回登录页）"
                return result
            if "hcnsec" not in cur:
                result["error"] = f"打开 profile 跳到异常域名: {cur[:80]}"
                return result

            # ---------- 检测按钮状态 ----------
            print("🔍 检测签到按钮状态…")
            info = find_checkin_button(sb)
            print(f"  按钮状态: {json.dumps(info, ensure_ascii=False)}")

            state = info.get("state", "not_found")
            result["button_before"] = info.get("text", "")

            # ---------- 已签到 → 读金额 ----------
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

            # ---------- 未找到按钮 ----------
            if state == "not_found":
                print(f"⚠️ 未找到签到按钮")
                print(f"  含“签到”的元素: {info.get('checkin_elems')}")
                result["error"] = "未找到签到按钮"
                sb.save_screenshot("no_checkin_button.png")
                try:
                    with open("no_checkin_button.html", "w", encoding="utf-8") as f:
                        f.write(sb.get_page_source())
                    print("  已保存页面源码到 no_checkin_button.html")
                except Exception as e:
                    print(f"  保存源码失败: {e}")
                return result

            # ---------- 执行签到 ----------
            print(f"  ✅ 找到'立即签到'按钮，开始签到…")
            print(f"    匹配元素: tag={info.get('tag')} is_button={info.get('is_button')} text='{info.get('text')}'")

            try:
                sb.uc_click("[data-checkin-target='1']", reconnect_time=2)
                result["checkin_clicked"] = True
                print("✅ 已点击 (uc_click)")
            except Exception as e:
                print(f"  ⚠️ uc_click 异常: {e}")

            print("⏳ 等待 CF 弹窗渲染（3 秒）…")
            sb.sleep(3)

            print("🔐 尝试处理 CF 验证…")
            for attempt in range(3):
                try:
                    sb.uc_gui_click_captcha()
                except Exception:
                    pass
                sb.sleep(3)

            print("⏳ 等待签到请求完成（15 秒）…")
            sb.sleep(15)

            after_info = find_checkin_button(sb)
            if after_info.get("state") == "ready":
                print("  ⚠️ uc_click 后按钮状态未变，尝试 JS 直接 click…")
                js_clicked = sb.execute_script("""
                    (function() {
                        const el = document.querySelector("[data-checkin-target='1']");
                        if (!el) return false;
                        let target = el;
                        if (el.tagName !== 'BUTTON') {
                            const innerBtn = el.querySelector('button');
                            if (innerBtn) target = innerBtn;
                        }
                        target.click();
                        return true;
                    })()
                """)
                print(f"  JS click 结果: {js_clicked}")
                result["checkin_clicked"] = True

                print("⏳ 再次等待签到请求完成（12 秒）…")
                sb.sleep(12)

                try:
                    sb.uc_gui_click_captcha()
                except Exception:
                    pass
                sb.sleep(5)

            # ---------- 读取金额 + 按钮状态 ----------
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
