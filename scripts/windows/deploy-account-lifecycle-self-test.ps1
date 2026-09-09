[CmdletBinding()]
param()

# Disposable lifecycle verification: the deployment driver is real; runtime,
# stop, network, and Python are fixture programs.  No project DB or .env is used.
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$powerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $powerShell -PathType Leaf)) { $powerShell = (Get-Command powershell.exe -ErrorAction Stop).Source }
$fixtures = @()

function Write-FixtureFile([string]$Path, [string]$Contents) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    if ([IO.Path]::GetExtension($Path) -eq '.cmd') { $Contents = ($Contents -replace "`r?`n", "`r`n") + "`r`n" }
    [IO.File]::WriteAllText($Path, $Contents, (New-Object Text.UTF8Encoding($false)))
}
function Assert-True($Condition, [string]$Message) { if (-not $Condition) { throw "SELF-TEST FAILED: $Message" } }
function New-Fixture([string]$Name, [switch]$ExistingEnvironment, [switch]$LegacyBackendEnvironment) {
    $base = Join-Path ([IO.Path]::GetTempPath()) ('workbench-deploy-account-' + [Guid]::NewGuid().ToString('N'))
    $root = Join-Path $base $Name
    $events = Join-Path $base 'events.log'
    New-Item -ItemType Directory -Path $root | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'deploy.ps1') -Destination (Join-Path $root 'deploy.ps1')
    Write-FixtureFile (Join-Path $root '.env.example') 'FIXTURE_DEFAULT=true'
    if ($ExistingEnvironment) { Write-FixtureFile (Join-Path $root '.env') 'EXISTING_DATABASE=preserved' }
    if ($LegacyBackendEnvironment) { Write-FixtureFile (Join-Path $root 'backend\.env') 'LEGACY_DATABASE=preserved' }
    Write-FixtureFile (Join-Path $root 'setup.ps1') @'
param([switch]$SkipFrontendBuild,[string]$NetworkMode='')
Add-Content -LiteralPath $env:WORKBENCH_DEPLOY_EVENTS -Value 'runtime-setup'
exit 0
'@
    Write-FixtureFile (Join-Path $root 'stop.ps1') @'
Add-Content -LiteralPath $env:WORKBENCH_DEPLOY_EVENTS -Value 'stop'
exit 0
'@
    Write-FixtureFile (Join-Path $root 'scripts\windows\Network.psm1') @'
function Initialize-DeploymentNetwork { param([string]$Mode='auto'); [pscustomobject]@{ Mode = $Mode } }
Export-ModuleMember -Function Initialize-DeploymentNetwork
'@
    Write-FixtureFile (Join-Path $root 'scripts\windows\Runtime.psm1') @'
function Get-ExpectedRuntimeVersions { [pscustomobject]@{ Node='fixture'; Python='fixture'; Pnpm='fixture' } }
function Get-ProjectPython { return $env:WORKBENCH_DEPLOY_FAKE_PYTHON }
Export-ModuleMember -Function Get-ExpectedRuntimeVersions,Get-ProjectPython
'@
    foreach ($script in @('check_database_startup_preflight.py', 'prepare_account_deployment.py', 'upgrade_postgres_schema.py', 'setup_accounts.py')) {
        Write-FixtureFile (Join-Path $root ('backend\scripts\' + $script)) '# fixture marker'
    }
    $python = Join-Path $base 'fake-python.cmd'
    Write-FixtureFile $python @'
@echo off
if /I "%~nx1"=="check_database_startup_preflight.py" goto preflight
if /I "%~nx1"=="prepare_account_deployment.py" goto backup
if /I "%~nx1"=="upgrade_postgres_schema.py" goto migration
if /I "%~nx1"=="setup_accounts.py" goto accounts
exit /b 44
:preflight
echo fixture database configuration is ready
>>"%WORKBENCH_DEPLOY_EVENTS%" echo database-preflight
if exist "%~dp0postgres-preflight.marker" exit /b 23
exit /b 0
:backup
echo fixture account backup is ready
>>"%WORKBENCH_DEPLOY_EVENTS%" echo backup-verified
if exist "%~dp0backup.marker" exit /b 19
exit /b 0
:migration
echo fixture migration is ready
>>"%WORKBENCH_DEPLOY_EVENTS%" echo migration
exit /b 0
:accounts
echo fixture account setup is ready
>>"%WORKBENCH_DEPLOY_EVENTS%" echo account-setup:%2
if exist "%~dp0accountpending.marker" exit /b 2
exit /b 0
'@
    return [pscustomobject]@{ Base=$base; Root=$root; Events=$events; Python=$python; FailureStage='' }
}
function Invoke-Fixture($Fixture, [switch]$NonInteractive, [string]$FailureStage = '') {
    $env:WORKBENCH_DEPLOY_EVENTS = $Fixture.Events
    $env:WORKBENCH_DEPLOY_FAKE_PYTHON = $Fixture.Python
    if ($FailureStage) { Write-FixtureFile (Join-Path $Fixture.Base ($FailureStage + '.marker')) 'fail this fixture stage' }
    $arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $Fixture.Root 'deploy.ps1'),'-SkipFrontendBuild')
    if ($NonInteractive) { $arguments += '-NonInteractive' }
    & $powerShell @arguments | Out-Host
    return [int]$LASTEXITCODE
}

try {
    $fresh = New-Fixture -Name 'fresh'
    $fixtures += $fresh
    Assert-True ((Invoke-Fixture $fresh -NonInteractive) -eq 0) 'fresh deployment failed'
    Assert-True ((@(Get-Content -LiteralPath $fresh.Events) -join '|') -eq 'runtime-setup|database-preflight|stop|backup-verified|migration|account-setup:--non-interactive') 'fresh deployment order or noninteractive account handling is wrong'
    Assert-True (Test-Path -LiteralPath (Join-Path $fresh.Root '.windows-deploy-ready.json')) 'successful fresh deployment did not publish readiness'
    $freshAcl = Get-Acl -LiteralPath (Join-Path $fresh.Root '.env')
    Assert-True ([bool]$freshAcl.AreAccessRulesProtected) 'fresh environment ACL still inherits directory access'
    Assert-True ((@($freshAcl.Access | Where-Object { $_.AccessControlType -eq 'Allow' })).Count -eq 1) 'fresh environment ACL grants more than its owner'

    $existing = New-Fixture -Name 'existing' -ExistingEnvironment
    $fixtures += $existing
    Assert-True ((Invoke-Fixture $existing) -eq 0) 'existing deployment failed'
    Assert-True ((@(Get-Content -LiteralPath $existing.Events) -join '|') -eq 'runtime-setup|database-preflight|stop|backup-verified|migration|account-setup:') 'existing deployment did not verify backup before migration'
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $existing.Root '.env')).Trim() -eq 'EXISTING_DATABASE=preserved') 'existing environment was modified'

    $legacy = New-Fixture -Name 'legacy-backend-env' -LegacyBackendEnvironment
    $fixtures += $legacy
    Assert-True ((Invoke-Fixture $legacy) -eq 0) 'legacy backend environment deployment failed'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $legacy.Root '.env'))) 'legacy backend environment was replaced with root defaults'
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $legacy.Root 'backend\.env')).Trim() -eq 'LEGACY_DATABASE=preserved') 'legacy backend environment was not preserved'

    $backupFailure = New-Fixture -Name 'backup-failure'
    $fixtures += $backupFailure
    Write-FixtureFile (Join-Path $backupFailure.Root '.windows-deploy-ready.json') '{"old":true}'
    Assert-True ((Invoke-Fixture $backupFailure -NonInteractive -FailureStage 'backup') -ne 0) 'backup failure unexpectedly succeeded'
    Assert-True ((@(Get-Content -LiteralPath $backupFailure.Events) -join '|') -eq 'runtime-setup|database-preflight|stop|backup-verified') 'backup failure continued to migration or account setup'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $backupFailure.Root '.windows-deploy-ready.json'))) 'failed deployment retained a stale readiness stamp'

    $postgresPreflightFailure = New-Fixture -Name 'postgres-preflight-failure'
    $fixtures += $postgresPreflightFailure
    Assert-True ((Invoke-Fixture $postgresPreflightFailure -NonInteractive -FailureStage 'postgres-preflight') -ne 0) 'missing PostgreSQL configuration unexpectedly succeeded'
    Assert-True ((@(Get-Content -LiteralPath $postgresPreflightFailure.Events) -join '|') -eq 'runtime-setup|database-preflight') 'PostgreSQL configuration failure stopped services or ran database stages'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $postgresPreflightFailure.Root '.windows-deploy-ready.json'))) 'PostgreSQL configuration failure published readiness'

    $accountPending = New-Fixture -Name 'account-pending'
    $fixtures += $accountPending
    Write-FixtureFile (Join-Path $accountPending.Base 'accountpending.marker') 'pending'
    Assert-True ((Invoke-Fixture $accountPending -NonInteractive) -ne 0) 'pending administrator unexpectedly published deployment readiness'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $accountPending.Root '.windows-deploy-ready.json'))) 'pending administrator published readiness'

    Write-Host 'Windows deployment account lifecycle self-test passed.' -ForegroundColor Green
    exit 0
}
finally {
    Remove-Item Env:WORKBENCH_DEPLOY_EVENTS -ErrorAction SilentlyContinue
    Remove-Item Env:WORKBENCH_DEPLOY_FAKE_PYTHON -ErrorAction SilentlyContinue
    Remove-Item Env:WORKBENCH_DEPLOY_FAKE_FAILURE_STAGE -ErrorAction SilentlyContinue
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    foreach ($fixture in $fixtures) {
        if ($fixture -and (Test-Path -LiteralPath $fixture.Base)) {
            $resolved = [IO.Path]::GetFullPath($fixture.Base).TrimEnd('\')
            if ((Split-Path -Parent $resolved).TrimEnd('\') -ieq $tempRoot -and (Split-Path -Leaf $resolved).StartsWith('workbench-deploy-account-', [StringComparison]::OrdinalIgnoreCase)) {
                Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
}
