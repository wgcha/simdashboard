[CmdletBinding()]
param()

# Exercise only the source-runtime frontend build path with fixture runtimes.
# No installed project dependencies, database, or user configuration is used.
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

function New-Fixture([string]$Name) {
    $base = Join-Path ([IO.Path]::GetTempPath()) ('workbench source frontend build-' + [Guid]::NewGuid().ToString('N'))
    $root = Join-Path $base 'project'
    New-Item -ItemType Directory -Path (Join-Path $root 'scripts\windows') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'scripts\windows\prepare-source-environment.ps1') -Destination (Join-Path $root 'scripts\windows\prepare-source-environment.ps1')
    $tools = Join-Path $root '.tools'
    Write-FixtureFile (Join-Path $tools 'node-fixture-win-x64\node.exe') ''
    Write-FixtureFile (Join-Path $tools 'python\cpython-fixture-windows-x86_64-none\python.exe') ''
    Write-FixtureFile (Join-Path $tools 'uv\uv.exe') ''
    Write-FixtureFile (Join-Path $tools 'pnpm.cmd') @'
@echo off
setlocal EnableExtensions
if /I "%~1"=="install" exit /b 0
set "attempt=0"
if exist "%WORKBENCH_ESBUILD_TEST_COUNTER%" set /p attempt=<"%WORKBENCH_ESBUILD_TEST_COUNTER%"
set /a attempt+=1
>"%WORKBENCH_ESBUILD_TEST_COUNTER%" echo %attempt%
echo fixture build attempt %attempt%
if /I "%WORKBENCH_ESBUILD_TEST_MODE%"=="generic" goto generic
if /I "%WORKBENCH_ESBUILD_TEST_MODE%"=="always-lock" goto locked
if "%attempt%"=="1" goto locked
exit /b 0
:generic
echo [vite:react] transform failed: The process cannot access the file because it is being used by another process. 1>&2
exit /b 9
:locked
echo error during build: [vite:esbuild-transpile] remove C:\Users\fixture\AppData\Local\Temp\esbuild-035d9f216ed67b50449a79968cb30a4dd3adbada43d46a1045ed74223fa48b6d: The process cannot access the file because it is being used by another process. 1>&2
exit /b 1
'@
    Write-FixtureFile (Join-Path $tools 'uv.cmd') @'
@echo off
exit /b 0
'@
    Write-FixtureFile (Join-Path $root '.venv-runtime\Scripts\python.exe') ''
    Write-FixtureFile (Join-Path $root 'backend\requirements.lock') '# fixture lock'
    Write-FixtureFile (Join-Path $root 'frontend\package.json') '{"name":"fixture"}'
    Write-FixtureFile (Join-Path $root 'scripts\windows\bootstrap-runtime.ps1') '# fixture marker'
    Write-FixtureFile (Join-Path $root 'scripts\windows\Runtime.psm1') @'
function Get-ExpectedRuntimeVersions { [pscustomobject]@{ Node='fixture'; Python='fixture'; Pnpm='fixture' } }
function Set-ProjectNodePath { return $env:WORKBENCH_ESBUILD_FIXTURE_ROOT }
function Invoke-ProjectPythonBootstrap { throw 'Unexpected Python bootstrap in frontend build fixture.' }
function Get-ProjectPython { Join-Path $env:WORKBENCH_ESBUILD_FIXTURE_ROOT '.venv-runtime\Scripts\python.exe' }
function Get-ProjectUv { Join-Path $env:WORKBENCH_ESBUILD_FIXTURE_ROOT '.tools\uv.cmd' }
function Get-ProjectPnpm { Join-Path $env:WORKBENCH_ESBUILD_FIXTURE_ROOT '.tools\pnpm.cmd' }
Export-ModuleMember -Function Get-ExpectedRuntimeVersions,Set-ProjectNodePath,Invoke-ProjectPythonBootstrap,Get-ProjectPython,Get-ProjectUv,Get-ProjectPnpm
'@
    return [pscustomobject]@{ Base=$base; Root=$root; Counter=(Join-Path $base 'build-count.txt'); Name=$Name }
}

function Invoke-Fixture($Fixture, [string]$Mode) {
    $env:WORKBENCH_ESBUILD_FIXTURE_ROOT = $Fixture.Root
    $env:WORKBENCH_ESBUILD_TEST_COUNTER = $Fixture.Counter
    $env:WORKBENCH_ESBUILD_TEST_MODE = $Mode
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Fixture.Root 'scripts\windows\prepare-source-environment.ps1'))
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 turns native stderr into NativeCommandError
        # records when redirected, so keep this fixture runner non-terminating.
        $ErrorActionPreference = 'Continue'
        $output = @(& $powerShell @arguments 2>&1)
        $exitCode = [int]$LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousErrorActionPreference }
    return [pscustomobject]@{ ExitCode=$exitCode; Output=(@($output | ForEach-Object { "$_" }) -join "`n") }
}

try {
    $transient = New-Fixture 'transient-lock'
    $fixtures += $transient
    $result = Invoke-Fixture $transient 'retry-once'
    Assert-True ($result.ExitCode -eq 0) "known esbuild temporary-file sharing violation did not recover on retry (exit $($result.ExitCode)): $($result.Output)"
    Assert-True ((Get-Content -Raw -LiteralPath $transient.Counter).Trim() -eq '2') 'known esbuild sharing violation did not make exactly one retry'
    Assert-True ($result.Output -match 'esbuild-035d9f216ed67b50449a79968cb30a4dd3adbada43d46a1045ed74223fa48b6d' -and $result.Output -match 'fixture build attempt 2') 'first-attempt diagnostics or retry output were not retained'

    $generic = New-Fixture 'generic-error'
    $fixtures += $generic
    $result = Invoke-Fixture $generic 'generic'
    Assert-True ($result.ExitCode -ne 0) 'unrelated frontend build error unexpectedly succeeded'
    Assert-True ((Get-Content -Raw -LiteralPath $generic.Counter).Trim() -eq '1') 'unrelated frontend build error was incorrectly retried'
    Assert-True ($result.Output -match '\[vite:react\]' -and $result.Output -match 'process cannot access') 'unrelated build diagnostics were not retained'

    $persistent = New-Fixture 'persistent-lock'
    $fixtures += $persistent
    $result = Invoke-Fixture $persistent 'always-lock'
    Assert-True ($result.ExitCode -ne 0) 'persistent esbuild sharing violation unexpectedly succeeded'
    Assert-True ((Get-Content -Raw -LiteralPath $persistent.Counter).Trim() -eq '2') 'persistent esbuild sharing violation did not stop at the configured attempt limit'
    Assert-True ($result.Output -match 'persistent Windows esbuild temporary-file sharing violation' -and $result.Output -match 'esbuild-035d9f216ed67b50449a79968cb30a4dd3adbada43d46a1045ed74223fa48b6d') 'exhausted retry diagnostics were not retained'

    Write-Host 'Windows source frontend build self-test passed.' -ForegroundColor Green
    exit 0
}
finally {
    Remove-Item Env:WORKBENCH_ESBUILD_FIXTURE_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:WORKBENCH_ESBUILD_TEST_COUNTER -ErrorAction SilentlyContinue
    Remove-Item Env:WORKBENCH_ESBUILD_TEST_MODE -ErrorAction SilentlyContinue
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    foreach ($fixture in $fixtures) {
        if ($fixture -and (Test-Path -LiteralPath $fixture.Base)) {
            $resolved = [IO.Path]::GetFullPath($fixture.Base).TrimEnd('\')
            if ((Split-Path -Parent $resolved).TrimEnd('\') -ieq $tempRoot -and (Split-Path -Leaf $resolved).StartsWith('workbench source frontend build-', [StringComparison]::OrdinalIgnoreCase)) {
                Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
}
