from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.database import connect, initialize_database


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "media_migration_script",
    ROOT / "scripts" / "migrate_media_to_database.py",
)
assert SPEC and SPEC.loader
media_migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media_migration)


def _insert_legacy_asset(asset_id: str, file_path: str) -> None:
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO media_assets
                (id, analysis_run_id, asset_type, title, file_path, mime_type,
                 file_size, checksum, metadata_json)
            VALUES (?, 'run-drop-001', 'IMAGE', 'migration test', ?, 'image/png', NULL, NULL, ?)
            """,
            [asset_id, file_path, json.dumps({"test": True})],
        )


def test_migration_preflight_validates_bytes_before_database_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    initialize_database()
    source = tmp_path / "fake.png"
    source.write_bytes(b"not-a-png")
    _insert_legacy_asset("media-migration-invalid", source.name)
    monkeypatch.setattr(media_migration, "ASSET_ROOT", tmp_path.resolve())

    with connect() as connection:
        before = connection.execute("SELECT count(*) FROM asset_blobs").fetchone()[0]
        with pytest.raises(ValueError, match="PNG signature"):
            media_migration.plan_targets(connection, include_demo=False)
        after = connection.execute("SELECT count(*) FROM asset_blobs").fetchone()[0]
    assert before == after


def test_migration_execute_rejects_a_file_changed_after_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    initialize_database()
    source = tmp_path / "changing.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
    _insert_legacy_asset("media-migration-changing", source.name)
    monkeypatch.setattr(media_migration, "ASSET_ROOT", tmp_path.resolve())

    with connect() as connection:
        plan = media_migration.plan_targets(connection, include_demo=False)
    source.write_bytes(b"\x89PNG\r\n\x1a\nsecond")

    with pytest.raises(RuntimeError, match="changed after preflight"):
        media_migration.execute(plan)
    with connect() as connection:
        assert connection.execute(
            "SELECT blob_id FROM media_assets WHERE id='media-migration-changing'"
        ).fetchone()[0] is None
