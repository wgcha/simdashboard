[CmdletBinding()]
param()

# This is a dependency-free smoke test for the update driver. It builds a fully
# disposable target and replaces Git, the lifecycle scripts, Runtime.psm1, and
# Python with tiny fakes. No repository, server, database, or network is touched.
$ErrorActionPreference = 'Stop'
$SkillRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$PowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $PowerShell -PathType Leaf)) {
    $PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
}

function Write-Utf8File {
    param([string]$Path, [string]$Contents)
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    [System.IO.File]::WriteAllText($Path, $Contents, (New-Object System.Text.UTF8Encoding($false)))
}

function Assert-Condition {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "SELF-TEST FAILED: $Message" }
}

function New-Fixture {
    param([string]$Scenario, [int]$BackendPort = 0, [int]$FrontendPort = 0)
    $id = [Guid]::NewGuid().ToString('N')
    $base = Join-Path ([System.IO.Path]::GetTempPath()) "workbench-update-selftest-$id"
    $driver = Join-Path $base 'driver'
    $target = Join-Path $base 'target'
    $log = Join-Path $base 'events.log'
    New-Item -ItemType Directory -Path (Join-Path $driver 'scripts\windows') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $target 'scripts\windows') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $SkillRoot 'update.ps1') -Destination (Join-Path $driver 'update.ps1')

    $module = @'
function Get-WorkbenchGitUpdatePlan {
    param([string]$Root, [string]$RepositoryUrl, [string]$Branch)
    Add-Content -LiteralPath $env:SELFTEST_LOG -Value 'plan'
    if ($env:SELFTEST_SCENARIO -eq 'fetchfailure') { throw 'fake fetch failure' }
    if ($env:SELFTEST_SCENARIO -eq 'lockhold') { Start-Sleep -Seconds 3 }
    [pscustomobject]@{ Root=$Root; Mode='Existing'; Branch='test'; Remote='fake'; TargetRef='fake/test'; TargetCommit='abc'; OriginalCommit='abc'; BackupDirectory=$null; Changed=$false }
}
function Invoke-WorkbenchGitUpdate { param($Plan); Add-Content -LiteralPath $env:SELFTEST_LOG -Value 'apply' }
Export-ModuleMember -Function Get-WorkbenchGitUpdatePlan,Invoke-WorkbenchGitUpdate
'@
    if ($Scenario -eq 'applyfailure') {
        $module = $module.Replace("function Invoke-WorkbenchGitUpdate { param(`$Plan); Add-Content -LiteralPath `$env:SELFTEST_LOG -Value 'apply' }", "function Invoke-WorkbenchGitUpdate { param(`$Plan); Add-Content -LiteralPath `$env:SELFTEST_LOG -Value 'apply'; throw 'fake apply failure' }")
    }
    Write-Utf8File (Join-Path $driver 'scripts\windows\GitUpdate.psm1') $module

    $lifecycle = @{
        'stop.ps1' = @'
param([int]$BackendPort=0,[int]$FrontendPort=0)
Add-Content -LiteralPath $env:SELFTEST_LOG -Value ("stop backend=$BackendPort frontend=$FrontendPort")
Write-Output 'fake stop stdout'
if ($env:SELFTEST_SCENARIO -eq 'stopfailure') { exit 6 }
exit 0
'@;
        'deploy.ps1' = @'
param([string]$NetworkMode='')
Add-Content -LiteralPath $env:SELFTEST_LOG -Value ("deploy network=$NetworkMode")
Write-Output 'fake deploy stdout'
if ($env:SELFTEST_SCENARIO -eq 'deployfailure') { exit 7 }
exit 0
'@;
        'start.ps1' = @'
param([int]$BackendPort=8000,[int]$FrontendPort=5173,[switch]$NoBrowser,[string]$NetworkMode='')
Add-Content -LiteralPath $env:SELFTEST_LOG -Value ("start backend=$BackendPort frontend=$FrontendPort")
Write-Output 'fake start stdout'
if ($env:SELFTEST_SCENARIO -eq 'startfailure') { exit 8 }
exit 0
'@
    }
    foreach ($name in $lifecycle.Keys) { Write-Utf8File (Join-Path $target $name) $lifecycle[$name] }
    if ($BackendPort -or $FrontendPort) {
        $json = @{ backend = @{ port=$BackendPort }; frontend = @{ port=$FrontendPort } } | ConvertTo-Json
        Write-Utf8File (Join-Path $target '.server-pids.json') $json
    }
    return [pscustomobject]@{ Base=$base; Driver=$driver; Target=$target; Log=$log; Scenario=$Scenario }
}

function Invoke-Fixture {
    param($Fixture)
    $env:SELFTEST_LOG = $Fixture.Log
    $env:SELFTEST_SCENARIO = $Fixture.Scenario
    $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $Fixture.Driver 'update.ps1'),'-ProjectRoot',$Fixture.Target,'-Yes','-NoBrowser')
    & $PowerShell @args | Out-Null
    return [int]$LASTEXITCODE
}

$fixtures = @()
try {
    $fixture = New-Fixture -Scenario 'success'; $fixtures += $fixture
    $code = Invoke-Fixture $fixture
    Assert-Condition ($code -eq 0) "success scenario exit was $code"
    $events = @(Get-Content -LiteralPath $fixture.Log)
    Assert-Condition ((($events -join '|') -eq 'plan|stop backend=0 frontend=0|apply|deploy network=|start backend=8000 frontend=5173')) 'success stage order or child exit handling was incorrect'

    $fixture = New-Fixture -Scenario 'fetchfailure'; $fixtures += $fixture
    $code = Invoke-Fixture $fixture
    Assert-Condition ($code -ne 0) 'fetch failure unexpectedly succeeded'
    $events = @(Get-Content -LiteralPath $fixture.Log)
    Assert-Condition (($events -notcontains 'stop backend=0 frontend=0') -and ($events -notmatch '^stop ')) 'stop ran after fetch failure'

    $fixture = New-Fixture -Scenario 'stopfailure'; $fixtures += $fixture
    $code = Invoke-Fixture $fixture
    $events = @(Get-Content -LiteralPath $fixture.Log)
    Assert-Condition ($code -eq 6) "stop failure exit was $code"
    Assert-Condition (($events -notmatch '^apply$') -and ($events -notmatch '^deploy ')) 'a failed stop continued to source apply or deploy'

    $fixture = New-Fixture -Scenario 'applyfailure'; $fixtures += $fixture
    $code = Invoke-Fixture $fixture
    $events = @(Get-Content -LiteralPath $fixture.Log)
    Assert-Condition ($code -ne 0) 'apply failure unexpectedly succeeded'
    Assert-Condition (($events -contains 'apply') -and ($events -notmatch '^deploy ')) 'a failed source apply continued to deploy'

    $fixture = New-Fixture -Scenario 'deployfailure'; $fixtures += $fixture
    $code = Invoke-Fixture $fixture
    $events = @(Get-Content -LiteralPath $fixture.Log)
    Assert-Condition ($code -eq 7) "deploy failure exit was $code"
    Assert-Condition (($events -join '|') -notmatch 'start ') 'a failed deploy continued to start'

    $fixture = New-Fixture -Scenario 'startfailure'; $fixtures += $fixture
    $code = Invoke-Fixture $fixture
    $events = @(Get-Content -LiteralPath $fixture.Log)
    Assert-Condition ($code -eq 8) "start failure exit was $code"
    Assert-Condition (($events -match '^start ') -and (($events | Where-Object { $_ -match '^start ' }).Count -eq 1)) 'start failure was not surfaced as the final stage'

    $fixture = New-Fixture -Scenario 'ports' -BackendPort 9123 -FrontendPort 9456; $fixtures += $fixture
    $code = Invoke-Fixture $fixture
    $events = @(Get-Content -LiteralPath $fixture.Log)
    Assert-Condition ($code -eq 0) 'custom port scenario failed'
    Assert-Condition (($events -join '|') -match 'stop backend=9123 frontend=9456.*start backend=9123 frontend=9456') 'custom ports were not forwarded to stop/start'

    $fixture = New-Fixture -Scenario 'lockhold'; $fixtures += $fixture
    $env:SELFTEST_LOG = $fixture.Log; $env:SELFTEST_SCENARIO = $fixture.Scenario
    $firstArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $fixture.Driver 'update.ps1'),'-ProjectRoot',$fixture.Target,'-Yes','-NoBrowser')
    $first = Start-Process -FilePath $PowerShell -ArgumentList $firstArgs -PassThru -WindowStyle Hidden
    Start-Sleep -Milliseconds 500
    $secondCode = Invoke-Fixture $fixture
    Assert-Condition ($secondCode -ne 0) 'duplicate lock was accepted'
    $first.WaitForExit()
    Assert-Condition ($first.ExitCode -eq 0) "first lock holder failed with exit $($first.ExitCode)"

    Write-Host 'Windows update entry self-test passed.' -ForegroundColor Green
    exit 0
}
finally {
    Remove-Item Env:SELFTEST_LOG -ErrorAction SilentlyContinue
    Remove-Item Env:SELFTEST_SCENARIO -ErrorAction SilentlyContinue
    $tempParent = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
    $fixturePrefix = 'workbench-update-selftest-'
    foreach ($fixture in $fixtures) {
        if ($fixture -and (Test-Path -LiteralPath $fixture.Base)) {
            # Resolve the generated path and verify both its immediate parent
            # and name before allowing recursive cleanup.
            $resolvedBase = [System.IO.Path]::GetFullPath([string]$fixture.Base).TrimEnd('\')
            $resolvedParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $resolvedBase)).TrimEnd('\')
            $resolvedLeaf = Split-Path -Leaf $resolvedBase
            if ($resolvedParent -ieq $tempParent -and $resolvedLeaf.StartsWith($fixturePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                Remove-Item -LiteralPath $resolvedBase -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
}
