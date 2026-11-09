# Fork 借鉴与整合审查 · 2026-11-09

比较主线 `7c4ae83` 与 [t971226-alt/workbox 的 BOX build 60 快照](https://github.com/t971226-alt/workbox/tree/64e5ce8d4b1210b526e55aced31d2cb4dd324a09/snapshots/box-storage-build60)。本次读取 Git 对象、源码、测试和清单，没有执行 fork 的程序、安装器或迁移，也没有使用真实业务库。

## 版本关系

目前该 fork 的 `main` 仍为 `5be0336`。`feat/box-storage-workbench-review` 相对其同步过的上游 `d5089b6` 只增加迁移方案；实际 BOX 源码在 `archive/box-storage-build60` 中，以独立快照保存。不能把方案分支或较大的构建号当作主线的完整升级包。

对 build 58→60 的 Git blob 比较：新增四个表格核对文件，另九个文件变化，没有删除。会话、材料总表及完整性校验大多早于 build 60。其 [发布说明](https://github.com/t971226-alt/workbox/blob/64e5ce8d4b1210b526e55aced31d2cb4dd324a09/docs/archives/box-storage-build60.md)也明确：本次保存既有本地构建，没有从快照提交重新构建或重跑全部历史测试。

## 值得借鉴的部分

| 能力 | 对 WorkBox 的价值与边界 |
| --- | --- |
| 外部表格核对 | 选择 Excel/CSV/TSV 的名称列与日期范围，预览未收录及同名歧义，再确认。主线已有导出，尚无此类外部清单对照入口。适合作为后续独立功能，保存来源表、工作表和行号；不根据不完整日期暗补年份，不把原包收录当作材料齐全或交付。 |
| 文件完整性与失败恢复 | 借鉴副本丢失/被改动后阻止确认、重试前重新核对来源摘要、旧失败记录不能覆盖新版本的回归场景。主线已有文档版本保留、复制校验和归档恢复，应补强原链路。 |
| 收录预览与错误解释 | 让用户看清目标项目、目录、匹配依据和待处理项，保留取消、草稿及失败重试。沿用主线项目与批次身份，不恢复已退役的任务组页面。 |
| 缺项与文件定位 | 未找到实际文件时禁用打开、说明原因；材料缺失与未收录分别展示。界面应显示完整所属项目和读取范围，不能让异常数量看成确定的零。 |
| 悬浮球角标 | 贴边半隐藏时角标留在可见区域是有价值的细节；只在以后引入贴边行为时考虑，不改变主线现有交付口径。 |

代码依据：[表格核对](https://github.com/t971226-alt/workbox/blob/64e5ce8d4b1210b526e55aced31d2cb4dd324a09/snapshots/box-storage-build60/sheet_checklist.py)、[完整性规则](https://github.com/t971226-alt/workbox/blob/64e5ce8d4b1210b526e55aced31d2cb4dd324a09/snapshots/box-storage-build60/collection_integrity.py)、[重试保护](https://github.com/t971226-alt/workbox/blob/64e5ce8d4b1210b526e55aced31d2cb4dd324a09/snapshots/box-storage-build60/retry_intake.py)、[完整性测试](https://github.com/t971226-alt/workbox/blob/64e5ce8d4b1210b526e55aced31d2cb4dd324a09/snapshots/box-storage-build60/tests/test_intake_integrity.py)、[总表定位](https://github.com/t971226-alt/workbox/blob/64e5ce8d4b1210b526e55aced31d2cb4dd324a09/snapshots/box-storage-build60/master_ledger_ui.py)。这些是静态审阅结果，不代表本次已执行其测试。

## 安全和完整性核对

- **源码清单完整。** 快照共 422 个文件，清单列出 421 个，另一个就是清单本身。逐个 Git blob 的 SHA-256 均匹配，未发现缺项或额外未列文件。这证明快照与其清单一致，不证明可运行、兼容或发行来源已认证。
- **不能整目录覆盖。** 主线顶层 Python 模块共 56 个，与快照同名且同字节的只有 6 个，24 个同名文件已分叉，26 个没有同名对应文件。数量不代表功能缺失，但覆盖会绕过现有项目合并、版本、交付统计与更新代码。两边数据库都有版本号 1，却有不同表和业务语义，不能仅依据这个数字共用数据库。
- **依赖尚不闭合。** 快照的 `requirements.txt` 中有 10 个直接依赖未进入 `requirements-lock.txt`，涉及解压及其依赖。按当前锁文件无法证明完整重建。主线继续使用自己的锁定环境。
- **历史记录策略冲突。** fork 的显式直接覆盖选项会删除被替换版本及其检查、提交记录，见 [version_overwrite.py](https://github.com/t971226-alt/workbox/blob/64e5ce8d4b1210b526e55aced31d2cb4dd324a09/snapshots/box-storage-build60/version_overwrite.py)。主线保留旧版本与交付历史，不引入这一选项。
- **解压与便携更新单独审查。** fork 有实际解压及另一套程序发现、就地更新流程。主线目前不解压或执行 ZIP，并使用自己的安装更新校验、备份及恢复流程；这两类能力本次不迁移。快照中自带的清单及 PE 版本信息不能替代可信发行来源验证。
- **许可与敏感内容边界。** 根 LICENSE 在忽略换行后与主线相同，第三方许可仍需随具体移植依赖核对。常见 GitHub 凭据/私钥模式扫描未发现命中；该扫描不等同全面秘密检测。

## 后续落地门槛

先从当前 main 以独立小 PR 迁移纯逻辑或测试场景，再接现有服务。表格核对、目录绑定、解压、便携更新不得绑在一次整库合并中。涉及数据库时使用匿名旧库验证项目合并关系、版本、原件、历史交付、撤销及统计；目录模型保持已有项目路径。

每项改动需保留确认前无副作用、取消和失败恢复、源文件与目标身份、版本摘要和原历史的回归。受影响测试、完整 Windows 四组、网站和仓库边界检查通过后才合并；发布再核对准确 main、产物清单与数据保留。当前 BOX 功能迁移仍维持此前“仅合方案”的边界。
