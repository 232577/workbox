# WorkBox 双设备协作

设备 A 负责开发、集成与发布协调，设备 B 参与开发并复验交接。代码通过本仓库的 Git 分支、提交和 PR 合并；Concord 负责认领、消息及交接，不同步工作箱业务库和客户文件。

## 接入范围

- 使用官方 npm 包 `@concord-ai/concord-mcp`。本次配置核对的是 0.22.0，要求 Node.js >=22.12；Codex 适配器版本门槛为 0.147.0，版本满足不代表消息桥接已可用。
- 在实际 Git 仓库中运行 `concord setup`。若已有 MCP/Hook，先备份并合并；需要仅在本项目启用时，使用 `setup --no-mcp --no-adapters`，按 Codex 官方配置规则将 MCP 和生命周期 Hook 放入项目 `.codex/config.toml`，避免安装器添加全局客户端配置。
- Cloud 登录使用官方 `concord cloud login`，两台设备各自登录同一账号。登录是整机开关，不是单项目开关；MCP 和 Hook 的加载范围仍可限制在本项目。凭据只保存在各自用户目录，不能提交到 Git 或发给另一台设备。
- 两端 `origin` 应指向 `232577/workbox`，Cloud 项目标识应为 `github.com/232577/workbox`。SSH 和 HTTPS 形式可规范为同一项目。`cloud status --json` 的 `linked`、`apiCompatible`、`keyAccepted` 应全部为 true，再通过 `inspect_work` 检查实际会话。
- 每个会话使用真实 Codex session 派生的身份。用任务标题或进展注明设备 A/B，不将一个固定 agent_id 复制给所有会话，不冒用另一端身份确认消息。

## 每项开发

1. 检查 Git 工作区和最新主分支，保留未提交内容；通过 `inspect_work` 阅读当前任务与文件范围。
2. 用稳定 task_id 调用 `start_work`，声明 expected_files、独立分支和职责。Cloud 可拒绝重叠认领，但这不是文件系统锁；与实际持有人协调并确认后，才能继续重叠部分。重复 `start_work` 会累加文件范围；需要缩减时，先使用当前版本正规 release，再按明确的新任务范围认领，不能把重复调用成功当成旧范围已释放。
3. 使用 `update_work` 记录进度、问题、决定及消息。确认接收者的真实 ID，不把在线状态或请求成功当作对方已阅读。
4. 完成后提交并推送代码，通过 `finish_work` 记录真实结果，并用 `transfer_work` 的当前版本发出交接。交接必须含提交 SHA、改动文件、测试结果、风险、剩余事项和接手范围。
5. B 接受并复验后回复；A 处理反馈，再按既有 PR、CI、备份与发布规程集成。Concord 不增加发布权限，也不替代正式数据恢复点。

## 消息验收

A 给 B 发送带唯一标记的连接测试，B 回答设备名称、项目标识和通信状态。B 再发送独立反向测试，A 收到并回复；最后两端读取消息回执。四步都有实际记录才算双向通过。

应分别记录“发送到服务端”“接收端读取”“模型回复”。后台程序收到消息不等于闲置模型被唤醒。Codex managed bridge、活动轮次 Hook 和手工拉取的能力不同；桥接不工作时，应明确说明消息需安全点或下一轮读取。

Codex 需要用户信任新增 Hook。重启前先保存任务、提交、配置位置和未完成测试；重启后在实际 WorkBox 项目打开 `/mcp` 和 `/hooks` 检查加载与信任，再用 `inspect_work` 恢复任务。Windows managed daemon 要求非管理员启动；不能绕过这一检查或停止其他正在工作的会话。

Cloud 的 `inspect_work` 查询正文不等于接收确认。原消息仍为 `sent` 时，应先通过官方收件入口取得消息，再回复，不修改回执。需要显式等待时，可使用官方 `inbox watch --agent <自己的实际ID> --provider codex --once`，但该命令也不能证明闲置模型会自动运行。具体主机、会话、消息标识和测试时间回执仅保留在私有验证记录中，不提交到公共仓库。

参考：[Concord Codex 接入](https://github.com/Get-Concord-AI/concord-mcp/blob/main/docs/codex.md)、[Concord 安装说明](https://github.com/Get-Concord-AI/concord-mcp#quick-start)、[Codex 项目 MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)、[Codex Hook 与信任](https://learn.chatgpt.com/docs/hooks)。
