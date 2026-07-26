#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HidenCloud 自动续期脚本（优化版）
- 使用独立 BrowserContext 隔离多账号
- 函数式结构，便于阅读和维护
- 支持 Cookie 免密登录 + 账密兜底
- 自动回写新 Cookie 到 GitHub Secret
- Telegram 汇总报告
"""

import os
import sys
import json
import time
import random
import re
import requests
from datetime import datetime
from base64 import b64encode

from DrissionPage import Chromium, ChromiumOptions

# ---------------- 可选依赖 ----------------
try:
    import nacl.public
    import nacl.encoding
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

# ==================== 配置读取 ====================

def load_config():
    """从环境变量读取配置"""
    accounts_str = os.getenv('ACCOUNTS', '[]')
    try:
        accounts = json.loads(accounts_str)
    except Exception as e:
        log(f"❌ ACCOUNTS 解析失败: {e}")
        accounts = []

    return {
        'tg_token': os.getenv('TG_BOT_TOKEN'),
        'tg_chat_id': os.getenv('TG_CHAT_ID'),
        'gh_token': os.getenv('REPO_TOKEN') or os.getenv('GH_TOKEN'),
        'gh_repo': os.getenv('GITHUB_REPOSITORY'),
        'accounts': accounts,
    }

# ==================== 工具函数 ====================

def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def send_tg(config: dict, message: str):
    if not config['tg_token'] or not config['tg_chat_id']:
        return
    try:
        url = f"https://api.telegram.org/bot{config['tg_token']}/sendMessage"
        requests.post(url, json={
            "chat_id": config['tg_chat_id'],
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        log(f"❌ TG 发送失败: {e}")

def update_github_secret(config: dict, secret_name: str, secret_value: str) -> bool:
    """把新 Cookie 加密后写回 GitHub Secret"""
    if not (config['gh_token'] and config['gh_repo'] and NACL_AVAILABLE):
        return False

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {config['gh_token']}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    try:
        r = requests.get(
            f"https://api.github.com/repos/{config['gh_repo']}/actions/secrets/public-key",
            headers=headers, timeout=15
        )
        if r.status_code != 200:
            return False

        key_data = r.json()
        public_key = nacl.public.PublicKey(
            key_data['key'].encode('utf-8'),
            nacl.encoding.Base64Encoder()
        )
        sealed_box = nacl.public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(secret_value.encode('utf-8'))
        encrypted_value = b64encode(encrypted).decode('utf-8')

        r_update = requests.put(
            f"https://api.github.com/repos/{config['gh_repo']}/actions/secrets/{secret_name}",
            headers=headers,
            json={"encrypted_value": encrypted_value, "key_id": key_data['key_id']},
            timeout=15
        )
        if r_update.status_code in (201, 204):
            log(f"🎉 成功更新 GitHub Secret: [{secret_name}]")
            return True
        return False
    except Exception:
        return False

# ==================== 浏览器相关 ====================

def create_browser_options() -> ChromiumOptions:
    """完善的浏览器启动配置"""
    co = ChromiumOptions()
    co.set_browser_path('/usr/bin/google-chrome')
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--window-size=1920,1080')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--disable-infobars')
    co.headless(False)  # Turnstile 强烈建议有头

    # 真实一点的 User-Agent
    co.set_user_agent(
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
    )
    return co

def solve_turnstile(tab) -> bool:
    """尝试处理 Cloudflare Turnstile"""
    log("🛡️ 尝试处理 Turnstile...")
    try:
        iframe = tab.get_frame('css:iframe[src^="https://challenges.cloudflare.com"]', timeout=5)
        if not iframe:
            return True  # 没有验证码，直接通过

        time.sleep(2)
        try:
            # 优先走 shadow root
            sr = iframe.ele('tag:body').shadow_root
            if sr:
                target = sr.ele('css:input[type="checkbox"]') or sr.ele('css:div.main-wrapper')
                if target:
                    target.click.at(offset_x=10, offset_y=10)
        except Exception:
            try:
                iframe.frame_ele.click.at(offset_x=25, offset_y=30)
            except Exception:
                pass

        # 等待 token 出现
        for _ in range(15):
            time.sleep(1)
            resp = tab.ele('css:[name="cf-turnstile-response"]')
            if resp and len(resp.value or '') > 10:
                log("✅ Turnstile 通过")
                return True
        return False
    except Exception:
        return False

def extract_due_date(tab) -> str:
    """提取到期时间（多种策略）"""
    try:
        # 策略1：找 Due date 标签附近的日期
        due_label = (
            tab.ele('text:Due date') or
            tab.ele('text:Due Date') or
            tab.ele('text:DUE DATE')
        )
        if due_label and due_label.parent():
            m = re.search(r'(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})', due_label.parent().text)
            if m:
                return m.group(1)

        # 策略2：抓页面所有日期，取最后一个（避开 Member since）
        dates = re.findall(r'(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})', tab.html)
        if dates:
            return dates[-1] if len(dates) > 1 else dates[0]
    except Exception as e:
        log(f"⚠️ 日期提取异常: {e}")
    return "未知日期"

# ==================== 账号处理核心 ====================

def try_cookie_login(tab, cookie_env: str) -> bool:
    """尝试用已有 Cookie 免密登录"""
    saved = os.getenv(cookie_env, '[]')
    if not saved or saved == '[]':
        return False

    log("🍪 尝试 Cookie 免密登录...")
    try:
        cookie_name = "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d"
        cookie_value = None

        try:
            raw = json.loads(saved)
            if isinstance(raw, list) and raw:
                cookie_name = raw[0].get('name', cookie_name)
                cookie_value = raw[0].get('value')
        except Exception:
            cookie_value = saved.strip()

        if not cookie_value:
            return False

        # 先访问中转页，再注入 cookie
        tab.get("https://dash.hidencloud.com/robots.txt")
        time.sleep(1)

        # 使用 context / tab 级别设置 cookie（独立上下文更干净）
        tab.set.cookies([{
            'name': cookie_name,
            'value': cookie_value,
            'domain': 'dash.hidencloud.com',
            'path': '/',
            'secure': True,
            'httpOnly': True,
            'sameSite': 'Lax',
            'expires': int(time.time()) + 3600 * 24 * 365
        }])

        tab.get("https://dash.hidencloud.com/dashboard")
        time.sleep(4)

        if "login" not in tab.url and "Your Services" in tab.html:
            log("🎉 Cookie 登录成功")
            return True

        log("⚠️ Cookie 可能已过期")
        return False
    except Exception as e:
        log(f"⚠️ Cookie 注入异常: {e}")
        return False

def try_password_login(tab, email: str, password: str, cookie_env: str, config: dict) -> bool:
    """账密登录（兜底），成功后自动回写 Cookie"""
    log("🔑 使用账号密码登录...")
    try:
        tab.get("https://dash.hidencloud.com/auth/login")
        time.sleep(3)

        email_input = tab.ele('css:input[name="username"]') or tab.ele('css:input[name="email"]')
        if not email_input:
            if tab.ele('css:iframe[src^="https://challenges.cloudflare.com"]'):
                solve_turnstile(tab)
                time.sleep(3)
                email_input = tab.ele('css:input[name="username"]') or tab.ele('css:input[name="email"]')

        if not email_input:
            log("❌ 找不到登录输入框（可能被拦截）")
            try:
                tab.get_screenshot(path='.', name='err_no_input.png')
            except Exception:
                pass
            return False

        email_input.input(email, clear=True)
        time.sleep(0.5)
        tab.ele('css:input[name="password"]').input(password, clear=True)
        time.sleep(1)

        if tab.ele('css:iframe[src^="https://challenges.cloudflare.com"]'):
            solve_turnstile(tab)

        login_btn = (
            tab.ele('xpath://button[contains(text(), "Sign in")]') or
            tab.ele('css:button[type="submit"]')
        )
        if login_btn:
            login_btn.click()
        else:
            tab.ele('css:input[name="password"]').input('\n')

        for _ in range(15):
            if "login" not in tab.url and "Your Services" in tab.html:
                log("🎉 账密登录成功")
                # 抓取新 Cookie 并回写
                if cookie_env:
                    for c in tab.cookies():
                        if c.get('name', '').startswith('remember_web_'):
                            new_cookie = {'name': c['name'], 'value': c['value']}
                            update_github_secret(config, cookie_env, json.dumps([new_cookie]))
                            break
                return True
            time.sleep(1)

        log("❌ 登录失败（密码错误或被拦截）")
        return False
    except Exception as e:
        log(f"❌ 登录异常: {e}")
        return False

def do_renew(tab) -> dict:
    """执行续期流程，返回结果字典"""
    res = {"server": "?", "old_date": "?", "new_date": "?", "status": ""}

    # 找服务器
    server_ele = None
    for _ in range(10):
        server_ele = tab.ele('xpath://*[contains(text(), "Free Server #")]')
        if server_ele:
            break
        time.sleep(1)

    if not server_ele:
        res["status"] = "❌ 找不到服务器"
        return res

    m = re.search(r'#(\d{6})', server_ele.text)
    if not m:
        res["status"] = "❌ ID 解析失败"
        return res

    server_id = m.group(1)
    res["server"] = server_id
    res["old_date"] = extract_due_date(tab)

    # 进入管理页
    tab.get(f"https://dash.hidencloud.com/service/{server_id}/manage")
    time.sleep(4)

    renew_btn = tab.ele('xpath://button[contains(., "Renew")]')
    if not renew_btn:
        res["status"] = "❌ 找不到续期按钮"
        return res

    renew_btn.click()
    time.sleep(2)

    page_text = tab.html
    if "Renewal Restricted" in page_text or "less than 1 day left" in page_text:
        log("⚠️ 未到续期时间")
        res["status"] = "⏭️ 未到期"
        return res

    create_btn = tab.ele('xpath://button[contains(., "Create Invoice")]')
    if not create_btn:
        res["status"] = "❌ 弹窗状态未知"
        return res

    create_btn.click()

    # 等待跳转到发票页
    for _ in range(15):
        if "/payment/invoice/" in tab.url:
            break
        time.sleep(1)

    if "/payment/invoice/" not in tab.url:
        res["status"] = "❌ 账单页超时"
        return res

    tab.scroll.to_bottom()
    time.sleep(1)

    pay_btn = tab.ele('xpath://button[contains(., "Pay")]')
    if not pay_btn:
        res["status"] = "❌ 支付按钮缺失"
        return res

    pay_btn.click()

    for _ in range(15):
        if "dashboard" in tab.url and "Success" in tab.html:
            break
        time.sleep(1)

    if "Success" in tab.html:
        time.sleep(2)
        res["new_date"] = extract_due_date(tab)
        res["status"] = "✅ 续期成功"
    else:
        res["status"] = "⚠️ 支付状态未知"

    return res

def process_account(browser, account: dict, index: int, total: int, config: dict) -> dict:
    """处理单个账号（独立上下文）"""
    email = account.get('email', '')
    password = account.get('password', '')
    cookie_env = account.get('cookie_env', '')
    display_name = email.split('@')[0] if '@' in email else f"账号{index+1}"

    log(f"\n{'='*50}")
    log(f"🚀 处理账号 [{index+1}/{total}]: {display_name}")

    res = {
        "name": display_name,
        "server": "?",
        "old_date": "?",
        "new_date": "?",
        "status": ""
    }

    if not email or not password:
        res["status"] = "❌ 配置错误"
        return res

    context = None
    try:
        # ---------- 关键独立上下文 ----------
        context = browser.new_context()
        tab = context.new_tab()

        # 1. 先尝试 Cookie 登录
        logged_in = try_cookie_login(tab, cookie_env)

        # 2. 失败则走账密
        if not logged_in:
            logged_in = try_password_login(tab, email, password, cookie_env, config)

        if not logged_in:
            res["status"] = "❌ 登录失败"
            return res

        # 3. 执行续期
        renew_res = do_renew(tab)
        res.update(renew_res)

    except Exception as e:
        log(f"💥 异常: {e}")
        res["status"] = "❌ 脚本异常"
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass

    return res

# ==================== 主流程 ====================

def main():
    config = load_config()
    accounts = config['accounts']

    if not accounts:
        send_tg(config, "🚨 HidenCloud\n❌ ACCOUNTS 未配置。")
        return

    co = create_browser_options()
    browser = None
    results = []

    try:
        browser = Chromium(co)

        for i, account in enumerate(accounts):
            res = process_account(browser, account, i, len(accounts), config)

            icon = "✅" if "成功" in res['status'] else ("⏭️" if "未到期" in res['status'] else "❌")
            line = (
                f"{icon} <b>{res['name']}</b> (<code>#{res['server']}</code>)\n"
                f"📅 旧到期: {res['old_date']}\n"
            )
            if "成功" in res['status']:
                line += f"📅 新到期: {res['new_date']}\n"
            line += f"🖥️ 状态: {res['status']}\n"
            results.append(line)

            if i < len(accounts) - 1:
                time.sleep(random.randint(3, 6))

    except Exception as e:
        results.append(f"❌ 浏览器异常: {e}")
    finally:
        if browser:
            try:
                browser.quit()
            except Exception:
                pass

        if results:
            send_tg(config, "☁️ <b>HidenCloud 续期报告</b>\n\n" + "\n".join(results))

if __name__ == "__main__":
    main()
