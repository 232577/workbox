# 可追溯发布

## 来源

发布只能来自已经合入 `origin/main` 的准确提交。先获取远端，确认工作树干净、本地 main 与 origin/main 一致，并保存完整提交 SHA。禁止将其他机器的整个工作目录覆盖进发布副本。

软件版本当前保持 1.0.0；每次桌面发布使用新的日期/构建标签和新资产名。已有标签及安装包绝不重新指向或替换。网站单独变更可使用相同桌面安装包，但必须记录新的站点源码提交和部署 ID，不能编造新桌面版本。

## 门槛

1. PR 的仓库边界、网站和所有 Windows 回归检查通过，失败、跳过和警告明确记录。
2. 本机原生交互、安装器和匿名空数据验证覆盖改动。不会借用其他构建的结果。
3. 新环境使用锁定依赖，在干净克隆构建；运行数据、恢复点、测试临时目录和凭据不入包。
4. 记录 EXE、安装包和载荷清单 SHA-256，公开材料使用匿名实例及准确构建来源。
5. 新标签创建前核对不存在；发布先草稿，核对每个资产摘要和源提交后再公开。
6. 网站下载目录更新必须在对应资产可用后部署；保留全部旧链接和现有统计存储、权限。
7. 匿名完整下载、Range、网页交互、媒体、权限边界通过后，记录发布完成。仅上传成功不是验收通过。

## 命令基线

```powershell
git fetch origin --tags
git status --short
git rev-parse HEAD
git rev-parse origin/main
.\.venv\Scripts\python.exe scripts/dev.py check
powershell -ExecutionPolicy Bypass -File build.ps1
.\.venv\Scripts\python.exe -m scripts.build_installer --help
```

`build.ps1` 使用当前克隆的 `.venv`，Inno Setup 6 在发布机单独安装。不要以更新日期为由重建或重新安装一个字节相同的正式版本。

新标签使用附注标签，并仅在核验后通过一次性 `WORKBOX_RELEASE_TAGS=1` 允许本地 hook 创建；该变量不允许覆盖或删除已有标签。服务器端标签规则仍然生效。推送后核对远端标签目标，不使用 `--force` 或资产 `--clobber`。

网站部署凭据由发布者本机登录或受保护的 CI environment 提供，不复制个人 OAuth 到源码或 PR。当前 CI **只检查，不自动部署，也不自动公开安装包**。执行部署时，应直接针对已核验的干净克隆 `website/wrangler.jsonc`，保存 SHA 和部署 ID；不得改其他站点或重置 Durable Object。

## 干净 Windows 安装验收

`WorkBox installer acceptance` 只由主分支上的 `workflow_dispatch` 运行。候选先保留为草稿，
提供安装包、`BUILD_PROVENANCE.json` 和 `PACKAGE_CONTENTS.sha256`；输入准确源码提交与
独立测量的安装包 SHA-256。工作流核对源码属于 main 历史后，在一次性 Windows runner
下载并验证候选和上一正式版本。下载步骤使用 GitHub 的短期只读令牌，安装步骤不传入令牌。

验收覆盖上一版全新安装、匿名数据库及材料保护、候选原目录升级、逐文件摘要、运行时构建标识
及卸载后匿名数据保留。存在任何既有 WorkBox 安装注册信息时直接拒绝测试；不在工作电脑上
临时覆盖卸载注册项。通过前不得公开候选，也不得把云端虚拟机称为第二台物理设备。

## 失败与回退

公开前失败：保留失败证据和草稿，不把它称作正式版。公开后发现问题：新增修复版本或在网站指向先前的已验证资产，保留原标签和资产。业务数据恢复属于独立、受控的本机流程，不能通过 Git checkout 或覆盖旧数据库完成。

源码校验清单属于指定提交/发布的快照，不是每次代码编辑都必须重写的锁文件。历史清单保留于发布标签和 `docs/releases/`；新发布应从指定 Git 提交生成新清单及源码归档，而不是沿用旧清单声称当前树完全相同。
