"""Final version (designation ID) level is optional under Final/CAE/<Case>/ (issue #55, 2026-10-20).

Real server: ``FINAL/CAE/completed_Assy_Motion_..._Inner/Horizontal_Force_Angle`` was shown as
``Final 버전 확정됨 · Final 버전 폴더 이름 형식 아님``. A 32-hex child stays a version folder; any other
child means the Case has no version level and its content starts there (one unversioned Final).
"""
from __future__ import annotations

import pytest

from app.services import environment_folder_profiles as profiles
from tests.test_depth_schema import (  # noqa: F401  (fixtures)
    CSV, CSV_BYTES, DIST, DIST_CASE, DIST_TAIL, FINAL_ID, USAGE, USAGE_CASE, _build_distribution, _build_usage,
    _register, _restore_default_depth_schema, admin_client, by_path, dist_rules, usage_rules)

OTHER_ID = "1" * 32


def _role(resolved, path):
    entry = resolved[path]
    return entry["role_kind"], (entry["deviation"] or {}).get("code"), entry["status"]


@pytest.mark.unit
def test_cae_case_without_version_level_has_no_deviation_and_mirrors_working_one_level_up():
    base = f"{DIST}/Final/CAE/{DIST_CASE}"
    paths = [f"{DIST}/Working", f"{DIST}/Final", f"{DIST}/Final/CAE", base]
    parts = DIST_TAIL.split("/")
    paths += [f"{base}/{'/'.join(parts[:i + 1])}" for i in range(len(parts))]
    paths.append(f"{base}/{DIST_TAIL}/2_Face")
    resolved = by_path(profiles.resolve_tree([DIST.split("/")[0], DIST, *paths], dist_rules()))
    assert _role(resolved, base) == ("SIMULATION_CASE", None, "CONFIRMED")
    # No FINAL_VERSION anywhere: Drop = LOAD_CASE (Working L3), run = EXECUTION_RUN, INDIVIDUAL = RUN_OPTION, scene = SCENE.
    assert [resolved[f"{base}/{'/'.join(parts[:i + 1])}"]["role_kind"] for i in range(len(parts))] == [
        "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION"]
    assert _role(resolved, f"{base}/{DIST_TAIL}/2_Face") == ("SCENE", None, "CONFIRMED")
    assert not [e for e in resolved.values() if e["deviation"]]
    assert {e["role_kind"] for e in resolved.values()} & {"FINAL_VERSION"} == set()
    assert all(resolved[p]["segment"] == "FINAL" for p in paths if p.startswith(f"{DIST}/Final"))


@pytest.mark.unit
def test_the_real_unversioned_folder_is_no_longer_a_warning():
    path = f"{DIST}/Final/CAE/completed_Assy_Motion_75R9J_Set0907_Stand0907_Inner/Horizontal_Force_Angle"
    for rules, expected in ((dist_rules(), "LOAD_CASE"), (usage_rules(), "SCENE")):
        resolved = profiles.resolve_path(path.split("/"), rules)
        assert (resolved["role_kind"], resolved["deviation"], resolved["status"]) == (expected, None, "CONFIRMED")


@pytest.mark.unit
@pytest.mark.parametrize("version", [FINAL_ID, FINAL_ID.upper()])
def test_a_32_hex_child_is_still_a_version_folder(version):
    base = f"{DIST}/Final/CAE/{DIST_CASE}/{version}"
    resolved = profiles.resolve_path(base.split("/"), dist_rules())
    assert (resolved["role_kind"], resolved["deviation"]) == ("FINAL_VERSION", None)
    below = profiles.resolve_path(f"{base}/{DIST_TAIL}".split("/"), dist_rules())
    assert below["role_kind"] == "RUN_OPTION"


@pytest.mark.unit
def test_versioned_and_unversioned_children_of_one_case_are_each_read_by_their_own_name():
    case = f"{DIST}/Final/CAE/{DIST_CASE}"
    rules = dist_rules()
    assert profiles.resolve_path(f"{case}/{OTHER_ID}".split("/"), rules)["role_kind"] == "FINAL_VERSION"
    assert profiles.resolve_path(f"{case}/{OTHER_ID}/Drop".split("/"), rules)["role_kind"] == "LOAD_CASE"
    assert profiles.resolve_path(f"{case}/Horizontal/Drop".split("/"), rules)["role_kind"] == "EXECUTION_RUN"
    # 31 and 33 hex characters are not IDs.
    for name in ("a" * 31, "a" * 33):
        resolved = profiles.resolve_path(f"{case}/{name}".split("/"), rules)
        assert (resolved["role_kind"], resolved["deviation"]) == ("LOAD_CASE", None)


@pytest.mark.unit
def test_deeper_levels_beyond_the_working_schema_are_content():
    deep = f"{DIST}/Final/CAE/{DIST_CASE}/Drop/run/INDIVIDUAL/2_Face/extra"
    resolved = profiles.resolve_path(deep.split("/"), dist_rules())
    assert (resolved["role_kind"], resolved["status"], resolved["deviation"]) == (None, "CONTENT", None)


@pytest.mark.unit
def test_final_report_still_expects_the_id_folder():
    resolved = profiles.resolve_path(f"{DIST}/Final/Report/{DIST_CASE}/not-an-id".split("/"), dist_rules())
    assert (resolved["role_kind"], resolved["deviation"]["code"]) == ("FINAL_VERSION", "FINAL_VERSION_INVALID")
    assert "FINAL_VERSION_INVALID" in profiles.WARNING_DEVIATIONS


@pytest.mark.duckdb_integration
def test_unversioned_final_case_registers_and_displays_without_deviation(admin_client):
    client, root = admin_client
    _build_distribution(root, final=False)
    # An unversioned manual Final copy next to a versioned one.
    for tail in (f"{DIST}/Final/CAE/{DIST_CASE}/{DIST_TAIL}", f"{DIST}/Final/CAE/{DIST_CASE}/{FINAL_ID}/{DIST_TAIL}"):
        copy = root / tail / "2_Face"
        copy.mkdir(parents=True)
        (copy / CSV).write_bytes(CSV_BYTES)
    (root / DIST / "Final" / "CAD").mkdir()
    scan, preview, registered = _register(client, "DISTRIBUTION")
    nodes = by_path(scan["nodes"])
    assert not [n for n in nodes.values() if n.get("deviation")], [n for n in nodes.values() if n.get("deviation")]
    case = f"{DIST}/Final/CAE/{DIST_CASE}"
    assert nodes[f"{case}/Drop"]["role_kind"] == "LOAD_CASE"
    assert nodes[f"{case}/{DIST_TAIL}/2_Face"]["role_kind"] == "SCENE"
    assert nodes[f"{case}/{FINAL_ID}"]["role_kind"] == "FINAL_VERSION"
    assert nodes[f"{case}/{FINAL_ID}/{DIST_TAIL}/2_Face"]["role_kind"] == "SCENE"
    # Final is never a result source (D12), versioned or not: only the Working Case is captured.
    assert not [row for row in preview["rows"] if "/Final/" in row["relative_path"]]
    assert len(registered["capture_jobs"]) == 1 and registered["capture_jobs"][0]["status"] == "COMPLETED"


@pytest.mark.duckdb_integration
def test_usage_unversioned_final_case_scene_level(admin_client):
    client, root = admin_client
    _build_usage(root)
    copy = root / USAGE / "Final" / "CAE" / USAGE_CASE / "Horizontal_Force_Angle"
    copy.mkdir(parents=True)
    (copy / "a_result.json").write_text("{}", encoding="utf-8")
    scan, preview, _registered = _register(client, "USAGE")
    nodes = by_path(scan["nodes"])
    final_scene = nodes[f"{USAGE}/Final/CAE/{USAGE_CASE}/Horizontal_Force_Angle"]
    assert (final_scene["role_kind"], final_scene.get("deviation")) == ("SCENE", None)
    assert not [row for row in preview["rows"] if "/Final/" in row["relative_path"]]
