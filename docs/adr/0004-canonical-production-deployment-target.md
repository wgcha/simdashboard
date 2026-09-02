# ADR 0004: Canonical production deployment target

- Status: accepted
- Date: 2026-08-24

## Decision

The canonical production target is **Rocky Linux 8 + nginx + systemd +
PostgreSQL 18**:

```text
client → nginx (TLS) → loopback FastAPI systemd service → PostgreSQL 18
```

The `deploy/rocky8/` bundle and runbook are the production deployment
baseline. The service runs with the application database role; migrations,
privilege changes, backup and restore remain owner/admin operations. `SIMDASH_IMPORT_ROOT`
is an external, explicitly configured read-only input root and must survive
application release replacement.

Windows remains a **development and database-migration compatibility profile**.
It supports local execution, PostgreSQL setup/transfer, DuckDB-to-PostgreSQL
migration and compatibility preflight through the existing Windows scripts.
`DEPLOYMENT_PROFILE=windows-vm-intranet` is therefore a compatibility contract,
not the canonical production target.

There is currently no supported Windows one-command production deployment with
the Rocky-equivalent nginx/reverse-proxy, Windows service, TLS binding,
firewall and health-rollback automation. A Windows production target requires
a separately approved `deploy/windows/` implementation and ADR amendment; it
must not be inferred from the compatibility profile.

## Implementation status

- Decision and documentation alignment: complete.
- Rocky installer, nginx/systemd integration, PostgreSQL role model and
  rollback baseline: implemented, subject to the release gates in the runbook.
- Rocky `SIMDASH_IMPORT_ROOT` installer/service wiring and external mount
  preflight: implemented in the current deployment templates; release
  validation on an actual Rocky host remains pending.
- Windows production deployment automation: intentionally not implemented.

## Consequences

- Production acceptance, smoke tests, proxy hardening and offline package
  requirements are defined against Rocky 8.
- Windows tests protect development and data-transfer compatibility; they do
  not certify production deployment.
- Documentation must distinguish Vite development proxy, outbound corporate
  proxy and nginx production reverse proxy.
- New deployment work should extend `deploy/rocky8/` first. Windows deployment
  work is out of scope until a separate target is approved.

## Revisit conditions

Revisit this ADR only when the organization approves a different operating
system, reverse-proxy/service model, PostgreSQL major version, or a separately
maintained Windows production deployment project.
