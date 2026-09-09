[CmdletBinding(DefaultParameterSetName = 'Offline')]
param(
    [Parameter(ParameterSetName = 'Offline', Mandatory = $true)][string]$LocalHelperDistributionSource,
    [Parameter(ParameterSetName = 'Online', Mandatory = $true)][Uri]$LocalHelperManifestUrl,
    [string]$TargetDirectory = '',
    [string]$ProjectRoot = ''
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'InstallLocalHelperDistribution.psm1') -Force
try {
    if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
    $ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
    if (-not $TargetDirectory) {
        # Ask the server's own Python configuration to resolve .env and
        # backend/.env exactly as the distribution API does.  Do not duplicate
        # dotenv precedence in PowerShell.
        Import-Module (Join-Path $PSScriptRoot 'Runtime.psm1') -Force
        $python = Get-ProjectPython
        $code = 'import sys; from pathlib import Path; root=Path(sys.argv[1]); sys.path.insert(0, str(root / "backend")); import app.config; from app.services.local_helper_distribution import distribution_directory; print(distribution_directory())'
        $TargetDirectory = (& $python -c $code $ProjectRoot | Select-Object -Last 1).Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($TargetDirectory)) { throw 'Could not resolve the server distribution directory.' }
    }
    $arguments = @{ TargetDirectory = $TargetDirectory }
    if ($PSCmdlet.ParameterSetName -eq 'Online') { $arguments.ManifestUrl = $LocalHelperManifestUrl }
    else { $arguments.SourceDirectory = $LocalHelperDistributionSource }
    $result = Install-LocalHelperDistribution @arguments
    Write-Host ("Local helper distribution ready: {0} ({1})" -f $result.Version, $result.Filename) -ForegroundColor Green
    exit 0
}
catch {
    # Do not print URLs, local paths, or request exceptions: this command may
    # run in shared update logs and must never turn credentials into log data.
    Write-Host 'Local helper distribution unavailable. The existing distribution was preserved.' -ForegroundColor Yellow
    exit 1
}
