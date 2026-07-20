$ErrorActionPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root '.server-pids.json'

if (Test-Path -LiteralPath $PidFile) {
    $serverPids = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json
    @($serverPids.backend, $serverPids.frontend) | ForEach-Object {
        if ($_ -and (Get-Process -Id $_ -ErrorAction SilentlyContinue)) {
            Stop-Process -Id $_
        }
    }
    Remove-Item -LiteralPath $PidFile
}

Write-Host 'Analysis Canvas 서버를 종료했습니다.'
