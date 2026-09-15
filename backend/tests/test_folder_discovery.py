from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.security import hash_password
from app.services import folder_discovery_plan as plan
from app.services import folder_discovery_scan as scan

pytestmark = pytest.mark.duckdb_integration

def node(path, parent, depth):
    return {"relative_path": path, "parent_path": parent, "name": path.rsplit("/", 1)[-1], "depth": depth, "file_count": 0, "extensions": [], "identity": "test"}
def rule(depth, role, prefix="", analysis_type=""):
    return {"depth":depth,"role":role,"prefix":prefix,"delimiter":"_","code_token":2,"name_from_token":3,"analysis_type":analysis_type}

def test_scan_selected_folder_and_global_entry_limit(tmp_path, monkeypatch):
    root=tmp_path/"root"; leaf=root/"P_001_Project"/"R_002_Request"/"L_003_Load"; leaf.mkdir(parents=True); (leaf/"input.H3D").write_text("not parsed")
    result=scan.scan(root,"P_001_Project")
    assert result["status"] == "COMPLETE" and result["nodes"][0]["relative_path"] == "P_001_Project" and result["nodes"][-1]["extensions"] == [".h3d"]
    monkeypatch.setattr(scan,"MAX_ENTRIES",1); (root/"other").mkdir(); limited=scan.scan(root,"")
    assert limited["status"] == "INCOMPLETE" and limited["issues"][0]["code"] == "ENTRY_LIMIT"

@pytest.mark.parametrize("value",["../outside","/outside","a/../../b"])
def test_relative_paths_cannot_escape_root(tmp_path,value):
    with pytest.raises(Exception): scan.target(tmp_path,value)

def test_token_zero_and_missing_token():
    assert plan.extract("PRJ_001_Display_Alpha",{"delimiter":"_","code_token":0,"name_from_token":3}) == ("PRJ_001_Display_Alpha","Display_Alpha")
    with pytest.raises(ValueError): plan.extract("PRJ_001",{"delimiter":"_","code_token":4,"name_from_token":1})

def test_inherited_parent_scopes_through_container():
    nodes=[node("P_01_Project",None,0),node("P_01_Project/container","P_01_Project",1),node("P_01_Project/container/R_77_Request","P_01_Project/container",2),node("P_01_Project/container/R_77_Request/L_01_Load","P_01_Project/container/R_77_Request",3)]
    result=plan.build_plan(nodes,[rule(0,"PROJECT","P_"),rule(2,"REQUEST","R_"),rule(3,"LOAD_CASE","L_","DROP")],"root",[],[],[])
    request=next(row for row in result["rows"] if row["role"]=="REQUEST"); load=next(row for row in result["rows"] if row["role"]=="LOAD_CASE")
    assert result["can_apply"] and load["parent_target_id"] == request["target_id"]

def test_same_folder_multi_role_and_duplicate_conflicts():
    same=plan.build_plan([node("P_01_Project",None,0)],[rule(0,"PROJECT","P_"),rule(0,"REQUEST","P_")],"root",[],[],[])
    assert [row["role"] for row in same["rows"]] == ["PROJECT","REQUEST"] and same["rows"][1]["parent_target_id"] == same["rows"][0]["target_id"]
    duplicate=plan.build_plan([node("P_01_One",None,0),node("P_01_Two",None,0),node("P_only",None,0)],[rule(0,"PROJECT","P_")],"root",[],[],[])
    assert not duplicate["can_apply"] and duplicate["summary"]["conflicts"] >= 2

def test_confirmed_registry_only_and_boundary_conflicts():
    existing={"relative_path":"P_01_Project","role":"PROJECT","scope_key":"","code":"01","name":"Project","analysis_type":"","target_id":"project-confirmed","target_valid":True}
    keep=plan.build_plan([node("P_01_Project",None,0)],[rule(0,"PROJECT","P_")],"root",[existing],[],[])
    assert keep["rows"][0]["status"] == "KEEP" and keep["rows"][0]["target_id"] == "project-confirmed"
    candidate=node("P_01_Project",None,0)
    legacy=plan.build_plan([candidate],[rule(0,"PROJECT","P_")],"root",[],["P_01_Project/old"],[])
    semantic=plan.build_plan([candidate],[rule(0,"PROJECT","P_")],"root",[],[],[{"relative_path":"P_01_Project/x","project_id":"other","request_id":None,"load_case_id":None}])
    assert legacy["rows"][0]["status"] == "CONFLICT" and semantic["rows"][0]["status"] == "CONFLICT"

def test_normalized_path_collision_and_whitespace_label_are_explicit():
    first=node("P_001_Alpha",None,0); second=node("Ｐ_001_Alpha",None,0)
    collision=plan.build_plan([first,second],[rule(0,"PROJECT")],"root",[],[],[])
    assert not collision["can_apply"] and all(row["status"] == "CONFLICT" for row in collision["rows"])
    spaced_rule=rule(0,"PROJECT","P_"); spaced_rule.update(code_token=0,name_from_token=2)
    spaced=plan.build_plan([node("P_ Alpha",None,0)],[spaced_rule],"root",[],[],[])
    assert spaced["rows"][0]["name"] == "Alpha"

def test_normalized_spelling_cannot_alias_confirmed_registry_target():
    existing={"relative_path":"P_001_Alpha","role":"PROJECT","scope_key":"","code":"001","name":"Alpha","analysis_type":"","target_id":"project-confirmed","target_valid":True}
    result=plan.build_plan([node("Ｐ_001_Alpha",None,0)],[rule(0,"PROJECT")],"root",[existing],[],[])
    assert result["rows"][0]["status"] == "CONFLICT"

def test_api_scan_preview_apply_reapply_rules_cas_and_global_admin(tmp_path, monkeypatch):
    root=tmp_path/"spdm"; (root/"P_001_Project"/"R_002_Request"/"L_003_Load").mkdir(parents=True)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT",str(root)); monkeypatch.setenv("AUTH_MODE","password"); monkeypatch.setenv("AUTH_SECRET_KEY","folder-discovery-test-secret-key-at-least-32")
    initialize_database(); stamp=datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        for user,admin in (("folder-global",True),("folder-local",False)):
            conn.execute("INSERT INTO users(id,username,password_hash,display_name,legacy_role,account_status,is_global_admin,is_active,created_at,updated_at) VALUES(?,?,?,?, 'admin','ACTIVE',?,?,?,?)",[user,user,hash_password("correct-horse-battery-staple"),user,admin,True,stamp,stamp])
    with TestClient(app) as client:
        def headers(user):
            response=client.post("/api/auth/login",json={"username":user,"password":"correct-horse-battery-staple"}); assert response.status_code==200,response.text
            return {"Authorization":f"Bearer {response.json()['access_token']}"}
        global_headers, local_headers=headers("folder-global"),headers("folder-local")
        assert client.post("/api/folder-discovery/scan",headers=local_headers,json={}).status_code==403
        surveyed=client.post("/api/folder-discovery/scan",headers=global_headers,json={"relative_path":"P_001_Project"}); assert surveyed.status_code==200,surveyed.text
        rules=[rule(0,"PROJECT","P_"),rule(1,"REQUEST","R_"),rule(2,"LOAD_CASE","L_","DROP")]
        preview=client.post("/api/folder-discovery/preview",headers=global_headers,json={"scan_id":surveyed.json()["id"],"rules":rules}); assert preview.status_code==200,preview.text
        applied=client.post("/api/folder-discovery/apply",headers=global_headers,json={"preview_id":preview.json()["id"]}); assert applied.status_code==200,applied.text
        assert applied.json()["created"] == {"projects":1,"requests":1,"load_cases":1}
        assert client.post("/api/folder-discovery/apply",headers=global_headers,json={"preview_id":preview.json()["id"]}).json()==applied.json()
        with connect() as conn:
            project_id,request_id,load_id=conn.execute("SELECT p.id,r.id,l.id FROM projects p JOIN analysis_requests r ON r.project_id=p.id JOIN load_cases l ON l.request_id=r.id WHERE p.name='Project'").fetchone()
            assert conn.execute("SELECT count(*) FROM project_memberships WHERE project_id=?",[project_id]).fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM quality_thresholds WHERE project_id=?",[project_id]).fetchone()[0] >= 1
            assert conn.execute("SELECT count(*) FROM workspace_layouts").fetchone()[0] >= 1
        kept_preview=client.post("/api/folder-discovery/preview",headers=global_headers,json={"scan_id":surveyed.json()["id"],"rules":rules}); assert kept_preview.status_code==200
        assert all(row["status"] == "KEEP" for row in kept_preview.json()["rows"])
        saved=client.put("/api/folder-discovery/rules",headers=global_headers,json={"relative_path":"P_001_Project","rules":rules,"expected_revision":0}); assert saved.status_code==200,saved.text
        assert client.post("/api/folder-discovery/apply",headers=global_headers,json={"preview_id":kept_preview.json()["id"]}).status_code==409
        assert client.put("/api/folder-discovery/rules",headers=global_headers,json={"relative_path":"P_001_Project","rules":rules,"expected_revision":0}).status_code==409
        (root/"P_001_Project"/"R_002_Request"/"L_004_New").mkdir()
        fresh=client.post("/api/folder-discovery/scan",headers=global_headers,json={"relative_path":"P_001_Project"}).json()
        new_preview=client.post("/api/folder-discovery/preview",headers=global_headers,json={"scan_id":fresh["id"],"rules":rules}); assert new_preview.status_code==200
        new_apply=client.post("/api/folder-discovery/apply",headers=global_headers,json={"preview_id":new_preview.json()["id"]}); assert new_apply.json()["created"] == {"projects":0,"requests":0,"load_cases":1}
        with connect() as conn:
            assert conn.execute("SELECT request_id FROM load_cases WHERE name='New'").fetchone()[0] == request_id
        import app.services.folder_discovery as service
        original=service.configured_root; other=tmp_path/"other"; other.mkdir()
        monkeypatch.setattr(service,"configured_root",lambda _conn: other)
        assert client.post("/api/folder-discovery/preview",headers=global_headers,json={"scan_id":fresh["id"],"rules":rules}).status_code == 409
        monkeypatch.setattr(service,"configured_root",original)
        (root/"P_001_Project"/"R_002_Request"/"L_005_Audit").mkdir()
        rollback_scan=client.post("/api/folder-discovery/scan",headers=global_headers,json={"relative_path":"P_001_Project"}).json()
        rollback_preview=client.post("/api/folder-discovery/preview",headers=global_headers,json={"scan_id":rollback_scan["id"],"rules":rules}).json()
        import app.routers.folder_discovery as router
        original_audit=router.write_audit_event
        monkeypatch.setattr(router,"write_audit_event",lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("audit failed")))
        before=2
        with pytest.raises(RuntimeError, match="audit failed"):
            client.post("/api/folder-discovery/apply",headers=global_headers,json={"preview_id":rollback_preview["id"]})
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM load_cases WHERE request_id=?",[request_id]).fetchone()[0] == before
            assert conn.execute("SELECT applied_json FROM folder_discovery_previews WHERE id=?",[rollback_preview["id"]]).fetchone()[0] is None
        monkeypatch.setattr(router,"write_audit_event",original_audit)
