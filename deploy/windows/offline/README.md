# Simulation Workbench — Windows Server 2022 offline installer

Run the compiled installer EXE, or extract the entire ZIP and run `install.cmd`.
Windows will request elevation to register services. First installation asks
for the installation directory, bundled/existing PostgreSQL, and first web
administrator. Subsequent releases read the saved settings from the same path.

Default web address: `http://COMPUTERNAME:8080/home/` (or server IP).
The API listens on loopback 18080; a new bundled PostgreSQL uses loopback 55432.
The target needs Windows Server 2022 x64; no internet, Python, Node, Git or
VS Code installation is required. Every Python package is installed from the
included wheelhouse. API and web run as automatic Windows services.

Existing databases are backed up and verified before pending schema migrations.
Normal installation never requests database replacement. Credentials, accounts,
managed attachments and backups are persistent state. Database software major
upgrades and importing another PC's data are separate procedures.

Advanced: `install.ps1 -Config C:\deployment\install.json` accepts the provided
`config/install.example.json` structure. `-Check` validates the bundle and OS
without changing services or databases. For HTTPS supply an organization
certificate/key and its hostname; there is no online ACME request.

The installation keeps operational settings in
`C:\ProgramData\SimulationWorkbench\state\install-settings.json` by default.
Use the same install directory for every update. Read failure phase information
in `state/recovery-required.json`; do not reset an existing DB to bypass a failure.

Build source: `scripts/windows/build-offline-bundle.ps1`.
Full Korean guide: `docs/windows-server-offline-installation.md` in the source
repository. Follow `docs/windows-deployment-policy.md` for all future development.
An executable build and mock tests do not certify a clean disconnected Server
2022 VM; perform fresh/update/reboot acceptance before production release.
