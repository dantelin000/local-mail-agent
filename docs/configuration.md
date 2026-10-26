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
| `privacy.raw_retention_hours` | queue原文目标保留期1至8760小时，默认72；仅清理去重迁移完成、分类完成且已读回写成功或未开启的原文；未完成任务可超期保留 |
| `delivery` | 一个或多个出口，各有唯一id、type和enabled |

`local`不是另一个云端模型；它直接组织本机分类结果，避免额外模型调用。模型不能确定、输入不完整或附件未分析的邮件留待本机复核。

敏感内容保护不是可关闭的垃圾邮件过滤器。验证码、重置内容、敏感个人信息和疑似指令注入默认留本机。报告只列这类邮件的数量，不把其标题、正文或原始链接交给外部出口。金额与长数字也采用保守脱敏规则。

回溯窗口只限制采集和处理范围。采集、导入和已读回写结束时均检查queue原文清理；未完成分类、去重迁移或已读回写的原文不会因超过 `privacy.raw_retention_hours` 被删除。分类及回写满足清理条件后可以提前删除。报告、投递记录和备份仍需要由用户管理保留周期。

collector数据库的每次连接均开启SQLite安全删除。新建数据库启用增量回收，每次清理提交后最多回收64页；旧库保持现有auto_vacuum模式，增量回收可能无效，但安全删除仍生效。不在采集线程执行全库 `VACUUM`，避免长时间独占写锁；既有备份及启用安全删除前的残留不在此保证范围内。

例外显示只针对本人收件邮箱：根据Apple Mail账号配置及收件头在本机恢复，且不进入Codex输入。文件导入没有账号地址映射，因而不会猜测本人地址。

持续运行使用 `run` 或 macOS原生App；没有安装cron或开机任务。休眠恢复后处理最近一个应执行批次，并使用当前回溯窗口，不保证补齐长期离线期间的每一期旧报告。

外发开关不会删除待发队列。暂停外发后报告继续保存在本机；重新开启时，先前排队但未发送的报告仍可能发送。每个出口的目的地随排队配置冻结；只有pending/retryable记录可以因配置或内容变化而被替换。sent、uncertain、rejected、cancelled以及正在发送的记录均不会自动重新入队。Telegram Bot队列只保存解析后token和chat id的SHA-256指纹；凭据值变化或旧记录缺少指纹时取消任务，历史扫描也不会重新绑定该报告。

非归档出口的启用基线在首次观察时冻结，取当前计划时段的起点（本地时区最近一个不晚于当前时间的 `schedule.hours` 时刻）。直接生成报告时，再取该起点与首份报告slot的较早者，因此跨分钟完成或在生成期间被扫描到的首份报告仍可入队。扫描已有报告时不使用历史slot回拨基线，只接受首次观察的当前时段及之后的报告；已有基线不会随配置变化或再次扫描而回拨。归档出口保留历史归档行为。
