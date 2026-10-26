param()
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$VersionFile = Join-Path $PSScriptRoot 'assets\windows-version.txt'
$Icon = Join-Path $PSScriptRoot 'build\WorkBox.ico'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot '.local\pyinstaller-cache'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Create .venv and install requirements-lock.txt before building.'
}
& $Python -m pytest -q --junitxml=.local/final-tests.xml
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; build stopped.' }
& $Python -m scripts.prepare_build
if ($LASTEXITCODE -ne 0) { throw 'Build asset preparation failed.' }
& $Python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx `
    --name WorkBox --icon $Icon --version-file $VersionFile `
    --specpath build --workpath build/pyinstaller --distpath dist main.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
if (-not (Test-Path -LiteralPath 'dist\WorkBox\WorkBox.exe')) { throw 'Missing executable.' }
Write-Output 'Build complete: dist\WorkBox\WorkBox.exe'
