[CmdletBinding()]
param()

# Contract test: exercises the builder's negative gates without copying the
# repository or executing vendor binaries. It is intentionally safe on a
# developer workstation and on CI hosts without Server 2022.
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$builder = Join-Path $root 'scripts\windows\build-offline-bundle.ps1'
if (-not (Test-Path -LiteralPath $builder -PathType Leaf)) { throw 'offline bundle builder is missing' }
$help = ''
try { $help = (& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $builder -SourceRoot $root -VendorRoot (Join-Path $root '.missing-offline-vendor') -SkipFrontendBuild -AllowDirty 2>&1 | Out-String) } catch { $help = $_.Exception.Message }
if ($LASTEXITCODE -eq 0 -or (-join $help) -notmatch 'vendor root') { throw 'builder did not reject missing vendor root' }
$installer = Get-Content -Raw -LiteralPath (Join-Path $root 'deploy\windows\offline\install.ps1')
$builderText = Get-Content -Raw -LiteralPath $builder
$setupText = Get-Content -Raw -LiteralPath (Join-Path $root 'deploy\windows\offline\bundle-setup.iss')
if ($setupText -notmatch '(?m)^ArchitecturesInstallIn64BitMode=x64compatible\s*$') { throw 'Installer must launch native x64 PowerShell.' }
foreach ($needle in @('bundle-manifest.json', 'Windows Server 2022', 'network = ''none''', 'prepare_account_deployment.py', 'upgrade_postgres_schema.py', 'vc_redist.x64.exe', 'windows-password-intranet', '--no-index', 'SimulationWorkbenchApi', 'SimulationWorkbenchProxy')) {
    if ($installer -notmatch [regex]::Escape($needle) -and $builderText -notmatch [regex]::Escape($needle)) { throw "offline contract is missing: $needle" }
}
Write-Host 'Windows offline installer contract self-test passed.' -ForegroundColor Green
