from __future__ import annotations

import json

import pytest

from app.domains.analysis_insights.condition_comparison import compare_run_conditions


def _run(run_id: str, *, input_value: object = None, metadata: object = None, solver: object = "RADIOSS", execution_load_case_id: str = "load") -> dict[str, object]:
    return {
        "id": run_id,
        "load_case_id": "load",
        "execution_load_case_id": execution_load_case_id,
        "input_json": json.dumps(input_value) if input_value is not None else None,
        "metadata_json": json.dumps(metadata) if metadata is not None else None,
        "solver": solver,
    }


def _rows(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    return {item["key"]: item for item in payload["rows"]}  # type: ignore[index]


@pytest.mark.unit
def test_condition_comparison_uses_only_linked_snapshot_and_explicit_metadata_contract() -> None:
    payload = compare_run_conditions(
        [
            _run("base", input_value={"material": "AL", "thickness": {"value": 1, "unit": "mm"}, "pressure_mpa": 0, "contact": "general", "nested": {"a": [False, 0]}}),
            _run("target", input_value={"material": "AL", "thickness_mm": 1, "pressure_mpa": 0, "contact": "general", "nested": {"a": [False, 0]}}, metadata={"run_conditions": {"mesh_size_mm": 8}, "unrelated": "ignored"}),
        ],
        "load", "base", "target",
    )
    rows = _rows(payload)
    assert rows["material"]["status"] == "SAME"
    assert rows["thickness"]["status"] == "SAME"
    assert rows["pressure_mpa"]["status"] == "SAME"
    assert rows["input.nested.a"]["status"] == "SAME"
    assert rows["input.nested.a"]["baseline"]["value"] == [False, 0]
    assert "unrelated" not in rows
    assert rows["mesh_size_mm"]["status"] == "UNKNOWN"
    assert rows["mesh_size_mm"]["target"]["source"] == "analysis_run_metadata.run_conditions:target"


@pytest.mark.unit
def test_condition_comparison_closes_conflicts_units_and_absence_to_unknown() -> None:
    payload = compare_run_conditions(
        [
            _run("base", input_value={"thickness": {"value": 1, "unit": "mm"}, "material": "A"}, metadata={"run_conditions": {"thickness_mm": 2}}),
            _run("target", input_value={"thickness": {"value": 1, "unit": "cm"}}),
        ],
        "load", "base", "target",
    )
    rows = _rows(payload)
    assert rows["thickness"]["status"] == "UNKNOWN"
    assert rows["thickness"]["baseline"] is None
    assert "출처" in rows["thickness"]["reason"]
    assert rows["material"]["status"] == "UNKNOWN"
    assert rows["contact"]["status"] == "UNKNOWN"
    assert rows["solver"]["status"] == "SAME"


@pytest.mark.unit
def test_condition_comparison_rejects_wrong_load_case_snapshot_and_keeps_bool_distinct_from_zero() -> None:
    payload = compare_run_conditions(
        [
            _run("base", input_value={"enabled": False}, execution_load_case_id="other"),
            _run("target", input_value={"enabled": 0}),
        ],
        "load", "base", "target",
    )
    rows = _rows(payload)
    assert rows["input.enabled"]["status"] == "UNKNOWN"
    assert rows["input.enabled"]["baseline"] is None
    assert rows["input.enabled"]["target"]["value"] == 0


@pytest.mark.unit
def test_condition_comparison_keeps_explicit_custom_empty_and_nullable_unit_values_unknown() -> None:
    payload = compare_run_conditions(
        [
            _run("base", metadata={"run_conditions": {"custom": [], "material": {"value": "AL6061", "unit": None}, "thickness": 1}}),
            _run("target", metadata={"run_conditions": {"custom": [], "material": {"value": "AL6061", "unit": None}, "thickness": 1}}),
        ],
        "load", "base", "target",
    )
    rows = _rows(payload)
    assert rows["input.custom"]["status"] == "UNKNOWN"
    assert "사용할 수 없습니다" in rows["input.custom"]["reason"]
    assert rows["material"]["status"] == "SAME"
    assert rows["thickness"]["status"] == "UNKNOWN"
    assert "단위" in rows["thickness"]["reason"]


@pytest.mark.unit
@pytest.mark.parametrize('value', [{}, '   ', {'value': float('inf'), 'unit': 'N'}, {'value': 1, 'unit': 3}])
def test_invalid_custom_values_remain_visible_as_unknown(value):
    payload = compare_run_conditions([
        _run('base', metadata={'run_conditions': {'custom': value}}),
        _run('target', metadata={'run_conditions': {'custom': value}}),
    ], 'load', 'base', 'target')
    assert _rows(payload)['input.custom']['status'] == 'UNKNOWN'
    json.dumps(payload, allow_nan=False)


@pytest.mark.unit
@pytest.mark.parametrize('key,baseline,target,status', [
    ('contact', {'enabled': False}, {'enabled': 0}, 'CHANGED'),
    ('contact', {'enabled': False, 'friction': 0}, {'friction': 0, 'enabled': False}, 'SAME'),
    ('contact', ['part-a', 'part-b'], ['part-b', 'part-a'], 'CHANGED'),
    ('thickness', {'value': 1, 'unit': 'mm'}, {'value': 1, 'unit': 'cm'}, 'UNKNOWN'),
])
def test_condition_value_semantics(key, baseline, target, status):
    payload = compare_run_conditions([
        _run('base', metadata={'run_conditions': {key: baseline}}),
        _run('target', metadata={'run_conditions': {key: target}}),
    ], 'load', 'base', 'target')
    assert _rows(payload)[key]['status'] == status
