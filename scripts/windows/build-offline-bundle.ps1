[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$VendorRoot,
    [string]$SourceRoot = '',
    [string]$Output = '',
    [string]$ReleaseId = '',
    [switch]$SkipFrontendBuild,
    [switch]$AllowDirty,
    [string]$InnoSetupCompiler = '',
    [switch]$KeepStaging
)

# Build a Windows Server 2022 x64 bundle without downloading anything.  The
# vendor directory is intentionally explicit: an operator can inspect and
# approve the Python, PostgreSQL, Caddy and WinSW inputs before this script
# copies them into a transfer artifact.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Fail([string]$Message) { throw "OFFLINE_BUNDLE_BUILD_FAILED: $Message" }
function Require-File([string]$Path, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Fail "$Name is missing: $Path" }
}
function Require-Directory([string]$Path, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { Fail "$Name is missing: $Path" }
}
function Reject-ReparsePoints([string]$Path, [string]$Name) {
    $links = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -Attributes ReparsePoint -ErrorAction SilentlyContinue)
    if ($links.Count -gt 0) { Fail "$Name contains a reparse point: $($links[0].FullName)" }
}
function Copy-Tree([string]$From, [string]$To) {
    if ((Get-Item -LiteralPath $From -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { Fail "Linked payload directory: $From" }
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $From -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { Fail "Linked payload path: $($item.FullName)" }
        if ($item.Name -in @('__pycache__', '.pytest_cache', '.git')) { continue }
        if ($item.Name -like '.env*' -or $item.Name -like '.postgres-owner*' -or $item.Extension -in @('.pyc', '.log', '.duckdb', '.db', '.dump', '.bak')) { continue }
        if ($item.PSIsContainer) { Copy-Tree $item.FullName (Join-Path $To $item.Name) }
        else { Copy-Item -LiteralPath $item.FullName -Destination $To }
    }
}
function Get-Relative([string]$Base, [string]$Path) {
    $baseUri = New-Object Uri (([IO.Path]::GetFullPath($Base).TrimEnd('\') + '\'))
    $pathUri = New-Object Uri ([IO.Path]::GetFullPath($Path))
    return [Uri]::UnescapeDataString($baseUri.MakeRelativeUri($pathUri).ToString()).Replace('/', '\')
}

if (-not $SourceRoot) { $SourceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
$SourceRoot = [IO.Path]::GetFullPath($SourceRoot)
$VendorRoot = [IO.Path]::GetFullPath($VendorRoot)
Require-Directory $SourceRoot 'source root'
Require-Directory $VendorRoot 'vendor root'
$dirty = (& git -C $SourceRoot status --porcelain 2>$null)
if ($LASTEXITCODE -ne 0 -and -not $AllowDirty) { Fail 'A release requires a verifiable Git checkout.' }
if ($dirty -and -not $AllowDirty) { Fail 'source worktree is dirty; commit changes or pass -AllowDirty.' }
$sourceRevision = ([string](& git -C $SourceRoot rev-parse HEAD 2>$null)).Trim()
if (-not $ReleaseId) {
    $sha = (& git -C $SourceRoot rev-parse --short=12 HEAD 2>$null).Trim()
    if (-not $sha) { $sha = 'source' }
    $ReleaseId = ((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '-' + $sha)
}
if ($ReleaseId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$') { Fail 'ReleaseId contains unsupported characters.' }
if (-not $Output) { $Output = Join-Path $SourceRoot "dist\simworkbench-windows-offline-$ReleaseId.zip" }
$Output = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $Output) { Fail "Refusing to overwrite existing output: $Output" }
if ($InnoSetupCompiler -and (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $Output) "simworkbench-windows-offline-$ReleaseId.exe"))) { Fail 'Installer release already exists; choose a new ReleaseId.' }

$frontendDist = Join-Path $SourceRoot 'frontend\dist'
Require-File (Join-Path $SourceRoot 'backend\requirements.lock') 'backend requirements.lock'
if (-not $SkipFrontendBuild) {
    Import-Module (Join-Path $SourceRoot 'scripts\windows\Runtime.psm1') -Force
    $null = Set-ProjectNodePath
    $pnpm = Get-ProjectPnpm
    $previousBase = $env:VITE_APP_BASE_PATH
    $env:VITE_APP_BASE_PATH = '/home/'
    Push-Location (Join-Path $SourceRoot 'frontend')
    try { & $pnpm install --offline --frozen-lockfile; if ($LASTEXITCODE -ne 0) { Fail 'offline frontend dependency install failed.' }; & $pnpm run build; if ($LASTEXITCODE -ne 0) { Fail 'frontend build failed.' } } finally {
        Pop-Location
        if ($null -eq $previousBase) { Remove-Item Env:VITE_APP_BASE_PATH -ErrorAction SilentlyContinue } else { $env:VITE_APP_BASE_PATH = $previousBase }
    }
} else { Require-File (Join-Path $frontendDist 'index.html') 'frontend build' }
$html = Get-Content -LiteralPath (Join-Path $frontendDist 'index.html') -Raw
if ($html -notmatch 'src="/home/assets/' -or $html -match 'src="https?://') { Fail 'Offline frontend must use a production build with base=/home/ and local assets.' }

# Supported vendor layout.  A prepared Python tree is used directly on the
# target; this avoids copying a machine-specific venv.  The tree must contain
# python.exe, venv and pip; locked packages are installed from the wheelhouse.
$pythonSource = Join-Path $VendorRoot 'python-runtime'
$pythonExe = Join-Path $pythonSource 'python.exe'
$wheelhouse = Join-Path $VendorRoot 'wheelhouse'
$postgresSource = Join-Path $VendorRoot 'postgresql'
$caddyExe = Join-Path $VendorRoot 'caddy\caddy.exe'
$winswExe = Join-Path $VendorRoot 'winsw\WinSW-x64.exe'
Require-Directory $pythonSource 'vendor Python runtime'
Require-File $pythonExe 'vendor Python executable'
Require-Directory $wheelhouse 'vendor wheelhouse'
Require-Directory $postgresSource 'vendor PostgreSQL runtime'
Require-File $caddyExe 'vendor Caddy executable'
Require-File $winswExe 'vendor WinSW executable'
$pgDump = Join-Path $postgresSource 'bin\pg_dump.exe'
$psql = Join-Path $postgresSource 'bin\psql.exe'
Require-File $pgDump 'PostgreSQL pg_dump.exe'
Require-File $psql 'PostgreSQL psql.exe'
foreach ($name in @('postgres', 'pg_ctl', 'initdb', 'pg_restore')) { Require-File (Join-Path $postgresSource "bin\$name.exe") $name }
Require-File (Join-Path $VendorRoot 'caddy\LICENSE') 'Caddy license'
Require-File (Join-Path $VendorRoot 'winsw\LICENSE.txt') 'WinSW license'
Require-File (Join-Path $postgresSource 'server_license.txt') 'PostgreSQL license'
$vcRedist = Join-Path $VendorRoot 'vc_redist.x64.exe'
Require-File $vcRedist 'VC++ Redistributable x64 installer'
$vcSignature = Get-AuthenticodeSignature -LiteralPath $vcRedist
if ($vcSignature.Status -ne 'Valid' -or $vcSignature.SignerCertificate.Subject -notmatch 'Microsoft') { Fail 'VC++ Redistributable is not signed by Microsoft.' }
$expectedPython = (Get-Content -Raw -LiteralPath (Join-Path $SourceRoot '.python-version')).Trim()
$actualPython = (& $pythonExe -c 'import platform; print(platform.python_version())').Trim()
if ($actualPython -ne $expectedPython) { Fail "vendor Python version mismatch: expected $expectedPython, got $actualPython" }
$bits = ([string](& $pythonExe -I -c 'import struct; print(struct.calcsize(chr(80))*8)')).Trim()
if ($LASTEXITCODE -ne 0 -or $bits -ne '64') { Fail 'Vendor Python must be x64.' }
$pgVersion = (& (Join-Path $postgresSource 'bin\postgres.exe') --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Fail 'PostgreSQL runtime cannot execute.' }
$caddyVersion = (& $caddyExe version | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Fail 'Caddy runtime cannot execute.' }

$outputDirectory = Split-Path -Parent $Output
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$stage = Join-Path $outputDirectory ('.offline-build-' + [Guid]::NewGuid().ToString('N'))
$bundle = Join-Path $stage "simworkbench-windows-offline-$ReleaseId"
try {
    New-Item -ItemType Directory -Force -Path $bundle | Out-Null
    Write-Host 'Verifying the complete wheelhouse in a fresh environment without network access...' -ForegroundColor Cyan
    $validationEnv = Join-Path $stage 'validation-venv'
    & $pythonExe -I -m venv $validationEnv
    if ($LASTEXITCODE -ne 0) { Fail 'Could not create offline validation venv.' }
    $validationPython = Join-Path $validationEnv 'Scripts\python.exe'
    & $validationPython -I -m pip --isolated install --no-index --only-binary=:all: --disable-pip-version-check --find-links $wheelhouse -r (Join-Path $SourceRoot 'backend\requirements.lock')
    if ($LASTEXITCODE -ne 0) { Fail 'The wheelhouse cannot satisfy the lock file offline on Windows.' }
    & $validationPython -I -m pip --isolated check
    if ($LASTEXITCODE -ne 0) { Fail 'Offline dependency consistency check failed.' }
    & $validationPython -I -c 'import fastapi,uvicorn,psycopg,sqlalchemy,alembic,duckdb,cryptography,ijson'
    if ($LASTEXITCODE -ne 0) { Fail 'Offline native dependency import failed.' }
    $releaseBackend = Join-Path $bundle "release\$ReleaseId\backend"
    New-Item -ItemType Directory -Force -Path $releaseBackend | Out-Null
    foreach ($directory in @('app', 'migrations', 'scripts', 'public_assets')) {
        $sourceDirectory = Join-Path $SourceRoot "backend\$directory"
        if (Test-Path -LiteralPath $sourceDirectory -PathType Container) { Copy-Tree $sourceDirectory (Join-Path $releaseBackend $directory) }
    }
    foreach ($fileName in @('alembic.ini', 'requirements.txt', 'requirements.lock')) { Copy-Item -LiteralPath (Join-Path $SourceRoot "backend\$fileName") -Destination $releaseBackend -Force }
    Copy-Tree $frontendDist (Join-Path $bundle "release\$ReleaseId\frontend\dist")
    Copy-Item -LiteralPath (Join-Path $SourceRoot '.python-version') -Destination (Join-Path $bundle "release\$ReleaseId")
    Get-ChildItem -LiteralPath (Join-Path $bundle "release\$ReleaseId") -Recurse -File |
        Where-Object { $_.Extension -in @('.pyc', '.log') -or $_.Name -eq '.env' -or $_.Name -eq '.postgres-owner.env' -or $_.Name -like '*.db' } |
        Remove-Item -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $bundle 'runtime') | Out-Null
    Copy-Tree $pythonSource (Join-Path $bundle 'runtime\python')
    Copy-Tree $postgresSource (Join-Path $bundle 'runtime\postgresql')
    New-Item -ItemType Directory -Force -Path (Join-Path $bundle 'runtime\caddy'), (Join-Path $bundle 'runtime\winsw'), (Join-Path $bundle 'runtime\wheelhouse') | Out-Null
    Copy-Item -LiteralPath $caddyExe -Destination (Join-Path $bundle 'runtime\caddy\caddy.exe')
    if (Test-Path -LiteralPath (Join-Path $VendorRoot 'caddy\LICENSE')) { Copy-Item -LiteralPath (Join-Path $VendorRoot 'caddy\LICENSE') -Destination (Join-Path $bundle 'runtime\caddy\LICENSE') }
    Copy-Item -LiteralPath $winswExe -Destination (Join-Path $bundle 'runtime\winsw\WinSW-x64.exe')
    Copy-Item -LiteralPath (Join-Path $VendorRoot 'winsw\LICENSE.txt') -Destination (Join-Path $bundle 'runtime\winsw\LICENSE.txt')
    Copy-Tree $wheelhouse (Join-Path $bundle 'runtime\wheelhouse')
    Copy-Item -LiteralPath $vcRedist -Destination (Join-Path $bundle 'runtime\vc_redist.x64.exe')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\..\deploy\windows\offline\install.ps1') -Destination $bundle
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\..\deploy\windows\offline\install.cmd') -Destination $bundle
    Copy-Item -LiteralPath (Join-Path $SourceRoot 'deploy\windows\offline\README.md') -Destination $bundle
    New-Item -ItemType Directory -Force -Path (Join-Path $bundle 'docs') | Out-Null
    foreach ($guide in @('windows-server-offline-installation.md', 'windows-deployment-policy.md')) {
        Copy-Item -LiteralPath (Join-Path $SourceRoot "docs\$guide") -Destination (Join-Path $bundle 'docs')
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $bundle 'config') | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\..\deploy\windows\offline\install.example.json') -Destination (Join-Path $bundle 'config\install.example.json')
    Set-Content -LiteralPath (Join-Path $bundle 'RELEASE_ID') -Value $ReleaseId -Encoding ASCII

    $files = @()
    foreach ($file in Get-ChildItem -LiteralPath $bundle -Recurse -File) {
        if ($file.Name -eq 'bundle-manifest.json') { continue }
        $relative = Get-Relative $bundle $file.FullName
        $files += [ordered]@{ path = $relative; bytes = [int64]$file.Length; sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    $manifest = [ordered]@{
        format = 'simulation-workbench-windows-offline-bundle'
        format_version = 1
        release_id = $ReleaseId
        source = [ordered]@{ revision = $sourceRevision; dirty = [bool]$dirty; frontend_rebuilt = -not [bool]$SkipFrontendBuild }
        versions = [ordered]@{ python = $expectedPython; postgresql = $pgVersion; caddy = $caddyVersion; requirements_sha256 = (Get-FileHash -LiteralPath (Join-Path $SourceRoot 'backend\requirements.lock') -Algorithm SHA256).Hash.ToLowerInvariant() }
        target = [ordered]@{ os = 'Windows Server 2022'; architecture = 'x64'; network = 'none' }
        payload = [ordered]@{ python = 'runtime/python'; postgresql = 'runtime/postgresql'; caddy = 'runtime/caddy/caddy.exe'; winsw = 'runtime/winsw/WinSW-x64.exe'; frontend = "release/$ReleaseId/frontend/dist" }
        files = @($files | Sort-Object path)
    }
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $bundle 'bundle-manifest.json') -Encoding UTF8
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($bundle, $Output, [IO.Compression.CompressionLevel]::Optimal, $true)
    $hash = (Get-FileHash -LiteralPath $Output -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath ($Output + '.sha256') -Value ("$hash  " + (Split-Path -Leaf $Output)) -Encoding ASCII
    Write-Host "Windows offline bundle created: $Output" -ForegroundColor Green
    Write-Host "Transfer checksum: $Output.sha256"
    if ($InnoSetupCompiler) {
        Require-File $InnoSetupCompiler 'Inno Setup compiler'
        $template = Join-Path $SourceRoot 'deploy\windows\offline\bundle-setup.iss'
        & $InnoSetupCompiler "/DBundleDir=$bundle" "/DReleaseId=$ReleaseId" "/DOutputDir=$outputDirectory" $template
        if ($LASTEXITCODE -ne 0) { Fail 'Inno Setup compilation failed.' }
        $exe = Join-Path $outputDirectory "simworkbench-windows-offline-$ReleaseId.exe"
        Require-File $exe 'compiled installer'
        Set-Content -LiteralPath ($exe + '.sha256') -Value ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + (Split-Path -Leaf $exe)) -Encoding ASCII
        Write-Host "Offline installer created: $exe" -ForegroundColor Green
    }
    if ($KeepStaging) { Write-Host "Staged bundle: $bundle" }
}
finally {
    if (-not $KeepStaging -and (Test-Path -LiteralPath $stage)) {
        $resolvedStage = (Resolve-Path -LiteralPath $stage).Path
        if (-not $resolvedStage.StartsWith($outputDirectory.TrimEnd('\') + '\.offline-build-', [StringComparison]::OrdinalIgnoreCase)) { Fail 'Build cleanup path validation failed.' }
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
