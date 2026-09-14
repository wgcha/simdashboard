import copy
import json

import pytest

from app.domains.semantic_mapping.engine import (
    SemanticValidationError, inspect_sample, preview_recipe, resolve_widgets,
    validate_recipe, validate_template,
)

pytestmark = pytest.mark.unit


def item(id="stress", kind="scalar", **kwargs):
    return {"id": id, "key": id, "label": "응력", "kind": kind, "unit": "MPa", **kwargs}


def recipe(**mapping):
    return {"format": "csv", "mappings": [{"result_item_id": "stress", "source": "S", **mapping}]}


def template(type="kpi", **kwargs):
    return {"widgets": [{"id": "w1", "type": type, "item_ids": ["stress"], **kwargs}]}


def code_error(code, operation):
    with pytest.raises(SemanticValidationError) as caught:
        operation()
    assert caught.value.code == code


def test_two_formats_normalize_to_same_semantic_item_and_widget():
    items = [item()]
    csv = preview_recipe(recipe(), items, "a.csv", b"S\n120\n")
    other = {"format": "json", "records_path": "run.values", "mappings": [{"source": "peak_pa", "source_unit": "Pa", "result_item_id": "stress"}]}
    js = preview_recipe(other, items, "b.json", b'{"run":{"values":[{"peak_pa":120000000}]}}')
    assert csv["observations"] == js["observations"]
    assert resolve_widgets(template(), items, csv) == resolve_widgets(template(), items, js)
    assert csv["scalars"][0]["value"] == 120


def test_dimensioned_results_preserve_grain_and_do_not_choose_first_for_card():
    items = [item(dimensions=["node"])]
    parsed = preview_recipe(recipe(dimensions={"node": "N"}), items, "a.csv", b"N,S\n10,120\n20,100\n")
    assert len({v["variable_key"] for v in parsed["scalars"]}) == 2
    assert resolve_widgets(template(), items, parsed)[0]["status"] == "AMBIGUOUS_RESULT"
    widget = resolve_widgets(template(filters={"node": "20"}), items, parsed)[0]
    assert widget["data"][0]["value"] == 100


def test_missing_target_does_not_fall_back_to_unrelated_result():
    parsed = preview_recipe(recipe(), [item()], "a.csv", b"S\n120\n")
    other = item("other")
    result = resolve_widgets(template(item_ids=["other"]), [item(), other], parsed)
    assert result[0]["status"] == "MISSING_RESULT"
    assert result[0]["data"] == []


def test_duplicate_scalar_requires_explicit_aggregate_and_mean_is_correct():
    code_error("DUPLICATE_RESULT", lambda: preview_recipe(recipe(), [item()], "a.csv", b"S\n100\n140\n"))
    parsed = preview_recipe(recipe(aggregate="mean"), [item()], "a.csv", b"S\n100\n140\n")
    assert parsed["scalars"][0]["value"] == 120


def test_dimension_identity_survives_row_order_and_source_rename():
    items = [item(dimensions=["node"])]
    a = preview_recipe(recipe(dimensions={"node": "N"}), items, "old.csv", b"N,S\n10,120\n20,100\n")
    b = preview_recipe(recipe(dimensions={"node": "N"}), items, "renamed.csv", b"N,S\n20,100\n10,120\n")
    assert {r["variable_key"] for r in a["scalars"]} == {r["variable_key"] for r in b["scalars"]}


def test_curve_converts_both_axes_and_rejects_duplicate_x():
    items = [item(kind="curve")]
    reading = recipe(x_source="t", x_unit="ms", target_x_unit="s", source_unit="Pa")
    parsed = preview_recipe(reading, items, "a.csv", b"t,S\n0,1000000\n1000,2000000\n")
    assert parsed["curves"][0]["points"] == [{"x": 0., "y": 1.}, {"x": 1., "y": 2.}]
    code_error("CURVE_ORDER_INVALID", lambda: preview_recipe(reading, items, "a.csv", b"t,S\n1,2\n1,3\n"))
    code_error("INCOMPATIBLE_RESULT", lambda: validate_template(template(), items))
    assert resolve_widgets(template("line"), items, parsed)[0]["status"] == "READY"


def test_unit_dimension_conflict_is_rejected_before_reading():
    code_error("UNIT_MISMATCH", lambda: validate_recipe(recipe(source_unit="mm"), [item()]))


def test_display_change_does_not_mutate_payload():
    parsed = preview_recipe(recipe(), [item()], "a.csv", b"S\n120\n")
    before = copy.deepcopy(parsed)
    view = resolve_widgets(template(display_unit="Pa", title="Peak", decimals=1), [item()], parsed)
    assert view[0]["data"][0]["value"] == 120e6
    assert parsed == before


def test_multiple_widgets_same_type_have_independent_bindings():
    parsed = preview_recipe(recipe(), [item()], "a.csv", b"S\n120\n")
    configuration = {"widgets": [*template()["widgets"], {**template()["widgets"][0], "id": "w2", "display_unit": "Pa"}]}
    results = resolve_widgets(configuration, [item()], parsed)
    assert [w["data"][0]["value"] for w in results] == [120., 120e6]


def test_scatter_matches_dimension_keys_not_order_and_reports_missing_pair():
    items = [item(dimensions=["node"]), item("disp", unit="mm", dimensions=["node"])]
    a = preview_recipe(recipe(dimensions={"node": "N"}), items, "a.csv", b"N,S\n10,120\n20,100\n")
    b_recipe = {"format": "csv", "mappings": [{"source": "D", "result_item_id": "disp", "dimensions": {"node": "N"}}]}
    b = preview_recipe(b_recipe, items, "b.csv", b"N,D\n20,2\n10,1\n")
    both = {"observations": a["observations"] + b["observations"]}
    view = template("scatter", x_item_id="stress", y_item_id="disp")
    assert [(p["x"], p["y"]) for p in resolve_widgets(view, items, both)[0]["data"]] == [(120., 1.), (100., 2.)]
    both["observations"].pop()
    assert resolve_widgets(view, items, both)[0]["status"] == "INCOMPATIBLE_RESULT"


@pytest.mark.parametrize("data,code", [(b"S,S\n1,2\n", "FIELDS_INVALID"), (b"S\nNaN\n", "NONFINITE_VALUE"), (b"S,T\n1\n", "ROW_INVALID")])
def test_malformed_csv_fails_without_partial_results(data, code):
    code_error(code, lambda: preview_recipe(recipe(), [item()], "a.csv", data))


def test_json_duplicate_fields_and_nonfinite_rejected():
    r = {"format": "json", "mappings": [{"source": "S", "result_item_id": "stress"}]}
    code_error("DUPLICATE_FIELD", lambda: preview_recipe(r, [item()], "a.json", b'{"S":1,"S":2}'))
    code_error("NONFINITE_VALUE", lambda: preview_recipe(r, [item()], "a.json", b'{"S":NaN}'))


def test_wrong_extension_and_missing_field_not_silently_used():
    code_error("FORMAT_MISMATCH", lambda: preview_recipe(recipe(), [item()], "a.json", b"S\n120\n"))
    code_error("VALUE_MISSING", lambda: preview_recipe(recipe(), [item()], "a.csv", b"other\n120\n"))


def test_skip_policy_reports_missing_values_but_keeps_valid_rows():
    parsed = preview_recipe(recipe(missing="skip"), [item()], "a.csv", b'S\n""\n120\n')
    assert parsed["summary"]["skipped_values"] == 1
    assert parsed["warnings"]
    assert parsed["scalars"][0]["value"] == 120


def test_inspection_is_bounded_and_lists_nested_fields():
    sample = json.dumps([{"values": {"stress": i}} for i in range(30)]).encode()
    inspected = inspect_sample("a.json", sample)
    assert inspected["fields"] == ["values.stress"]
    assert len(inspected["rows"]) == 20
    assert inspected["row_count"] == 30


def test_boolean_and_text_are_not_coerced_to_floats():
    values = [item(data_type="BOOLEAN", unit="")]
    assert preview_recipe(recipe(), values, "a.csv", b"S\nfalse\n")["scalars"][0]["value"] is False
    code_error("TYPE_MISMATCH", lambda: preview_recipe(recipe(), values, "a.csv", b"S\nmaybe\n"))


def test_gauge_threshold_uses_item_unit_and_display_conversion_preserves_verdict():
    parsed = preview_recipe(recipe(), [item()], "a.csv", b"S\n120\n")
    displayed = resolve_widgets(template("gauge", threshold=100, display_unit="Pa"), [item()], parsed)[0]
    assert displayed["threshold"] == 100e6
    assert displayed["verdict"] == "FAIL"


def test_scatter_can_convert_axes_of_different_dimensions_independently():
    items = [item(), item("disp", unit="mm")]
    reading = {"format": "csv", "mappings": [{"result_item_id": "stress", "source": "S"}, {"result_item_id": "disp", "source": "D"}]}
    parsed = preview_recipe(reading, items, "a.csv", b"S,D\n120,2\n")
    view = template("scatter", x_item_id="stress", y_item_id="disp", x_display_unit="Pa", y_display_unit="m")
    displayed = resolve_widgets(view, items, parsed)[0]
    assert displayed["data"][0] == {"x": 120e6, "y": .002, "dimensions": {}}
    assert (displayed["x_unit"], displayed["y_unit"]) == ("Pa", "m")


@pytest.mark.parametrize("broken", [None, [], {"format": []}, {"format": "csv", "mappings": [{"result_item_id": []}]}])
def test_malformed_definitions_raise_controlled_validation_errors(broken):
    with pytest.raises(SemanticValidationError):
        validate_recipe(broken, [item()])


def test_output_point_limit_applies_across_mappings(monkeypatch):
    from app.domains.semantic_mapping import engine
    monkeypatch.setattr(engine, "MAX_OUTPUT_VALUES", 3)
    items = [item(kind="curve"), item("displacement", kind="curve", unit="mm")]
    reading = {"format": "csv", "mappings": [
        {"result_item_id": "stress", "source": "S", "x_source": "T"},
        {"result_item_id": "displacement", "source": "D", "x_source": "T"},
    ]}
    code_error("OUTPUT_LIMIT", lambda: preview_recipe(reading, items, "a.csv", b"T,S,D\n0,1,2\n1,2,3\n"))


def test_serialized_output_limit_bounds_large_text(monkeypatch):
    from app.domains.semantic_mapping import engine
    monkeypatch.setattr(engine, "MAX_OUTPUT_BYTES", 1000)
    code_error("OUTPUT_LIMIT", lambda: preview_recipe(recipe(), [item(data_type="TEXT", unit="")], "a.csv", b"S\n" + b"a" * 800 + b"\n"))


def test_nested_korean_json_fields_are_readable():
    reading = {"format": "json", "records_path": "해석.결과", "mappings": [{"result_item_id": "stress", "source": "측정.응력"}]}
    source = json.dumps({"해석": {"결과": [{"측정": {"응력": 120}}]}}).encode()
    assert preview_recipe(reading, [item()], "결과.json", source)["observations"][0]["value"] == 120


def test_filter_typo_is_rejected_at_definition_time():
    code_error("FILTER_INVALID", lambda: validate_template(template(filters={"nodd": "1"}), [item(dimensions=["node"])]))


def test_new_template_never_reinterprets_old_value_with_changed_type():
    parsed = preview_recipe(recipe(), [item()], "a.csv", b"S\n120\n")
    view = resolve_widgets(template(), [item(data_type="TEXT")], parsed)
    assert view[0]["status"] == "INCOMPATIBLE_RESULT"


def test_line_requires_common_x_units_or_explicit_conversion():
    items = [item(kind="curve"), item("other", kind="curve")]
    a = preview_recipe(recipe(x_source="T", x_unit="s"), items, "a.csv", b"T,S\n0,1\n1,2\n")
    reading = {"format": "csv", "mappings": [{"result_item_id": "other", "source": "S", "x_source": "T", "x_unit": "ms"}]}
    b = preview_recipe(reading, items, "b.csv", b"T,S\n0,3\n1000,4\n")
    combined = {"observations": a["observations"] + b["observations"]}
    view = template("line", item_ids=["stress", "other"])
    assert resolve_widgets(view, items, combined)[0]["status"] == "INCOMPATIBLE_RESULT"
    view["widgets"][0]["x_display_unit"] = "s"
    rendered = resolve_widgets(view, items, combined)[0]
    assert rendered["status"] == "READY"
    assert rendered["data"][1]["points"][-1]["x"] == 1
    assert combined["observations"][1]["points"][-1]["x"] == 1000


def test_documented_csv_and_json_examples_feed_all_six_widgets():
    from pathlib import Path
    directory = Path(__file__).resolve().parents[2] / "examples" / "semantic-mapping"
    definitions = json.loads((directory / "definitions.json").read_text(encoding="utf-8"))
    outcomes = []
    for reading in definitions["recipes"]:
        source = directory / ("result." + reading["definition"]["format"])
        parsed = preview_recipe(reading, definitions["items"], source.name, source.read_bytes())
        views = resolve_widgets(definitions["templates"][0], definitions["items"], parsed)
        assert len(views) == 6
        assert {v["status"] for v in views} == {"READY"}
        assert views[0]["data"][0]["value"] == 120
        assert views[1]["verdict"] == "PASS"
        assert views[5]["data"][0]["y"] == 2
        outcomes.append(parsed["observations"])
    assert outcomes[0] == outcomes[1]
