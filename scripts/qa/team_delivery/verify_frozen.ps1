[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Executable,
    [Parameter(Mandatory)][string]$Output,
    [string]$DesktopSource,
    [ValidateRange(10, 120)][int]$TimeoutSeconds = 45
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public static class WorkBoxQaWindows {
    [StructLayout(LayoutKind.Sequential)] struct Point { public int X, Y; }
    delegate bool WindowCallback(IntPtr window, IntPtr argument);
    [DllImport("user32.dll")] static extern bool EnumWindows(WindowCallback callback, IntPtr argument);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    [DllImport("user32.dll")] static extern bool ScreenToClient(IntPtr window, ref Point point);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);

    public static IntPtr[] OwnedBy(uint process) {
        var windows = new List<IntPtr>();
        EnumWindows((window, unused) => {
            uint owner;
            GetWindowThreadProcessId(window, out owner);
            if (process == owner && IsWindowVisible(window)) windows.Add(window);
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }

    public static bool Click(IntPtr window, uint process, int screenX, int screenY) {
        uint owner;
        GetWindowThreadProcessId(window, out owner);
        if (owner != process || !IsWindowVisible(window)) return false;
        var point = new Point { X = screenX, Y = screenY };
        if (!ScreenToClient(window, ref point)) return false;
        var position = (IntPtr)((point.X & 0xFFFF) | (point.Y << 16));
        return PostMessage(window, 0x0201, (IntPtr)1, position)
            && PostMessage(window, 0x0202, IntPtr.Zero, position);
    }
}
'@

function Find-NativeControl([System.Windows.Automation.AutomationElement]$Root, [string]$Name) {
    foreach ($element in $Root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)) {
        if ($element.Current.Name -eq $Name -and -not $element.Current.IsOffscreen) { return $element }
    }
}

function Invoke-NativeControl([System.Windows.Automation.AutomationElement]$Element) {
    [object]$pattern = $null
    if ($Element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
        $pattern.Invoke()
    } elseif ($Element.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$pattern)) {
        $pattern.Toggle()
    } else {
        throw ('No native button pattern for ' + $Element.Current.Name)
    }
}

function Click-NativeNavigation([System.Windows.Automation.AutomationElement]$Window, [string]$Name) {
    $control = Find-NativeControl $Window $Name
    if ($null -eq $control) { throw "Missing navigation control $Name" }
    $rect = $control.Current.BoundingRectangle
    $windowHandle = [IntPtr]$Window.Current.NativeWindowHandle
    if ($rect.IsEmpty -or -not [WorkBoxQaWindows]::Click($windowHandle, $process.Id,
            $rect.X + $rect.Width / 2, $rect.Y + $rect.Height / 2)) {
        throw "Could not click WorkBox navigation control $Name"
    }
}

function Find-OwnedWindow([int]$ProcessId, [string]$ControlName) {
    foreach ($handle in [WorkBoxQaWindows]::OwnedBy($ProcessId)) {
        $window = [System.Windows.Automation.AutomationElement]::FromHandle($handle)
        if (Find-NativeControl $window $ControlName) { return $window }
    }
}

function Wait-ForNativeControl([scriptblock]$Lookup, [string]$Message) {
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    do {
        if ($process.HasExited) { throw "WorkBox exited before $Message" }
        try {
            $result = & $Lookup
            if ($null -ne $result) { return $result }
        } catch [System.Windows.Automation.ElementNotAvailableException] {
            # A first-run dialog can close while UI Automation is reading it.
        }
        Start-Sleep -Milliseconds 100
    } while ($clock.Elapsed.TotalSeconds -lt $TimeoutSeconds)
    $process.Refresh()
    $ownedCount = [WorkBoxQaWindows]::OwnedBy($process.Id).Count
    throw "WorkBox UI did not reach $Message. WorkBox exposed $ownedCount visible windows. $($process.MainWindowTitle)"
}

$exe = (Get-Item -LiteralPath $Executable).FullName
$outputRoot = New-Item -ItemType Directory -Path $Output -Force
$evidence = (New-Item -ItemType Directory -Path (Join-Path $outputRoot.FullName ('frozen-' + [guid]::NewGuid().ToString('N')))).FullName
$state = (New-Item -ItemType Directory -Path (Join-Path $evidence 'desktop-data')).FullName
$launchDirectory = [System.IO.Path]::GetDirectoryName($exe)
$launchArguments = @()
if ($DesktopSource) {
    $launchDirectory = (Get-Item -LiteralPath $DesktopSource).FullName
    $launchArguments += @('-m', 'workbox')
}
$launchArguments += @('--data-dir', ('"{0}"' -f $state), '--panel', '--no-tray')
$summary = [ordered]@{
    mode = if ($DesktopSource) { 'source rehearsal' } else { 'frozen application' }
    executable_sha256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    onboarding_completed = $false
    team_workspace_opened = $false
    team_workspace_embedded = $false
    project_workspace_reopened = $false
    exited_natively = $false
    desktop_state_removed = $false
}
$process = $null
try {
    $process = Start-Process -FilePath $exe -WorkingDirectory $launchDirectory -ArgumentList $launchArguments -PassThru
    $wizard = Wait-ForNativeControl {
        $process.Refresh()
        if ($process.MainWindowTitle -like '*首次设置') { Find-OwnedWindow $process.Id '首次设置下一步' }
    } 'the first-run setup'
    Invoke-NativeControl (Find-NativeControl $wizard '首次设置下一步')
    $null = Wait-ForNativeControl { Find-NativeControl $wizard '通用模式（推荐）' } 'the default desktop mode'
    Invoke-NativeControl (Wait-ForNativeControl { Find-NativeControl $wizard '首次设置下一步' } 'the setup mode step')
    Invoke-NativeControl (Wait-ForNativeControl { Find-NativeControl $wizard '完成首次设置' } 'the setup confirmation step')
    $main = Wait-ForNativeControl { Find-OwnedWindow $process.Id '团队交件' } 'the main WorkBox window'
    $summary.onboarding_completed = $true
    Click-NativeNavigation $main '团队交件'
    $null = Wait-ForNativeControl { Find-NativeControl $main '打开团队工作台' } 'the embedded team workspace'
    $summary.team_workspace_opened = $true
    $summary.team_workspace_embedded = $true
    Click-NativeNavigation $main '项目'
    $null = Wait-ForNativeControl { Find-NativeControl $main '收进文件' } 'the original project workspace'
    $summary.project_workspace_reopened = $true
    Click-NativeNavigation $main '退出'
    if (-not $process.WaitForExit($TimeoutSeconds * 1000) -or $process.ExitCode -ne 0) {
        throw 'Native WorkBox exit did not complete successfully.'
    }
    if (Test-Path -LiteralPath (Join-Path $state 'workbox.db.session.lock')) {
        throw 'WorkBox left the anonymous database session locked.'
    }
    $summary.exited_natively = $true
} finally {
    if ($null -ne $process -and -not $process.HasExited) {
        $process.Kill()
        $process.WaitForExit()
        $summary.forced_cleanup = $true
    }
    $target = (Resolve-Path -LiteralPath $state).Path
    if ($target -eq $state -and [System.IO.Path]::GetDirectoryName($target) -eq $evidence) {
        Remove-Item -LiteralPath $target -Recurse -Force
        $summary.desktop_state_removed = -not (Test-Path -LiteralPath $target)
    }
    $summary | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'frozen-smoke.json') -Encoding utf8
}

Write-Output (Join-Path $evidence 'frozen-smoke.json')
