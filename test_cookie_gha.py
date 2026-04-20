# test_cookie_gha.py
import os
import time
import json
from DrissionPage import ChromiumPage, ChromiumOptions

def diagnostic_test():
    raw_cookie_val = os.getenv('HIDENCLOUD_COOKIE_1', '').strip()
    
    if not raw_cookie_val:
        print("❌ 错误：请先在 GitHub Secrets 中配置 HIDENCLOUD_COOKIE_1")
        return

    print(f"🚀 启动无代理裸连诊断... Cookie 长度: {len(raw_cookie_val)}")

    co = ChromiumOptions()
    co.set_browser_path('/usr/bin/google-chrome')
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--window-size=1920,1080')
    co.headless(False) 
    
    # 彻底移除了 Proxy 配置

    page = ChromiumPage(co)

    try:
        print("🔗 步骤1: 访问静态文件建立上下文 (裸连请求)...")
        page.get("https://dash.hidencloud.com/robots.txt")
        time.sleep(2)

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

        print("🚀 步骤3: 携带 Cookie 访问 Dashboard...")
        page.get("https://dash.hidencloud.com/dashboard")
        time.sleep(5)

        print(f"📊 最终 URL: {page.url}")
        page.get_screenshot(path='.', name='diagnostic_result_noproxy.png')
        
        if "login" not in page.url and "Your Services" in page.html:
            print("🎉 诊断结果: 裸连 Cookie 登录大成功！代理完全是多余的！")
        else:
            print("❌ 诊断结果: 登录失败，请查看下载的截图。")

    except Exception as e:
        print(f"💥 运行异常: {e}")
    finally:
        page.quit()

if __name__ == "__main__":
    diagnostic_test()
