from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts import postgres_replacement as replacement
from scripts import postgres_transfer as transfer
from scripts.postgres_transfer import _normalized_asset_path, safe_relative_path, validate_bundle


def _strict_inventory() -> dict[str, object]:
    return {
        "format": "analysis-canvas-media-inventory",
        "format_version": 1,
        "blob_count": 0,
        "chunk_count": 0,
        "declared_chunk_count": 0,
        "total_blob_bytes": 0,
        "media_reference_count": 0,
        "drop_video_reference_count": 0,
        "reference_count": 0,
        "unbound_media_asset_count": 0,
        "missing_media_blob_count": 0,
        "missing_drop_video_blob_count": 0,
        "missing_reference_count": 0,
        "orphan_blob_count": 0,
        "orphan_chunk_count": 0,
        "corrupt_blob_count": 0,
        "corrupt_blob_ids": [],
        "content_integrity_verified": True,
        "demo_expected_count": 20,
        "demo_contract_active": True,
        "demo_exact": True,
        "demo_count": 20,
        "missing_demo_ids": [],
        "unexpected_demo_ids": [],
        "catalog_sha256": "a" * 64,
    }


class _BackupMaintenance:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, _parameters=None):
        rendered = str(statement)
        if "datconnlimit" in rendered:
            return SimpleNamespace(fetchone=lambda: (-1,))
        if "pg_stat_activity" in rendered:
            return SimpleNamespace(fetchone=lambda: (0,))
        return SimpleNamespace()


class _SwapAdmin:
    def __init__(self, *, active: int = 0, fail_promote: bool = False):
        self.active = active
        self.fail_promote = fail_promote
        self.commands: list[str] = []
        self.rename_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, parameters=None):
        rendered = str(statement)
        self.commands.append(rendered)
        if isinstance(statement, str) and "oid::text" in statement:
            database = parameters[0]
            return SimpleNamespace(fetchone=lambda: (("20",) if "stage" in database else ("10",)))
        if isinstance(statement, str) and "pg_stat_activity" in statement:
            return SimpleNamespace(fetchone=lambda: (self.active,))
        if "RENAME TO" in rendered:
            self.rename_count += 1
            if self.fail_promote and self.rename_count == 2:
                raise RuntimeError("promote failed")
        return SimpleNamespace()


@pytest.mark.parametrize("value", ["", "../secret", "/absolute", "C:/secret", "assets/../../secret"])
def test_safe_relative_path_rejects_unsafe_values(value: str):
    with pytest.raises(RuntimeError):
        safe_relative_path(value)


def test_safe_relative_path_normalizes_windows_separators():
    assert safe_relative_path("imports\\run-1\\image.svg").as_posix() == "imports/run-1/image.svg"


def test_normalized_asset_path_accepts_case_insensitive_assets_prefix():
    assert _normalized_asset_path("AsSeTs\\imports\\run-1\\image.svg") == "imports/run-1/image.svg"


def test_validate_bundle_rejects_asset_checksum_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    dump = bundle / "database.dump"
    dump.write_bytes(b"dump")
    archive = bundle / "assets.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("sample.svg", b"actual")
    manifest = {
        "format": "analysis-canvas-postgresql-transfer",
        "format_version": 2,
        "bundle_id": str(uuid4()),
        "database": "simulation_dashboard",
        "alembic_revision": "head",
        "table_counts": {},
        "media_inventory": _strict_inventory(),
        "database_dump": {"file": dump.name, "bytes": dump.stat().st_size, "sha256": hashlib.sha256(b"dump").hexdigest()},
        "assets_archive": {"file": archive.name, "bytes": archive.stat().st_size, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
        "assets": [{"path": "sample.svg", "bytes": 6, "sha256": hashlib.sha256(b"wrong!").hexdigest()}],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("scripts.postgres_transfer.expected_alembic_head", lambda: "head")
    with pytest.raises(RuntimeError, match="Asset checksum failed"):
        validate_bundle(bundle)


def test_validate_bundle_rejects_noncanonical_bundle_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest = {
        "format": "analysis-canvas-postgresql-transfer",
        "format_version": 2,
        "bundle_id": "../escape",
        "database": "simulation_dashboard",
        "alembic_revision": "head",
        "media_inventory": _strict_inventory(),
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("scripts.postgres_transfer.expected_alembic_head", lambda: "head")
    with pytest.raises(RuntimeError, match="bundle_id"):
        validate_bundle(bundle)


def test_validate_bundle_rejects_legacy_format_before_reading_payloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        json.dumps({
            "format": "analysis-canvas-postgresql-transfer",
            "format_version": 1,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.postgres_transfer.expected_alembic_head", lambda: "head")

    with pytest.raises(RuntimeError, match="version 1 is not supported"):
        validate_bundle(bundle)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda inventory: inventory.pop("catalog_sha256"),
        lambda inventory: inventory.__setitem__("reference_count", True),
    ],
)
def test_validate_bundle_rejects_incomplete_or_tampered_media_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate
):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    inventory = _strict_inventory()
    mutate(inventory)
    (bundle / "manifest.json").write_text(
        json.dumps({
            "format": "analysis-canvas-postgresql-transfer",
            "format_version": 2,
            "bundle_id": str(uuid4()),
            "database": "simulation_dashboard",
            "alembic_revision": "head",
            "media_inventory": inventory,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.postgres_transfer.expected_alembic_head", lambda: "head")

    with pytest.raises(RuntimeError, match="media inventory is not strict"):
        validate_bundle(bundle)


def test_transfer_assets_excludes_bound_media_but_keeps_shared_legacy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ("bound.png", "shared.png", "legacy.png", "template.svg"):
        (assets / name).write_bytes(name.encode("ascii"))
    monkeypatch.setattr(transfer, "ASSETS", assets)
    snapshot = {
        "managed_asset_paths": ["shared.png", "legacy.png", "template.svg"],
        "bound_media_asset_paths": ["bound.png", "shared.png"],
    }

    exported = transfer.transfer_assets(snapshot)

    assert [item["path"] for item in exported] == ["legacy.png", "shared.png", "template.svg"]


class _SnapshotHolder:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _AfterSnapshotConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_export_records_inventory_from_same_pg_dump_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    before = {
        "alembic_revision": "head",
        "server_version": 180000,
        "table_counts": {"asset_blobs": 1},
        "managed_asset_paths": [],
        "bound_media_asset_paths": [],
    }
    inventory = _strict_inventory()
    holder = _SnapshotHolder()
    monkeypatch.setattr(transfer, "require_stopped", lambda: None)
    monkeypatch.setattr(transfer, "dotenv_values", lambda _path: {"DATABASE_URL": "postgresql://app:pw@db/test"})
    monkeypatch.setattr(transfer, "_snapshot_export_state", lambda _url: (holder, "snapshot-123", before, inventory))
    monkeypatch.setattr(transfer, "expected_alembic_head", lambda: "head")
    monkeypatch.setattr(transfer, "transfer_assets", lambda _snapshot: [])
    monkeypatch.setattr(transfer, "find_pg_tool", lambda name: name)
    monkeypatch.setattr(transfer, "database_snapshot", lambda _url: before)
    monkeypatch.setattr(transfer.psycopg, "connect", lambda _url: _AfterSnapshotConnection())
    monkeypatch.setattr(transfer, "media_inventory", lambda _connection: inventory)
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        commands.append(command)
        if command[0] == "pg_dump":
            Path(command[command.index("--file") + 1]).write_bytes(b"dump")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(transfer.subprocess, "run", fake_run)

    bundle = transfer.export_bundle(tmp_path)

    assert "--snapshot=snapshot-123" in commands[0]
    assert holder.closed is True
    assert json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["media_inventory"] == inventory


def test_restored_inventory_uses_app_url_and_rejects_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(transfer.psycopg, "connect", lambda url: seen.append(url) or Connection())
    monkeypatch.setattr(transfer, "media_inventory", lambda _connection: {**_strict_inventory(), "catalog_sha256": "b" * 64})

    with pytest.raises(RuntimeError, match="does not match"):
        transfer.verify_restored_media_inventory(
            "postgresql+psycopg://simdashboard_app:pw@db/test",
            {**_strict_inventory(), "catalog_sha256": "c" * 64},
        )

    assert seen == ["postgresql://simdashboard_app:pw@db/test"]


def test_transfer_database_only_verifier_receives_app_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        transfer.subprocess,
        "run",
        lambda command, **kwargs: captured.update({"command": command, **kwargs}) or SimpleNamespace(returncode=0),
    )

    transfer.run_database_only_verifier("postgresql+psycopg://simdashboard_app:pw@db/test")

    assert captured["env"]["DATABASE_URL"] == "postgresql+psycopg://simdashboard_app:pw@db/test"
    assert captured["env"]["ANALYSIS_DB_BACKEND"] == "postgresql"


def test_recovery_marker_blocks_mutation_but_not_validate_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    marker = tmp_path / ".setup-recovery-required.json"
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(transfer, "RECOVERY_MARKER", marker)
    monkeypatch.setattr(transfer, "require_stopped", lambda: None)
    monkeypatch.setattr(transfer, "validate_bundle", lambda _bundle: {})
    transfer.import_bundle(tmp_path, True)
    with pytest.raises(RuntimeError, match="manual recovery"):
        transfer.import_bundle(tmp_path, False)


def test_replacement_database_names_are_unique_and_postgres_safe():
    first = replacement.database_name("simulation_dashboard_stage")
    second = replacement.database_name("simulation_dashboard_stage")
    assert first != second
    assert first.startswith("simulation_dashboard_stage_")
    assert len(first.encode("ascii")) <= 63


def test_swap_rechecks_database_identity_before_any_ddl(monkeypatch: pytest.MonkeyPatch):
    admin = _SwapAdmin()
    monkeypatch.setattr(replacement.psycopg, "connect", lambda *_args, **_kwargs: admin)
    with pytest.raises(RuntimeError, match="staging database identity changed"):
        replacement.swap_databases(
            "postgresql://postgres:secret@127.0.0.1:5432/postgres",
            "simulation_dashboard_stage_test",
            expected_staging_oid="999",
            expected_target_oid="10",
        )
    assert not any("ALTER DATABASE" in command for command in admin.commands)


def test_swap_stops_on_active_connections_without_forcing_them(monkeypatch: pytest.MonkeyPatch):
    admin = _SwapAdmin(active=1)
    monkeypatch.setattr(replacement.psycopg, "connect", lambda *_args, **_kwargs: admin)
    with pytest.raises(RuntimeError, match="active connections"):
        replacement.swap_databases(
            "postgresql://postgres:secret@127.0.0.1:5432/postgres",
            "simulation_dashboard_stage_test",
            expected_staging_oid="20",
            expected_target_oid="10",
        )
    assert not any("pg_terminate_backend" in command for command in admin.commands)


def test_failed_stage_promotion_attempts_to_reenable_and_restore_names(monkeypatch: pytest.MonkeyPatch):
    admin = _SwapAdmin(fail_promote=True)
    monkeypatch.setattr(replacement.psycopg, "connect", lambda *_args, **_kwargs: admin)
    with pytest.raises(RuntimeError, match="promote failed"):
        replacement.swap_databases(
            "postgresql://postgres:secret@127.0.0.1:5432/postgres",
            "simulation_dashboard_stage_test",
            expected_staging_oid="20",
            expected_target_oid="10",
        )
    assert admin.rename_count >= 3
    assert any("ALLOW_CONNECTIONS true" in command for command in admin.commands)


def test_replacement_backup_verifies_database_and_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "sample.svg").write_text("safe", encoding="utf-8")

    def fake_run(command, **_kwargs):
        if "--file" in command:
            Path(command[command.index("--file") + 1]).write_bytes(b"verified-dump")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(replacement.subprocess, "run", fake_run)
    monkeypatch.setattr(replacement.psycopg, "connect", lambda *_args, **_kwargs: _BackupMaintenance())
    monkeypatch.setattr(replacement, "_table_counts", lambda _url: {"projects": 2})
    monkeypatch.setattr(replacement, "_verify_dump_restore", lambda *_args, **_kwargs: None)
    backup = replacement.create_replacement_backup(
        "postgresql://postgres:secret@127.0.0.1:5432/postgres",
        assets,
        tmp_path / "backups",
        lambda name: name,
    )
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "analysis-canvas-pre-replacement"
    assert manifest["asset_count"] == 1
    assert manifest["database_dump"]["sha256"] == hashlib.sha256(b"verified-dump").hexdigest()
    with zipfile.ZipFile(backup / "assets.zip") as archive:
        assert archive.read("sample.svg") == b"safe"


def test_replacement_backup_failure_leaves_incomplete_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(replacement, "_table_counts", lambda _url: {})
    monkeypatch.setattr(replacement.psycopg, "connect", lambda *_args, **_kwargs: _BackupMaintenance())
    monkeypatch.setattr(
        replacement.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "pg_dump")),
    )
    with pytest.raises(subprocess.CalledProcessError):
        replacement.create_replacement_backup(
            "postgresql://postgres:secret@127.0.0.1:5432/postgres",
            tmp_path / "assets",
            tmp_path / "backups",
            lambda name: name,
        )
    created = list((tmp_path / "backups").iterdir())
    assert len(created) == 1
    assert (created[0] / "BACKUP_INCOMPLETE").is_file()


def test_asset_activation_restores_previous_directory_when_stage_move_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    backend = tmp_path / "backend"
    current = backend / "assets"
    staged = backend / "staged"
    current.mkdir(parents=True)
    staged.mkdir()
    (current / "old.txt").write_text("old", encoding="utf-8")
    (staged / "new.txt").write_text("new", encoding="utf-8")
    monkeypatch.setattr(transfer, "BACKEND", backend)
    monkeypatch.setattr(transfer, "ASSETS", current)
    real_replace = transfer.os.replace
    calls = 0

    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("activation failed")
        return real_replace(source, destination)

    monkeypatch.setattr(transfer.os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="activation failed"):
        transfer._activate_staged_assets(staged)
    assert (current / "old.txt").read_text(encoding="utf-8") == "old"
    assert (staged / "new.txt").read_text(encoding="utf-8") == "new"


class _ImportAdmin:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, _parameters=None):
        rendered = str(statement)
        if "rolcreatedb" in rendered:
            return SimpleNamespace(fetchone=lambda: (True, True, True))
        if "FROM pg_database" in rendered:
            return SimpleNamespace(fetchone=lambda: (1,))
        if "FROM pg_roles" in rendered:
            return SimpleNamespace(fetchall=lambda: [])
        raise AssertionError(rendered)


def test_import_never_bootstraps_when_pre_replacement_backup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(transfer, "require_stopped", lambda: None)
    monkeypatch.setattr(
        transfer,
        "validate_bundle",
        lambda _bundle: {"database_dump": {"file": "database.dump"}, "table_counts": {}, "assets": []},
    )
    monkeypatch.setattr(transfer, "dotenv_values", lambda _path: {})
    monkeypatch.setenv("POSTGRES_ADMIN_URL", "postgresql://postgres:secret@127.0.0.1:5432/postgres")
    monkeypatch.setattr(transfer.psycopg, "connect", lambda *_args, **_kwargs: _ImportAdmin())
    monkeypatch.setattr(transfer, "create_replacement_backup", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("backup failed")))
    monkeypatch.setattr(
        transfer.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("bootstrap must not run after backup failure"),
    )
    with pytest.raises(RuntimeError, match="backup failed"):
        transfer.import_bundle(tmp_path, False, replace_existing=True, backup_dir=tmp_path / "backups")
