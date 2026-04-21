# scripts/hidencloud_renew.py
# -*- coding: utf-8 -*-

import os
import time
import random
import re
import requests
import sys
import json
from datetime import datetime
from DrissionPage import ChromiumPage, ChromiumOptions

try:
    import nacl.public
    import nacl.encoding
    from base64 import b64encode
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

class HidenCloudAutoRenew:
    def __init__(self):
        self.tg_token = os.getenv('TG_BOT_TOKEN')
        self.tg_chat_id = os.getenv('TG_CHAT_ID')
        self.gh_token = os.getenv('REPO_TOKEN') or os.getenv('GH_TOKEN')
        self.gh_repo = os.getenv('GITHUB_REPOSITORY')
        
        accounts_str = os.getenv('ACCOUNTS', '[]')
        try:
            self.accounts = json.loads(accounts_str)
        except Exception as e:
            self.log(f"❌ ACCOUNTS 解析失败: {e}")
            self.accounts = []
            
        self.results = []

    def log(self, msg):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        sys.stdout.flush()

    def send_tg_notification(self, message):
        if not self.tg_token or not self.tg_chat_id: return
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {"chat_id": self.tg_chat_id, "text": message, "parse_mode": "HTML"}
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            self.log(f"❌ TG 发送失败: {e}")

    def update_github_secret(self, secret_name, secret_value):
        if not self.gh_token or not self.gh_repo or not NACL_AVAILABLE: return False
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.gh_token}", "X-GitHub-Api-Version": "2022-11-28"}
        try:
            r = requests.get(f"https://api.github.com/repos/{self.gh_repo}/actions/secrets/public-key", headers=headers)
            if r.status_code != 200: return False
            key_data = r.json()
            public_key = nacl.public.PublicKey(key_data['key'].encode('utf-8'), nacl.encoding.Base64Encoder())
            sealed_box = nacl.public.SealedBox(public_key)
            encrypted = sealed_box.encrypt(secret_value.encode('utf-8'))
            encrypted_value = b64encode(encrypted).decode('utf-8')
            r_update = requests.put(f"https://api.github.com/repos/{self.gh_repo}/actions/secrets/{secret_name}", headers=headers, json={"encrypted_value": encrypted_value, "key_id": key_data['key_id']})
            if r_update.status_code in [201, 204]:
                self.log(f"🎉 成功将核心票据自动更新至 Github Secret: [{secret_name}]")
                return True
            return False
        except: return False

    def solve_turnstile(self, page):
        self.log("🛡️ 尝试处理 Turnstile (仅账密登录兜底)...")
        try:
            target_iframe = page.get_frame('css:iframe[src^="https://challenges.cloudflare.com"]', timeout=5)
            if not target_iframe: return False
            time.sleep(2)
            try:
                sr = target_iframe.ele('tag:body').shadow_root
                if sr:
                    target_ele = sr.ele('css:input[type="checkbox"]') or sr.ele('css:div.main-wrapper')
                    if target_ele:
                        target_ele.click.at(offset_x=10, offset_y=10)
            except: 
                try: target_iframe.frame_ele.click.at(offset_x=25, offset_y=30)
                except: pass
            
            for _ in range(15):
                time.sleep(1)
                resp = page.ele('css:[name="cf-turnstile-response"]')
                if resp and len(resp.value) > 10: return True
            return False
        except: return False

    def extract_due_date(self, page):
        try:
            date_match = re.search(r'(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})', page.html)
            return date_match.group(1) if date_match else "未知日期"
        except: return "未知日期"

    def process_account(self, page, account, index):
        email, password, cookie_env = account.get('email', ''), account.get('password', ''), account.get('cookie_env', '')
        display_name = email.split('@')[0] if '@' in email else f"账号{index+1}"
        res = {"name": display_name, "server": "?", "old_date": "?", "new_date": "?", "status": ""}
        self.log(f"\n{'='*50}\n🚀 处理账号 [{index+1}/{len(self.accounts)}]: {display_name}")

        if not email or not password:
            res["status"] = "❌ 配置错误"
            return res

        try:
            page.clear_cache(cookies=True)
            saved_cookie_str = os.getenv(cookie_env, '[]')
            logged_in = False
            
            # --- 步骤 1: 实战验证成功的 CDP 极速免密登录 ---
            if saved_cookie_str and saved_cookie_str != '[]':
                self.log("🍪 启动 CDP 底层极速免密登录...")
                try:
                    cookie_name = "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d"
                    cookie_value = None
                    try:
                        raw_data = json.loads(saved_cookie_str)
                        if isinstance(raw_data, list) and len(raw_data) > 0:
                            cookie_name = raw_data[0].get('name', cookie_name)
                            cookie_value = raw_data[0].get('value')
                    except:
                        cookie_value = saved_cookie_str.strip()

                    if cookie_value:
                        # 1. 访问中转站避开服务端 Session
                        page.get("https://dash.hidencloud.com/robots.txt")
                        time.sleep(1)
                        # 2. 彻底清洗环境
                        page.clear_cache(cookies=True)
                        # 3. 底层强行注入
                        page.run_cdp('Network.setCookie', 
                            name=cookie_name, value=cookie_value,
                            domain='dash.hidencloud.com', path='/',
                            secure=True, httpOnly=True, sameSite='Lax',
                            expires=int(time.time()) + 3600 * 24 * 365
                        )
                        self.log("✅ 核心金牌注入完成，直达 Dashboard...")
                        # 4. 进入面板
                        page.get("https://dash.hidencloud.com/dashboard")
                        time.sleep(4)
                        
                        if "login" not in page.url and "Your Services" in page.html:
                            logged_in = True
                            self.log("🎉 Cookie 免密登录完美成功！")
                        else:
                            self.log("⚠️ Cookie 可能已在服务端过期，启动降级方案...")
                except Exception as e:
                    self.log(f"⚠️ 注入异常: {e}")

            # --- 步骤 2: 降级账密登录 (作为兜底) ---
            if not logged_in:
                self.log("🔑 退回账号密码登录...")
                page.clear_cache(cookies=True)
                page.get("https://dash.hidencloud.com/auth/login")
                time.sleep(3) 

                email_input = page.ele('css:input[name="username"]') or page.ele('css:input[name="email"]')
                if not email_input:
                    if page.ele('css:iframe[src^="https://challenges.cloudflare.com"]'):
                        self.solve_turnstile(page)
                        time.sleep(4)
                        email_input = page.ele('css:input[name="username"]') or page.ele('css:input[name="email"]')
                
                if not email_input:
                    self.log("❌ 被防爬虫拦截，无法找到输入框。建议手动更新 GitHub Secret 里的 Cookie。")
                    try: page.get_screenshot(path='.', name=f'err_no_input_{index}.png')
                    except: pass
                    res["status"] = "❌ 登录白屏/拦截"
                    return res
                    
                email_input.input(email, clear=True)
                time.sleep(0.5)
                page.ele('css:input[name="password"]').input(password, clear=True)
                time.sleep(1)

                if page.ele('css:iframe[src^="https://challenges.cloudflare.com"]'):
                    self.solve_turnstile(page)

                login_btn = page.ele('xpath://button[contains(text(), "Sign in")]') or page.ele('css:button[type="submit"]')
                if login_btn: login_btn.click()
                else: page.ele('css:input[name="password"]').input('\n')

                for _ in range(15):
                    if "login" not in page.url and "Your Services" in page.html:
                        logged_in = True
                        break
                    time.sleep(1)

                if not logged_in:
                    self.log("❌ 登录失败！可能是密码错误或被拦截。")
                    res["status"] = "❌ 登录失败"
                    return res
                
                self.log("🎉 账密登录成功，自动抓取新 Cookie...")
                if cookie_env:
                    current_cookies = page.cookies()
                    for c in current_cookies:
                        if c.get('name', '').startswith('remember_web_'):
                            target_cookie = {'name': c.get('name'), 'value': c.get('value')}
                            self.update_github_secret(cookie_env, json.dumps([target_cookie]))
                            break

            # --- 步骤 3: 提取信息并续期 ---
            self.log("🔍 提取服务器信息...")
            server_ele = None
            for _ in range(10):
                server_ele = page.ele('xpath://*[contains(text(), "Free Server #")]')
                if server_ele: break
                time.sleep(1)

            if not server_ele:
                self.log("❌ 找不到服务器 (可能是账号暂无可用服务)")
                res["status"] = "❌ 找不到服务器"
                return res
                
            server_match = re.search(r'#(\d{6})', server_ele.text)
            if not server_match:
                res["status"] = "❌ ID解析失败"
                return res
            res["server"] = server_match.group(1)
            res["old_date"] = self.extract_due_date(page)
            
            page.get(f"https://dash.hidencloud.com/service/{res['server']}/manage")
            time.sleep(4) 

            renew_btn = page.ele('xpath://button[contains(., "Renew")]')
            if not renew_btn:
                res["status"] = "❌ 找不到续期按钮"
                return res

            renew_btn.click()
            time.sleep(2)

            page_text = page.html
            if "Renewal Restricted" in page_text or "less than 1 day left" in page_text:
                self.log("⚠️ 收到拦截：暂不能续期")
                res["status"] = "⏭️ 未到期"
                return res
                
            create_btn = page.ele('xpath://button[contains(., "Create Invoice")]')
            if create_btn: 
                create_btn.click()
                for _ in range(15):
                    if "/payment/invoice/" in page.url: break
                    time.sleep(1)
                
                if "/payment/invoice/" in page.url:
                    page.scroll.to_bottom()
                    time.sleep(1)
                    pay_btn = page.ele('xpath://button[contains(., "Pay")]')
                    if pay_btn: 
                        pay_btn.click()
                        for _ in range(15):
                            if "dashboard" in page.url and "Success" in page.html: break
                            time.sleep(1)
                        if "Success" in page.html:
                            time.sleep(2)
                            res["new_date"] = self.extract_due_date(page)
                            res["status"] = "✅ 续期成功"
                        else: res["status"] = "⚠️ 支付状态未知"
                    else: res["status"] = "❌ 支付按钮缺失"
                else: res["status"] = "❌ 账单页超时"
            else: res["status"] = "❌ 弹窗状态未知"

        except Exception as e:
            self.log(f"💥 异常: {e}")
            res["status"] = f"❌ 脚本异常"

        return res

    def run(self):
        if not self.accounts:
            self.send_tg_notification("🚨 HidenCloud\n❌ ACCOUNTS 未配置。")
            return

        co = ChromiumOptions()
        co.set_browser_path('/usr/bin/google-chrome')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-gpu')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--window-size=1920,1080')
        co.headless(False)
        co.set_argument('--disable-blink-features=AutomationControlled')

        page = None
        try:
            page = ChromiumPage(co)
            for i, account in enumerate(self.accounts):
                res = self.process_account(page, account, i)
                icon = "✅" if "成功" in res['status'] else ("⏭️" if "未到期" in res['status'] else "❌")
                line = f"{icon} <b>{res['name']}</b> (<code>#{res['server']}</code>)\n   • 旧到期: {res['old_date']}\n"
                if "成功" in res['status']: line += f"   • 新到期: {res['new_date']}\n"
                line += f"   • 状态: {res['status']}\n"
                self.results.append(line)
                if i < len(self.accounts) - 1: time.sleep(random.randint(3, 6))
        except Exception as e:
            self.results.append(f"❌ 浏览器异常: {e}")
        finally:
            if page: page.quit()
            if self.results: self.send_tg_notification("☁️ <b>HidenCloud 续期报告</b>\n\n" + "\n".join(self.results))

if __name__ == "__main__":
    bot = HidenCloudAutoRenew()
    bot.run()
