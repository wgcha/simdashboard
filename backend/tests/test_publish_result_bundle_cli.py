from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import publish_result_bundle


pytestmark = pytest.mark.unit


def test_cli_known_error_is_safe_json_and_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = publish_result_bundle.main(
        [
            "--source-bundle",
            str(tmp_path / "missing-source"),
            "--import-root",
            str(tmp_path / "missing-root"),
            "--publication-id",
            "publication-1",
            "--check",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "RESULT_BUNDLE_SOURCE_UNSAFE"
    assert str(tmp_path) not in captured.err
    assert captured.out == ""


def _source(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    (source / "summary.json").write_text(
        '[{"variable_key":"peak","data_type":"FLOAT","value":1}]', encoding="utf-8"
    )
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "schema_id": "cli-test",
                "version": 1,
                "context": {"project_id": "project", "request_id": "request", "load_case_id": "loadcase"},
                "mappings": [{"kind": "typed_scalars", "path": "summary.json"}],
            }
        ),
        encoding="utf-8",
    )
    return source


def test_cli_success_and_check_do_not_mutate_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _source(tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    code = publish_result_bundle.main(
        ["--source-bundle", str(source), "--import-root", str(target), "--publication-id", "publication-1", "--check"]
    )
    checked = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(checked) == {"bundle_path", "manifest_checksum", "bundle_fingerprint", "entry_count"}
    assert list(target.iterdir()) == []

    code = publish_result_bundle.main(
        ["--source-bundle", str(source), "--import-root", str(target), "--publication-id", "publication-1"]
    )
    published = json.loads(capsys.readouterr().out)
    assert code == 0
    assert published == checked
    assert (target / published["bundle_path"]).is_dir()


def test_cli_check_help_describes_target_mutation_scope(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        publish_result_bundle.main(["--help"])
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "without mutating" in help_text
    assert "--import-root" in help_text
