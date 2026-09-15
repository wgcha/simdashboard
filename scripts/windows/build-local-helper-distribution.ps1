[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')]
    [string]$Version,
    [string]$OutputDirectory = '',
    [switch]$InstallBuildDependency
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Python = Join-Path $Root '.venv-runtime\Scripts\python.exe'
$Uv = Join-Path $Root '.tools\uv\uv.exe'
$env:UV_CACHE_DIR = Join-Path $Root '.tools\uv-cache'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Build Python was not found: $Python" }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $Root 'dist\local-helper\windows-x64' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$ExpectedOutput = [IO.Path]::GetFullPath((Join-Path $Root 'dist\local-helper\windows-x64'))
if ($OutputDirectory -ne $ExpectedOutput) { throw "OutputDirectory must be the release location: $ExpectedOutput" }
function Assert-WorkspaceChild([string]$Path, [string]$Parent, [string]$Name) {
    $resolvedPath = [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $resolvedParent = [IO.Path]::GetFullPath($Parent).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedPath.StartsWith($resolvedParent, [StringComparison]::OrdinalIgnoreCase)) { throw "$Name must remain below $resolvedParent" }
}

& $Python -c 'import PyInstaller' 2>$null
if ($LASTEXITCODE -ne 0) {
    if (-not $InstallBuildDependency) {
        throw 'PyInstaller is required only on the Windows release builder. Run again with -InstallBuildDependency to install the pinned build dependency into .venv-runtime.'
    }
    if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) { throw "uv was not found: $Uv" }
    # PyInstaller's official documentation recommends invoking the installed
    # module with the intended Python environment.  This project uses uv to
    # keep that dependency inside the release-builder virtual environment.
    & $Uv pip install --python $Python 'pyinstaller==6.22.2'
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed (exit code $LASTEXITCODE)." }
}

$BuildRoot = Join-Path $Root 'dist\local-helper\.build'
$PyInstallerDist = Join-Path $BuildRoot 'dist'
$PyInstallerWork = Join-Path $BuildRoot 'work'
$PyInstallerSpec = Join-Path $BuildRoot 'spec'
$Stage = Join-Path $BuildRoot 'stage'
$ArchiveName = "SimulationWorkbenchLocalHelper-$Version-windows-x64.zip"
$Archive = Join-Path $OutputDirectory $ArchiveName
Assert-WorkspaceChild -Path $BuildRoot -Parent (Join-Path $Root 'dist\local-helper') -Name 'BuildRoot'
Assert-WorkspaceChild -Path $Stage -Parent $BuildRoot -Name 'Stage'
Assert-WorkspaceChild -Path $Archive -Parent $OutputDirectory -Name 'Archive'
foreach ($directory in @($BuildRoot, $OutputDirectory)) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
foreach ($directory in @($PyInstallerDist, $PyInstallerWork, $PyInstallerSpec, $Stage)) {
    if (Test-Path -LiteralPath $directory) { Remove-Item -LiteralPath $directory -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

Push-Location $Root
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --name SimulationWorkbenchLocalHelper `
        --distpath $PyInstallerDist --workpath $PyInstallerWork --specpath $PyInstallerSpec `
        --paths $Root --collect-all fastapi --collect-all starlette --collect-all uvicorn --collect-all pydantic `
        --collect-submodules uvicorn --hidden-import tkinter --hidden-import tkinter.filedialog `
        (Join-Path $Root 'scripts\windows\local_helper_entry.py')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed (exit code $LASTEXITCODE)." }
}
finally { Pop-Location }

$Runner = Join-Path $PyInstallerDist 'SimulationWorkbenchLocalHelper.exe'
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) { throw 'PyInstaller did not produce SimulationWorkbenchLocalHelper.exe.' }
& $Runner --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'The frozen local helper did not pass its --help smoke test.' }
$smokeData = Join-Path $BuildRoot 'smoke-data'
Assert-WorkspaceChild -Path $smokeData -Parent $BuildRoot -Name 'SmokeData'
$listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
$listener.Start()
try { $smokePort = $listener.LocalEndpoint.Port } finally { $listener.Stop() }
if (@(Get-Process -Name SimulationWorkbenchLocalHelper -ErrorAction SilentlyContinue | Where-Object Path -eq $Runner).Count -gt 0) { throw 'The build executable is already running. Stop the prior build smoke test first.' }
if (Test-Path -LiteralPath $smokeData) { Remove-Item -LiteralPath $smokeData -Recurse -Force }
New-Item -ItemType Directory -Force -Path $smokeData | Out-Null
$smoke = Start-Process -FilePath $Runner -ArgumentList @('--data-dir', ('"' + $smokeData + '"'), '--port', "$smokePort", '--server-url', 'http://127.0.0.1:18000') -WindowStyle Hidden -PassThru
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(12); $identity = $null
    while ([DateTime]::UtcNow -lt $deadline -and $null -eq $identity) {
        try { $identity = Invoke-RestMethod -Uri "http://127.0.0.1:$smokePort/v1/identity" -TimeoutSec 1 -ErrorAction Stop } catch { Start-Sleep -Milliseconds 250 }
    }
    if ($null -eq $identity -or $identity.managed -ne $true -or [string]::IsNullOrWhiteSpace([string]$identity.device_id)) { throw 'Frozen helper did not expose a managed loopback identity.' }
} finally {
    # Both the one-file bootloader and its child use this exact build path.
    # The pre-check above proved no such executable was running before us.
    foreach ($smokeProcess in @(Get-Process -Name SimulationWorkbenchLocalHelper -ErrorAction SilentlyContinue | Where-Object Path -eq $Runner)) { Stop-Process -Id $smokeProcess.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 300
    if (@(Get-Process -Name SimulationWorkbenchLocalHelper -ErrorAction SilentlyContinue | Where-Object Path -eq $Runner).Count -gt 0) { throw 'Frozen helper smoke-test process did not stop.' }
    Remove-Item -LiteralPath $smokeData -Recurse -Force -ErrorAction SilentlyContinue
}
& $Runner --picker-helper --kind not-a-picker 2>$null
if ($LASTEXITCODE -eq 0) { throw 'Frozen picker helper accepted an invalid picker kind.' }

Copy-Item -LiteralPath $Runner -Destination (Join-Path $Stage 'SimulationWorkbenchLocalHelper.exe')
Copy-Item -LiteralPath (Join-Path $Root 'start-local-runner.ps1') -Destination (Join-Path $Stage 'start-local-runner.ps1')
Copy-Item -LiteralPath (Join-Path $Root 'start-local-runner.bat') -Destination (Join-Path $Stage 'start-local-runner.bat')

$files = Get-ChildItem -LiteralPath $Stage -File | Sort-Object Name | ForEach-Object {
    [ordered]@{
        path = $_.Name
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        size_bytes = [int64]$_.Length
    }
}
$artifactManifest = [ordered]@{ format = 1; version = $Version; files = @($files) }
$artifactManifestPath = Join-Path $Stage 'artifact-manifest.json'
[IO.File]::WriteAllText($artifactManifestPath, ($artifactManifest | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding($false)))

if (Test-Path -LiteralPath $Archive) { Remove-Item -LiteralPath $Archive -Force }
Compress-Archive -LiteralPath (Get-ChildItem -LiteralPath $Stage -File | Select-Object -ExpandProperty FullName) -DestinationPath $Archive -CompressionLevel Optimal
$size = [int64](Get-Item -LiteralPath $Archive).Length
$hash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
$distributionManifest = [ordered]@{
    format = 1
    version = $Version
    filename = $ArchiveName
    sha256 = $hash
    size_bytes = $size
    released_at = [DateTime]::UtcNow.ToString('o')
}
$publicManifest = Join-Path $OutputDirectory 'distribution-manifest.json'
$temporaryManifest = "$publicManifest.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
$previousManifest = "$temporaryManifest.previous"
[IO.File]::WriteAllText($temporaryManifest, ($distributionManifest | ConvertTo-Json -Depth 3), (New-Object System.Text.UTF8Encoding($false)))
try {
    # Publish metadata only after its versioned ZIP has been completed and
    # hashed. File.Replace keeps an existing server-side reader from seeing a
    # partially written manifest; the first publication is an atomic rename.
    if (Test-Path -LiteralPath $publicManifest -PathType Leaf) { [IO.File]::Replace($temporaryManifest, $publicManifest, $previousManifest) }
    else { Move-Item -LiteralPath $temporaryManifest -Destination $publicManifest }
} finally { Remove-Item -LiteralPath $temporaryManifest, $previousManifest -Force -ErrorAction SilentlyContinue }
Write-Host "Ready: $Archive" -ForegroundColor Green
Write-Host "SHA-256: $hash" -ForegroundColor Green
exit 0
