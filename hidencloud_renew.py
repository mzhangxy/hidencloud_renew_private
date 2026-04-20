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

# 尝试导入加密库，用于更新 GitHub Secrets
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
        
        # 解析多账号配置
        accounts_str = os.getenv('ACCOUNTS', '[]')
        try:
            self.accounts = json.loads(accounts_str)
        except Exception as e:
            self.log(f"❌ ACCOUNTS 环境变量解析失败: {e}")
            self.accounts = []
            
        self.results = []

    def log(self, msg):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        sys.stdout.flush()

    def send_tg_notification(self, message):
        if not self.tg_token or not self.tg_chat_id:
            self.log("⚠️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过通知")
            return
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {"chat_id": self.tg_chat_id, "text": message, "parse_mode": "HTML"}
            requests.post(url, json=payload, timeout=10)
            self.log("📤 TG 通知已发送")
        except Exception as e:
            self.log(f"❌ TG 发送失败: {e}")

    def update_github_secret(self, secret_name, secret_value):
        if not self.gh_token or not self.gh_repo or not NACL_AVAILABLE:
            self.log(f"⚠️ 缺少 GH_TOKEN/REPO 或 nacl 库，无法更新 Secret [{secret_name}]")
            return False

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.gh_token}",
            "X-GitHub-Api-Version": "2022-11-28"
        }

        try:
            pub_key_url = f"https://api.github.com/repos/{self.gh_repo}/actions/secrets/public-key"
            r = requests.get(pub_key_url, headers=headers)
            if r.status_code != 200:
                self.log(f"❌ 获取仓库公钥失败: {r.text}")
                return False
            key_data = r.json()

            public_key = nacl.public.PublicKey(key_data['key'].encode('utf-8'), nacl.encoding.Base64Encoder())
            sealed_box = nacl.public.SealedBox(public_key)
            encrypted = sealed_box.encrypt(secret_value.encode('utf-8'))
            encrypted_value = b64encode(encrypted).decode('utf-8')

            update_url = f"https://api.github.com/repos/{self.gh_repo}/actions/secrets/{secret_name}"
            payload = {"encrypted_value": encrypted_value, "key_id": key_data['key_id']}
            r_update = requests.put(update_url, headers=headers, json=payload)
            
            if r_update.status_code in [201, 204]:
                self.log(f"🎉 成功更新 GitHub Secret: {secret_name}")
                return True
            else:
                self.log(f"❌ GitHub Secret 更新失败: {r_update.text}")
                return False
        except Exception as e:
            self.log(f"💥 更新 GitHub Secret 发生异常: {e}")
            return False

    def solve_turnstile(self, page):
        """核心处理 CF Turnstile 的逻辑 (穿透 ShadowRoot + CDP 点击)"""
        self.log("🛡️ 检测到 Turnstile，开始处理...")
        try:
            # 1. 检查是否已自动通过
            resp_input = page.ele('css:[name="cf-turnstile-response"]')
            if resp_input and resp_input.value:
                self.log("⚡ Token 已存在，自动通过！")
                return True

            # 2. 定位 iframe
            self.log("🔍 寻找 Turnstile iframe...")
            target_iframe = page.get_frame('css:iframe[src^="https://challenges.cloudflare.com"]', timeout=8)
            if not target_iframe:
                self.log("❌ 找不到 iframe")
                return False

            time.sleep(2)
            click_success = False
            
            # 3. 穿透 Closed Shadow Root
            try:
                iframe_body = target_iframe.ele('tag:body')
                sr = iframe_body.shadow_root
                if sr:
                    self.log("🔓 成功进入 Shadow Root")
                    target_ele = sr.ele('css:input[type="checkbox"]') or sr.ele('css:div.main-wrapper')
                    if target_ele:
                        self.log("🖱️ 执行精确 CDP 点击...")
                        target_ele.click.at(offset_x=10, offset_y=10)
                        click_success = True
            except Exception as e:
                self.log(f"⚠️ 穿透尝试失败: {e}")

            # 4. 保底盲点
            if not click_success:
                self.log("🏹 执行 iframe 保底盲点...")
                try:
                    target_iframe.frame_ele.click.at(offset_x=25, offset_y=30)
                    click_success = True
                except Exception as e:
                    self.log(f"❌ 盲点失败: {e}")

            # 5. 验证结果
            if click_success:
                self.log("⏳ 等待验证通过...")
                for _ in range(15):
                    time.sleep(1)
                    resp = page.ele('css:[name="cf-turnstile-response"]')
                    if resp and resp.value:
                        self.log("🎉 过盾成功！")
                        return True
            return False
        except Exception as e:
            self.log(f"🔥 Turnstile 处理异常: {e}")
            return False

    def extract_due_date(self, page):
        """提取页面中的 Due date"""
        try:
            # 匹配形如 "26 Apr 2026" 或 "26 Apr 2026 14:00" 的日期
            date_match = re.search(r'(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})', page.html)
            if date_match:
                return date_match.group(1)
            return "未知日期"
        except:
            return "未知日期"

    def process_account(self, page, account, index):
        email = account.get('email', '')
        password = account.get('password', '')
        cookie_env = account.get('cookie_env', '')
        display_name = email.split('@')[0] if '@' in email else f"账号{index+1}"
        
        result_msg = {"name": display_name, "server": "?", "old_date": "?", "new_date": "?", "status": ""}
        self.log(f"\n{'='*50}\n🚀 开始处理账号 [{index+1}/{len(self.accounts)}]: {display_name}")

        if not email or not password:
            self.log("❌ 账号配置缺失邮箱或密码")
            result_msg["status"] = "❌ 配置错误"
            return result_msg

        try:
            # --- 步骤 1: 尝试 Cookie 登录 ---
            page.clear_cache(cookies=True)
            saved_cookie_str = os.getenv(cookie_env, '[]')
            logged_in = False
            
            if saved_cookie_str and saved_cookie_str != '[]':
                self.log("🍪 尝试 Cookie 登录...")
                try:
                    cookies = json.loads(saved_cookie_str)
                    page.set.cookies(cookies)
                    page.get("https://dash.hidencloud.com/dashboard")
                    time.sleep(3)
                    if "login" not in page.url and "Your Services" in page.html:
                        self.log("✅ Cookie 登录成功")
                        logged_in = True
                except:
                    pass

            # --- 步骤 2: 降级账号密码登录 ---
            if not logged_in:
                self.log("🔑 退回账号密码登录...")
                page.clear_cache(cookies=True)
                page.get("https://dash.hidencloud.com/auth/login")
                
                email_input = page.ele('css:input[name="username"]') or page.ele('css:input[name="email"]')
                if not email_input:
                    self.log("❌ 无法找到邮箱输入框，页面可能加载失败")
                    result_msg["status"] = "❌ 登录白屏/超时"
                    return result_msg
                    
                email_input.input(email, clear=True)
                time.sleep(0.5)
                page.ele('css:input[name="password"]').input(password, clear=True)
                time.sleep(1)

                # 处理可能存在的 Turnstile
                if page.ele('css:iframe[src^="https://challenges.cloudflare.com"]'):
                    if not self.solve_turnstile(page):
                        self.log("❌ 登录界面的 Turnstile 验证失败")
                        result_msg["status"] = "❌ 验证码失败"
                        return result_msg

                # 点击登录
                login_btn = page.ele('xpath://button[contains(text(), "Sign in")]')
                if login_btn:
                    login_btn.click()
                else:
                    page.ele('css:input[name="password"]').input('\n')

                self.log("⏳ 等待登录跳转...")
                for _ in range(15):
                    if "login" not in page.url and "Your Services" in page.html:
                        logged_in = True
                        break
                    time.sleep(1)

                if not logged_in:
                    self.log("❌ 登录超时或失败")
                    result_msg["status"] = "❌ 登录失败"
                    return result_msg
                
                self.log("✅ 账密登录成功")
                
                # 保存新 Cookie
                if cookie_env:
                    current_cookies = page.cookies()
                    if current_cookies:
                        self.log(f"🔄 正在保存新 Cookie 到 {cookie_env}...")
                        self.update_github_secret(cookie_env, json.dumps(current_cookies))

            # --- 步骤 3: 提取 Server ID 并跳转管理页 ---
            self.log("🔍 寻找服务器实例...")
            server_match = re.search(r'Free Server\s+#(\d{6})', page.html)
            if not server_match:
                self.log("❌ 未找到 Free Server 实例")
                result_msg["status"] = "❌ 找不到服务器"
                return result_msg
                
            server_id = server_match.group(1)
            result_msg["server"] = server_id
            self.log(f"⚡ 找到服务器: #{server_id}")

            # 提取表格中当前的 Due date (Dashboard 页面)
            result_msg["old_date"] = self.extract_due_date(page)
            
            manage_url = f"https://dash.hidencloud.com/service/{server_id}/manage"
            self.log(f"🔗 跳转管理页: {manage_url}")
            page.get(manage_url)
            time.sleep(3)

            # --- 步骤 4: 点击 Renew 并处理弹窗 ---
            renew_btn = page.ele('xpath://button[contains(., "Renew")]')
            if not renew_btn:
                self.log("❌ 未找到 Renew 按钮")
                result_msg["status"] = "❌ 找不到续期按钮"
                return result_msg

            self.log("🖱️ 点击 Renew 按钮...")
            renew_btn.click()
            time.sleep(2)

            page_text = page.html
            if "Renewal Restricted" in page_text or "less than 1 day left" in page_text:
                self.log("⚠️ 收到拦截弹窗：未到续期时间")
                result_msg["status"] = "⏭️ 离到期超过一天，暂不能续期"
                return result_msg
                
            if "Renew Plan" in page_text or page.ele('xpath://button[contains(., "Create Invoice")]'):
                self.log("✅ 满足续期条件，准备生成账单...")
                create_btn = page.ele('xpath://button[contains(., "Create Invoice")]')
                if create_btn:
                    create_btn.click()
                else:
                    self.log("❌ 找不到 Create Invoice 按钮")
                    result_msg["status"] = "❌ 账单创建异常"
                    return result_msg
            else:
                self.log("❓ 未知弹窗状态")
                result_msg["status"] = "❌ 弹窗状态未知"
                return result_msg

            # --- 步骤 5: 支付流程 ---
            self.log("⏳ 等待跳转至账单页...")
            for _ in range(15):
                if "/payment/invoice/" in page.url:
                    break
                time.sleep(1)
                
            if "/payment/invoice/" not in page.url:
                self.log("❌ 跳转账单页超时")
                result_msg["status"] = "❌ 账单页超时"
                return result_msg

            page.scroll.to_bottom()
            time.sleep(1)
            pay_btn = page.ele('xpath://button[contains(., "Pay")]')
            if not pay_btn:
                self.log("❌ 找不到 Pay 按钮")
                result_msg["status"] = "❌ 支付按钮缺失"
                return result_msg

            self.log("🖱️ 点击 Pay 按钮...")
            pay_btn.click()

            # --- 步骤 6: 验证结果提取新日期 ---
            self.log("⏳ 等待支付完成并跳回首页...")
            for _ in range(15):
                if "dashboard" in page.url and "Success" in page.html:
                    break
                time.sleep(1)

            if "Success" in page.html:
                self.log("🎉 支付成功提示已出现！")
                time.sleep(2) # 等待列表渲染更新
                result_msg["new_date"] = self.extract_due_date(page)
                result_msg["status"] = "✅ 续期成功"
            else:
                self.log("⚠️ 未捕获到明确的成功提示")
                result_msg["status"] = "⚠️ 支付后状态未知"

        except Exception as e:
            self.log(f"💥 账号处理崩溃: {e}")
            traceback.print_exc()
            result_msg["status"] = f"❌ 脚本异常: {str(e)[:20]}"

        return result_msg

    def run(self):
        self.log("🚀 启动 HidenCloud 自动续期流程...")
        if not self.accounts:
            self.log("❌ 致命错误: 未检测到 ACCOUNTS 配置！程序退出。")
            self.send_tg_notification("🚨 <b>HidenCloud 续期失败</b>\n❌ ACCOUNTS 环境变量未配置。")
            return

        # 配置浏览器环境
        co = ChromiumOptions()
        co.set_browser_path('/usr/bin/google-chrome')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-gpu')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--window-size=1280,1024')
        co.headless(True)
        
        # 加载本地 Xray
        proxy = os.getenv('PROXY')
        if proxy:
            co.set_argument(f'--proxy-server={proxy}')
            self.log(f"🌐 已配置并启用本地代理: {proxy}")

        page = None
    
        try:
            page = ChromiumPage(co)
            for i, account in enumerate(self.accounts):
                res = self.process_account(page, account, i)
                
                # 格式化当前账号的通知行
                icon = "✅" if "成功" in res['status'] else ("⏭️" if "暂不能续期" in res['status'] else "❌")
                line = f"{icon} <b>{res['name']}</b> (<code>#{res['server']}</code>)\n"
                line += f"   • 旧到期日: {res['old_date']}\n"
                if "成功" in res['status']:
                    line += f"   • 新到期日: {res['new_date']}\n"
                line += f"   • 状态: {res['status']}\n"
                self.results.append(line)
                
                if i < len(self.accounts) - 1:
                    time.sleep(random.randint(3, 6))

        except Exception as e:
            self.log(f"💥 全局浏览器崩溃: {e}")
            self.results.append(f"❌ 浏览器引擎崩溃: {e}")
        finally:
            if page:
                page.quit()
            
            # 发送全局报告
            if self.results:
                msg = "☁️ <b>HidenCloud 续期报告</b>\n\n" + "\n".join(self.results)
                self.send_tg_notification(msg)


if __name__ == "__main__":
    bot = HidenCloudAutoRenew()
    bot.run()
