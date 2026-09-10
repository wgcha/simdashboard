from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import backup_postgres as backup
from scripts import restore_postgres as restore


pytestmark = pytest.mark.unit


def _inventory() -> dict[str, object]:
    return {
        "format": "analysis-canvas-media-inventory",
        "format_version": 1,
        "blob_count": 1,
        "chunk_count": 1,
        "declared_chunk_count": 1,
        "total_blob_bytes": 3,
        "media_reference_count": 1,
        "drop_video_reference_count": 20,
        "reference_count": 21,
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
        "demo_count": 20,
        "missing_demo_ids": [],
        "unexpected_demo_ids": [],
        "demo_exact": True,
        "catalog_sha256": "a" * 64,
    }


def _account_inventory() -> dict[str, object]:
    return {"format": "analysis-canvas-account-inventory", "format_version": 1,
            "users": {"count": 2, "pending_count": 0, "active_count": 1, "suspended_count": 1, "global_admin_count": 1, "identity_authorization_sha256": "b" * 64},
            "project_memberships": {"count": 1, "authorization_sha256": "c" * 64}}


def _manifest(dump: Path, *, inventory: object | None = None, **overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "format": "postgresql-custom",
        "filename": dump.name,
        "bytes": dump.stat().st_size,
        "sha256": restore.sha256(dump),
        "database": "source_database",
        "media_inventory": _inventory() if inventory is None else inventory,
    }
    manifest.update(overrides)
    return manifest


def _restore_argv(dump: Path) -> list[str]:
    return [
        "restore_postgres.py",
        str(dump),
        "--database-url",
        "postgresql+psycopg://owner:pw@db/test",
        "--verify-database-url",
        "postgresql+psycopg://app:pw@db/test",
        "--confirm-database",
        "test",
    ]


def _backup_argv(output_dir: Path, *, label: str = "audit") -> list[str]:
    return [
        "backup_postgres.py",
        "--database-url",
        "postgresql+psycopg://app:pw@db/test",
        "--output-dir",
        str(output_dir),
        "--label",
        label,
    ]


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, _timezone=None):
        return datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


class _SnapshotConnection:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.closed = False

    def execute(self, statement: str):
        self.commands.append(statement)
        if "pg_export_snapshot" in statement:
            return SimpleNamespace(fetchone=lambda: ("snapshot-123",))
        return SimpleNamespace(fetchone=lambda: None)

    def close(self) -> None:
        self.closed = True


def test_backup_snapshot_inventory_holds_repeatable_read_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _SnapshotConnection()
    monkeypatch.setattr(backup.psycopg, "connect", lambda _url: connection)
    monkeypatch.setattr(backup, "media_inventory", lambda actual: _inventory() if actual is connection else {})
    monkeypatch.setattr(backup, "require_media_integrity", lambda report: None)

    monkeypatch.setattr(backup, "account_inventory", lambda actual: _account_inventory() if actual is connection else {})
    actual, snapshot, inventory, accounts = backup._snapshot_inventory("postgresql+psycopg://app:pw@db/test")

    assert actual is connection
    assert snapshot == "snapshot-123"
    assert inventory == _inventory()
    assert accounts == _account_inventory()
    assert connection.commands == [
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        "SELECT pg_export_snapshot()",
    ]
    assert connection.closed is False


def test_backup_manifest_embeds_inventory_and_pg_dump_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    inventory = _inventory()
    holder = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(backup, "_snapshot_inventory", lambda _url: (holder, "snapshot-123", inventory, _account_inventory()))
    monkeypatch.setattr(backup, "executable", lambda name: name)
    monkeypatch.setenv("LC_ALL", "ko_KR")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        assert _kwargs["env"]["LC_ALL"] == "C"
        assert _kwargs["env"]["LC_MESSAGES"] == "C"
        assert _kwargs["env"]["LANGUAGE"] == "C"
        commands.append(command)
        if command[0] == "pg_dump":
            Path(command[command.index("--file") + 1]).write_bytes(b"dump")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", _backup_argv(tmp_path))

    backup.main()

    assert "--snapshot=snapshot-123" in commands[0]
    manifest = next(tmp_path.glob("*.manifest.json"))
    assert json.loads(manifest.read_text(encoding="utf-8"))["media_inventory"] == inventory
    assert json.loads(manifest.read_text(encoding="utf-8"))["account_inventory"] == _account_inventory()
    assert backup.os.environ["LC_ALL"] == "ko_KR"


def test_deployment_bundle_uses_same_snapshot_and_distinct_restore_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from scripts import deployment_media_backup
    holder = SimpleNamespace(closed=False)
    holder.close = lambda: setattr(holder, "closed", True)
    def snapshot(_url, *, deployment=False):
        assert deployment is True
        return holder, "deployment-snapshot", _inventory(), _account_inventory()
    monkeypatch.setattr(backup, "_snapshot_inventory", snapshot)
    monkeypatch.setattr(backup, "executable", lambda name: name)
    def bundle(connection, inventory, assets_root, archive_path):
        assert connection is holder and not holder.closed
        assert inventory == _inventory()
        archive_path.write_bytes(b"archived assets")
        return {"filename": archive_path.name, "bytes": archive_path.stat().st_size, "sha256": backup.sha256(archive_path)}
    monkeypatch.setattr(deployment_media_backup, "create_deployment_media_bundle", bundle)
    def run(command, **_kwargs):
        if command[0] == "pg_dump":
            assert not holder.closed
            assert "--snapshot=deployment-snapshot" in command
            Path(command[command.index("--file") + 1]).write_bytes(b"snapshot dump")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(backup.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", _backup_argv(tmp_path) + ["--deployment-assets-root", str(tmp_path / "assets")])
    backup.main()
    dump = next(tmp_path.glob("*.dump"))
    manifest = json.loads(dump.with_suffix(".manifest.json").read_text())
    assert holder.closed
    assert manifest["format"] == "analysis-canvas-deployment-postgresql"
    assert manifest["recovery_contract"] == "database-and-assets-before-migration"
    assert manifest["assets_backup"]["sha256"] == backup.sha256(next(tmp_path.glob("*.assets.zip")))
    # A dual-read bundle must never masquerade as a strict DB-only archive.
    with pytest.raises(RuntimeError, match="manifest 형식"):
        restore._read_verified_manifest(dump)


def test_deployment_file_failure_does_not_publish_dump_or_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from scripts import deployment_media_backup
    holder = SimpleNamespace(closed=False)
    holder.close = lambda: setattr(holder, "closed", True)
    monkeypatch.setattr(backup, "_snapshot_inventory", lambda _url, **_kwargs: (holder, "snapshot", _inventory(), _account_inventory()))
    monkeypatch.setattr(backup, "executable", lambda name: name)
    def fail(*_args):
        raise RuntimeError("synthetic missing source")
    monkeypatch.setattr(deployment_media_backup, "create_deployment_media_bundle", fail)
    monkeypatch.setattr(backup.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("must stop before dump"))
    monkeypatch.setattr(sys, "argv", _backup_argv(tmp_path) + ["--deployment-assets-root", str(tmp_path / "assets")])
    with pytest.raises(RuntimeError, match="synthetic missing source"):
        backup.main()
    assert holder.closed
    assert not list(tmp_path.glob("*.dump"))
    assert not list(tmp_path.glob("*.manifest.json"))


def test_tools_only_check_never_opens_database_or_creates_backup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(backup, "executable", lambda name: name)
    monkeypatch.setattr(backup.psycopg, "connect", lambda *_args, **_kwargs: pytest.fail("tools check must not connect to DB"))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 15
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(backup.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["backup_postgres.py", "--check-tools", "--output-dir", str(tmp_path / "no-backup")])
    backup.main()
    assert calls == [["pg_dump", "--version"], ["pg_restore", "--version"]]
    assert not (tmp_path / "no-backup").exists()


def test_windows_launch_write_protection_has_exact_stage(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.backup_failure_details import child_failure_details
    monkeypatch.setattr(backup, "executable", lambda name: name)
    def fail(*_args, **_kwargs):
        error = PermissionError(13, "synthetic write protection")
        error.winerror = 19
        raise error
    monkeypatch.setattr(backup.subprocess, "run", fail)
    monkeypatch.setattr(sys, "argv", ["backup_postgres.py", "--check-tools"])
    with pytest.raises(PermissionError):
        backup.main()
    assert child_failure_details(capsys.readouterr().err) == {
        "stage": "pg_dump_version", "exception_type": "PermissionError", "errno": 13, "winerror": 19,
    }


@pytest.mark.parametrize("label", ["../escape", "nested/archive", "white space", ""])
def test_backup_rejects_pathlike_or_unsafe_label_before_snapshot_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, label: str
) -> None:
    monkeypatch.setattr(
        backup,
        "_snapshot_inventory",
        lambda _url: pytest.fail("unsafe label must be rejected before database access"),
    )
    monkeypatch.setattr(sys, "argv", _backup_argv(tmp_path, label=label))

    with pytest.raises(RuntimeError, match="backup label"):
        backup.main()


def test_backup_refuses_existing_or_symlink_final_target_before_snapshot_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(backup, "datetime", _FixedDateTime)
    final_path = tmp_path / "audit-20260825T120000Z.dump"
    final_path.write_bytes(b"do-not-overwrite")
    monkeypatch.setattr(
        backup,
        "_snapshot_inventory",
        lambda _url: pytest.fail("existing final must be rejected before database access"),
    )
    monkeypatch.setattr(sys, "argv", _backup_argv(tmp_path))

    with pytest.raises(FileExistsError, match="덮어쓸 수 없습니다"):
        backup.main()
    assert final_path.read_bytes() == b"do-not-overwrite"

    final_path.unlink()
    target = tmp_path / "target.dump"
    target.write_bytes(b"symlink-target")
    try:
        final_path.symlink_to(target.name)
    except OSError:
        pytest.skip("test filesystem does not support symlink fixtures")
    with pytest.raises(FileExistsError, match="덮어쓸 수 없습니다"):
        backup.main()
    assert target.read_bytes() == b"symlink-target"

    final_path.unlink()
    manifest_path = tmp_path / "audit-20260825T120000Z.manifest.json"
    manifest_path.write_text("existing manifest", encoding="utf-8")
    with pytest.raises(FileExistsError, match="덮어쓸 수 없습니다"):
        backup.main()
    assert manifest_path.read_text(encoding="utf-8") == "existing manifest"


def test_backup_rejects_symlink_output_directory_before_snapshot_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_target = tmp_path / "actual-output"
    output_target.mkdir()
    symlink_output = tmp_path / "linked-output"
    try:
        symlink_output.symlink_to(output_target.name, target_is_directory=True)
    except OSError:
        pytest.skip("test filesystem does not support symlink fixtures")
    monkeypatch.setattr(
        backup,
        "_snapshot_inventory",
        lambda _url: pytest.fail("symlink output must be rejected before database access"),
    )
    monkeypatch.setattr(sys, "argv", _backup_argv(symlink_output))

    with pytest.raises(RuntimeError, match="symlink"):
        backup.main()


def test_backup_failure_retains_unique_partial_without_final_or_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    holder = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(backup, "datetime", _FixedDateTime)
    monkeypatch.setattr(backup, "_snapshot_inventory", lambda _url: (holder, "snapshot-123", _inventory(), _account_inventory()))
    monkeypatch.setattr(backup, "executable", lambda name: name)

    def fail_after_partial(command: list[str], **_kwargs):
        assert command[0] == "pg_dump"
        Path(command[command.index("--file") + 1]).write_bytes(b"incomplete")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(backup.subprocess, "run", fail_after_partial)
    monkeypatch.setattr(sys, "argv", _backup_argv(tmp_path))

    with pytest.raises(subprocess.CalledProcessError):
        backup.main()

    assert not (tmp_path / "audit-20260825T120000Z.dump").exists()
    assert not (tmp_path / "audit-20260825T120000Z.manifest.json").exists()
    partials = list(tmp_path.glob(".audit-20260825T120000Z.dump.*.partial"))
    assert len(partials) == 1
    assert partials[0].read_bytes() == b"incomplete"


class _RestoreConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _statement: str):
        return SimpleNamespace(fetchone=lambda: (0,))


def test_restore_requires_matching_inventory_and_database_only_verifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dump = tmp_path / "restore.dump"
    dump.write_bytes(b"dump")
    inventory = _inventory()
    dump.with_suffix(".manifest.json").write_text(
        json.dumps(_manifest(dump, inventory=inventory)), encoding="utf-8"
    )
    monkeypatch.setattr(restore.psycopg, "connect", lambda _url: _RestoreConnection())
    monkeypatch.setattr(restore, "executable", lambda name: name)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        restore.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )
    verified: dict[str, object] = {}
    calls: list[str] = []
    monkeypatch.setattr(
        restore,
        "_harden_audit_event_privileges",
        lambda owner_database_url: calls.append(f"harden:{owner_database_url}"),
    )
    monkeypatch.setattr(
        restore,
        "_verify_restored_media",
        lambda *, expected, verify_database_url: calls.append("inventory") or verified.update(
            {"expected": expected, "verify_database_url": verify_database_url}
        ) or inventory,
    )
    monkeypatch.setattr(
        restore,
        "_run_database_only_verifier",
        lambda verify_database_url: calls.append("verifier") or verified.update({"runtime_gate": verify_database_url}),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            *_restore_argv(dump),
        ],
    )

    restore.main()

    assert verified == {
        "expected": inventory,
        "verify_database_url": "postgresql+psycopg://app:pw@db/test",
        "runtime_gate": "postgresql+psycopg://app:pw@db/test",
    }
    assert calls == ["harden:postgresql+psycopg://owner:pw@db/test", "inventory", "verifier"]
    restore_command = next(command for command in commands if "--single-transaction" in command)
    assert "--exit-on-error" in restore_command
    assert "--create" not in restore_command
    archive_list_command = next(command for command in commands if command[1:2] == ["--list"])
    assert archive_list_command[2] != str(dump)
    assert restore_command[-1] == archive_list_command[2]
    assert not Path(archive_list_command[2]).exists()


def test_restore_verifies_account_inventory_when_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dump = tmp_path / "accounts.dump"
    dump.write_bytes(b"dump")
    expected_accounts = _account_inventory()
    dump.with_suffix(".manifest.json").write_text(
        json.dumps(_manifest(dump, account_inventory=expected_accounts)), encoding="utf-8"
    )
    monkeypatch.setattr(restore.psycopg, "connect", lambda _url: _RestoreConnection())
    monkeypatch.setattr(restore, "executable", lambda name: name)
    monkeypatch.setattr(restore.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(restore, "_harden_audit_event_privileges", lambda _url: None)
    monkeypatch.setattr(restore, "_verify_restored_media", lambda **_kwargs: _inventory())
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        restore, "_verify_restored_accounts",
        lambda *, expected, verify_database_url: observed.update({"expected": expected, "url": verify_database_url}) or expected,
    )
    monkeypatch.setattr(restore, "_run_database_only_verifier", lambda _url: None)
    monkeypatch.setattr(sys, "argv", _restore_argv(dump))

    restore.main()

    assert observed == {"expected": expected_accounts, "url": "postgresql+psycopg://app:pw@db/test"}


def test_restore_clean_uses_single_transaction_and_failure_stops_post_restore_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dump = tmp_path / "restore.dump"
    dump.write_bytes(b"dump")
    dump.with_suffix(".manifest.json").write_text(json.dumps(_manifest(dump)), encoding="utf-8")
    commands: list[list[str]] = []

    def fail_restore(command: list[str], **_kwargs):
        commands.append(command)
        if "--single-transaction" in command:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(restore, "executable", lambda name: name)
    monkeypatch.setattr(restore.subprocess, "run", fail_restore)
    monkeypatch.setattr(restore.psycopg, "connect", lambda _url: _RestoreConnection())
    monkeypatch.setattr(restore, "_harden_audit_event_privileges", lambda _url: pytest.fail("must not harden"))
    monkeypatch.setattr(restore, "_verify_restored_media", lambda **_kwargs: pytest.fail("must not verify"))
    monkeypatch.setattr(restore, "_run_database_only_verifier", lambda _url: pytest.fail("must not run verifier"))
    monkeypatch.setattr(sys, "argv", [*_restore_argv(dump), "--clean"])

    with pytest.raises(subprocess.CalledProcessError):
        restore.main()

    restore_command = next(command for command in commands if "--single-transaction" in command)
    assert "--clean" in restore_command
    assert "--if-exists" in restore_command
    assert "--exit-on-error" in restore_command
    assert "--create" not in restore_command


def test_restore_hardening_uses_the_owner_url_and_explicit_app_role(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setenv("SIM_DASH_APP_ROLE", "restore_app")

    def fake_run(command, **kwargs):
        observed.update({"command": command, **kwargs})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(restore.subprocess, "run", fake_run)

    restore._harden_audit_event_privileges("postgresql+psycopg://owner:pw@db/test")

    assert observed["command"] == [
        sys.executable,
        str(restore.BACKEND / "scripts" / "harden_postgres_privileges.py"),
    ]
    assert observed["cwd"] == restore.BACKEND
    assert observed["check"] is True
    assert observed["env"]["DATABASE_URL"] == "postgresql+psycopg://owner:pw@db/test"
    assert observed["env"]["SIM_DASH_APP_ROLE"] == "restore_app"


def test_restore_rejects_a_backup_without_media_inventory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dump = tmp_path / "restore.dump"
    dump.write_bytes(b"dump")
    manifest = _manifest(dump)
    manifest.pop("media_inventory")
    dump.with_suffix(".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            *_restore_argv(dump),
        ],
    )

    with pytest.raises(RuntimeError, match="media_inventory"):
        restore.main()


def test_restore_rejects_same_database_name_on_a_different_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dump = tmp_path / "restore.dump"
    dump.write_bytes(b"dump")
    dump.with_suffix(".manifest.json").write_text(
        json.dumps(_manifest(dump)), encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "restore_postgres.py",
            str(dump),
            "--database-url",
            "postgresql://owner:pw@restore-host/test",
            "--verify-database-url",
            "postgresql://app:pw@different-host/test",
            "--confirm-database",
            "test",
        ],
    )

    with pytest.raises(RuntimeError, match="host, port"):
        restore.main()


@pytest.mark.parametrize(
    "overrides",
    [
        {"format": "wrong-format"},
        {"bytes": 999},
        {"media_inventory": {"format": "analysis-canvas-media-inventory"}},
    ],
)
def test_restore_rejects_invalid_manifest_before_target_access_or_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, overrides: dict[str, object]
) -> None:
    dump = tmp_path / "restore.dump"
    dump.write_bytes(b"dump")
    dump.with_suffix(".manifest.json").write_text(json.dumps(_manifest(dump, **overrides)), encoding="utf-8")
    commands: list[list[str]] = []
    monkeypatch.setattr(restore.subprocess, "run", lambda command, **_kwargs: commands.append(command))
    monkeypatch.setattr(
        restore.psycopg,
        "connect",
        lambda _url: (_ for _ in ()).throw(AssertionError("target must not be queried")),
    )
    monkeypatch.setattr(restore, "_harden_audit_event_privileges", lambda _url: pytest.fail("must not harden"))
    monkeypatch.setattr(restore, "_run_database_only_verifier", lambda _url: pytest.fail("must not verify"))
    monkeypatch.setattr(sys, "argv", _restore_argv(dump))

    with pytest.raises(RuntimeError):
        restore.main()

    assert commands == []


def test_restore_rejects_invalid_archive_before_target_access_or_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dump = tmp_path / "restore.dump"
    dump.write_bytes(b"dump")
    dump.with_suffix(".manifest.json").write_text(json.dumps(_manifest(dump)), encoding="utf-8")
    commands: list[list[str]] = []

    def fail_archive_list(command, **_kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(restore, "executable", lambda name: name)
    monkeypatch.setattr(restore.subprocess, "run", fail_archive_list)
    monkeypatch.setattr(
        restore.psycopg,
        "connect",
        lambda _url: (_ for _ in ()).throw(AssertionError("target must not be queried")),
    )
    monkeypatch.setattr(restore, "_harden_audit_event_privileges", lambda _url: pytest.fail("must not harden"))
    monkeypatch.setattr(restore, "_run_database_only_verifier", lambda _url: pytest.fail("must not verify"))
    monkeypatch.setattr(sys, "argv", _restore_argv(dump))

    with pytest.raises(subprocess.CalledProcessError):
        restore.main()

    assert commands[0][:2] == ["pg_restore", "--list"]
    assert Path(commands[0][2]).name == "archive.dump"
    assert Path(commands[0][2]) != dump
    assert not Path(commands[0][2]).exists()


def test_restore_database_only_subprocess_failure_blocks_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        restore.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "verify")),
    )

    with pytest.raises(subprocess.CalledProcessError):
        restore._run_database_only_verifier("postgresql+psycopg://app:pw@db/test")
