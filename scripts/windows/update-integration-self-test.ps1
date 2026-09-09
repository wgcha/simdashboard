[CmdletBinding()]
param()
# Real Git/module/driver; lifecycle and Python are harmless fixture programs.
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('workbench-update-integration-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixtureRoot | Out-Null
$remoteRoot = Join-Path $fixtureRoot 'remote source'
$targetRoot = Join-Path $fixtureRoot ('existing ' + [char]0xD55C + [char]0xAE00)
$eventPath = Join-Path $fixtureRoot 'events.txt'
$savedEvents = $env:WORKBENCH_UPDATE_TEST_EVENTS
$env:WORKBENCH_UPDATE_TEST_EVENTS = $eventPath
function Write-FixtureFile([string]$Path, [string]$Value) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [IO.File]::WriteAllText($Path, $Value, (New-Object Text.UTF8Encoding($true)))
}
function Test-Git([string]$Root, [string[]]$Arguments) {
    $priorPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = & git.exe -C $Root @Arguments 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $priorPreference
    if ($code -ne 0) { throw ($output -join "`n") }
    return ($output -join "`n")
}
function Assert-True($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Invoke-Driver([string]$DriverRoot, [string[]]$ExtraArguments = @()) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $DriverRoot 'update.ps1') -ProjectRoot $targetRoot -RepositoryUrl $remoteRoot -Branch fixture -Yes -NoBrowser @ExtraArguments | Out-Host
    return $LASTEXITCODE
}
try {
    New-Item -ItemType Directory -Path $remoteRoot, $targetRoot | Out-Null
    Test-Git $remoteRoot @('init', '-b', 'fixture') | Out-Null
    foreach ($relative in @('update.bat', 'update.ps1', 'scripts\windows\GitUpdate.psm1')) {
        $destination = Join-Path $remoteRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $sourceRoot $relative) -Destination $destination
    }
    Write-FixtureFile (Join-Path $remoteRoot '.gitignore') @'
.env
backend/data/
.local-runner/
.server-pids.json
backups/
.update.lock
.gitupdate.lock
notes.private
'@
    Write-FixtureFile (Join-Path $remoteRoot 'stop.ps1') @'
param([int]$BackendPort=0,[int]$FrontendPort=0)
Write-Output 'fixture stop output'
Add-Content -LiteralPath $env:WORKBENCH_UPDATE_TEST_EVENTS -Value "stop:$BackendPort/$FrontendPort"
exit 0
'@
    Write-FixtureFile (Join-Path $remoteRoot 'deploy.ps1') @'
param([string]$NetworkMode)
Write-Output 'fixture deploy output'
Add-Content -LiteralPath $env:WORKBENCH_UPDATE_TEST_EVENTS -Value 'deploy'
exit 0
'@
    Write-FixtureFile (Join-Path $remoteRoot 'start.ps1') @'
param([int]$BackendPort,[int]$FrontendPort,[switch]$NoBrowser)
Write-Output 'fixture start output'
Add-Content -LiteralPath $env:WORKBENCH_UPDATE_TEST_EVENTS -Value "start:$BackendPort/$FrontendPort"
exit 0
'@
    Write-FixtureFile (Join-Path $remoteRoot 'scripts\windows\import-local-helper-distribution.ps1') @'
param([string]$ProjectRoot,[string]$LocalHelperDistributionSource,[string]$LocalHelperManifestUrl)
Add-Content -LiteralPath $env:WORKBENCH_UPDATE_TEST_EVENTS -Value 'helper'
if ($env:WORKBENCH_UPDATE_TEST_HELPER_FAIL -eq '1') { exit 9 }
exit 0
'@
    Write-FixtureFile (Join-Path $remoteRoot 'version.txt') 'one'
    # These source documents are tracked in the real deployment repository even
    # though their parent folders also contain private runtime files.
    Write-FixtureFile (Join-Path $remoteRoot 'deploy\windows\certs\README.md') 'Certificate placement instructions only.'
    Write-FixtureFile (Join-Path $remoteRoot 'log\work-log.md') 'Tracked development work log.'
    Test-Git $remoteRoot @('add', '.') | Out-Null
    Test-Git $remoteRoot @('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','initial updater') | Out-Null

    # The user's actual starting point: a ZIP deployment followed by git init.
    Test-Git $targetRoot @('init', '-b', 'main') | Out-Null
    Copy-Item -LiteralPath (Join-Path $remoteRoot 'stop.ps1') -Destination $targetRoot
    Write-FixtureFile (Join-Path $targetRoot 'version.txt') 'old source'
    Write-FixtureFile (Join-Path $targetRoot '.env') 'KEEP=secret-fixture'
    Write-FixtureFile (Join-Path $targetRoot 'backend\data\existing.duckdb') 'database bytes'
    Write-FixtureFile (Join-Path $targetRoot '.local-runner\state.json') 'personal state'
    Write-FixtureFile (Join-Path $targetRoot 'notes.private') 'personal note'
    Write-FixtureFile (Join-Path $targetRoot '.server-pids.json') '{"backend":{"port":9123},"frontend":{"port":9456}}'
    $preserved = @{}
    foreach ($relative in @('.env','backend\data\existing.duckdb','.local-runner\state.json','notes.private')) {
        $preserved[$relative] = (Get-FileHash -LiteralPath (Join-Path $targetRoot $relative)).Hash
    }
    Assert-True ((Invoke-Driver $sourceRoot) -eq 0) 'Real module + entry bootstrap failed.'
    Assert-True ((Test-Git $targetRoot @('branch','--show-current')) -eq 'fixture') 'Bootstrap branch was not established.'
    foreach ($relative in $preserved.Keys) {
        Assert-True ((Get-FileHash -LiteralPath (Join-Path $targetRoot $relative)).Hash -eq $preserved[$relative]) "Changed personal file: $relative"
    }
    $backup = @(Get-ChildItem -LiteralPath (Join-Path $targetRoot 'backups') -Directory -Filter 'git-update-*')[0]
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $backup.FullName 'version.txt')) -eq 'old source') 'Bootstrap original source backup was lost.'
    $expected = 'stop:9123/9456|deploy|start:9123/9456'
    Assert-True ((@(Get-Content -LiteralPath $eventPath) -join '|') -eq $expected) 'Bootstrap stages or ports are incorrect.'
    Write-Host 'PASS: real Git unborn bootstrap + driver; source backup, settings/database preservation, custom ports.'

    Write-FixtureFile (Join-Path $remoteRoot 'version.txt') 'two'
    Test-Git $remoteRoot @('add', 'version.txt') | Out-Null
    Test-Git $remoteRoot @('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','new version') | Out-Null
    Assert-True ((Invoke-Driver $targetRoot) -eq 0) 'Real installed driver fast-forward failed.'
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $targetRoot 'version.txt')) -eq 'two') 'Fast-forward did not apply the new source.'
    Assert-True ((@(Get-Content -LiteralPath $eventPath) -join '|') -eq ($expected + '|' + $expected)) 'Second update stage order changed.'
    Assert-True ([string]::IsNullOrEmpty((Test-Git $targetRoot @('status','--porcelain')))) 'Updater left unignored files blocking the next update.'
    Write-Host 'PASS: installed driver real fast-forward and clean status after logs/locks/backups.'

    $offlineSource = Join-Path $fixtureRoot 'offline helper source'
    New-Item -ItemType Directory -Path $offlineSource | Out-Null
    Assert-True ((Invoke-Driver $targetRoot @('-LocalHelperDistributionSource', $offlineSource)) -eq 0) 'Helper distribution update failed.'
    Assert-True ((@(Get-Content -LiteralPath $eventPath) | Select-Object -Last 4) -join '|' -eq 'stop:9123/9456|deploy|helper|start:9123/9456') 'Helper phase was not placed after deployment.'
    $env:WORKBENCH_UPDATE_TEST_HELPER_FAIL = '1'
    Assert-True ((Invoke-Driver $targetRoot @('-LocalHelperDistributionSource', $offlineSource)) -ne 0) 'Failed helper import unexpectedly started the server.'
    Assert-True ((@(Get-Content -LiteralPath $eventPath) | Select-Object -Last 3) -join '|' -eq 'stop:9123/9456|deploy|helper') 'Failed helper import did not stop before start.'
    Remove-Item Env:WORKBENCH_UPDATE_TEST_HELPER_FAIL -ErrorAction SilentlyContinue
    $beforeDualSource = @(Get-Content -LiteralPath $eventPath).Count
    Assert-True ((Invoke-Driver $targetRoot @('-LocalHelperDistributionSource', $offlineSource, '-LocalHelperManifestUrl', 'https://example.invalid/manifest.json')) -ne 0) 'Dual helper sources unexpectedly accepted.'
    Assert-True ((@(Get-Content -LiteralPath $eventPath).Count -eq $beforeDualSource)) 'Dual helper sources stopped services.'
    Write-Host 'PASS: helper distribution success, failed import stop, and source validation.'

    $beforeDirty = @(Get-Content -LiteralPath $eventPath).Count
    Write-FixtureFile (Join-Path $targetRoot 'version.txt') 'manual user change'
    Assert-True ((Invoke-Driver $targetRoot) -ne 0) 'Dirty source unexpectedly accepted.'
    Assert-True ((@(Get-Content -LiteralPath $eventPath)).Count -eq $beforeDirty) 'Dirty preflight stopped services.'
    Write-Host 'PASS: dirty preflight preserves running services and manual source.'

    # Deliver only update.bat to a second old ZIP folder. Exercise the real clone,
    # external driver, first Enter prompt, and no-.git initialization together.
    $zipRoot = Join-Path $fixtureRoot ('zip without git ' + [char]0xD55C + [char]0xAE00)
    New-Item -ItemType Directory -Path $zipRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $remoteRoot 'stop.ps1') -Destination $zipRoot
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'update.bat') -Destination $zipRoot
    Write-FixtureFile (Join-Path $zipRoot '.env') 'KEEP=zip-settings'
    $priorRepository = $env:SIMDASH_UPDATE_REPOSITORY
    $priorBranch = $env:SIMDASH_UPDATE_BRANCH
    $priorPreference = $ErrorActionPreference
    $beforeZip = @(Get-Content -LiteralPath $eventPath).Count
    Push-Location -LiteralPath $zipRoot
    try {
        $env:SIMDASH_UPDATE_REPOSITORY = $remoteRoot
        $env:SIMDASH_UPDATE_BRANCH = 'fixture'
        $ErrorActionPreference = 'Continue'
        '' | & $env:ComSpec /d /c update.bat 2>&1 | Out-Host
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
        $ErrorActionPreference = $priorPreference
        $env:SIMDASH_UPDATE_REPOSITORY = $priorRepository
        $env:SIMDASH_UPDATE_BRANCH = $priorBranch
    }
    Assert-True ($code -eq 0) 'End-to-end single-file ZIP update failed.'
    Assert-True ((Test-Git $zipRoot @('branch','--show-current')) -eq 'fixture') 'Single-file ZIP branch initialization failed.'
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $zipRoot '.env')) -eq 'KEEP=zip-settings') 'Single-file ZIP settings changed.'
    Assert-True ((@(Get-Content -LiteralPath $eventPath)).Count -eq ($beforeZip + 3)) 'Single-file ZIP update did not finish the service sequence.'
    Write-Host 'PASS: full single-file bootstrap + Enter + real Git/module/driver in a no-.git ZIP folder.'
    Write-Host "Fixtures retained: $fixtureRoot"
}
finally { $env:WORKBENCH_UPDATE_TEST_EVENTS = $savedEvents }
exit 0
