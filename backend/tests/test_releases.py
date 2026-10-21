"""버전 게시판(docs/features/version-board.md): RELEASES.md 파서, 빌드 정보, 세 버전 값의 일치, API.

``RELEASES.md`` 맨 위 항목·``app.version.__version__``·``frontend/package.json`` 의 ``version`` 은
항상 같아야 한다(배포 때 함께 올린다).  이 파일의 일치 시험이 그 규칙의 검사이다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import initialize_database
from app.main import app
from app.services import releases
from app.version import __version__

from tests.test_physicsai_link_contract import PASSWORD, _cleanup, _insert_user

REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE = """# 릴리스 기록

규칙 설명은 항목으로 읽히지 않는다.
- 머리말의 글머리표도 항목이 아니다.

## 0.3.0 — 2026-10-21

- 첫째 변경
- 둘째 변경은 줄이 길어
  다음 줄로 이어진다.

## 0.2.0 - 2026-10-06
* 별표 글머리표

## 0.1.0 — 2026-09-15
"""


@pytest.mark.unit
def test_parser_reads_entries_newest_first_with_notes() -> None:
    entries = releases.parse_releases(SAMPLE, strict=True)
    assert [(e.version, e.date) for e in entries] == [("0.3.0", "2026-10-21"), ("0.2.0", "2026-10-06"), ("0.1.0", "2026-09-15")]
    assert entries[0].notes == ["첫째 변경", "둘째 변경은 줄이 길어 다음 줄로 이어진다."]
    assert entries[1].notes == ["별표 글머리표"]
    assert entries[2].notes == []


@pytest.mark.unit
def test_parser_skips_bad_headings_unless_strict() -> None:
    text = "## 1.0 — 2026-01-01\n- 버전 형식 오류\n\n## 1.0.0 — 2026-13-40\n- 날짜 오류\n\n## 0.9.0 — 2026-01-01\n- 정상\n\n## 0.9.0 — 2026-01-02\n- 중복\n"
    assert [(e.version, e.notes) for e in releases.parse_releases(text)] == [("0.9.0", ["정상"])]
    for bad, fragment in (("## 1.0 — 2026-01-01\n", "heading must be"), ("## 1.0.0 — 2026-13-40\n", "invalid date"),
                          ("## 0.9.0 — 2026-01-01\n## 0.9.0 — 2026-01-02\n", "appears twice")):
        with pytest.raises(ValueError, match=fragment):
            releases.parse_releases(bad, strict=True)
    assert releases.parse_releases("") == []


@pytest.mark.unit
def test_load_releases_missing_file_is_empty(tmp_path: Path) -> None:
    assert releases.load_releases(tmp_path) == []
    (tmp_path / "RELEASES.md").write_text("﻿" + SAMPLE, encoding="utf-8")
    assert [e.version for e in releases.load_releases(tmp_path)] == ["0.3.0", "0.2.0", "0.1.0"]


@pytest.mark.unit
def test_version_values_agree_with_top_release_entry() -> None:
    entries = releases.parse_releases((REPO_ROOT / "RELEASES.md").read_text(encoding="utf-8"), strict=True)
    assert entries, "RELEASES.md has no entry"
    package_version = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))["version"]
    assert entries[0].version == __version__ == package_version, (
        f"RELEASES.md top entry {entries[0].version}, backend/app/version.py {__version__}, "
        f"frontend/package.json {package_version} must be equal (bump them together)"
    )
    assert all(entry.notes for entry in entries), "every release needs at least one bullet"
    keys = [releases.version_tuple(entry.version) for entry in entries]
    assert keys == sorted(keys, reverse=True), "RELEASES.md must list the newest version first"
    dates = [entry.date for entry in entries]
    assert dates == sorted(dates, reverse=True), "release dates must not increase going down the file"


@pytest.mark.unit
def test_stamped_build_info_wins_and_is_normalised(tmp_path: Path) -> None:
    (tmp_path / "build-info.json").write_text(
        json.dumps({"commit": "0C09B07ABCDEF0123456789ABCDEF0123456789A", "built_at": "2026-10-21T09:43:38+09:00"}), encoding="utf-8")
    assert releases.read_build_info(tmp_path) == {"commit": "0c09b07abcde", "built_at": "2026-10-21T00:43:38Z", "source": "bundle"}


@pytest.mark.unit
def test_build_info_ignores_garbage_and_empty_without_git(tmp_path: Path) -> None:
    (tmp_path / "build-info.json").write_text(json.dumps({"commit": "not a sha", "built_at": "yesterday"}), encoding="utf-8")
    assert releases.read_build_info(tmp_path) == {"commit": None, "built_at": None, "source": None}
    (tmp_path / "build-info.json").write_text("[1, 2]", encoding="utf-8")
    assert releases.read_build_info(tmp_path)["source"] is None


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_build_info_from_git_checkout(tmp_path: Path) -> None:
    def run(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], check=True, capture_output=True)

    run("init", "-q")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    run("add", "a.txt")
    run("commit", "-q", "-m", "init", "--date=2026-10-01T12:00:00+00:00")
    head = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    info = releases.read_build_info(tmp_path)
    assert info["source"] == "git" and info["commit"] == head[:12]
    assert info["built_at"] and info["built_at"].endswith("Z")


@pytest.mark.unit
def test_releases_response_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "RELEASES.md").write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(releases, "release_root", lambda: tmp_path)
    monkeypatch.setattr(releases, "_process_build_info", lambda: {"commit": "abc1234", "built_at": None, "source": "git"})
    payload = releases.releases_response()
    assert payload["version"] == {"current": __version__, "released_at": "2026-10-21"}  # the date of the entry equal to the running version
    monkeypatch.setattr(releases, "__version__", "9.9.9")
    assert releases.releases_response()["version"] == {"current": "9.9.9", "released_at": None}
    assert payload["build"] == {"commit": "abc1234", "built_at": None, "source": "git"}
    assert [r["version"] for r in payload["releases"]] == ["0.3.0", "0.2.0", "0.1.0"]


def test_releases_endpoint_requires_login_and_serves_any_active_user(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    suffix = uuid4().hex[:8]
    admin_id = _insert_user(f"rel-admin-{suffix}", admin=True, membership_role=None)
    outsider_id = _insert_user(f"rel-outsider-{suffix}", admin=False, membership_role=None)
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    try:
        with TestClient(app) as client:
            assert client.get("/api/releases").status_code == 401
            login = client.post("/api/auth/login", json={"username": f"rel-outsider-{suffix}", "password": PASSWORD})
            assert login.status_code == 200
            client.cookies.clear()
            response = client.get("/api/releases", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
            assert response.status_code == 200
            body = response.json()
            assert body["version"]["current"] == __version__
            assert body["version"]["released_at"] == body["releases"][0]["date"]
            assert body["releases"][0]["version"] == __version__ and body["releases"][0]["notes"]
            assert set(body["build"]) == {"commit", "built_at", "source"}
            assert client.post("/api/releases", headers={"Authorization": f"Bearer {login.json()['access_token']}"}).status_code == 405
    finally:
        _cleanup([admin_id, outsider_id], suffix)
