[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$modulePath = Join-Path $PSScriptRoot 'InstallLocalHelperDistribution.psm1'
Import-Module $modulePath -Force
$root = Join-Path ([IO.Path]::GetTempPath()) ('local-helper-distribution-import-' + [Guid]::NewGuid().ToString('N'))

function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Get-Hash([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function New-Release([string]$Directory, [string]$Version, [string]$Payload = 'release') {
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    $name = "SimulationWorkbenchLocalHelper-$Version-windows-x64.zip"
    $archive = Join-Path $Directory $name
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::Open($archive, [IO.Compression.ZipArchiveMode]::Create)
    try {
        $entry = $zip.CreateEntry('artifact-manifest.json')
        $stream = New-Object IO.StreamWriter($entry.Open(), (New-Object Text.UTF8Encoding($false)))
        try { $stream.Write('{"format":1,"files":[]}') } finally { $stream.Dispose() }
        $payloadEntry = $zip.CreateEntry('payload.txt'); $payloadStream = New-Object IO.StreamWriter($payloadEntry.Open(), (New-Object Text.UTF8Encoding($false)))
        try { $payloadStream.Write($Payload) } finally { $payloadStream.Dispose() }
    } finally { $zip.Dispose() }
    $manifest = [ordered]@{ format = 1; version = $Version; filename = $name; sha256 = Get-Hash $archive; size_bytes = [int64](Get-Item -LiteralPath $archive).Length; released_at = '2026-09-09T00:00:00Z' }
    [IO.File]::WriteAllText((Join-Path $Directory 'distribution-manifest.json'), ($manifest | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    return $archive
}

try {
    $source = Join-Path $root 'source'; $target = Join-Path $root 'target'
    $archive = New-Release $source '0.1.0' 'known-good'
    $first = Install-LocalHelperDistribution -SourceDirectory $source -TargetDirectory $target
    Assert-True ($first.Status -eq 'ready' -and $first.Version -eq '0.1.0') 'offline import did not report ready.'
    $targetManifest = Join-Path $target 'distribution-manifest.json'; $targetArchive = Join-Path $target $first.Filename
    $oldManifestHash = Get-Hash $targetManifest; $oldArchiveHash = Get-Hash $targetArchive
    $repeat = Install-LocalHelperDistribution -SourceDirectory $source -TargetDirectory $target
    Assert-True ($repeat.Version -eq '0.1.0' -and (Get-Hash $targetManifest) -eq $oldManifestHash) 'idempotent offline import changed the manifest.'

    [IO.File]::WriteAllBytes($archive, [byte[]](1,2,3,4))
    $rejectedTamper = $false
    try { Install-LocalHelperDistribution -SourceDirectory $source -TargetDirectory $target | Out-Null } catch { $rejectedTamper = $true }
    Assert-True $rejectedTamper 'tampered offline archive was accepted.'
    Assert-True ((Get-Hash $targetManifest) -eq $oldManifestHash -and (Get-Hash $targetArchive) -eq $oldArchiveHash) 'tampered import changed the active release.'

    $unsafe = Join-Path $root 'unsafe'; New-Item -ItemType Directory -Path $unsafe | Out-Null
    [IO.File]::WriteAllText((Join-Path $unsafe 'distribution-manifest.json'), '{"format":1,"version":"0.1.0","filename":"../outside.zip","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","size_bytes":1,"released_at":"2026-09-09T00:00:00Z"}', (New-Object Text.UTF8Encoding($false)))
    $rejectedPath = $false
    try { Install-LocalHelperDistribution -SourceDirectory $unsafe -TargetDirectory $target | Out-Null } catch { $rejectedPath = $true }
    Assert-True $rejectedPath 'path traversal filename was accepted.'
    Assert-True ((Get-Hash $targetManifest) -eq $oldManifestHash -and (Get-Hash $targetArchive) -eq $oldArchiveHash) 'unsafe manifest changed the active release.'

    $goodSource = Join-Path $root 'good-source'; New-Release $goodSource '0.1.0' 'known-good' | Out-Null
    $directoryTarget = Join-Path $root 'directory-target'; New-Item -ItemType Directory -Path $directoryTarget | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $directoryTarget $first.Filename) | Out-Null
    $rejectedDirectory = $false
    try { Install-LocalHelperDistribution -SourceDirectory $goodSource -TargetDirectory $directoryTarget | Out-Null } catch { $rejectedDirectory = $true }
    Assert-True $rejectedDirectory 'an existing directory at the archive filename was accepted.'

    $junction = Join-Path $root 'source-junction'
    New-Item -ItemType Junction -Path $junction -Target $goodSource | Out-Null
    $rejectedJunction = $false
    try { Install-LocalHelperDistribution -SourceDirectory $junction -TargetDirectory $target | Out-Null } catch { $rejectedJunction = $true }
    Assert-True $rejectedJunction 'a junction source was accepted.'
    [IO.Directory]::Delete($junction)
    Write-Host 'PASS: offline import, idempotency, tamper rejection, path traversal rejection, and active-release preservation.' -ForegroundColor Green
}
finally {
    if (Test-Path -LiteralPath $root) {
        $resolved = [IO.Path]::GetFullPath($root); $parent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([char]92)
        if ((Split-Path -Parent $resolved).TrimEnd([char]92) -ieq $parent -and (Split-Path -Leaf $resolved).StartsWith('local-helper-distribution-import-')) { Remove-Item -LiteralPath $resolved -Recurse -Force }
    }
}
