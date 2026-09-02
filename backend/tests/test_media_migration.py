from __future__ import annotations

import importlib.util
import json
import sys
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
sys.modules[SPEC.name] = media_migration
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


def _png_bytes(label: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + label


def _rebuild_drop_video_assets_with_legacy_null(connection, video_id: str) -> None:
    rows = connection.execute(
        """
        SELECT video_id, load_case_id, blob_id, original_filename, mime_type,
               scene_name, sort_order, metadata_json
        FROM drop_video_assets
        ORDER BY video_id
        """
    ).fetchall()
    connection.execute("DROP TABLE drop_video_assets")
    connection.execute(
        """
        CREATE TABLE drop_video_assets (
            video_id VARCHAR PRIMARY KEY,
            load_case_id VARCHAR NOT NULL,
            blob_id VARCHAR,
            original_filename VARCHAR NOT NULL,
            mime_type VARCHAR NOT NULL,
            scene_name VARCHAR NOT NULL,
            sort_order INTEGER NOT NULL,
            metadata_json VARCHAR NOT NULL
        )
        """
    )
    for row in rows:
        values = list(row)
        if str(values[0]) == video_id:
            values[2] = None
        connection.execute(
            """
            INSERT INTO drop_video_assets
                (video_id, load_case_id, blob_id, original_filename, mime_type,
                 scene_name, sort_order, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
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


def test_migration_execute_rolls_back_all_prior_items_when_a_later_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    initialize_database()
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(_png_bytes(b"first"))
    second.write_bytes(_png_bytes(b"second"))
    _insert_legacy_asset("media-migration-rollback-01", first.name)
    _insert_legacy_asset("media-migration-rollback-02", second.name)
    monkeypatch.setattr(media_migration, "ASSET_ROOT", tmp_path.resolve())

    with connect() as connection:
        before_blobs = connection.execute("SELECT count(*) FROM asset_blobs").fetchone()[0]
        before_chunks = connection.execute("SELECT count(*) FROM asset_blob_chunks").fetchone()[0]
        plan = media_migration.plan_targets(connection, include_demo=False)
    second.write_bytes(_png_bytes(b"changed-late"))

    with pytest.raises(RuntimeError, match="changed after preflight"):
        media_migration.execute(plan)

    with connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM media_assets WHERE id IN (?, ?) AND blob_id IS NOT NULL",
            ["media-migration-rollback-01", "media-migration-rollback-02"],
        ).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM asset_blobs").fetchone()[0] == before_blobs
        assert connection.execute("SELECT count(*) FROM asset_blob_chunks").fetchone()[0] == before_chunks


def test_migration_execute_is_idempotent_for_already_connected_media(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    initialize_database()
    source = tmp_path / "idempotent.png"
    source.write_bytes(_png_bytes(b"idempotent"))
    _insert_legacy_asset("media-migration-idempotent", source.name)
    monkeypatch.setattr(media_migration, "ASSET_ROOT", tmp_path.resolve())

    with connect() as connection:
        plan = media_migration.plan_targets(connection, include_demo=False)
    first_results = media_migration.execute(plan)
    assert first_results["media"][0]["outcome"] == "migrated"

    with connect() as connection:
        blob_id = connection.execute(
            "SELECT blob_id FROM media_assets WHERE id='media-migration-idempotent'"
        ).fetchone()[0]
        blob_count = connection.execute("SELECT count(*) FROM asset_blobs").fetchone()[0]
        second_plan = media_migration.plan_targets(connection, include_demo=False)
    assert second_plan["total"] == 0
    assert media_migration.execute(second_plan) == {"media": [], "demo": []}
    with connect() as connection:
        assert connection.execute(
            "SELECT blob_id FROM media_assets WHERE id='media-migration-idempotent'"
        ).fetchone()[0] == blob_id
        assert connection.execute("SELECT count(*) FROM asset_blobs").fetchone()[0] == blob_count


def test_migration_plan_skips_fully_bound_demos_without_reading_legacy_video_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    initialize_database()
    empty_legacy_video_root = tmp_path / "video_example"
    empty_legacy_video_root.mkdir()
    monkeypatch.setattr(media_migration, "DROP_VIDEO_SOURCE_DIR", empty_legacy_video_root)

    with connect() as connection:
        plan = media_migration.plan_targets(connection, include_demo=True)

    assert plan == {"media": [], "demo": [], "total": 0}


def test_migration_rejects_stale_plan_when_another_blob_is_already_connected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    initialize_database()
    source = tmp_path / "expected.png"
    different = tmp_path / "different.png"
    source.write_bytes(_png_bytes(b"expected"))
    different.write_bytes(_png_bytes(b"different"))
    _insert_legacy_asset("media-migration-stale-plan", source.name)
    monkeypatch.setattr(media_migration, "ASSET_ROOT", tmp_path.resolve())

    with connect() as connection:
        plan = media_migration.plan_targets(connection, include_demo=False)
        stored = media_migration.store_file(
            connection,
            different,
            filename=different.name,
            mime_type="image/png",
            asset_type="IMAGE",
        )
        media_migration.attach_stored_media(connection, "media-migration-stale-plan", stored)

    with pytest.raises(RuntimeError, match="다른 blob"):
        media_migration.execute(plan)


def test_migration_dry_run_does_not_change_database_or_write_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    initialize_database()
    source = tmp_path / "dry-run.png"
    source.write_bytes(_png_bytes(b"dry-run"))
    _insert_legacy_asset("media-migration-dry-run", source.name)
    monkeypatch.setattr(media_migration, "ASSET_ROOT", tmp_path.resolve())
    monkeypatch.setattr(
        media_migration,
        "initialize_database",
        lambda: (_ for _ in ()).throw(AssertionError("migration CLI must not initialize the database")),
    )
    receipt = tmp_path / "must-not-exist.json"
    monkeypatch.setattr(sys, "argv", ["migrate_media_to_database.py", "--no-demo"])

    assert media_migration.main() == 0
    assert not receipt.exists()
    with connect() as connection:
        assert connection.execute(
            "SELECT blob_id FROM media_assets WHERE id='media-migration-dry-run'"
        ).fetchone()[0] is None


def test_migration_execute_requires_and_writes_non_overwritable_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    initialize_database()
    source = tmp_path / "receipt.png"
    source.write_bytes(_png_bytes(b"receipt"))
    _insert_legacy_asset("media-migration-receipt", source.name)
    monkeypatch.setattr(media_migration, "ASSET_ROOT", tmp_path.resolve())
    monkeypatch.setattr(
        media_migration,
        "initialize_database",
        lambda: (_ for _ in ()).throw(AssertionError("migration CLI must not initialize the database")),
    )
    receipt = tmp_path / "migration-receipt.json"

    monkeypatch.setattr(sys, "argv", ["migrate_media_to_database.py", "--execute", "--no-demo"])
    with pytest.raises(SystemExit) as missing_receipt:
        media_migration.main()
    assert missing_receipt.value.code == 2
    with connect() as connection:
        assert connection.execute(
            "SELECT blob_id FROM media_assets WHERE id='media-migration-receipt'"
        ).fetchone()[0] is None

    monkeypatch.setattr(
        sys,
        "argv",
        ["migrate_media_to_database.py", "--execute", "--no-demo", "--receipt", str(receipt)],
    )
    assert media_migration.main() == 0
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["format"] == "simdashboard-media-migration-receipt"
    assert payload["format_version"] == 1
    assert payload["migration_id"]
    assert payload["executed_at"].endswith("Z")
    assert payload["planned_total"] == 1
    assert payload["media"] == [
        {
            "asset_id": "media-migration-receipt",
            "legacy_logical_path": source.name,
            "source_sha256": media_migration.inspect_file(
                source, filename=source.name, mime_type="image/png", asset_type="IMAGE"
            ).sha256,
            "source_size": source.stat().st_size,
            "blob_id": payload["media"][0]["blob_id"],
            "outcome": "migrated",
        }
    ]
    # The writer requests 0600 on its O_EXCL pending reservation. The WSL
    # fixture root may be a Windows mount that does not preserve POSIX modes.
    assert receipt.is_file()

    second = tmp_path / "receipt-second.png"
    second.write_bytes(_png_bytes(b"second-receipt"))
    _insert_legacy_asset("media-migration-receipt-second", second.name)
    with pytest.raises(FileExistsError, match="덮어쓸 수 없습니다"):
        media_migration.main()
    with connect() as connection:
        assert connection.execute(
            "SELECT blob_id FROM media_assets WHERE id='media-migration-receipt-second'"
        ).fetchone()[0] is None


def test_migration_execute_backfills_a_legacy_null_demo_blob_in_place() -> None:
    initialize_database()
    scene = media_migration.DROP_VIDEO_DEMO_SCENES[0]
    with connect() as connection:
        _rebuild_drop_video_assets_with_legacy_null(connection, scene.video_id)
        plan = media_migration.plan_targets(connection, include_demo=True)
    assert [item["video_id"] for item in plan["demo"]] == [scene.video_id]

    results = media_migration.execute(plan)

    assert results["demo"] == [
        {
            "video_id": scene.video_id,
            "load_case_id": "loadcase-drop-bottom-001",
            "legacy_logical_path": f"video_example/{scene.filename}",
            "source_sha256": plan["demo"][0]["sha256"],
            "source_size": plan["demo"][0]["file_size"],
            "blob_id": results["demo"][0]["blob_id"],
            "outcome": "migrated",
        }
    ]
    with connect() as connection:
        assert connection.execute(
            "SELECT blob_id FROM drop_video_assets WHERE video_id=?", [scene.video_id]
        ).fetchone()[0] == results["demo"][0]["blob_id"]


def test_migration_never_overwrites_a_racing_non_null_demo_blob() -> None:
    initialize_database()
    scene = media_migration.DROP_VIDEO_DEMO_SCENES[0]
    other = media_migration.DROP_VIDEO_DEMO_SCENES[1]
    with connect() as connection:
        other_blob_id = connection.execute(
            "SELECT blob_id FROM drop_video_assets WHERE video_id=?", [other.video_id]
        ).fetchone()[0]
        _rebuild_drop_video_assets_with_legacy_null(connection, scene.video_id)
        plan = media_migration.plan_targets(connection, include_demo=True)
        connection.execute("UPDATE drop_video_assets SET blob_id=? WHERE video_id=?", [other_blob_id, scene.video_id])

    with pytest.raises(RuntimeError, match="다른 blob"):
        media_migration.execute(plan)
    with connect() as connection:
        assert connection.execute(
            "SELECT blob_id FROM drop_video_assets WHERE video_id=?", [scene.video_id]
        ).fetchone()[0] == other_blob_id
