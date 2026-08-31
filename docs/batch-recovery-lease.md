# Batch recovery lease and internal finalization release gate

The recovery lease layer claims, renews, and releases ownership of a
previously-created queued DEMO_ONLY batch attempt. An internal-only finalization
CAS is now implemented on top of that lease: it verifies the active owner,
opaque token, generation, expiry, and crash-window identity in the same
transaction before changing the attempt to `SUCCEEDED`, clearing the lease,
and recording the success event/dispatch/progress updates.

This finalization boundary is not wired to a router, scheduler, worker, retry
endpoint, automatic rerun, or public recovery API. It does not invoke a
runner. The code-level finalization result is therefore not production
recovery authority until the PostgreSQL release gates below are complete.

DuckDB coverage is restricted to its serialized single-connection development
adapter. Before any multi-worker recovery capability is released, apply Alembic
head to a production-like PostgreSQL database and verify concurrent claims,
expiry takeover, stale renewal, and stale release from separate connections.

The finalizer compares-and-swaps the active owner, opaque token, generation,
expiry, and exact attempt identity in the same transaction as its state write.
Downstream event, dispatch, progress, or status-sync failure rolls back the
attempt state and lease together. DuckDB tests verify this serialized local
contract; separate-connection PostgreSQL tests remain mandatory before any
multi-worker recovery is enabled.

그 사내 gate test는
[`backend/tests/test_postgres_batch_recovery_concurrency.py`](../backend/tests/test_postgres_batch_recovery_concurrency.py)다.
claim race 단일 승자, 동일 owner/token claim idempotency race, fenced
finalization race와 private projection, 만료 predecessor의 successor 변경 차단,
finalization integrity 오류의 전체 rollback 5개를 서로 다른 PostgreSQL session으로
검증한다. `ANALYSIS_TEST_POSTGRES=1`, DB명이 정확히
`simdashboard_recovery_test` 또는 `simdashboard_recovery_test_<ticket>`인 dedicated DB,
`SIM_DASH_OWNER_ROLE`(기본 `simdashboard_owner`)과 다른 non-superuser app role,
database와 `public` schema `CREATE` 권한 모두 false, Alembic head, recovery table
DML 권한 가드를 통과하지 못하면 실행하지 않는다. 노트북에서 이 모듈이 `5 skipped`인
것은 정상이며 운영 합격 증거가 아니다.

An active retry with the same owner and opaque token is idempotent, including
when it repeats the initial expected generation. It returns the original lease
without extending expiry or incrementing generation. Once that token expires it
cannot be used to claim again; takeover requires a new opaque token and the
current expected generation. The token must therefore remain internal and is
removed from every public and administrator projection.

The DuckDB-to-PostgreSQL transfer tool now fail-closes when any recovery lease
owner/token/acquired/expiry metadata remains (including expired or malformed
rows), or when only part of the five lease columns exists. It records that
preflight in the manifest, takes one source snapshot, NULL-stages the five
reverse immediate-FK references, restores them with compare-and-swap updates
inside the target transaction, and commits only after the full checksum match.
The same blockers make a read-only dry-run exit non-zero before any PostgreSQL
connection, after an explicitly requested manifest or JSON report is emitted.
Execute mode also verifies the target table/column contract and required lease
constraints/indexes before its first INSERT.
Actual PostgreSQL live migration, app-role privilege verification, and
separate-connection lease/finalization concurrency remain release gates. Until
those gates pass, this lease plus internal-finalization slice is not production
recovery authority.

개인 노트북에서 닫을 수 있는 DuckDB/application 계약과 사내 PostgreSQL·proxy·Rocky
배포 release gate의 경계, 책임자, 인수인계 증적 형식은
[`personal-laptop-to-corporate-release-handoff.md`](personal-laptop-to-corporate-release-handoff.md)를 따른다.
