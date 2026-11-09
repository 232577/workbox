# 多地点开发与维护

## 一个远端，不再互相覆盖目录

所有地点使用 `https://github.com/232577/workbox.git`。`main` 是集成基线，不是个人工作目录。每项任务独立分支，经过 Pull Request 与检查后合并。旧版私有开发仓库仅保留历史、恢复工具和本机记录，不能作为整目录导出的源去覆盖这个仓库。

Git 只同步代码、测试和公开文档；**不同步实际项目、工作目录、交付材料或数据库**。不要把运行中的数据目录放到 Git、网盘实时同步或 CI 中。

## 新电脑第一次开始

```powershell
git clone https://github.com/232577/workbox.git
cd workbox
python scripts/dev.py setup
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
python scripts/dev.py doctor
python scripts/dev.py start feat/my-task
```

Windows x64、Python 3.11 和 Node.js 22 是当前验证基线。Git 身份和 GitHub 登录由各开发者在自己机器配置，不复制别人的令牌。`setup` 只配置当前克隆的 hooks、pull.ff、push.default 和 fetch.prune，不修改全局 Git 配置。

## 一项任务的流程

先写回归、再实现。同步前先看 `git status`；工具遇到未提交内容会停止，不会替你丢弃或藏起改动。提交使用明确文件名，避免把整个磁盘或私有资料批量加入。

```powershell
.\.venv\Scripts\python.exe scripts/dev.py check
git add <本任务的明确文件路径>
git diff --cached
git commit -m "feat: describe the verified change"
git push -u origin feat/my-task
gh pr create --base main
```

PR 必须通过仓库边界、网站和四组 Windows 测试。页面改动附匿名真实截图。当前只有一个所有者时不要求本人给自己审批，但仍走 PR 和自动检查；增加其他维护者后应开启至少一位非作者审阅。CODEOWNERS 标明责任人，不代表已经进行了独立评审。

## 换地点、接续与冲突

离开前把已完成的独立修改提交并推到自己的任务分支，在 PR 中写明完成项、测试、剩余工作；未完成提交可标记 WIP，不合入 main。同一任务只能有一个正在写入的工作副本，另一个地点接手前先 fetch 并核对分支最新提交。并行任务必须使用不同分支和不同克隆或 worktree。

```powershell
python scripts/dev.py resume feat/my-task
```

`resume` 先核对干净工作区及唯一的协作远端，再 fetch。新电脑会检出远端任务并设置跟踪；已有任务只能快速前进。本地有未推送提交、双方分歧、上游关联错误或远端分支不存在时直接停止，不切换到别的任务，也不 stash/reset/覆盖。它不提供跨电脑编辑锁；接手前仍须和另一地点明确交接。

交接至少写清：任务分支与完整提交号、已完成内容、实际执行的测试及失败/跳过项、尚未完成内容。另一个地点应核对 `git rev-parse HEAD`，不要仅根据本机文件日期判断新旧。凭据由各地点独立配置，真实数据不随分支接续。

功能分支落后 main 时显式执行 `git fetch origin`、`git merge origin/main`，解决冲突并重跑测试。已共享的分支不随意 rebase/强推。分歧不会通过目录覆盖、`reset --hard` 或 `git clean` 自动解决。

合并后，确认工作目录干净，再 `git switch main`、`python scripts/dev.py sync-main`。该命令只允许快速前进；本地 main 有独立提交时会拒绝，提交不会丢失。

## 控制本地开发占用

优先复用已有工作副本和依赖环境。每项任务在 `.local/<任务名>/` 内使用固定测试输出目录；顺序重跑复用它，并行测试才分配不同目录。保留简短验证报告、失败原因、来源提交和校验值，不为每次重跑再保存一整套项目与材料。构建载荷、匿名测试夹具与正式数据恢复点分开放置；清理前核对目录用途、是否仍被进程使用及能否重建。工作材料、归档、未完成恢复日志和升级恢复点不按缓存处理。

## 安全与发布

本地 hooks 拦截常见私有文件/凭据，阻止直接推送 main 和修改已有标签；服务器端规则防止换一台机器后绕过本地设置。扫描不能替代人工审查，尤其是客户名称、截图内容和新型凭证。

部署和安装包发布遵循 [发布规程](docs/RELEASING.md)。桌面变更不能只凭网站测试发布；Windows CI 通过也不等同于真实安装、跨设备或原生交互验收。

常规 Actions 更新按周合为一组，仍须通过全部必需检查并审阅后合并；不会自动批准大版本升级，也不关闭安全告警。GitHub 通知的含义和个人可选设置见 [通知与维护](docs/NOTIFICATIONS.md)。
