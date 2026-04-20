# scripts/hidencloud_renew.py
# -*- coding: utf-8 -*-

import os
import time
import random
import re
import requests
import sys
import traceback
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
        if not self.gh_token or not self.gh_repo:
            self.log("⚠️ 缺少 GH_TOKEN 或 GITHUB_REPOSITORY 环境变量，无法更新 Cookie")
            return False
        if not NACL_AVAILABLE:
            self.log("❌ 缺少 pynacl 库，无法加密 Secret")
            return False
            
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.gh_token}", "X-GitHub-Api-Version": "2022-11-28"}
        try:
            r = requests.get(f"https://api.github.com/repos/{self.gh_repo}/actions/secrets/public-key", headers=headers)
            if r.status_code != 200: 
                self.log(f"❌ 获取仓库公钥失败: {r.text}")
                return False
            key_data = r.json()
            public_key = nacl.public.PublicKey(key_data['key'].encode('utf-8'), nacl.encoding.Base64Encoder())
            sealed_box = nacl.public.SealedBox(public_key)
            encrypted = sealed_box.encrypt(secret_value.encode('utf-8'))
            encrypted_value = b64encode(encrypted).decode('utf-8')
            r_update = requests.put(f"https://api.github.com/repos/{self.gh_repo}/actions/secrets/{secret_name}", headers=headers, json={"encrypted_value": encrypted_value, "key_id": key_data['key_id']})
            if r_update.status_code in [201, 204]:
                self.log(f"🎉 成功将新 Cookie 保存至 Github Secret: [{secret_name}]")
                return True
            else:
                self.log(f"❌ Secret 更新失败 (权限不足?): {r_update.text}")
                return False
        except Exception as e: 
            self.log(f"💥 Secret 更新发生异常: {e}")
            return False
    
    def solve_turnstile(self, page):
        self.log("🛡️ 开始处理 Turnstile...")
        try:
            resp_input = page.ele('css:[name="cf-turnstile-response"]')
            if resp_input and len(resp_input.value) > 10:
                self.log("⚡ Token 已存在，无需重复破解！")
                return True

            target_iframe = page.get_frame('css:iframe[src^="https://challenges.cloudflare.com"]', timeout=8)
            if not target_iframe:
                self.log("⚠️ 未找到 CF iframe")
                return False
            
            time.sleep(2)
            click_success = False
            
            try:
                sr = target_iframe.ele('tag:body').shadow_root
                if sr:
                    target_ele = sr.ele('css:input[type="checkbox"]') or sr.ele('css:div.main-wrapper')
                    if target_ele:
                        self.log("🎯 穿透 ShadowRoot 成功，执行精准点击...")
                        target_ele.click.at(offset_x=10, offset_y=10)
                        click_success = True
            except Exception as e:
                self.log(f"⚠️ ShadowRoot 尝试失败: {e}")

            if not click_success:
                self.log("🏹 执行 iframe 保底盲点...")
                try:
                    target_iframe.frame_ele.click.at(offset_x=25, offset_y=30)
                    click_success = True
                except: pass

            if click_success:
                self.log("⏳ 已经点击验证框，等待 CF 验证结果...")
                for i in range(15):
                    time.sleep(1)
                    resp = page.ele('css:[name="cf-turnstile-response"]')
                    if resp and len(resp.value) > 10:
                        self.log(f"🎉 CF 验证通过！(耗时 {i+1}s)")
                        return True
            
            self.log("❌ CF 验证超时")
            return False
        except Exception as e:
            self.log(f"💥 Turnstile 异常: {e}")
            return False

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
            saved_cookie = os.getenv(cookie_env, '[]')
            logged_in = False
            
            # --- 步骤 1: 尝试 Cookie 登录 ---
            if saved_cookie and saved_cookie != '[]':
                try:
                    page.set.cookies(json.loads(saved_cookie))
                    page.get("https://dash.hidencloud.com/dashboard")
                    time.sleep(3)
                    if "login" not in page.url and "Your Services" in page.html: logged_in = True
                except: pass

            # --- 步骤 2: 降级账号密码登录 ---
            if not logged_in:
                self.log("🔑 退回账号密码登录...")
                page.clear_cache(cookies=True)
                page.get("https://dash.hidencloud.com/auth/login")
                time.sleep(3) 

                email_input = page.ele('css:input[name="username"]') or page.ele('css:input[name="email"]')
                
                if not email_input:
                    self.log("🔍 未找到输入框，检查是否被 CF 前置盾拦截...")
                    if page.ele('css:iframe[src^="https://challenges.cloudflare.com"]'):
                        self.log("🚧 确认为 CF 强力前置盾，尝试破解...")
                        if self.solve_turnstile(page):
                            self.log("✅ 前置盾破除，等待页面重定向...")
                            time.sleep(5)
                            email_input = page.ele('css:input[name="username"]') or page.ele('css:input[name="email"]')
                
                if not email_input:
                    self.log("❌ 依然找不到邮箱输入框，截图留证: err_no_input.png")
                    try: page.get_screenshot(path='.', name=f'err_no_input_{index}.png')
                    except: pass
                    res["status"] = "❌ 登录白屏/拦截"
                    return res
                    
                self.log("⌨️ 正在输入账号和密码...")
                email_input.input(email, clear=True)
                time.sleep(0.5)
                page.ele('css:input[name="password"]').input(password, clear=True)
                time.sleep(1)

                self.log("🛡️ 检测表单上的 Turnstile 验证码...")
                if page.ele('css:iframe[src^="https://challenges.cloudflare.com"]'):
                    if not self.solve_turnstile(page):
                        self.log("❌ 验证码破解失败，截图留证: err_turnstile.png")
                        try: page.get_screenshot(path='.', name=f'err_turnstile_{index}.png')
                        except: pass
                        res["status"] = "❌ 验证码失败"
                        return res

                self.log("🖱️ 点击登录按钮...")
                login_btn = page.ele('xpath://button[contains(text(), "Sign in")]') or page.ele('css:button[type="submit"]')
                if login_btn: login_btn.click()
                else: 
                    self.log("⚠️ 找不到登录按钮，尝试回车提交")
                    page.ele('css:input[name="password"]').input('\n')

                self.log("⏳ 等待页面跳转至 Dashboard...")
                for _ in range(15):
                    if "login" not in page.url and "Your Services" in page.html:
                        logged_in = True
                        break
                    time.sleep(1)

                if not logged_in:
                    self.log("❌ 登录超时或账号密码错误！截图留证: err_login_fail.png")
                    try: page.get_screenshot(path='.', name=f'err_login_fail_{index}.png')
                    except: pass
                    res["status"] = "❌ 登录失败"
                    return res
                
                self.log("🎉 账号密码登录成功！")
                if cookie_env:
                    self.update_github_secret(cookie_env, json.dumps(page.cookies()))

            # --- 步骤 3: 提取信息并跳转 ---
            self.log("🔍 等待提取服务器 ID...")
            server_ele = None
            for _ in range(10): # 增加动态重试等待渲染
                server_ele = page.ele('xpath://*[contains(text(), "Free Server #")]')
                if server_ele: break
                time.sleep(1)

            if not server_ele:
                self.log("❌ 在控制台未找到 'Free Server #' 元素，截图留证: err_no_server.png")
                try: page.get_screenshot(path='.', name=f'err_no_server_{index}.png')
                except: pass
                res["status"] = "❌ 找不到服务器"
                return res
                
            server_text = server_ele.text
            server_match = re.search(r'#(\d{6})', server_text)
            if not server_match:
                self.log(f"❌ Server ID 格式解析失败 (原文: {server_text})")
                res["status"] = "❌ ID解析失败"
                return res
                
            res["server"] = server_match.group(1)
            self.log(f"⚡ 成功提取服务器 ID: #{res['server']}")
            res["old_date"] = self.extract_due_date(page)
            
            manage_url = f"https://dash.hidencloud.com/service/{res['server']}/manage"
            self.log(f"🔗 跳转管理页: {manage_url}")
            page.get(manage_url)
            time.sleep(4) # 给管理页足够的加载时间

            # --- 步骤 4: 续期判断 ---
            self.log("🔍 寻找 Renew 按钮...")
            renew_btn = page.ele('xpath://button[contains(., "Renew")]')
            if not renew_btn:
                self.log("❌ 找不到 Renew 按钮，截图留证: err_no_renew_btn.png")
                try: page.get_screenshot(path='.', name=f'err_no_renew_btn_{index}.png')
                except: pass
                res["status"] = "❌ 找不到续期按钮"
                return res

            self.log("🖱️ 点击 Renew 按钮...")
            renew_btn.click()
            time.sleep(2)

            page_text = page.html
            if "Renewal Restricted" in page_text or "less than 1 day left" in page_text:
                self.log("⚠️ 收到拦截弹窗：离到期超过一天，暂不能续期")
                res["status"] = "⏭️ 离到期超过一天，暂不能续期"
                return res
                
            self.log("✅ 满足续期条件，准备点击 Create Invoice...")
            create_btn = page.ele('xpath://button[contains(., "Create Invoice")]')
            if create_btn: 
                create_btn.click()
                self.log("🖱️ 点击了 Create Invoice 按钮")
            else:
                self.log("❌ 找不到 Create Invoice 按钮，截图留证: err_no_invoice_btn.png")
                try: page.get_screenshot(path='.', name=f'err_no_invoice_btn_{index}.png')
                except: pass
                res["status"] = "❌ 弹窗状态未知"
                return res

            # --- 步骤 5: 支付流程 ---
            self.log("⏳ 等待跳转至账单页...")
            for _ in range(15):
                if "/payment/invoice/" in page.url: break
                time.sleep(1)
                
            if "/payment/invoice/" not in page.url:
                self.log("❌ 账单页跳转超时，截图留证: err_invoice_timeout.png")
                try: page.get_screenshot(path='.', name=f'err_invoice_timeout_{index}.png')
                except: pass
                res["status"] = "❌ 账单页超时"
                return res

            self.log("📜 向下滚动寻找 Pay 按钮...")
            page.scroll.to_bottom()
            time.sleep(1)
            pay_btn = page.ele('xpath://button[contains(., "Pay")]')
            if pay_btn: 
                self.log("🖱️ 点击 Pay 按钮...")
                pay_btn.click()
            else:
                self.log("❌ 找不到 Pay 按钮，截图留证: err_no_pay_btn.png")
                try: page.get_screenshot(path='.', name=f'err_no_pay_btn_{index}.png')
                except: pass
                res["status"] = "❌ 支付按钮缺失"
                return res

            # --- 步骤 6: 验证结果 ---
            self.log("⏳ 等待支付完成，跳转回 Dashboard...")
            for _ in range(15):
                if "dashboard" in page.url and "Success" in page.html: break
                time.sleep(1)

            if "Success" in page.html:
                self.log("🎉 支付成功！")
                time.sleep(2)
                res["new_date"] = self.extract_due_date(page)
                res["status"] = "✅ 续期成功"
            else:
                self.log("⚠️ 未捕获到明确的成功提示")
                try: page.get_screenshot(path='.', name=f'warn_payment_status_{index}.png')
                except: pass
                res["status"] = "⚠️ 支付后状态未知"

        except Exception as e:
            self.log(f"💥 账号处理引发未捕获异常: {e}")
            res["status"] = f"❌ 异常: {str(e)[:20]}"

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
        co.set_argument('--disable-features=IsolateOrigins,site-per-process')
        
        proxy = os.getenv('PROXY')
        if proxy: co.set_argument(f'--proxy-server={proxy}')

        page = None
        try:
            page = ChromiumPage(co)
            for i, account in enumerate(self.accounts):
                res = self.process_account(page, account, i)
                icon = "✅" if "成功" in res['status'] else ("⏭️" if "暂不能续期" in res['status'] else "❌")
                line = f"{icon} <b>{res['name']}</b> (<code>#{res['server']}</code>)\n   • 旧到期: {res['old_date']}\n"
                if "成功" in res['status']: line += f"   • 新到期: {res['new_date']}\n"
                line += f"   • 状态: {res['status']}\n"
                self.results.append(line)
                if i < len(self.accounts) - 1: time.sleep(random.randint(3, 6))
        except Exception as e:
            self.results.append(f"❌ 浏览器引擎崩溃: {e}")
        finally:
            if page: page.quit()
            if self.results: self.send_tg_notification("☁️ <b>HidenCloud 续期报告</b>\n\n" + "\n".join(self.results))

if __name__ == "__main__":
    bot = HidenCloudAutoRenew()
    bot.run()
