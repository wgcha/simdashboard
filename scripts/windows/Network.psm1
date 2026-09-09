Set-StrictMode -Version Latest

function Get-NetworkSettingValue {
    param([object]$Settings, [string]$Name, $Default)
    if ($null -eq $Settings) { return $Default }
    $property = $Settings.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) { return $Default }
    return $property.Value
}

function Get-MaskedNetworkValue {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    try {
        $uri = [Uri]$Value
        $port = if ($uri.IsDefaultPort) { '' } else { ":$($uri.Port)" }
        return "$($uri.Scheme)://$($uri.Host)$port"
    }
    catch {
        return '[invalid network address]'
    }
}

function ConvertTo-MaskedNetworkMessage {
    param([string]$Message)
    if ([string]::IsNullOrWhiteSpace($Message)) { return '' }
    return [regex]::Replace($Message, '(?i)(https?://)[^/\s@]*@', '$1***:***@')
}

function Set-ProcessNetworkVariable {
    param([string]$Name, [AllowNull()][string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
        return
    }
    Set-Item -LiteralPath "Env:$Name" -Value $Value
}

function Get-FirstProcessVariable {
    param([string[]]$Names)
    foreach ($name in $Names) {
        $value = [Environment]::GetEnvironmentVariable($name, 'Process')
        if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
    }
    return ''
}

function Join-NoProxyValues {
    param([string[]]$Values)
    $seen = @{}
    $result = New-Object System.Collections.Generic.List[string]
    foreach ($value in $Values) {
        if ([string]::IsNullOrWhiteSpace($value)) { continue }
        foreach ($item in ($value -split ',')) {
            $normalized = $item.Trim()
            if ([string]::IsNullOrWhiteSpace($normalized)) { continue }
            $key = $normalized.ToLowerInvariant()
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                [void]$result.Add($normalized)
            }
        }
    }
    return ($result -join ',')
}

function Test-ProxyEndpoint {
    param([string]$ProxyUri, [int]$TimeoutMilliseconds = 750)
    try {
        $uri = [Uri]$ProxyUri
        if ($uri.Scheme -notin @('http', 'https') -or [string]::IsNullOrWhiteSpace($uri.Host)) { return $false }
        $port = if ($uri.IsDefaultPort) { if ($uri.Scheme -eq 'https') { 443 } else { 80 } } else { $uri.Port }
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $connect = $client.BeginConnect($uri.Host, $port, $null, $null)
            if (-not $connect.AsyncWaitHandle.WaitOne($TimeoutMilliseconds)) { return $false }
            $client.EndConnect($connect)
            return $client.Connected
        }
        finally {
            $client.Close()
        }
    }
    catch {
        return $false
    }
}

function Test-NoProxyHost {
    param([string]$Uri, [string]$NoProxy)
    try { $hostName = ([Uri]$Uri).Host } catch { return $false }
    foreach ($entry in ($NoProxy -split ',')) {
        $rule = $entry.Trim()
        if (-not $rule) { continue }
        if ($hostName -ieq $rule -or ($rule.StartsWith('.') -and $hostName.EndsWith($rule, [System.StringComparison]::OrdinalIgnoreCase))) { return $true }
        if ($rule.StartsWith('*.') -and $hostName.EndsWith($rule.Substring(1), [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
        if ($rule.Contains('*') -and $hostName -like $rule) { return $true }
        if ($rule -match '^([0-9]+(?:\.[0-9]+){3})/(8|16|24)$') {
            $network = [System.Net.IPAddress]::Parse($matches[1]).GetAddressBytes()
            $address = $null
            if ([System.Net.IPAddress]::TryParse($hostName, [ref]$address) -and $address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork) {
                $bytes = $address.GetAddressBytes(); $prefixBytes = [int]$matches[2] / 8; $matchesNetwork = $true
                for ($index = 0; $index -lt $prefixBytes; $index += 1) { if ($network[$index] -ne $bytes[$index]) { $matchesNetwork = $false; break } }
                if ($matchesNetwork) { return $true }
            }
        }
    }
    return $false
}

function Resolve-OptionalCertificatePath {
    param([string]$CertificatePath, [string]$SettingsPath)
    if (-not [string]::IsNullOrWhiteSpace($CertificatePath)) {
        $resolved = $CertificatePath
        if (-not [System.IO.Path]::IsPathRooted($resolved)) {
            $resolved = Join-Path (Split-Path -Parent $SettingsPath) $resolved
        }
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "Configured CA certificate was not found: $resolved"
        }
        return (Resolve-Path -LiteralPath $resolved).Path
    }

    $settingsDirectory = Split-Path -Parent $SettingsPath
    $candidates = @(Join-Path $settingsDirectory 'certs\DigitalCity.crt')
    $desktop = [Environment]::GetFolderPath('Desktop')
    if (-not [string]::IsNullOrWhiteSpace($desktop)) { $candidates += Join-Path $desktop 'DigitalCity.crt' }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return (Resolve-Path -LiteralPath $candidate).Path }
    }
    return ''
}

function Convert-CertificateToPem {
    param([System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate)
    $base64 = [Convert]::ToBase64String($Certificate.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert))
    $lines = [regex]::Matches($base64, '.{1,64}') | ForEach-Object { $_.Value }
    return "-----BEGIN CERTIFICATE-----`n$($lines -join "`n")`n-----END CERTIFICATE-----`n"
}

function New-ProcessCaBundle {
    param([string]$CertificatePath)
    if ([string]::IsNullOrWhiteSpace($CertificatePath)) { return '' }
    $text = [System.IO.File]::ReadAllText($CertificatePath)
    if ($text -notmatch '-----BEGIN CERTIFICATE-----') {
        try { $text = Convert-CertificateToPem -Certificate (New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($CertificatePath)) }
        catch { throw "Configured CA certificate is neither PEM nor a readable DER certificate: $CertificatePath" }
    }

    $fingerprints = @{}
    $pem = New-Object System.Text.StringBuilder
    foreach ($location in @([System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser, [System.Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine)) {
        try {
            $store = New-Object System.Security.Cryptography.X509Certificates.X509Store('Root', $location)
            $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
            try {
                foreach ($certificate in $store.Certificates) {
                    $thumbprint = $certificate.Thumbprint.ToUpperInvariant()
                    if (-not $fingerprints.ContainsKey($thumbprint)) { $fingerprints[$thumbprint] = $true; [void]$pem.Append((Convert-CertificateToPem -Certificate $certificate)) }
                }
            }
            finally { $store.Close() }
        }
        catch { }
    }
    if ($pem.Length -eq 0) { throw 'Could not read the Windows trusted root store to build the process CA bundle.' }
    [void]$pem.Append($text.Trim())
    [void]$pem.Append("`n")
    $directory = Join-Path ([System.IO.Path]::GetTempPath()) 'simdashboard-network-ca'
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $bundlePath = Join-Path $directory ("ca-$PID-$([Guid]::NewGuid().ToString('N')).pem")
    [System.IO.File]::WriteAllText($bundlePath, $pem.ToString(), (New-Object System.Text.UTF8Encoding($false)))
    return $bundlePath
}

function Initialize-DeploymentNetwork {
    [CmdletBinding()]
    param(
        [string]$SettingsPath = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'deploy\windows\network-settings.json'),
        [ValidateSet('auto', 'direct', 'proxy')]
        [string]$Mode = ''
    )

    if (-not (Test-Path -LiteralPath $SettingsPath -PathType Leaf)) {
        throw "Deployment network settings were not found: $SettingsPath"
    }
    $resolvedSettingsPath = (Resolve-Path -LiteralPath $SettingsPath).Path
    $settings = Get-Content -Raw -LiteralPath $resolvedSettingsPath | ConvertFrom-Json
    $configuredMode = [string](Get-NetworkSettingValue -Settings $settings -Name 'mode' -Default 'auto')
    $effectiveMode = if ([string]::IsNullOrWhiteSpace($Mode)) { $configuredMode.ToLowerInvariant() } else { $Mode.ToLowerInvariant() }
    if ($effectiveMode -notin @('auto', 'direct', 'proxy')) { throw "Unsupported deployment network mode '$effectiveMode'. Use auto, direct, or proxy." }

    $configuredProxy = [string](Get-NetworkSettingValue -Settings $settings -Name 'proxy' -Default '')
    if (-not [string]::IsNullOrWhiteSpace($configuredProxy)) {
        try {
            $proxyUri = [Uri]$configuredProxy
            if ($proxyUri.Scheme -notin @('http', 'https') -or [string]::IsNullOrWhiteSpace($proxyUri.Host)) { throw 'invalid proxy URI' }
        }
        catch {
            throw "Deployment proxy setting is invalid: $(Get-MaskedNetworkValue $configuredProxy)"
        }
    }

    $defaultNoProxy = @('127.0.0.1', 'localhost', '::1', '10.0.0.0/8', '10.*', '165.213.0.0/16', '165.213.*', '168.219.0.0/16', '168.219.*', '202.20.0.0/16', '202.20.*', '112.107.220.0/24', '112.107.220.*', '.samsung.net', 'samsung.net')
    $configuredNoProxy = @(Get-NetworkSettingValue -Settings $settings -Name 'noProxy' -Default @())
    $existingNoProxy = Get-FirstProcessVariable -Names @('NO_PROXY', 'no_proxy')
    $noProxy = Join-NoProxyValues -Values (@($existingNoProxy) + $configuredNoProxy + $defaultNoProxy)
    Set-ProcessNetworkVariable -Name 'NO_PROXY' -Value $noProxy
    Set-ProcessNetworkVariable -Name 'no_proxy' -Value $noProxy

    $existingHttpProxy = Get-FirstProcessVariable -Names @('HTTP_PROXY', 'http_proxy')
    $existingHttpsProxy = Get-FirstProcessVariable -Names @('HTTPS_PROXY', 'https_proxy')
    $proxySource = 'direct'
    $selectedProxy = ''
    if ($effectiveMode -eq 'direct') {
        Set-ProcessNetworkVariable -Name 'HTTP_PROXY' -Value ''
        Set-ProcessNetworkVariable -Name 'HTTPS_PROXY' -Value ''
        Set-ProcessNetworkVariable -Name 'http_proxy' -Value ''
        Set-ProcessNetworkVariable -Name 'https_proxy' -Value ''
    }
    elseif ($effectiveMode -eq 'proxy') {
        if ([string]::IsNullOrWhiteSpace($configuredProxy)) { throw 'Proxy mode requires a non-empty proxy value in network-settings.json.' }
        $selectedProxy = $configuredProxy
        $proxySource = 'settings'
    }
    elseif (-not [string]::IsNullOrWhiteSpace($existingHttpProxy) -or -not [string]::IsNullOrWhiteSpace($existingHttpsProxy)) {
        $selectedProxy = if ($existingHttpsProxy) { $existingHttpsProxy } else { $existingHttpProxy }
        $proxySource = 'existing environment'
    }
    elseif (-not [string]::IsNullOrWhiteSpace($configuredProxy) -and (Test-ProxyEndpoint -ProxyUri $configuredProxy)) {
        $selectedProxy = $configuredProxy
        $proxySource = 'reachable settings proxy'
    }

    if (-not [string]::IsNullOrWhiteSpace($selectedProxy)) {
        Set-ProcessNetworkVariable -Name 'HTTP_PROXY' -Value $selectedProxy
        Set-ProcessNetworkVariable -Name 'HTTPS_PROXY' -Value $selectedProxy
        Set-ProcessNetworkVariable -Name 'http_proxy' -Value $selectedProxy
        Set-ProcessNetworkVariable -Name 'https_proxy' -Value $selectedProxy
    }

    $certificatePath = Resolve-OptionalCertificatePath -CertificatePath ([string](Get-NetworkSettingValue -Settings $settings -Name 'caCertificatePath' -Default '')) -SettingsPath $resolvedSettingsPath
    $certificateBundle = New-ProcessCaBundle -CertificatePath $certificatePath
    if (-not [string]::IsNullOrWhiteSpace($certificatePath)) {
        Set-ProcessNetworkVariable -Name 'NODE_EXTRA_CA_CERTS' -Value $certificateBundle
        Set-ProcessNetworkVariable -Name 'SSL_CERT_FILE' -Value $certificateBundle
        Set-ProcessNetworkVariable -Name 'REQUESTS_CA_BUNDLE' -Value $certificateBundle
        Set-ProcessNetworkVariable -Name 'PIP_CERT' -Value $certificateBundle
        Set-ProcessNetworkVariable -Name 'CURL_CA_BUNDLE' -Value $certificateBundle
        Set-ProcessNetworkVariable -Name 'NPM_CONFIG_CAFILE' -Value $certificateBundle
        Set-ProcessNetworkVariable -Name 'npm_config_cafile' -Value $certificateBundle
        Set-ProcessNetworkVariable -Name 'SIMDASH_CA_BUNDLE' -Value $certificateBundle
    }

    $useWindowsTrustStore = [bool](Get-NetworkSettingValue -Settings $settings -Name 'useWindowsTrustStore' -Default $true)
    if ($useWindowsTrustStore) {
        Set-ProcessNetworkVariable -Name 'NODE_USE_SYSTEM_CA' -Value '1'
        Set-ProcessNetworkVariable -Name 'UV_SYSTEM_CERTS' -Value 'true'
    }

    $summary = [pscustomobject]@{
        Mode = $effectiveMode
        ProxySource = $proxySource
        Proxy = Get-MaskedNetworkValue $selectedProxy
        NoProxy = $noProxy
        Certificate = if ($certificatePath) { Split-Path -Leaf $certificatePath } else { '' }
        UsesWindowsTrustStore = $useWindowsTrustStore
    }
    $proxyMessage = if ($summary.Proxy) { $summary.Proxy } else { 'direct' }
    Write-Host "Network: $($summary.Mode); outbound $proxyMessage ($($summary.ProxySource)); TLS verification remains enabled." -ForegroundColor DarkCyan
    return $summary
}

function Invoke-AtomicDownload {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [Parameter(Mandatory = $true)]
        [string]$Destination,
        [string]$ExpectedSha256 = '',
        [ValidateRange(1, 10)]
        [int]$Retries = 3,
        [ValidateRange(1, 3600)]
        [int]$TimeoutSeconds = 120
    )

    if (-not [string]::IsNullOrWhiteSpace($ExpectedSha256) -and $ExpectedSha256 -notmatch '^[a-fA-F0-9]{64}$') {
        throw 'ExpectedSha256 must be a 64-character SHA-256 hex value.'
    }
    $directory = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        if ([string]::IsNullOrWhiteSpace($ExpectedSha256) -or ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ieq $ExpectedSha256)) {
            return $Destination
        }
        Remove-Item -LiteralPath $Destination -Force
    }

    $lastError = $null
    for ($attempt = 1; $attempt -le $Retries; $attempt += 1) {
        $partial = "$Destination.partial.$PID.$attempt.$([Guid]::NewGuid().ToString('N'))"
        try {
            $caBundle = [Environment]::GetEnvironmentVariable('SIMDASH_CA_BUNDLE', 'Process')
            if (-not [string]::IsNullOrWhiteSpace($caBundle)) {
                $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
                if ($null -eq $curl) { throw 'A configured custom CA requires curl.exe for verified downloads before Node and uv are installed.' }
                $curlArguments = @('--fail', '--location', '--silent', '--show-error', '--connect-timeout', [string]$TimeoutSeconds, '--max-time', [string]$TimeoutSeconds, '--cacert', $caBundle, '--noproxy', $env:NO_PROXY, '--output', $partial)
                $proxy = Get-FirstProcessVariable -Names @('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy')
                if (-not [string]::IsNullOrWhiteSpace($proxy)) {
                    try { $proxyUri = [Uri]$proxy; if ([string]::IsNullOrWhiteSpace($proxyUri.UserInfo)) { $curlArguments += @('--proxy', $proxy) } }
                    catch { throw 'Configured process proxy is not a valid URI.' }
                }
                $curlArguments += $Uri
                & $curl.Source @curlArguments
                if ($LASTEXITCODE -ne 0) { throw "curl.exe failed with exit code $LASTEXITCODE." }
            }
            else {
                $webRequestArguments = @{ Uri = $Uri; OutFile = $partial; UseBasicParsing = $true; TimeoutSec = $TimeoutSeconds }
                $proxy = Get-FirstProcessVariable -Names @('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy')
                if (-not [string]::IsNullOrWhiteSpace($proxy) -and -not (Test-NoProxyHost -Uri $Uri -NoProxy $env:NO_PROXY)) { $webRequestArguments.Proxy = $proxy }
                Invoke-WebRequest @webRequestArguments
            }
            if (-not (Test-Path -LiteralPath $partial -PathType Leaf) -or (Get-Item -LiteralPath $partial).Length -eq 0) { throw 'Download produced an empty file.' }
            if (-not [string]::IsNullOrWhiteSpace($ExpectedSha256)) {
                $actual = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash
                if ($actual -ine $ExpectedSha256) { throw 'Downloaded file checksum verification failed.' }
            }
            Move-Item -LiteralPath $partial -Destination $Destination -Force
            return $Destination
        }
        catch {
            $lastError = $_
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
            if ($attempt -lt $Retries) { Start-Sleep -Seconds ([Math]::Min(5, $attempt)) }
        }
    }
    throw "Download failed after $Retries attempts for $(Get-MaskedNetworkValue $Uri): $(ConvertTo-MaskedNetworkMessage $lastError.Exception.Message)"
}

Export-ModuleMember -Function Initialize-DeploymentNetwork, Invoke-AtomicDownload
