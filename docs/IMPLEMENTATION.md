# 2026-11-27 补同步个人项目交付

已交付且关联「这是家」任务的单个项目可从项目菜单打开「补同步到这是家…」。确认窗口显示原站编号、标题、材料账号和原交付时间，默认取消。确认后将当前有效的人工交付加入原有提交队列，沿用交付时间、文件归档和工作量统计。

补同步适用于原站仍待开始或进行中、且本轮从未退回的任务。登记时复核当前账号、任务和本地交付记录，发送前再核对确认时的原站版本。已有提交或待核对请求时保留现有记录。排队期间任务版本变化时停止发送并提示核对。升级数据库时，已有队列保留原有提交规则。

隔离回归覆盖确认取消、确认时切换账号、任务变化后停止发送、已排队和已提交的重复请求，以及原交付记录和归档文件保持一致。已检查匿名 Qt 项目菜单、确认窗口和排队反馈。Windows 原生端到端用例通过本机回环站点确认了从菜单到原站回执的路径，只提交了所选任务编号。

---

# 2026-11-26 本地普通文件收录保留命名数据流

从本机收进 DOCX 等普通材料时，工作副本保留文件正文和附加的 Windows 命名数据流。收录及版本轮换沿用同一复制校验。正文和全部数据流一致时才记录为重复；正文一致但附加数据不一致时提示核对，并保留原件和现有工作文件。

含数据流校验的文档轮换日志保存新副本的流清单。恢复时再次核对副本，发现外部变化时保留现场。回退到较旧的版本前，用新版先处理尚未完成的轮换恢复。

单项操作失败时，工作台顶栏显示该项的具体原因。多项失败继续显示汇总，详细原因仍可查看。见[匿名收录失败提示](ui/desktop-shell/intake-single-failure.png)。

---

# 2026-11-26 多选右键与批量状态更新

项目清单右键的批量操作使用打开菜单时选择的项目编号。三项可直接设置的状态为待开发、开发中和待交付。批量删除一次确认，将所选项目移入已删除，保留原文件、归档和交付记录。见[匿名 Qt 菜单截图](ui/desktop-shell/multi-project-context-actions.png)。

批量改状态从当前清单判断是否需要重新开始确认，在写入事务中核对是否出现未经确认的新交付。合并和恢复阻断以批次查询校验。完成后刷新项目清单及件数，保留仍可见的选中项目和滚动位置。悬浮球余量使用计数查询。

在 Windows 隔离 Qt 环境测试 700 个匿名项目，选中 120 个交替设置待开发和开发中。各取 6 次中位数，提交耗时从 664.0 毫秒到 3.8 毫秒，操作到清单刷新的时间从 857.0 毫秒到 112.9 毫秒。测量使用匿名数据库和固定项目清单。

---

# 2026-11-26 这是家待办同步到个人项目

「团队交件」提供主动同步入口。它重新读取当前材料账号的待办，将核对后的任务交给个人项目写入队列。新项目进入待开发，按原站任务编号记录关联。已有唯一同名项目保留进度和目录；重复同步保留本机名称和状态。重名、已删除和已归档项目提示待核对。

用隔离数据验证了账号切换、原站延期读取、重号、本机项目重名、并发、工作目录创建重试和超过 500 项的同步。端到端用例在真实 Qt 入口模拟匿名原站响应，验证首次新建与关联，以及再次同步后本机待交付状态仍可继续处理。同步结果先返回团队页，再刷新个人清单；清单读取失败时仍可再次同步。匿名 Qt 界面已在 580 和 780 逻辑像素宽度下检查。

原站刷新后若不再有待核对的关联，或退出、切换了原站账号，工作台、团队交件页和悬浮提示会清除对应的旧编号。相同编号再次刷新时保留后来出现的收件和交付反馈。切换账号后，旧会话的同步失败消息被丢弃，并继续同步当前账号。

匿名 Qt 回归覆盖了重名冲突消失、退出和切换账号、保留较新的错误提示、相同编号更新及忽略旧会话的同步失败。已核对冲突消失前后的界面截图。

个人交件按原站全名优先匹配。Word 文件名省略了原站任务的尾部版本号时，也能核对对应任务。多个版本、其他原站账号的版本候选，或文件名中的版本与登记目录冲突时，需要明确选择。收录保留文件原名，确认交付继续使用原站任务的实际账号、完整名称和编号。

在隔离 Windows 中验证了团队交件和普通项目收件入口、账号切换、文件夹及项目历史名称。回归覆盖原件字节校验、只更新选中的原站编号，以及再次拖入已交付文件时保留记录。

个人待办标题带尾部版本号，且本机未关联项目有同名未标版本的当前名称或历史名称时，自动同步会保留该待办供核对。全名匹配优先。确认关联时可选当前名称同名且未标版本的项目，保留它的编号、目录、文件和交付历史，并记录原站完整标题和任务编号。不同明确版本不能关联到同一项目。

隔离 Qt 测试覆盖待核对提示、实际关联对话框和完成后的界面。数据库回归验证了原有交付事件、不同原站任务编号和既有版本项目。

---

# 2026-11-26 多选文件预览和批量确认交付

多选时的文件区标明已选数量，并显示当前选中项目的文件。切换焦点可在所选项目之间预览。窄窗通过「查看文件」打开预览，返回后可继续多选。

右击已选项目时，「批量确认交付」应用于所选待交付项目。原有单项入口明确标为「仅确认当前项目交付」。两处批量入口共用确认和归档流程。

---

# Windows 材料交付保留命名数据流

项目工作文件归档会复制并校验 Windows 命名数据流，包括安全来源标记和应用附加元数据。交付清单在有命名流时记录各流的大小和 SHA-256，命名流变化会形成新版本。没有命名流的清单沿用原签名格式，旧归档和交付记录原样保留。如果旧副本里的命名流与当前材料不一致，工作箱会拒绝复用该副本。

Windows NTFS 匿名回归覆盖有来源标记和附加元数据的 DOCX、空命名流、旧归档保留、已归档数据流被修改、复制中断后的重试，以及批量提交前新增的数据流。

---

# 2026-11-25 多选清单位置和单项目交付提示

多选后的「批量操作」从清单顶部打开，包含全选、清空、修改类型及原有批量操作。进入或退出多选、打开或关闭操作弹层时，项目清单使用相同的可见高度和滚动位置。

单项目确认交付失败时，工作台直接显示归档或核验返回的完整原因。长文本换行，可选择并复制，辅助功能也可读取。归档成功后继续显示交付结果和工作量统计信息。

Windows Qt 隔离回归先复现了两个窗口尺寸下多选挤压清单，以及单项目归档失败仅显示汇总。验证使用匿名项目和独立数据目录，并通过实际文件写入占用核对 `WinError 32` 提示。

---

# 2026-11-25 项目清单原生多选

项目清单直接支持 Ctrl 点选、Shift 连选和 Ctrl+A，页首「全选」也可选中当前结果。选中多项时，清单底部自动显示批量操作。项目表导出按当前所选项目取数。已删除视图保持单选和恢复操作。

窄窗单击项目后继续显示清单。选中一项时，通过「查看文件」或 Enter 进入详情。改名和同名合并继续沿用原批次筛选，异步完成时保留现有多选。

两个 Windows Qt 回归先复现了直接连选只选中一项、窄窗单击立即隐藏清单的问题。实现后通过鼠标、键盘、导出与项目类型回归。原批次、后台刷新和收件定位相关 27 项通过，构建标识及更新客户端 110 项通过。浅色 913×610 与深色 660×480 均用匿名项目核对。 [项目清单截图](ui/desktop-shell/light-913-native-selection.png)

---

# 2026-11-25 批量选择与改名后的批次视图

批量操作移至左侧清单底部。勾选「批量选择」后，使用 Qt 原生扩展选择，支持 Ctrl 点选增减项目、Shift 点选或按方向键连选，以及 Ctrl+A 全选当前结果。

用同次收录的 8 个匿名项目复现改名问题。去掉其中一个名称中的句点后，原批次编号、工作目录层级和材料仍正确，完成回调却清空了批次筛选。改名和已确认合并后仅在当前结果内定位项目。窄窗继续显示操作前的清单或详情，拖动滚动条或清单读取失败时跳过定位。收件回执的主动查看入口保留原跳转行为。

Windows Qt 相关回归 122 项通过，改名与批次后端 78 项通过，网站 195 项通过。新增回归先复现了 Shift 只选中两端、操作区挤压文件区、长类型展开后截断和改名清空筛选。审查后补充窄窗详情内确认同名合并的回归，验证原批次筛选仍显示保留项目时，详情继续打开。匿名 Qt 画面验证了 913×610 浅色及深色窗口和 660×480 窄窗，宽窗右侧高度和分栏比例一致，宽窗操作下拉框从 749px 缩至 184px。全套 Windows 结果记录在对应 Pull Request。 [批量选择截图](ui/desktop-shell/light-913-bulk-selection.png)

---

# 2026-11-24 单层项目状态菜单

项目右键及「项目操作」共用同一个 Qt 菜单。「待开发」「开发中」「待交付」直接出现在菜单首层，当前状态保留原生互斥选中标记。支持方向键和回车，归档、删除、恢复阻断和前台任务忙碌时停用状态修改。交付仍通过独立的「确认交付」入口。

匿名 Windows Qt 回归先复现首层找不到状态操作。调整后检查项目 ID、键盘、无障碍选中状态、后台刷新、类型及批量操作；实际 Qt 菜单截图核对了三项状态的排列。构建身份推进至 `v1.0.0.20261124.2`，两个已发布构建均可识别后续更新。

---

# 2026-11-24 批量交付与工作量刷新

勾选「批量选择」时保留窗口尺寸、左右分栏及手动列宽。窄窗口沿用项目清单与详情之间的切换。所选日常项目均为待交付时，可通过「设为已交付…」明确确认。工作文件按原路径和名称复制并核验，归档、交付状态和记录由同一事务登记。提交前复查来源和归档的文件清单，已核对的文件保持只读租约。失败或取消时回滚整批交付，重试可按已有归档尝试记录复用已核验的同内容副本。

点击「今日工作量」会刷新顶部计数和交付记录。停留在该页时可再次点击，当前日期和筛选条件继续保留。

Windows 匿名回归先复现了分栏被隐藏、批量交付不可用、任务取消反馈缺失、早先核验项目的文件变化漏检和点击工作量后计数仍旧的问题。913×610 和 1200×500 的 Qt 用例核对勾选前后窗口及分栏尺寸，660×480 的用例核对窄窗口。使用 913×610 的匿名界面截图核对批量操作，用真实点击和 Qt 截图核对工作量计数与记录同步更新。

批量交付、归档恢复和历史兼容用例通过 126 项，1 项因 Windows 符号链接权限跳过。批量操作与后台刷新界面通过 28 项，工作量及相关界面通过 120 项，批量布局及邻近界面通过 54 项。Qt 用例记录了 QFluentWidgets 使用 `QDomDocument.setContent` 的已有弃用警告。四组 Windows 全量回归见对应 Pull Request。

---

# 2026-11-24 交付确认键盘操作

打开「确认本次交付」时，默认焦点在「确认交付」。Enter 和小键盘 Enter 确认当前选择，Esc 取消。用 Tab 把焦点移到「取消」后，Enter 触发该按钮。下拉列表展开时，Enter 只选择版本。待核对的 ZIP 保持禁用确认。

Windows Qt 回归先复现原焦点错误及待核对 ZIP 的回车关闭问题。匿名弹窗截图核对了默认焦点，键盘用例覆盖工作台打开交付、取消后保持待交付、确认所选 ZIP 和复用既有交付流程。全量检查见对应 Pull Request。

---

# 2026-11-23 项目改名与批次目录

历史批次有时把首个项目目录同时登记为批次外层。修改该项目名称时，沿用共用目录和原批次。普通项目的专用工作目录仍在原父目录下同步改名。

已合并项目退出批次工作目录的分配。其他批次成员沿用实际登记的目录。对于历史上嵌套在保留备份目录内的项目，明确登记的子目录仍归该项目。备份目录中的其他文件仍需确认归属。

新确认的同名合并核对并提交保留项目的材料后，将原项目工作目录按实际目录标识移入同盘恢复位置。保留记录和共用批次目录，恢复日志覆盖目录移动与登记写入之间的中断。已完成的历史合并可保留原恢复路径。

匿名 Windows 测试先复现旧项目目录重新出现、批次成员误入备份路由，再验证目录登记和收件归属。批次与目录测试 38 项通过，收件与路由测试 125 项通过。改名、合并、中断恢复、历史文稿和批次界面等 122 项通过。延迟清理增加了当前工作根目录的保护，两项相关回归通过。

---

# 2026-11-23 项目名称与文件复制

项目清单的 `Ctrl+C` 按可见选中行的顺序复制项目名称，每行一个。文件列表的 `Ctrl+C` 和「文件操作 → 复制文件」把选中的本地文件交给系统剪贴板，拖出文件只提供复制操作。文件拖回主窗口或悬浮球时跳过收件。

项目进入「已交付」后，项目清单隐藏名称前的「加急」。原有加急标记继续保留，状态回到待交付时会恢复显示。

使用 `049ff0f` 基线的隔离工作树和匿名文件。项目名称用例覆盖实际加急标签和批量选择，文件交互用例覆盖复制、拖动启动、归档目录和应用内收件拦截。完整套件结果见此改动的 Pull Request。

---

# 2026-11-20 Python 源码目录迁移

以 `e899bb5` 为基线。71 个应用模块迁入 `workbox/`，由 `desktop/`、`projects/`、`storage/` 和 `updates/` 承担各自职责。应用组合与模块启动入口位于包内，构建脚本迁入 `scripts/`。选择可从仓库直接导入的常规 Python 包布局，沿用现有依赖环境。

- 源码命令为 `python -m workbox`。源码运行的数据目录仍以仓库为基准，冻结程序仍以 EXE 所在目录为基准。
- PyInstaller 从包内主程序和更新助手入口收集模块。安装验收在指定提交中识别旧版和新版助手路径，发现多个或非常规文件时拒绝继续。
- 根目录源码的五项回归在实现前按预期失败。仓库入口、已暂存文件和各职责目录均经过边界检查。
- 使用隔离工作树和匿名夹具验证。完整源代码与构建验证结果记录在此迁移的 Pull Request 中。

---

# 2026-11-20 源码整理验证

本地 `main` 原为 `48a97f3`。GitHub `main` 随后合入 #106 和 #107，共涉及 27 个文件。两次提交的对象和源码树均按 GitHub 提供的哈希校验，本地 `main` 快进到 `a7db3044287d10e1376d2296cd79f425e8b821f0`。整理工作在同一基线的独立分支进行。

- 独立界面直接使用通用控件与时间格式，文档读取直接使用底层目录排除规则。各扫描入口沿用原来的筛选口径。
- 仓库检查拦截强制加入的测试、浏览器和工作树产物。开发脚本按职责整理，分片测试可指定各任务自用的报告和夹具目录。
- 13 项定向检查在改动前失败，覆盖模块依赖、生成目录和测试输出位置。改动后相应回归通过。
- 297 个 Python 文件通过语法解析。仓库边界检查和网站 188 项测试通过。受影响的 Qt 用例 129 项通过，记录到 907 条依赖弃用警告。文档与 ZIP 扫描、归档规则及生成目录检查 85 项通过，1 项因 Windows 链接权限跳过。
- 本机 Git 测试夹具在 `a7db304` 和整理分支上均出现 8 项相同失败。Git for Windows 的 `sh.exe` 在夹具推送初始化时退出，错误为 `NtCreateDirectoryObject: 0xC0000022`。整理分支的 3 项测试输出目录用例通过。四组完整桌面测试覆盖 214 个测试文件，运行结论随 PR 检查记录。

---

# 2026-11-16 implementation evidence

## Committed-update duplicate consolidation and final fixture correction

Review identified redundant copies of changed/removed old program files in both
`backup/` and `retired/`. The update now retains the fixed recovery backup and journal,
and consolidates only this transaction's explicitly registered retired duplicates
after the committed journal is durable. Creation/move handle identities, both full
digests and origin streams are verified while parent and file handles remain held.
Missing, changed or unverified files remain untouched. Historical and unfinished
transactions are not scanned. Consolidation is outside the rollback exception path;
its failure retains duplicate bytes without rolling back the installed target.
Bounded removed/retained counts travel in the existing successful helper receipt;
the receipt mechanism and journal format are unchanged.

The new ordinary cases failed before implementation (11 missing-behavior/API failures;
one additional local native-rename WinError 6 was an environment failure, not a valid
feature assertion). Direct primitive/copy cases subsequently passed 11 tests. The two
new apply cases plus corrected handoff tests passed 9 tests on the normal Windows
execution channel, without changing native assertions. Helper receipt count cases
also failed before implementation and are included in the final affected-module run.

Candidate `ce1f1ff` CI `43789645854` had five successful jobs and two test failures in
the remaining desktop job: old anonymous r2/r3 protocol fixtures were implicitly
using the new production CURRENT_TAG. That run had 3337 passed, 2 failed, 2 explicit
UNC skips and 5977 warnings; it is not recorded as passed. A named fixture now scopes
the simulated old-client identity to the three relevant cases. Production downgrade
checks remain unchanged; added same/older rejection cases retain the files unchanged.
The corrected handoff module passed 7 tests.

Final affected modules (`update_file_ops`, `incremental_apply`, `incremental_handoff`,
`update_helper`, `delta_format`, `incremental_package`, `ui_release_identity`,
`incremental_update`) passed **327 tests**, with one expected duplicate-ZIP warning.
Independent static review found no blocker. The next complete CI and frozen/installer
gates must use this new candidate, not the earlier failed run.

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
