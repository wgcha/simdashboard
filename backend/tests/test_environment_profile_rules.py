import pytest

from app.services.environment_folder_profiles import resolve_role, validate_rules

pytestmark = pytest.mark.unit


def test_parent_scoped_rules_preserve_role_ambiguity_and_ignore_physical_depth():
    rules = {"rules": [{"role_kind": "RUN_OPTION", "parent_role": "EXECUTION_RUN", "pattern": "Fatigue*", "match_mode": "glob"}]}
    assert resolve_role("Fatigue Cycle 2", 7, "EXECUTION_RUN", rules, "DISTRIBUTION") == ("RUN_OPTION", True, False)
    assert resolve_role("Fatigue Cycle 2", 12, "EXECUTION_RUN", rules, "DISTRIBUTION") == ("RUN_OPTION", True, False)
    assert resolve_role("Fatigue Cycle 2", 7, "SIMULATION_CASE", rules, "DISTRIBUTION") == (None, False, False)
    rules["rules"].append({"role_kind": "SCENE", "parent_role": "EXECUTION_RUN", "pattern": "Fatigue*"})
    assert resolve_role("Fatigue Cycle 2", 7, "EXECUTION_RUN", rules, "DISTRIBUTION") == (None, True, True)


@pytest.mark.parametrize("rule", [
    {"role_kind": "RUN_OPTION", "pattern": "*"},
    {"role_kind": "SIMULATION_CASE", "depth": -1},
    {"role_kind": "SIMULATION_CASE", "pattern": "bad\x00"},
    {"role_kind": "SIMULATION_CASE", "match_mode": "execute"},
])
def test_invalid_rules_do_not_cross_environment_or_execute_arbitrary_matchers(rule):
    with pytest.raises(ValueError):
        validate_rules("USAGE", {"rules": [rule]})
