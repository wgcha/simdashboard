from pathlib import Path

import pytest

from scripts import postgres_cli as cli


pytestmark = pytest.mark.unit


def _tool(root: Path, version: str, name: str = "pg_dump") -> Path:
    path = root / version / "bin" / f"{name}.exe"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


@pytest.fixture
def windows_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "WINDOWS", True)
    monkeypatch.delenv("POSTGRES_BIN", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli, "_windows_installation_roots", lambda: [tmp_path])
    return tmp_path


@pytest.mark.parametrize("name", ["pg_dump", "pg_restore"])
def test_installed_tools_work_without_path_or_postgres_bin(windows_installation, name):
    expected = _tool(windows_installation, "18", name)
    assert cli.executable(name) == str(expected.resolve())


def test_discovery_sorts_versions_numerically(windows_installation):
    _tool(windows_installation, "9.6")
    expected = _tool(windows_installation, "18")
    assert cli.executable("pg_dump") == str(expected.resolve())


def test_explicit_postgres_bin_precedes_path_and_discovery(windows_installation, monkeypatch):
    configured = _tool(windows_installation, "17")
    _tool(windows_installation, "18")
    monkeypatch.setenv("POSTGRES_BIN", str(configured.parent))
    monkeypatch.setattr(cli.shutil, "which", lambda name: "path-tool.exe")
    assert cli.executable("pg_dump") == str(configured.resolve())


def test_missing_tools_fail_without_a_fake_success_path(windows_installation):
    with pytest.raises(cli.PostgresToolNotFound, match="POSTGRES_BIN"):
        cli.executable("pg_dump")
