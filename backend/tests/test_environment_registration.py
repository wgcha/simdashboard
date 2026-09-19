from app.services.folder_discovery_environment import _interpret, _recompute_context


def test_manual_parent_assignments_propagate_through_neutral_containers():
    nodes = [
        {"relative_path": "neutral", "parent_path": None, "name": "neutral", "role_kind": None},
        {"relative_path": "neutral/p", "parent_path": "neutral", "name": "p", "role_kind": "PROJECT"},
        {"relative_path": "neutral/p/box", "parent_path": "neutral/p", "name": "box", "role_kind": None},
        {"relative_path": "neutral/p/box/r", "parent_path": "neutral/p/box", "name": "r", "role_kind": "REQUEST"},
        {"relative_path": "neutral/p/box/r/c", "parent_path": "neutral/p/box/r", "name": "c", "role_kind": "SIMULATION_CASE"},
    ]
    _recompute_context(nodes, "root", "USAGE", None, None)
    project, request, case = nodes[1], nodes[3], nodes[4]
    assert request["parent_context"] == project["target_id"]
    assert case["project_id"] == project["target_id"]
    assert case["request_id"] == request["target_id"]


def test_recompute_preserves_explicit_link_target():
    nodes = [
        {"relative_path": "p", "parent_path": None, "name": "p", "role_kind": "PROJECT", "target_id": "project-linked"},
        {"relative_path": "p/r", "parent_path": "p", "name": "r", "role_kind": "REQUEST", "target_id": "request-linked"},
        {"relative_path": "p/r/c", "parent_path": "p/r", "name": "c", "role_kind": "SIMULATION_CASE"},
    ]
    _recompute_context(nodes, "root", "USAGE", None, None)
    assert nodes[2]["request_id"] == "request-linked"
    assert nodes[2]["project_id"] == "project-linked"


def test_conflicting_profile_matches_require_review_not_container_passthrough():
    nodes = _interpret(
        [{"relative_path": "Project_1", "parent_path": None, "name": "Project_1", "depth": 0}],
        "root", "USAGE", None, None,
        {"rules": [
            {"role_kind": "PROJECT", "pattern": "Project*", "match_mode": "glob"},
            {"role_kind": "REQUEST", "pattern": "Project*", "match_mode": "glob"},
        ]},
    )
    assert nodes[0]["role_kind"] is None
    assert nodes[0]["status"] == "UNRESOLVED"
