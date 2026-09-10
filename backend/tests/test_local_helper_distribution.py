from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services import local_helper_distribution as service


def _release(directory: Path, *, filename: str = "SimulationWorkbenchLocalHelper.zip") -> bytes:
    payload = b"PK\x03\x04distribution-api-fixture"
    (directory / "SimulationWorkbenchLocalHelper.zip").write_bytes(payload)
    (directory / "distribution-manifest.json").write_text(json.dumps({
        "format": 1, "version": "test-1", "filename": filename,
        "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload),
        "released_at": "2026-09-09T00:00:00Z",
    }), encoding="utf-8")
    return payload


def test_public_distribution_serves_verified_release_without_login(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_HELPER_DISTRIBUTION_DIR", str(tmp_path))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "distribution-test-key-at-least-32-characters")
    payload = _release(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/local-helper/distribution")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["status"] == "ready"
        assert body["sha256"] == hashlib.sha256(payload).hexdigest()
        assert body["size_bytes"] == len(payload)
        assert str(tmp_path) not in response.text
        download = client.get(body["artifact_url"])
        assert download.status_code == 200
        assert download.content == payload
        assert download.headers["content-type"] == "application/zip"
        assert download.headers["cache-control"] == "no-store"
        assert "SimulationWorkbenchLocalHelper.zip" in download.headers["content-disposition"]
        assert client.head(body["artifact_url"]).status_code == 200
        # No initial administrator exists: only the exact public distribution
        # routes are available while all private paths remain setup-blocked.
        assert client.get("/api/auth/status").json()["setup_required"] is True
        assert client.get("/api/local-helper/distribution/other").status_code == 503
        assert client.post(body["artifact_url"], json={}).status_code == 405


def test_missing_or_corrupt_distribution_cannot_be_downloaded(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_HELPER_DISTRIBUTION_DIR", str(tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/local-helper/distribution").json()["status"] == "unavailable"
        assert client.get("/api/local-helper/distribution/download").status_code == 404
        _release(tmp_path)
        (tmp_path / "SimulationWorkbenchLocalHelper.zip").write_bytes(b"corrupt")
        assert client.get("/api/local-helper/distribution").json()["status"] == "unavailable"
        assert client.get("/api/local-helper/distribution/download").status_code == 404


def test_distribution_manifest_rejects_parent_path(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_HELPER_DISTRIBUTION_DIR", str(tmp_path))
    _release(tmp_path, filename="../SimulationWorkbenchLocalHelper.zip")
    with TestClient(app) as client:
        assert client.get("/api/local-helper/distribution").json()["status"] == "unavailable"
        assert client.get("/api/local-helper/distribution/download").status_code == 404


def test_release_verification_cache_invalidates_on_file_or_manifest_change(monkeypatch, tmp_path):
    payload = _release(tmp_path)
    calls = []
    original_hash = service._sha256
    def record_hash(path):
        calls.append(path)
        return original_hash(path)
    monkeypatch.setattr(service, "_sha256", record_hash)
    assert service.load_distribution(tmp_path)[0] is not None
    assert service.load_distribution(tmp_path)[0] is not None
    assert len(calls) == 1
    # Same-sized replacement cannot inherit a previously successful result.
    archive = tmp_path / "SimulationWorkbenchLocalHelper.zip"
    archive.write_bytes(b"X" * len(payload))
    assert service.load_distribution(tmp_path)[0] is None
    assert service.load_distribution(tmp_path)[0] is None
    assert len(calls) == 2
    _release(tmp_path)
    assert service.load_distribution(tmp_path)[0] is not None
    manifest_path = tmp_path / "distribution-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert service.load_distribution(tmp_path)[0] is None


def test_windows_utf8_bom_manifest_is_supported(tmp_path):
    _release(tmp_path)
    manifest = tmp_path / "distribution-manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8-sig")
    assert service.load_distribution(tmp_path)[0] is not None


def test_explicit_directory_argument_does_not_use_bundled_fallback(monkeypatch, tmp_path):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    _release(bundled)
    monkeypatch.setattr(service, "bundled_distribution_directory", lambda: bundled)

    assert service.load_distribution(tmp_path / "missing")[0] is None


def test_configured_missing_directory_does_not_use_bundled_fallback(monkeypatch, tmp_path):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    _release(bundled)
    monkeypatch.setenv("LOCAL_HELPER_DISTRIBUTION_DIR", str(tmp_path / "configured-missing"))
    monkeypatch.setattr(service, "bundled_distribution_directory", lambda: bundled)

    assert service.load_distribution()[0] is None


def test_invalid_runtime_manifest_does_not_use_bundled_fallback(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    bundled = tmp_path / "bundled"
    runtime.mkdir()
    bundled.mkdir()
    _release(bundled)
    (runtime / service.MANIFEST_NAME).write_text("{invalid", encoding="utf-8")
    monkeypatch.delenv("LOCAL_HELPER_DISTRIBUTION_DIR", raising=False)
    monkeypatch.setattr(service, "runtime_distribution_directory", lambda: runtime)
    monkeypatch.setattr(service, "bundled_distribution_directory", lambda: bundled)

    assert service.load_distribution()[0] is None


def test_runtime_manifest_directory_does_not_use_bundled_fallback(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    bundled = tmp_path / "bundled"
    runtime.mkdir()
    bundled.mkdir()
    _release(bundled)
    (runtime / service.MANIFEST_NAME).mkdir()
    monkeypatch.delenv("LOCAL_HELPER_DISTRIBUTION_DIR", raising=False)
    monkeypatch.setattr(service, "runtime_distribution_directory", lambda: runtime)
    monkeypatch.setattr(service, "bundled_distribution_directory", lambda: bundled)

    assert service.load_distribution()[0] is None


def test_missing_runtime_manifest_serves_valid_bundled_release(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    bundled = tmp_path / "bundled"
    runtime.mkdir()
    bundled.mkdir()
    payload = _release(bundled)
    monkeypatch.delenv("LOCAL_HELPER_DISTRIBUTION_DIR", raising=False)
    monkeypatch.setattr(service, "runtime_distribution_directory", lambda: runtime)
    monkeypatch.setattr(service, "bundled_distribution_directory", lambda: bundled)

    artifact, reason = service.load_distribution()
    assert reason is None
    assert artifact is not None
    assert artifact.path.parent == bundled
    assert artifact.path.read_bytes() == payload


def test_import_directory_stays_at_runtime_path_when_bundled_release_exists(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    _release(bundled)
    monkeypatch.delenv("LOCAL_HELPER_DISTRIBUTION_DIR", raising=False)
    monkeypatch.setattr(service, "runtime_distribution_directory", lambda: runtime)
    monkeypatch.setattr(service, "bundled_distribution_directory", lambda: bundled)

    assert service.distribution_directory() == runtime
