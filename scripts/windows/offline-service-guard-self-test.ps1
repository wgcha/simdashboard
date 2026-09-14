[CmdletBinding()]
param()

# Extract the installer guards so this test never touches real Windows services.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$installer = Join-Path $root 'deploy\windows\offline\install.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors) { throw 'offline installer did not parse' }
foreach ($name in @('Fail', 'Get-OwnedService', 'Get-DatabaseIdentity', 'Assert-DatabaseConnectionPair')) {
    $definition = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true) | Select-Object -First 1
    if (-not $definition) { throw "missing installer function: $name" }
    Invoke-Expression $definition.Extent.Text
}

$script:expectedExe = Join-Path $root 'state\services\SimulationWorkbenchApi.exe'
$script:fakeService = [pscustomobject]@{ PathName = '"' + $script:expectedExe + '"'; StartName = 'NT AUTHORITY\LocalService' }
function Get-CimInstance { return $script:fakeService }
function Assert-Fails([scriptblock]$Action, [string]$Case) {
    try { & $Action; throw "service guard accepted $Case" }
    catch { if ($_.Exception.Message -like "service guard accepted*") { throw } }
}

if (-not (Get-OwnedService 'SimulationWorkbenchApi' $script:expectedExe '' 'NT AUTHORITY\LocalService')) { throw 'owned service was not accepted' }
$script:fakeService = [pscustomobject]@{ PathName = '"C:\OtherApp\service.exe"'; StartName = 'NT AUTHORITY\LocalService' }
Assert-Fails { Get-OwnedService 'SimulationWorkbenchApi' $script:expectedExe '' 'NT AUTHORITY\LocalService' } 'foreign executable'
$script:fakeService = [pscustomobject]@{ PathName = '"' + $script:expectedExe + '"'; StartName = 'LocalSystem' }
Assert-Fails { Get-OwnedService 'SimulationWorkbenchApi' $script:expectedExe '' 'NT AUTHORITY\LocalService' } 'foreign account'
$expectedData = Join-Path $root 'state\postgres\data'
$script:fakeService = [pscustomobject]@{ PathName = '"' + $script:expectedExe + '" runservice -N "SimulationWorkbenchPostgreSQL" -D "' + $expectedData + '"'; StartName = 'LocalSystem' }
if (-not (Get-OwnedService 'SimulationWorkbenchPostgreSQL' $script:expectedExe $expectedData)) { throw 'owned PostgreSQL service was not accepted' }
$script:fakeService = [pscustomobject]@{ PathName = '"' + $script:expectedExe + '" runservice -N "SimulationWorkbenchPostgreSQL" -D "' + $expectedData + '-foreign"'; StartName = 'LocalSystem' }
Assert-Fails { Get-OwnedService 'SimulationWorkbenchPostgreSQL' $script:expectedExe $expectedData } 'PostgreSQL data prefix collision'
$script:fakeService = [pscustomobject]@{ PathName = '"' + $script:expectedExe + '" runservice -N "OtherService" -D "' + $expectedData + '"'; StartName = 'LocalSystem' }
Assert-Fails { Get-OwnedService 'SimulationWorkbenchPostgreSQL' $script:expectedExe $expectedData } 'foreign PostgreSQL registered name'
$script:fakeService = $null
if ($null -ne (Get-OwnedService 'SimulationWorkbenchApi' $script:expectedExe '' 'NT AUTHORITY\LocalService')) { throw 'absent service was not treated as absent' }

$appUrl = 'postgresql+psycopg://simdashboard_app:app-secret@127.0.0.1:55432/simulation_dashboard'
$ownerUrl = 'postgresql://simdashboard_owner:owner-secret@127.0.0.1:55432/simulation_dashboard'
Assert-DatabaseConnectionPair $appUrl $ownerUrl 'bundled' 55432
Assert-DatabaseConnectionPair 'postgresql://app:secret@db.example:5432/simulation_dashboard' 'postgresql+psycopg://owner:secret@db.example/simulation_dashboard' 'existing' 55432
Assert-DatabaseConnectionPair 'postgresql://app:secret@db.example:5432/simulation_dashboard?sslmode=require' 'postgresql://owner:secret@db.example:5432/simulation_dashboard?sslmode=require' 'existing' 55432
Assert-DatabaseConnectionPair '' '' 'existing' 55432
Assert-Fails { Assert-DatabaseConnectionPair $appUrl '' 'bundled' 55432 } 'missing owner URL'
Assert-Fails { Assert-DatabaseConnectionPair '' $ownerUrl 'bundled' 55432 } 'missing app URL'
Assert-Fails { Assert-DatabaseConnectionPair $appUrl 'postgresql://owner:secret@localhost:55432/simulation_dashboard' 'bundled' 55432 } 'different database host'
Assert-Fails { Assert-DatabaseConnectionPair $appUrl 'postgresql://owner:secret@127.0.0.1:55433/simulation_dashboard' 'bundled' 55432 } 'different database port'
Assert-Fails { Assert-DatabaseConnectionPair $appUrl 'postgresql://owner:secret@127.0.0.1:55432/other_database' 'bundled' 55432 } 'different database name'
Assert-Fails { Assert-DatabaseConnectionPair 'postgresql://app:secret@db.example:55432/simulation_dashboard' 'postgresql://owner:secret@db.example:55432/simulation_dashboard' 'bundled' 55432 } 'bundled external host'
Assert-Fails { Assert-DatabaseConnectionPair 'postgresql://app:secret@127.0.0.1:5432/simulation_dashboard' 'postgresql://owner:secret@127.0.0.1:5432/simulation_dashboard' 'bundled' 55432 } 'bundled wrong port'
Assert-Fails { Assert-DatabaseConnectionPair 'postgresql://app:secret@127.0.0.1:55432/other_database' 'postgresql://owner:secret@127.0.0.1:55432/other_database' 'bundled' 55432 } 'bundled wrong database'
Assert-Fails { Assert-DatabaseConnectionPair 'postgresql://app:secret@127.0.0.1:55432/Simulation_Dashboard' 'postgresql://owner:secret@127.0.0.1:55432/Simulation_Dashboard' 'bundled' 55432 } 'bundled case-distinct database'
Assert-Fails { Assert-DatabaseConnectionPair 'postgresql://app:secret@127.0.0.1:55432/simulation_dashboard?host=other.example' $ownerUrl 'bundled' 55432 } 'query-override host'
Assert-Fails { Assert-DatabaseConnectionPair 'postgresql://app:secret@127.0.0.1:55432/simulation_dashboard?h%6fst=other.example' $ownerUrl 'bundled' 55432 } 'encoded query-override host'
Assert-Fails { Assert-DatabaseConnectionPair 'postgresql://app:secret@127.0.0.1:55432/simulation_dashboard?port=5432' $ownerUrl 'bundled' 55432 } 'query-override port'
Assert-Fails { Assert-DatabaseConnectionPair 'postgresql://app:secret@127.0.0.1:55432/simulation_dashboard?dbname=other' $ownerUrl 'bundled' 55432 } 'query-override database'
Write-Host 'Windows offline service ownership self-test passed.' -ForegroundColor Green
