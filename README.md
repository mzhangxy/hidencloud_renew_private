☁️ HidenCloud 自动续期脚本 (Auto-Renew)本项目是一个基于 DrissionPage 和 GitHub Actions 的 HidenCloud 免费服务器全自动续期工具。它内置了强大的 Cloudflare Turnstile 验证码绕过机制，并结合 Xray-core 本地代理隧道，实现固定节点 IP 登录，最大程度降低风控封号风险。✨ 核心特性🛡️ 无头级过盾： 采用 CDP 底层协议物理点击 + ShadowRoot 穿透技术，在纯 Headless 环境下稳定攻破 CF Turnstile 验证码。🌐 固定 IP 登录： 工作流内置 Xray-core，支持挂载自定义 V2ray 节点，确保每次登录 IP 纯净且一致。👥 多账号并发： 支持通过 JSON 数组配置无数个子账号，脚本将自动循环处理。🍪 智能 Cookie 托管： 优先使用 Cookie 极速登录，登录/续期成功后自动提取新 Cookie 并通过 GitHub API 覆盖更新至 Secrets，减少账密暴露频率。📢 全景 TG 通知： 无论续费成功、处于冷却期还是遇到风控，都会推送排版精美的 Telegram 报告。🛠️ 部署指南第一步：获取必要凭证Telegram 机器人： 在 TG 中向 @BotFather 申请一个 Bot 获取 Token。向 @userinfobot 发送消息获取你的 Chat ID。GitHub PAT (个人访问令牌)： 脚本需要修改仓库的 Secret 以保存 Cookie。请前往 GitHub Settings -> Developer settings -> Personal access tokens (Classic)，生成一个勾选了 repo 权限的 Token。节点配置 (Xray JSON)： 打开电脑上的 V2rayN，选中一个稳定的节点，右键点击 -> “导出所选服务器为客户端配置(json)”。使用记事本打开并复制里面的全部代码。第二步：配置 GitHub Secrets (核心步骤)请进入你的 GitHub 仓库 -> Settings -> Secrets and variables -> Actions -> New repository secret，依次添加以下变量：Secret 名称描述示例 / 说明TG_BOT_TOKENTelegram 机器人 Token123456789:ABCdefGhIJKlmNoPQRsTUVwxyZTG_CHAT_IDTelegram 接收者 ID12345678REPO_TOKENGitHub PAT (拥有 repo 权限)ghp_xxxxxxxxxxxxxxxxxxxxxxXRAY_CONFIG_JSON导出的 V2ray 节点配置直接粘贴导出的完整 JSON 内容ACCOUNTS多账号配置文件必须是严格的 JSON 数组格式（见下方说明）📝 ACCOUNTS 格式规范ACCOUNTS 变量是你所有 HidenCloud 账号的“花名册”。请务必确保它是合法的 JSON 数组，最后一项不能有逗号。JSON[
  {
    "email": "user1@example.com",
    "password": "Password_01",
    "cookie_env": "HIDENCLOUD_COOKIE_1"
  },
  {
    "email": "user2@example.com",
    "password": "Password_02",
    "cookie_env": "HIDENCLOUD_COOKIE_2"
  }
]
email: 登录邮箱。password: 登录密码。cookie_env: 用于存放该账号 Cookie 的 Secret 名称。你可以自己命名（如 COOKIE_A），脚本在首次登录成功后，会自动在仓库 Secrets 中为你创建或更新这个变量，你不需要手动去建。🚀 运行与触发自动运行： 默认配置为每天 UTC 时间凌晨 2:00（北京时间上午 10:00）自动执行一次。你可以修改 .github/workflows/renew.yml 中的 cron 表达式来调整时间。手动运行： 进入仓库的 Actions 选项卡，点击左侧的 HidenCloud Auto Renew，然后点击右侧的 Run workflow 按钮即可立即触发测试。⚠️ 注意事项代理端口： 默认提供的 Xray 配置文件会在 Actions 虚拟机本地开启 10808 端口（Socks5）。如果你的导出的 JSON 里 inbounds 端口不是 10808，请确保同步修改 renew.yml 中的 PROXY 环境变量。账户安全： 请妥善保管你的 REPO_TOKEN 和节点配置。将本仓库设置为 Private (私有) 仓库以防止信息泄露。Created for automated maintenance. Use responsibly.
