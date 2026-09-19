[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$modulePath = Join-Path $PSScriptRoot 'GitUpdate.psm1'
Import-Module $modulePath -Force

function Invoke-TestGit {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string[]]$Arguments)
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & git.exe -C $Path @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) { throw "git $($Arguments -join ' ') failed in '$Path': $($output -join [Environment]::NewLine)" }
    return @($output)
}

function Write-TestFile {
    param([string]$Path, [string]$Contents)
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) { [System.IO.Directory]::CreateDirectory($parent) | Out-Null }
    [System.IO.File]::WriteAllText($Path, $Contents, [System.Text.Encoding]::UTF8)
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Assert-Throws {
    param([scriptblock]$Action, [string]$Contains)
    try { & $Action }
    catch {
        if ($_.Exception.Message -notmatch [regex]::Escape($Contains)) { throw "Expected error containing '$Contains', got '$($_.Exception.Message)'" }
        return
    }
    throw "Expected an error containing '$Contains'."
}

function Invoke-WithConsoleEncoding {
    param(
        [Parameter(Mandatory = $true)][System.Text.Encoding]$Encoding,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    $previous = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = $Encoding
        return @(& $Action)
    }
    finally {
        [Console]::OutputEncoding = $previous
    }
}

function Initialize-RemoteFixture {
    param([string]$Base, [string]$Branch)
    $source = Join-Path $Base 'source repository'
    $bare = Join-Path $Base 'remote.git'
    [System.IO.Directory]::CreateDirectory($source) | Out-Null
    Invoke-TestGit -Path $source -Arguments @('init') | Out-Null
    Invoke-TestGit -Path $source -Arguments @('config', 'user.email', 'self-test@example.invalid') | Out-Null
    Invoke-TestGit -Path $source -Arguments @('config', 'user.name', 'Git update self test') | Out-Null
    Invoke-TestGit -Path $source -Arguments @('checkout', '-b', $Branch) | Out-Null
    Write-TestFile -Path (Join-Path $source 'app.txt') -Contents 'version one'
    Write-TestFile -Path (Join-Path $source ('folder\' + $script:UnicodeName + ' file.txt')) -Contents 'unicode source'
    Write-TestFile -Path (Join-Path $source ('folder\' + $script:UnicodeName + ' [literal] spaced.txt')) -Contents 'literal path source'
    Write-TestFile -Path (Join-Path $source '.env.example') -Contents 'example only'
    Write-TestFile -Path (Join-Path $source 'deploy\windows\certs\README.md') -Contents 'source documentation'
    Write-TestFile -Path (Join-Path $source 'log\work-log.md') -Contents 'source work log'
    Invoke-TestGit -Path $source -Arguments @('add', '.') | Out-Null
    Invoke-TestGit -Path $source -Arguments @('commit', '-m', 'initial') | Out-Null
    Invoke-TestGit -Path $source -Arguments @('init', '--bare', $bare) | Out-Null
    Invoke-TestGit -Path $source -Arguments @('remote', 'add', 'origin', $bare) | Out-Null
    Invoke-TestGit -Path $source -Arguments @('push', '-u', 'origin', $Branch) | Out-Null
    return [pscustomobject]@{ Source = $source; Bare = $bare; Branch = $Branch }
}

$script:UnicodeName = [string][char]0xD55C + [string][char]0xAE00
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ('simdashboard git update ' + $script:UnicodeName + ' ' + [guid]::NewGuid().ToString('N'))
try {
    [System.IO.Directory]::CreateDirectory($temporary) | Out-Null
    $branch = 'codex/windows-one-click-deploy'
    $fixture = Initialize-RemoteFixture -Base $temporary -Branch $branch

    # Existing checkout: an absent upstream falls back to origin/current-branch,
    # fetches a local bare repository, and applies only a fast-forward.
    $existing = Join-Path $temporary ('existing deploy ' + $script:UnicodeName + ' [literal] spaced')
    Invoke-TestGit -Path $temporary -Arguments @('clone', '--branch', $branch, $fixture.Bare, $existing) | Out-Null
    Invoke-TestGit -Path $existing -Arguments @('branch', '--unset-upstream') | Out-Null
    Write-TestFile -Path (Join-Path $fixture.Source 'app.txt') -Contents 'version two'
    Invoke-TestGit -Path $fixture.Source -Arguments @('add', 'app.txt') | Out-Null
    Invoke-TestGit -Path $fixture.Source -Arguments @('commit', '-m', 'forward') | Out-Null
    Invoke-TestGit -Path $fixture.Source -Arguments @('push', 'origin', $branch) | Out-Null
    # Windows PowerShell 5.1 decodes native Git output using the configured
    # console code page. Git emits UTF-8 paths, so CP949 must not turn a
    # tracked Korean path into replacement characters before GetFullPath sees it.
    $encodingBefore = [Console]::OutputEncoding
    Invoke-WithConsoleEncoding -Encoding ([Text.Encoding]::GetEncoding(949)) -Action {
        Assert-Throws -Action {
            & (Get-Module GitUpdate) {
                param($Repository)
                Invoke-UpdateGit -Git (Get-UpdateGit) -Root $Repository -Arguments @('rev-parse', '--verify', 'refs/heads/nonexistent-encoding-probe')
            } $existing
        } -Contains 'Git command failed'
        Assert-True ([Console]::OutputEncoding.CodePage -eq 949) 'Git command failure did not restore CP949.'
    } | Out-Null
    $plan = Invoke-WithConsoleEncoding -Encoding ([Text.Encoding]::GetEncoding(949)) -Action {
        $result = Get-WorkbenchGitUpdatePlan -Root $existing
        Assert-True ([Console]::OutputEncoding.CodePage -eq 949) 'Git command success did not restore CP949.'
        return $result
    }
    $plan = @($plan)[-1]
    Assert-True ([Console]::OutputEncoding.CodePage -eq $encodingBefore.CodePage) 'Git update plan did not restore the process console encoding.'
    Assert-True ($plan.Mode -eq 'Existing' -and $plan.Changed -and $plan.Branch -eq $branch) 'Existing fast-forward plan is incorrect.'
    Invoke-WorkbenchGitUpdate -Plan $plan | Out-Null
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $existing 'app.txt')).Trim() -eq 'version two') 'Existing fast-forward did not update source.'

    # Updating a clean worktree must preserve an unrelated user stash. The
    # updater must not create, consume, or clear stash entries as a side effect.
    Write-TestFile -Path (Join-Path $existing 'app.txt') -Contents 'personal stashed edit'
    Invoke-TestGit -Path $existing -Arguments @('stash', 'push', '-m', 'self-test preserved stash', '--', 'app.txt') | Out-Null
    $stashBefore = @(Invoke-TestGit -Path $existing -Arguments @('stash', 'list', '--format=%H%x09%s'))
    $stashContentBefore = @(Invoke-TestGit -Path $existing -Arguments @('stash', 'show', '--format=fuller', '--stat', 'stash@{0}'))
    Write-TestFile -Path (Join-Path $fixture.Source 'app.txt') -Contents 'version three'
    Invoke-TestGit -Path $fixture.Source -Arguments @('add', 'app.txt') | Out-Null
    Invoke-TestGit -Path $fixture.Source -Arguments @('commit', '-m', 'second forward') | Out-Null
    Invoke-TestGit -Path $fixture.Source -Arguments @('push', 'origin', $branch) | Out-Null
    # Local non-Markdown log output must not block an update or be modified.
    $localLogs = @('log/local.txt', 'log/nested/trace.json', 'log/nested/session.LOG', 'log/no-extension')
    foreach ($relative in $localLogs) {
        Write-TestFile -Path (Join-Path $existing $relative) -Contents ('preserved ' + $relative)
    }
    $stashPlan = Get-WorkbenchGitUpdatePlan -Root $existing
    Invoke-WorkbenchGitUpdate -Plan $stashPlan | Out-Null
    foreach ($relative in $localLogs) {
        Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $existing $relative)).Trim() -eq ('preserved ' + $relative)) "Local log file '$relative' was changed."
    }
    foreach ($relative in @('log/local.md', 'log/nested/review.MD')) {
        Write-TestFile -Path (Join-Path $existing $relative) -Contents 'local review note'
        Assert-Throws -Action { Get-WorkbenchGitUpdatePlan -Root $existing } -Contains 'local changes'
        Remove-Item -LiteralPath (Join-Path $existing $relative)
    }
    Write-TestFile -Path (Join-Path $existing 'log/work-log.md') -Contents 'local tracked review note'
    Assert-Throws -Action { Get-WorkbenchGitUpdatePlan -Root $existing } -Contains 'local changes'
    Invoke-TestGit -Path $existing -Arguments @('checkout', '--', 'log/work-log.md') | Out-Null
    $stashAfter = @(Invoke-TestGit -Path $existing -Arguments @('stash', 'list', '--format=%H%x09%s'))
    $stashContentAfter = @(Invoke-TestGit -Path $existing -Arguments @('stash', 'show', '--format=fuller', '--stat', 'stash@{0}'))
    Assert-True (($stashBefore -join [Environment]::NewLine) -eq ($stashAfter -join [Environment]::NewLine)) 'Existing stash entries changed during update.'
    Assert-True (($stashContentBefore -join [Environment]::NewLine) -eq ($stashContentAfter -join [Environment]::NewLine)) 'Existing stash content changed during update.'
    Assert-True (($stashAfter -join [Environment]::NewLine) -match 'self-test preserved stash') 'Existing stash entry was not retained during update.'
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $existing 'app.txt')).Trim() -eq 'version three') 'Update with an existing stash did not apply the remote commit.'

    Write-TestFile -Path (Join-Path $existing 'app.txt') -Contents 'local edit'
    Assert-Throws -Action { Get-WorkbenchGitUpdatePlan -Root $existing } -Contains 'local changes'
    Invoke-TestGit -Path $existing -Arguments @('checkout', '--', 'app.txt') | Out-Null
    Invoke-TestGit -Path $existing -Arguments @('config', 'user.email', 'self-test@example.invalid') | Out-Null
    Invoke-TestGit -Path $existing -Arguments @('config', 'user.name', 'Git update self test') | Out-Null
    Write-TestFile -Path (Join-Path $existing 'local-only-source.txt') -Contents 'diverged'
    Invoke-TestGit -Path $existing -Arguments @('add', 'local-only-source.txt') | Out-Null
    Invoke-TestGit -Path $existing -Arguments @('commit', '-m', 'local divergent commit') | Out-Null
    Write-TestFile -Path (Join-Path $fixture.Source 'remote-only-source.txt') -Contents 'remote divergent'
    Invoke-TestGit -Path $fixture.Source -Arguments @('add', 'remote-only-source.txt') | Out-Null
    Invoke-TestGit -Path $fixture.Source -Arguments @('commit', '-m', 'remote divergent commit') | Out-Null
    Invoke-TestGit -Path $fixture.Source -Arguments @('push', 'origin', $branch) | Out-Null
    Assert-Throws -Action { Get-WorkbenchGitUpdatePlan -Root $existing } -Contains 'cannot fast-forward'

    # No-.git bootstrap uses the supplied local bare remote, backs up only a colliding
    # source file, and leaves personal settings, databases, environments, and orphan
    # files exactly where the user put them.
    $bootstrap = Join-Path $temporary ('ZIP deploy with spaces ' + $script:UnicodeName)
    [System.IO.Directory]::CreateDirectory($bootstrap) | Out-Null
    Write-TestFile -Path (Join-Path $bootstrap 'app.txt') -Contents 'old zip source'
    Write-TestFile -Path (Join-Path $bootstrap ('folder\' + $script:UnicodeName + ' file.txt')) -Contents 'old Unicode source'
    Write-TestFile -Path (Join-Path $bootstrap ('folder\' + $script:UnicodeName + ' [literal] spaced.txt')) -Contents 'old literal path source'
    Write-TestFile -Path (Join-Path $bootstrap '.env') -Contents 'SECRET=must-not-move'
    Write-TestFile -Path (Join-Path $bootstrap '.postgres-owner.env') -Contents 'POSTGRES_PASSWORD=must-not-move'
    Write-TestFile -Path (Join-Path $bootstrap '.venv-runtime\keep.txt') -Contents 'environment'
    Write-TestFile -Path (Join-Path $bootstrap 'backend\data\local.db') -Contents 'database'
    Write-TestFile -Path (Join-Path $bootstrap 'output\result.txt') -Contents 'result'
    Write-TestFile -Path (Join-Path $bootstrap 'backups\updater-driver-test\driver.txt') -Contents 'temporary update driver'
    Write-TestFile -Path (Join-Path $bootstrap 'orphan personal file.txt') -Contents 'orphan'
    $bootstrapPlan = Get-WorkbenchGitUpdatePlan -Root $bootstrap -RepositoryUrl $fixture.Bare -Branch $branch
    Assert-True ($bootstrapPlan.Mode -eq 'Bootstrap' -and $bootstrapPlan.BackupDirectory) 'No-.git bootstrap plan is incorrect.'
    Invoke-WorkbenchGitUpdate -Plan $bootstrapPlan | Out-Null
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $bootstrap 'app.txt')).Trim() -eq 'version three') 'Bootstrap did not check out remote source.'
    foreach ($relative in @('.env', '.postgres-owner.env', '.venv-runtime\keep.txt', 'backend\data\local.db', 'output\result.txt', 'backups\updater-driver-test\driver.txt', 'orphan personal file.txt')) {
        Assert-True (Test-Path -LiteralPath (Join-Path $bootstrap $relative)) "Bootstrap moved protected or orphan file '$relative'."
    }
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $bootstrapPlan.BackupDirectory 'app.txt')).Trim() -eq 'old zip source') 'Colliding source was not retained in the backup.'
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $bootstrapPlan.BackupDirectory ('folder\' + $script:UnicodeName + ' file.txt'))).Trim() -eq 'old Unicode source') 'Unicode colliding source was not retained in the backup.'
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $bootstrapPlan.BackupDirectory ('folder\' + $script:UnicodeName + ' [literal] spaced.txt'))).Trim() -eq 'old literal path source') 'Literal bracket/space colliding source was not retained in the backup.'

    # An empty, unborn .git repository is also bootstrapable, unless its index has
    # staged user files. This confirms it never silently overwrites that index.
    $unborn = Join-Path $temporary 'unborn repository'
    [System.IO.Directory]::CreateDirectory($unborn) | Out-Null
    Invoke-TestGit -Path $unborn -Arguments @('init') | Out-Null
    $unbornPlan = Get-WorkbenchGitUpdatePlan -Root $unborn -RepositoryUrl $fixture.Bare -Branch $branch
    Assert-True ($unbornPlan.Mode -eq 'Bootstrap') 'Unborn repository was not planned as bootstrap.'
    Invoke-WorkbenchGitUpdate -Plan $unbornPlan | Out-Null
    Assert-True (Test-Path -LiteralPath (Join-Path $unborn ('folder\' + $script:UnicodeName + ' file.txt'))) 'Unborn bootstrap missed Unicode source path.'
    Assert-True (Test-Path -LiteralPath (Join-Path $unborn ('folder\' + $script:UnicodeName + ' [literal] spaced.txt'))) 'Unborn bootstrap missed literal bracket/space source path.'

    $protectedSource = Join-Path $temporary 'protected source'
    Invoke-TestGit -Path $temporary -Arguments @('clone', $fixture.Bare, $protectedSource) | Out-Null
    Invoke-TestGit -Path $protectedSource -Arguments @('checkout', $branch) | Out-Null
    Invoke-TestGit -Path $protectedSource -Arguments @('config', 'user.email', 'self-test@example.invalid') | Out-Null
    Invoke-TestGit -Path $protectedSource -Arguments @('config', 'user.name', 'Git update self test') | Out-Null
    Write-TestFile -Path (Join-Path $protectedSource '.env') -Contents 'bad remote secret'
    Invoke-TestGit -Path $protectedSource -Arguments @('add', '.env') | Out-Null
    Invoke-TestGit -Path $protectedSource -Arguments @('commit', '-m', 'protected path') | Out-Null
    Invoke-TestGit -Path $protectedSource -Arguments @('push', 'origin', 'HEAD:refs/heads/protected') | Out-Null
    $protectedRoot = Join-Path $temporary 'protected destination'
    [System.IO.Directory]::CreateDirectory($protectedRoot) | Out-Null
    Write-TestFile -Path (Join-Path $protectedRoot '.env') -Contents 'local secret'
    Assert-Throws -Action { Get-WorkbenchGitUpdatePlan -Root $protectedRoot -RepositoryUrl $fixture.Bare -Branch 'protected' } -Contains 'protected deployment path'
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $protectedRoot '.env')).Trim() -eq 'local secret') 'Protected path check touched local dotenv file.'

    Write-Host 'Windows Git update module self-test passed.' -ForegroundColor Green
}
finally {
    $temporaryBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/')
    $temporaryResolved = [System.IO.Path]::GetFullPath($temporary).TrimEnd('\', '/')
    $temporaryParent = Split-Path -Parent $temporaryResolved
    $temporaryLeaf = Split-Path -Leaf $temporaryResolved
    if ((Test-Path -LiteralPath $temporaryResolved) -and
        [System.String]::Equals($temporaryParent, $temporaryBase, [System.StringComparison]::OrdinalIgnoreCase) -and
        $temporaryLeaf.StartsWith('simdashboard git update ', [System.StringComparison]::Ordinal)) {
        Remove-Item -LiteralPath $temporaryResolved -Recurse -Force -ErrorAction SilentlyContinue
    }
}
exit 0
