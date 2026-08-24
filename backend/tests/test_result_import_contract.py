from __future__ import annotations

import math

import pytest

from app.schemas.result_import import ResultFile, ResultType
from app.services.result_import_adapter import LegacyResultParserAdapter
from app.services.result_import_contract import (
    ResultContractError,
    ResultProvenance,
    normalize_parser_output,
)
from app.services.result_import_service import ResultImportService


def _provenance(result_type: ResultType) -> ResultProvenance:
    return ResultProvenance("bundle/results.csv", result_type.value, "test-parser")


@pytest.mark.unit
def test_scalar_and_series_legacy_shapes_share_typed_contract():
    scalar = normalize_parser_output(
        [{"variable_key": "peak", "display_name": "Peak", "value_double": 2.5, "threshold_double": 3.0}],
        result_type=ResultType.SCALAR_RESULTS,
        provenance=_provenance(ResultType.SCALAR_RESULTS),
    )
    series = normalize_parser_output(
        [{"variable_key": "history", "display_name": "History", "time_value": 1.0, "value_double": 2.5}],
        result_type=ResultType.GENERIC_TIME_HISTORY,
        provenance=_provenance(ResultType.GENERIC_TIME_HISTORY),
    )

    assert scalar.scalars[0].value_double == 2.5
    assert scalar.scalars[0].provenance.source_path == "bundle/results.csv"
    assert scalar.scalar_persistence_rows()[0] == {
        "variable_key": "peak",
        "display_name": "Peak",
        "value_double": 2.5,
        "unit": None,
        "threshold_double": 3.0,
        "criterion_key": None,
        "verdict": None,
    }
    # Missing units intentionally remain absent so ResultRepository retains
    # its historical defaults.
    assert series.time_series_persistence_rows()[0] == {
        "variable_key": "history",
        "display_name": "History",
        "time_value": 1.0,
        "value_double": 2.5,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "result_type"),
    [
        ({"scalars": [{"variable_key": "bad", "value_double": "2"}], "time_series": []}, ResultType.OPEN_CELL_STRESS),
        ({"scalars": [{"variable_key": "bad", "value_double": math.inf}], "time_series": []}, ResultType.OPEN_CELL_STRESS),
        ([{"variable_key": "bad", "value_double": 1.0}], ResultType.GENERIC_TIME_HISTORY),
        ([{"variable_key": "bad", "time_value": 0.0}], ResultType.SCALAR_RESULTS),
    ],
)
def test_malformed_parser_output_fails_closed(raw, result_type):
    with pytest.raises(ResultContractError):
        normalize_parser_output(raw, result_type=result_type, provenance=_provenance(result_type))


@pytest.mark.unit
def test_legacy_adapter_adds_parser_provenance(tmp_path, monkeypatch):
    path = tmp_path / "history.csv"
    path.write_text("time,variable_key,value,display_name\n0.0,history,1.5,History\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    legacy_service = ResultImportService(tmp_path.name)
    assert legacy_service.root_dir == tmp_path.resolve()

    payload = LegacyResultParserAdapter().parse_file(
        ResultFile(
            type=ResultType.GENERIC_TIME_HISTORY,
            path="history.csv",
        ),
        path,
        source_path=str(path.relative_to(legacy_service.root_dir)),
    )

    assert payload.time_series[0].provenance.parser == "GenericTimeHistoryParser"
    assert payload.time_series[0].provenance.source_path == "history.csv"
    assert payload.time_series[0].for_persistence()["time_value"] == 0.0
