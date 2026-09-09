[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$DistributionDirectory)

# This harness decodes the exact browser-generated PowerShell payload.  Only
# its install parent and port are rewritten to a private temp folder/port so
# the verification never touches a user's installed helper.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Python = Join-Path $Root '.venv-runtime\Scripts\python.exe'
$Node = Join-Path $Root '.tools\node-v22.23.2-win-x64\node.exe'
if (-not (Test-Path -LiteralPath $Node -PathType Leaf)) { $Node = (Get-Command node.exe -ErrorAction Stop).Source }
$DistributionDirectory = [IO.Path]::GetFullPath($DistributionDirectory)
$manifest = Get-Content -Raw -LiteralPath (Join-Path $DistributionDirectory 'distribution-manifest.json') | ConvertFrom-Json
$goodArchive = Join-Path $DistributionDirectory ([string]$manifest.filename)
if (-not (Test-Path -LiteralPath $goodArchive -PathType Leaf)) { throw 'The release archive is missing.' }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('local-helper-installer-test-' + [Guid]::NewGuid().ToString('N'))
$webRoot = Join-Path $temporary 'web'; $installParent = Join-Path $temporary 'LocalAppData\SimulationWorkbench'
function Get-FreePort {
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return $listener.LocalEndpoint.Port } finally { $listener.Stop() }
}
$port = Get-FreePort; $webPort = Get-FreePort
if ($webPort -eq $port) { $webPort = Get-FreePort }
$temporaryFull = [IO.Path]::GetFullPath($temporary)
$tempBoundary = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
if (-not $temporaryFull.StartsWith($tempBoundary, [StringComparison]::OrdinalIgnoreCase)) { throw 'Test path escaped the temporary directory.' }

function Set-ArchiveRoute([string]$Archive) {
    $route = Join-Path $webRoot 'api\local-helper\distribution'
    New-Item -ItemType Directory -Force -Path $route | Out-Null
    Copy-Item -LiteralPath $Archive -Destination (Join-Path $route 'download') -Force
}
function Get-ArchiveDescriptor([string]$Archive) {
    [ordered]@{
        status = 'ready'; version = 'installer-self-test'; filename = 'SimulationWorkbenchLocalHelper.zip'
        artifact_url = '/api/local-helper/distribution/download'
        sha256 = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
        size_bytes = [int64](Get-Item -LiteralPath $Archive).Length; released_at = [DateTime]::UtcNow.ToString('o')
    }
}
function New-BrokenArchive([string]$Destination) {
    $stage = Join-Path $temporary 'broken-stage'; Expand-Archive -LiteralPath $goodArchive -DestinationPath $stage -Force
    $brokenLauncher = Join-Path $stage 'start-local-runner.ps1'
    $launcherText = [IO.File]::ReadAllText($brokenLauncher)
    $launcherText = $launcherText.Replace('$workspace = Split-Path', "throw 'intentional installer rollback self-test failure'`n`$workspace = Split-Path")
    [IO.File]::WriteAllText($brokenLauncher, $launcherText, (New-Object Text.UTF8Encoding($true)))
    $files = Get-ChildItem -LiteralPath $stage -File | Where-Object Name -ne 'artifact-manifest.json' | Sort-Object Name | ForEach-Object {
        [ordered]@{ path = $_.Name; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(); size_bytes = [int64]$_.Length }
    }
    [IO.File]::WriteAllText((Join-Path $stage 'artifact-manifest.json'), (([ordered]@{ format = 1; version = 'broken'; files = @($files) }) | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding($false)))
    Compress-Archive -LiteralPath (Get-ChildItem -LiteralPath $stage -File | Select-Object -ExpandProperty FullName) -DestinationPath $Destination -CompressionLevel Optimal
}
function New-Payload([object]$Descriptor, [string]$Name) {
    $configPath = Join-Path $temporary "$Name.json"; $batPath = Join-Path $temporary "$Name.bat"; $payloadPath = Join-Path $temporary "$Name.ps1"
    $configJson = [ordered]@{ serverUrl = "http://127.0.0.1:$webPort"; webOrigin = "http://127.0.0.1:$webPort"; autoStart = $false; distribution = $Descriptor } | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText($configPath, $configJson, (New-Object Text.UTF8Encoding($false)))
    $moduleUrl = ([Uri](Join-Path $Root 'frontend\src\features\local-pc\setupLauncher.ts')).AbsoluteUri
    $nodeScript = "import { readFileSync, writeFileSync } from 'node:fs'; import { buildLocalHelperSetup } from '$moduleUrl'; writeFileSync(process.argv[2], buildLocalHelperSetup(JSON.parse(readFileSync(process.argv[1], 'utf8'))));"
    & $Node --experimental-strip-types --input-type=module -e $nodeScript $configPath $batPath
    if ($LASTEXITCODE -ne 0) { throw 'Could not generate the production setup payload.' }
    $batch = Get-Content -Raw -LiteralPath $batPath
    $encoded = [regex]::Match($batch, '(?ms)^:: WORKBENCH_LOCAL_HELPER_SETUP_PAYLOAD\r?\n([A-Za-z0-9+/=]+)\s*$').Groups[1].Value
    if (-not $encoded) { throw 'Generated setup payload was missing.' }
    $payload = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($encoded))
    $escapedInstallParent = $installParent.Replace("'", "''")
    $payload = [regex]::Replace($payload, '(?m)^\$installParent = .+$', "`$installParent = '$escapedInstallParent'")
    $testData = (Join-Path $installParent 'local-runner-data').Replace("'", "''")
    $payload = $payload.Replace('& $launcher -ServerUrl', "& `$launcher -Port $port -DataDir '$testData' -ServerUrl")
    $payload = $payload.Replace('http://127.0.0.1:8766/v1/identity', "http://127.0.0.1:$port/v1/identity")
    [IO.File]::WriteAllText($payloadPath, $payload, (New-Object Text.UTF8Encoding($true)))
    return $payloadPath
}
function Invoke-Payload([string]$Payload, [bool]$ExpectSuccess) {
    Write-Host "TEST payload: $([IO.Path]::GetFileName($Payload))"
    $succeeded = $false
    try { & $Payload; $succeeded = $true }
    catch { if ($ExpectSuccess) { throw }; Write-Host "Expected installer rejection: $($_.Exception.Message)" }
    if ($succeeded -ne $ExpectSuccess) { throw "Installer payload success=$succeeded, expected $ExpectSuccess." }
}
function Test-HelperReady {
    try {
        $identity = Invoke-RestMethod -Uri "http://127.0.0.1:$port/v1/identity" -TimeoutSec 2
        return $identity.managed -eq $true
    } catch { return $false }
}
function Stop-TestHelper {
    # PyInstaller has a bootloader parent and child. Stop only executables in
    # this test's private directory, never an arbitrary owner of a TCP port.
    foreach ($helperProcess in @(Get-Process -Name SimulationWorkbenchLocalHelper -ErrorAction SilentlyContinue)) {
        if ($helperProcess.Path -and $helperProcess.Path.StartsWith($temporaryFull + '\', [StringComparison]::OrdinalIgnoreCase)) { Stop-Process -Id $helperProcess.Id -Force -ErrorAction SilentlyContinue }
    }
    Start-Sleep -Milliseconds 250
}

New-Item -ItemType Directory -Force -Path $webRoot, $installParent | Out-Null
Set-ArchiveRoute $goodArchive
$web = Start-Process -FilePath $Python -ArgumentList @('-m', 'http.server', "$webPort", '--bind', '127.0.0.1', '--directory', $webRoot) -WindowStyle Hidden -PassThru
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    $webReady = $false
    while ([DateTime]::UtcNow -lt $deadline -and -not $webReady) { try { Invoke-WebRequest -Uri "http://127.0.0.1:$webPort/api/local-helper/distribution/download" -UseBasicParsing -TimeoutSec 1 | Out-Null; $webReady = $true } catch { Start-Sleep -Milliseconds 100 } }
    if (-not $webReady) { throw 'The isolated archive server did not start.' }
    $installRoot = Join-Path $installParent 'local-helper'
    $initialPayload = New-Payload (Get-ArchiveDescriptor $goodArchive) 'initial'
    Invoke-Payload $initialPayload $true
    $installedExe = Join-Path $installRoot 'SimulationWorkbenchLocalHelper.exe'
    if (-not (Test-Path -LiteralPath $installedExe -PathType Leaf)) { throw 'Fresh installation did not contain the frozen helper.' }
    $expectedHash = (Get-FileHash -LiteralPath $installedExe -Algorithm SHA256).Hash
    $expectedLauncherHash = (Get-FileHash -LiteralPath (Join-Path $installRoot 'start-local-runner.ps1') -Algorithm SHA256).Hash
    if (-not (Test-HelperReady)) { throw 'Fresh installation did not launch the helper.' }

    Invoke-Payload $initialPayload $false
    if ((Get-FileHash -LiteralPath $installedExe -Algorithm SHA256).Hash -ne $expectedHash) { throw 'Running-helper rejection changed the installed release.' }
    Stop-TestHelper

    $brokenArchive = Join-Path $temporary 'broken.zip'; New-BrokenArchive $brokenArchive; Set-ArchiveRoute $brokenArchive
    $brokenPayload = New-Payload (Get-ArchiveDescriptor $brokenArchive) 'broken'
    Invoke-Payload $brokenPayload $false
    if (-not (Test-Path -LiteralPath $installedExe -PathType Leaf) -or (Get-FileHash -LiteralPath $installedExe -Algorithm SHA256).Hash -ne $expectedHash) { throw 'Failed update did not restore the prior release.' }
    if ((Get-FileHash -LiteralPath (Join-Path $installRoot 'start-local-runner.ps1') -Algorithm SHA256).Hash -ne $expectedLauncherHash) { throw 'Failed update kept the broken launcher instead of restoring the previous release.' }

    Set-ArchiveRoute $goodArchive
    $retryPayload = New-Payload (Get-ArchiveDescriptor $goodArchive) 'retry'
    Invoke-Payload $retryPayload $true
    if (-not (Test-HelperReady)) { throw 'Retry after rollback did not launch the helper.' }
    Stop-TestHelper
    Write-Host 'PASS: fresh install, running-process rejection, rollback, and retry are verified.' -ForegroundColor Green
}
finally {
    Stop-TestHelper
    if (-not $web.HasExited) { Stop-Process -Id $web.Id -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
}
