"""Regression contracts for absent values without changing stored recipe semantics."""
import json

import pytest

from app.domains.semantic_mapping.engine import SemanticValidationError, inspect_sample, preview_recipe, resolve_widgets

pytestmark = pytest.mark.unit


def item(kind="scalar", data_type="FLOAT"):
    return {"id": "value", "key": "value", "label": "Value", "kind": kind,
            "data_type": data_type, "unit": "mm" if data_type in {"FLOAT", "INTEGER"} else "",
            "dimensions": [], **({"components": ["X", "Y", "Z"]} if kind == "vector" else {})}


def recipe(missing="preserve", **mapping):
    return {"reader_version": 2, "format": "json", "input_layout": "json_object",
            "mappings": [{"result_item_id": "value", "source": "#/value", "missing": missing, **mapping}]}


@pytest.mark.parametrize("data_type", ["FLOAT", "INTEGER", "TEXT", "BOOLEAN"])
@pytest.mark.parametrize("empty", [None, "", " \t "])
def test_preserved_scalar_never_becomes_zero_or_none_text(data_type, empty):
    definition = item(data_type=data_type)
    parsed = preview_recipe(recipe(), [definition], "sample.json", json.dumps({"value": empty}).encode())
    assert parsed["observations"][0]["value"] is None
    assert parsed["scalars"][0]["value"] is None
    widgets = resolve_widgets({"widgets": [{"id": "value", "type": "kpi", "item_ids": ["value"]}]}, [definition], parsed)
    assert widgets[0]["status"] == "NO_VALUE"


@pytest.mark.parametrize("kind", ["scalar", "vector"])
def test_preserve_rejects_unknown_source_instead_of_calling_it_empty(kind):
    with pytest.raises(SemanticValidationError):
        preview_recipe(recipe(), [item(kind)], "sample.json", b'{"different":null}')


@pytest.mark.parametrize("raw", [[None, None], [None, None, None, None], 7, {"X": 1}, [1, False, 3], [1, {}, 3]])
def test_vector_shape_and_type_errors_are_not_missing_values(raw):
    with pytest.raises(SemanticValidationError):
        preview_recipe(recipe(), [item("vector")], "sample.json", json.dumps({"value": raw}).encode())


def test_same_vector_recipe_reads_empty_partial_and_later_filled_values():
    definition = item("vector")
    for raw, expected in [([None, "", " "], [None, None, None]), ([0, "", 3.649], [0, None, 3.649]), ([-.008, -46.656, 3.649], [-.008, -46.656, 3.649])]:
        parsed = preview_recipe(recipe(), [definition], "sample.json", json.dumps({"value": raw}).encode())
        assert len(parsed["observations"]) == 1
        observed = parsed["observations"][0]
        assert observed["kind"] == "vector" and observed["components"] == ["X", "Y", "Z"]
        assert observed["value"] == expected
        before = json.dumps(parsed, sort_keys=True)
        widgets = resolve_widgets({"widgets": [{"id": "z", "type": "kpi", "item_ids": ["value"], "vector_component": "Z", "display_unit": "m"}]}, [definition], parsed)
        if expected[2] is None:
            assert widgets[0]["status"] == "NO_VALUE"
        else:
            assert widgets[0]["data"][0]["value"] == pytest.approx(expected[2] / 1000)
        assert json.dumps(parsed, sort_keys=True) == before


@pytest.mark.parametrize("missing", ["skip", "error"])
def test_legacy_whitespace_numeric_behavior_is_not_reinterpreted(missing):
    with pytest.raises(SemanticValidationError) as error:
        preview_recipe(recipe(missing), [item()], "sample.json", b'{"value":" "}')
    assert error.value.code == "TYPE_MISMATCH"


@pytest.mark.parametrize("kind,raw", [("scalar", None), ("vector", [None, None, None])])
def test_empty_values_do_not_hide_incompatible_units(kind, raw):
    with pytest.raises(SemanticValidationError):
        preview_recipe(recipe(source_unit="MPa"), [item(kind)], "sample.json", json.dumps({"value": raw}).encode())


def test_sample_uses_later_real_value_after_blanks_without_losing_zero_false():
    result = inspect_sample("rows.json", b'[{"n":" ","flag":null},{"n":0,"flag":false}]')
    fields = {field["source"]: field for field in result["field_details"]}
    assert fields["#/n"]["value"] == 0
    assert fields["#/flag"]["value"] is False
    assert fields["#/n"]["empty_count"] == 1


def test_explicit_vector_definition_supplies_structure_for_json_null():
    parsed = preview_recipe(recipe(), [item("vector")], "sample.json", b'{"value":null}')
    assert parsed["observations"][0]["value"] == [None, None, None]
    assert parsed["observations"][0]["components"] == ["X", "Y", "Z"]


@pytest.mark.parametrize("x,y", [(None, None), (None, 3), (0, None), (0, 3)])
def test_scatter_known_empty_pair_has_no_value_but_zero_is_a_real_point(x, y):
    definitions = [{**item(), "id": axis, "key": axis} for axis in ("x", "y")]
    definition = {**recipe(), "mappings": [{"result_item_id": axis, "source": f"#/{axis}", "missing": "preserve"} for axis in ("x", "y")]}
    parsed = preview_recipe(definition, definitions, "sample.json", json.dumps({"x": x, "y": y}).encode())
    widgets = resolve_widgets({"widgets": [{"id": "xy", "type": "scatter", "x_item_id": "x", "y_item_id": "y"}]}, definitions, parsed)
    assert widgets[0]["status"] == ("NO_VALUE" if x is None or y is None else "READY")
    if x is not None and y is not None:
        assert widgets[0]["data"][0]["x"] == 0
