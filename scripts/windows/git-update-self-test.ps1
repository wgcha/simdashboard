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
    $existing = Join-Path $temporary ('existing deploy ' + $script:UnicodeName)
    Invoke-TestGit -Path $temporary -Arguments @('clone', '--branch', $branch, $fixture.Bare, $existing) | Out-Null
    Invoke-TestGit -Path $existing -Arguments @('branch', '--unset-upstream') | Out-Null
    Write-TestFile -Path (Join-Path $fixture.Source 'app.txt') -Contents 'version two'
    Invoke-TestGit -Path $fixture.Source -Arguments @('add', 'app.txt') | Out-Null
    Invoke-TestGit -Path $fixture.Source -Arguments @('commit', '-m', 'forward') | Out-Null
    Invoke-TestGit -Path $fixture.Source -Arguments @('push', 'origin', $branch) | Out-Null
    $plan = Get-WorkbenchGitUpdatePlan -Root $existing
    Assert-True ($plan.Mode -eq 'Existing' -and $plan.Changed -and $plan.Branch -eq $branch) 'Existing fast-forward plan is incorrect.'
    Invoke-WorkbenchGitUpdate -Plan $plan | Out-Null
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $existing 'app.txt')).Trim() -eq 'version two') 'Existing fast-forward did not update source.'

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
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $bootstrap 'app.txt')).Trim() -eq 'version two') 'Bootstrap did not check out remote source.'
    foreach ($relative in @('.env', '.postgres-owner.env', '.venv-runtime\keep.txt', 'backend\data\local.db', 'output\result.txt', 'backups\updater-driver-test\driver.txt', 'orphan personal file.txt')) {
        Assert-True (Test-Path -LiteralPath (Join-Path $bootstrap $relative)) "Bootstrap moved protected or orphan file '$relative'."
    }
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $bootstrapPlan.BackupDirectory 'app.txt')).Trim() -eq 'old zip source') 'Colliding source was not retained in the backup.'
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $bootstrapPlan.BackupDirectory ('folder\' + $script:UnicodeName + ' file.txt'))).Trim() -eq 'old Unicode source') 'Unicode colliding source was not retained in the backup.'

    # An empty, unborn .git repository is also bootstrapable, unless its index has
    # staged user files. This confirms it never silently overwrites that index.
    $unborn = Join-Path $temporary 'unborn repository'
    [System.IO.Directory]::CreateDirectory($unborn) | Out-Null
    Invoke-TestGit -Path $unborn -Arguments @('init') | Out-Null
    $unbornPlan = Get-WorkbenchGitUpdatePlan -Root $unborn -RepositoryUrl $fixture.Bare -Branch $branch
    Assert-True ($unbornPlan.Mode -eq 'Bootstrap') 'Unborn repository was not planned as bootstrap.'
    Invoke-WorkbenchGitUpdate -Plan $unbornPlan | Out-Null
    Assert-True (Test-Path -LiteralPath (Join-Path $unborn ('folder\' + $script:UnicodeName + ' file.txt'))) 'Unborn bootstrap missed Unicode source path.'

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
