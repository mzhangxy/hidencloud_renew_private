# test_cookie_gha.py
import os
import time
import json
from DrissionPage import ChromiumPage, ChromiumOptions

def diagnostic_test():
    # 1. 获取手动填入的 Cookie 值
    raw_cookie_val = os.getenv('HIDENCLOUD_COOKIE_1', '').strip()
    
    if not raw_cookie_val:
        print("❌ 错误：请先在 GitHub Secrets 中配置 HIDENCLOUD_COOKIE_1")
        return

    print(f"🚀 启动 GHA 环境诊断... Cookie 长度: {len(raw_cookie_val)}")

    # 2. 配置浏览器 (针对 GHA 环境)
    co = ChromiumOptions()
    co.set_browser_path('/usr/bin/google-chrome')
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--window-size=1920,1080')
    # 配合外层 xvfb-run，必须关闭无头模式以模拟真实环境
    co.headless(False) 
    
    # 设置代理 (确保 IP 一致性)
    proxy = os.getenv('PROXY')
    if proxy:
        co.set_argument(f'--proxy-server={proxy}')
        print(f"🌐 代理已挂载: {proxy}")

    page = ChromiumPage(co)

    try:
        # 3. 建立域名上下文 (访问 robots.txt 避开后端 Session 分配)
        print("🔗 步骤1: 访问静态文件建立上下文...")
        page.get("https://dash.hidencloud.com/robots.txt")
        time.sleep(2)

        # 4. 强制注入 Cookie (使用 CDP 协议 100% 复刻 Playwright 逻辑)
        # 如果你填入的是 JSON，我们解析它；如果是纯文本，直接作为 Value
        cookie_name = "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d"
        cookie_value = raw_cookie_val
        
        try:
            data = json.loads(raw_cookie_val)
            if isinstance(data, list) and len(data) > 0:
                cookie_name = data[0].get('name', cookie_name)
                cookie_value = data[0].get('value', cookie_value)
        except:
            pass

        print(f"🖱️ 步骤2: 注入核心票据 [{cookie_name}]")
        page.run_cdp('Network.setCookie', 
            name=cookie_name,
            value=cookie_value,
            domain='dash.hidencloud.com',
            path='/',
            secure=True,
            httpOnly=True,
            sameSite='Lax',
            expires=int(time.time()) + 3600 * 24 * 365
        )

        # 5. 尝试进入 Dashboard
        print("🚀 步骤3: 携带 Cookie 访问 Dashboard...")
        page.get("https://dash.hidencloud.com/dashboard")
        time.sleep(5)

        # 6. 结果判定与拍照
        print(f"📊 最终 URL: {page.url}")
        page.get_screenshot(path='.', name='diagnostic_result.png')
        
        if "login" not in page.url and "Your Services" in page.html:
            print("🎉 诊断结果: Cookie 登录成功！代码逻辑无误。")
        else:
            print("❌ 诊断结果: 登录失败，被重定向至登录页。")
            # 额外检查：是否是因为被 Cloudflare 拦截
            if "challenges.cloudflare.com" in page.html:
                print("⚠️ 发现原因: 访问 Dashboard 时触发了 CF 五秒盾拦截。")

    except Exception as e:
        print(f"💥 运行异常: {e}")
    finally:
        page.quit()

if __name__ == "__main__":
    diagnostic_test()
