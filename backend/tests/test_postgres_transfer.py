from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts.postgres_transfer import safe_relative_path, validate_bundle


@pytest.mark.parametrize("value", ["", "../secret", "/absolute", "C:/secret", "assets/../../secret"])
def test_safe_relative_path_rejects_unsafe_values(value: str):
    with pytest.raises(RuntimeError):
        safe_relative_path(value)


def test_safe_relative_path_normalizes_windows_separators():
    assert safe_relative_path("imports\\run-1\\image.svg").as_posix() == "imports/run-1/image.svg"


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
        "format_version": 1,
        "database": "simulation_dashboard",
        "alembic_revision": "head",
        "table_counts": {},
        "database_dump": {"file": dump.name, "bytes": dump.stat().st_size, "sha256": hashlib.sha256(b"dump").hexdigest()},
        "assets_archive": {"file": archive.name, "bytes": archive.stat().st_size, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
        "assets": [{"path": "sample.svg", "bytes": 6, "sha256": hashlib.sha256(b"wrong!").hexdigest()}],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("scripts.postgres_transfer.expected_alembic_head", lambda: "head")
    with pytest.raises(RuntimeError, match="Asset checksum failed"):
        validate_bundle(bundle)

