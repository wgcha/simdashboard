[CmdletBinding()]
param()

# Exercise the source PostgreSQL selection wrapper with fixture executables.
# No PostgreSQL server, roles, or project .env is touched by this test.
$ErrorActionPreference = 'Stop'
$SourceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$PowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$fixtures = @()

function Write-FixtureFile([string]$Path, [string]$Contents) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [IO.File]::WriteAllText($Path, $Contents, (New-Object Text.UTF8Encoding($false)))
}
function Assert-True($Condition, [string]$Message) { if (-not $Condition) { throw "SELF-TEST FAILED: $Message" } }
function New-Fixture([string]$Name, [string]$Environment = '') {
    $base = Join-Path ([IO.Path]::GetTempPath()) ('workbench-source-postgres-' + [Guid]::NewGuid().ToString('N'))
    $root = Join-Path $base $Name
    $log = Join-Path $base 'python.log'
    New-Item -ItemType Directory -Path $root | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $root 'scripts\windows') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $SourceRoot 'scripts\windows\initialize-source-postgres.ps1') -Destination (Join-Path $root 'scripts\windows\initialize-source-postgres.ps1')
    if ($Environment) { Write-FixtureFile (Join-Path $root '.env') $Environment }
    Write-FixtureFile (Join-Path $root 'backend\scripts\setup_local_postgres.py') @'
import os
import sys
with open(os.environ["WORKBENCH_SOURCE_POSTGRES_LOG"], "a", encoding="utf-8") as stream:
    stream.write(" ".join(sys.argv[1:]) + "\n")
'@
    Write-FixtureFile (Join-Path $root 'backend\scripts\source_deployment_database_config.py') @'
import json
import os
print(json.dumps({"backend": os.environ["WORKBENCH_SOURCE_POSTGRES_BACKEND"], "has_database_url": os.environ["WORKBENCH_SOURCE_POSTGRES_HAS_URL"] == "true"}))
'@
    return [pscustomobject]@{
        Base=$base; Root=$root; Log=$log
        Backend = if ($Environment -match 'ANALYSIS_DB_BACKEND=duckdb') { 'duckdb' } else { 'postgresql' }
        HasDatabaseUrl = [bool]($Environment -match 'DATABASE_URL=')
    }
}
function Invoke-Fixture($Fixture, [switch]$EnvironmentCreated, [switch]$NonInteractive, [string]$AdminUrl = '') {
    $env:WORKBENCH_SOURCE_POSTGRES_LOG = $Fixture.Log
    $env:WORKBENCH_SOURCE_POSTGRES_BACKEND = $Fixture.Backend
    $env:WORKBENCH_SOURCE_POSTGRES_HAS_URL = $Fixture.HasDatabaseUrl.ToString().ToLowerInvariant()
    if ($AdminUrl) { $env:POSTGRES_ADMIN_URL = $AdminUrl } else { Remove-Item Env:POSTGRES_ADMIN_URL -ErrorAction SilentlyContinue }
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Fixture.Root 'scripts\windows\initialize-source-postgres.ps1'), '-PythonPath', (Join-Path $SourceRoot '.venv-runtime\Scripts\python.exe'))
    if ($EnvironmentCreated) { $arguments += '-EnvironmentCreated' }
    if ($NonInteractive) { $arguments += '-NonInteractive' }
    & $PowerShell @arguments | Out-Host
    return [int]$LASTEXITCODE
}

function Test-AdministratorPrompt {
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $SourceRoot 'scripts\windows\initialize-source-postgres.ps1'),
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors.Count) { throw "Initializer did not parse: $($errors[0].Message)" }
    $definition = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-AdministratorUrl' }, $true)
    if (-not $definition) { throw 'Get-AdministratorUrl was not found.' }
    $global:WorkBenchPromptAnswers = New-Object System.Collections.Generic.Queue[string]
    @('db.internal', '5433', 'admin user') | ForEach-Object { $global:WorkBenchPromptAnswers.Enqueue($_) }
    function global:Read-Host {
        param([string]$Prompt, [switch]$AsSecureString)
        if ($AsSecureString) { return (ConvertTo-SecureString 'p@ss word' -AsPlainText -Force) }
        return $global:WorkBenchPromptAnswers.Dequeue()
    }
    try {
        $NonInteractive = $false
        . ([scriptblock]::Create($definition.Extent.Text))
        $url = Get-AdministratorUrl
        Assert-True ($url -eq 'postgresql://admin%20user:p%40ss%20word@db.internal:5433/postgres') 'interactive administrator prompt did not preserve hidden-password URL encoding'
    }
    finally {
        Remove-Item Function:\global:Read-Host -ErrorAction SilentlyContinue
        Remove-Variable WorkBenchPromptAnswers -Scope Global -ErrorAction SilentlyContinue
    }
}

try {
    Test-AdministratorPrompt
    $fresh = New-Fixture -Name 'fresh' -Environment 'ANALYSIS_DB_BACKEND=duckdb'
    $fixtures += $fresh
    Assert-True ((Invoke-Fixture $fresh -EnvironmentCreated -NonInteractive -AdminUrl 'postgresql://admin:secret@127.0.0.1:5432/postgres') -eq 0) 'generated environment did not provision PostgreSQL'
    $freshInvocation = (Get-Content -Raw -LiteralPath $fresh.Log).Trim()
    Assert-True (($freshInvocation -match '--seed-mode empty') -and ($freshInvocation -notmatch '--replace-existing')) 'fresh provisioning did not use the empty, non-replacement setup mode'

    $existing = New-Fixture -Name 'existing' -Environment "ANALYSIS_DB_BACKEND=postgresql`nDATABASE_URL=postgresql://simdashboard_app:secret@127.0.0.1:5432/simulation_dashboard"
    $fixtures += $existing
    Assert-True ((Invoke-Fixture $existing -NonInteractive) -eq 0) 'existing PostgreSQL configuration was not retained'
    Assert-True (-not (Test-Path -LiteralPath $existing.Log)) 'existing PostgreSQL configuration invoked a provisioning command'

    $duckdb = New-Fixture -Name 'duckdb' -Environment 'ANALYSIS_DB_BACKEND=duckdb'
    $fixtures += $duckdb
    Assert-True ((Invoke-Fixture $duckdb -NonInteractive) -eq 0) 'existing DuckDB selection was not preserved'
    Assert-True (-not (Test-Path -LiteralPath $duckdb.Log)) 'existing DuckDB selection invoked a provisioning command'

    $missingAdmin = New-Fixture -Name 'missing-admin' -Environment 'ANALYSIS_DB_BACKEND=duckdb'
    $fixtures += $missingAdmin
    Assert-True ((Invoke-Fixture $missingAdmin -EnvironmentCreated -NonInteractive) -ne 0) 'non-interactive provisioning accepted a missing administrator URL'
    Assert-True (-not (Test-Path -LiteralPath $missingAdmin.Log)) 'missing administrator URL invoked a provisioning command'

    $legacyData = New-Fixture -Name 'legacy-data' -Environment 'ANALYSIS_DB_BACKEND=postgresql'
    $fixtures += $legacyData
    Write-FixtureFile (Join-Path $legacyData.Root 'backend\data\analysis_dashboard.duckdb') 'legacy data marker'
    Assert-True ((Invoke-Fixture $legacyData -NonInteractive -AdminUrl 'postgresql://admin:secret@127.0.0.1:5432/postgres') -ne 0) 'unconfigured PostgreSQL deployment ignored legacy DuckDB data'
    Assert-True (-not (Test-Path -LiteralPath $legacyData.Log)) 'legacy DuckDB protection invoked a provisioning command'

    Write-Host 'Windows source PostgreSQL initializer self-test passed.' -ForegroundColor Green
}
finally {
    Remove-Item Env:WORKBENCH_SOURCE_POSTGRES_LOG -ErrorAction SilentlyContinue
    Remove-Item Env:WORKBENCH_SOURCE_POSTGRES_BACKEND -ErrorAction SilentlyContinue
    Remove-Item Env:WORKBENCH_SOURCE_POSTGRES_HAS_URL -ErrorAction SilentlyContinue
    Remove-Item Env:POSTGRES_ADMIN_URL -ErrorAction SilentlyContinue
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    foreach ($fixture in $fixtures) {
        if ($fixture -and (Test-Path -LiteralPath $fixture.Base)) {
            $resolved = [IO.Path]::GetFullPath($fixture.Base).TrimEnd('\')
            if ((Split-Path -Parent $resolved).TrimEnd('\') -ieq $tempRoot -and (Split-Path -Leaf $resolved).StartsWith('workbench-source-postgres-', [StringComparison]::OrdinalIgnoreCase)) {
                Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
}
