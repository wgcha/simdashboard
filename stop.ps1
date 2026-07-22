$ErrorActionPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root '.server-pids.json'

function Stop-ProcessTree([int]$ProcessId) {
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" | ForEach-Object {
        Stop-ProcessTree -ProcessId $_.ProcessId
    }
    if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        Stop-Process -Id $ProcessId -Force
    }
}

if (Test-Path -LiteralPath $PidFile) {
    $serverPids = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json
    @($serverPids.backend, $serverPids.frontend) | ForEach-Object {
        if ($_) { Stop-ProcessTree -ProcessId $_ }
    }
    Remove-Item -LiteralPath $PidFile
}

Write-Host 'Analysis Canvas 서버를 종료했습니다.'
