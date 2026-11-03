# 发布前：在线更新产物一致性校验

运行 `python -m scripts.verify_update_release`，把待发布更新清单与实际安装包、构建报告、冻结程序标识报告和完整载荷核对。工具仅离线读取，成功返回 0，发现不一致返回 1；命令参数缺失返回 2。

它不会下载文件、运行 EXE、启动安装器、改数据库、上传资产或改官网。不要将“校验通过”表述为已安装、已部署、已通过第二台设备验收或已经完成发布。

## 输入准备

准备以下文件，全部来自同一个已审查候选构建，保留原始记录：

| 输入 | 来源 |
| --- | --- |
| `--manifest` | 待发布的 `/updates/stable.json` 内容，使用现有 GitHub Release 兼容结构。不是仅有版本文字的自定义 JSON。 |
| `--build-info` | `scripts.build_installer` 产生的 `BUILD_INFO.json`。 |
| `--payload` | 同一次构建的 `payload`，包含 `PACKAGE_CONTENTS.sha256`。 |
| `--installer` | 复制为最终发布日期/修订文件名的同字节安装包；复制不得覆盖既有发布资产。 |
| `--runtime-info` | 可信候选 `WorkBox.exe --build-info --build-info-output` 写入的新文件，选取版本和发布标签，再加上实际 EXE 的 `app_sha256`。 |
| `--expected-tag` | 本次明确要发布的标签，不从远端 latest 猜测。 |

例如 `v1.0.0.20261103.1` 必须对应 `WorkBox-1.0.0-Setup-x64-20261103-r1.exe`。未带修订号的日期标签不添加 `-r1`。软件版本仍为 1.0.0。

冻结程序报告应在既有构建验收流程中生成。下例只用于你已经审核来源并核对过预期摘要的候选 EXE，不要为了得到报告而运行来源不明的下载：

```powershell
# 示例路径须换成同一次可信候选构建；不要指向正式业务目录。
$exe = (Resolve-Path '.local/candidate/payload/WorkBox.exe').Path
$probe = [IO.Path]::GetFullPath('.local/candidate/runtime-probe.json')
$report = [IO.Path]::GetFullPath('.local/candidate/runtime-info.json')
if ((Test-Path -LiteralPath $probe) -or (Test-Path -LiteralPath $report)) {
    throw '运行标识证据已存在，请使用新的候选目录，不覆盖旧报告'
}
$before = (Get-FileHash -Algorithm SHA256 $exe).Hash.ToLowerInvariant()
$process = Start-Process -FilePath $exe -ArgumentList @(
    '--build-info', '--build-info-output', ('"{0}"' -f $probe)
) -Wait -PassThru -WindowStyle Hidden
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $probe -PathType Leaf)) {
    throw '候选构建标识读取失败'
}
$info = Get-Content -LiteralPath $probe -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
$after = (Get-FileHash -Algorithm SHA256 $exe).Hash.ToLowerInvariant()
if ($before -ne $after) { throw '读取期间 EXE 发生变化' }
$shareable = [ordered]@{
    version = $info.version
    release_tag = $info.release_tag
    app_sha256 = $after
} | ConvertTo-Json
$stream = [IO.File]::Open($report, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
$writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
try { $writer.WriteLine($shareable) } finally { $writer.Dispose() }
```

冻结程序没有控制台，不能依赖标准输出采集报告；`Start-Process -Wait` 明确等待进程结束后再读取新文件。`--build-info` 不访问 WorkBox 数据库；上面命令由发布者明确执行，不由校验器执行。原始 `runtime-probe.json` 可能包含本机 `data_dir`，仅在本机保留；用于校验和分享的 `runtime-info.json` 只保留 `version`、`release_tag`、`app_sha256` 三个字段，不能拿其他 EXE 的旧报告复用。

## 执行

```powershell
python -m scripts.verify_update_release `
  --manifest .local/candidate/stable.json `
  --build-info .local/candidate/BUILD_INFO.json `
  --runtime-info .local/candidate/runtime-info.json `
  --payload .local/candidate/payload `
  --installer .local/candidate/WorkBox-1.0.0-Setup-x64-20261103-r1.exe `
  --expected-tag v1.0.0.20261103.1
if ($LASTEXITCODE -ne 0) { throw '发布产物不一致，停止发布' }
```

工具检查精确官方 URL、正式版标志、唯一匹配资产、大小和 SHA-256，重读安装包及载荷字节；拒绝丢失/额外文件、大小写重复路径、路径穿越、设备/文件流路径、符号链接和目录联接、常见数据/配置路径、重复 JSON 键和超限输入。普通非执行文件可以为空，与现有构建器保持一致；EXE 仍必须满足 MZ、大小和摘要检查。

报告只输出已验证的发布名称、标签、摘要和数量，不包含本机绝对路径；输出的 `scope` 为 `offline-artifact-consistency`。失败不得忽略退出码或仅检查是否生成了 JSON。

## 仍然需要的发布门槛

这只是发布前的一道检查，并未自动接入或替换现有部署脚本。发布者需要显式调用并检查退出码。

构建报告和运行报告是调用者提供的证据：校验器核对其字段与实测文件摘要，但不独立证明报告来源，也不从安装器内部提取并证明其内容等于旁边的 payload。停止所有对候选目录的写入后再检查；该结果是检查时的文件状态，不是文件系统事务或持续锁定。

还必须保留准确源码提交到冻结程序的溯源、真实安装/取消/失败回归、原目录与自定义数据目录验收、本机恢复点、匿名下载后复核以及官网实际响应检查。SHA-256 一致不等于独立发布者签名。常见私有路径过滤不是完整敏感信息扫描；不上传含真实客户数据的候选目录或证据。

稳定清单只能在对应资产实际可下载且验收通过后部署。工具接受的 `uploaded` 字段只校验输入声明，并不会替你确认远端资产存在。已有版本的资产、标签和真实业务数据都不由此工具更改。
