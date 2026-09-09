Set-StrictMode -Version Latest

# This module deliberately uses the git executable rather than a managed Git API.  It
# keeps credentials in the user's existing Git credential manager and, more
# importantly, lets Git retain its usual Windows path handling.
$script:DefaultRepositoryUrl = 'https://github.com/wgcha/simdashboard.git'
$script:DefaultBranch = 'codex/windows-one-click-deploy'

function Get-UpdateGit {
    $command = Get-Command git.exe -ErrorAction SilentlyContinue
    if (-not $command) { $command = Get-Command git -ErrorAction SilentlyContinue }
    if (-not $command) { throw 'Git was not found. Install Git for Windows and retry the update.' }
    return $command.Source
}

function Invoke-UpdateGit {
    param(
        [Parameter(Mandatory = $true)][string]$Git,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )

    # Git uses non-zero exits for ordinary probes (for example an unborn HEAD).
    # Keep a caller's ErrorActionPreference=Stop from turning those probes into
    # NativeCommandError before this function can inspect $LASTEXITCODE.
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $result = & $Git -C $Root @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $text = (@($result | ForEach-Object { [string]$_ }) -join [Environment]::NewLine).Trim()
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw ("Git command failed (exit code {0}): git {1}{2}" -f $exitCode, ($Arguments -join ' '), $(if ($text) { [Environment]::NewLine + $text } else { '' }))
    }
    return [pscustomobject]@{ ExitCode = $exitCode; Output = $text }
}

function Get-AbsoluteRoot {
    param([Parameter(Mandatory = $true)][string]$Root)
    if (-not [System.IO.Path]::IsPathRooted($Root)) { throw 'Root must be an absolute path.' }
    $resolved = [System.IO.Path]::GetFullPath($Root)
    if ([System.String]::Equals($resolved, [System.IO.Path]::GetPathRoot($resolved), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Root cannot be a filesystem root.' }
    $full = $resolved.TrimEnd('\', '/')
    if (-not (Test-Path -LiteralPath $full -PathType Container)) { throw "Project root does not exist: $full" }
    return $full
}

function Test-ReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    return (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Assert-SafeRoot {
    param([Parameter(Mandatory = $true)][string]$Root)
    $cursor = $Root
    while ($true) {
        if (Test-ReparsePoint -Path $cursor) { throw "Refusing a project root under a symbolic link or junction: $cursor" }
        $parent = Split-Path -Parent $cursor
        if (-not $parent -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function ConvertTo-RelativeProjectPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )
    if ([string]::IsNullOrWhiteSpace($RelativePath) -or [System.IO.Path]::IsPathRooted($RelativePath)) { throw 'Git returned an unsafe tracked path.' }
    $normalized = $RelativePath -replace '/', [string][System.IO.Path]::DirectorySeparatorChar
    if ($normalized -match '(^|\\)\.\.?(\\|$)' -or $normalized.StartsWith('\\')) { throw "Git returned an unsafe tracked path: $RelativePath" }
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $Root $normalized))
    $prefix = $Root.TrimEnd('\') + [string][System.IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Git returned a path outside the project root: $RelativePath" }
    return $candidate
}

function Assert-SafeExistingPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $rootPrefix = $Root.TrimEnd('\') + [string][System.IO.Path]::DirectorySeparatorChar
    $cursor = $Path
    while ($cursor.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or $cursor -eq $Root) {
        if (Test-Path -LiteralPath $cursor) {
            if (Test-ReparsePoint -Path $cursor) { throw "Refusing to follow a symbolic link or junction: $cursor" }
        }
        if ($cursor -eq $Root) { break }
        $cursor = Split-Path -Parent $cursor
    }
}

function Get-GitDirectoryKind {
    param([Parameter(Mandatory = $true)][string]$Root)
    $dotGit = Join-Path $Root '.git'
    if (-not (Test-Path -LiteralPath $dotGit)) { return 'Missing' }
    if (Test-ReparsePoint -Path $dotGit) { throw 'A .git symbolic link or junction is not supported by the one-click updater.' }
    if (-not (Test-Path -LiteralPath $dotGit -PathType Container)) { throw 'A .git file (linked worktree) is not supported by the one-click updater.' }
    return 'Directory'
}

function Get-CurrentHead {
    param([string]$Git, [string]$Root)
    $head = Invoke-UpdateGit -Git $Git -Root $Root -Arguments @('rev-parse', '--verify', 'HEAD') -AllowFailure
    if ($head.ExitCode -eq 0) { return $head.Output.Trim() }
    return $null
}

function Assert-CleanWorktree {
    param([string]$Git, [string]$Root)
    $status = Invoke-UpdateGit -Git $Git -Root $Root -Arguments @('status', '--porcelain=v1', '--untracked-files=all')
    if ($status.Output) { throw 'The project has local changes or untracked source files. Commit, move, or remove them before updating.' }
}

function Get-BranchName {
    param([string]$Git, [string]$Root)
    $branch = Invoke-UpdateGit -Git $Git -Root $Root -Arguments @('symbolic-ref', '--quiet', '--short', 'HEAD') -AllowFailure
    if ($branch.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($branch.Output)) { throw 'The project is in detached HEAD state. Check out its deployment branch before updating.' }
    return $branch.Output.Trim()
}

function Get-ExistingRemoteTarget {
    param([string]$Git, [string]$Root, [string]$Branch)
    $upstream = Invoke-UpdateGit -Git $Git -Root $Root -Arguments @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}') -AllowFailure
    if ($upstream.ExitCode -eq 0 -and $upstream.Output) {
        $remote = Invoke-UpdateGit -Git $Git -Root $Root -Arguments @('config', '--get', ("branch.{0}.remote" -f $Branch)) -AllowFailure
        $merge = Invoke-UpdateGit -Git $Git -Root $Root -Arguments @('config', '--get', ("branch.{0}.merge" -f $Branch)) -AllowFailure
        if ($remote.ExitCode -eq 0 -and $merge.ExitCode -eq 0 -and $remote.Output -and $merge.Output) {
            return [pscustomobject]@{ Remote = $remote.Output.Trim(); SourceRef = $merge.Output.Trim(); TargetRef = $upstream.Output.Trim() }
        }
        throw 'The current branch upstream is incomplete. Configure its remote and merge branch before updating.'
    }

    $origin = Invoke-UpdateGit -Git $Git -Root $Root -Arguments @('remote', 'get-url', 'origin') -AllowFailure
    if ($origin.ExitCode -ne 0 -or -not $origin.Output) { throw "The current branch has no upstream and origin is unavailable for branch '$Branch'." }
    $sourceRef = 'refs/heads/' + $Branch
    return [pscustomobject]@{ Remote = 'origin'; SourceRef = $sourceRef; TargetRef = ('origin/' + $Branch) }
}

function Fetch-TargetCommit {
    param([string]$Git, [string]$Root, [string]$Remote, [string]$SourceRef)
    # FETCH_HEAD is intentionally used as the immutable plan target. Fetch changes Git
    # metadata only; it never updates the working tree.
    $fetchArguments = @('fetch', '--no-tags', $Remote, $SourceRef)
    # Keep a normal remote-tracking ref as well as FETCH_HEAD.  The former is
    # required when bootstrap establishes the branch upstream after checkout.
    if ($SourceRef -match '^refs/heads/(.+)$') {
        $fetchArguments = @('fetch', '--no-tags', $Remote, ($SourceRef + ':refs/remotes/' + $Remote + '/' + $Matches[1]))
    }
    Invoke-UpdateGit -Git $Git -Root $Root -Arguments $fetchArguments | Out-Null
    $target = Invoke-UpdateGit -Git $Git -Root $Root -Arguments @('rev-parse', '--verify', 'FETCH_HEAD')
    return $target.Output.Trim()
}

function Test-AllowedTemplatePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $name = [System.IO.Path]::GetFileName($Path)
    return $name -ieq '.gitkeep' -or $name -match '\.(example|sample|template)$'
}

function Test-ProtectedRemotePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $lower = $Path.Replace('\\', '/').TrimStart('/').ToLowerInvariant()
    if (Test-AllowedTemplatePath -Path $lower) { return $false }
    if ($lower -in @('deploy/windows/certs/readme.md', 'log/work-log.md')) { return $false }
    if ($lower -match '(^|/)\.git($|/)' -or $lower -match '(^|/)\.tools($|/)' -or $lower -match '(^|/)\.venv[^/]*($|/)' -or $lower -match '(^|/)node_modules($|/)') { return $true }
    if ($lower -match '(^|/)\.env[^/]*$' -or $lower -match '(^|/)\.postgres-owner\.env[^/]*$') { return $true }
    if ($lower -match '^backend/data($|/)' -or $lower -match '^frontend/dist($|/)' -or $lower -match '^(backups|output|dist|transfer-bundles)($|/)' -or $lower -match '(^|/)\.local-runner($|/)') { return $true }
    if ($lower -match '(^|/)(network-settings\.local\.json|\.setup-proxy\.env[^/]*|\.server-pids\.(json|env)|\.windows-deploy-ready\.json[^/]*|\.setup-recovery-required\.json)$') { return $true }
    if ($lower -match '(^|/)(cert|certs|certificate|certificates|runtime|run|logs?)($|/)' -or $lower -match '\.(pem|pfx|p12|key)$') { return $true }
    return $false
}

function Get-BootstrapTrackedPaths {
    param([string]$Git, [string]$Root, [string]$Commit)
    # NUL-delimited tree records avoid Git's human-oriented quotePath escaping for
    # spaces and non-ASCII names. A symlink can redirect checkout outside Root, so
    # it is rejected before any local collision is moved.
    $tree = Invoke-UpdateGit -Git $Git -Root $Root -Arguments @('ls-tree', '-r', '-z', $Commit)
    $paths = New-Object System.Collections.Generic.List[string]
    foreach ($entry in @($tree.Output -split [char]0 | Where-Object { $_ })) {
        $tab = $entry.IndexOf([char]9)
        if ($tab -lt 0) { throw 'Git returned an invalid tree record during bootstrap.' }
        $header = $entry.Substring(0, $tab).Split(' ')
        if ($entry.Substring(0, $tab) -notmatch '^[0-7]{6} (blob|commit) [0-9a-fA-F]+$') { throw 'Git returned an invalid tree record during bootstrap.' }
        $path = $entry.Substring($tab + 1)
        if ($header[0] -eq '120000') { throw "The update source tracks symbolic link '$path'. Refusing bootstrap." }
        if (Test-ProtectedRemotePath -Path $path) { throw "The update source tracks protected deployment path '$path'. Refusing bootstrap before touching local files." }
        ConvertTo-RelativeProjectPath -Root $Root -RelativePath $path | Out-Null
        $paths.Add($path)
    }
    return @($paths.ToArray())
}

function Get-BootstrapCollisions {
    param([string]$Root, [string[]]$TrackedPaths)
    $collisions = New-Object System.Collections.Generic.List[string]
    foreach ($relative in $TrackedPaths) {
        $target = ConvertTo-RelativeProjectPath -Root $Root -RelativePath $relative
        Assert-SafeExistingPath -Root $Root -Path $target
        if (Test-Path -LiteralPath $target) {
            if (Test-Path -LiteralPath $target -PathType Container) { throw "A local directory blocks source file '$relative'. Move it manually before bootstrap so its contents remain protected." }
            $collisions.Add($target)
        }
        $parent = Split-Path -Parent $target
        while ($parent -and $parent -ne $Root) {
            Assert-SafeExistingPath -Root $Root -Path $parent
            if (Test-Path -LiteralPath $parent -PathType Leaf) {
                $collisions.Add($parent)
                break
            }
            $parent = Split-Path -Parent $parent
        }
    }
    return @($collisions | Sort-Object -Unique)
}

function Get-BackupDirectory {
    param([string]$Root)
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    return (Join-Path $Root ("backups\git-update-{0}-{1}" -f $stamp, [guid]::NewGuid().ToString('N').Substring(0, 8)))
}

function Add-UpdaterExclude {
    param([string]$Root)
    $exclude = Join-Path $Root '.git\info\exclude'
    $lines = @('.gitupdate.lock', '.update.lock', '/update.bat', 'backups/git-update-*', 'backups/updater-driver-*', '/log/update-*.log')
    $existing = if (Test-Path -LiteralPath $exclude) { @(Get-Content -LiteralPath $exclude) } else { @() }
    $add = @($lines | Where-Object { $existing -notcontains $_ })
    foreach ($line in @($add)) { Add-Content -LiteralPath $exclude -Value $line }
}

function Enter-UpdateLock {
    param([string]$Root)
    $lockPath = Join-Path $Root '.gitupdate.lock'
    try {
        $stream = New-Object System.IO.FileStream($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $stream.SetLength(0)
        $bytes = [System.Text.Encoding]::UTF8.GetBytes(("pid={0}`nstarted={1:o}`n" -f $PID, (Get-Date)))
        $stream.Write($bytes, 0, $bytes.Length)
        return [pscustomobject]@{ Path = $lockPath; Stream = $stream }
    }
    catch { throw "Another update appears to be running for this folder ($lockPath)." }
}

function Exit-UpdateLock {
    param($Lock)
    if ($Lock) {
        if ($Lock.Stream) { $Lock.Stream.Dispose() }
        Remove-Item -LiteralPath $Lock.Path -Force -ErrorAction SilentlyContinue
    }
}

function Move-BootstrapCollisions {
    param([string]$Root, [string[]]$Collisions, [string]$BackupDirectory)
    $hasCollision = $false
    foreach ($candidate in $Collisions) {
        $hasCollision = $true
        break
    }
    if (-not $hasCollision) { return }
    $backupParent = Split-Path -Parent $BackupDirectory
    Assert-SafeExistingPath -Root $Root -Path $backupParent
    [System.IO.Directory]::CreateDirectory($BackupDirectory) | Out-Null
    $manifestPath = Join-Path $BackupDirectory 'MOVED-FILES.txt'
    [System.IO.File]::WriteAllText($manifestPath, ("# Git update collision backup; do not delete until the update is verified." + [Environment]::NewLine), [System.Text.Encoding]::UTF8)
    foreach ($source in $Collisions) {
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { continue }
        Assert-SafeExistingPath -Root $Root -Path $source
        $relative = $source.Substring($Root.TrimEnd('\').Length).TrimStart('\')
        $destination = Join-Path $BackupDirectory $relative
        $destinationParent = Split-Path -Parent $destination
        [System.IO.Directory]::CreateDirectory($destinationParent) | Out-Null
        [System.IO.File]::AppendAllText($manifestPath, ("PENDING{0}{1}{2}" -f [char]9, $relative, [Environment]::NewLine), [System.Text.Encoding]::UTF8)
        Move-Item -LiteralPath $source -Destination $destination -ErrorAction Stop
        [System.IO.File]::AppendAllText($manifestPath, ("MOVED{0}{1}{2}" -f [char]9, $relative, [Environment]::NewLine), [System.Text.Encoding]::UTF8)
    }
}

function New-UpdatePlan {
    param($Root, $Mode, $Branch, $Remote, $TargetRef, $TargetCommit, $OriginalCommit, $BackupDirectory, $Changed, $Git, $SourceRef, $TrackedPaths)
    return [pscustomobject]@{
        Root = $Root
        Mode = $Mode
        Branch = $Branch
        Remote = $Remote
        TargetRef = $TargetRef
        TargetCommit = $TargetCommit
        OriginalCommit = $OriginalCommit
        BackupDirectory = $BackupDirectory
        Changed = [bool]$Changed
        # Internal values are intentionally retained on the plan so Invoke can repeat
        # safety checks without resolving a different remote or branch.
        _Git = $Git
        _SourceRef = $SourceRef
        _TrackedPaths = @($TrackedPaths)
    }
}

function Get-WorkbenchGitUpdatePlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [string]$RepositoryUrl = $script:DefaultRepositoryUrl,
        [string]$Branch = $script:DefaultBranch
    )
    $rootPath = Get-AbsoluteRoot -Root $Root
    Assert-SafeRoot -Root $rootPath
    $git = Get-UpdateGit
    $kind = Get-GitDirectoryKind -Root $rootPath
    $bareProbe = Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('rev-parse', '--is-bare-repository') -AllowFailure
    if ($bareProbe.ExitCode -eq 0 -and $bareProbe.Output.Trim() -eq 'true') { throw 'Bare repositories are not supported by the one-click updater.' }
    $topProbe = Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('rev-parse', '--show-toplevel') -AllowFailure
    if ($topProbe.ExitCode -eq 0 -and $topProbe.Output) {
        $discoveredTop = [System.IO.Path]::GetFullPath($topProbe.Output.Trim()).TrimEnd('\', '/')
        if (-not [System.String]::Equals($discoveredTop, $rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'Root is inside another Git worktree. Use the exact deployment root, not a parent repository subdirectory.'
        }
    }

    if ($kind -eq 'Directory') {
        $inside = Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('rev-parse', '--is-inside-work-tree') -AllowFailure
        $top = Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('rev-parse', '--show-toplevel') -AllowFailure
        $topPath = if ($top.ExitCode -eq 0 -and $top.Output) { [System.IO.Path]::GetFullPath($top.Output.Trim()).TrimEnd('\', '/') } else { $null }
        if ($inside.ExitCode -ne 0 -or $inside.Output.Trim() -ne 'true' -or -not $topPath -or -not ([System.String]::Equals($topPath, $rootPath, [System.StringComparison]::OrdinalIgnoreCase))) {
            throw 'Root must be the exact top-level directory of a non-bare Git worktree. Parent repositories and linked worktrees are not supported.'
        }
        $bare = Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('rev-parse', '--is-bare-repository')
        if ($bare.Output.Trim() -eq 'true') { throw 'Bare repositories are not supported by the one-click updater.' }
        Add-UpdaterExclude -Root $rootPath
    }

    $original = Get-CurrentHead -Git $git -Root $rootPath
    if ($original) {
        Assert-CleanWorktree -Git $git -Root $rootPath
        $currentBranch = Get-BranchName -Git $git -Root $rootPath
        $target = Get-ExistingRemoteTarget -Git $git -Root $rootPath -Branch $currentBranch
        $commit = Fetch-TargetCommit -Git $git -Root $rootPath -Remote $target.Remote -SourceRef $target.SourceRef
        Get-BootstrapTrackedPaths -Git $git -Root $rootPath -Commit $commit | Out-Null
        if ($commit -ne $original) {
            $fastForward = Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('merge-base', '--is-ancestor', $original, $commit) -AllowFailure
            if ($fastForward.ExitCode -ne 0) { throw 'The remote branch cannot fast-forward this deployment. Resolve the branch divergence before updating.' }
        }
        return New-UpdatePlan -Root $rootPath -Mode 'Existing' -Branch $currentBranch -Remote $target.Remote -TargetRef $target.TargetRef -TargetCommit $commit -OriginalCommit $original -BackupDirectory $null -Changed ($commit -ne $original) -Git $git -SourceRef $target.SourceRef -TrackedPaths @()
    }

    # A newly initialized repository with staged files is ambiguous: the index may
    # represent user work that checkout would replace, so require explicit recovery.
    if ($kind -eq 'Directory') {
        $staged = Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('diff', '--cached', '--quiet') -AllowFailure
        if ($staged.ExitCode -ne 0) { throw 'The unborn Git repository has staged files. Preserve or commit them before bootstrap.' }
    }
    else {
        Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('init') | Out-Null
    }
    $origin = Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('remote', 'get-url', 'origin') -AllowFailure
    if ($origin.ExitCode -eq 0) { Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('remote', 'set-url', 'origin', $RepositoryUrl) | Out-Null }
    else { Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('remote', 'add', 'origin', $RepositoryUrl) | Out-Null }
    Add-UpdaterExclude -Root $rootPath
    $sourceRef = 'refs/heads/' + $Branch
    $commit = Fetch-TargetCommit -Git $git -Root $rootPath -Remote 'origin' -SourceRef $sourceRef
    $paths = Get-BootstrapTrackedPaths -Git $git -Root $rootPath -Commit $commit
    $collisions = Get-BootstrapCollisions -Root $rootPath -TrackedPaths $paths
    return New-UpdatePlan -Root $rootPath -Mode 'Bootstrap' -Branch $Branch -Remote 'origin' -TargetRef ('origin/' + $Branch) -TargetCommit $commit -OriginalCommit $null -BackupDirectory (Get-BackupDirectory -Root $rootPath) -Changed $true -Git $git -SourceRef $sourceRef -TrackedPaths $paths | Add-Member -PassThru -NotePropertyName _Collisions -NotePropertyValue @($collisions)
}

function Invoke-WorkbenchGitUpdate {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Plan)
    foreach ($required in @('Root', 'Mode', 'Branch', 'Remote', 'TargetCommit', 'OriginalCommit', 'Changed')) {
        if ($null -eq $Plan.PSObject.Properties[$required]) { throw "The update plan is missing '$required'. Generate a new plan and retry." }
    }
    $rootPath = Get-AbsoluteRoot -Root ([string]$Plan.Root)
    Assert-SafeRoot -Root $rootPath
    $lock = $null
    try {
        $lock = Enter-UpdateLock -Root $rootPath
        $git = Get-UpdateGit
        if ($Plan.Mode -eq 'Existing') {
            $head = Get-CurrentHead -Git $git -Root $rootPath
            if ($head -ne [string]$Plan.OriginalCommit) { throw 'The deployment commit changed after the update plan was created. Generate a new plan.' }
            Assert-CleanWorktree -Git $git -Root $rootPath
            $branch = Get-BranchName -Git $git -Root $rootPath
            if ($branch -ne [string]$Plan.Branch) { throw 'The checked-out branch changed after the update plan was created. Generate a new plan.' }
            $targetCheck = Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('cat-file', '-e', (([string]$Plan.TargetCommit) + '^{commit}')) -AllowFailure
            if ($targetCheck.ExitCode -ne 0) { throw 'The planned remote commit is no longer available locally. Generate a new plan.' }
            Get-BootstrapTrackedPaths -Git $git -Root $rootPath -Commit ([string]$Plan.TargetCommit) | Out-Null
            if ([bool]$Plan.Changed) {
                Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('merge', '--ff-only', '--no-edit', [string]$Plan.TargetCommit) | Out-Null
            }
            return $Plan
        }
        if ($Plan.Mode -ne 'Bootstrap') { throw "Unsupported update plan mode '$($Plan.Mode)'." }
        if (Get-CurrentHead -Git $git -Root $rootPath) { throw 'The bootstrap repository gained a commit after planning. Generate a new plan.' }
        $staged = Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('diff', '--cached', '--quiet') -AllowFailure
        if ($staged.ExitCode -ne 0) { throw 'The bootstrap repository has staged files. Generate a new plan after preserving them.' }
        $targetCheck = Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('cat-file', '-e', (([string]$Plan.TargetCommit) + '^{commit}')) -AllowFailure
        if ($targetCheck.ExitCode -ne 0) { throw 'The planned remote commit is no longer available locally. Generate a new plan.' }
        $paths = Get-BootstrapTrackedPaths -Git $git -Root $rootPath -Commit ([string]$Plan.TargetCommit)
        $collisions = Get-BootstrapCollisions -Root $rootPath -TrackedPaths $paths
        Move-BootstrapCollisions -Root $rootPath -Collisions $collisions -BackupDirectory ([string]$Plan.BackupDirectory)
        try {
            Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('checkout', '-B', [string]$Plan.Branch, [string]$Plan.TargetCommit) | Out-Null
            Invoke-UpdateGit -Git $git -Root $rootPath -Arguments @('branch', '--set-upstream-to', (([string]$Plan.Remote) + '/' + ([string]$Plan.Branch)), [string]$Plan.Branch) | Out-Null
        }
        catch {
            throw ("Bootstrap source checkout failed. Local colliding files are safely retained in '{0}'. {1}" -f $Plan.BackupDirectory, $_.Exception.Message)
        }
        return $Plan
    }
    finally { Exit-UpdateLock -Lock $lock }
}

Export-ModuleMember -Function Get-WorkbenchGitUpdatePlan, Invoke-WorkbenchGitUpdate
