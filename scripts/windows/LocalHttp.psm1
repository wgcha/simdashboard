Set-StrictMode -Version Latest

function Assert-LocalHttpUri {
    param([Parameter(Mandatory = $true)][string]$Uri)
    $parsed = $null
    if (-not [Uri]::TryCreate($Uri, [UriKind]::Absolute, [ref]$parsed) -or $parsed.Scheme -ne 'http' -or $parsed.UserInfo) {
        throw 'Only unauthenticated HTTP loopback URLs are permitted for local service checks.'
    }
    $ipAddress = $null
    $isLoopbackIp = [System.Net.IPAddress]::TryParse($parsed.DnsSafeHost, [ref]$ipAddress) -and [System.Net.IPAddress]::IsLoopback($ipAddress)
    if (-not [string]::Equals($parsed.DnsSafeHost, 'localhost', [System.StringComparison]::OrdinalIgnoreCase) -and -not $isLoopbackIp) {
        throw 'Only unauthenticated HTTP loopback URLs are permitted for local service checks.'
    }
    return $parsed
}

function Read-BoundedResponseBody {
    param(
        [Parameter(Mandatory = $true)]$Stream,
        [ValidateRange(1, 16777216)][int]$MaxBodyBytes = 1048576,
        [ValidateRange(1, 30000)][int]$TimeoutMilliseconds = 2000
    )
    $buffer = New-Object byte[] 4096
    $body = New-Object System.IO.MemoryStream
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $total = 0
        while ($true) {
            $remainingTimeout = $TimeoutMilliseconds - [int]$clock.ElapsedMilliseconds
            if ($remainingTimeout -le 0) { throw 'Local HTTP response exceeded its time limit.' }
            $readTask = $Stream.ReadAsync($buffer, 0, $buffer.Length)
            if (-not $readTask.Wait($remainingTimeout)) { throw 'Local HTTP response exceeded its time limit.' }
            $read = $readTask.Result
            if ($read -le 0) { break }
            $remaining = $MaxBodyBytes - $total
            if ($read -gt $remaining) { throw 'Local HTTP response exceeded the body limit.' }
            $body.Write($buffer, 0, $read)
            $total += $read
        }
        return [Text.Encoding]::UTF8.GetString($body.ToArray())
    }
    finally { $body.Dispose() }
}

function Invoke-LocalHttp {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [ValidateRange(250, 30000)][int]$TimeoutMilliseconds = 2000,
        [ValidateRange(1, 16777216)][int]$MaxBodyBytes = 1048576
    )
    $parsed = Assert-LocalHttpUri -Uri $Uri
    $request = [Net.HttpWebRequest]::Create($parsed)
    $request.Proxy = $null
    $request.UseDefaultCredentials = $false
    $request.AllowAutoRedirect = $false
    $request.Timeout = $TimeoutMilliseconds
    $request.ReadWriteTimeout = $TimeoutMilliseconds
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $response = $null
    try {
        try { $response = $request.GetResponse() }
        catch [Net.WebException] {
            if ($null -eq $_.Exception.Response) { throw }
            $response = $_.Exception.Response
        }
        $elapsedMilliseconds = [int]$clock.ElapsedMilliseconds
        $remainingMilliseconds = $TimeoutMilliseconds - $elapsedMilliseconds
        if ($remainingMilliseconds -le 0) { throw 'Local HTTP response exceeded its time limit.' }
        $content = Read-BoundedResponseBody -Stream $response.GetResponseStream() -MaxBodyBytes $MaxBodyBytes -TimeoutMilliseconds $remainingMilliseconds
        return [pscustomobject]@{
            StatusCode = [int]$response.StatusCode
            StatusDescription = [string]$response.StatusDescription
            Content = $content
        }
    }
    finally {
        if ($response) { $response.Dispose() }
        $request.Abort()
    }
}

Export-ModuleMember -Function Assert-LocalHttpUri, Invoke-LocalHttp
