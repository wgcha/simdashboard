[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$settingsPath = Join-Path $Root 'deploy\windows\network-settings.json'
Import-Module (Join-Path $Root 'scripts\windows\Network.psm1') -Force

$names = @('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'NO_PROXY', 'no_proxy', 'NODE_USE_SYSTEM_CA', 'UV_SYSTEM_CERTS', 'NODE_EXTRA_CA_CERTS', 'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'PIP_CERT', 'CURL_CA_BUNDLE', 'NPM_CONFIG_CAFILE', 'npm_config_cafile', 'SIMDASH_CA_BUNDLE')
$original = @{}
foreach ($name in $names) { $original[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$tempFile = Join-Path ([System.IO.Path]::GetTempPath()) ("simdashboard-network-selftest-$PID.bin")
try {
    $direct = Initialize-DeploymentNetwork -SettingsPath $settingsPath -Mode direct
    if ($direct.ProxySource -ne 'direct' -or $env:HTTP_PROXY -or $env:HTTPS_PROXY) { throw 'Direct mode did not clear process proxy settings.' }
    if ($env:NO_PROXY -notmatch '127\.0\.0\.1' -or $env:NO_PROXY -notmatch 'samsung\.net') { throw 'NO_PROXY normalization is incomplete.' }
    if ($env:NODE_USE_SYSTEM_CA -ne '1' -or $env:UV_SYSTEM_CERTS -ne 'true') { throw 'Windows trust-store settings were not enabled.' }

    $env:HTTPS_PROXY = 'http://user:secret@proxy.example.test:8081'
    Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue
    $existing = Initialize-DeploymentNetwork -SettingsPath $settingsPath -Mode auto
    if ($existing.ProxySource -ne 'existing environment' -or $existing.Proxy -ne 'http://proxy.example.test:8081') { throw 'Existing proxy precedence or masking failed.' }
    if ($env:HTTPS_PROXY -ne 'http://user:secret@proxy.example.test:8081') { throw 'Existing HTTPS proxy was overwritten.' }

    $explicit = Initialize-DeploymentNetwork -SettingsPath $settingsPath -Mode proxy
    if ($explicit.ProxySource -ne 'settings' -or $env:HTTPS_PROXY -ne 'http://168.219.61.252:8080') { throw 'Explicit proxy mode did not override the inherited proxy.' }

    [System.IO.File]::WriteAllText($tempFile, 'verified local download fixture')
    $hash = (Get-FileHash -LiteralPath $tempFile -Algorithm SHA256).Hash
    Invoke-AtomicDownload -Uri 'https://example.invalid/runtime.zip' -Destination $tempFile -ExpectedSha256 $hash | Out-Null
    if (-not (Test-Path -LiteralPath $tempFile -PathType Leaf)) { throw 'Verified existing download was not retained.' }

    Write-Host 'Windows network module self-test passed.' -ForegroundColor Green
}
finally {
    Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
    foreach ($name in $names) {
        if ($null -eq $original[$name]) { Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath "Env:$name" -Value $original[$name] }
    }
}
