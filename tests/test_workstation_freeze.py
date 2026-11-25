"""Exercise freeze variants in PowerShell without compiling or launching WorkBox."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('workstation', [False, True])
def test_powershell_freeze_configuration_keeps_engines_and_outputs_separate(workstation):
    powershell = shutil.which('pwsh') or shutil.which('powershell')
    if not powershell:
        pytest.skip('PowerShell is required to exercise the Windows freeze entry')
    script = ROOT / 'scripts/build.ps1'
    command = r"""
        $tokens = $null; $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$tokens, [ref]$errors)
        if ($errors.Count) { throw ($errors | Out-String) }
        $function = $ast.Find({ param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Get-FreezeConfiguration'
        }, $true)
        if ($null -eq $function) { throw 'Missing freeze configuration' }
        Invoke-Expression $function.Extent.Text
        Get-FreezeConfiguration -Workstation:($args[1] -eq 'yes') | ConvertTo-Json -Depth 4 -Compress
    """
    # -Command argument handling differs between Windows PowerShell and pwsh;
    # the literal fixture paths are quoted for both shells.
    command = command.replace('$args[0]', "'" + str(script).replace("'", "''") + "'")
    command = command.replace('$args[1]', "'yes'" if workstation else "'no'")
    result = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command', command],
                            check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
    config = json.loads(result.stdout)
    args = config['MainOptions']
    if workstation:
        assert config['DistRoot'] == 'dist/workstation'
        assert config['BuildRoot'] == 'build/workstation'
        assert config['EvidenceRoot'] == '.local/workstation-freeze'
        for name in ('playwright', 'pyee', 'greenlet'):
            assert any(args[i:i + 2] == ['--exclude-module', name] for i in range(len(args)))
        assert '--copy-metadata' not in args and '--collect-data' not in args
    else:
        assert config['DistRoot'] == 'dist'
        assert config['BuildRoot'] == 'build'
        assert '--exclude-module' not in args
        assert any(args[i:i + 2] == ['--collect-data', 'playwright'] for i in range(len(args)))
        for name in ('playwright', 'pyee', 'greenlet', 'typing_extensions'):
            assert any(args[i:i + 2] == ['--copy-metadata', name] for i in range(len(args)))
