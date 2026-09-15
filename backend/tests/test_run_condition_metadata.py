import pytest

from app.folder_import import FolderImportError
from app.services.master_result_refresh import _ingestion_command
from app.services.run_condition_metadata import validate_run_conditions
from app.services.run_criteria_metadata import validate_result_criteria

pytestmark = pytest.mark.unit


def test_valid_conditions_are_bounded_json_and_detached():
    raw = {"drop_height": {"value": 500, "unit": "mm"}, "enabled": False, "payload": None}
    result = validate_run_conditions(raw)
    assert result == raw
    assert result is not raw
    result["drop_height"]["value"] = 700
    assert raw["drop_height"]["value"] == 500


def test_invalid_shapes_size_depth_and_nonfinite_values_are_controlled():
    cases = [[], {str(index): index for index in range(65)}, {"value": float("nan")}, {"value": float("inf")}, {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}]
    for value in cases:
        with pytest.raises(FolderImportError, match="") as error:
            validate_run_conditions(value)
        assert error.value.code == "RUN_CONDITIONS_INVALID"
    with pytest.raises(FolderImportError) as error:
        validate_run_conditions({"x": "x" * 17000})
    assert error.value.code == "RUN_CONDITIONS_INVALID"


def test_absent_metadata_preserves_legacy_command_shape():
    command = _ingestion_command("bundle/manifest.json", "manifest", "bundle", ("p", "r", "l"), {}, {})
    assert command["metadata"] == {"manifest_checksum": "manifest", "bundle_fingerprint": "bundle"}


def test_ingestion_command_copies_run_conditions_without_spreading_metadata():
    conditions = {"material": {"value": "AL6061", "unit": None}, "zero": 0}
    manifest = {"metadata": {"run_conditions": conditions, "author": "ignored"}}
    command = _ingestion_command("bundle/manifest.json", "manifest", "bundle", ("p", "r", "l"), {}, manifest)
    assert command["metadata"]["run_conditions"] == conditions
    assert command["metadata"] is not manifest["metadata"]
    assert "author" not in command["metadata"]
    conditions["zero"] = 9
    assert command["metadata"]["run_conditions"]["zero"] == 0


def test_ingestion_command_rejects_malformed_run_conditions_before_persistence():
    with pytest.raises(FolderImportError) as error:
        _ingestion_command("bundle/manifest.json", "manifest", "bundle", ("p", "r", "l"), {}, {"metadata": {"run_conditions": []}})
    assert error.value.code == "RUN_CONDITIONS_INVALID"


def test_run_conditions_size_is_measured_as_json_bytes():
    value = {"text": "가" * 6000}
    with pytest.raises(FolderImportError) as error:
        validate_run_conditions(value)
    assert error.value.code == "RUN_CONDITIONS_INVALID"


def test_result_criteria_are_bounded_detached_and_reject_ambiguous_shapes():
    value = {"peak": {"operator": "LTE", "upper": 75, "unit": "MPa", "label": "Peak stress"}}
    snapshot = validate_result_criteria(value)
    assert snapshot == value and snapshot is not value
    snapshot["peak"]["upper"] = 1
    assert value["peak"]["upper"] == 75
    for malformed in (
        {"peak": {"operator": "LTE", "upper": 75, "lower": 0, "unit": "MPa"}},
        {"peak": {"operator": "LTE", "uppper": 75, "unit": "MPa"}},
        {"peak": {"operator": "BETWEEN", "lower": 3, "upper": 2, "unit": "MPa"}},
        {"a": {"operator": "LTE", "upper": 1, "unit": "N"}, " a": {"operator": "LTE", "upper": 1, "unit": "N"}},
    ):
        with pytest.raises(FolderImportError) as error:
            validate_result_criteria(malformed)
        assert error.value.code == "RESULT_CRITERIA_INVALID"
