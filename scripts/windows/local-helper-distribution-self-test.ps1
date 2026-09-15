[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$DistributionDirectory)

$ErrorActionPreference = 'Stop'
$DistributionDirectory = [IO.Path]::GetFullPath($DistributionDirectory)
$manifestPath = Join-Path $DistributionDirectory 'distribution-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'distribution-manifest.json is missing.' }
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
if ($manifest.format -ne 1 -or [string]$manifest.filename -notmatch '^[A-Za-z0-9._-]+\.zip$' -or [string]$manifest.sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'distribution manifest is invalid.' }
$archive = Join-Path $DistributionDirectory ([string]$manifest.filename)
if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) { throw 'distribution archive is missing.' }
if ((Get-Item -LiteralPath $archive).Length -ne [int64]$manifest.size_bytes -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ine [string]$manifest.sha256) { throw 'distribution archive checksum verification failed.' }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('local-helper-self-test-' + [Guid]::NewGuid().ToString('N'))
$temporary = [IO.Path]::GetFullPath($temporary)
$tempBoundary = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
if (-not $temporary.StartsWith($tempBoundary, [StringComparison]::OrdinalIgnoreCase)) { throw 'Distribution test escaped the temporary directory.' }
try {
    Expand-Archive -LiteralPath $archive -DestinationPath $temporary -Force
    $inner = Get-Content -Raw -LiteralPath (Join-Path $temporary 'artifact-manifest.json') | ConvertFrom-Json
    if ($inner.format -ne 1) { throw 'inner artifact manifest is invalid.' }
    foreach ($file in @($inner.files)) {
        if ([string]$file.path -match '(^[\\/]|\.\.[\\/])') { throw 'inner manifest contains an unsafe path.' }
        $path = Join-Path $temporary ([string]$file.path)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -ne [int64]$file.size_bytes -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine [string]$file.sha256) { throw "inner artifact verification failed: $($file.path)" }
    }
    $runner = Join-Path $temporary 'SimulationWorkbenchLocalHelper.exe'
    & $runner --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'frozen helper --help smoke test failed.' }
} finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force } }
Write-Host 'PASS: local helper distribution is verified.' -ForegroundColor Green
