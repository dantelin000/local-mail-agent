# 报告出口

所有出口共享同一个已脱敏报告结构；正文来源是通过本机检查的摘要，不含原始邮件和附件。Markdown、HTML和JSON先保存在本机，然后每个已启用出口独立投递。新增出口无需改邮件读取或分类逻辑。

## 本机留档

`type="archive"`。相对目录以私有数据目录为基准，也可指定本地绝对目录。每期保存三种格式。报告自身始终保存在 `reports/`，所以关闭额外archive出口也不会丢失原始报告。

## 邮件

`type="email"`。固定SMTP主机、发件人和收件人，支持STARTTLS或SSL。密码通过 `password_env` 引用。收件人不会从邮件正文读取，也不自动回复源邮件。报告作为HTML附件和文字内容投递。

程序仅在服务器确认接受后记为sent。发送过程中超时会进入uncertain；部分收件人已接受时也不整批重发。稳定Message-ID帮助追溯，但SMTP不提供严格的恰好一次语义。实现依据：[Python smtplib](https://docs.python.org/3/library/smtplib.html)。

## 本机 Telegram Bot

`type="telegram_bot"`。自己创建机器人，并先与其建立私聊。将Bot Token和本人chat ID放入配置所引用的环境变量或钥匙串。每次先确认固定目标是本人指定的私聊，再使用 [sendDocument](https://core.telegram.org/bots/api#senddocument) 发送“简短预览＋完整HTML文件”。不调用getUpdates，不需要服务器，也不会干扰已有Bot轮询程序。

文件需要正常下载阅读，因此未启用阻止保存的protect_content。请根据自己的需求保护Telegram账号和设备。机器人私聊不是端到端加密的秘密聊天。

## Telegram Saved Messages

`type="telegram_saved"`。这是个人账号客户端模式，与Bot模式分开。

1. 从 [Telegram API development tools](https://core.telegram.org/api/obtaining_api_id) 获取自己的API ID/hash。
2. 安装 `python -m pip install '.[telegram]'`。
3. 按 [示例](../examples/telegram-saved.toml) 配置凭据引用；可用 `secret set` 将值写入系统钥匙串。
4. 运行 `local-mail-agent --config FILE telegram-login`，在本机终端输入手机号、登录码和必要的两步验证密码。
5. 成功后只保存私有目录中的session；登录本身不发送报告。后续投递只发给 `me`。

程序关闭接收更新和实体缓存，不读取其他聊天。需要注意：session本身具有账号访问能力，文件权限不等于内容加密；切勿分享或提交，可在Telegram设备管理中撤销此会话。参考：[Telethon登录](https://docs.telethon.dev/en/stable/basic/signing-in.html)、[session文件](https://docs.telethon.dev/en/stable/concepts/sessions.html)。

## SSH 转发

`type="ssh"`，使用现有SSH配置和主机指纹验证。发送的是JSON报告，不包含本机账号凭据。主机和远端参数来自受信任的配置；不会执行邮件提供的命令。

远端同样安装此程序，并配置一个非SSH、非archive的出口。命令示例：

```toml
[[delivery]]
id = "relay"
type = "ssh"
enabled = true
host = "mail-relay.example.test"
command = ["local-mail-agent", "--config", "/etc/local-mail-agent/relay.toml", "relay", "--channel", "telegram-bot"]
timeout = 90
```

远端配置也必须明确允许外发。远端只接收规定字段的脱敏报告，保留受权限保护的报告投递包和状态，便于断线后核对；不接收原始邮箱库。用只服务此用途的SSH账号是可选的部署加固，程序不修改SSH服务或防火墙。

## 状态与扩展

`pending → sending → sent`。明确暂时失败进入 `retryable`，逐步延长间隔，最多8次；不确定是否已送达则进入 `uncertain`，停止重发。当前版本保留待核记录，通过 `status` 查看，不提供强制重发不确定消息的快捷操作。

开发新出口：在 `delivery.py` 的DRIVERS注册一个 `(report, channel) -> {message_id}` 函数，并补充配置允许字段和测试。报告字段必须先通过 `reports.validate`；异常只返回固定的无隐私原因代码。不可把API异常原文或Token写进日志。
