import json
from pathlib import Path

import pytest

from app.domains.semantic_mapping.engine import (
    INPUT_V2_MAX_BYTES,
    SemanticValidationError,
    inspect_sample,
    preview_recipe,
    validate_recipe,
)
from app.domains.semantic_mapping.reader_v2 import ReplayableRows, read


pytestmark = pytest.mark.unit


def _item(identifier="value", **extra):
    return {"id": identifier, "key": identifier, "label": identifier, "kind": "scalar", "unit": "", **extra}


def _recipe(*, fmt="json", layout="json_object", source="#/value", **extra):
    return {
        "reader_version": 2, "format": fmt, "input_layout": layout,
        "encoding": "utf-8", "delimiter": ",", "header_row": 1, "records_path": "",
        "mappings": [{"result_item_id": "value", "source": source}], **extra,
    }


def test_v2_fixture_key_value_csv_and_json_object_have_lossless_pointers():
    samples = Path(__file__).with_name("fixtures") / "semantic_samples"
    csv = inspect_sample("issue_23.csv", (samples / "issue_23.csv").read_bytes())
    js = inspect_sample("issue_24.json", (samples / "issue_24.json").read_bytes())
    pointer = "#/Set Top Disp. (mm)"
    vector = "#/Set CG Coord. (mm)/2"
    assert csv["recipe_suggestion"]["input_layout"] == "csv_key_value"
    assert js["recipe_suggestion"]["input_layout"] == "json_object"
    assert {field["source"] for field in csv["field_details"]} >= {pointer, vector}
    assert {field["source"] for field in js["field_details"]} >= {pointer, vector}
    csv_recipe = {**_recipe(fmt="csv", layout="csv_key_value", source=pointer), **csv["recipe_suggestion"], "mappings": [{"result_item_id": "value", "source": pointer}]}
    json_recipe = {**_recipe(source=pointer), **js["recipe_suggestion"], "mappings": [{"result_item_id": "value", "source": pointer}]}
    assert preview_recipe(csv_recipe, [_item()], "issue_23.csv", (samples / "issue_23.csv").read_bytes())["scalars"][0]["value"] == -311.643
    assert preview_recipe(json_recipe, [_item()], "issue_24.json", (samples / "issue_24.json").read_bytes())["scalars"][0]["value"] == -311.643


def test_v2_inspection_pages_fields_after_full_discovery_and_preserves_zero_null_and_arrays():
    source = json.dumps([{f"field-{index}": index for index in range(300)} for _ in range(3)]).encode()
    inspected = inspect_sample("wide.json", source, row_limit=1, field_offset=256, field_limit=100)
    assert inspected["field_count"] == 300
    assert len(inspected["field_details"]) == 44
    assert inspected["has_more_rows"] and not inspected["has_more_fields"]
    special = inspect_sample("special.json", b'{"a/b~c":[0,null,false]}')
    details = {entry["source"]: entry for entry in special["field_details"]}
    assert details["#/a~1b~0c/0"]["value"] == 0
    assert details["#/a~1b~0c/1"]["missing"] is False
    assert details["#/a~1b~0c/1"]["null_count"] == 1
    assert details["#/a~1b~0c/2"]["value"] is False


def test_v2_reader_handles_cp949_utf16_tsv_records_and_more_than_v1_row_field_mapping_caps():
    cp949 = "값\t결과\n0\t1\n".encode("cp949")
    inspected = inspect_sample("result.tsv", cp949)
    assert inspected["recipe_suggestion"]["encoding"] == "cp949"
    assert inspected["recipe_suggestion"]["delimiter"] == "\t"
    utf16 = '[{"value": 1}, {"value": 2}]'.encode("utf-16")
    assert inspect_sample("r.json", utf16)["recipe_suggestion"]["encoding"] == "utf-16"
    assert inspect_sample("r.json", '[{"value": 1}]'.encode("utf-16-le"))["recipe_suggestion"]["encoding"] == "utf-16"
    mappings = [{"result_item_id": "value", "source": "#/value"} for _ in range(65)]
    validate_recipe(_recipe(mappings=mappings), [_item()])
    rows = b"x,y\n" + b"\n".join(f"{i},{i}".encode() for i in range(50_001))
    curve = _item("value", kind="curve")
    recipe = _recipe(fmt="csv", layout="csv_table", source="#/y", mappings=[{"result_item_id": "value", "source": "#/y", "x_source": "#/x"}])
    parsed = preview_recipe(recipe, [curve], "large.csv", rows)
    assert parsed["summary"]["row_count"] == 50_001
    assert len(parsed["curves"][0]["points"]) == 50_001


def test_v2_autodetect_is_content_led_but_saved_format_stays_pinned():
    assert inspect_sample("misnamed.csv", b'{"value": 2}')["format"] == "json"
    ordinary = inspect_sample("series.csv", b"time,value\n0,1\n1,2\n")
    assert ordinary["recipe_suggestion"]["input_layout"] == "csv_table"
    recipe = _recipe(fmt="csv", layout="csv_table", source="#/value")
    with pytest.raises(SemanticValidationError) as caught:
        preview_recipe(recipe, [_item()], "misnamed.csv", b'{"value": 2}')
    assert caught.value.code in {"CSV_INVALID", "NO_RECORDS", "ROW_INVALID"}


def test_v2_json_record_pointer_and_nullable_missing_policy_are_used_in_execution():
    payload = b'{"run":{"values":[{"value":0},{"value":null},{"value":2}]}}'
    recipe = _recipe(layout="json_records", source="#/value", records_path="#/run/values", mappings=[{"result_item_id": "value", "source": "#/value", "missing": "skip", "aggregate": "mean"}])
    parsed = preview_recipe(recipe, [_item()], "r.json", payload)
    assert parsed["scalars"][0]["value"] == 1
    assert parsed["summary"]["skipped_values"] == 1


def test_v2_input_limit_is_exported_and_enforced_without_v1_limit_regression():
    assert INPUT_V2_MAX_BYTES == 64 * 1024 * 1024
    with pytest.raises(SemanticValidationError) as caught:
        inspect_sample("r.json", b"{" + b" " * INPUT_V2_MAX_BYTES + b"}")
    assert caught.value.code == "FILE_SIZE_LIMIT"


def test_v2_does_not_inherit_the_v1_normalized_value_count_cap(monkeypatch):
    from app.domains.semantic_mapping import engine

    monkeypatch.setattr(engine, "MAX_OUTPUT_VALUES", 3)
    curve = _item("value", kind="curve")
    recipe = _recipe(
        fmt="csv", layout="csv_table", source="#/y",
        mappings=[{"result_item_id": "value", "source": "#/y", "x_source": "#/x"}],
    )
    parsed = preview_recipe(recipe, [curve], "many.csv", b"x,y\n0,1\n1,2\n2,3\n3,4\n")
    assert len(parsed["curves"][0]["points"]) == 4


def test_v2_table_and_root_record_rows_are_replayable_not_materialized_lists():
    table = read("table.csv", b"x,y\n0,1\n1,2\n")
    records = read("records.json", b'[{"x":0},{"x":1}]')
    assert isinstance(table["rows"], ReplayableRows)
    assert isinstance(records["rows"], ReplayableRows)
    assert list(table["rows"])[1]["y"] == "2"
    assert records["rows"][1]["x"] == 1
    saved = read("records.json", b'[{"x":0},{"x":1}]', {"format": "json", "records_path": "#", "input_layout": "json_records"})
    assert isinstance(saved["rows"], ReplayableRows)


def test_v2_missing_null_empty_and_zero_remain_distinct():
    result = inspect_sample("r.json", b'[{"x":null},{"x":""},{"x":0},{}]')
    field = next(entry for entry in result["field_details"] if entry["source"] == "#/x")
    assert (field["missing_count"], field["null_count"], field["empty_count"]) == (1, 1, 1)


def test_v2_content_detection_suggestion_is_executable_with_original_filename():
    payload = b'{"value":2}'
    suggested = inspect_sample("result.txt", payload)["recipe_suggestion"]
    result = preview_recipe({**_recipe(), **suggested}, [_item()], "result.txt", payload)
    assert result["scalars"][0]["value"] == 2


def test_v2_streamed_json_does_not_silently_replace_duplicate_keys():
    with pytest.raises(SemanticValidationError) as error:
        inspect_sample("r.json", b'[{"x":1,"x":2}]')
    assert error.value.code == "DUPLICATE_FIELD"


def test_v2_no_bom_utf16_big_endian_suggestion_replays_and_large_csv_cells_work():
    content = '{"value":2}'.encode("utf-16-be")
    suggested = inspect_sample("be.json", content)["recipe_suggestion"]
    result = preview_recipe({**_recipe(), **suggested}, [_item()], "be.json", content)
    assert result["scalars"][0]["value"] == 2
    assert inspect_sample("large-cell.csv", ("value\n" + "x" * 140_000).encode())["row_count"] == 1


def test_v1_literal_pointer_looking_key_keeps_original_meaning():
    recipe = {"format": "json", "mappings": [{"result_item_id": "value", "source": "#/a"}]}
    assert preview_recipe(recipe, [_item()], "r.json", b'{"#/a":1,"a":2}')["scalars"][0]["value"] == 1


def test_v2_short_csv_row_reports_absent_column_instead_of_json_null():
    result = inspect_sample("r.csv", b"x,y\n0,1\n2\n", overrides={"input_layout": "csv_table"})
    field = next(entry for entry in result["field_details"] if entry["source"] == "#/y")
    assert field["missing_count"] == 1 and field["null_count"] == 0
