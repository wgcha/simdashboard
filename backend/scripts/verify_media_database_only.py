from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import database_settings  # noqa: E402
from app.database import connect  # noqa: E402
from app.database_connection import postgres_connection_budget  # noqa: E402
from app.services.media_integrity import MediaIntegrityError, media_inventory, require_media_integrity  # noqa: E402
from scripts.check_postgres_pool_budget import check_budget  # noqa: E402


BACKEND = Path(__file__).resolve().parents[1]


def _expected_revision() -> str:
    """Return the single Alembic head from the checked-in migration graph.

    This verifier used to carry a copied revision literal.  That made the
    production database-only gate silently become stale whenever a migration
    was added after that literal.  Alembic's version scripts are the migration
    source of truth, so resolve the head from that graph at verification time
    and fail closed if the graph ever develops multiple heads.
    """
    config = Config(str(BACKEND / "alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Alembic migration head is invalid: {heads}")
    return heads[0]


def _revision_matches(*, backend: str, current: str | None, expected: str | None) -> bool:
    """Require a current Alembic revision whenever PostgreSQL is selected."""
    return backend != "postgresql" or current == expected


def _single_revision(rows: list[object]) -> str | None:
    """Return the only populated version row; reject corrupt multi-row state."""
    if len(rows) != 1:
        return None
    row = rows[0]
    if not row or not row[0]:
        return None
    return str(row[0])


def _private_path(raw: str) -> Path:
    root = (Path(__file__).resolve().parents[1] / "assets").resolve()
    parts = Path(raw).parts
    relative = Path(*parts[1:]) if parts and parts[0].lower() == "assets" else Path(raw)
    path = (root / relative).resolve()
    if root not in path.parents:
        raise RuntimeError(f"unsafe legacy media path: {raw}")
    return path


def verify() -> dict[str, object]:
    settings = database_settings()
    expected_revision = _expected_revision() if settings.backend == "postgresql" else None
    raw_public_root = Path(__file__).resolve().parents[1] / "public_assets"
    raw_legacy_root = Path(__file__).resolve().parents[1] / "assets"
    if raw_public_root.is_symlink() or raw_legacy_root.is_symlink():
        raise RuntimeError("public/static and legacy roots may not be symlinks")
    public_root = raw_public_root.resolve()
    legacy_root = raw_legacy_root.resolve()
    if public_root == legacy_root or public_root in legacy_root.parents or legacy_root in public_root.parents:
        raise RuntimeError("public/static and legacy roots overlap")

    with connect() as connection:
        report = media_inventory(connection)
        legacy_paths = [
            row[0]
            for row in connection.execute(
                "SELECT file_path FROM media_assets WHERE blob_id IS NULL AND file_path IS NOT NULL"
            ).fetchall()
        ]
        revision = None
        revision_row_count = None
        permission_failures: list[str] = []
        if settings.backend == "postgresql":
            revision_rows = connection.execute("SELECT version_num FROM alembic_version ORDER BY version_num").fetchall()
            revision_row_count = len(revision_rows)
            revision = _single_revision(revision_rows)
            current_user = str(connection.execute("SELECT current_user").fetchone()[0])
            expected_role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app")
            if current_user != expected_role:
                permission_failures.append("current_user:unexpected_role")
            required_privileges = {
                "asset_blobs": "SELECT,INSERT,UPDATE,DELETE",
                "asset_blob_chunks": "SELECT,INSERT,UPDATE,DELETE",
                "media_assets": "SELECT,INSERT,UPDATE",
                "drop_video_assets": "SELECT,INSERT,UPDATE",
            }
            for table, privileges in required_privileges.items():
                allowed = connection.execute(
                    "SELECT has_table_privilege(current_user, ?, ?)", [table, privileges]
                ).fetchone()[0]
                if not allowed:
                    permission_failures.append(f"{table}:{privileges}")
    for raw in legacy_paths:
        if public_root in _private_path(str(raw)).parents:
            raise RuntimeError(f"legacy media is reachable below public root: {raw}")

    workers = int(os.getenv("UVICORN_WORKERS", "1"))
    connection_budget = postgres_connection_budget(workers, settings.postgres_pool)
    configured_max = os.getenv("POSTGRES_MAX_CONNECTIONS")
    if settings.backend == "postgresql" and configured_max:
        # Keep this operational gate identical to the canonical pool checker,
        # including reserved capacity and the dedicated import-gate session.
        connection_budget = check_budget(
            workers,
            int(configured_max),
            int(os.getenv("POSTGRES_RESERVED_CONNECTIONS", "10")),
        )
    revision_valid = _revision_matches(backend=settings.backend, current=revision, expected=expected_revision)
    try:
        require_media_integrity(report)
    except MediaIntegrityError as error:
        media_error = str(error)
    else:
        media_error = None
    if media_error or permission_failures or not revision_valid:
        raise RuntimeError(json.dumps({
            "media_inventory": report,
            "media_error": media_error,
            "revision": revision,
            "expected_revision": expected_revision,
            "revision_row_count": revision_row_count,
            "permission_failures": permission_failures,
        }, ensure_ascii=False, sort_keys=True))
    return {
        "status": "database_only",
        "backend": settings.backend,
        **report,
        "connection_budget": connection_budget,
        "revision": revision,
        "expected_revision": expected_revision,
        "revision_row_count": revision_row_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the database-only media storage gate.")
    parser.parse_args()
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        raise SystemExit(1)
