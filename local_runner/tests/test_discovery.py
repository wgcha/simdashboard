"""Discovery preserves real file candidates and never starts a program."""

from pathlib import Path
from unittest.mock import patch

from local_runner.discovery import discover_programs
from local_runner.models import ProgramCandidate


def test_discovery_preserves_versions_keywords_and_deduplicates_sources(tmp_path):
    files = []
    for version in ("2024.1", "2025.1"):
        executable = tmp_path / version / "hmopengl.exe"
        executable.parent.mkdir()
        executable.write_bytes(b"discovery fixture only")
        executable.chmod(0o755)
        files.append(("HyperMesh", executable, version))
    with patch("local_runner.discovery._registry_paths", return_value=files), patch(
        "local_runner.discovery._known_paths", return_value=[files[0], ("HyperMesh", tmp_path / "missing.exe", "2023.1")]
    ), patch("subprocess.run", side_effect=AssertionError("Discovery must never launch a process")):
        candidates = [ProgramCandidate.model_validate(item) for item in discover_programs()]
    assert [item.version for item in candidates] == ["2024.1", "2025.1"]
    assert all("메시 수정" in item.keywords for item in candidates)
    assert candidates[0].executable_path != candidates[1].executable_path


def test_unknown_version_is_an_editable_candidate_not_invalid_response(tmp_path):
    executable = tmp_path / "hmopengl.exe"
    executable.write_bytes(b"discovery fixture only")
    executable.chmod(0o755)
    with patch("local_runner.discovery._registry_paths", return_value=[("HyperMesh", executable, "")]), patch(
        "local_runner.discovery._known_paths", return_value=[]
    ):
        candidate = ProgramCandidate.model_validate(discover_programs()[0])
    assert candidate.version == "버전 확인 필요"
