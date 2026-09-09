[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$startScript = Join-Path $PSScriptRoot 'start.ps1'
& $startScript -DatabaseBackend postgresql
exit $LASTEXITCODE
