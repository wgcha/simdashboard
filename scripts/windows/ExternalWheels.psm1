Set-StrictMode -Version Latest

# Optional, separately supplied wheels (ADR 0006: the SCX drive adapter
# `vd_scx_drive_adapter-*.whl`).  They are never committed or bundled.  The
# operator copies them once into a persistent `external-wheels` folder; every
# dependency sync (`uv pip sync`) or fresh release venv removes them, so the
# deployment scripts reinstall them from that folder right afterwards.
#
# Rules: local files only (`--no-index`), no dependency resolution
# (`--no-deps`), empty/absent folder in `none` mode does nothing.  In `scx`
# mode a missing or broken adapter is an error; otherwise it is a warning.

$script:AdapterModule = 'scx_drive_adapter'

function Get-ExternalWheelsDriveMode {
    [CmdletBinding()]
    param([string[]]$EnvFiles = @())

    # Same precedence as the app: process environment, then the .env files in
    # load order (python-dotenv does not override an already set value).
    $raw = [Environment]::GetEnvironmentVariable('SIMDASH_DRIVE_GATEWAY')
    if ($null -eq $raw) {
        foreach ($file in $EnvFiles) {
            if (-not $file -or -not (Test-Path -LiteralPath $file -PathType Leaf)) { continue }
            $found = $null
            foreach ($line in @(Get-Content -LiteralPath $file -ErrorAction Stop)) {
                if ($line -match '^\s*(?:export\s+)?SIMDASH_DRIVE_GATEWAY\s*=\s*(.*)$') {
                    $found = $Matches[1].Trim()
                    if ($found -match '^"(.*)"\s*(#.*)?$' -or $found -match "^'(.*)'\s*(#.*)?$") { $found = $Matches[1] }
                    else { $found = ($found -replace '\s+#.*$', '').Trim() }
                }
            }
            if ($null -ne $found) { $raw = $found; break }
        }
    }
    if ($null -eq $raw) { return 'none' }
    $value = $raw.Trim().ToLowerInvariant()
    if ($value -eq 'scx') { return 'scx' }
    return 'none'
}

function Get-ExternalWheelFiles {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$WheelDirectory)
    if (-not (Test-Path -LiteralPath $WheelDirectory -PathType Container)) { return @() }
    $item = Get-Item -LiteralPath $WheelDirectory -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "The external wheel folder must be a physical directory, not a link: $WheelDirectory" }
    return @(Get-ChildItem -LiteralPath $WheelDirectory -Filter '*.whl' -File -Force |
        Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
        Sort-Object Name | ForEach-Object { $_.FullName })
}

function Install-ExternalWheels {
    <#
    Installs every *.whl in WheelDirectory into the given Python environment
    with --no-deps --no-index and checks `import scx_drive_adapter` when the
    adapter wheel is present or scx mode is selected.  Returns a summary
    object.  Throws only in scx mode (or for an unsafe folder).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$WheelDirectory,
        [ValidateSet('none', 'scx')][string]$DriveMode = 'none',
        # When set, `uv pip install --python <Python>` is used (source venv has
        # no pip after `uv pip sync`); otherwise `python -m pip`.
        [string]$Uv = ''
    )

    $wheels = @(Get-ExternalWheelFiles -WheelDirectory $WheelDirectory)
    $result = [pscustomobject]@{ DriveMode = $DriveMode; Directory = $WheelDirectory; Wheels = $wheels; Installed = $false; AdapterImported = $false; Skipped = $false; Error = '' }
    if ($wheels.Count -eq 0) {
        if ($DriveMode -eq 'scx') {
            throw ("SIMDASH_DRIVE_GATEWAY=scx but no adapter wheel was found in '$WheelDirectory'. Copy vd_scx_drive_adapter-<version>-py3-none-any.whl into that folder once and rerun. " +
                "SCX 모드인데 '$WheelDirectory' 폴더에 어댑터 wheel이 없습니다. vd_scx_drive_adapter-<버전>-py3-none-any.whl을 이 폴더에 한 번 복사한 뒤 다시 실행하십시오.")
        }
        $result.Skipped = $true
        return $result
    }

    Write-Host "Installing $($wheels.Count) external wheel(s) from $WheelDirectory (--no-deps --no-index; drive mode: $DriveMode):"
    foreach ($wheel in $wheels) { Write-Host "  - $([IO.Path]::GetFileName($wheel))" }
    $failure = ''
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        if ($Uv) {
            $output = @(& $Uv pip install --python $Python --no-deps --no-index --reinstall @wheels 2>&1)
        }
        else {
            $output = @(& $Python -I -m pip --isolated install --no-deps --no-index --force-reinstall --disable-pip-version-check @wheels 2>&1)
        }
        $exitCode = [int]$LASTEXITCODE
        foreach ($line in $output) { Write-Host "    $line" }
        if ($exitCode -ne 0) { $failure = "external wheel installation failed (exit code $exitCode)" }
        else { $result.Installed = $true }

        $adapterSupplied = @($wheels | Where-Object { [IO.Path]::GetFileName($_) -match '^vd_scx_drive_adapter-' }).Count -gt 0
        if (-not $failure -and ($adapterSupplied -or $DriveMode -eq 'scx')) {
            $importOutput = @(& $Python -I -c "import $($script:AdapterModule)" 2>&1)
            $importExit = [int]$LASTEXITCODE
            if ($importExit -eq 0) { $result.AdapterImported = $true; Write-Host "  import $($script:AdapterModule): OK" }
            else {
                foreach ($line in $importOutput) { Write-Host "    $line" }
                $failure = "import $($script:AdapterModule) failed after installing the external wheel(s) (exit code $importExit)"
            }
        }
    }
    finally { $ErrorActionPreference = $previousPreference }

    if ($failure) {
        $result.Error = $failure
        if ($DriveMode -eq 'scx') {
            throw ("SIMDASH_DRIVE_GATEWAY=scx: $failure. Replace the wheel in '$WheelDirectory' with the adapter release's vd_scx_drive_adapter wheel and rerun. " +
                "SCX 모드: 외부 어댑터 wheel 설치·import에 실패했습니다. '$WheelDirectory'의 wheel을 어댑터 릴리스의 vd_scx_drive_adapter wheel로 바꾸고 다시 실행하십시오.")
        }
        Write-Warning "$failure. SIMDASH_DRIVE_GATEWAY is none, so the update continues without it. 외부 wheel 설치에 실패했지만 드라이브 모드가 none이므로 계속합니다."
    }
    elseif ($result.Installed) {
        Write-Host 'External wheels installed.' -ForegroundColor Green
    }
    return $result
}

Export-ModuleMember -Function Get-ExternalWheelsDriveMode, Get-ExternalWheelFiles, Install-ExternalWheels
