# GitHub 通知与维护

## 收到通知不等于软件故障

| 通知 | 含义 | 应当怎样处理 |
| --- | --- | --- |
| `dependabot[bot]`、`Bump actions/...` | 机器人提出测试工具升级的 Pull Request，并非已安装软件自动更新 | 检查具体差异、运行完整检查，确认后合并；不因版本号更大就直接接受 |
| `PR run failed: WorkBox checks` | 某个具体提交的自动检查失败 | 看 Actions 中该提交的失败日志，再确认修复后最新提交是否通过；旧邮件不会随修复消失 |
| `Re:` | 同一 PR 讨论或状态的后续消息 | 按 PR 编号与提交号归为同一任务，不当作多个独立故障 |
| 自动代码审查机器人 | 评审结果、状态或服务限制 | 区分“发现问题”和“审查未完成”；额度不足不算评审通过，也不证明产品有缺陷 |

2026-11-02 的 PR #1 中，早期提交在英文 Windows 区域下暴露中文日期格式化错误，修复后最终 PR 和 main 检查通过。对应旧失败邮件是保留的历史事件。自动评审另报告额度已用尽，该轮没有独立自动评审结论，不能冒充已审阅通过。

## 仓库侧降低重复通知

`.github/dependabot.yml` 对 GitHub Actions 使用每周一北京时间 09:00 的常规检查计划，将普通版本更新合并为一个维护 PR，同时最多保留一个此类请求。首次启用或调整配置仍可能立即检查；安全更新和安全告警不按此规则关闭。因此它减少的是重复维护请求，不承诺一周只收到一封邮件。

Actions 仍使用完整提交 SHA 固定版本、只读权限和官方托管运行器。网站、仓库边界和四组 Windows 测试仍是合并门槛。没有通过关闭检查、忽略失败或关闭安全功能来减少邮件。

对已有重复升级请求，先将相同修改纳入一个经过测试的维护 PR，再将旧请求作为已被替代关闭并注明关联；不要通过“永久忽略依赖”来处理通知。

## 个人邮箱偏好是独立选择

在 GitHub 的 Settings → Notifications → System → Actions 中，可选择只接收失败工作流的通知。仓库顶部 Watch 可选择参与/被提及或自定义事件；调整前要考虑哪些 PR/安全提醒仍需要收到。本仓库脚本不会修改账户级通知、邮箱过滤规则、自动评审设置或付费额度。

需紧急关注的是当前必需检查仍失败、真实安全告警、凭证泄漏或发布校验失败；已修复的旧 CI 通知和待审的普通依赖升级无需当作产品故障处理。

## 官方说明

- [工作流通知](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs)
- [管理 Actions 通知](https://docs.github.com/en/subscriptions-and-notifications/how-tos/managing-github-actions-notifications)
- [合并 Dependabot 维护请求](https://docs.github.com/en/code-security/tutorials/secure-your-dependencies/optimizing-pr-creation-version-updates)
