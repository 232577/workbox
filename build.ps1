param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_API = 'pyside6'
$Python = if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
} else {
    (Resolve-Path -LiteralPath $PythonExecutable).Path
}
$VersionFile = Join-Path $PSScriptRoot 'assets\windows-version.txt'
$Icon = Join-Path $PSScriptRoot 'build\WorkBox.ico'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot '.local\pyinstaller-cache'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Create .venv and install requirements-lock.txt before building.'
}
& $Python -m scripts.run_tests -q --junitxml=.local/final-tests.xml
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; build stopped.' }
& $Python -m scripts.prepare_build
if ($LASTEXITCODE -ne 0) { throw 'Build asset preparation failed.' }
# 独立助手只收集标准库；先生成并检查，再构建保留 Qt 的主程序。
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed --noupx `
    --name WorkBoxUpdater --icon $Icon --version-file $VersionFile `
    --exclude-module PySide6 --exclude-module PyQt6 --exclude-module PyQt5 `
    --exclude-module qfluentwidgets --exclude-module qframelesswindow `
    --exclude-module qdarkstyle --exclude-module qtpy `
    --specpath build/updater --workpath build/pyinstaller-updater --distpath build/updater-dist update_helper_main.py
if ($LASTEXITCODE -ne 0) { throw 'Updater PyInstaller failed.' }
$Updater = Join-Path $PSScriptRoot 'build\updater-dist\WorkBoxUpdater.exe'
if (-not (Test-Path -LiteralPath $Updater -PathType Leaf)) { throw 'Missing updater executable.' }
$SelfCheck = Start-Process -FilePath $Updater -ArgumentList @('--self-check', '--noninteractive') `
    -Wait -PassThru -WindowStyle Hidden
if ($SelfCheck.ExitCode -ne 0) { throw 'Updater self-check failed; main build stopped.' }
$UpdaterHash = (Get-FileHash -LiteralPath $Updater -Algorithm SHA256).Hash
& $Python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx `
    --name WorkBox --icon $Icon --version-file $VersionFile `
    --hidden-import qdarkstyle.dark.darkstyle_rc --hidden-import qdarkstyle.light.lightstyle_rc `
    --collect-data qfluentwidgets --collect-data qframelesswindow `
    --specpath build --workpath build/pyinstaller --distpath dist main.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
if (-not (Test-Path -LiteralPath 'dist\WorkBox\WorkBox.exe')) { throw 'Missing executable.' }
$CopiedUpdater = Join-Path $PSScriptRoot 'dist\WorkBox\WorkBoxUpdater.exe'
Copy-Item -LiteralPath $Updater -Destination $CopiedUpdater
if ((Get-FileHash -LiteralPath $CopiedUpdater -Algorithm SHA256).Hash -ne $UpdaterHash) {
    throw 'Updater copy digest mismatch.'
}
Write-Output 'Build complete: dist\WorkBox\WorkBox.exe'
