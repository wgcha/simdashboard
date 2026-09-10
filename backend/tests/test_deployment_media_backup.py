from __future__ import annotations

import hashlib
import io
import os
import subprocess
import zipfile
from pathlib import Path

import duckdb
import pytest

from scripts.deployment_media_backup import DeploymentMediaBackupError, create_deployment_media_bundle
from scripts import deployment_media_backup as bundle_module


pytestmark = pytest.mark.unit


def _inventory(**changes: object) -> dict[str, object]:
    report: dict[str, object] = {
        "format": "analysis-canvas-media-inventory", "format_version": 1,
        "blob_count": 1, "chunk_count": 1, "declared_chunk_count": 1, "total_blob_bytes": 1,
        "media_reference_count": 1, "drop_video_reference_count": 0, "reference_count": 1,
        "unbound_media_asset_count": 0, "missing_media_blob_count": 0, "missing_drop_video_blob_count": 0,
        "missing_reference_count": 0, "orphan_blob_count": 0, "orphan_chunk_count": 0,
        "corrupt_blob_count": 0, "corrupt_blob_ids": [], "content_integrity_verified": True,
        "demo_contract_active": False, "demo_expected_count": 0, "demo_count": 0,
        "missing_demo_ids": [], "unexpected_demo_ids": [], "demo_exact": True,
        "catalog_sha256": "a" * 64,
    }
    report.update(changes)
    return report


def _connection(*paths: object) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE media_assets (file_path VARCHAR, blob_id VARCHAR)")
    for value in paths:
        connection.execute("INSERT INTO media_assets VALUES (?, NULL)", [value])
    return connection


@pytest.mark.parametrize("reference", ["legacy.txt", "Assets/legacy.txt"])
def test_valid_dual_read_archives_all_assets_and_optional_demo(tmp_path: Path, reference: str) -> None:
    assets = tmp_path / "backend" / "assets"
    assets.mkdir(parents=True)
    (assets / "legacy.txt").write_bytes(b"legacy")
    (assets / "other.txt").write_bytes(b"other")
    demo = tmp_path / "video_example"
    demo.mkdir()
    (demo / "scene.mp4").write_bytes(b"demo")
    archive = tmp_path / "protected" / "media.zip"
    archive.parent.mkdir()

    result = create_deployment_media_bundle(_connection(reference), _inventory(unbound_media_asset_count=1), assets, archive)

    assert result["file_count"] == 3
    assert result["demo_file_count"] == 1
    assert result["referenced_file_count"] == 1
    assert result["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == ["assets/legacy.txt", "assets/other.txt", "video_example/scene.mp4"]


@pytest.mark.parametrize("raw", ["assets/missing.mp4", "assets/../../secret.mp4", "C:/secret.mp4", "/secret.mp4", r"\\server\share\secret.mp4"])
def test_missing_or_escaping_legacy_path_fails_without_path_leak(tmp_path: Path, raw: str) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    archive = tmp_path / "media.zip"
    with pytest.raises(DeploymentMediaBackupError) as raised:
        create_deployment_media_bundle(_connection(raw), _inventory(unbound_media_asset_count=1), assets, archive, tmp_path / "no-demo")
    assert raised.value.code in {"UNBOUND_MEDIA_FILE_MISSING", "UNBOUND_MEDIA_PATH_INVALID"}
    assert raw not in str(raised.value)


def test_symlink_asset_is_rejected_without_exposing_name(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    target = tmp_path / "outside"
    target.write_bytes(b"private")
    link = assets / "secret-link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable in this test environment")
    with pytest.raises(DeploymentMediaBackupError) as raised:
        create_deployment_media_bundle(_connection(), _inventory(), assets, tmp_path / "media.zip", tmp_path / "no-demo")
    assert raised.value.code == "ASSETS_REPARSE_POINT"
    assert "secret-link" not in str(raised.value)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_windows_junction_in_assets_ancestor_is_rejected_without_path_leak(tmp_path: Path) -> None:
    target = tmp_path / "target"
    assets = target / "assets"
    assets.mkdir(parents=True)
    junction = tmp_path / "owned-junction"
    completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)], capture_output=True, text=True)
    if completed.returncode != 0:
        pytest.skip("junction creation unavailable in this test environment")
    try:
        with pytest.raises(DeploymentMediaBackupError) as raised:
            create_deployment_media_bundle(_connection(), _inventory(), junction / "assets", tmp_path / "media.zip", tmp_path / "no-demo")
        assert raised.value.code == "ASSETS_REPARSE_POINT"
        assert "owned-junction" not in str(raised.value)
    finally:
        junction.rmdir()


def test_corrupt_blob_and_unexpected_demo_are_blocked() -> None:
    with pytest.raises(DeploymentMediaBackupError, match="MEDIA_BLOB_CORRUPT"):
        create_deployment_media_bundle(_connection(), _inventory(corrupt_blob_count=1, corrupt_blob_ids=["private-blob"], content_integrity_verified=False), Path("missing"), Path("out.zip"))
    with pytest.raises(DeploymentMediaBackupError, match="MEDIA_DATABASE_REFERENCES_INVALID"):
        create_deployment_media_bundle(_connection(), _inventory(demo_exact=False, demo_count=1, unexpected_demo_ids=["private-demo"]), Path("missing"), Path("out.zip"))


def test_incomplete_demo_and_missing_demo_source_are_warnings(tmp_path: Path) -> None:
    archive = tmp_path / "media.zip"
    result = create_deployment_media_bundle(
        _connection(), _inventory(demo_contract_active=True, demo_expected_count=20, demo_count=19, missing_demo_ids=["fixture"], demo_exact=False),
        tmp_path / "missing-assets", archive, tmp_path / "missing-demo",
    )
    assert result["warnings"] == ["INCOMPLETE_DEMO_CATALOG_INCLUDED", "DEMO_SOURCE_MISSING"]
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == []


def test_archive_cannot_be_published_over_an_existing_file(tmp_path: Path) -> None:
    archive = tmp_path / "media.zip"
    archive.write_bytes(b"existing")
    with pytest.raises(DeploymentMediaBackupError, match="ARCHIVE_EXISTS"):
        create_deployment_media_bundle(_connection(), _inventory(), tmp_path / "missing", archive, tmp_path / "missing-demo")


@pytest.mark.parametrize("change", ["add", "replace", "delete"])
def test_source_tree_change_before_publication_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    source = assets / "result.txt"
    source.write_bytes(b"original result")
    original = bundle_module._archive
    def archive(records, target, verify_catalog):
        def mutate_and_verify():
            if change == "add":
                (assets / "new.txt").write_bytes(b"new result")
            elif change == "replace":
                source.write_bytes(b"changed result")
            else:
                source.unlink()
            verify_catalog()
        return original(records, target, mutate_and_verify)
    monkeypatch.setattr(bundle_module, "_archive", archive)
    output = tmp_path / "backup.zip"
    with pytest.raises(DeploymentMediaBackupError, match="ASSETS_CHANGED"):
        create_deployment_media_bundle(_connection(), _inventory(), assets, output, tmp_path / "missing-demo")
    assert not output.exists()


def test_zip_readback_detects_corrupt_member_before_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "result.txt").write_bytes(b"original result")
    original = zipfile.ZipFile.open
    def corrupt_read(self, name, mode="r", *args, **kwargs):
        if self.mode == "r":
            return io.BytesIO(b"corrupted archive member")
        return original(self, name, mode, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", corrupt_read)
    output = tmp_path / "backup.zip"
    with pytest.raises(DeploymentMediaBackupError, match="ARCHIVE_VERIFY_FAILED"):
        create_deployment_media_bundle(_connection(), _inventory(), assets, output, tmp_path / "missing-demo")
    assert not output.exists()
