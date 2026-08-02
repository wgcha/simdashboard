[CmdletBinding()]
param(
    [ValidateSet('Fresh', 'Transfer', 'Keep')]
    [string]$Mode = '',

    [ValidateSet('Empty', 'Demo')]
    [string]$SeedMode = '',

    [string]$Bundle = '',
    [string]$BackupDir = '',
    [string]$ProxyUrl = '',
    [string]$NoProxy = '',
    [string]$AdminHost = '127.0.0.1',
    [ValidateRange(1, 65535)]
    [int]$AdminPort = 5432,
    [string]$AdminUser = 'postgres',
    [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProxyFile = Join-Path $Root '.setup-proxy.env'
$RecoveryMarker = Join-Path $Root '.setup-recovery-required.json'
$DefaultNoProxy = @('127.0.0.1', 'localhost', '::1')

function Protect-LogText {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return '' }
    return ([string]$Value) -replace '(?i)\b(https?|postgresql(?:\+psycopg)?)://([^\s/:@]+):([^\s/@]+)@', '$1://***:***@'
}

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Read-EnvFile {
    param([string]$Path)
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $values }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
        $parts = $trimmed.Split('=', 2)
        $values[$parts[0].Trim()] = $parts[1]
    }
    return $values
}

function Test-ProxyUrl {
    param([string]$Value)
    if (-not $Value) { return $true }
    if ($Value.Contains("`r") -or $Value.Contains("`n")) { return $false }
    $uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri)) { return $false }
    return $uri.Scheme -in @('http', 'https') -and -not [string]::IsNullOrWhiteSpace($uri.Host)
}

function Test-NoProxyValue {
    param([string]$Value)
    return -not ($Value.Contains("`r") -or $Value.Contains("`n"))
}

function Merge-NoProxy {
    param([string[]]$Values)
    $seen = @{}
    $result = New-Object System.Collections.Generic.List[string]
    foreach ($item in @($DefaultNoProxy) + @($Values)) {
        foreach ($entry in ([string]$item -split ',')) {
            $normalized = $entry.Trim()
            if (-not $normalized) { continue }
            $key = $normalized.ToLowerInvariant()
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $result.Add($normalized)
            }
        }
    }
    return ($result -join ',')
}

function Protect-PrivateFile {
    param([string]$Path)
    if ($env:OS -ne 'Windows_NT') { return }
    $principal = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    if (-not $principal) { throw "현재 Windows 사용자를 확인하지 못해 $Path ACL을 설정할 수 없습니다." }
    & icacls.exe $Path '/inheritance:r' '/grant:r' "${principal}:(R,W)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "$Path 사용자 전용 ACL 설정에 실패했습니다." }
}

function Save-ProxySettings {
    param([string]$Url, [string]$Exceptions)
    $content = New-Object System.Collections.Generic.List[string]
    $content.Add('# setup.ps1 managed file - do not commit')
    if ($Url) {
        $content.Add("HTTP_PROXY=$Url")
        $content.Add("HTTPS_PROXY=$Url")
    }
    $content.Add("NO_PROXY=$Exceptions")
    $temporary = "$ProxyFile.$PID.tmp"
    try {
        Set-Content -LiteralPath $temporary -Value $content -Encoding UTF8
        Protect-PrivateFile -Path $temporary
        Move-Item -LiteralPath $temporary -Destination $ProxyFile -Force
    }
    catch {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Set-ProxyEnvironment {
    param([string]$Url, [string]$Exceptions)
    $proxyNames = @('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'npm_config_proxy', 'npm_config_https_proxy')
    foreach ($name in $proxyNames) {
        if ($Url) { Set-Item -LiteralPath "Env:$name" -Value $Url }
        else { Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue }
    }
    foreach ($name in @('NO_PROXY', 'no_proxy')) {
        Set-Item -LiteralPath "Env:$name" -Value $Exceptions
    }
}

function Invoke-MaskedExternal {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$FailureMessage = '명령 실행에 실패했습니다.'
    )
    & $FilePath @Arguments 2>&1 | ForEach-Object { Write-Host (Protect-LogText $_) }
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) { throw "$FailureMessage (exit code $exitCode)" }
}

function Find-PgTool {
    param([string]$Name)
    $command = Get-Command "$Name.exe" -ErrorAction SilentlyContinue
    if (-not $command) { $command = Get-Command $Name -ErrorAction SilentlyContinue }
    if ($command) { return $command.Source }
    $candidates = @()
    if ($env:POSTGRES_BIN) { $candidates += (Join-Path $env:POSTGRES_BIN "$Name.exe") }
    if ($env:ProgramFiles) {
        $candidates += Get-ChildItem -Path (Join-Path $env:ProgramFiles 'PostgreSQL\*\bin') -Filter "$Name.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName
    }
    return $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}

function Get-PlainText {
    param([Security.SecureString]$SecureValue)
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Get-AdminUrl {
    if ($env:POSTGRES_ADMIN_URL) { return $env:POSTGRES_ADMIN_URL }
    if ($NonInteractive) {
        throw '비대화형 실행은 POSTGRES_ADMIN_URL 환경변수가 필요합니다. 관리자 비밀번호를 명령행 인자로 전달하지 마세요.'
    }
    $hostInput = Read-Host "PostgreSQL 관리자 호스트 [$AdminHost]"
    if ($hostInput) { $script:AdminHost = $hostInput }
    $portInput = Read-Host "PostgreSQL 포트 [$AdminPort]"
    if ($portInput) {
        $parsedPort = 0
        if (-not [int]::TryParse($portInput, [ref]$parsedPort) -or $parsedPort -lt 1 -or $parsedPort -gt 65535) {
            throw 'PostgreSQL 포트는 1~65535 사이의 숫자여야 합니다.'
        }
        $script:AdminPort = $parsedPort
    }
    $userInput = Read-Host "PostgreSQL 관리자 사용자 [$AdminUser]"
    if ($userInput) { $script:AdminUser = $userInput }
    if (-not $AdminHost -or $AdminHost -notmatch '^[A-Za-z0-9._:-]+$') {
        throw 'PostgreSQL 관리자 호스트 형식이 올바르지 않습니다.'
    }
    $securePassword = Read-Host 'PostgreSQL 관리자 비밀번호' -AsSecureString
    $password = Get-PlainText -SecureValue $securePassword
    if (-not $password) { throw 'PostgreSQL 관리자 비밀번호가 필요합니다.' }
    $escapedUser = [Uri]::EscapeDataString($AdminUser)
    $escapedPassword = [Uri]::EscapeDataString($password)
    $hostForUrl = if ($AdminHost.Contains(':') -and -not $AdminHost.StartsWith('[')) { "[$AdminHost]" } else { $AdminHost }
    $url = "postgresql://${escapedUser}:${escapedPassword}@${hostForUrl}:$AdminPort/postgres"
    $password = $null
    return $url
}

try {
    Set-Location -LiteralPath $Root
    if (Test-Path -LiteralPath $RecoveryMarker -PathType Leaf) {
        throw '이전 PostgreSQL 교체가 수동 복구 대기 중입니다. .setup-recovery-required.json을 관리자와 검토하기 전에는 설치를 다시 실행할 수 없습니다.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'setup-windows.bat') -PathType Leaf)) {
        throw 'setup-windows.bat을 찾을 수 없습니다. GitHub 저장소 전체를 다시 내려받으세요.'
    }

    $savedProxy = Read-EnvFile -Path $ProxyFile
    $effectiveProxy = if ($PSBoundParameters.ContainsKey('ProxyUrl')) { $ProxyUrl } elseif ($env:SETUP_PROXY_URL) { $env:SETUP_PROXY_URL } else { [string]$savedProxy['HTTPS_PROXY'] }
    $effectiveNoProxy = if ($PSBoundParameters.ContainsKey('NoProxy')) { $NoProxy } elseif ($env:SETUP_NO_PROXY) { $env:SETUP_NO_PROXY } else { [string]$savedProxy['NO_PROXY'] }

    if (-not $NonInteractive) {
        $displayProxy = if ($effectiveProxy) { Protect-LogText $effectiveProxy } else { '사용 안 함' }
        $proxyInput = Read-Host "회사 HTTP/HTTPS 프록시 URL [$displayProxy] (유지: Enter, 제거: -)"
        if ($proxyInput -eq '-') { $effectiveProxy = '' }
        elseif ($proxyInput) { $effectiveProxy = $proxyInput }
        $displayNoProxy = Merge-NoProxy -Values @($effectiveNoProxy)
        $noProxyInput = Read-Host "프록시 예외 목록 [$displayNoProxy] (쉼표 구분)"
        if ($noProxyInput) { $effectiveNoProxy = $noProxyInput }
    }
    if (-not (Test-ProxyUrl -Value $effectiveProxy)) {
        throw '프록시 URL은 http:// 또는 https:// 형식이어야 하며 올바른 호스트를 포함해야 합니다.'
    }
    if (-not (Test-NoProxyValue -Value $effectiveNoProxy)) {
        throw '프록시 예외 목록에는 줄바꿈을 포함할 수 없습니다.'
    }
    $effectiveNoProxy = Merge-NoProxy -Values @($effectiveNoProxy)
    Save-ProxySettings -Url $effectiveProxy -Exceptions $effectiveNoProxy
    Set-ProxyEnvironment -Url $effectiveProxy -Exceptions $effectiveNoProxy
    Write-Host ("프록시: " + $(if ($effectiveProxy) { Protect-LogText $effectiveProxy } else { '사용 안 함' }))
    Write-Host "NO_PROXY: $effectiveNoProxy"

    Write-Step 'Python 및 프런트엔드 의존성 설치'
    $env:SETUP_NO_PAUSE = '1'
    $setupWindows = Join-Path $Root 'setup-windows.bat'
    Invoke-MaskedExternal -FilePath 'cmd.exe' -Arguments @('/d', '/c', "call `"$setupWindows`"") -FailureMessage '의존성 설치에 실패했습니다.'

    if (-not $Mode) {
        if ($NonInteractive) { throw '비대화형 실행은 -Mode Fresh, Transfer 또는 Keep이 필요합니다.' }
        Write-Host "`nPostgreSQL 처리 모드:"
        Write-Host '  1. Fresh    새 DB 초기화'
        Write-Host '  2. Transfer 전송 번들 이관'
        Write-Host '  3. Keep     기존 DB 유지 및 연결 검증'
        switch (Read-Host '선택 [1-3]') {
            '1' { $Mode = 'Fresh' }
            '2' { $Mode = 'Transfer' }
            '3' { $Mode = 'Keep' }
            default { throw 'PostgreSQL 처리 모드를 올바르게 선택해야 합니다.' }
        }
    }

    $python = Join-Path $Root '.venv-runtime\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { $python = Join-Path $Root '.venv\Scripts\python.exe' }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Python 가상환경을 찾을 수 없습니다.' }
    $env:PYTHONIOENCODING = 'utf-8'

    if ($Mode -eq 'Keep') {
        Write-Step '기존 PostgreSQL 설정 연결 검증'
        Invoke-MaskedExternal -FilePath $python -Arguments @((Join-Path $Root 'backend\scripts\check_postgres_connection.py')) -FailureMessage '기존 PostgreSQL 연결 검증에 실패했습니다.'
    }
    else {
        $pgDump = Find-PgTool -Name 'pg_dump'
        $pgRestore = Find-PgTool -Name 'pg_restore'
        if (-not $pgDump -or -not $pgRestore) {
            throw 'pg_dump와 pg_restore를 찾을 수 없습니다. PostgreSQL bin 폴더를 PATH 또는 POSTGRES_BIN에 추가하세요.'
        }
        $env:POSTGRES_BIN = Split-Path -Parent $pgDump
        $env:POSTGRES_ADMIN_URL = Get-AdminUrl

        if ($Mode -eq 'Fresh') {
            if (-not $SeedMode) {
                if ($NonInteractive) { throw '비대화형 Fresh 실행은 -SeedMode Empty 또는 Demo가 필요합니다.' }
                $seedChoice = Read-Host '초기 데이터 선택 (1: 빈 운영 DB, 2: 데모 데이터) [1]'
                $SeedMode = if ($seedChoice -eq '2') { 'Demo' } else { 'Empty' }
            }
            Write-Step "PostgreSQL 새 DB 초기화 ($SeedMode)"
            $arguments = @((Join-Path $Root 'backend\scripts\setup_local_postgres.py'), '--seed-mode', $SeedMode.ToLowerInvariant())
            if ($BackupDir) { $arguments += @('--backup-dir', $BackupDir) }
            $arguments += '--replace-existing'
            Invoke-MaskedExternal -FilePath $python -Arguments $arguments -FailureMessage 'PostgreSQL 초기화에 실패했습니다.'
        }
        else {
            if (-not $Bundle -and -not $NonInteractive) { $Bundle = Read-Host '전송 번들 폴더 경로' }
            if (-not $Bundle) { throw 'Transfer 모드는 -Bundle 경로가 필요합니다.' }
            $resolvedBundle = (Resolve-Path -LiteralPath $Bundle -ErrorAction Stop).Path
            Write-Step '전송 번들 사전 검증'
            $validateArguments = @((Join-Path $Root 'backend\scripts\postgres_transfer.py'), 'import', $resolvedBundle, '--validate-only')
            Invoke-MaskedExternal -FilePath $python -Arguments $validateArguments -FailureMessage '전송 번들 사전 검증에 실패했습니다.'
            Write-Step 'PostgreSQL 전송 번들 이관'
            $importArguments = @((Join-Path $Root 'backend\scripts\postgres_transfer.py'), 'import', $resolvedBundle, '--replace-existing')
            if ($BackupDir) { $importArguments += @('--backup-dir', $BackupDir) }
            Invoke-MaskedExternal -FilePath $python -Arguments $importArguments -FailureMessage 'PostgreSQL 전송 번들 이관에 실패했습니다.'
        }

        Remove-Item -LiteralPath Env:POSTGRES_ADMIN_URL -ErrorAction SilentlyContinue
        Write-Step '최종 PostgreSQL 연결 검증'
        Invoke-MaskedExternal -FilePath $python -Arguments @((Join-Path $Root 'backend\scripts\check_postgres_connection.py')) -FailureMessage '최종 PostgreSQL 연결 검증에 실패했습니다.'
    }

    Write-Host "`n통합 설치가 완료되었습니다." -ForegroundColor Green
    Write-Host '다음 명령: start-postgresql.bat'
    exit 0
}
catch {
    Remove-Item -LiteralPath Env:POSTGRES_ADMIN_URL -ErrorAction SilentlyContinue
    Write-Host ("`n[ERROR] " + (Protect-LogText $_.Exception.Message)) -ForegroundColor Red
    if (Test-Path -LiteralPath $RecoveryMarker -PathType Leaf) {
        Write-Host '서비스와 재설치를 중단했습니다. .setup-recovery-required.json 및 백업을 관리자와 검토하세요.'
    }
    else {
        Write-Host '서비스는 시작하지 않았습니다. 오류를 해결한 뒤 setup.bat을 다시 실행하세요.'
    }
    exit 1
}
