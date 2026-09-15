# Local runner

`local_runner` runs registered desktop programs only on this PC. The normal
mode is managed: it connects to one fixed central Simulation Workbench server,
then keeps a separate catalogue, batches, history, executor, and SQLite file
for each approved account-PC binding.

## Start managed mode

For an individual Windows PC, sign in to the web application and open
**내 PC 설정 → PC 도우미 설치 파일 받기**. The setup installs the bundled runtime
under the current user's LocalAppData directory; source, Git and a separate
Python installation are not required on that PC. The central server must have
a verified Windows release available. Release-build and server preparation
steps are in [personal onboarding](../docs/personal-onboarding-implementation.md).

The command below remains available for source-based development:

```powershell
./start-local-runner.ps1 -ServerUrl https://workbench.example.com
```

The launcher derives the allowed browser origin from `ServerUrl`; pass exact
additional values with `-Origin`. It checks the public loopback identity
endpoint and never prints a connection code. HTTPS is required except for
`localhost`, `127.0.0.1`, or `::1` development servers. The server URL is
fixed in the helper configuration and the helper does not follow redirects.

At the first connection, the web app creates a short-lived pairing request.
The helper previews the account with the central server and shows a native
Windows confirmation. Only after approval does it redeem the request and save
the per-binding device secret locally. Browser pages receive neither the
secret nor a long-lived local token.

Use `-InstallAutoStart` once to create a current-user Startup launcher. It
does not change startup settings unless that switch is supplied. The helper
must be running for the browser to use local programs; opening the web app
later restores an existing approved binding automatically.

## Managed security and recovery

- `/v1/identity` is deliberately minimal and unauthenticated. All account
  operations use a short-lived central binding session and are introspected by
  the fixed server before catalogue, history, execution, retry, or completion.
- The central server fixes the actor and work context. A local retry or
  completion is checked against the immutable original run context.
- Execute and retry grants are stored before process launch. Run state changes
  enter a durable SQLite outbox with monotonic per-run sequence numbers and
  are retried in the background after browser closure, network failure, or
  helper restart.
- A central outage rejects new work but does not stop an already launched
  process. Revoked or expired sessions fail closed.
- Loopback Host, exact Origin, JSON body type, and a 64 KiB body limit are
  enforced. Program execution uses `shell=False` and rejects script entry
  points.

## Legacy standalone development mode

The old bearer connection-code API remains solely for explicit local
development and has its own database/configuration:

```powershell
.venv-runtime\Scripts\python.exe -m local_runner --standalone --data-dir "$env:TEMP\local-runner-dev" --port 8766
```

`--print-token` also requires `--standalone`. Managed mode never imports the
legacy `local-runner.sqlite3` automatically.
