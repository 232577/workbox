# 文件级增量更新协议 v1

用户已选择「只更新变化文件，完整安装包兜底」。本次实现源码、构建工具与隔离验收；不安装到正式目录，不改真实数据，不公开新的安装包。

## 用户行为

检查更新仍由用户主动发起。已安装的新更新器优先选择当前构建对应、且小于完整安装包的增量资产。缺少匹配资产、基线不符、下载或校验失败时，下载同一目标版本的完整安装包并提示原因；取消不触发兜底下载。旧更新器仍只识别完整 EXE，因此首次启用需要一次完整升级。

增量下载完成后，继续沿用等待文件任务结束、持有数据会话锁创建数据库恢复点、安全停机的流程。程序文件按清单更新；数据库、工作目录、材料、ZIP、恢复点及卸载登记不属于增量包。

## 资产与信任

- 完整安装包仍为既有 `WorkBox-1.0.0-Setup-x64-<date>[-rN].exe`。
- 增量资产为 `WorkBox-1.0.0-Delta-x64-<from suffix>-to-<to suffix>.wbdelta`，suffix 采用相同的日期/可选 rN 规则。
- Release 的现有 `assets` 列表提供 `name,size,digest,browser_download_url,state`。客户端只接受同一 WorkBox 仓库、同一目标 tag、上传完成、HTTPS 白名单来源和 SHA-256；不把哈希称为数字签名，不增加新信任源。
- `.wbdelta` 是受限 ZIP 容器，仅供软件更新器使用。此协议不改变业务 ZIP 的不解压、不执行规则。

## 包结构（冻结）

仅包含 UTF-8 `delta.json` 和 `payload/<relative path>` 文件，无目录条目。JSON 顶层字段恰为：

```json
{
  "format": "workbox-file-delta-v1",
  "from_tag": "v1.0.0.20261113.2",
  "to_tag": "v1.0.0.20261113.3",
  "base": {"WorkBox.exe": {"sha256": "64 lowercase hex", "size": 123}},
  "target": {"WorkBox.exe": {"sha256": "64 lowercase hex", "size": 124}}
}
```

示例省略了必需运行库。`base` 和 `target` 是完整程序载荷文件映射，不只是改动列表；包括 `PACKAGE_CONTENTS.sha256` 文件本身。每个记录恰有 `sha256,size` 两项。根据两张映射计算：目标新增或摘要/长度改变的文件必须且只能出现在 payload；删除项为 base 有而 target 没有的路径。未变文件不进包。仅大小改变而摘要相同也按变化处理，最终仍核对真实字节。

两张映射都必须包含 `WorkBox.exe`、`WorkBoxUpdater.exe`、`PACKAGE_CONTENTS.sha256`、`_internal/python311.dll`、`_internal/PySide6/Qt6Core.dll`、`_internal/PySide6/plugins/platforms/qwindows.dll`。没有更新助手的旧载荷不能成为可用增量基线。

允许的根文件为上述两个 EXE、`PACKAGE_CONTENTS.sha256`、`安装说明.txt`、`THIRD_PARTY_NOTICES.md`；其他文件只能在 `_internal/` 或 `licenses/` 内，许可证限 `.txt`。拒绝业务数据后缀 `.db/.db-wal/.db-shm/.sqlite/.sqlite3/.csv/.doc/.docx/.xls/.xlsx/.pdf/.zip/.7z/.log`，任何层级的 data/backups/logs/.local/.git/.codex/工作项目/ZIP归档/升级恢复点 目录均拒绝。

路径仅接受 NFC 正规化的相对 POSIX 路径。拒绝设备名、绝对/盘符/UNC/反斜线、空段、`.`/`..`、控制字符、ADS 冒号、结尾空格/句点；按 Windows 不区分大小写拒绝碰撞和文件/目录前缀冲突。拒绝 ZIP 重复条目、链接、加密、未知文件、声明外文件和大小不符。仅支持 Stored/Deflate。

限制：每张映射最多 10000 文件；每个文件及目标/基线各自总原始长度不超过 512 MiB；包不超过 256 MiB；delta.json 不超过 4 MiB。先检查目录声明，再分块读取验证真实大小及摘要，不能信任 ZIP 的声明而无限解压。

## 公共模块接口（冻结）

`delta_format.py` 只使用标准库和已有 `native_files.plain_path`，不得导入 Qt、业务服务或带当前构建号的模块，以便独立冻结助手。

- `build_key(tag: str) -> tuple[int, int]`：沿用现有正式 tag 规则。
- `delta_filename(from_tag: str, to_tag: str) -> str`：仅严格递增版本。
- `validate_path(value: str) -> str`：上面的程序路径边界。
- `validate_manifest(value: object) -> dict`：严格结构、完整映射、限额，返回独立规范副本。
- `changed_paths(manifest: dict) -> list[str]`、`removed_paths(manifest: dict) -> list[str]`：排序后的路径。
- `read_manifest(package: Path, *, expected_sha256: str, expected_size: int, from_tag: str, to_tag: str) -> dict`：同一个打开文件句柄验证外层摘要、完整包结构和全部 payload 字节。
- `stage_package(package: Path, destination: Path, *, expected_sha256: str, expected_size: int, from_tag: str, to_tag: str) -> dict`：上述完整验证后，写入新建、空的本地普通 staging 目录；拒绝已有目录/链接/重解析点。返回规范 manifest。只解包清单内程序文件，不调用 ZIP 的 extract/extractall。

## 生成方

`scripts/build_incremental_update.py` 提供 `build_delta(base_payload, target_payload, output, *, from_tag, to_tag) -> dict`，以及对应 CLI。输入是已生成的干净安装载荷；逐文件重新核对各自 `PACKAGE_CONTENTS.sha256`，核对无多余文件，并把清单文件本身加入文件映射。拒绝未知/缺失/越界/链接/被改动载荷、输出覆盖输入和既有输出。生成确定性排序、固定 ZIP 时间戳的受限包，再用公共 reader 自验；返回资产名、实际字节长度、摘要、来源/目标 tag、改动和删除数量。实际上传状态由发布系统验证后提供，构建器不能把本地文件称为已上传。

官网 stable.json 保持旧客户端兼容，只为目录中真实提供的可选增量资产追加合法 assets。既有 r2 和历史发布不伪造增量资产，也不改变其下载链接/摘要。

`build.ps1` 先从 `update_helper_main.py` 用同一锁定环境生成标准库 onefile/windowed `WorkBoxUpdater.exe`，再构建原有 WorkBox onedir，并将助手复制入载荷。`scripts/build_installer.py` 与安装验收允许并要求该新助手。后续增量也可以更换助手，因为实际执行的是更新缓存中的已验证副本。

## 客户端与替换方

`Release` 增加可选增量描述，旧六字段构造、完整包解析/下载保持兼容。`incremental_update.download_update(release, cache_root, *, app_dir, cancel_event=None, progress=None) -> Path` 选择、校验和下载；不具备安装能力的源码模式仍只下载完整包。`verify_downloaded_update(path, release, app_dir)` 在停机前重新验证完整包或增量包和基线。

基线必须逐文件符合受信任包的完整 base 映射，已知程序目录中出现额外文件时不能猜测或删除。没有相同构建/不满足条件时走完整更新。下载、选择和校验在后台任务进行，取消保持现有程序。

现有 `launch_installer` 增加增量分支：复制并核验安装目录中 `WorkBoxUpdater.exe` 到更新缓存，用隐藏窗口、无 shell 拼接参数启动副本，等待原 WorkBox 进程退出。参数只允许受验证计划；不得启动新的 Codex CLI 或 app-server。

独立 `incremental_apply.py` 在安装目录下新建本次专用事务目录，确保文件移动位于同一卷。全部改动先 staging 校验，旧受管文件复制并复核到备份，然后落盘恢复日志，逐项通过可恢复的无覆盖移动更换；仅将清单明确移除且仍匹配旧摘要的程序文件移出当前运行目录。任何基线、锁、空间或路径错误均不得碰业务数据。这不是整个目录或整次更新的一步原子切换，中间状态依靠事务记录恢复。

初始日志在非活动目录写入并刷新到磁盘后，才以不覆盖的方式登记活动事务，避免首次日志前中断误封启动。应用持有程序目录共享锁，更新和恢复独占同一锁。stage 字节落盘后再次核对；保留原有安全来源标记，新下载文件保留 Internet 来源，未知命名流或恢复标记变化使操作停止。

最终程序移动由 `update_file_ops.py` 绑定已核对的文件句柄和目标父目录句柄，通过不覆盖目标的相对改名完成。更换前原文件保留在事务隔离区；移除项也先移入隔离区，不按路径直接删除。外部占位或目录变化不能静默覆盖；短暂 Windows 映像占用只做有限等待，每次重试重验，不强杀进程或解除保护。

全部目标摘要通过后，以新 EXE 的 `--build-info` 进行不打开数据库的构建标识探测，再记录 committed、请求以原 data-dir 重启。替换或探测失败，恢复旧程序文件并核对 base；回滚冲突/恢复日志损坏则停止，不猜测覆盖。日志及备份保留用于恢复。

进程中断的未提交事务必须可由独立助手恢复；正常启动在打开业务数据库前检查并阻止未解决事务。若主 EXE 已不能启动，缓存中的独立助手和恢复记录仍保留，提供明确恢复入口。新程序开始业务运行后的数据迁移/用户新操作不做自动数据库倒退，不能宣称无条件自动回滚。

双击安装目录助手可定位唯一未完成事务；若安装入口缺失，缓存中符合固定名称的助手副本可用同目录计划定位原目录，仍须核对版本、目录标识及助手自身摘要，并由用户默认选“否”的确认后恢复。恢复不自动打开业务。失败收据会使下一次主动更新跳过同一个失败增量包，改用完整包；尚未恢复的事务仍先阻止任何更新。

## 验收

先红后绿：包规范与攻击性路径、实际变更/新增/删除/未变、基线不符全量兜底、取消不兜底、网络或摘要失败、空间不足、文件占用、逐项失败回滚、异常中断恢复、恢复冲突、数据哨兵不变、助手副本生命周期及最终构建探测。使用匿名目录，完整 Windows/网站 CI，另做 Windows 独立助手进程验收。任何源码/CI成功都不等于已安装或公开发布。

候选阶段已有相关整组 343 项、发行/安装门槛兼容 110 项、网站 182 项通过；真实链接、命名流与句柄操作在正常 Windows 测试权限下验证，未把权限不足重述为通过。独立审查发现的分块 HTTP 截断兜底、初始日志窗口和最终文件操作竞态均有回归。

独立助手已实际冻结为 EXE，以匿名可执行测试程序验证成功替换/启动请求、目标构建错误回滚、注入中断后恢复三条路径，数据和材料哨兵字节不变；助手内五个相关模块的冻结代码与当前源码逐项匹配。该验收不是正式 WorkBox 安装包或第二台物理设备验收。完整结果以对应提交的 CI、匿名本机报告及未来发布门槛为准，不能据此声称已经安装或发布。
