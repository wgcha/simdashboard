Set-StrictMode -Version Latest

$script:ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:ExpectedPythonVersion = (Get-Content -Raw -LiteralPath (Join-Path $script:ProjectRoot '.python-version')).Trim()
$script:ExpectedNodeVersion = (Get-Content -Raw -LiteralPath (Join-Path $script:ProjectRoot '.node-version')).Trim()
$frontendPackage = Get-Content -Raw -LiteralPath (Join-Path $script:ProjectRoot 'frontend\package.json') | ConvertFrom-Json
$script:ExpectedPnpmVersion = ([string]$frontendPackage.packageManager -replace '^pnpm@', '').Trim()

function Get-ProjectRoot {
    return $script:ProjectRoot
}

function Get-ExpectedRuntimeVersions {
    return [pscustomobject]@{
        Python = $script:ExpectedPythonVersion
        Node = $script:ExpectedNodeVersion
        Pnpm = $script:ExpectedPnpmVersion
    }
}

function Test-ExactVersion {
    param(
        [Parameter(Mandatory = $true)][string]$Actual,
        [Parameter(Mandatory = $true)][string]$Expected
    )
    return $Actual.Trim().TrimStart('v') -eq $Expected.Trim().TrimStart('v')
}

function Set-ProjectNodePath {
    $nodeDirectory = Join-Path $script:ProjectRoot (".tools\node-{0}-win-x64" -f $script:ExpectedNodeVersion)
    $node = Join-Path $nodeDirectory 'node.exe'
    if (-not (Test-Path -LiteralPath $node -PathType Leaf)) {
        $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
        if (-not $nodeCommand) { $nodeCommand = Get-Command node -ErrorAction SilentlyContinue }
        if (-not $nodeCommand) {
            throw "Node.js $script:ExpectedNodeVersion was not found. Install the project runtime under .tools or run scripts\\windows\\bootstrap-runtime.ps1."
        }
        $node = $nodeCommand.Source
        $nodeDirectory = Split-Path -Parent $node
    }

    $version = (& $node --version).Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-ExactVersion -Actual $version -Expected $script:ExpectedNodeVersion)) {
        throw "Node.js $script:ExpectedNodeVersion is required; found $version."
    }

    $entries = @($env:Path -split ';' | Where-Object { $_ })
    $withoutNode = @($entries | Where-Object { $_.TrimEnd('\\') -ine $nodeDirectory.TrimEnd('\\') })
    [Environment]::SetEnvironmentVariable('PATH', $null, 'Process')
    [Environment]::SetEnvironmentVariable('Path', ((@($nodeDirectory) + @($withoutNode)) -join ';'), 'Process')
    return $node
}

function Get-ProjectPythonBootstrap {
    $candidates = @(
        (Join-Path $script:ProjectRoot (".tools\python\cpython-{0}-windows-x86_64-none\python.exe" -f $script:ExpectedPythonVersion)),
        (Join-Path $script:ProjectRoot '.tools\python\python.exe')
    )
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $version = (& $candidate -c 'import sys;print(chr(46).join(map(str,sys.version_info[:3])))').Trim()
        if ($LASTEXITCODE -eq 0 -and (Test-ExactVersion -Actual $version -Expected $script:ExpectedPythonVersion)) {
            return $candidate
        }
    }

    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $version = (& $py.Source '-3.12' -c 'import sys;print(chr(46).join(map(str,sys.version_info[:3])))' 2>$null).Trim()
        if ($LASTEXITCODE -eq 0 -and (Test-ExactVersion -Actual $version -Expected $script:ExpectedPythonVersion)) {
            return [pscustomobject]@{ FilePath = $py.Source; Arguments = @('-3.12') }
        }
    }
    throw "Python $script:ExpectedPythonVersion was not found. Install the project runtime under .tools or run scripts\\windows\\bootstrap-runtime.ps1."
}

function Invoke-ProjectPythonBootstrap {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $bootstrap = Get-ProjectPythonBootstrap
    if ($bootstrap -is [string]) {
        & $bootstrap @Arguments
    }
    else {
        $bootstrapArguments = @($bootstrap.Arguments) + $Arguments
        & $bootstrap.FilePath @bootstrapArguments
    }
    if ($LASTEXITCODE -ne 0) { throw "Python bootstrap command failed (exit code $LASTEXITCODE)." }
}

function Get-ProjectPython {
    $python = Join-Path $script:ProjectRoot '.venv-runtime\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'The Windows virtual environment was not found. Run .\\setup.ps1 first.'
    }
    $version = (& $python -c 'import sys;print(chr(46).join(map(str,sys.version_info[:3])))').Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-ExactVersion -Actual $version -Expected $script:ExpectedPythonVersion)) {
        throw "The Windows virtual environment must use Python $script:ExpectedPythonVersion; found $version. Recreate .venv-runtime with .\\setup.ps1."
    }
    return $python
}

function Get-ProjectUv {
    $uv = Join-Path $script:ProjectRoot '.tools\uv\uv.exe'
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
        $command = Get-Command uv.exe -ErrorAction SilentlyContinue
        if ($command) { $uv = $command.Source }
    }
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
        throw 'uv was not found. Install the project runtime under .tools before running setup.'
    }
    return $uv
}

function Get-ProjectPnpm {
    $pnpmCandidates = @(
        (Join-Path $script:ProjectRoot '.tools\pnpm.cmd'),
        (Join-Path $script:ProjectRoot (".tools\node-{0}-win-x64\pnpm.cmd" -f $script:ExpectedNodeVersion))
    )
    $command = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if ($command) { $pnpmCandidates += $command.Source }
    foreach ($candidate in $pnpmCandidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $version = (& $candidate --version).Trim()
        if ($LASTEXITCODE -eq 0 -and (Test-ExactVersion -Actual $version -Expected $script:ExpectedPnpmVersion)) {
            return $candidate
        }
    }
    throw "pnpm $script:ExpectedPnpmVersion was not found. Enable Corepack for Node.js $script:ExpectedNodeVersion and retry setup."
}

Export-ModuleMember -Function Get-ProjectRoot, Get-ExpectedRuntimeVersions, Set-ProjectNodePath, Get-ProjectPythonBootstrap, Invoke-ProjectPythonBootstrap, Get-ProjectPython, Get-ProjectUv, Get-ProjectPnpm
