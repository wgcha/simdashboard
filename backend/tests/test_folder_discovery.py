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


def test_empty_or_missing_delimiter_keeps_the_whole_folder_name_without_code():
    rule = {"delimiter":"", "code_token":2, "name_from_token":3}
    assert plan.extract("조립 결과 폴더", rule) == ("", "조립 결과 폴더")
    assert plan.extract("PRJ-001", {**rule, "delimiter":"_"}) == ("", "PRJ-001")


def test_configured_delimiter_must_be_present_before_a_rule_matches():
    missing = plan.build_plan([node("P-001-Project", None, 0)], [rule(0, "PROJECT", "P-")], "root", [], [], [])
    assert missing["rows"] == [] and not missing["can_apply"]
    whole_name = rule(0, "PROJECT", "P-")
    whole_name["delimiter"] = ""
    matched = plan.build_plan([node("P-001-Project", None, 0)], [whole_name], "root", [], [], [])
    assert matched["rows"][0]["code"] == "" and matched["rows"][0]["name"] == "P-001-Project"


def test_excluding_a_subtree_removes_collisions_and_retains_keep_rows():
    nodes = [node("P_001_One", None, 0), node("P_001_Two", None, 0)]
    data = {"nodes": nodes, "root_key": "root"}
    # Build through a small service seam so the behavior includes path validation and row assembly.
    from app.services import folder_discovery as service
    class Connection:
        def execute(self, *_args, **_kwargs):
            class Result:
                def fetchall(self): return []
            return Result()
    original_registry, original_bindings = service.registry, service.mapping_repository.semantic_bindings
    service.registry = lambda *_args: []
    service.mapping_repository.semantic_bindings = lambda *_args: []
    try:
        result = service.proposal(Connection(), data, [rule(0, "PROJECT", "P_")], options=plan._default_options(), excluded_paths=["P_001_Two"])
    finally:
        service.registry, service.mapping_repository.semantic_bindings = original_registry, original_bindings
    assert result["can_apply"] and result["summary"]["excluded"] == 1
    assert [row["status"] for row in result["rows"]] == ["CREATE", "EXCLUDED"]
    assert result["rows"][1]["excluded_by"] == "P_001_Two"


def test_exclusions_are_boundary_aware_and_root_can_exclude_everything(monkeypatch):
    from app.services import folder_discovery as service
    class Connection:
        def execute(self, *_args, **_kwargs):
            class Result:
                def fetchall(self): return []
            return Result()
    monkeypatch.setattr(service, "registry", lambda *_args: [])
    monkeypatch.setattr(service.mapping_repository, "semantic_bindings", lambda *_args: [])
    data = {"root_key": "root", "nodes": [node("P_001_One", None, 0), node("Ｐ_001_One", None, 0), node("P_002_Good", None, 0)]}
    result = service.proposal(Connection(), data, [rule(0, "PROJECT", "P_")], options=plan._default_options(), excluded_paths=["p_001_one"])
    assert result["can_apply"] and result["excluded_paths"] == ["Ｐ_001_One"]
    assert [row["status"] for row in result["rows"]] == ["EXCLUDED", "EXCLUDED", "CREATE"]
    root_data = {"root_key": "root", "nodes": [node("", None, 0), node("child", "", 1)]}
    root_rule = rule(0, "PROJECT")
    root_rule.update(delimiter="", prefix="")
    root = service.proposal(Connection(), root_data, [root_rule], options=plan._default_options(), excluded_paths=[""])
    assert root["excluded_paths"] == [""] and not root["can_apply"]
    assert all(row["status"] == "EXCLUDED" and row["excluded_by"] == "" for row in root["rows"])


def test_api_exclusions_are_validated_recomputed_and_never_materialized(tmp_path, monkeypatch):
    root = tmp_path / "spdm"
    (root / "P_001_One").mkdir(parents=True)
    (root / "P_001_Two").mkdir()
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "folder-exclusion-test-secret-key-at-least-32")
    initialize_database()
    stamp = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute("INSERT INTO users(id,username,password_hash,display_name,legacy_role,account_status,is_global_admin,is_active,created_at,updated_at) VALUES(?,?,?,?, 'admin','ACTIVE',TRUE,TRUE,?,?)",
                     ["folder-exclusion-admin", "folder-exclusion-admin", hash_password("correct-horse-battery-staple"), "admin", stamp, stamp])
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "folder-exclusion-admin", "password": "correct-horse-battery-staple"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        surveyed = client.post("/api/folder-discovery/scan", headers=headers, json={})
        assert surveyed.status_code == 200, surveyed.text
        payload = {"scan_id": surveyed.json()["id"], "rules": [rule(1, "PROJECT", "P_")], "excluded_paths": ["P_001_Two"]}
        preview = client.post("/api/folder-discovery/preview", headers=headers, json=payload)
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["can_apply"] and body["excluded_paths"] == ["P_001_Two"] and body["summary"]["excluded"] == 1
        assert [row["status"] for row in body["rows"]] == ["CREATE", "EXCLUDED"]
        applied = client.post("/api/folder-discovery/apply", headers=headers, json={"preview_id": body["id"]})
        assert applied.status_code == 200 and applied.json()["excluded_count"] == 1
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM projects WHERE name IN ('One','Two')").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM folder_discovery_registry").fetchone()[0] == 1
        forged = client.post("/api/folder-discovery/preview", headers=headers,
                             json={**payload, "excluded_paths": ["not-scanned"]})
        assert forged.status_code == 422 and forged.json()["detail"]["code"] == "EXCLUSION_PATH_INVALID"
        restored = client.post("/api/folder-discovery/preview", headers=headers, json=payload)
        assert restored.status_code == 200
        assert [row["status"] for row in restored.json()["rows"]] == ["KEEP", "EXCLUDED"]
        assert client.post("/api/folder-discovery/apply", headers=headers, json={"preview_id": restored.json()["id"]}).json()["kept_count"] == 1
        all_excluded = client.post("/api/folder-discovery/preview", headers=headers,
                                   json={**payload, "excluded_paths": ["P_001_One", "P_001_Two"]})
        assert all_excluded.status_code == 200 and not all_excluded.json()["can_apply"]
        assert client.post("/api/folder-discovery/apply", headers=headers, json={"preview_id": all_excluded.json()["id"]}).status_code == 409
        stale = client.post("/api/folder-discovery/preview", headers=headers, json=payload).json()
        (root / "P_003_Changed").mkdir()
        assert client.post("/api/folder-discovery/apply", headers=headers, json={"preview_id": stale["id"]}).status_code == 409


def test_keywords_match_anywhere_case_insensitively_and_repeated_load_case_codes_are_allowed():
    nodes = [node("prefixPROJECTsuffix", None, 0), node("prefixPROJECTsuffix/Request", "prefixPROJECTsuffix", 1),
             node("prefixPROJECTsuffix/Request/Case_01_A", "prefixPROJECTsuffix/Request", 2),
             node("prefixPROJECTsuffix/Request/Case_01_B", "prefixPROJECTsuffix/Request", 2)]
    rules = [rule(0, "PROJECT", "project"), rule(1, "REQUEST", "request"), rule(2, "LOAD_CASE", "case_", "DROP")]
    rules[0]["delimiter"] = rules[1]["delimiter"] = ""
    result = plan.build_plan(nodes, rules, "root", [], [], [])
    loads = [row for row in result["rows"] if row["role_kind"] == "LOAD_CASE"]
    assert result["can_apply"] and [row["code"] for row in loads] == ["01", "01"]


def test_custom_load_case_and_metadata_roles_resolve_future_handoff_ids():
    options = {"roles": [
        {"key":"PROJECT", "label":"프로젝트", "kind":"PROJECT", "active":True},
        {"key":"REQUEST", "label":"의뢰", "kind":"REQUEST", "active":True},
        {"key":"THERMAL_CASE", "label":"열해석", "kind":"LOAD_CASE", "active":True},
        {"key":"RESULT_FOLDER", "label":"결과", "kind":"RESULTS", "active":True},
        {"key":"INPUT", "label":"입력", "kind":"INPUT", "active":True},
    ], "analysis_types": [{"key":"THERMAL", "label":"열", "active":True}]}
    nodes = [node("p_001_Project", None, 0), node("p_001_Project/r_002_Request", "p_001_Project", 1),
             node("p_001_Project/r_002_Request/c_003_Case", "p_001_Project/r_002_Request", 2),
             node("p_001_Project/r_002_Request/c_003_Case/results", "p_001_Project/r_002_Request/c_003_Case", 3)]
    result = plan.build_plan(nodes, [rule(0,"PROJECT","p_"), rule(1,"REQUEST","r_"), rule(2,"THERMAL_CASE","c_","THERMAL"),
                                     {**rule(3,"RESULT_FOLDER","results"), "delimiter":""}], "root", [], [], [], options)
    metadata = next(row for row in result["rows"] if row["role"] == "RESULT_FOLDER")
    load = next(row for row in result["rows"] if row["role"] == "THERMAL_CASE")
    assert result["can_apply"] and metadata["role_kind"] == "RESULTS"
    assert metadata["load_case_id"] == load["target_id"] and metadata["request_id"] and metadata["project_id"]

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
        catalog=client.get("/api/folder-discovery/catalog",headers=global_headers); assert catalog.status_code==200,catalog.text
        catalog_body=catalog.json(); assert {entry["key"] for entry in catalog_body["roles"]} >= {"PROJECT","REQUEST","LOAD_CASE","RESULTS","INPUT"}
        custom_catalog={**catalog_body,"expected_revision":catalog_body["revision"],
                        "roles":[*catalog_body["roles"],{"key":"THERMAL_CASE","label":"열 해석","kind":"LOAD_CASE","active":True},
                                 {"key":"RESULT_FOLDER","label":"결과","kind":"RESULTS","active":True}],
                        "analysis_types":[*catalog_body["analysis_types"],{"key":"THERMAL","label":"열","active":True}]}
        catalog_saved=client.put("/api/folder-discovery/catalog",headers=global_headers,json=custom_catalog); assert catalog_saved.status_code==200,catalog_saved.text
        assert catalog_saved.json()["revision"] == catalog_body["revision"] + 1
        assert client.put("/api/folder-discovery/catalog",headers=global_headers,json=custom_catalog).status_code==409
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
        (root/"P_004_Custom"/"R_005_Request"/"T_006_Case"/"results").mkdir(parents=True)
        custom_scan=client.post("/api/folder-discovery/scan",headers=global_headers,json={}).json()
        custom_rules=[rule(1,"PROJECT","P_004_"),rule(2,"REQUEST","R_005_"),rule(3,"THERMAL_CASE","T_006_","THERMAL"),{**rule(4,"RESULT_FOLDER","results"), "delimiter":""}]
        custom_preview=client.post("/api/folder-discovery/preview",headers=global_headers,json={"scan_id":custom_scan["id"],"rules":custom_rules}); assert custom_preview.status_code==200 and custom_preview.json()["can_apply"],custom_preview.text
        custom_apply=client.post("/api/folder-discovery/apply",headers=global_headers,json={"preview_id":custom_preview.json()["id"]}); assert custom_apply.status_code==200,custom_apply.text; assert custom_apply.json()["created"] == {"projects":1,"requests":1,"load_cases":1}
        assert next(row for row in custom_preview.json()["rows"] if row["role"]=="RESULT_FOLDER")["role_kind"] == "RESULTS"
        custom_repeat=client.post("/api/folder-discovery/preview",headers=global_headers,json={"scan_id":custom_scan["id"],"rules":custom_rules}); assert all(row["status"]=="KEEP" for row in custom_repeat.json()["rows"])
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


def test_duckdb_0027_registry_upgrade_preserves_rows_and_catalog_changes():
    import duckdb
    from app.database import ensure_folder_discovery_schema
    with duckdb.connect(":memory:") as conn:
        conn.execute("CREATE TABLE folder_discovery_registry (id VARCHAR PRIMARY KEY, root_key VARCHAR NOT NULL, relative_path VARCHAR NOT NULL, role VARCHAR NOT NULL, scope_key VARCHAR NOT NULL, code VARCHAR NOT NULL, name VARCHAR NOT NULL, analysis_type VARCHAR NOT NULL DEFAULT '', parent_target_id VARCHAR, target_id VARCHAR NOT NULL UNIQUE, created_at TIMESTAMP NOT NULL, UNIQUE(root_key,role,scope_key,code), UNIQUE(root_key,relative_path,role))")
        conn.execute("INSERT INTO folder_discovery_registry VALUES('old','root','legacy','PROJECT','','001','Existing','','','project-id',CURRENT_TIMESTAMP)")
        ensure_folder_discovery_schema(conn)
        assert conn.execute("SELECT role,role_kind,code,name,target_id FROM folder_discovery_registry").fetchone() == ('PROJECT','PROJECT','001','Existing','project-id')
        conn.execute("UPDATE folder_discovery_catalog SET revision=2, analysis_types_json='[]'")
        ensure_folder_discovery_schema(conn)
        assert conn.execute("SELECT revision,analysis_types_json FROM folder_discovery_catalog").fetchone() == (2,'[]')
        conn.execute("INSERT INTO folder_discovery_registry VALUES('new','root','whole-name','PROJECT','PROJECT','',NULL,'Whole name','','','new-id',CURRENT_TIMESTAMP)")
        assert conn.execute("SELECT count(*) FROM folder_discovery_registry").fetchone()[0] == 2


def test_project_code_collision_across_custom_roles_is_rejected():
    options = plan._default_options()
    options['roles'].append(dict(key='CUSTOM_PROJECT', label='Other project', kind='PROJECT', active=True))
    nodes = [node('P_001_One',None,0),node('Q_001_Two',None,0)]
    result = plan.build_plan(nodes,[rule(0,'PROJECT','P_'),rule(0,'CUSTOM_PROJECT','Q_')],'root',[],[],[],options)
    assert not result['can_apply']
    assert all(row['status']=='CONFLICT' for row in result['rows'])


def test_excluding_registered_project_does_not_release_its_code(monkeypatch):
    from app.services import folder_discovery as service
    class Connection:
        def execute(self, *_args, **_kwargs):
            class Result:
                def fetchall(self): return []
            return Result()
    registered = dict(relative_path='P_001_One', role='PROJECT', role_kind='PROJECT', code='001', name='One',
                      scope_key='', target_id='existing-project', target_valid=True, analysis_type='')
    monkeypatch.setattr(service, 'registry', lambda *_args: [registered])
    monkeypatch.setattr(service.mapping_repository, 'semantic_bindings', lambda *_args: [])
    data = dict(root_key='root', nodes=[node('P_001_One',None,0),node('P_001_Two',None,0)])
    result = service.proposal(Connection(), data, [rule(0,'PROJECT','P_')], options=plan._default_options(), excluded_paths=['P_001_One'])
    assert not result['can_apply']
    assert [row['status'] for row in result['rows']] == ['EXCLUDED','CONFLICT']
