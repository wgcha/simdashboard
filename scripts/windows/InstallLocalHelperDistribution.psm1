Set-StrictMode -Version Latest

# Imports a pre-built Windows helper release.  This module deliberately never
# builds the executable and accepts only the public manifest plus its
# versioned ZIP; credentials and browser/session state are not part of this
# channel.
$script:ManifestName = 'distribution-manifest.json'
$script:MaximumManifestBytes = [int64]65536
$script:MaximumArchiveBytes = [int64]1073741824
$script:VersionPattern = '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'

function Get-LocalHelperDistributionDefaultDirectory {
    param([string]$ProjectRoot = '')
    if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
    return [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'dist\local-helper\windows-x64'))
}

function Assert-NonRootDirectory {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Name)
    $full = [IO.Path]::GetFullPath($Path).TrimEnd([char]92, [char]47)
    if (-not $full -or $full -eq [IO.Path]::GetPathRoot($full).TrimEnd([char]92, [char]47)) { throw "$Name cannot be a filesystem root." }
    return $full
}

function Assert-ChildPath {
    param([Parameter(Mandatory = $true)][string]$Candidate, [Parameter(Mandatory = $true)][string]$Parent, [Parameter(Mandatory = $true)][string]$Name)
    $candidateFull = [IO.Path]::GetFullPath($Candidate)
    $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd([char]92, [char]47) + [IO.Path]::DirectorySeparatorChar
    if (-not $candidateFull.StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase)) { throw "$Name escaped its permitted directory." }
    return $candidateFull
}

function Assert-NoReparsePointAncestors {
    param([Parameter(Mandatory = $true)][string]$Path, [switch]$MustExist, [Parameter(Mandatory = $true)][string]$Name)
    $full = [IO.Path]::GetFullPath($Path)
    if ($MustExist -and -not (Test-Path -LiteralPath $full)) { throw "$Name does not exist." }
    $cursor = $full
    while (-not (Test-Path -LiteralPath $cursor)) { $cursor = Split-Path -Parent $cursor; if (-not $cursor) { throw "$Name has no existing parent." } }
    while ($cursor) {
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "$Name cannot be a symbolic link or junction." }
        $parent = Split-Path -Parent $cursor
        if (-not $parent -or $parent -eq $cursor) { break }; $cursor = $parent
    }
    return $full
}

function Get-OrdinaryFile {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Name)
    Assert-NoReparsePointAncestors -Path $Path -MustExist -Name $Name | Out-Null
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw "$Name must be a regular file." }
    return $item
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-DistributionManifest {
    param([Parameter(Mandatory = $true)][string]$ManifestPath)
    $item = Get-OrdinaryFile -Path $ManifestPath -Name 'Distribution manifest'
    if ($item.PSIsContainer -or $item.Length -gt $script:MaximumManifestBytes) { throw 'The distribution manifest exceeds its safe size limit.' }
    try { $raw = [IO.File]::ReadAllText($ManifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json -ErrorAction Stop }
    catch { throw 'The distribution manifest is not valid UTF-8 JSON.' }
    if ($null -eq $raw -or $raw.format -ne 1) { throw 'The distribution manifest format is unsupported.' }
    $version = ([string]$raw.version).Trim()
    $filename = [string]$raw.filename
    $sha256 = ([string]$raw.sha256).ToLowerInvariant()
    $size = [int64]0
    if (-not [int64]::TryParse([string]$raw.size_bytes, [ref]$size)) { throw 'The distribution manifest size is invalid.' }
    if ($version -notmatch $script:VersionPattern -or $filename -notmatch '^[A-Za-z0-9._-]+\.zip$' -or $sha256 -notmatch '^[a-f0-9]{64}$' -or $size -le 0 -or $size -gt $script:MaximumArchiveBytes) { throw 'The distribution manifest contains unsafe values.' }
    $expectedFilename = 'SimulationWorkbenchLocalHelper-{0}-windows-x64.zip' -f $version
    if ($filename -cne $expectedFilename) { throw 'The distribution filename does not match its version.' }
    # Validate release time here so a malformed manifest cannot be published.
    try { [DateTime]::Parse([string]$raw.released_at, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind) | Out-Null }
    catch { throw 'The distribution release time is invalid.' }
    return [pscustomobject]@{ Version = $version; Filename = $filename; Sha256 = $sha256; SizeBytes = $size }
}

function Save-HttpsFile {
    param([Parameter(Mandatory = $true)][Uri]$Uri, [Parameter(Mandatory = $true)][string]$Destination, [Parameter(Mandatory = $true)][int64]$MaximumBytes)
    if ($Uri.Scheme -ne 'https' -or $Uri.UserInfo -or $Uri.Query -or $Uri.Fragment) { throw 'Only a credential-free HTTPS manifest URL is allowed.' }
    $request = [Net.HttpWebRequest]::Create($Uri)
    $request.Method = 'GET'; $request.AllowAutoRedirect = $false; $request.Timeout = 30000; $request.ReadWriteTimeout = 30000
    $response = $null; $input = $null; $output = $null
    try {
        $response = [Net.HttpWebResponse]$request.GetResponse()
        if ([int]$response.StatusCode -ne 200 -or $response.ContentLength -gt $MaximumBytes) { throw 'The release server returned an invalid response.' }
        $input = $response.GetResponseStream(); $output = [IO.File]::Open($Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $buffer = New-Object byte[] 65536; $total = [int64]0
        while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $total += $read; if ($total -gt $MaximumBytes) { throw 'The release download exceeds its safe size limit.' }
            $output.Write($buffer, 0, $read)
        }
    }
    finally { if ($output) { $output.Dispose() }; if ($input) { $input.Dispose() }; if ($response) { $response.Dispose() } }
}

function Copy-ExactFile {
    param([Parameter(Mandatory = $true)][string]$Source, [Parameter(Mandatory = $true)][string]$Destination, [Parameter(Mandatory = $true)][int64]$ExpectedBytes)
    $sourceItem = Get-OrdinaryFile -Path $Source -Name 'Offline distribution archive'
    if ($sourceItem.Length -ne $ExpectedBytes) { throw 'The offline release archive size does not match its manifest.' }
    $input = $null; $output = $null
    try {
        $input = [IO.File]::Open($Source, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $output = [IO.File]::Open($Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $buffer = New-Object byte[] 65536; $total = [int64]0
        while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) { $total += $read; if ($total -gt $ExpectedBytes) { throw 'The offline release archive changed while being read.' }; $output.Write($buffer, 0, $read) }
        if ($total -ne $ExpectedBytes) { throw 'The offline release archive changed while being read.' }
    }
    finally { if ($output) { $output.Dispose() }; if ($input) { $input.Dispose() } }
}

function Install-LocalHelperDistribution {
    [CmdletBinding(DefaultParameterSetName = 'Offline')]
    param(
        [Parameter(ParameterSetName = 'Offline', Mandatory = $true)][string]$SourceDirectory,
        [Parameter(ParameterSetName = 'Online', Mandatory = $true)][Uri]$ManifestUrl,
        [string]$TargetDirectory = ''
    )
    if (-not $TargetDirectory) { $TargetDirectory = Get-LocalHelperDistributionDefaultDirectory }
    $target = Assert-NonRootDirectory -Path $TargetDirectory -Name 'Distribution target'
    Assert-NoReparsePointAncestors -Path $target -Name 'Distribution target' | Out-Null
    $stagingParent = Assert-ChildPath -Candidate (Join-Path $target '.staging') -Parent $target -Name 'Distribution staging'
    New-Item -ItemType Directory -Force -Path $target, $stagingParent | Out-Null
    Assert-NoReparsePointAncestors -Path $target -MustExist -Name 'Distribution target' | Out-Null
    Assert-NoReparsePointAncestors -Path $stagingParent -MustExist -Name 'Distribution staging' | Out-Null
    $lockPath = Assert-ChildPath -Candidate (Join-Path $target '.distribution-import.lock') -Parent $target -Name 'Distribution lock'
    if (Test-Path -LiteralPath $lockPath) { Get-OrdinaryFile -Path $lockPath -Name 'Distribution lock' | Out-Null }
    $lock = $null
    try { $lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
    catch { throw 'Another local helper distribution import is already running.' }
    $stage = Assert-ChildPath -Candidate (Join-Path $stagingParent ([Guid]::NewGuid().ToString('N'))) -Parent $stagingParent -Name 'Distribution staging'
    New-Item -ItemType Directory -Path $stage | Out-Null
    try {
        Assert-NoReparsePointAncestors -Path $stage -MustExist -Name 'Distribution staging' | Out-Null
        $stagedManifest = Join-Path $stage $script:ManifestName
        if ($PSCmdlet.ParameterSetName -eq 'Online') {
            Save-HttpsFile -Uri $ManifestUrl -Destination $stagedManifest -MaximumBytes $script:MaximumManifestBytes
        } else {
            $source = Assert-NonRootDirectory -Path $SourceDirectory -Name 'Offline distribution source'
            Assert-NoReparsePointAncestors -Path $source -MustExist -Name 'Offline distribution source' | Out-Null
            $sourceManifest = Assert-ChildPath -Candidate (Join-Path $source $script:ManifestName) -Parent $source -Name 'Offline manifest'
            $sourceItem = Get-OrdinaryFile -Path $sourceManifest -Name 'Offline distribution manifest'
            if ($sourceItem.Length -gt $script:MaximumManifestBytes) { throw 'The offline distribution manifest exceeds its safe size limit.' }
            [IO.File]::Copy($sourceManifest, $stagedManifest, $false)
        }
        $manifest = Read-DistributionManifest -ManifestPath $stagedManifest
        $stagedArchive = Join-Path $stage $manifest.Filename
        if ($PSCmdlet.ParameterSetName -eq 'Online') {
            $archiveUri = [Uri]::new([Uri]::new($ManifestUrl, '.'), $manifest.Filename)
            Save-HttpsFile -Uri $archiveUri -Destination $stagedArchive -MaximumBytes $manifest.SizeBytes
        } else {
            $sourceArchive = Assert-ChildPath -Candidate (Join-Path $source $manifest.Filename) -Parent $source -Name 'Offline archive'
            Copy-ExactFile -Source $sourceArchive -Destination $stagedArchive -ExpectedBytes $manifest.SizeBytes
        }
        if ((Get-OrdinaryFile -Path $stagedArchive -Name 'Staged archive').Length -ne $manifest.SizeBytes -or (Get-Sha256 $stagedArchive) -cne $manifest.Sha256) { throw 'The release archive does not match its manifest.' }
        # Hold the publisher lock from validation through this last target
        # reparse check and the manifest commit.
        Assert-NoReparsePointAncestors -Path $target -MustExist -Name 'Distribution target' | Out-Null
        Assert-NoReparsePointAncestors -Path $stagingParent -MustExist -Name 'Distribution staging' | Out-Null
        $publishedArchive = Assert-ChildPath -Candidate (Join-Path $target $manifest.Filename) -Parent $target -Name 'Published archive'
        if (Test-Path -LiteralPath $publishedArchive) {
            $existingArchive = Get-OrdinaryFile -Path $publishedArchive -Name 'Published archive'
            if ($existingArchive.Length -ne $manifest.SizeBytes -or (Get-Sha256 $publishedArchive) -cne $manifest.Sha256) { throw 'A different archive already occupies this versioned filename.' }
        } else { Move-Item -LiteralPath $stagedArchive -Destination $publishedArchive }
        $publishedManifest = Assert-ChildPath -Candidate (Join-Path $target $script:ManifestName) -Parent $target -Name 'Published manifest'
        $replacement = Join-Path $stage ('manifest-' + [Guid]::NewGuid().ToString('N') + '.tmp')
        [IO.File]::Copy($stagedManifest, $replacement, $false)
        $backup = Join-Path $stage ('manifest-' + [Guid]::NewGuid().ToString('N') + '.bak')
        if (Test-Path -LiteralPath $publishedManifest) { Get-OrdinaryFile -Path $publishedManifest -Name 'Published manifest' | Out-Null; [IO.File]::Replace($replacement, $publishedManifest, $backup, $true) }
        else { Move-Item -LiteralPath $replacement -Destination $publishedManifest }
        return [pscustomobject]@{ Status = 'ready'; Version = $manifest.Version; Filename = $manifest.Filename; TargetDirectory = $target }
    }
    finally {
        if (Test-Path -LiteralPath $stage) {
            try { Assert-NoReparsePointAncestors -Path $stage -MustExist -Name 'Distribution staging cleanup' | Out-Null; Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue } catch { }
        }
        if ($lock) { $lock.Dispose() }
    }
}

Export-ModuleMember -Function Get-LocalHelperDistributionDefaultDirectory, Install-LocalHelperDistribution
