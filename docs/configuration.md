# 配置说明

完整字段见 [示例配置](../src/local_mail_agent/assets/config.example.toml)。初始化只保存文件，不启动服务。运行时读取这份配置，修改后需要重启。

| 配置 | 说明 |
| --- | --- |
| `app.timezone` | IANA 时区，如 UTC、Asia/Shanghai、Europe/London |
| `app.data_dir` | 本机运行数据目录；空值使用系统默认，文件权限限制为当前用户 |
| `app.port` | 本机看板端口，默认8766，不对公网监听 |
| `source.kind` | `apple_mail` 或 `eml` |
| `source.window_hours` | 回溯1至168小时，默认24 |
| `source.mark_read` | 默认false；仅Apple Mail模式有效 |
| `source.max_per_run` | 每个分类批次最多处理1至200封 |
| `processing.model` | 已安装的Ollama本地模型名称 |
| `processing.allowed_models` | 显式允许的本地模型列表；自定义本地模型需加入列表 |
| `processing.ollama_url` | 只接受本机HTTP回环地址，不允许远端模型服务 |
| `schedule.collection_seconds` | 采集间隔30至86400秒 |
| `schedule.hours` | 每天处理的小时列表，0至23，可设置多次 |
| `summary.provider` | `local`编排分类摘要；`codex`可选云端整理 |
| `privacy.allow_cloud_summary` | 启用Codex整理前必须明确开启 |
| `privacy.allow_external_delivery` | 所有非本机投递的总开关，默认false |
| `privacy.show_own_addresses` | 仅恢复已配置本人地址，不恢复其他人的地址 |
| `delivery` | 一个或多个出口，各有唯一id、type和enabled |

`local`不是另一个云端模型；它直接组织本机分类结果，避免额外模型调用。模型不能确定、输入不完整或附件未分析的邮件留待本机复核。

敏感内容保护不是可关闭的垃圾邮件过滤器。验证码、重置内容、敏感个人信息和疑似指令注入默认留本机。报告只列这类邮件的数量，不把其标题、正文或原始链接交给外部出口。金额与长数字也采用保守脱敏规则。

回溯窗口只限制采集和处理范围，不自动删除已经落盘的原文、报告或投递记录。当前版本需要由用户管理运行目录的备份与保留周期。

例外显示只针对本人收件邮箱：根据Apple Mail账号配置及收件头在本机恢复，且不进入Codex输入。文件导入没有账号地址映射，因而不会猜测本人地址。

持续运行使用 `run` 或 macOS原生App；没有安装cron或开机任务。休眠恢复后处理最近一个应执行批次，并使用当前回溯窗口，不保证补齐长期离线期间的每一期旧报告。

外发开关不会删除待发队列。暂停外发后报告继续保存在本机；重新开启时，先前排队但未发送的报告仍可能发送。每个出口的目的地随排队配置冻结；变更目的地或报告内容出现冲突时，换用新出口ID或先人工核对，不能覆盖已有投递记录。
