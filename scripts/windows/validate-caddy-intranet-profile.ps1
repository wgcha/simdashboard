[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Caddyfile,
    [string]$CaddyExecutable = ''
)

$ErrorActionPreference = 'Stop'
try {
    $Caddyfile = [IO.Path]::GetFullPath($Caddyfile)
    if (-not (Test-Path -LiteralPath $Caddyfile -PathType Leaf)) { throw 'Caddyfile was not found.' }
    $content = [IO.File]::ReadAllText($Caddyfile)
    if ($content -match '__REPLACE_[A-Z_]+__') { throw 'Caddyfile still contains a replacement token.' }
    $fileToken = '(?:"[^"\r\n]+"|[^\s"]+)'
    if ($content -notmatch ('(?m)^\s*tls\s+' + $fileToken + '\s+' + $fileToken + '\s*$') -or $content -notmatch '(?m)^\s*handle\s+@api\s*\{') { throw 'Caddyfile is missing the manual TLS or API route contract.' }
    if ($CaddyExecutable) {
        $CaddyExecutable = [IO.Path]::GetFullPath($CaddyExecutable)
        if (-not (Test-Path -LiteralPath $CaddyExecutable -PathType Leaf)) { throw 'Caddy executable was not found.' }
        & $CaddyExecutable validate --config $Caddyfile --adapter caddyfile | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Caddy rejected the supplied configuration.' }
    }
    Write-Host 'Caddy intranet profile is ready for an operator-supplied certificate and service configuration.' -ForegroundColor Green
    exit 0
}
catch {
    Write-Host 'Caddy intranet profile is unavailable. Review the local configuration and certificate paths.' -ForegroundColor Yellow
    exit 1
}
