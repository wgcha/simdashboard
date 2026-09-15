[CmdletBinding()]
param(
    [string]$Config = '',
    [switch]$Check,
    [switch]$SkipAccountSetup,
    [switch]$SkipFirewall,
    [switch]$NonInteractive
)

# Server installer for an extracted, hash-checked bundle.  There is no package
# manager, download, Git, Node, or online PostgreSQL repository in this file.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$BundleRoot = [IO.Path]::GetFullPath($PSScriptRoot)

function Fail([string]$Message) { throw "OFFLINE_INSTALL_FAILED: $Message" }
function Require-File([string]$Path, [string]$Name) { if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Fail "$Name is missing: $Path" } }
function Require-Directory([string]$Path, [string]$Name) { if (-not (Test-Path -LiteralPath $Path -PathType Container)) { Fail "$Name is missing: $Path" } }
function Read-Json([string]$Path) { Require-File $Path 'configuration'; try { return (Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json) } catch { Fail 'configuration JSON is invalid.' } }
function Get-ConfigValue($Object, [string]$Name, $Default = '') {
    $property = $Object.PSObject.Properties[$Name]
    if ($property -and $null -ne $property.Value) { return [string]$property.Value }
    return [string]$Default
}
function Safe-Relative([string]$Path) {
    if (-not $Path -or [IO.Path]::IsPathRooted($Path) -or $Path -match '[:\x00-\x1f]' -or $Path -match '(^|[\\/])\.{1,2}([\\/]|$)' -or $Path -match '[. ]([\\/]|$)') { Fail 'manifest contains an unsafe path.' }
}
function Assert-BundleTree([string]$Directory) {
    if ((Get-Item -LiteralPath $Directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { Fail 'bundle contains a linked directory.' }
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { Fail 'bundle contains a linked path.' }
        if ($item.PSIsContainer) { Assert-BundleTree $item.FullName }
    }
}
function Verify-BundleManifest {
    Assert-BundleTree $BundleRoot
    $manifestPath = Join-Path $BundleRoot 'bundle-manifest.json'
    $manifest = Read-Json $manifestPath
    if ($manifest.format -ne 'simulation-workbench-windows-offline-bundle' -or [int]$manifest.format_version -ne 1) { Fail 'unsupported bundle manifest.' }
    if ($manifest.target.os -ne 'Windows Server 2022' -or $manifest.target.architecture -ne 'x64' -or $manifest.target.network -ne 'none') { Fail 'bundle target is not Windows Server 2022 x64 offline.' }
    $releaseId = [string]$manifest.release_id
    if ($releaseId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$') { Fail 'bundle release ID is invalid.' }
    $releaseIdFile = (Get-Content -Raw -LiteralPath (Join-Path $BundleRoot 'RELEASE_ID')).Trim()
    if ($releaseIdFile -ne $releaseId) { Fail 'RELEASE_ID does not match bundle manifest.' }
    $seen = @{}
    foreach ($entry in @($manifest.files)) {
        $relative = ([string]$entry.path).Replace('/', '\')
        Safe-Relative $relative
        if ($seen.ContainsKey($relative)) { Fail 'bundle manifest contains a duplicate path.' }
        $candidate = [IO.Path]::GetFullPath((Join-Path $BundleRoot $relative))
        if (-not $candidate.StartsWith($BundleRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { Fail 'manifest path escapes bundle.' }
        Require-File $candidate "manifest file $relative"
        $actual = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne ([string]$entry.sha256).ToLowerInvariant() -or (Get-Item -LiteralPath $candidate).Length -ne [int64]$entry.bytes) { Fail "bundle checksum failed: $relative" }
        $seen[$relative.Replace('/', '\')] = $true
    }
    foreach ($file in Get-ChildItem -LiteralPath $BundleRoot -Recurse -Force -File) {
        $relative = $file.FullName.Substring($BundleRoot.TrimEnd('\').Length + 1)
        if ($relative -ieq 'bundle-manifest.json' -or $relative -ieq 'config\install.json') { continue }
        if (-not $seen.ContainsKey($relative)) { Fail "bundle manifest omits file: $relative" }
    }
    return $manifest
}
function Get-EnvValue([string]$Path, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match ('^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*)$')) { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return ''
}
function Set-EnvValue([string]$Path, [string]$Name, [string]$Value) {
    $lines = @(); if (Test-Path -LiteralPath $Path) { $lines = @(Get-Content -LiteralPath $Path) }
    $result = New-Object Collections.Generic.List[string]; $found = $false
    foreach ($line in $lines) {
        if ($line -match ('^\s*' + [regex]::Escape($Name) + '\s*=') -and $line -notmatch '^\s*#') { $result.Add("$Name=$Value"); $found = $true } else { $result.Add($line) }
    }
    if (-not $found) { $result.Add("$Name=$Value") }
    $tmp = "$Path.$PID.tmp"; [IO.File]::WriteAllLines($tmp, $result, (New-Object Text.UTF8Encoding($false))); Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Set-ManagedAcl([string]$Path, [string]$ServiceAccount, [ValidateSet('read', 'modify', 'admin')][string]$Access, [bool]$Recurse = $false) {
    if ($ServiceAccount -and $ServiceAccount -ine 'NT AUTHORITY\LOCAL SERVICE' -and $ServiceAccount -ine 'NT AUTHORITY\LocalService') {
        Fail 'Only the built-in LocalService account is supported for application services.'
    }
    if (-not (Test-Path -LiteralPath $Path)) { Fail "ACL target is missing: $Path" }
    $items = @((Get-Item -LiteralPath $Path -Force))
    if ($Recurse -and $items[0].PSIsContainer) { $items += @(Get-ChildItem -LiteralPath $Path -Recurse -Force) }
    $administrators = New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-544'
    $system = New-Object Security.Principal.SecurityIdentifier 'S-1-5-18'
    $localService = New-Object Security.Principal.SecurityIdentifier 'S-1-5-19'
    foreach ($item in $items) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { Fail "ACL target contains a linked path: $($item.FullName)" }
        $acl = Get-Acl -LiteralPath $item.FullName -ErrorAction Stop
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRuleAll($rule) }
        $inheritance = if ($item.PSIsContainer) { [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit } else { [Security.AccessControl.InheritanceFlags]::None }
        foreach ($identity in @($administrators, $system)) {
            $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, [Security.AccessControl.FileSystemRights]::FullControl, $inheritance, [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow)
            $acl.AddAccessRule($rule)
        }
        if ($Access -ne 'admin') {
            $rights = if ($Access -eq 'modify') { [Security.AccessControl.FileSystemRights]::Modify } else { [Security.AccessControl.FileSystemRights]::ReadAndExecute }
            $rule = New-Object Security.AccessControl.FileSystemAccessRule($localService, $rights, $inheritance, [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow)
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $item.FullName -AclObject $acl -ErrorAction Stop
    }
}
function Set-PrivateAcl([string]$Path, [string]$ServiceAccount, [switch]$Recurse) { Set-ManagedAcl $Path $ServiceAccount 'modify' ([bool]$Recurse) }
function Set-SecretAcl([string]$Path, [string]$ServiceAccount, [switch]$Recurse) { Set-ManagedAcl $Path $ServiceAccount 'read' ([bool]$Recurse) }
function Set-AdminOnlyAcl([string]$Path, [switch]$Recurse) { Set-ManagedAcl $Path '' 'admin' ([bool]$Recurse) }
function New-RandomSecret([int]$Bytes = 48) {
    $buffer = New-Object byte[] $Bytes
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($buffer) } finally { $generator.Dispose() }
    return [Convert]::ToBase64String($buffer)
}
function Invoke-Tool([string]$File, [string[]]$Arguments, [string]$WorkingDirectory = '') {
    if ($WorkingDirectory) { Push-Location $WorkingDirectory }
    try { & $File @Arguments | Out-Host; $code = [int]$LASTEXITCODE } finally { if ($WorkingDirectory) { Pop-Location } }
    if ($code -ne 0) { Fail "offline tool failed (exit code $code): $([IO.Path]::GetFileName($File))" }
}
function Invoke-Python([string]$Python, [string]$Script, [string[]]$Arguments = @(), [string]$WorkingDirectory) {
    $previousPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $WorkingDirectory
        Invoke-Tool $Python (@($Script) + $Arguments) $WorkingDirectory
    }
    finally {
        if ($null -eq $previousPythonPath) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
        else { $env:PYTHONPATH = $previousPythonPath }
    }
}
function Get-OwnedService([string]$Name, [string]$ExpectedExecutable, [string]$ExpectedData = '', [string]$ExpectedAccount = '') {
    if ($Name -notmatch '^[A-Za-z][A-Za-z0-9_-]*$') { Fail 'invalid managed service name.' }
    $record = Get-CimInstance Win32_Service -Filter "Name = '$Name'" -ErrorAction Stop
    if (-not $record) { return $null }
    $pathName = [string]$record.PathName
    $expectedPath = [IO.Path]::GetFullPath($ExpectedExecutable)
    $quoted = '"' + $expectedPath + '"'
    if (-not ($pathName.StartsWith($quoted, [StringComparison]::OrdinalIgnoreCase) -or
              $pathName.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase) -or
              ($pathName.StartsWith($expectedPath, [StringComparison]::OrdinalIgnoreCase) -and
               $pathName.Length -gt $expectedPath.Length -and [char]::IsWhiteSpace($pathName[$expectedPath.Length])))) {
        Fail "service $Name is registered to an unowned executable; it was not changed."
    }
    if ($ExpectedData) {
        $dataMatch = [regex]::Match($pathName, '(?i)(?:^|\s)-D\s+(?:"(?<directory>[^"]+)"|(?<directory>\S+))(?=\s|$)')
        $nameMatch = [regex]::Match($pathName, '(?i)(?:^|\s)-N\s+(?:"(?<service>[^"]+)"|(?<service>\S+))(?=\s|$)')
        if (-not $dataMatch.Success -or -not $nameMatch.Success -or
            -not [IO.Path]::GetFullPath($dataMatch.Groups['directory'].Value).Equals([IO.Path]::GetFullPath($ExpectedData), [StringComparison]::OrdinalIgnoreCase) -or
            -not $nameMatch.Groups['service'].Value.Equals($Name, [StringComparison]::OrdinalIgnoreCase)) {
            Fail "service $Name uses an unexpected PostgreSQL name or data directory; it was not changed."
        }
    }
    if ($ExpectedAccount) {
        $actualAccount = ([string]$record.StartName).Replace(' ', '').ToLowerInvariant()
        $wantedAccount = $ExpectedAccount.Replace(' ', '').ToLowerInvariant()
        if ($actualAccount -ne $wantedAccount) { Fail "service $Name uses an unexpected account; it was not changed." }
    }
    return $record
}
function Stop-ManagedServices {
    foreach ($name in @('SimulationWorkbenchApi', 'SimulationWorkbenchProxy')) {
        $serviceExe = Join-Path (Join-Path $StateRoot 'services') "$name.exe"
        $null = Get-OwnedService $name $serviceExe '' 'NT AUTHORITY\LocalService'
        $service = Get-Service -Name $name -ErrorAction SilentlyContinue
        if ($service -and $service.Status -ne 'Stopped') { Stop-Service -Name $name -Force -ErrorAction Stop; $service.WaitForStatus('Stopped', (New-TimeSpan -Seconds 30)) }
    }
}
function Initialize-BundledPostgres([string]$PgRoot, [string]$StateRoot, [int]$Port) {
    $data = Join-Path $StateRoot 'postgres\data'
    $passwordPath = Join-Path $StateRoot 'postgres-admin.password'
    $pgCtl = Join-Path $PgRoot 'bin\pg_ctl.exe'; $initdb = Join-Path $PgRoot 'bin\initdb.exe'
    Require-File $pgCtl 'pg_ctl.exe'; Require-File $initdb 'initdb.exe'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $data) | Out-Null
    $clusterReady = Test-Path -LiteralPath (Join-Path $data 'PG_VERSION') -PathType Leaf
    if (-not $clusterReady -and (Test-Path -LiteralPath $data)) {
        if (@(Get-ChildItem -LiteralPath $data -Force -ErrorAction Stop).Count -gt 0) {
            Fail 'PostgreSQL data directory is nonempty without PG_VERSION; preserve it for manual recovery.'
        }
    }
    if ($clusterReady -and -not (Test-Path -LiteralPath $passwordPath -PathType Leaf)) {
        Fail 'PostgreSQL cluster exists but its administrator credential is missing; preserve it for manual recovery.'
    }
    if (-not (Test-Path -LiteralPath $passwordPath -PathType Leaf)) {
        [IO.File]::WriteAllText($passwordPath, (New-RandomSecret 36), (New-Object Text.UTF8Encoding($false)))
    }
    Set-AdminOnlyAcl $passwordPath
    if (-not (Test-Path -LiteralPath (Join-Path $data 'PG_VERSION') -PathType Leaf)) {
        $password = (Get-Content -Raw -LiteralPath $passwordPath).Trim()
        $passwordFile = Join-Path $StateRoot 'postgres-admin.secret.tmp'
        [IO.File]::WriteAllText($passwordFile, $password, (New-Object Text.UTF8Encoding($false)))
        Set-AdminOnlyAcl $passwordFile
        try { Invoke-Tool $initdb @('-D', $data, '-U', 'postgres', '--auth=scram-sha-256', "--pwfile=$passwordFile", '--encoding=UTF8') } finally { Remove-Item -LiteralPath $passwordFile -Force -ErrorAction SilentlyContinue }
        Add-Content -LiteralPath (Join-Path $data 'postgresql.conf') -Value "`nlisten_addresses = '127.0.0.1'`nport = $Port`n"
    }
    $adminPassword = (Get-Content -Raw -LiteralPath $passwordPath).Trim()
    $service = Get-Service -Name SimulationWorkbenchPostgreSQL -ErrorAction SilentlyContinue
    if ($service) { $null = Get-OwnedService 'SimulationWorkbenchPostgreSQL' $pgCtl $data }
    if (-not $service -or $service.Status -ne 'Running') {
        $listeners = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
        if ($listeners.Count -gt 0) { Fail "PostgreSQL loopback port $Port is already occupied; the bundled service was not started." }
    }
    if (-not $service) { Invoke-Tool $pgCtl @('register', '-N', 'SimulationWorkbenchPostgreSQL', '-D', $data, '-S', 'auto') }
    if (-not $service -or $service.Status -ne 'Running') { Start-Service -Name SimulationWorkbenchPostgreSQL -ErrorAction Stop }
    $url = "postgresql://postgres:$([Uri]::EscapeDataString($adminPassword))@127.0.0.1:$Port/postgres"
    return $url
}
function Install-WinSwService([string]$WinSw, [string]$Name, [string]$Executable, [string]$Arguments, [string]$WorkingDirectory, [string]$LogPath, [string]$ServiceAccount) {
    if ($ServiceAccount -ine 'NT AUTHORITY\LOCAL SERVICE' -and $ServiceAccount -ine 'NT AUTHORITY\LocalService') { Fail 'Only LocalService can run bundled application services.' }
    $serviceDir = Join-Path $StateRoot 'services'; New-Item -ItemType Directory -Force -Path $serviceDir, $LogPath | Out-Null
    $exe = Join-Path $serviceDir "$Name.exe"; $xml = Join-Path $serviceDir "$Name.xml"
    $null = Get-OwnedService $Name $exe '' 'NT AUTHORITY\LocalService'
    $old = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($old -and $old.Status -ne 'Stopped') { Stop-Service -Name $Name -Force -ErrorAction Stop; $old.WaitForStatus('Stopped', (New-TimeSpan -Seconds 30)) }
    if ($old -and -not (Test-Path -LiteralPath $exe -PathType Leaf)) { Fail "managed service wrapper is missing: $Name" }
    if (-not $old -and (Test-Path -LiteralPath $exe -PathType Leaf)) {
        if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $WinSw -Algorithm SHA256).Hash) {
            Fail "unregistered service wrapper differs from the bundle: $Name"
        }
    }
    elseif (-not $old) { Copy-Item -LiteralPath $WinSw -Destination $exe }
    $pythonPathLine = if ($Name -eq 'SimulationWorkbenchApi') { '<env name="PYTHONPATH" value="' + [Security.SecurityElement]::Escape($WorkingDirectory) + '" />' } else { '' }
    $dependencyLines = if ($Name -eq 'SimulationWorkbenchApi' -and $pgMode -eq 'bundled') { '<depend>SimulationWorkbenchPostgreSQL</depend>' }
                       elseif ($Name -eq 'SimulationWorkbenchProxy') { '<depend>SimulationWorkbenchApi</depend>' }
                       else { '' }
    $xmlText = @"
<service>
  <id>$Name</id><name>$Name</name><description>Simulation Workbench offline service</description>
  <executable>$([Security.SecurityElement]::Escape($Executable))</executable>
  <arguments>$([Security.SecurityElement]::Escape($Arguments))</arguments>
  <workingdirectory>$([Security.SecurityElement]::Escape($WorkingDirectory))</workingdirectory>
  $pythonPathLine
  <logpath>$([Security.SecurityElement]::Escape($LogPath))</logpath><log mode="roll" />
  $dependencyLines
  <onfailure action="restart" delay="10 sec" />
  <serviceaccount><username>NT AUTHORITY\LocalService</username></serviceaccount><startmode>Automatic</startmode>
</service>
"@
    $xmlTemporary = "$xml.$PID.tmp"
    [IO.File]::WriteAllText($xmlTemporary, $xmlText, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $xmlTemporary -Destination $xml -Force
    foreach ($directory in @($InstallRoot, $ReleasesRoot, $StateRoot, $TargetRelease, (Join-Path $TargetRelease 'frontend'))) {
        if (Test-Path -LiteralPath $directory -PathType Container) { Set-ManagedAcl $directory $ServiceAccount 'read' $false }
    }
    Set-SecretAcl (Join-Path $TargetRelease 'backend') $ServiceAccount -Recurse
    Set-SecretAcl (Join-Path $TargetRelease 'frontend\dist') $ServiceAccount -Recurse
    Set-SecretAcl (Join-Path $TargetRelease 'runtime') $ServiceAccount -Recurse
    Set-SecretAcl $serviceDir $ServiceAccount -Recurse
    Set-PrivateAcl $LogPath $ServiceAccount -Recurse
    if (-not $old) { & $exe install | Out-Host; if ($LASTEXITCODE -ne 0) { Fail "could not install service $Name" } }
    foreach ($secret in @('POSTGRES_ADMIN_URL', 'POSTGRES_OWNER_URL', 'SIM_DASH_OWNER_PASSWORD', 'SIM_DASH_APP_PASSWORD')) {
        Remove-Item -LiteralPath "Env:$secret" -ErrorAction SilentlyContinue
    }
    Start-Service -Name $Name -ErrorAction Stop
}
function Wait-Health([string]$Url, [int]$TimeoutSeconds = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $bodyText = Get-OfflineHttpBody $Url
            if ($bodyText) { $body = $bodyText | ConvertFrom-Json; if ($body.status -eq 'ok' -and $body.database_backend -eq 'postgresql') { return } }
        } catch { }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    Fail "service readiness timed out: $Url"
}
function Get-OfflineHttpResponse([string]$Url) {
    $uri = $null
    if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -notin @('http', 'https')) { Fail 'invalid local readiness URL.' }
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(5)
    try {
        $response = $client.GetAsync($uri).GetAwaiter().GetResult()
        try {
            $contentType = if ($response.Content.Headers.ContentType) { [string]$response.Content.Headers.ContentType.MediaType } else { '' }
            return [pscustomobject]@{
                StatusCode = [int]$response.StatusCode
                ContentType = $contentType
                Body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            }
        }
        finally { $response.Dispose() }
    }
    finally { $client.Dispose(); $handler.Dispose() }
}
function Get-DatabaseIdentity([string]$Connection) {
    $uri = $null
    $normalized = $Connection.Replace('postgresql+psycopg://', 'postgresql://')
    if (-not [Uri]::TryCreate($normalized, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne 'postgresql' -or -not $uri.Host -or $uri.Fragment) { Fail 'A PostgreSQL connection URL is invalid.' }
    # SQLAlchemy and libpq can let query parameters override the URL's host,
    # port, or database. Backup tooling parses the authority separately, so
    # accepting such overrides could inspect one DB and run the app on another.
    foreach ($parameter in $uri.Query.TrimStart('?').Split('&')) {
        if (-not $parameter) { continue }
        $name = [Uri]::UnescapeDataString(($parameter -split '=', 2)[0].Replace('+', ' ')).ToLowerInvariant()
        if ($name -in @('host', 'hostaddr', 'port', 'dbname', 'database', 'service', 'servicefile', 'options')) {
            Fail 'A PostgreSQL connection URL must not override its target in query parameters.'
        }
    }
    $database = [Uri]::UnescapeDataString($uri.AbsolutePath.TrimStart('/'))
    if (-not $database -or $database.Contains('/')) { Fail 'A PostgreSQL connection must identify exactly one database.' }
    return [pscustomobject]@{ Host=$uri.Host.ToLowerInvariant(); Port=$(if ($uri.Port -lt 0) {5432} else {$uri.Port}); Database=$database }
}
function Assert-DatabaseConnectionPair([string]$AppUrl, [string]$OwnerUrl, [string]$Mode, [int]$Port) {
    if (-not $AppUrl -and -not $OwnerUrl) { return }
    if (-not $AppUrl -or -not $OwnerUrl) { Fail 'An existing app database requires both application and owner connection URLs.' }
    $appIdentity = Get-DatabaseIdentity $AppUrl
    $ownerIdentity = Get-DatabaseIdentity $OwnerUrl
    if ($appIdentity.Host -ne $ownerIdentity.Host -or $appIdentity.Port -ne $ownerIdentity.Port -or $appIdentity.Database -cne $ownerIdentity.Database) { Fail 'Application and owner connections must refer to the same PostgreSQL database.' }
    if ($Mode -eq 'bundled' -and ($appIdentity.Host -ne '127.0.0.1' -or $appIdentity.Port -ne $Port -or $appIdentity.Database -cne 'simulation_dashboard')) { Fail 'Bundled mode cannot connect to another PostgreSQL database. Existing settings were preserved.' }
}
function Get-OfflineHttpBody([string]$Url) {
    try {
        $response = Get-OfflineHttpResponse $Url
        if ($response.StatusCode -eq 200) { return [string]$response.Body }
    }
    catch { }
    return ''
}
function Wait-StaticHome([string]$Url, [int]$TimeoutSeconds = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $body = Get-OfflineHttpBody $Url
            if ($body -match '<html' -and $body -match '(?i)(?:src|href)=["'']([^"'']*?/assets/[^"'']+\.(?:js|css))') {
                $assetUrl = [Uri]::new(([Uri]$Url), $Matches[1]).AbsoluteUri
                $asset = Get-OfflineHttpResponse $assetUrl
                if ($asset.StatusCode -eq 200 -and $asset.ContentType -match '(javascript|css)' -and $asset.Body.Length -gt 100) { return }
            }
        } catch { }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    Fail "frontend readiness timed out: $Url"
}

$manifest = Verify-BundleManifest
$os = Get-CimInstance Win32_OperatingSystem
if ($os.Caption -notmatch 'Windows Server 2022' -or -not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) { Fail 'Windows Server 2022 with x64 PowerShell is required.' }
if ($Check) { Write-Host "Offline bundle $($manifest.release_id) passed integrity and host checks." -ForegroundColor Green; exit 0 }
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($NonInteractive) { Fail 'Unattended installation requires an elevated PowerShell.' }
    $elevatedArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'))
    if ($Config) { $elevatedArgs += @('-Config', ('"' + [IO.Path]::GetFullPath($Config) + '"')) }
    if ($SkipAccountSetup) { $elevatedArgs += '-SkipAccountSetup' }
    if ($SkipFirewall) { $elevatedArgs += '-SkipFirewall' }
    $elevated = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -Verb RunAs -ArgumentList $elevatedArgs -Wait -PassThru
    exit $elevated.ExitCode
}

# Saved non-secret settings make every new package use the same deployment.
if ($Config) { $cfg = Read-Json ([IO.Path]::GetFullPath($Config)) }
else {
    $cfg = Read-Json (Join-Path $BundleRoot 'config\install.example.json')
    if (-not $NonInteractive) {
        $chosenRoot = Read-Host "Installation directory [$($cfg.installRoot)]"
        if ($chosenRoot) { $cfg.installRoot = $chosenRoot }
    }
    $savedConfig = Join-Path $cfg.installRoot 'state\install-settings.json'
    if (Test-Path -LiteralPath $savedConfig -PathType Leaf) { $cfg = Read-Json $savedConfig; Write-Host 'Using the existing installation settings.' -ForegroundColor Cyan }
    elseif ($NonInteractive) { Fail 'First unattended installation requires -Config with explicit settings.' }
    else {
        $mode = Read-Host 'PostgreSQL: 1 = install bundled server, 2 = connect to an existing server [1]'
        if ($mode -and $mode -notin @('1', '2')) { Fail 'Choose PostgreSQL mode 1 or 2.' }
        if ($mode -eq '2') {
            $cfg.postgresMode = 'existing'
            $dbState = Read-Host 'Existing server: 1 = no app database / empty target, 2 = existing application database [2]'
            if ($dbState -and $dbState -notin @('1', '2')) { Fail 'Choose database state 1 or 2.' }
            $names = if ($dbState -eq '1') { @('postgresAdminUrl') } else { @('databaseUrl', 'ownerUrl') }
            foreach ($name in $names) {
                $secret = Read-Host "$name (PostgreSQL connection URL; hidden input)" -AsSecureString
                $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
                try { $cfg.$name = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
                finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer); $secret.Dispose() }
                if (-not $cfg.$name) { Fail "$name is required." }
            }
        }
    }
}
$InstallRoot = [IO.Path]::GetFullPath((Get-ConfigValue $cfg 'installRoot' 'C:\ProgramData\SimulationWorkbench'))
if ($InstallRoot -notmatch '^[A-Za-z]:\\.+' -or $InstallRoot -eq [IO.Path]::GetPathRoot($InstallRoot) -or $InstallRoot -match '["\r\n]') { Fail 'Use a dedicated absolute local installation directory.' }
for ($ancestor = $InstallRoot; $ancestor; $ancestor = Split-Path -Parent $ancestor) {
    if ((Test-Path -LiteralPath $ancestor) -and ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { Fail 'Installation path must not contain links or junctions.' }
}
$StateRoot = Join-Path $InstallRoot 'state'
$ReleasesRoot = Join-Path $InstallRoot 'releases'
foreach ($path in @($StateRoot, $ReleasesRoot, (Join-Path $StateRoot 'install-settings.json'), (Join-Path $StateRoot '.env'), (Join-Path $StateRoot '.postgres-owner.env'), (Join-Path $StateRoot 'assets'))) {
    if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { Fail 'Installed state must contain physical files and directories.' }
}
$pgMode = (Get-ConfigValue $cfg 'postgresMode' 'bundled').ToLowerInvariant()
$serviceAccount = 'NT AUTHORITY\LOCAL SERVICE'
$apiPort = [int](Get-ConfigValue $cfg 'apiPort' 18080)
$webPort = [int](Get-ConfigValue $cfg 'webPort' 8080)
$pgPort = [int](Get-ConfigValue $cfg 'postgresPort' 55432)
if ($pgMode -notin @('bundled', 'existing')) { Fail 'postgresMode must be bundled or existing.' }
if ($apiPort -lt 1024 -or $apiPort -gt 65535 -or $webPort -lt 1 -or $webPort -gt 65535 -or $pgPort -lt 1024 -or $pgPort -gt 65535 -or @($apiPort, $webPort, $pgPort | Select-Object -Unique).Count -ne 3) { Fail 'Choose three distinct valid API/web/PostgreSQL ports.' }
if ((Get-ConfigValue $cfg 'serviceAccount' $serviceAccount) -ine $serviceAccount) { Fail 'The application services use the built-in LocalService account.' }
foreach ($pair in @(@('database','simulation_dashboard'), @('ownerRole','simdashboard_owner'), @('appRole','simdashboard_app'))) {
    if ((Get-ConfigValue $cfg $pair[0] $pair[1]) -ne $pair[1]) { Fail 'Automatic provisioning uses the documented fixed database and role names.' }
}
$tlsMode = (Get-ConfigValue $cfg 'tlsMode' 'http').ToLowerInvariant()
$https = $tlsMode -eq 'certificate'
if ($tlsMode -notin @('http', 'certificate')) { Fail 'tlsMode must be http or certificate.' }
$serverName = Get-ConfigValue $cfg 'serverName' '_'
if ($serverName -notmatch '^[_A-Za-z0-9.-]+$') { Fail 'serverName must be a hostname or IPv4 address.' }
$cert = Get-ConfigValue $cfg 'tlsCertificate'
$key = Get-ConfigValue $cfg 'tlsKey'
if ($https) {
    if ($serverName -eq '_') { Fail 'HTTPS requires the hostname covered by the supplied certificate.' }
    Require-File $cert 'TLS certificate'; Require-File $key 'TLS key'
}
$settingsPath = Join-Path $StateRoot 'install-settings.json'
if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
    $knownSettings = Read-Json $settingsPath
    if ($pgMode -ne (Get-ConfigValue $knownSettings 'postgresMode' 'bundled') -or $pgPort -ne [int](Get-ConfigValue $knownSettings 'postgresPort' 55432)) { Fail 'An app update cannot change the installed PostgreSQL mode or port. Use the database transfer procedure.' }
    foreach ($pair in @(@('databaseUrl', '.env', 'DATABASE_URL'), @('ownerUrl', '.postgres-owner.env', 'POSTGRES_OWNER_URL'))) {
        $proposed = Get-ConfigValue $cfg $pair[0]
        $known = Get-EnvValue (Join-Path $StateRoot $pair[1]) $pair[2]
        if ($proposed -and $known -and $proposed -cne $known) { Fail 'Supplied database credentials conflict with the existing installation. Stored credentials were preserved.' }
    }
}
$effectiveAppUrl = Get-EnvValue (Join-Path $StateRoot '.env') 'DATABASE_URL'
$effectiveOwnerUrl = Get-EnvValue (Join-Path $StateRoot '.postgres-owner.env') 'POSTGRES_OWNER_URL'
if (-not $effectiveAppUrl) { $effectiveAppUrl = Get-ConfigValue $cfg 'databaseUrl' }
if (-not $effectiveOwnerUrl) { $effectiveOwnerUrl = Get-ConfigValue $cfg 'ownerUrl' }
Assert-DatabaseConnectionPair $effectiveAppUrl $effectiveOwnerUrl $pgMode $pgPort
if ($pgMode -eq 'existing' -and -not $NonInteractive -and -not (Get-ConfigValue $cfg 'postgresAdminUrl') -and -not (Get-ConfigValue $cfg 'databaseUrl') -and -not (Get-EnvValue (Join-Path $StateRoot '.env') 'DATABASE_URL')) {
    $secret = Read-Host 'PostgreSQL administrator URL for retrying the unfinished empty-target setup (hidden input)' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try { $cfg.postgresAdminUrl = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer); $secret.Dispose() }
}
$releaseId = [string]$manifest.release_id
$releaseSource = Join-Path $BundleRoot "release\$releaseId"
$bootstrapPython = Join-Path $BundleRoot 'runtime\python\python.exe'
$vcRedist = Join-Path $BundleRoot 'runtime\vc_redist.x64.exe'
$winsw = Join-Path $BundleRoot 'runtime\winsw\WinSW-x64.exe'
foreach ($file in @($bootstrapPython, $vcRedist, $winsw, (Join-Path $releaseSource 'backend\requirements.lock'), (Join-Path $releaseSource 'frontend\dist\index.html'))) { Require-File $file 'required offline payload' }
if (Test-Path -LiteralPath (Join-Path $StateRoot '.setup-recovery-required.json')) { Fail 'A prior database bootstrap requires recovery. Review state/.setup-recovery-required.json.' }
if (Test-Path -LiteralPath $InstallRoot) {
    if ((Get-ChildItem -LiteralPath $InstallRoot -Force | Measure-Object).Count -and -not (Test-Path -LiteralPath (Join-Path $StateRoot 'install-settings.json'))) { Fail 'The chosen directory contains files from an unrecognized installation. Use its supported transfer procedure or another directory.' }
}
$lock = New-Object Threading.Mutex($false, 'Global\SimulationWorkbenchOfflineInstaller')
$lockHeld = $false
$appStopped = $false
$TargetRelease = ''
$phase = 'prepare'
$savedEnvironment = @{}
$environmentCaptured = $false
try {
    try { $lockHeld = $lock.WaitOne(0) } catch [Threading.AbandonedMutexException] { $lockHeld = $true }
    if (-not $lockHeld) { Fail 'Another offline installation is running.' }
    # A shell's source-PC credentials must never override the installed files.
    $environmentCaptured = $true
    foreach ($item in Get-ChildItem Env:) {
        if ($item.Name -match '^(DATABASE_URL|ANALYSIS_|AUTH_|OIDC_|POSTGRES_|SIM_DASH_|SIMDASH_|DEPLOYMENT_PROFILE|PYTHONPATH|PYTHONIOENCODING|PIP_)') {
            $savedEnvironment[$item.Name] = $item.Value
            Remove-Item -LiteralPath "Env:$($item.Name)"
        }
    }
    New-Item -ItemType Directory -Path $StateRoot, $ReleasesRoot -Force | Out-Null
    Set-SecretAcl $InstallRoot $serviceAccount
    Set-AdminOnlyAcl $StateRoot
    $stateEnv = Join-Path $StateRoot '.env'
    $stateOwner = Join-Path $StateRoot '.postgres-owner.env'
    if (-not (Test-Path -LiteralPath $stateEnv)) { [IO.File]::WriteAllText($stateEnv, "ANALYSIS_DB_BACKEND=postgresql`nAUTH_MODE=password`n", (New-Object Text.UTF8Encoding($false))) }
    if (-not (Test-Path -LiteralPath $stateOwner)) { [IO.File]::WriteAllText($stateOwner, '', (New-Object Text.UTF8Encoding($false))) }
    $existingUrl = Get-EnvValue $stateEnv 'DATABASE_URL'
    $adminUrl = Get-ConfigValue $cfg 'postgresAdminUrl'
    if (-not $existingUrl -and (Get-ConfigValue $cfg 'databaseUrl')) { Set-EnvValue $stateEnv 'DATABASE_URL' $cfg.databaseUrl; $existingUrl = $cfg.databaseUrl }
    if (-not (Get-EnvValue $stateOwner 'POSTGRES_OWNER_URL') -and (Get-ConfigValue $cfg 'ownerUrl')) { Set-EnvValue $stateOwner 'POSTGRES_OWNER_URL' $cfg.ownerUrl }
    if ((Get-EnvValue $stateEnv 'ANALYSIS_DB_BACKEND') -ne 'postgresql') { Fail 'Existing database selection must be PostgreSQL; automatic conversion is forbidden.' }
    if ((Get-EnvValue $stateEnv 'AUTH_MODE') -ne 'password') { Fail 'This installer preserves password-auth installations; use a separate reviewed procedure for OIDC.' }
    if ($pgMode -eq 'existing' -and -not ($existingUrl -or $adminUrl)) { Fail 'Existing PostgreSQL requires app/owner connections or an admin connection for an empty target.' }
    # Save only operational choices. Database credentials live in protected env files.
    $persisted = $cfg | ConvertTo-Json -Depth 5 | ConvertFrom-Json
    foreach ($name in @('postgresAdminUrl', 'databaseUrl', 'ownerUrl')) { $persisted | Add-Member -NotePropertyName $name -NotePropertyValue '' -Force }
    if (-not (Test-Path -LiteralPath $settingsPath)) { $persisted | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $settingsPath -Encoding UTF8 }
    $vcProcess = Start-Process -FilePath $vcRedist -ArgumentList @('/install', '/quiet', '/norestart') -WindowStyle Hidden -Wait -PassThru
    if ($vcProcess.ExitCode -notin @(0, 1638, 3010)) { Fail "VC++ runtime installation failed ($($vcProcess.ExitCode))." }
    if ($vcProcess.ExitCode -eq 3010) { Fail 'The runtime requires a Windows restart. Restart Windows, then rerun this installer.' }
    # A fresh directory per attempt avoids mutating an active release and allows retry.
    $TargetRelease = Join-Path $ReleasesRoot ($releaseId + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
    New-Item -ItemType Directory -Path $TargetRelease -Force | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $releaseSource -Force) { Copy-Item -LiteralPath $item.FullName -Destination $TargetRelease -Recurse }
    $runtimeTarget = Join-Path $TargetRelease 'runtime'
    New-Item -ItemType Directory -Path $runtimeTarget -Force | Out-Null
    foreach ($name in @('python', 'postgresql', 'caddy')) { Copy-Item -LiteralPath (Join-Path $BundleRoot "runtime\$name") -Destination $runtimeTarget -Recurse }
    $venv = Join-Path $runtimeTarget 'venv'
    Invoke-Tool (Join-Path $runtimeTarget 'python\python.exe') @('-I', '-m', 'venv', $venv)
    $python = Join-Path $venv 'Scripts\python.exe'
    Invoke-Tool $python @('-I', '-m', 'pip', '--isolated', 'install', '--no-index', '--only-binary=:all:', '--disable-pip-version-check', '--find-links', (Join-Path $BundleRoot 'runtime\wheelhouse'), '-r', (Join-Path $TargetRelease 'backend\requirements.lock'))
    Invoke-Tool $python @('-I', '-m', 'pip', '--isolated', 'check')
    Invoke-Tool $python @('-I', '-c', 'import fastapi,psycopg,uvicorn,alembic,cryptography,duckdb')
    $env:POSTGRES_BIN = Join-Path $runtimeTarget 'postgresql\bin'
    $env:PYTHONIOENCODING = 'utf-8'
    $caddy = Join-Path $runtimeTarget 'caddy\caddy.exe'
    $backend = Join-Path $TargetRelease 'backend'
    function Sync-StateFiles([ValidateSet('stage','sync')][string]$Action) {
        Invoke-Python $python (Join-Path $backend 'scripts\offline_deployment_environment.py') @($Action, '--state-dir', $StateRoot, '--release-dir', $TargetRelease) $backend
        Set-SecretAcl (Join-Path $TargetRelease '.env') $serviceAccount
        Set-AdminOnlyAcl (Join-Path $TargetRelease '.postgres-owner.env')
        Set-AdminOnlyAcl $stateEnv; Set-AdminOnlyAcl $stateOwner
    }
    $assets = Join-Path $StateRoot 'assets'
    $logs = Join-Path $StateRoot 'logs'
    New-Item -ItemType Directory -Path $assets, $logs -Force | Out-Null
    Set-PrivateAcl $assets $serviceAccount -Recurse; Set-PrivateAcl $logs $serviceAccount -Recurse
    Set-EnvValue $stateEnv 'SIMDASH_ASSETS_ROOT' $assets
    $phase = 'stop-services'
    Stop-ManagedServices
    $appStopped = $true
    $phase = 'database-prepare'
    if ($pgMode -eq 'bundled') {
        $stablePg = Join-Path $InstallRoot 'postgresql'
        if (-not (Test-Path -LiteralPath $stablePg)) { Copy-Item -LiteralPath (Join-Path $BundleRoot 'runtime\postgresql') -Destination $stablePg -Recurse }
        # Existing binary/data major stays unchanged when the app is updated.
        $adminUrl = Initialize-BundledPostgres $stablePg $StateRoot $pgPort
        Set-AdminOnlyAcl (Join-Path $StateRoot 'postgres')
    }
    Sync-StateFiles 'stage'
    if (-not $existingUrl) {
        if (-not $adminUrl) { Fail 'Initial database provisioning requires an administrator connection.' }
        $env:POSTGRES_ADMIN_URL = $adminUrl
        try { Invoke-Python $python (Join-Path $backend 'scripts\setup_local_postgres.py') @('--seed-mode', 'empty') $backend }
        finally { Remove-Item Env:POSTGRES_ADMIN_URL -ErrorAction SilentlyContinue; $adminUrl = $null }
        Sync-StateFiles 'sync'
    }
    # Even initial configured empty targets produce a verified classification
    # manifest. Never infer emptiness from a connection error or missing dump.
    $phase = 'backup'
    Invoke-Python $python (Join-Path $backend 'scripts\check_database_startup_preflight.py') @() $backend
    Invoke-Python $python (Join-Path $backend 'scripts\prepare_account_deployment.py') @('--project-root', $TargetRelease) $backend
    $backupDirs = @(Get-ChildItem -LiteralPath (Join-Path $TargetRelease 'backups\accounts') -Directory | Sort-Object Name)
    if ($backupDirs.Count -ne 1) { Fail 'Expected exactly one verified backup for this release attempt.' }
    $backupSource = $backupDirs[0].FullName
    $durableBackup = Join-Path $StateRoot ('backups\' + $backupDirs[0].Name)
    New-Item -ItemType Directory -Path (Split-Path -Parent $durableBackup) -Force | Out-Null
    Copy-Item -LiteralPath $backupSource -Destination $durableBackup -Recurse
    foreach ($file in Get-ChildItem -LiteralPath $backupSource -Recurse -Force -File) {
        $copy = Join-Path $durableBackup $file.FullName.Substring($backupSource.Length + 1)
        if ((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash) { Fail 'Persistent backup verification failed.' }
    }
    Set-AdminOnlyAcl $durableBackup -Recurse
    $phase = 'migration'
    Invoke-Python $python (Join-Path $backend 'scripts\upgrade_postgres_schema.py') @('--allow-empty-bootstrap') $backend
    Invoke-Python $python (Join-Path $backend 'scripts\check_postgres_schema.py') @() $backend
    Set-EnvValue $stateEnv 'AUTH_COOKIE_SECURE' ($(if ($https) {'true'} else {'false'}))
    Set-EnvValue $stateEnv 'DEPLOYMENT_PROFILE' ($(if ($https) {'windows-password-intranet'} else {'windows-server-intranet-http'}))
    Sync-StateFiles 'stage'
    $phase = 'accounts'
    if (-not $SkipAccountSetup) {
        $accountArgs = if ($NonInteractive) { @('--non-interactive') } else { @() }
        Invoke-Python $python (Join-Path $backend 'scripts\setup_accounts.py') $accountArgs $backend
        Sync-StateFiles 'sync'
    }
    Invoke-Python $python (Join-Path $backend 'scripts\setup_accounts.py') @('--check') $backend
    Invoke-Python $python (Join-Path $backend 'scripts\check_deployment_profile.py') @() $backend
    $phase = 'services'
    Set-SecretAcl $TargetRelease $serviceAccount
    Set-AdminOnlyAcl (Join-Path $TargetRelease '.postgres-owner.env')
    Set-AdminOnlyAcl (Join-Path $TargetRelease 'backups') -Recurse
    $tlsLine = ''
    if ($https) {
        $certDir = Join-Path $StateRoot 'certificates'
        New-Item -ItemType Directory -Path $certDir -Force | Out-Null
        $stableCert = Join-Path $certDir 'server.crt'; $stableKey = Join-Path $certDir 'server.key'
        if ([IO.Path]::GetFullPath($cert) -ine $stableCert) { Copy-Item -LiteralPath $cert -Destination $stableCert -Force }
        if ([IO.Path]::GetFullPath($key) -ine $stableKey) { Copy-Item -LiteralPath $key -Destination $stableKey -Force }
        Set-SecretAcl $certDir $serviceAccount -Recurse
        $persisted.tlsCertificate = $stableCert; $persisted.tlsKey = $stableKey
        $tlsLine = '    tls "' + $stableCert.Replace('\','/') + '" "' + $stableKey.Replace('\','/') + '"'
    }
    $scheme = if ($https) { 'https' } else { 'http' }
    $siteAddress = if ($serverName -eq '_') { "${scheme}://:$webPort" } else { "${scheme}://${serverName}:$webPort" }
    $staticRoot = (Join-Path $TargetRelease 'frontend\dist').Replace('\','/')
    $caddyConfig = Join-Path $StateRoot 'Caddyfile'
    $caddyText = @"
{
    admin off
    auto_https off
}
$siteAddress {
$tlsLine
    encode zstd gzip
    root * "$staticRoot"
    redir / /home/ 308
    redir /home /home/ 308
    @backend path /api /api/* /assets/* /health
    handle @backend {
        reverse_proxy 127.0.0.1:$apiPort
    }
    handle_path /home/* {
        try_files {path} /index.html
        file_server
    }
}
"@
    [IO.File]::WriteAllText($caddyConfig, $caddyText, (New-Object Text.UTF8Encoding($false)))
    Set-SecretAcl $caddyConfig $serviceAccount
    Invoke-Tool $caddy @('validate', '--config', $caddyConfig, '--adapter', 'caddyfile')
    Install-WinSwService $winsw 'SimulationWorkbenchApi' $python "-m uvicorn app.main:app --host 127.0.0.1 --port $apiPort" $backend $logs $serviceAccount
    Install-WinSwService $winsw 'SimulationWorkbenchProxy' $caddy "run --config `"$caddyConfig`" --adapter caddyfile" $StateRoot $logs $serviceAccount
    $phase = 'health'
    $healthHost = if ($serverName -eq '_') { '127.0.0.1' } else { $serverName }
    Wait-Health "http://127.0.0.1:$apiPort/api/health"
    Wait-Health "${scheme}://${healthHost}:$webPort/api/health"
    Wait-StaticHome "${scheme}://${healthHost}:$webPort/home/"
    if (-not $SkipFirewall) {
        $rule = Get-NetFirewallRule -Name 'SimulationWorkbenchWeb' -ErrorAction SilentlyContinue
        if ($rule) { $rule | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -LocalPort $webPort | Out-Null }
        else { New-NetFirewallRule -Name 'SimulationWorkbenchWeb' -DisplayName 'Simulation Workbench Web' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $webPort -RemoteAddress LocalSubnet -Profile Any | Out-Null }
    }
    $persisted | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $StateRoot 'install-settings.json') -Encoding UTF8
    $stamp = @{ release_id=$releaseId; release_directory=(Split-Path -Leaf $TargetRelease); installed_at_utc=[DateTime]::UtcNow.ToString('o'); api_port=$apiPort; web_port=$webPort; backup_directory=$durableBackup }
    $stamp | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $StateRoot 'current.json') -Encoding UTF8
    Remove-Item -LiteralPath (Join-Path $StateRoot 'recovery-required.json') -Force -ErrorAction SilentlyContinue
    $displayHost = if ($serverName -eq '_') { $env:COMPUTERNAME } else { $serverName }
    Write-Host "Installed and healthy: ${scheme}://${displayHost}:$webPort/home/" -ForegroundColor Green
} catch {
    if ($appStopped) { try { Stop-ManagedServices } catch { Write-Host 'Check Windows services: automatic stop could not be completed.' -ForegroundColor Red } }
    if ($lockHeld -and (Test-Path -LiteralPath $StateRoot)) {
        @{ release_id=$releaseId; phase=$phase; app_stopped=$appStopped; failed_at_utc=[DateTime]::UtcNow.ToString('o'); action='Review this phase and retained backup before retrying. Do not reset the database.' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $StateRoot 'recovery-required.json') -Encoding UTF8
    }
    Write-Host "Installation failed in phase '$phase': $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if ($TargetRelease -and (Test-Path -LiteralPath (Join-Path $TargetRelease '.setup-recovery-required.json'))) { Copy-Item -LiteralPath (Join-Path $TargetRelease '.setup-recovery-required.json') -Destination (Join-Path $StateRoot '.setup-recovery-required.json') -Force }
    if ($environmentCaptured) {
        foreach ($item in @(Get-ChildItem Env:)) { if ($item.Name -match '^(DATABASE_URL|ANALYSIS_|AUTH_|OIDC_|POSTGRES_|SIM_DASH_|SIMDASH_|DEPLOYMENT_PROFILE|PYTHONPATH|PYTHONIOENCODING|PIP_)') { Remove-Item -LiteralPath "Env:$($item.Name)" } }
        foreach ($name in $savedEnvironment.Keys) { Set-Item -LiteralPath "Env:$name" -Value $savedEnvironment[$name] }
    }
    if ($lockHeld) { $lock.ReleaseMutex() }
    $lock.Dispose()
}
