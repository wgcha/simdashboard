[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$source = Join-Path $root 'deploy\windows\offline\install.ps1'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
foreach ($name in @('Fail', 'Require-File', 'Read-Json', 'Safe-Relative', 'Assert-BundleTree', 'Verify-BundleManifest')) {
    $node = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $false)
    if (-not $node) { throw "Missing manifest verifier function $name" }
    . ([scriptblock]::Create($node.Extent.Text))
}
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('workbench-manifest-' + [Guid]::NewGuid().ToString('N'))
$BundleRoot = Join-Path $testRoot 'bundle'
New-Item -ItemType Directory -Path $BundleRoot -Force | Out-Null
function Reset-Fixture {
    [IO.File]::WriteAllText((Join-Path $BundleRoot 'RELEASE_ID'), 'test1')
    [IO.File]::WriteAllText((Join-Path $BundleRoot 'payload.txt'), 'original')
    $files = @(foreach ($name in @('RELEASE_ID', 'payload.txt')) {
        $file = Join-Path $BundleRoot $name
        @{ path = $name; bytes = (Get-Item -LiteralPath $file).Length; sha256 = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash }
    })
    $script:fixture = @{ format = 'simulation-workbench-windows-offline-bundle'; format_version = 1; release_id = 'test1'; target = @{os='Windows Server 2022'; architecture='x64'; network='none'}; files=$files }
    Save-Fixture
}
function Save-Fixture { $fixture | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $BundleRoot 'bundle-manifest.json') -Encoding UTF8 }
function Assert-Rejected([string]$Label, [scriptblock]$Action) {
    $rejected = $false
    try { & $Action | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw "Manifest accepted $Label" }
}
try {
    Reset-Fixture
    if ((Verify-BundleManifest).release_id -ne 'test1') { throw 'Valid manifest rejected.' }
    [IO.File]::WriteAllText((Join-Path $BundleRoot 'payload.txt'), 'modified')
    Assert-Rejected 'tampered payload' { Verify-BundleManifest }
    Reset-Fixture
    $fixture.files += $fixture.files[0]; Save-Fixture
    Assert-Rejected 'duplicate paths' { Verify-BundleManifest }
    Reset-Fixture
    $fixture.files = @($fixture.files[0]); Save-Fixture
    Assert-Rejected 'unlisted file' { Verify-BundleManifest }
    Reset-Fixture
    $fixture.files[1].path = '..\outside.txt'; Save-Fixture
    Assert-Rejected 'path traversal' { Verify-BundleManifest }
    Reset-Fixture
    $fixture.release_id = '..'; Save-Fixture
    Assert-Rejected 'release path traversal' { Verify-BundleManifest }
    Reset-Fixture
    foreach ($path in @('file.txt:alternate', '.\payload.txt', 'payload.txt.', 'C:\outside', '')) { Assert-Rejected 'unsafe Windows path' { Safe-Relative $path } }
    Reset-Fixture
    $outside = Join-Path $testRoot 'outside'
    New-Item -ItemType Directory -Path $outside | Out-Null
    $link = Join-Path $BundleRoot 'linked'
    New-Item -ItemType Junction -Path $link -Target $outside | Out-Null
    try { Assert-Rejected 'junction in bundle' { Verify-BundleManifest } }
    finally { [IO.Directory]::Delete($link) }
    Write-Host 'Offline manifest behavior tests passed: valid, tampered, omitted, duplicate, unsafe paths and junction.' -ForegroundColor Green
} finally {
    $resolved = (Resolve-Path -LiteralPath $testRoot).Path
    if (-not $resolved.StartsWith([IO.Path]::GetTempPath().TrimEnd('\') + '\workbench-manifest-', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture cleanup path.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
