# 三种报告样板

三种版面使用同一份通过检查的脱敏摘要，不改变分类、原文保留、已读回写或投递权限。
包内的[对比入口](../examples/reports/index.html)采用虚构邮件，可以离线打开。

| 配置值 | 版面 | 适合 |
| --- | --- | --- |
| `reading` | 彩色阅读版，购物、投资、房产、技术与游戏按主题折叠；可调字号和外观 | 日常阅读、手机浏览；默认 |
| `compact` | 紧凑速览版，一行主题配标签；需关注的邮件默认展开，其余逐封展开 | 快速扫读较多邮件 |
| `print` | 正式打印版，完整摘要与编排报告全部展开，设置A4打印样式 | 留档、浏览器打印或另存PDF |

## 设定默认版面

编辑配置文件并重启运行中的进程：

```toml
[report]
template = "reading" # reading / compact / print
```

旧配置没有 `[report]` 时自动采用 `reading`。未知名称会报错，不会静默切换。
初始化新配置时也可使用 `local-mail-agent init --template compact`。
`doctor` 的 `report_template` 字段可确认当前配置。

## 离线比较

```sh
local-mail-agent demo --all-templates --output-dir ./template-previews
local-mail-agent demo --template compact --output-dir ./compact-preview
```

第一条命令输出 `index.html` 路径，分别链接三种HTML。示例日期固定为2030年1月2日，
且标明虚构演示。命令不读取邮箱、不调用模型、不访问网络，也不会创建采集或投递数据库。
每份演示同时保存JSON、Markdown与HTML，方便复核和重新排版。

## 只改变已有报告的版面

```sh
local-mail-agent render /path/to/sanitized-report.json --template print --output ./report-print.html
```

输入必须是程序生成、通过相同隐私检查的结构化JSON，不接受原邮件或任意HTML。
命令不修改输入文件、原报告、分类记录或发送队列，只写出指定HTML。
省略 `--template` 则使用输入报告保存的版面。它不会将重新排版的文件自动推送。

## 出口与兼容

本机归档、邮件HTML附件、Telegram Bot、个人收藏夹和SSH转发均使用报告里固定的模板。
配置选择只影响随后生成的报告，已排队的报告保留原版面。
已发送、结果不确定、拒绝、取消和正在发送的记录不会因为切换样板而自动重发。
重新排版本身不会重新处理邮件或设置已读。

新报告JSON的 `version=2` 并包含 `template` 字段；程序继续接受旧的 `version=1` 报告，
使用旧渲染器保持其原始版面。SSH转发需将本机和远端一起升级至0.2.0或更高的兼容版本；
旧版接收端不识别新字段，会拒绝新报告。远端自己的模板配置不覆盖来件已固定的选择。

所有动态文字均转义，无远程图片、字体或脚本。阅读版只运行固定且有CSP哈希校验的
字号、外观、折叠控制脚本；速览版和打印版完全不使用JavaScript。
本人收件邮箱仍由 `privacy.show_own_addresses` 控制；模板选项不能放宽脱敏规则。
打印完整内容请用 `print`，避免浏览器遗漏折叠内容。手机上的Telegram预览器可能只显示源码，
应先下载HTML，再用支持本地HTML的浏览器打开；版面选择不改变客户端对附件的支持情况。
