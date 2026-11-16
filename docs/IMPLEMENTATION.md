# 2026-11-16 implementation evidence

## Incremental package compatibility and staging

The package path allowlist now accepts only the exact PyInstaller runtime path
`_internal/base_library.zip` as an opaque file. Other ZIP names, locations and case
variants remain rejected. The inner archive is never inspected or extracted.

Staging now uses handle-relative exclusive creation. The initial staging root
identity is checked against the acquired root handle before creating child entries;
parent handles remain held through writing and flushing. Collisions never overwrite
files and failures retain their partial staging evidence.

The inherited uncommitted safe-create patch was preserved before modification in a
read-only local checkpoint, SHA-256
`1a4d33ffa6cc0e25183f319cce7318279945fa21e50b9958e162dfcfa2bdc670`.
An additional uncommitted last-instant reparse experiment was not run and is not
included in this change. Its text remains in that checkpoint. No existing baseline
test or assertion was removed or weakened. No claim is made for that extra coverage.

Regression evidence, using isolated anonymous temporary fixtures and
`python -m scripts.run_tests`:

| Change | Before implementation | After implementation |
| --- | --- | --- |
| Exact runtime ZIP and exclusive staging integration | 4 failed, 6 passed | Same 10 passed |
| Expected root identity before creation | 2 failed | Included in 41 passed ordinary regression selection |
| Expanded ordinary creation, collision, failure retention and builder/read/stage checks | — | 41 passed |

The ordinary selection did not execute symlink, junction or race experiments.
Full collection and frozen/installer validation are separate gates; these targeted
results do not establish them. Real databases, working materials, archive files and
existing installations were not accessed for these tests.

The subsequent affected-module run covered `test_delta_format.py`,
`test_incremental_package.py`, `test_update_file_ops.py`,
`test_incremental_update.py` and `test_ui_release_identity.py`: **217 passed**, with
one expected duplicate-ZIP-entry warning. The omitted extra experiment was not
present in this collection. Independent contract and implementation reviews found
no blocker; their checks are not a claim of a complete security audit.

## Independent release identity

The candidate identifies itself as `v1.0.0.20261116`; the software version remains
`1.0.0`. Identity regressions failed before the change and then passed: 34 identity
cases and three related metadata/build-probe compatibility cases. They verify that
the current r2 release can be offered this new full installer and that the new build
does not recommend itself as an update.

The current public r2 payload has no update helper. This release must therefore be
installed as a full package and becomes the first possible real incremental base.
No r2-to-candidate delta is generated or advertised. A source candidate is not a
completed build, installation or public release.

## Public presentation already delivered

PR #105 was squash-merged at `3406f7d71ba259702a49f6a3289aa43881d4740a` after all six
jobs of CI run `43786975628` passed. The website was deployed from that clean main
commit as Cloudflare version `34751182-ff24-4229-be9b-f027a11efea2`. Website tests:
162 passed. Local and public Chromium checks: 29 passed each. Public HTTP checks:
13 passed, including the full existing r2 installer hash, ranges and statistics
access restrictions. Existing Cloudflare beacon injection remains accounted for;
HTML is not described as byte-identical. The installer was unchanged by that deploy.
# 项目记录件数与筛选简化

2026-11-13 的需求确认：「件」指项目条目，按某天或一段时间首次记录的项目统计，并查看其当前状态。

## 本次实现

- 主清单保留一个记录日期入口和「全部、待开发、开发中、待交付、已交付」分段，各分段直接显示件数。
- 单日、包含首尾的日期区间、全部日期和日期未知均可选择。日期输入取消不生效，倒置范围不能应用。
- 批次、类型、归档范围、截止日期和加急条件收进更多筛选浮层。活动条件仍可见、可逐项清除；主窗隐藏或离开项目页时关闭浮层。
- 项目行只常驻批次编号。批次进度仍保留在工具提示和主动选择批次后的浮层内。
- 导出使用当前清单的项目 ID 和完整日期范围；修改筛选不改变已捕获的导出范围。
- 首次读取失败时，件数显示未读取标记，不将未知结果显示成零；错误提示和导出禁用状态保持可见。

## 统计边界

首次记录日期沿用现有 `created_at`，按北京时间显示；更新文件、重复收件和状态变更不会产生新的首次记录日期。合并后只计保留项目，沿用其首次日期；不回写历史。默认显示日常项目，归档和删除范围仍由明确筛选控制。

列表和状态件数来自同一只读事务。先应用日期、搜索、批次、类型及其他条件，再汇总当前状态，最后取所选状态的清单。这里没有历史状态回放，也不改变首次有效交付的工作量规则。

## 功能使用日志结论

现有文件收录记录是逐文件业务结果，交付记录包含自动、手动及历史迁移来源。归档分类和更正记录用于保存业务事实；它们不构成完整的界面操作日志。搜索、筛选、切页、导出、设置等入口缺少可比较的持久使用计数。

因此本次不按「没有记录」判定功能无人使用，不给出缺乏依据的频率排名。入口整理依据已确认的查看需求；没有删除业务能力或新增使用追踪。

## 验证

新增统计与界面测试先失败，再实现。覆盖日期首尾、北京时间跨日、未知日期、非法输入、同一快照、状态计数、导出范围、空结果恢复、批次事实保留及浮层关闭。现有选择与滚动锚点回归保留。

所有业务测试使用匿名隔离夹具；界面证据来自实际 Qt 渲染，覆盖浅色、深色及 1200×780、660×480 窗口。相关综合界面回归 46 项、日期组件 22 项、菜单激活 12 项及网站 158 项通过。独立审查发现的浮层关闭问题已补回归并修复。

首次读取失败的未读取标记已补先失败后通过的测试；日期控件、项目件数、单日筛选及导出整组 53 项通过。日期弹窗的原生鼠标测试先等待窗口实际显示，再操作日历。

完整测试按 `scripts/ci_tests.py` 分四组覆盖全部文件。本机整组运行曾出现原生窗口诊断及焦点提交、固定定时采样失败；失败记录保留，不能将它们归为组件弃用提示。菜单测试改为有界等待实际可见且活动，仍严格检查内容和全部激活路径，超时仍失败。最终完整结果以对应提交的 CI 和本机验收记录为准。此记录不表示已安装或发布。

2026-11-16 接续：日期编辑无效输入按 Esc 恢复后重新校验应用按钮，覆盖单日、区间起点及终点三种情况。新增三例先失败后通过，相关 97 项通过（708 条依赖警告），实际 Windows Qt 菜单入口验证恢复后可以应用。最终候选 `639099725e6f50ce6681591c4f6a2b8c5ac8c0fa` 与独立复验来源树相同；CI `43788829548` 六项全部成功，PR #103 合入 `6e6231eae0b8d36b6ea6430f2a596bd5d983c120`。源码合并不表示新安装包已发布。
