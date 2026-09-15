[CmdletBinding()]
param()

# Exercise the real identity guards without starting or stopping any process.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tokenStream = $null
$parseErrors = $null

function Import-Guard([string]$Path, [string]$Name) {
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokenStream, [ref]$parseErrors)
    if ($parseErrors) { throw "PowerShell parse failed: $Path" }
    $guard = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $Name }, $true) | Select-Object -First 1
    if (-not $guard) { throw "Guard function missing: $Name" }
    return $guard.Extent.Text
}

function Assert-Guard([bool]$Actual, [bool]$Expected, [string]$Case) {
    if ($Actual -ne $Expected) { throw "Identity guard failed: $Case" }
}

$startPath = Join-Path $root 'start.ps1'
$stopPath = Join-Path $root 'stop.ps1'
Invoke-Expression (Import-Guard $startPath 'Test-CurrentServerRecord')
Invoke-Expression (Import-Guard $startPath 'Test-RecordedProcessExists')
Invoke-Expression (Import-Guard $stopPath 'Test-ContainsWorkspacePath')
Invoke-Expression (Import-Guard $stopPath 'Test-RecordedServerIdentity')

$script:RootPath = [IO.Path]::GetFullPath($root).TrimEnd('\')
$script:fakeStart = [DateTime]::UtcNow
$script:fakeExecutable = Join-Path $root '.venv-runtime\Scripts\python.exe'
$script:fakeCommand = '"' + $script:fakeExecutable + '" -m uvicorn app.main:app --host 127.0.0.1 --port 8000'
$script:fakeIdentity = [pscustomobject]@{ CommandLine = $script:fakeCommand; ExecutablePath = $script:fakeExecutable }
Assert-Guard (Test-ContainsWorkspacePath ($script:RootPath + '-other\python.exe')) $false 'lookalike workspace prefix'

function Get-Process { [pscustomobject]@{ Id = 43991; StartTime = $script:fakeStart; Path = $script:fakeExecutable } }
function Get-CimInstance { $script:fakeIdentity }
function Get-ProcessIdentity { $script:fakeIdentity }
function Test-CurrentServerEndpoint { return $true }

$record = [pscustomobject]@{ id = 43991; port = 8000; startedAtUtc = $script:fakeStart.ToString('o') }
Assert-Guard (Test-CurrentServerRecord $record 8000 backend $script:fakeExecutable) $true 'start current process'
Assert-Guard (Test-RecordedServerIdentity $record 8000 backend) $true 'stop current process'

$wrongStart = [pscustomobject]@{ id = 43991; port = 8000; startedAtUtc = $script:fakeStart.AddMinutes(-1).ToString('o') }
Assert-Guard (Test-CurrentServerRecord $wrongStart 8000 backend $script:fakeExecutable) $false 'start reused PID'
Assert-Guard (Test-RecordedServerIdentity $wrongStart 8000 backend) $false 'stop reused PID'

$legacy = [pscustomobject]@{ id = 43991; port = 8000 }
Assert-Guard (Test-CurrentServerRecord $legacy 8000 backend $script:fakeExecutable) $false 'start legacy PID without time'
Assert-Guard (Test-RecordedServerIdentity $legacy 8000 backend) $false 'stop legacy PID without time'
Assert-Guard (Test-RecordedProcessExists 43991) $true 'start preserves live legacy PID metadata'

$script:fakeCommand = '"C:\OtherApp\python.exe" -m uvicorn app.main:app --port 8000'
$script:fakeIdentity = [pscustomobject]@{ CommandLine = $script:fakeCommand; ExecutablePath = 'C:\OtherApp\python.exe' }
Assert-Guard (Test-CurrentServerRecord $record 8000 backend $script:fakeExecutable) $false 'start foreign executable'
Assert-Guard (Test-RecordedServerIdentity $record 8000 backend) $false 'stop foreign workspace'

Write-Host 'Windows start/stop process identity self-test passed.' -ForegroundColor Green
