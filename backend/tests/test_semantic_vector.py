from __future__ import annotations

import base64
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.domains.semantic_mapping.engine import (
    SemanticValidationError,
    inspect_sample,
    preview_recipe,
    resolve_widgets,
)
from app.main import app


def _vector_item() -> dict:
    return {
        "id": "position", "key": "position", "label": "Contact position (mm)",
        "kind": "vector", "data_type": "FLOAT", "unit": "mm", "dimensions": [],
        "components": ["X", "Y", "Z"],
    }


def _recipe(*, missing: str = "preserve") -> dict:
    return {
        "reader_version": 2, "format": "json", "input_layout": "json_object",
        "encoding": "utf-8", "delimiter": ",", "header_row": 1, "records_path": "",
        "mappings": [{"result_item_id": "position", "source": "#/Contact", "source_unit": "mm", "missing": missing}],
    }


@pytest.mark.unit
def test_vector_parent_inspection_and_preserved_missing_values_keep_structure() -> None:
    inspected = inspect_sample("result.json", b'{"Contact":["",null,""],"other":[null,0]}')
    details = {entry["source"]: entry for entry in inspected["field_details"]}
    assert details["#/Contact"]["mappable"] is True
    assert details["#/Contact"]["vector_candidate"] is True
    assert details["#/Contact"]["components"] == ["X", "Y", "Z"]
    assert details["#/other/1"]["value"] == 0

    parsed = preview_recipe(_recipe(), [_vector_item()], "result.json", b'{"Contact":["",null,"  "]}')
    observation = parsed["observations"][0]
    assert observation["value"] == [None, None, None]
    assert observation["components"] == ["X", "Y", "Z"]
    assert observation["value_status"] == "MISSING"
    # The one canonical TEXT evidence row prevents a vector-only run from
    # disappearing from the generic run and variable catalog.
    canonical = parsed["scalars"]
    assert len(canonical) == 1
    assert canonical[0]["data_type"] == "TEXT"
    assert canonical[0]["value"] == "[null,null,null]"
    assert canonical[0]["variable_key"] == "position"


@pytest.mark.unit
def test_inspector_merges_array_shape_per_row_without_double_count_or_crash() -> None:
    mixed = inspect_sample("result.json", b'[{"v":1},{"v":[1,2,3]}]')
    entry = next(detail for detail in mixed["field_details"] if detail["source"] == "#/v")
    assert entry["shape_mixed"] is True and entry["mappable"] is False
    assert "MIXED_FIELD_SHAPE:#/v" in mixed["warnings"]

    empty = inspect_sample("result.json", b'{"v":[]}')
    entry = next(detail for detail in empty["field_details"] if detail["source"] == "#/v")
    assert entry["missing_count"] == 0 and entry["mappable"] is False

    changed = inspect_sample("result.json", b'[{"v":[]},{"v":[1,2,3]}]')
    entry = next(detail for detail in changed["field_details"] if detail["source"] == "#/v")
    assert entry["components"] == ["X", "Y", "Z"] and entry["vector_candidate"] is True

    blank_shape = inspect_sample("result.json", b'[{"v":[]},{"v":[null,null,null]}]')
    entry = next(detail for detail in blank_shape["field_details"] if detail["source"] == "#/v")
    assert entry["value"] == [None, None, None] and entry["components"] == ["X", "Y", "Z"] and entry["mappable"] is True

    nullable = inspect_sample("result.json", b'[{"v":null},{"v":[1,2,3]}]')
    entry = next(detail for detail in nullable["field_details"] if detail["source"] == "#/v")
    assert entry["data_type"] == "array" and entry["vector_candidate"] is True


@pytest.mark.unit
def test_vector_rejects_wrong_parent_type_length_and_unknown_preserve_pointer() -> None:
    for content, code in ((b'{"Contact":"bad"}', "VECTOR_INVALID"), (b'{"Contact":[1,2]}', "VECTOR_INVALID"), (b'{}', "VALUE_MISSING")):
        with pytest.raises(SemanticValidationError) as caught:
            preview_recipe(_recipe(), [_vector_item()], "result.json", content)
        assert caught.value.code == code

    nullable = preview_recipe(_recipe(), [_vector_item()], "result.json", b'{"Contact":null}')
    assert nullable["observations"][0]["value"] == [None, None, None]
    assert nullable["observations"][0]["value_status"] == "MISSING"


@pytest.mark.unit
def test_preserved_integer_scalar_is_null_not_a_zero_or_text_value() -> None:
    item = {
        "id": "count", "key": "count", "label": "Contact count", "kind": "scalar",
        "data_type": "INTEGER", "unit": "", "dimensions": [],
    }
    recipe = _recipe()
    recipe["mappings"] = [{"result_item_id": "count", "source": "#/count", "missing": "preserve"}]
    parsed = preview_recipe(recipe, [item], "result.json", b'{"count":null}')
    assert parsed["observations"][0]["value"] is None
    assert parsed["observations"][0]["value_status"] == "MISSING"


@pytest.mark.unit
def test_vector_widget_projection_and_table_missing_state() -> None:
    item = _vector_item()
    parsed = preview_recipe(_recipe(), [item], "result.json", b'{"Contact":[1,"",3]}')
    widgets = resolve_widgets(
        {"widgets": [
            {"id": "table", "type": "table", "item_ids": ["position"]},
            {"id": "x", "type": "kpi", "item_ids": ["position"], "vector_component": "X"},
            {"id": "y", "type": "gauge", "item_ids": ["position"], "vector_component": "Y", "threshold": 2, "display_unit": "m"},
        ]},
        [item], parsed,
    )
    assert widgets[0]["status"] == "READY" and widgets[0]["data"][0]["value"] == [1.0, None, 3.0]
    assert widgets[1]["status"] == "READY" and widgets[1]["data"][0]["value"] == 1.0
    assert widgets[2]["status"] == "NO_VALUE" and widgets[2]["unit"] == "m"
    with pytest.raises(SemanticValidationError) as caught:
        resolve_widgets({"widgets": [{"id": "bad", "type": "kpi", "item_ids": ["position"]}]}, [item], parsed)
    assert caught.value.code == "VECTOR_COMPONENT_INVALID"

    scalar = {"id": "zero", "key": "zero", "label": "Zero", "kind": "scalar", "data_type": "INTEGER", "unit": "", "dimensions": []}
    mixed = resolve_widgets(
        {"widgets": [{"id": "mixed", "type": "table", "item_ids": ["position", "zero"]}]},
        [item, scalar],
        {"observations": [parsed["observations"][0], {"item_id": "zero", "kind": "scalar", "data_type": "INTEGER", "value": 0, "value_status": "READY", "dimensions": {}, "unit": ""}]},
    )
    assert mixed[0]["status"] == "READY"


@pytest.mark.unit
def test_scatter_preserved_nulls_are_no_value_without_hiding_complete_pairs() -> None:
    scalar = lambda ident: {"id": ident, "key": ident, "label": ident, "kind": "scalar", "data_type": "FLOAT", "unit": "mm", "dimensions": []}
    template = {"widgets": [{"id": "xy", "type": "scatter", "x_item_id": "x", "y_item_id": "y", "display_unit": "m"}]}
    missing = resolve_widgets(template, [scalar("x"), scalar("y")], {"observations": [
        {"item_id": "x", "kind": "scalar", "data_type": "FLOAT", "value": None, "dimensions": {}, "unit": "mm", "value_status": "MISSING"},
        {"item_id": "y", "kind": "scalar", "data_type": "FLOAT", "value": None, "dimensions": {}, "unit": "mm", "value_status": "MISSING"},
    ]})
    assert missing[0]["status"] == "NO_VALUE" and missing[0]["data"] == []
    complete = resolve_widgets(template, [scalar("x"), scalar("y")], {"observations": [
        {"item_id": "x", "kind": "scalar", "data_type": "FLOAT", "value": 1000, "dimensions": {}, "unit": "mm"},
        {"item_id": "y", "kind": "scalar", "data_type": "FLOAT", "value": 2000, "dimensions": {}, "unit": "mm"},
    ]})
    assert complete[0]["status"] == "READY" and complete[0]["data"] == [{"x": 1.0, "y": 2.0, "dimensions": {}}]


@pytest.mark.duckdb_integration
def test_vector_only_import_creates_canonical_run_evidence_and_no_value_widget() -> None:
    with TestClient(app) as client:
        item = client.post("/api/semantic-mapping/items", json={"definition": _vector_item()})
        assert item.status_code == 201, item.text
        item_id = item.json()["id"]
        definition = _recipe()
        definition["mappings"][0]["result_item_id"] = item_id
        recipe = client.post("/api/semantic-mapping/recipes", json={
            "name": "vector recipe", "definition": definition, "sample_filename": "vector.json",
            "sample_content_base64": base64.b64encode(b'{"Contact":[null,"",null]}').decode(),
        })
        assert recipe.status_code == 201, recipe.text
        template = client.post("/api/semantic-mapping/templates", json={
            "name": "vector table", "definition": {"widgets": [{"id": "table", "type": "table", "item_ids": [item_id]}]},
        })
        assert template.status_code == 201, template.text
        activated = client.post("/api/semantic-mapping/activate-bundle", json={
            "recipe_id": recipe.json()["id"], "recipe_version": 1, "template_id": template.json()["id"], "template_version": 1,
            "expected_recipe_active_version": None, "expected_template_active_version": None,
        })
        assert activated.status_code == 200, activated.text
        boundary = "vector-" + uuid4().hex
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"recipe_id\"\r\n\r\n{recipe.json()['id']}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"template_id\"\r\n\r\n{template.json()['id']}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"load_case_id\"\r\n\r\nloadcase-drop-bottom-001\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"vector.json\"\r\nContent-Type: application/json\r\n\r\n{{\"Contact\":[null,\"\",null]}}\r\n--{boundary}--\r\n"
        ).encode()
        imported = client.post("/api/semantic-mapping/import", headers={"content-type": f"multipart/form-data; boundary={boundary}"}, content=body)
        assert imported.status_code == 200, imported.text
        assert imported.json()["widgets"][0]["status"] == "NO_VALUE"
        with connect() as conn:
            row = conn.execute("SELECT value_text FROM scalar_results WHERE analysis_run_id=?", [imported.json()["run_id"]]).fetchone()
            assert row == ("[null,null,null]",)
