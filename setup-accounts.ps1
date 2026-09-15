[CmdletBinding()]
param(
    [switch]$NoPause,
    [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$exitCode = 1

try {
    Import-Module (Join-Path $Root 'scripts\windows\Runtime.psm1') -Force
    $Python = Get-ProjectPython
    $arguments = @((Join-Path $Root 'backend\scripts\setup_accounts.py'))
    if ($NonInteractive) { $arguments += '--non-interactive' }

    Push-Location (Join-Path $Root 'backend')
    try {
        & $Python @arguments
        $exitCode = [int]$LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    if ($exitCode -eq 0) {
        Write-Host 'Account setup completed successfully.' -ForegroundColor Green
    }
    else {
        Write-Host "Account setup did not complete (exit code $exitCode). Review the messages above and retry after correcting the reported problem." -ForegroundColor Red
    }
}
catch {
    # The batch wrapper pauses after this message for double-click launches.
    # Do not print the exception object: startup failures can contain command
    # context that is not useful to an operator and may include configuration.
    Write-Host 'Account setup could not be launched. Verify the project runtime and retry.' -ForegroundColor Red
    $exitCode = 1
}

exit $exitCode
