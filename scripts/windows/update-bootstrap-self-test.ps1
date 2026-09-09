[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$fixtureRoot = Join-Path $projectRoot ('backups\update-bootstrap-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixtureRoot -Force | Out-Null
$savedRepository = $env:SIMDASH_UPDATE_REPOSITORY
$savedBranch = $env:SIMDASH_UPDATE_BRANCH
function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Invoke-TestGit([string]$Root, [string[]]$GitArgs) {
    $priorPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = & git.exe -C $Root @GitArgs 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $priorPreference
    if ($code -ne 0) { throw ($output -join "`n") }
}
function Invoke-TestBatch([string]$Path) {
    $priorPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Push-Location -LiteralPath (Split-Path -Parent $Path)
    try {
        $output = & $env:ComSpec /d /c 'update.bat <nul' 2>&1
        $code = $LASTEXITCODE
    }
    finally { Pop-Location }
    $ErrorActionPreference = $priorPreference
    Write-Host ($output -join "`n")
    return $code
}
try {
    $remoteRoot = Join-Path $fixtureRoot 'remote repository'
    $targetRoot = Join-Path $fixtureRoot ('existing deployment ' + [char]0xD55C + [char]0xAE00)
    New-Item -ItemType Directory -Path $remoteRoot, $targetRoot | Out-Null
    Invoke-TestGit $remoteRoot @('init', '-b', 'fixture')
    New-Item -ItemType Directory -Path (Join-Path $remoteRoot 'scripts\windows') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $remoteRoot 'scripts\windows\GitUpdate.psm1') -Value '# Bootstrap contract fixture only.'
    @'
param([string]$ProjectRoot, [string]$RepositoryUrl, [string]$Branch)
@{Root=$ProjectRoot;Repository=$RepositoryUrl;Branch=$Branch} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ProjectRoot 'driver-result.json')
# Replacing the running batch file must not execute its new tail.
Set-Content -LiteralPath (Join-Path $ProjectRoot 'update.bat') -Value '@echo off'
exit 0
'@ | Set-Content -LiteralPath (Join-Path $remoteRoot 'update.ps1') -Encoding UTF8
    Invoke-TestGit $remoteRoot @('add', '.')
    Invoke-TestGit $remoteRoot @('-c', 'user.name=Updater Test', '-c', 'user.email=updater@example.invalid', 'commit', '-m', 'fixture')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'update.bat') -Destination $targetRoot
    Set-Content -LiteralPath (Join-Path $targetRoot 'stop.ps1') -Value 'throw "The bootstrap fixture must not stop a real app."'
    Set-Content -LiteralPath (Join-Path $targetRoot '.env') -Value 'KEEP=original'
    $env:SIMDASH_UPDATE_REPOSITORY = $remoteRoot
    $env:SIMDASH_UPDATE_BRANCH = 'fixture'
    Assert-True ((Invoke-TestBatch (Join-Path $targetRoot 'update.bat')) -eq 0) 'Single-file bootstrap failed.'
    $result = Get-Content -Raw -LiteralPath (Join-Path $targetRoot 'driver-result.json') | ConvertFrom-Json
    Assert-True ($result.Root -eq $targetRoot) 'The staged driver received the wrong deployment root.'
    Assert-True ($result.Repository -eq $remoteRoot -and $result.Branch -eq 'fixture') 'Repository/branch arguments were lost.'
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $targetRoot '.env')).Trim() -eq 'KEEP=original') 'Bootstrap changed user settings.'
    Write-Host 'PASS: one-file bootstrap, spaces/Hangul paths, target forwarding, self-replacing batch, settings preservation.'

    $missingRoot = Join-Path $fixtureRoot 'remote missing updater'
    New-Item -ItemType Directory -Path $missingRoot | Out-Null
    Invoke-TestGit $missingRoot @('init', '-b', 'fixture')
    Set-Content -LiteralPath (Join-Path $missingRoot 'README.md') -Value 'Old remote'
    Invoke-TestGit $missingRoot @('add', '.')
    Invoke-TestGit $missingRoot @('-c', 'user.name=Updater Test', '-c', 'user.email=updater@example.invalid', 'commit', '-m', 'fixture')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'update.bat') -Destination $targetRoot -Force
    $env:SIMDASH_UPDATE_REPOSITORY = $missingRoot
    Assert-True ((Invoke-TestBatch (Join-Path $targetRoot 'update.bat')) -ne 0) 'Missing remote updater was accepted.'
    Write-Host 'PASS: missing remote updater fails clearly before service changes.'

    Push-Location -LiteralPath $targetRoot
    try {
        & $env:ComSpec /d /c 'update.bat -NoBrowser <nul' | Out-Host
        $firstOptionCode = $LASTEXITCODE
    }
    finally { Pop-Location }
    Assert-True ($firstOptionCode -eq 2) 'First-time bootstrap silently ignored unsupported options.'
    Write-Host 'PASS: first-time unsupported options show usage without downloading.'

    @'
param([switch]$NoBrowser)
if (-not $NoBrowser) { exit 8 }
exit 0
'@ | Set-Content -LiteralPath (Join-Path $targetRoot 'update.ps1') -Encoding UTF8
    $priorPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Push-Location -LiteralPath $targetRoot
    try {
        & $env:ComSpec /d /c 'update.bat -NoBrowser <nul' 2>&1 | Out-Host
        $code = $LASTEXITCODE
    }
    finally { Pop-Location }
    $ErrorActionPreference = $priorPreference
    Assert-True ($code -eq 0) 'Regular launcher did not forward arguments.'
    Write-Host 'PASS: installed updater forwards command-line arguments.'
    Write-Host "Fixtures retained: $fixtureRoot"
}
finally {
    $env:SIMDASH_UPDATE_REPOSITORY = $savedRepository
    $env:SIMDASH_UPDATE_BRANCH = $savedBranch
}
exit 0
