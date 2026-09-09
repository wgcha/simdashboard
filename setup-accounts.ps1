[CmdletBinding()]
param([switch]$NonInteractive)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $Root 'scripts\windows\Runtime.psm1') -Force
$Python = Get-ProjectPython
$arguments = @((Join-Path $Root 'backend\scripts\setup_accounts.py'))
if ($NonInteractive) { $arguments += '--non-interactive' }
Push-Location (Join-Path $Root 'backend')
try { & $Python @arguments; exit $LASTEXITCODE }
finally { Pop-Location }
