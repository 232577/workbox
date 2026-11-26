param([string]$PythonExecutable = '', [switch]$Workstation)
$ErrorActionPreference = 'Stop'

function Get-FreezeConfiguration {
    param([switch]$Workstation)
    if ($Workstation) {
        return @{
            BuildRoot = 'build/workstation'
            DistRoot = 'dist/workstation'
            EvidenceRoot = '.local/workstation-freeze'
            MainOptions = @('--runtime-hook', 'scripts/workstation_runtime_hook.py',
                '--exclude-module', 'playwright', '--exclude-module', 'pyee',
                '--exclude-module', 'greenlet')
        }
    }
    return @{
        BuildRoot = 'build'
        DistRoot = 'dist'
        EvidenceRoot = '.local'
        MainOptions = @('--collect-data', 'playwright', '--copy-metadata', 'playwright',
            '--copy-metadata', 'pyee', '--copy-metadata', 'greenlet',
            '--copy-metadata', 'typing_extensions')
    }
}

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepositoryRoot
$Configuration = Get-FreezeConfiguration -Workstation:$Workstation
$BuildRoot = $Configuration.BuildRoot
$DistRoot = $Configuration.DistRoot
$EvidenceRoot = $Configuration.EvidenceRoot
$MainOptions = $Configuration.MainOptions
$Frozen = Join-Path $DistRoot 'WorkBox'
$SourceCommit = ''
if ($Workstation) {
    $SourceCommit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $SourceCommit -notmatch '^[0-9a-f]{40}$') {
        throw 'Cannot determine workstation freeze source commit.'
    }
    $SourceChanges = & git status --porcelain --untracked-files=normal
    if ($LASTEXITCODE -ne 0 -or $SourceChanges) { throw 'Workstation freeze requires a clean source checkout.' }
}
New-Item -ItemType Directory -Force -Path $EvidenceRoot | Out-Null
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_API = 'pyside6'
$Python = if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    Join-Path $RepositoryRoot '.venv\Scripts\python.exe'
} else {
    (Resolve-Path -LiteralPath $PythonExecutable).Path
}
$VersionFile = Join-Path $RepositoryRoot 'assets\windows-version.txt'
$Icon = Join-Path $RepositoryRoot 'build\WorkBox.ico'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $RepositoryRoot (Join-Path $EvidenceRoot 'pyinstaller-cache')
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Create .venv and install requirements-lock.txt before building.'
}
& $Python -m scripts.run_tests -q "--junitxml=$EvidenceRoot/final-tests.xml"
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; build stopped.' }
& $Python -m scripts.prepare_build
if ($LASTEXITCODE -ne 0) { throw 'Build asset preparation failed.' }
# 独立助手只收集标准库；先生成并检查，再构建保留 Qt 的主程序。
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed --noupx `
    --name WorkBoxUpdater --paths $RepositoryRoot --icon $Icon --version-file $VersionFile `
    --exclude-module PySide6 --exclude-module PyQt6 --exclude-module PyQt5 `
    --exclude-module qfluentwidgets --exclude-module qframelesswindow `
    --exclude-module qdarkstyle --exclude-module qtpy `
    --specpath "$BuildRoot/updater" --workpath "$BuildRoot/pyinstaller-updater" --distpath "$BuildRoot/updater-dist" `
    workbox/updates/update_helper_main.py
if ($LASTEXITCODE -ne 0) { throw 'Updater PyInstaller failed.' }
$Updater = Join-Path $RepositoryRoot "$BuildRoot/updater-dist/WorkBoxUpdater.exe"
if (-not (Test-Path -LiteralPath $Updater -PathType Leaf)) { throw 'Missing updater executable.' }
$SelfCheck = Start-Process -FilePath $Updater -ArgumentList @('--self-check', '--noninteractive') `
    -Wait -PassThru -WindowStyle Hidden
if ($SelfCheck.ExitCode -ne 0) { throw 'Updater self-check failed; main build stopped.' }
$UpdaterHash = (Get-FileHash -LiteralPath $Updater -Algorithm SHA256).Hash
& $Python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx `
    --name WorkBox --paths $RepositoryRoot --icon $Icon --version-file $VersionFile `
    --hidden-import qdarkstyle.dark.darkstyle_rc --hidden-import qdarkstyle.light.lightstyle_rc `
    --collect-data qfluentwidgets --collect-data qframelesswindow `
    @MainOptions `
    --specpath $BuildRoot --workpath "$BuildRoot/pyinstaller" --distpath $DistRoot workbox/__main__.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
$Executable = Join-Path $Frozen 'WorkBox.exe'
if (-not (Test-Path -LiteralPath $Executable)) { throw 'Missing executable.' }
& $Python -m scripts.stage_webengine_runtime --frozen $Frozen --manifest "$EvidenceRoot/qt-engine-files.json"
if ($LASTEXITCODE -ne 0) { throw 'QtWebEngine runtime verification failed.' }
$CopiedUpdater = Join-Path $Frozen 'WorkBoxUpdater.exe'
Copy-Item -LiteralPath $Updater -Destination $CopiedUpdater
if ((Get-FileHash -LiteralPath $CopiedUpdater -Algorithm SHA256).Hash -ne $UpdaterHash) {
    throw 'Updater copy digest mismatch.'
}
if ($Workstation) {
    & $Python -m scripts.workstation_payload --frozen $Frozen
    if ($LASTEXITCODE -ne 0) { throw 'Workstation single-engine runtime verification failed.' }
    $FinalCommit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Cannot verify final source commit.' }
    $SourceChanges = & git status --porcelain --untracked-files=normal
    if ($LASTEXITCODE -ne 0 -or $FinalCommit -ne $SourceCommit -or $SourceChanges) {
        throw 'Source changed during workstation freeze; output cannot be packaged.'
    }
    $Provenance = @{
        variant = 'workstation'
        source_commit = $SourceCommit
        updater_sha256 = $UpdaterHash.ToLowerInvariant()
        executable_sha256 = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToLowerInvariant()
        qt_manifest_sha256 = (Get-FileHash -LiteralPath "$EvidenceRoot/qt-engine-files.json" -Algorithm SHA256).Hash.ToLowerInvariant()
        frozen_path = $Frozen
        created_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json
    $ProvenancePath = Join-Path $RepositoryRoot "$EvidenceRoot/FREEZE_PROVENANCE.json"
    [System.IO.File]::WriteAllText($ProvenancePath, $Provenance + "`n", [System.Text.UTF8Encoding]::new($false))
}
Write-Output "Build complete: $Executable"
