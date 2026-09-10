[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$StartScript = Join-Path $Root 'start.ps1'
$HelperSource = Join-Path $Root 'backend\scripts\windows_web_access.py'
$Python = Join-Path $Root '.venv-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { $Python = 'python' }

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "Assertion failed: $Message" }
}

function Get-StartFunction([string]$Name) {
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($StartScript, [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) { throw "start.ps1 did not parse: $($errors[0].Message)" }
    $function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $Name }, $true)
    if (-not $function) { throw "Function $Name was not found in start.ps1." }
    return [scriptblock]::Create($function.Extent.Text)
}

$fixture = Join-Path ([System.IO.Path]::GetTempPath()) ("analysis-canvas-web-access-" + [guid]::NewGuid().ToString('N'))
$savedEnvironment = @{}
foreach ($name in @('WINDOWS_WEB_HOST', 'AUTH_MODE', 'AUTH_COOKIE_SECURE')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}

try {
    New-Item -ItemType Directory -Path (Join-Path $fixture 'backend\scripts') -Force | Out-Null
    Copy-Item -LiteralPath $HelperSource -Destination (Join-Path $fixture 'backend\scripts\windows_web_access.py')
    Set-Content -LiteralPath (Join-Path $fixture '.env') -Value 'WINDOWS_WEB_HOST=127.0.0.1' -Encoding UTF8

    $helper = Join-Path $fixture 'backend\scripts\windows_web_access.py'
    $local = (& $Python $helper | ConvertFrom-Json)
    Assert-True ($LASTEXITCODE -eq 0) 'default helper invocation should succeed.'
    Assert-True ($local.host -eq '127.0.0.1' -and $local.mode -eq 'local') 'default listener should be loopback/local.'

    Set-Content -LiteralPath (Join-Path $fixture '.env') -Value @('WINDOWS_WEB_HOST=0.0.0.0', 'AUTH_MODE=password') -Encoding UTF8
    $lan = (& $Python $helper | ConvertFrom-Json)
    Assert-True ($LASTEXITCODE -eq 0) 'LAN host lookup should not require an account before setup.'
    Assert-True ($lan.host -eq '0.0.0.0' -and $lan.mode -eq 'lan') 'LAN listener metadata should be explicit.'
    $null = & $Python $helper '--check-auth'
    Assert-True ($LASTEXITCODE -eq 0) 'password LAN should pass the settings-only authentication check.'

    Set-Content -LiteralPath (Join-Path $fixture '.env') -Value @('WINDOWS_WEB_HOST=0.0.0.0', 'AUTH_MODE=disabled') -Encoding UTF8
    $null = & $Python $helper '--check-auth'
    Assert-True ($LASTEXITCODE -ne 0) 'disabled authentication must fail closed for LAN.'

    Set-Content -LiteralPath (Join-Path $fixture '.env') -Value @('WINDOWS_WEB_HOST=0.0.0.0', 'AUTH_MODE=unsupported') -Encoding UTF8
    $null = & $Python $helper '--check-auth'
    Assert-True ($LASTEXITCODE -ne 0) 'unknown authentication modes must fail closed for LAN.'

    Set-Content -LiteralPath (Join-Path $fixture '.env') -Value @('WINDOWS_WEB_HOST=0.0.0.0', 'AUTH_MODE=password', 'AUTH_COOKIE_SECURE=true') -Encoding UTF8
    $null = & $Python $helper '--check-auth'
    Assert-True ($LASTEXITCODE -ne 0) 'secure cookies must reject direct HTTP LAN guidance.'

    . (Get-StartFunction 'Test-FrontendHostRecord')
    Assert-True (Test-FrontendHostRecord ([pscustomobject]@{ host = '0.0.0.0' }) '0.0.0.0') 'matching frontend metadata should be accepted.'
    Assert-True (-not (Test-FrontendHostRecord ([pscustomobject]@{}) '0.0.0.0')) 'legacy frontend metadata without host must be rejected.'
    Assert-True (-not (Test-FrontendHostRecord ([pscustomobject]@{ host = '127.0.0.1' }) '0.0.0.0')) 'changed frontend host metadata must be rejected.'

    function Get-NetIPAddress {
        [CmdletBinding()]
        param([string]$AddressFamily)
        @(
            [pscustomobject]@{ IPAddress = '127.0.0.1'; AddressState = 'Preferred'; InterfaceIndex = 1 },
            [pscustomobject]@{ IPAddress = '169.254.9.2'; AddressState = 'Preferred'; InterfaceIndex = 2 },
            [pscustomobject]@{ IPAddress = '192.168.20.15'; AddressState = 'Preferred'; InterfaceIndex = 3 },
            [pscustomobject]@{ IPAddress = '10.10.10.12'; AddressState = 'Deprecated'; InterfaceIndex = 4 },
            [pscustomobject]@{ IPAddress = '10.10.10.13'; AddressState = 'Preferred'; InterfaceIndex = 5 }
        )
    }
    function Get-NetAdapter {
        [CmdletBinding()]
        param([int]$InterfaceIndex)
        [pscustomobject]@{ Status = if ($InterfaceIndex -eq 5) { 'Down' } else { 'Up' } }
    }
    . (Get-StartFunction 'Get-LanDashboardUrls')
    $urls = @(Get-LanDashboardUrls -Port 5199)
    Assert-True ($urls.Count -eq 1 -and $urls[0] -eq 'http://192.168.20.15:5199/workspace/overview') 'LAN URLs must exclude loopback, APIPA, deprecated, and down interfaces.'

    Write-Host 'Windows web access self-test passed.' -ForegroundColor Green
}
finally {
    foreach ($name in $savedEnvironment.Keys) {
        if ($null -eq $savedEnvironment[$name]) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
        else { Set-Item "Env:$name" $savedEnvironment[$name] }
    }
    if (Test-Path -LiteralPath $fixture) {
        $resolvedFixture = (Resolve-Path -LiteralPath $fixture).ProviderPath
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        if (-not $resolvedFixture.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
            [IO.Path]::GetFileName($resolvedFixture) -notmatch '^analysis-canvas-web-access-[a-f0-9]{32}$') {
            throw 'Refusing cleanup outside the disposable test fixture.'
        }
        Remove-Item -LiteralPath $resolvedFixture -Recurse -Force
    }
}
