[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Import-Module (Join-Path $PSScriptRoot 'LocalHttp.psm1') -Force

$probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
$probe.Start()
$port = ([System.Net.IPEndPoint]$probe.LocalEndpoint).Port
$probe.Stop()

$server = Start-Job -ArgumentList $port -ScriptBlock {
    param([int]$Port)
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
    $listener.Start()
    Write-Output 'READY'
    try {
        for ($requestNumber = 0; $requestNumber -lt 3; $requestNumber += 1) {
            $client = $listener.AcceptTcpClient()
            try {
                $stream = $client.GetStream()
                $reader = New-Object System.IO.StreamReader($stream, [Text.Encoding]::ASCII, $false, 1024, $true)
                $requestLine = $reader.ReadLine()
                while (($line = $reader.ReadLine())) { }
                $path = if ($requestLine) { ($requestLine -split ' ')[1] } else { '/' }
                if ($path -eq '/slow') { Start-Sleep -Seconds 1 }
                $body = if ($path -eq '/mismatch') { '{"status":"ok","database_backend":"postgresql"}' } else { '{"status":"ok","database_backend":"duckdb"}' }
                $bodyBytes = [Text.Encoding]::UTF8.GetBytes($body)
                $headers = [Text.Encoding]::ASCII.GetBytes("HTTP/1.1 200 OK`r`nContent-Type: application/json`r`nContent-Length: $($bodyBytes.Length)`r`nConnection: close`r`n`r`n")
                $stream.Write($headers, 0, $headers.Length)
                $stream.Write($bodyBytes, 0, $bodyBytes.Length)
                $stream.Flush()
            }
            catch {
                # A bounded client timeout can close the slow response before write.
            }
            finally { $client.Dispose() }
        }
    }
    finally { $listener.Stop() }
}

if (-not ('ForcedLoopbackProxy' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Net;

public sealed class ForcedLoopbackProxy : IWebProxy
{
    private readonly Uri proxyUri = new Uri("http://127.0.0.1:1");
    public int GetProxyCallCount { get; private set; }
    public int IsBypassedCallCount { get; private set; }
    public ICredentials Credentials { get; set; }

    public Uri GetProxy(Uri destination)
    {
        GetProxyCallCount++;
        return proxyUri;
    }

    public bool IsBypassed(Uri host)
    {
        IsBypassedCallCount++;
        return false;
    }
}
'@
}

$originalProxy = [Net.WebRequest]::DefaultWebProxy
$badProxy = New-Object ForcedLoopbackProxy
try {
    $readyDeadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        if ($server.State -eq 'Failed') { throw 'The local HTTP fixture failed to start.' }
        $ready = @(Receive-Job -Job $server -Keep) -contains 'READY'
        if (-not $ready) { Start-Sleep -Milliseconds 50 }
    } while (-not $ready -and [DateTime]::UtcNow -lt $readyDeadline)
    if (-not $ready) { throw 'The local HTTP fixture did not become ready.' }

    [Net.WebRequest]::DefaultWebProxy = $badProxy
    Write-Verbose 'Checking that a legacy request is forced through the default proxy.'
    $legacyRequest = [Net.HttpWebRequest]::Create("http://127.0.0.1:$port/ok")
    $legacyRequest.Timeout = 500
    try {
        $legacyResponse = $legacyRequest.GetResponse()
        try { throw 'A legacy request unexpectedly bypassed the forced default proxy.' }
        finally { $legacyResponse.Dispose() }
    }
    catch {
        if ($_.Exception.Message -eq 'A legacy request unexpectedly bypassed the forced default proxy.') { throw }
    }
    finally { $legacyRequest.Abort() }
    if ($badProxy.IsBypassedCallCount -lt 1 -or $badProxy.GetProxyCallCount -lt 1) {
        throw 'The forced default proxy was not consulted by the legacy request.'
    }

    $proxyCallsBeforeHelper = $badProxy.GetProxyCallCount
    Write-Verbose 'Checking that Invoke-LocalHttp bypasses the default proxy.'
    $response = Invoke-LocalHttp -Uri "http://127.0.0.1:$port/ok" -TimeoutMilliseconds 1000
    if ($response.StatusCode -ne 200 -or ($response.Content | ConvertFrom-Json).database_backend -ne 'duckdb') {
        throw 'The loopback helper did not return the local response while the default proxy was unusable.'
    }
    if (-not [object]::ReferenceEquals([Net.WebRequest]::DefaultWebProxy, $badProxy)) {
        throw 'The loopback helper modified the process-wide default proxy.'
    }
    if ($badProxy.GetProxyCallCount -ne $proxyCallsBeforeHelper) {
        throw 'The loopback helper consulted the process-wide default proxy.'
    }

    foreach ($invalidUri in @('https://127.0.0.1/', 'http://example.invalid/', 'http://user:secret@127.0.0.1/')) {
        try {
            Assert-LocalHttpUri -Uri $invalidUri | Out-Null
            throw "An external or credential-bearing URI was accepted: $invalidUri"
        }
        catch {
            if ($_.Exception.Message -like 'An external or credential-bearing URI*') { throw }
        }
    }

    $clock = [Diagnostics.Stopwatch]::StartNew()
    Write-Verbose 'Checking the request deadline.'
    try {
        Invoke-LocalHttp -Uri "http://127.0.0.1:$port/slow" -TimeoutMilliseconds 250 | Out-Null
        throw 'The bounded local HTTP request unexpectedly completed.'
    }
    catch {
        if ($_.Exception.Message -eq 'The bounded local HTTP request unexpectedly completed.') { throw }
    }
    finally { $clock.Stop() }
    if ($clock.ElapsedMilliseconds -gt 1500) { throw "The local HTTP timeout was not bounded ($($clock.ElapsedMilliseconds) ms)." }

    $tokens = $null
    $parseErrors = $null
    $startAst = [Management.Automation.Language.Parser]::ParseFile((Join-Path $Root 'start.ps1'), [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw 'start.ps1 could not be parsed for the readiness contract test.' }
    $waitFunction = $startAst.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Wait-HttpReady' }, $true)
    if (-not $waitFunction) { throw 'Wait-HttpReady was not found in start.ps1.' }
    . ([scriptblock]::Create($waitFunction.Extent.Text))
    $script:databaseBackend = 'duckdb'
    $Backend = Join-Path $Root 'backend'
    $Frontend = Join-Path $Root 'frontend'
    $process = [pscustomobject]@{ HasExited = $false }
    $process | Add-Member -MemberType ScriptMethod -Name Refresh -Value { }
    try {
        Write-Verbose 'Checking backend mismatch handling in Wait-HttpReady.'
        Wait-HttpReady -Name 'Backend' -Role backend -Uri "http://127.0.0.1:$port/mismatch" -Process $process -TimeoutSeconds 3
        throw 'A mismatched backend health response was accepted.'
    }
    catch {
        if ($_.Exception.Message -ne 'Backend health database mismatch.') { throw }
    }

    Write-Host 'Windows local HTTP self-test passed.' -ForegroundColor Green
}
finally {
    [Net.WebRequest]::DefaultWebProxy = $originalProxy
    Stop-Job -Job $server -ErrorAction SilentlyContinue
    Remove-Job -Job $server -Force -ErrorAction SilentlyContinue
}
