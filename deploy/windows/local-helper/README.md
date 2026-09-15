# Bundled Windows local helper

`windows-x64/` contains the verified standalone 0.1.0 release so a normal Git
checkout can serve the optional installer without a separate binary build.
The archive and `distribution-manifest.json` must both be committed; do not
replace the ZIP with a Git LFS pointer, which is not a downloadable ZIP.

The server checks archive size and SHA-256 before advertising the download.
Explicit `LOCAL_HELPER_DISTRIBUTION_DIR` settings and existing runtime releases
take precedence. The release importer continues writing to the runtime
directory by default; do not configure its target to this bundled directory.

Release contents: `SimulationWorkbenchLocalHelper.exe`,
`start-local-runner.ps1`, `start-local-runner.bat`, and a per-file integrity
manifest. No account databases, server environment files, pairing data, or
local user state are included. The 0.1.0 release metadata matches
`deploy/windows/local-helper-release.json`; the helper sources were introduced
in commit `0f8407b` and have not changed since that release.

For a new release, run `scripts/windows/build-local-helper-distribution.ps1`
with a new version on a Windows build machine, run both the distribution and
installer self-tests, then copy its versioned ZIP and manifest here together.
Keep only the current bundled version in the working tree. Publish its source
changes and metadata in the same update; do not reuse a versioned filename for
different bytes. Explicitly imported server releases remain operator-managed.
