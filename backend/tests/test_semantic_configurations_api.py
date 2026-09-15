from __future__ import annotations
import base64
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database_connection import connect

pytestmark = pytest.mark.duckdb_integration

def sample(): return base64.b64encode(b"stress,node\n12.5,N-1\n").decode()
def item(client):
    result=client.post("/api/semantic-mapping/items",json={"definition":{"key":"peak_stress","label":"Peak","kind":"scalar","unit":"MPa","dimensions":["node"]}}); assert result.status_code==201,result.text; return result.json()
def pair(client,item_id,**extra):
    recipe={"name":"csv","definition":{"format":"csv","mappings":[{"result_item_id":item_id,"source":"stress","source_unit":"MPa","dimensions":{"node":"node"}}]},"sample_filename":"sample.csv","sample_content_base64":sample()}
    template={"name":"view","definition":{"widgets":[{"id":"peak","type":"kpi","item_ids":[item_id]}]}}
    recipe.update(extra.get("recipe",{})); template.update(extra.get("template",{}))
    return client.post("/api/semantic-mapping/configurations",json={"recipe":recipe,"template":template})

def test_configuration_is_atomic_cas_linked_and_retains_sample():
    with TestClient(app) as client:
        entry=item(client); before=client.get("/api/semantic-mapping/catalog").json()
        bad=pair(client,entry["id"],template={"definition":{"widgets":[{"id":"bad","type":"kpi","item_ids":["missing"]}]}})
        assert bad.status_code==422
        assert len(client.get("/api/semantic-mapping/catalog").json()["recipes"]) == len(before["recipes"])
        saved=pair(client,entry["id"]); assert saved.status_code==201,saved.text
        body=saved.json(); assert body["recipe"]["definition"]["display_template_id"] == body["template"]["id"]
        opened=client.get(f"/api/semantic-mapping/configurations/{body['recipe']['id']}"); assert opened.status_code==200 and opened.json()["template"]["version"] == body["template"]["version"]
        updated=pair(client,entry["id"],recipe={"id":body["recipe"]["id"],"expected_version":1,"sample_content_base64":None,"sample_filename":None},template={"id":body["template"]["id"],"expected_version":1})
        assert updated.status_code==201 and updated.json()["recipe"]["version"] == 2
        with connect() as conn: assert conn.execute("SELECT sample_bytes FROM semantic_recipe_versions WHERE recipe_id=? AND version=2",[body["recipe"]["id"]]).fetchone()[0]
        stale=pair(client,entry["id"],recipe={"id":body["recipe"]["id"],"expected_version":0},template={"id":body["template"]["id"],"expected_version":0})
        assert stale.status_code==409


def test_failure_after_template_write_rolls_back_both_versions_and_sample():
    with TestClient(app) as client:
        entry = item(client)
        saved = pair(client, entry["id"]).json()
        before = client.get("/api/semantic-mapping/catalog").json()
        invalid_recipe = {
            "id": saved["recipe"]["id"], "expected_version": 1,
            "definition": {"format": "csv", "mappings": [{
                "result_item_id": entry["id"], "source": "missing-column",
                "source_unit": "MPa", "dimensions": {"node": "node"},
            }]},
        }
        failed = pair(client, entry["id"], recipe=invalid_recipe,
                      template={"id": saved["template"]["id"], "expected_version": 1})
        assert failed.status_code == 422, failed.text
        assert client.get("/api/semantic-mapping/catalog").json() == before
        # Template CAS succeeds first; stale recipe CAS must still roll it back.
        stale = pair(client, entry["id"],
                     recipe={"id": saved["recipe"]["id"], "expected_version": 0},
                     template={"id": saved["template"]["id"], "expected_version": 1})
        assert stale.status_code == 409, stale.text
        assert client.get("/api/semantic-mapping/catalog").json() == before
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM semantic_recipe_versions WHERE recipe_id=?", [saved["recipe"]["id"]]).fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM semantic_template_versions WHERE template_id=?", [saved["template"]["id"]]).fetchone()[0] == 1


def test_archiving_preserves_active_import_and_recorded_run():
    with TestClient(app) as client:
        entry = item(client)
        saved = pair(client, entry["id"]).json()
        activated = client.post("/api/semantic-mapping/activate-bundle", json={
            "recipe_id": saved["recipe"]["id"], "recipe_version": 1,
            "template_id": saved["template"]["id"], "template_version": 1,
            "expected_recipe_active_version": None, "expected_template_active_version": None,
        })
        assert activated.status_code == 200, activated.text
        archived = client.post(f"/api/semantic-mapping/items/{entry['id']}/archive", json={"expected_version": 1})
        assert archived.status_code == 200
        imported = client.post("/api/semantic-mapping/import", data={
            "recipe_id": saved["recipe"]["id"], "template_id": saved["template"]["id"],
            "load_case_id": "loadcase-drop-bottom-001",
        }, files={"file": ("sample.csv", base64.b64decode(sample()), "text/csv")})
        assert imported.status_code == 200, imported.text
        run_id = imported.json()["run_id"]
        result = client.get("/api/semantic-mapping/results", params={"run_id": run_id, "load_case_id": "loadcase-drop-bottom-001"})
        assert result.status_code == 200, result.text
        assert result.json()["widgets"][0]["status"] == "READY"
        assert result.json()["widgets"][0]["data"][0]["value"] == 12.5
        assert client.get("/api/semantic-mapping/catalog").json()["items"][0]["lifecycle_status"] == "ARCHIVED"

def test_archive_restore_rejects_new_definition_and_preserves_historic_usage():
    with TestClient(app) as client:
        entry=item(client); saved=pair(client,entry["id"]).json(); item_id=entry["id"]
        archived=client.post(f"/api/semantic-mapping/items/{item_id}/archive",json={"expected_version":1}); assert archived.status_code==200
        with connect() as conn: assert conn.execute("SELECT count(*) FROM audit_events WHERE action='SEMANTIC_ITEM_ARCHIVED'").fetchone()[0] >= 1
        assert client.post(f"/api/semantic-mapping/items/{item_id}/restore",json={"expected_version":1}).status_code==409
        rejected=pair(client,item_id); assert rejected.status_code==422
        historic=client.get(f"/api/semantic-mapping/configurations/{saved['recipe']['id']}"); assert historic.status_code==200 and historic.json()["recipe"]["definition"]["mappings"][0]["result_item_id"] == item_id
        restored=client.post(f"/api/semantic-mapping/items/{item_id}/restore",json={"expected_version":2}); assert restored.status_code==200
        usage=client.get(f"/api/semantic-mapping/items/{item_id}/usage"); assert usage.status_code==200 and usage.json()["recipes"] >= 1 and usage.json()["widgets"] >= 1
        changed=client.post("/api/semantic-mapping/items",json={"id":item_id,"expected_version":3,"definition":{"key":"peak_stress","label":"Peak","kind":"scalar","unit":"mm","dimensions":["node"]}})
        assert changed.status_code==422

def test_import_remaps_display_template_link_and_export_warns_on_old_link():
    with TestClient(app) as client:
        package={"format_version":1,"items":[{"id":"old-item","definition":{"id":"old-item","key":"imported_peak","label":"Imported","kind":"scalar","unit":"MPa","dimensions":[]}}],"templates":[{"id":"old-template","name":"Imported view","definition":{"widgets":[{"id":"w","type":"kpi","item_ids":["old-item"]}]}}],"recipes":[{"id":"old-recipe","name":"Imported csv","definition":{"format":"csv","mappings":[{"result_item_id":"old-item","source":"stress","source_unit":"MPa"}],"display_template_id":"old-template","display_template_version":7}}]}
        imported=client.post("/api/semantic-mapping/import-definitions",json=package); assert imported.status_code==201,imported.text
        recipe_id=next(entry["id"] for entry in imported.json()["created"] if entry.get("definition",{}).get("format") == "csv")
        opened=client.get(f"/api/semantic-mapping/configurations/{recipe_id}"); assert opened.status_code==200,opened.text
        assert opened.json()["template"]["id"] != "old-template" and opened.json()["template"]["version"] == 1
        item_id=item(client)["id"]; saved=pair(client,item_id).json()
        updated=client.post("/api/semantic-mapping/templates",json={"id":saved["template"]["id"],"name":"newer","expected_version":1,"definition":{"widgets":[{"id":"peak","type":"kpi","item_ids":[item_id]}]}}); assert updated.status_code==201
        exported=client.get("/api/semantic-mapping/export"); assert any(row["code"] == "DISPLAY_TEMPLATE_LINK_OMITTED" for row in exported.json()["warnings"])
