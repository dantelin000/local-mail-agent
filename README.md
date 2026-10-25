# Local Mail Agent

**邮件留在本机，报告按你的选择送达。**

用 Apple Mail 管理多个账号，本机 Ollama 模型分类、脱敏和摘要，再生成可以折叠阅读的 HTML 邮件简报。也支持导入 EML/mbox 文件。默认只在本机留档；已读回写、云端汇总和其他投递方式都可以单独选择。

[English](README.en.md) · [配置](docs/configuration.md) · [投递方式](docs/delivery.md) · [安全边界](SECURITY.md) · [开发说明](CONTRIBUTING.md)

> 0.1.0 Alpha。Apple Mail 采集需要 macOS；文件导入、报告和投递支持 macOS/Linux。外部邮箱和 Telegram 账号需自行配置。自动测试使用合成数据和模拟接口。

## 可以选择什么

| 环节 | 选择 | 默认 |
| --- | --- | --- |
| 读取 | Apple Mail 全部已启用账号的收件箱；EML/mbox 文件导入 | Apple Mail |
| 模型 | 在配置中指定已经安装的本地 Ollama 模型与允许列表 | `qwen3.5:9b`；不自动下载 |
| 时间 | 时区、采集间隔、每天处理的小时数、回溯窗口 | UTC；每15分钟；09/21点；24小时 |
| 原邮件 | 保持状态；成功处理、身份核对后标记已读 | 保持状态 |
| 汇总 | 直接编排本机分类摘要；可选 Codex CLI 云端汇总 | 本机编排 |
| 本机留档 | Markdown、HTML、结构化 JSON；自定义归档目录 | 开启 |
| 邮件投递 | SMTP STARTTLS / SSL；固定发件人与收件人 | 关闭 |
| Telegram Bot | 本机直接通过 Bot API 发给指定本人私聊 | 关闭 |
| Telegram Saved Messages | 登录个人 Telegram，发送到自己的收藏夹 | 关闭 |
| SSH 转发 | 经任意已配置 SSH 主机，再选择远端投递出口 | 关闭；不依赖指定云厂商或 Hermes |

多个出口可以同时开启。投递失败不重新分类；每份报告、每个出口分别记录结果。结果不确定时暂停重发，避免制造重复消息。没有自动回复、删除或移动原邮件功能。

## 安装与离线预览

需要 Python 3.11+。进入下载的源码目录：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
local-mail-agent init --source eml
local-mail-agent demo
```

最后一条命令输出合成演示 HTML 的本机路径。**演示不读取邮箱、不调用模型、不访问网络，也不会推送报告。**

配置默认位于 `~/.config/local-mail-agent/config.toml`，可以通过 `--config` 指定另一份。数据默认在 macOS 的 `~/Library/Application Support/LocalMailAgent/` 或 Linux 的 `~/.local/share/local-mail-agent/`；不写入源码目录。

## 连接 Apple Mail

1. 先在 Apple Mail 中登录并同步自己的邮箱账号。
2. 安装 Xcode Command Line Tools、Ollama，并自行准备所需本地模型。
3. 新建配置，例如：

```sh
local-mail-agent --config ~/.config/local-mail-agent/apple.toml init --timezone Asia/Shanghai
local-mail-agent --config ~/.config/local-mail-agent/apple.toml doctor
local-mail-agent --config ~/.config/local-mail-agent/apple.toml apple-mail build
local-mail-agent --config ~/.config/local-mail-agent/apple.toml apple-mail start
```

首次启动，由 macOS 询问是否允许这个 App 访问 Mail。通过原生 App 启动可让系统正确识别授权来源。默认看板是 `http://127.0.0.1:8766/`；只监听本机。App 持续运行时执行采集和定时报告，电脑休眠会延后处理。没有自动安装登录启动项。

采集按接收时间限制在配置窗口内，并按原生标识和规范化内容去重。默认只读；要在处理成功后设为已读，请明确设置 `source.mark_read=true`。回写前核对身份和完整 MIME 正文；只容忍投递头等非内容变化。身份变化的邮件进入待复核，不强制修改。

## 使用导出邮件

```sh
local-mail-agent --config ./config.toml init --source eml
local-mail-agent --config ./config.toml import /path/to/message.eml
local-mail-agent --config ./config.toml report
local-mail-agent --config ./config.toml serve
```

导入只加入本机队列；`report` 才调用已配置的本地模型。EML/mbox 模式不修改原始文件。超过回溯窗口的邮件会跳过；需要导入旧邮件时调整 `source.window_hours`（1至168小时）。持续定时处理使用 `run`。文件导入只分析可解析文字正文，不分析附件。

## 更换报告出口

编辑配置中的 `[[delivery]]`。启用一个外部出口，需要将该出口 `enabled=true`，并明确设置：

```toml
[privacy]
allow_external_delivery = true
```

也可以用 `init --delivery telegram-bot --allow-external` 创建一份待配置凭据的新文件。初始化本身不会启动任务或发送报告。

- 只在本机：保持默认配置即可。
- 邮件发送：见 [SMTP 示例](examples/email.toml)。程序发送的是简报，不是代你回复原邮件。
- 本机 Telegram Bot：见 [Bot 示例](examples/telegram-bot.toml)，无需服务器。
- 个人 Telegram 收藏夹：安装 `python -m pip install '.[telegram]'`，使用 [Saved Messages 示例](examples/telegram-saved.toml)，然后运行 `telegram-login`。只发送到 `me`。
- 自有服务器：见 [SSH 示例](examples/ssh.toml)，远端接收命令为 `relay --channel <出口ID>`。

凭据用环境变量名称引用；可选 `.[keychain]` 扩展及 `local-mail-agent secret set VARIABLE_NAME` 可写入系统钥匙串。不要把密码、Token、API hash 或 `.session` 写进源码、提交记录或公开问题。

修改配置后重启运行中的进程。报告无外部图片、字体和脚本；资讯按主题折叠，展开可读逐封摘要。自己的收件邮箱地址可通过 `privacy.show_own_addresses` 选择显示，且不加入云端汇总输入。

## 可选云端汇总

配置 `summary.provider="codex"` 并开启 `privacy.allow_cloud_summary=true`。需自行安装、登录兼容版本的 Codex CLI。本版本的额外文件访问限制依赖 macOS `sandbox-exec`；其他系统请使用默认本机编排。

仅通过检查的脱敏摘要进入 CLI。模型工具、浏览器、插件、记忆及 shell 功能关闭；CLI 不读取邮件库。它仍使用用户自己的正常登录状态，不复制认证文件。接口依据：[官方 Codex CLI 命令文档](https://learn.chatgpt.com/docs/developer-commands?surface=cli)。

## 隐私与检查

仓库从允许列入的源码文件重新建立，没有复制运行目录、真实报告、数据库、邮件样本、Telegram 会话或原项目 Git 历史。源码、提交历史、源码包和 wheel 可以使用同一个脚本检查：

```sh
python scripts/audit_release.py --history
python scripts/audit_release.py --history dist/*.whl dist/*.tar.gz
```

检测只输出文件位置和问题类型，不回显匹配的秘密。它检查常见密钥、个人绝对路径、非示例邮箱、实际主机引用和误打包的运行文件；属于辅助发布检查，仍需人工复核。详见 [SECURITY.md](SECURITY.md)。

## 测试与打包

```sh
python -m pip install -e '.[dev,telegram]'
python -W error::ResourceWarning -m unittest discover -s tests -v
python -m build
python scripts/normalize_archives.py dist/*.tar.gz
```

原生身份核对测试仅在 macOS 编译执行，不访问真实邮箱。CI 在 macOS/Linux 运行测试。MIT 许可证；模型权重、Ollama、第三方客户端及任何个人账号数据均不包含在分发包内。
