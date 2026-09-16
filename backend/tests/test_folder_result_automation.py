import pytest
from fastapi import HTTPException

from app.services import folder_discovery
from app.services.folder_discovery import _attach_result_defaults, _resolved_result_config
from app.services.folder_discovery_plan import build_plan
from app.services import semantic_result_refresh
from app.routers import semantic_mapping as semantic_router


def _node(path, parent, depth):
    return {"relative_path": path, "parent_path": parent, "depth": depth, "name": path.rsplit("/", 1)[-1]}


def _rule(depth, role, keyword, analysis_type=""):
    return {"depth": depth, "role": role, "keyword": keyword, "delimiter": "_", "code_token": 1,
            "name_from_token": 2, "analysis_type": analysis_type}


def test_result_rule_config_is_carried_to_the_apply_preview_row():
    config = {"recipe_ids": ["recipe-drop"], "template_id": "template-drop"}
    rules = [_rule(0, "PROJECT", "P_"), _rule(1, "REQUEST", "R_"),
             _rule(2, "LOAD_CASE", "L_", "DROP"), {**_rule(3, "RESULTS", "results"), "delimiter": "", "result_config": config, "result_config_source": "RULE"}]
    nodes = [_node("P_Project", None, 0), _node("P_Project/R_Request", "P_Project", 1),
             _node("P_Project/R_Request/L_Load", "P_Project/R_Request", 2),
             _node("P_Project/R_Request/L_Load/results", "P_Project/R_Request/L_Load", 3)]
    result = build_plan(nodes, rules, "root", [], [], [])
    row = next(entry for entry in result["rows"] if entry["role_kind"] == "RESULTS")
    assert row["result_config"] == config
    assert row["result_config_source"] == "RULE"
    assert row["binding_status"] == "WILL_CREATE"


def test_analysis_type_default_is_only_used_when_rule_has_no_config():
    default = {"recipe_ids": ["recipe-drop"]}
    assert _resolved_result_config({"analysis_type": "DROP"}, {"DROP": default}) == (default, "ANALYSIS_TYPE_DEFAULT")
    explicit = {"recipe_ids": ["recipe-specific"]}
    assert _resolved_result_config({"analysis_type": "DROP", "result_config": explicit}, {"DROP": default}) == (explicit, "RULE")


def test_load_case_analysis_default_is_applied_to_unconfigured_result_row():
    plan = {"rows": [{"role_kind": "LOAD_CASE", "target_id": "load", "analysis_type": "DROP"},
                     {"role_kind": "RESULTS", "load_case_id": "load", "result_config": None,
                      "result_config_source": "NONE", "binding_status": "CONFIG_REQUIRED"}]}
    result = _attach_result_defaults(plan, {"DROP": {"recipe_ids": ["recipe-drop"]}})
    assert result["rows"][1]["result_config_source"] == "ANALYSIS_TYPE_DEFAULT"
    assert result["rows"][1]["binding_status"] == "WILL_CREATE"


def test_result_refresh_discovers_only_direct_results_bindings(monkeypatch):
    monkeypatch.setattr(semantic_result_refresh.semantic_mapping, "semantic_bindings", lambda _conn: [
        {"id": "input", "relative_path": "a/input", "load_case_id": "load", "role": "INPUT"},
        {"id": "result-b", "relative_path": "a/results-b", "load_case_id": "load", "role": "RESULTS"},
        {"id": "result-a", "relative_path": "a/results-a", "load_case_id": "load", "role": "RESULTS"},
        {"id": "other", "relative_path": "a/other", "load_case_id": "other", "role": "RESULTS"},
    ])
    assert semantic_result_refresh.load_case_binding_ids(object(), "load") == ["result-a", "result-b"]


def test_existing_result_path_with_a_different_owner_is_never_reused(monkeypatch):
    monkeypatch.setattr(folder_discovery.mapping_repository, "semantic_bindings", lambda _conn: [
        {"id": "binding", "relative_path": "project/results", "load_case_id": "other", "role": "RESULTS"},
    ])
    monkeypatch.setattr(folder_discovery.mapping_repository, "binding", lambda _conn, _id: {
        "id": "binding", "project_id": "project", "request_id": "request", "load_case_id": "other", "role": "RESULTS",
    })
    item = {"role_kind": "RESULTS", "relative_path": "project/results", "project_id": "project",
            "request_id": "request", "load_case_id": "load"}
    with pytest.raises(HTTPException) as error:
        folder_discovery._result_binding(object(), item, None, "actor")
    assert error.value.detail["code"] == "SEMANTIC_BINDING_TARGET_CONFLICT"


def test_existing_input_binding_is_not_reused_as_a_result_binding(monkeypatch):
    monkeypatch.setattr(folder_discovery.mapping_repository, "semantic_bindings", lambda _conn: [
        {"id": "binding", "relative_path": "project/results", "load_case_id": "load", "role": "INPUT"},
    ])
    monkeypatch.setattr(folder_discovery.mapping_repository, "binding", lambda _conn, _id: {
        "id": "binding", "project_id": "project", "request_id": "request", "load_case_id": "load", "role": "INPUT",
    })
    item = {"role_kind": "RESULTS", "relative_path": "project/results", "project_id": "project",
            "request_id": "request", "load_case_id": "load"}
    with pytest.raises(HTTPException) as error:
        folder_discovery._result_binding(object(), item, None, "actor")
    assert error.value.detail["code"] == "SEMANTIC_BINDING_TARGET_CONFLICT"


def test_bulk_preview_reports_inactive_recipe_before_apply(monkeypatch):
    class Cursor:
        def __init__(self, records):
            self.description = [(key,) for key in (records[0] if records else [])]
            self._records = records

        def fetchall(self):
            return [tuple(record.values()) for record in self._records]

    class Connection:
        def execute(self, query, _args=None):
            if "folder_discovery_registry" in query:
                return Cursor([{"id": "registry", "relative_path": "project/results", "parent_target_id": "load"}])
            if "semantic_folder_bindings" in query:
                return Cursor([])
            raise AssertionError(query)

    monkeypatch.setattr(folder_discovery.mapping_repository, "binding_lineage", lambda *_args: ("project", "request", "load"))
    monkeypatch.setattr(folder_discovery.mapping_repository, "version", lambda *_args, **_kwargs: None)
    result = folder_discovery.result_config_preview(Connection(), "root", [{"registry_id": "registry", "result_config": {"recipe_ids": ["inactive"]}}])
    assert result["can_apply"] is False
    assert result["items"] == [{"registry_id": "registry", "status": "CONFIG_CONFLICT", "code": "SEMANTIC_RECIPE_NOT_ACTIVE"}]


def test_same_result_folder_rules_with_different_policies_conflict():
    options = {"roles": [
        {"key": "PROJECT", "label": "Project", "kind": "PROJECT", "active": True},
        {"key": "REQUEST", "label": "Request", "kind": "REQUEST", "active": True},
        {"key": "LOAD_CASE", "label": "Load", "kind": "LOAD_CASE", "active": True},
        {"key": "RESULT_A", "label": "Result A", "kind": "RESULTS", "active": True},
        {"key": "RESULT_B", "label": "Result B", "kind": "RESULTS", "active": True},
        {"key": "INPUT", "label": "Input", "kind": "INPUT", "active": True},
    ], "analysis_types": [{"key": "DROP", "label": "Drop", "active": True}]}
    result = build_plan([_node("results", None, 0)], [
        {**_rule(0, "RESULT_A", "results"), "delimiter": "", "result_config": {"recipe_ids": ["a"]}},
        {**_rule(0, "RESULT_B", "results"), "delimiter": "", "result_config": {"recipe_ids": ["b"]}},
    ], "root", [], [], [], options)
    assert result["can_apply"] is False
    assert all(row["status"] == "CONFLICT" for row in result["rows"])


def test_existing_binding_policy_wins_even_if_a_new_rule_policy_is_unavailable(monkeypatch):
    plan = {"rows": [{"role_kind": "RESULTS", "relative_path": "project/results", "project_id": "project",
                      "request_id": "request", "load_case_id": "load", "result_config": {"recipe_ids": ["new"]},
                      "result_config_source": "RULE", "binding_status": "WILL_CREATE", "status": "CREATE"}],
            "summary": {"conflicts": 0}, "can_apply": True}
    monkeypatch.setattr(folder_discovery.mapping_repository, "semantic_bindings", lambda _conn: [
        {"id": "manual", "relative_path": "project/results"},
    ])
    monkeypatch.setattr(folder_discovery.mapping_repository, "binding", lambda _conn, _id: {
        "id": "manual", "role": "RESULTS", "project_id": "project", "request_id": "request", "load_case_id": "load",
        "recipe_ids_json": "[\"manual\"]", "template_id": "historic", "revision": 4,
    })
    monkeypatch.setattr(folder_discovery.mapping_repository, "version", lambda *_args, **_kwargs: None)
    result = folder_discovery._validate_result_policies(object(), folder_discovery._attach_existing_result_bindings(object(), plan))
    row = result["rows"][0]
    assert row["result_config"] == {"recipe_ids": ["manual"], "template_id": "historic"}
    assert row["result_config_source"] == "EXISTING_BINDING"
    assert row["binding_status"] == "REUSE" and row["status"] == "CREATE" and result["can_apply"]


def test_snapshot_refresh_rejects_a_rebound_binding_before_file_access(monkeypatch):
    class Context:
        def __enter__(self): return object()
        def __exit__(self, *_args): return False

    snapshot = {"id": "binding", "role": "RESULTS", "load_case_id": "load-a", "revision": 1, "relative_path": "a/results"}
    monkeypatch.setattr(semantic_router, "connect", lambda: Context())
    monkeypatch.setattr(semantic_router.mapping_repository, "binding", lambda *_args: {**snapshot, "role": "INPUT", "load_case_id": "load-b", "revision": 2})
    with pytest.raises(HTTPException) as error:
        semantic_router._refresh_binding_snapshot(snapshot, object())
    assert error.value.detail["code"] == "SEMANTIC_BINDING_STALE"


def test_import_transaction_snapshot_guard_rejects_rebind_after_precheck(monkeypatch):
    snapshot = {"id": "binding", "role": "RESULTS", "project_id": "project", "request_id": "request",
                "load_case_id": "load-a", "revision": 1, "relative_path": "a/results"}
    # This models the reconnect committing after the folder scan precheck and
    # immediately before canonical import invokes its in-transaction guard.
    monkeypatch.setattr(semantic_router.mapping_repository, "binding_revision", lambda *_args: (2,))
    monkeypatch.setattr(semantic_router.mapping_repository, "binding", lambda *_args: {**snapshot, "revision": 2, "load_case_id": "load-b"})
    with pytest.raises(HTTPException) as error:
        semantic_router._assert_binding_snapshot(object(), snapshot, lock=True)
    assert error.value.detail["code"] == "SEMANTIC_BINDING_STALE"
