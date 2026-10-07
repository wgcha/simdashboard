[CmdletBinding()]
param()

# Exercise the optional external-wheels reinstall (ADR 0006) with fixture
# python/uv commands.  No real environment, network, database, or user
# configuration is used.
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
Import-Module (Join-Path $sourceRoot 'scripts\windows\ExternalWheels.psm1') -Force
$base = Join-Path ([IO.Path]::GetTempPath()) ('workbench external wheels-' + [Guid]::NewGuid().ToString('N'))
$savedGateway = [Environment]::GetEnvironmentVariable('SIMDASH_DRIVE_GATEWAY')

function Write-FixtureFile([string]$Path, [string]$Contents) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    if ([IO.Path]::GetExtension($Path) -eq '.cmd') { $Contents = ($Contents -replace "`r?`n", "`r`n") + "`r`n" }
    [IO.File]::WriteAllText($Path, $Contents, (New-Object Text.UTF8Encoding($false)))
}
function Assert-True($Condition, [string]$Message) { if (-not $Condition) { throw "SELF-TEST FAILED: $Message" } }
function Assert-Throws([scriptblock]$Action, [string]$Contains) {
    $message = ''
    try { & $Action | Out-Null } catch { $message = $_.Exception.Message }
    Assert-True ($message -and $message.Contains($Contains)) "expected an error containing '$Contains' but got '$message'"
}
function Reset-Log { if (Test-Path -LiteralPath $script:log) { Remove-Item -LiteralPath $script:log -Force } }
function Get-Log { if (Test-Path -LiteralPath $script:log) { return (Get-Content -Raw -LiteralPath $script:log) } return '' }

try {
    New-Item -ItemType Directory -Path $base -Force | Out-Null
    $script:log = Join-Path $base 'calls.log'
    $env:WORKBENCH_EXTWHEEL_LOG = $script:log
    # Fixture tools record their arguments; exit codes are steered by env vars.
    $uv = Join-Path $base 'tools\uv.cmd'
    Write-FixtureFile $uv @'
@echo off
>>"%WORKBENCH_EXTWHEEL_LOG%" echo uv %*
if defined WORKBENCH_EXTWHEEL_INSTALL_EXIT exit /b %WORKBENCH_EXTWHEEL_INSTALL_EXIT%
exit /b 0
'@
    $python = Join-Path $base 'tools\python.cmd'
    Write-FixtureFile $python @'
@echo off
>>"%WORKBENCH_EXTWHEEL_LOG%" echo python %*
if /I "%~3"=="pip" goto pip
if defined WORKBENCH_EXTWHEEL_IMPORT_EXIT echo ModuleNotFoundError: No module named 'scx_drive_adapter' 1>&2
if defined WORKBENCH_EXTWHEEL_IMPORT_EXIT exit /b %WORKBENCH_EXTWHEEL_IMPORT_EXIT%
exit /b 0
:pip
if defined WORKBENCH_EXTWHEEL_INSTALL_EXIT exit /b %WORKBENCH_EXTWHEEL_INSTALL_EXIT%
exit /b 0
'@
    $absent = Join-Path $base 'absent\external-wheels'
    $empty = Join-Path $base 'empty\external-wheels'
    New-Item -ItemType Directory -Path $empty -Force | Out-Null
    Write-FixtureFile (Join-Path $empty 'README.txt') 'not a wheel'
    $filled = Join-Path $base 'filled dir\external-wheels'
    Write-FixtureFile (Join-Path $filled 'vd_scx_drive_adapter-0.2.0-py3-none-any.whl') 'fixture wheel'

    # Default behaviour: absent or empty folder in none mode does nothing.
    foreach ($directory in @($absent, $empty)) {
        Reset-Log
        $result = Install-ExternalWheels -Python $python -WheelDirectory $directory -DriveMode none -Uv $uv
        Assert-True ($result.Skipped -and -not $result.Installed) "folder without wheels was not skipped: $directory"
        Assert-True ((Get-Log) -eq '') 'no tool may run when there are no external wheels'
    }

    # scx mode without a wheel is a clear error.
    Assert-Throws { Install-ExternalWheels -Python $python -WheelDirectory $absent -DriveMode scx -Uv $uv } 'no adapter wheel'
    Assert-Throws { Install-ExternalWheels -Python $python -WheelDirectory $empty -DriveMode scx -Uv $uv } 'external-wheels'

    # Wheel present (uv path, source venv): offline, no deps, then import check.
    Reset-Log
    $result = Install-ExternalWheels -Python $python -WheelDirectory $filled -DriveMode none -Uv $uv
    $calls = Get-Log
    Assert-True ($result.Installed -and $result.AdapterImported -and -not $result.Error) 'adapter wheel was not installed and imported'
    Assert-True ($calls -match 'uv pip install --python .+ --no-deps --no-index .*vd_scx_drive_adapter-0\.2\.0-py3-none-any\.whl') "uv install arguments are wrong: $calls"
    Assert-True ($calls -match 'python -I -c "?import scx_drive_adapter') "adapter import was not verified: $calls"
    Assert-True ($calls -notmatch '--index-url|--extra-index-url|--find-links') 'external wheel install must not consult an index'

    # Wheel present (pip path, offline release venv).
    Reset-Log
    $result = Install-ExternalWheels -Python $python -WheelDirectory $filled -DriveMode scx
    $calls = Get-Log
    Assert-True ($result.Installed -and $result.AdapterImported) 'pip path did not install the adapter wheel'
    Assert-True ($calls -match 'python -I -m pip --isolated install --no-deps --no-index .*vd_scx_drive_adapter') "pip install arguments are wrong: $calls"

    # Install failure: warning in none mode, error in scx mode.
    $env:WORKBENCH_EXTWHEEL_INSTALL_EXIT = '3'
    $result = Install-ExternalWheels -Python $python -WheelDirectory $filled -DriveMode none -Uv $uv -WarningAction SilentlyContinue
    Assert-True (-not $result.Installed -and $result.Error -match 'exit code 3') 'none-mode install failure was not reported as a non-fatal result'
    Assert-Throws { Install-ExternalWheels -Python $python -WheelDirectory $filled -DriveMode scx -Uv $uv } 'SIMDASH_DRIVE_GATEWAY=scx'
    Remove-Item Env:WORKBENCH_EXTWHEEL_INSTALL_EXIT

    # Import failure after a successful install.
    $env:WORKBENCH_EXTWHEEL_IMPORT_EXIT = '1'
    $result = Install-ExternalWheels -Python $python -WheelDirectory $filled -DriveMode none -Uv $uv -WarningAction SilentlyContinue
    Assert-True (-not $result.AdapterImported -and $result.Error -match 'import scx_drive_adapter failed') 'none-mode import failure was not reported'
    Assert-Throws { Install-ExternalWheels -Python $python -WheelDirectory $filled -DriveMode scx -Uv $uv } 'import scx_drive_adapter failed'
    Remove-Item Env:WORKBENCH_EXTWHEEL_IMPORT_EXIT

    # Drive mode follows the app: process env, then .env, then backend/.env.
    [Environment]::SetEnvironmentVariable('SIMDASH_DRIVE_GATEWAY', $null)
    $rootEnv = Join-Path $base 'mode\.env'
    $backendEnv = Join-Path $base 'mode\backend\.env'
    Assert-True ((Get-ExternalWheelsDriveMode -EnvFiles @($rootEnv, $backendEnv)) -eq 'none') 'missing .env files must mean none'
    Write-FixtureFile $backendEnv "SIMDASH_DRIVE_GATEWAY = 'SCX'  # adapter`n"
    Assert-True ((Get-ExternalWheelsDriveMode -EnvFiles @($rootEnv, $backendEnv)) -eq 'scx') 'backend/.env scx value was not read'
    Write-FixtureFile $rootEnv "# SIMDASH_DRIVE_GATEWAY=scx`nSIMDASH_DRIVE_GATEWAY=none`n"
    Assert-True ((Get-ExternalWheelsDriveMode -EnvFiles @($rootEnv, $backendEnv)) -eq 'none') 'root .env must take precedence over backend/.env'
    [Environment]::SetEnvironmentVariable('SIMDASH_DRIVE_GATEWAY', 'scx')
    Assert-True ((Get-ExternalWheelsDriveMode -EnvFiles @($rootEnv, $backendEnv)) -eq 'scx') 'process environment must take precedence over .env'
    [Environment]::SetEnvironmentVariable('SIMDASH_DRIVE_GATEWAY', $null)

    # The folder is never taken from an update source.
    $gitUpdate = Import-Module (Join-Path $sourceRoot 'scripts\windows\GitUpdate.psm1') -Force -PassThru
    Assert-True (& $gitUpdate { Test-ProtectedRemotePath -Path 'external-wheels/vd_scx_drive_adapter-0.2.0-py3-none-any.whl' }) 'external-wheels must be a protected deployment path'

    Write-Host 'Windows external wheels self-test passed.' -ForegroundColor Green
    exit 0
}
finally {
    [Environment]::SetEnvironmentVariable('SIMDASH_DRIVE_GATEWAY', $savedGateway)
    foreach ($name in @('WORKBENCH_EXTWHEEL_LOG', 'WORKBENCH_EXTWHEEL_INSTALL_EXIT', 'WORKBENCH_EXTWHEEL_IMPORT_EXIT')) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    if (Test-Path -LiteralPath $base) {
        $resolved = [IO.Path]::GetFullPath($base).TrimEnd('\')
        if ((Split-Path -Parent $resolved).TrimEnd('\') -ieq $tempRoot -and (Split-Path -Leaf $resolved).StartsWith('workbench external wheels-', [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
