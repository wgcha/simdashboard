from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.database import connect, initialize_database
from app.services.media_integrity import media_inventory, require_media_integrity
from app.services.media_storage_service import attach_stored_media, store_file


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "media_cleanup_script",
    ROOT / "scripts" / "cleanup_migrated_media_files.py",
)
assert SPEC and SPEC.loader
media_cleanup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = media_cleanup
SPEC.loader.exec_module(media_cleanup)
ORIGINAL_VERIFY_BACKUP_ARCHIVE = media_cleanup._verify_backup_archive

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _png_bytes(label: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + label


def _insert_legacy_asset(asset_id: str, file_path: str) -> None:
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO media_assets
                (id, analysis_run_id, asset_type, title, file_path, mime_type,
                 file_size, checksum, metadata_json)
            VALUES (?, 'run-drop-001', 'IMAGE', 'cleanup test', ?, 'image/png', NULL, NULL, ?)
            """,
            [asset_id, file_path, json.dumps({"test": True})],
        )


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _prepared_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    asset_id: str = "media-cleanup-asset",
    relative_path: str = "legacy.png",
) -> tuple[Path, dict[str, object], dict[str, object], str, Path]:
    initialize_database()
    monkeypatch.setattr(media_cleanup, "_verify_backup_archive", lambda _path: None)
    source = tmp_path / relative_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(_png_bytes(asset_id.encode("utf-8")))
    _insert_legacy_asset(asset_id, relative_path)
    monkeypatch.setattr(media_cleanup, "ASSET_ROOT", tmp_path.resolve())
    with connect() as connection:
        stored = store_file(
            connection,
            source,
            filename=source.name,
            mime_type="image/png",
            asset_type="IMAGE",
        )
        attach_stored_media(connection, asset_id, stored)
        inventory = media_inventory(connection)
    require_media_integrity(inventory)
    source_size, source_sha256 = media_cleanup._fingerprint(source)
    migration_id = "migration-cleanup-001"
    migration_time = NOW - timedelta(days=8)
    backup_time = NOW - timedelta(days=7)
    migration = {
        "format": "simdashboard-media-migration-receipt",
        "format_version": 1,
        "state": "COMPLETED",
        "migration_id": migration_id,
        "executed_at": migration_time.isoformat().replace("+00:00", "Z"),
        "media": [{
            "asset_id": asset_id,
            "legacy_logical_path": relative_path,
            "source_sha256": source_sha256,
            "source_size": source_size,
            "blob_id": stored.blob.id,
            "outcome": "migrated",
        }],
        "demo": [],
    }
    backup = {
        "format": "postgresql-custom",
        "created_at": backup_time.isoformat(),
        "media_inventory": inventory,
    }
    archive = tmp_path / "analysis-canvas-20260818T120000Z.dump"
    archive.write_bytes(b"verified disposable backup archive")
    archive_size, archive_sha256 = media_cleanup._fingerprint(archive)
    backup.update({"filename": archive.name, "bytes": archive_size, "sha256": archive_sha256})
    return source, migration, backup, migration_id, archive


def test_cleanup_accepts_exact_seven_day_boundary_and_deduplicates_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, migration, backup, _, _archive = _prepared_gate(tmp_path, monkeypatch)
    duplicate = dict(migration["media"][0])
    duplicate["asset_id"] = "media-cleanup-asset"
    migration["media"].append(duplicate)

    plan = media_cleanup.build_cleanup_plan(
        migration_receipt=migration,
        backup_manifest=backup,
        now=NOW,
    )

    assert source.exists()
    assert len(plan["candidates"]) == 1
    assert plan["candidates"][0]["asset_ids"] == ["media-cleanup-asset", "media-cleanup-asset"]


def test_cleanup_rejects_stale_pre_migration_or_wrong_backup_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _source, migration, backup, _, _archive = _prepared_gate(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="읽을 수 없습니다"):
        media_cleanup._read_json(tmp_path / "missing-backup.json", label="backup manifest")

    stale = dict(backup)
    stale["created_at"] = (NOW - timedelta(days=6)).isoformat()
    with pytest.raises(RuntimeError, match="최소 7일"):
        media_cleanup.build_cleanup_plan(migration_receipt=migration, backup_manifest=stale, now=NOW)

    before_migration = dict(backup)
    before_migration["created_at"] = (NOW - timedelta(days=9)).isoformat()
    with pytest.raises(RuntimeError, match="이후"):
        media_cleanup.build_cleanup_plan(migration_receipt=migration, backup_manifest=before_migration, now=NOW)

    wrong_inventory = dict(backup)
    wrong_inventory["media_inventory"] = {**backup["media_inventory"], "blob_count": -1}
    with pytest.raises(RuntimeError, match="inventory"):
        media_cleanup.build_cleanup_plan(migration_receipt=migration, backup_manifest=wrong_inventory, now=NOW)


def test_cleanup_rejects_database_drift_source_drift_and_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, migration, backup, _, _archive = _prepared_gate(tmp_path, monkeypatch)
    with connect() as connection:
        connection.execute("UPDATE media_assets SET file_path='drifted.png' WHERE id='media-cleanup-asset'")
    with pytest.raises(RuntimeError, match="현재 DB blob"):
        media_cleanup.build_cleanup_plan(migration_receipt=migration, backup_manifest=backup, now=NOW)

    source, migration, backup, _, _archive = _prepared_gate(tmp_path, monkeypatch, asset_id="media-cleanup-drift")
    plan = media_cleanup.build_cleanup_plan(migration_receipt=migration, backup_manifest=backup, now=NOW)
    original_rename = os.rename

    def mutate_between_descriptor_check_and_rename(src, dst, *args, **kwargs):
        if src == source.name and kwargs.get("src_dir_fd") is not None:
            source.write_bytes(_png_bytes(b"changed-before-quarantine"))
        return original_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(media_cleanup.os, "rename", mutate_between_descriptor_check_and_rename)
    monkeypatch.setattr(media_cleanup, "_require_secure_dirfd_support", lambda: None)
    with pytest.raises(RuntimeError, match="quarantine"):
        media_cleanup.execute_cleanup(plan)
    assert not source.exists()
    quarantined = list(tmp_path.glob(".simdashboard-cleanup-quarantine-*/legacy.png"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == _png_bytes(b"changed-before-quarantine")

    source, migration, backup, _, _archive = _prepared_gate(tmp_path, monkeypatch, asset_id="media-cleanup-symlink")
    link = tmp_path / "legacy-link.png"
    try:
        link.symlink_to(source.name)
    except OSError:
        pytest.skip("test filesystem does not support symlink fixtures")
    with connect() as connection:
        connection.execute("UPDATE media_assets SET file_path=? WHERE id=?", [link.name, "media-cleanup-symlink"])
    migration["media"][0]["legacy_logical_path"] = link.name
    with pytest.raises(RuntimeError, match="symlink"):
        media_cleanup.build_cleanup_plan(migration_receipt=migration, backup_manifest=backup, now=NOW)

    source, migration, backup, _, _archive = _prepared_gate(tmp_path, monkeypatch, asset_id="media-cleanup-demo-path")
    with connect() as connection:
        connection.execute("UPDATE media_assets SET file_path=? WHERE id=?", ["assets/VIDEO_EXAMPLE/legacy.mp4", "media-cleanup-demo-path"])
    migration["media"][0]["legacy_logical_path"] = "assets/VIDEO_EXAMPLE/legacy.mp4"
    with pytest.raises(RuntimeError, match="video_example"):
        media_cleanup.build_cleanup_plan(migration_receipt=migration, backup_manifest=backup, now=NOW)


@pytest.mark.parametrize("damage", ["missing", "bytes", "manifest-sha", "wrong-format", "zero-byte"])
def test_cleanup_rejects_missing_or_tampered_backup_before_database_or_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
):
    source, migration, backup, _migration_id, archive = _prepared_gate(tmp_path, monkeypatch)
    migration_path = _write_json(tmp_path / "migration.json", migration)
    backup_manifest_path = _write_json(tmp_path / "backup.json", backup)
    if damage == "missing":
        archive.unlink()
    elif damage == "bytes":
        archive.write_bytes(b"tampered archive bytes")
    elif damage == "manifest-sha":
        backup["sha256"] = "0" * 64
        _write_json(backup_manifest_path, backup)
    elif damage == "wrong-format":
        backup["format"] = "other-backup-format"
        _write_json(backup_manifest_path, backup)
    else:
        archive.write_bytes(b"")
        backup["bytes"] = 0
        backup["sha256"] = media_cleanup._fingerprint(archive)[1]
        _write_json(backup_manifest_path, backup)
    monkeypatch.setattr(
        media_cleanup,
        "connect",
        lambda: pytest.fail("backup evidence must be rejected before DB access"),
    )
    monkeypatch.setattr(media_cleanup, "utc_now", lambda: NOW)
    monkeypatch.setattr(sys, "argv", [
        "cleanup_migrated_media_files.py", "--migration-receipt", str(migration_path),
        "--backup-manifest", str(backup_manifest_path), "--backup", str(archive), "--approval-id", "CAB-123",
    ])

    with pytest.raises(RuntimeError, match="backup archive|backup manifest"):
        media_cleanup.main()
    assert source.exists()


def test_cleanup_rejects_symlink_backup_before_database_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, migration, backup, _migration_id, archive = _prepared_gate(tmp_path, monkeypatch)
    migration_path = _write_json(tmp_path / "migration.json", migration)
    backup_manifest_path = _write_json(tmp_path / "backup.json", backup)
    archive_link = tmp_path / "backup-link.dump"
    try:
        archive_link.symlink_to(archive.name)
    except OSError:
        pytest.skip("test filesystem does not support symlink fixtures")
    monkeypatch.setattr(
        media_cleanup,
        "connect",
        lambda: pytest.fail("symlink backup must be rejected before DB access"),
    )
    monkeypatch.setattr(media_cleanup, "utc_now", lambda: NOW)
    monkeypatch.setattr(sys, "argv", [
        "cleanup_migrated_media_files.py", "--migration-receipt", str(migration_path),
        "--backup-manifest", str(backup_manifest_path), "--backup", str(archive_link), "--approval-id", "CAB-123",
    ])

    with pytest.raises(RuntimeError, match="non-symlink"):
        media_cleanup.main()
    assert source.exists()


def test_cleanup_rejects_unparseable_custom_archive_before_database_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, migration, backup, _migration_id, archive = _prepared_gate(tmp_path, monkeypatch)
    migration_path = _write_json(tmp_path / "migration.json", migration)
    backup_manifest_path = _write_json(tmp_path / "backup.json", backup)
    commands: list[list[str]] = []

    def reject_archive(command: list[str], **_kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(media_cleanup, "executable", lambda name: name)
    monkeypatch.setattr(media_cleanup.subprocess, "run", reject_archive)
    monkeypatch.setattr(media_cleanup, "_verify_backup_archive", ORIGINAL_VERIFY_BACKUP_ARCHIVE)
    monkeypatch.setattr(
        media_cleanup,
        "connect",
        lambda: pytest.fail("unparseable archive must be rejected before DB access"),
    )
    monkeypatch.setattr(media_cleanup, "utc_now", lambda: NOW)
    monkeypatch.setattr(sys, "argv", [
        "cleanup_migrated_media_files.py", "--migration-receipt", str(migration_path),
        "--backup-manifest", str(backup_manifest_path), "--backup", str(archive), "--approval-id", "CAB-123",
    ])

    with pytest.raises(subprocess.CalledProcessError):
        media_cleanup.main()
    assert commands[0][:2] == ["pg_restore", "--list"]
    assert Path(commands[0][2]).name == "archive.dump"
    assert Path(commands[0][2]) != archive
    assert not Path(commands[0][2]).exists()
    assert source.exists()


def test_cleanup_removes_empty_quarantine_when_rename_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, migration, backup, _migration_id, _archive = _prepared_gate(tmp_path, monkeypatch)
    plan = media_cleanup.build_cleanup_plan(migration_receipt=migration, backup_manifest=backup, now=NOW)

    def fail_source_rename(src, _dst, *args, **kwargs):
        if src == source.name and kwargs.get("src_dir_fd") is not None:
            raise OSError("simulated rename failure")
        raise AssertionError("unexpected rename")

    monkeypatch.setattr(media_cleanup.os, "rename", fail_source_rename)
    monkeypatch.setattr(media_cleanup, "_require_secure_dirfd_support", lambda: None)

    with pytest.raises(OSError, match="simulated rename failure"):
        media_cleanup.execute_cleanup(plan)
    assert source.exists()
    assert not list(tmp_path.glob(".simdashboard-cleanup-quarantine-*"))


def test_cleanup_rejects_nested_symlink_swap_before_quarantine_or_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, migration, backup, _migration_id, _archive = _prepared_gate(
        tmp_path,
        monkeypatch,
        asset_id="media-cleanup-nested-race",
        relative_path="nested/legacy.png",
    )
    plan = media_cleanup.build_cleanup_plan(migration_receipt=migration, backup_manifest=backup, now=NOW)
    original_parent = media_cleanup._open_secure_parent
    nested = tmp_path / "nested"
    moved_nested = tmp_path / "nested-before-swap"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "legacy.png"
    outside_file.write_bytes(b"outside-must-survive")

    def swap_nested_then_open(logical_path: str):
        nested.rename(moved_nested)
        try:
            nested.symlink_to(outside.name, target_is_directory=True)
        except OSError:
            pytest.skip("test filesystem does not support symlink fixtures")
        return original_parent(logical_path)

    monkeypatch.setattr(media_cleanup, "_open_secure_parent", swap_nested_then_open)

    with pytest.raises(RuntimeError, match="secure parent traversal"):
        media_cleanup.execute_cleanup(plan)
    assert (moved_nested / "legacy.png").exists()
    assert outside_file.read_bytes() == b"outside-must-survive"
    assert not list(tmp_path.glob("nested-before-swap/.simdashboard-cleanup-quarantine-*"))


def test_cleanup_parses_and_plans_the_same_private_backup_staging_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, migration, backup, _migration_id, archive = _prepared_gate(tmp_path, monkeypatch)
    original_archive_bytes = archive.read_bytes()
    migration_path = _write_json(tmp_path / "migration.json", migration)
    backup_manifest_path = _write_json(tmp_path / "backup.json", backup)
    parsed_paths: list[Path] = []

    def parse_staged(path: Path) -> None:
        parsed_paths.append(path)
        assert path.read_bytes() == original_archive_bytes
        archive.write_bytes(b"source changed after staging")

    monkeypatch.setattr(media_cleanup, "_verify_backup_archive", parse_staged)
    monkeypatch.setattr(media_cleanup, "utc_now", lambda: NOW)
    monkeypatch.setattr(sys, "argv", [
        "cleanup_migrated_media_files.py", "--migration-receipt", str(migration_path),
        "--backup-manifest", str(backup_manifest_path), "--backup", str(archive), "--approval-id", "CAB-123",
    ])

    assert media_cleanup.main() == 0
    assert source.exists()
    assert archive.read_bytes() == b"source changed after staging"
    assert len(parsed_paths) == 1
    assert parsed_paths[0].name == "archive.dump"
    assert parsed_paths[0] != archive
    assert not parsed_paths[0].exists()


def test_cleanup_dry_run_requires_valid_approval_but_changes_neither_source_nor_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, migration, backup, _, archive = _prepared_gate(tmp_path, monkeypatch)
    migration_path = _write_json(tmp_path / "migration.json", migration)
    backup_path = _write_json(tmp_path / "backup.json", backup)
    cleanup_receipt = tmp_path / "must-not-exist.json"
    monkeypatch.setattr(media_cleanup, "utc_now", lambda: NOW)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cleanup_migrated_media_files.py",
            "--migration-receipt", str(migration_path),
            "--backup-manifest", str(backup_path),
            "--backup", str(archive),
            "--approval-id", "CAB-123",
        ],
    )
    assert media_cleanup.main() == 0
    assert source.exists()
    assert media_cleanup._fingerprint(archive) == (backup["bytes"], backup["sha256"])
    assert not cleanup_receipt.exists()

    monkeypatch.setattr(sys, "argv", [
        "cleanup_migrated_media_files.py", "--migration-receipt", str(migration_path),
        "--backup-manifest", str(backup_path), "--backup", str(archive), "--approval-id", "bad approval",
    ])
    with pytest.raises(RuntimeError, match="approval ID"):
        media_cleanup.main()


def test_cleanup_execute_requires_matching_migration_id_and_non_overwritable_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, migration, backup, migration_id, archive = _prepared_gate(tmp_path, monkeypatch)
    migration_path = _write_json(tmp_path / "migration.json", migration)
    backup_path = _write_json(tmp_path / "backup.json", backup)
    cleanup_receipt = tmp_path / "cleanup.json"
    monkeypatch.setattr(media_cleanup, "utc_now", lambda: NOW)
    monkeypatch.setattr(sys, "argv", [
        "cleanup_migrated_media_files.py", "--migration-receipt", str(migration_path),
        "--backup-manifest", str(backup_path), "--backup", str(archive), "--approval-id", "CAB-123", "--execute", "--confirm",
        "--migration-id", "other-migration", "--cleanup-receipt", str(cleanup_receipt),
    ])
    with pytest.raises(RuntimeError, match="migration-id"):
        media_cleanup.main()
    assert source.exists()
    assert not cleanup_receipt.exists()

    cleanup_receipt.write_text("reserved", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "cleanup_migrated_media_files.py", "--migration-receipt", str(migration_path),
        "--backup-manifest", str(backup_path), "--backup", str(archive), "--approval-id", "CAB-123", "--execute", "--confirm",
        "--migration-id", migration_id, "--cleanup-receipt", str(cleanup_receipt),
    ])
    with pytest.raises(FileExistsError, match="덮어쓸 수 없습니다"):
        media_cleanup.main()
    assert source.exists()


def test_cleanup_execute_rejects_an_empty_media_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, migration, backup, migration_id, archive = _prepared_gate(tmp_path, monkeypatch)
    migration["media"] = []
    migration_path = _write_json(tmp_path / "migration-empty.json", migration)
    backup_path = _write_json(tmp_path / "backup.json", backup)
    cleanup_receipt = tmp_path / "cleanup-empty.json"
    monkeypatch.setattr(media_cleanup, "utc_now", lambda: NOW)
    monkeypatch.setattr(sys, "argv", [
        "cleanup_migrated_media_files.py", "--migration-receipt", str(migration_path),
        "--backup-manifest", str(backup_path), "--backup", str(archive), "--approval-id", "CAB-123", "--execute", "--confirm",
        "--migration-id", migration_id, "--cleanup-receipt", str(cleanup_receipt),
    ])
    with pytest.raises(RuntimeError, match="삭제할 legacy media"):
        media_cleanup.main()
    assert source.exists()
    assert not cleanup_receipt.exists()


def test_cleanup_execute_deletes_only_validated_legacy_source_and_writes_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, migration, backup, migration_id, archive = _prepared_gate(tmp_path, monkeypatch)
    migration_path = _write_json(tmp_path / "migration.json", migration)
    backup_path = _write_json(tmp_path / "backup.json", backup)
    cleanup_receipt = tmp_path / "cleanup.json"
    monkeypatch.setattr(media_cleanup, "utc_now", lambda: NOW)
    monkeypatch.setattr(sys, "argv", [
        "cleanup_migrated_media_files.py", "--migration-receipt", str(migration_path),
        "--backup-manifest", str(backup_path), "--backup", str(archive), "--approval-id", "CAB-123", "--execute", "--confirm",
        "--migration-id", migration_id, "--cleanup-receipt", str(cleanup_receipt),
    ])

    assert media_cleanup.main() == 0
    assert not source.exists()
    receipt = json.loads(cleanup_receipt.read_text(encoding="utf-8"))
    assert receipt["format"] == "simdashboard-media-cleanup-receipt"
    assert receipt["format_version"] == 1
    assert receipt["state"] == "COMPLETED"
    assert receipt["migration_id"] == migration_id
    assert receipt["approval_id"] == "CAB-123"
    assert receipt["deleted"] == [{
        "legacy_logical_path": "legacy.png",
        "asset_ids": ["media-cleanup-asset"],
        "source_size": len(_png_bytes(b"media-cleanup-asset")),
        "source_sha256": migration["media"][0]["source_sha256"],
    }]
