"""Usage-environment materials: OptiStruct parser, background parse cache, local and drive reads.

Synthetic decks only (the issue #48 HyperMesh sample plus generated mesh lines) in
isolated temp SPDM roots, an isolated test database and the in-memory fake drive
adapter; never a real DB, drive or running service.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from app.database_connection import connect
from app.parsers.optistruct_deck_parser import (OptiStructDeckParser, OptiStructParseError, nastran_float,
                                                parse_optistruct_text)
from app.services import optistruct_materials
from app.services.storage import provider_for_root
from tests.test_depth_schema import USAGE, USAGE_CASE, _build_usage, _register
from tests.test_new_scene_registration import admin_client  # noqa: F401
from tests.test_drive_reads import scx  # noqa: F401

FIXTURE = Path(__file__).parent / "fixtures" / "optistruct" / "issue48_sample.fem"
BASE = "/api/materials"


def _sample() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _mesh(count: int, start: int = 1) -> str:
    lines = []
    for index in range(start, start + count):
        lines.append(f"GRID    {index:>8}        {index * 0.001:>8.3f}{1.0:>8.3f}{2.0:>8.3f}")
        lines.append(f"CQUAD4  {index:>8}      16{index:>8}{index + 1:>8}{index + 2:>8}{index + 3:>8}")
        if index % 7 == 0:
            lines.append(f"CHEXA   {index:>8}      18{1:>8}{2:>8}{3:>8}{4:>8}{5:>8}{6:>8}+")
            lines.append(f"+       {7:>8}{8:>8}")
    return "\n".join(lines) + "\n"


# --- parser -----------------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("text,expected", [
    ("1.5", 1.5), ("3.3-9", 3.3e-9), ("1.+5", 1e5), ("1.27-9", 1.27e-9), (".0012185", 0.0012185),
    ("-.5-3", -5e-4), ("1.5D3", 1500.0), ("2.1E+5", 2.1e5), ("7.83-9", 7.83e-9), ("", None), ("ENDT", None),
])
def test_nastran_real_shorthand(text, expected):
    value = nastran_float(text)
    assert value == pytest.approx(expected) if expected is not None else value is None


@pytest.mark.unit
def test_issue48_sample_maps_components_properties_materials_and_tables():
    deck = parse_optistruct_text(_sample())
    assert deck["solver"] == "OPTISTRUCT"
    parts = {part["id"]: part for part in deck["parts"]}
    assert len(parts) == 30
    assert parts["2"]["title"] == "open_cell" and parts["2"]["property_id"] == "16" and parts["2"]["material_id"] == "9"
    assert parts["2"]["thickness"] == 1.0
    assert parts["13"]["thickness"] == pytest.approx(1.9) and parts["13"]["material_id"] == "50250"
    assert parts["5"]["property_id"] == "18" and parts["5"]["material_id"] == "20"            # PSOLID
    assert parts["363"]["material_id"] == "51109"                                             # PBEAM
    assert parts["70"]["property_id"] == "58" and parts["70"]["material_id"] is None           # PBUSH
    assert parts["74"]["property_id"] is None                                                  # RBE component
    props = {item["id"]: item for item in deck["properties"]}
    assert props["24"]["subtype"] == "PSHELL" and props["24"]["title"] == "Steel_1.0T"
    assert props["2"]["fields"]["A"] == pytest.approx(12.56637) and props["2"]["fields"]["J"] == pytest.approx(25.13274)
    assert props["58"]["fields"]["K1"] == pytest.approx(9.225679) and props["59"]["fields"]["K4"] == 100000.0
    mats = {item["id"]: item for item in deck["materials"]}
    assert len(mats) == 8
    glass = mats["9"]
    assert glass["title"] == "Glass" and glass["representative_e"] == 64500.0 and glass["fields"]["NU"] == 0.3
    assert glass["density"]["raw"] == "3.3-9" and glass["density"]["converted_value"] == pytest.approx(3.3)
    assert mats["20"]["title"] == "Aluminum_PlasticStrain_2t_66GPa" and mats["20"]["fields"]["TREF"] == 20.0
    assert mats["20"]["fields"]["MATS1.TYPE"] == "PLASTIC" and mats["20"]["fields"]["MATS1.TID"] == 1
    assert mats["51102"]["fields"].get("NU") is None and mats["51102"]["density"]["value"] == pytest.approx(6e-11)
    tables = {item["id"]: item for item in deck["functions"]}
    assert set(tables) == {"61063", "61065", "61071", "1"}
    assert tables["61063"]["card"] == "TABLEMD" and tables["61063"]["point_count"] == 95
    assert tables["61063"]["points"][0] == {"x": 0.0, "y": 53.2}
    assert tables["61063"]["points"][-1] == {"x": 0.2, "y": 79.0}
    assert tables["61063"]["uses"][0]["owner_id"] == "50230"
    assert tables["1"]["card"] == "TABLES1" and tables["1"]["point_count"] == 100
    assert tables["1"]["points"][1] == {"x": pytest.approx(2.6739e-5), "y": pytest.approx(135826.2)}
    assert tables["1"]["uses"][0]["role"] == "MATS1 PLASTIC" and tables["1"]["title"] == "TABLES11"
    assert deck["unit_system"]["input"]["length"] == "mm"
    assert {item["code"] for item in deck["warnings"]} == {"OPTISTRUCT_UNITS_INFERRED"}


@pytest.mark.unit
def test_mesh_lines_end_kept_cards_and_are_skipped():
    text = "BEGIN BULK\n" + _sample() + _mesh(500) + "ENDDATA\nMAT1    999     1.0\n"
    deck = parse_optistruct_text(text)
    assert {item["id"] for item in deck["functions"]} == {"61063", "61065", "61071", "1"}
    assert deck["stats"]["skipped_mesh_lines"] == 500 * 2 + 71
    assert "999" not in {item["id"] for item in deck["materials"]}     # after ENDDATA
    assert {item["keyword"] for item in deck["unknown_cards"]} == {"BEGIN"}


@pytest.mark.unit
def test_small_large_and_free_field_formats_agree():
    small = ("MAT1          10210000.0         0.3     7.85-9                          \n"
             "PSHELL        11      10     1.5      10\n")
    large = ("MAT1*                 10        210000.0                             0.3\n"
             "*                 7.85-9\n"
             "PSHELL*               11              10             1.5              10\n")
    free = "MAT1,10,210000.0,,0.3,7.85-9\nPSHELL,11,10,1.5,10\n"
    results = [parse_optistruct_text(text) for text in (small, large, free)]
    for deck in results:
        material = deck["materials"][0]
        assert material["id"] == "10"
        assert material["fields"]["E"] == 210000.0 and material["fields"]["NU"] == 0.3
        assert material["fields"]["RHO"] == pytest.approx(7.85e-9)
        prop = deck["properties"][0]
        assert prop["id"] == "11" and prop["material_id"] == "10" and prop["thickness"] == 1.5
        # no $HMNAME COMP: one row per property
        assert [(part["id"], part["material_id"]) for part in deck["parts"]] == [("11", "10")]


@pytest.mark.unit
def test_free_field_continuation_and_composites():
    text = ("PCOMP,5,,,,,,,\n+,100,0.2,0.0,YES,,0.3,45.0,\n+,101,0.1,90.0\n"
            "PCOMPG  6                                                               +\n"
            "+             1     100    0.25     0.0     YES\n"
            "+             2             0.25    90.0\n"
            "MAT8    100     140000. 10000.  0.3     5000.   5000.   3000.   1.6-9\n"
            "MAT8    101     70000.  70000.  0.1     5000.                   2.0-9\n")
    deck = parse_optistruct_text(text)
    props = {item["id"]: item for item in deck["properties"]}
    assert [ply["MID"] for ply in props["5"]["fields"]["plies"]] == ["100", "100", "101"]
    assert props["5"]["thickness"] == pytest.approx(0.6) and props["5"]["material_id"] == "100"
    assert [ply["GPLYID"] for ply in props["6"]["fields"]["plies"]] == ["1", "2"]
    assert props["6"]["thickness"] == pytest.approx(0.5)
    mats = {item["id"]: item for item in deck["materials"]}
    assert mats["100"]["representative_e"] == 140000.0 and mats["100"]["fields"]["G12"] == 5000.0


@pytest.mark.unit
def test_includes_are_read_inline_and_unresolved_ones_warn():
    files = {"inc/mats.fem": b"$HMNAME MAT 7\"Inc\" \"MAT1\"\nMAT1           7  1000.0         0.3     1.0-9\n"}
    opened = []

    from contextlib import contextmanager

    @contextmanager
    def open_include(including, value):
        target = value.replace("\\", "/")
        if target not in files:
            raise OptiStructParseError("MATERIALS_INCLUDE_NOT_FOUND", "missing")
        opened.append((including, target))
        data = files[target]
        yield iter(data.splitlines(keepends=True)), target, len(data)

    text = ("PSHELL        11       7     2.0\nINCLUDE 'inc\\mats.fem'\nINCLUDE missing.fem\n"
            "$HMNAME COMP 3\"Panel\" 11 \"P\" 4\n")
    deck = parse_optistruct_text(text, open_include=open_include)
    assert opened == [("model.fem", "inc/mats.fem")]
    assert deck["parts"][0]["material_id"] == "7" and deck["materials"][0]["title"] == "Inc"
    assert deck["materials"][0]["source"]["file"] == "inc/mats.fem"
    assert any(item["code"] == "MATERIALS_INCLUDE_NOT_FOUND" for item in deck["warnings"])
    assert [item["file"] for item in deck["includes"]] == ["model.fem", "inc/mats.fem"]


@pytest.mark.unit
def test_duplicate_and_unresolved_references_warn():
    text = ("MAT1           1   100.0\nMAT1           1   200.0\nPSHELL         2       9     1.0\n"
            "$HMNAME COMP 4\"A\" 2 \"P\" 4\n$HMNAME COMP 5\"B\" 77 \"Q\" 4\nMATS1          1      55\n")
    deck = parse_optistruct_text(text)
    codes = [item["code"] for item in deck["warnings"]]
    assert "OPTISTRUCT_CARD_DUPLICATE" in codes and codes.count("MATERIAL_REFERENCE_UNRESOLVED") == 2
    assert "MATERIAL_FUNCTION_REFERENCE_UNRESOLVED" in codes
    assert deck["materials"][0]["representative_e"] == 200.0
    assert {(item["part_id"], item["reference"]) for item in deck["unresolved_references"]} == {
        ("4", "material_id"), ("5", "property_id")}


@pytest.mark.unit
def test_streaming_parse_of_a_synthetic_large_file(tmp_path):
    """~25 MB of mesh around the sample: correct result, bounded progress calls, throughput reported."""
    path = tmp_path / "big.fem"
    block = _mesh(20000).encode("ascii")
    with path.open("wb") as stream:
        stream.write(b"BEGIN BULK\n" + FIXTURE.read_bytes())
        while stream.tell() < 25 * 1024 * 1024:
            stream.write(block)
        stream.write(b"ENDDATA\n")
    size = path.stat().st_size
    calls = []
    started = time.perf_counter()
    with path.open("rb") as stream:
        deck = OptiStructDeckParser(on_progress=lambda done, total: calls.append(done)).parse(stream, "big.fem", size)
    seconds = time.perf_counter() - started
    print(f"OptiStruct streaming parse: {size / seconds / 1e6:.1f} MB/s ({size} bytes, {seconds:.2f} s)")
    assert len(deck["parts"]) == 30 and len(deck["functions"]) == 4
    assert deck["stats"]["bytes"] == size and calls[-1] == size
    assert seconds < 60


@pytest.mark.unit
def test_time_and_size_guards_abort_the_parse():
    def check():
        raise OptiStructParseError("MATERIALS_PARSE_TIME_LIMIT", "late", 413)
    with pytest.raises(OptiStructParseError) as error:
        parse_optistruct_text(_mesh(70000), check=check)
    assert error.value.code == "MATERIALS_PARSE_TIME_LIMIT"


# --- review fixes: hostile decks ----------------------------------------------------------------

def _tablest_chain(count: int) -> str:
    """MATS1 → TABLEST 1 → TABLEST 2 (twice) → … → TABLES1 <count+1>: 2**count paths without dedupe."""
    lines = [f"{'MAT1':<8}{1:>8}{1000.0:>8}", f"{'MATS1':<8}{1:>8}{1:>8}{'PLASTIC':>8}"]
    for index in range(1, count + 1):
        lines.append(f"{'TABLEST':<8}{index:>8}")
        lines.append(f"{'+':<8}{20.0:>8}{index + 1:>8}{80.0:>8}{index + 1:>8}{'ENDT':>8}")
    lines.append(f"{'TABLES1':<8}{count + 1:>8}")
    lines.append(f"{'+':<8}{0.0:>8}{100.0:>8}{0.1:>8}{150.0:>8}{'ENDT':>8}")
    return "\n".join(lines) + "\n"


@pytest.mark.unit
def test_tablest_doubling_chain_is_linear_and_deduplicated():
    started = time.perf_counter()
    deck = parse_optistruct_text(_tablest_chain(30))
    assert time.perf_counter() - started < 2.0
    tables = {item["id"]: item for item in deck["functions"]}
    assert len(tables) == 31
    assert all(len(item["uses"]) == 1 for item in tables.values())            # each table once per owner/role
    assert len(deck["function_uses"]) == 31
    assert tables["1"]["uses"][0]["role"] == "MATS1 PLASTIC"
    assert tables["31"]["uses"][0]["role"].startswith("MATS1 PLASTIC · 온도 ")  # role does not grow with depth
    assert tables["31"]["uses"][0]["role"].count("온도") == 1


@pytest.mark.unit
def test_tablest_self_reference_and_use_limit(monkeypatch):
    text = (f"{'MAT1':<8}{1:>8}{1000.0:>8}\n{'MATS1':<8}{1:>8}{5:>8}{'PLASTIC':>8}\n"
            f"{'TABLEST':<8}{5:>8}\n{'+':<8}{20.0:>8}{5:>8}{'ENDT':>8}\n")
    deck = parse_optistruct_text(text)
    assert len(deck["functions"][0]["uses"]) == 1
    assert "OPTISTRUCT_TABLE_REFERENCE_CYCLE" in {item["code"] for item in deck["warnings"]}

    from app.parsers import optistruct_deck_parser
    monkeypatch.setattr(optistruct_deck_parser, "MAX_FUNCTION_USES", 10)
    with pytest.raises(OptiStructParseError) as error:
        parse_optistruct_text(_tablest_chain(12))
    assert error.value.code == "MATERIALS_FUNCTION_USE_LIMIT" and error.value.status_code == 413


@pytest.mark.unit
@pytest.mark.parametrize("text", ["nan", "NaN", "inf", "-Infinity", "1.+999", "1e999", "1.5D999"])
def test_nastran_float_rejects_non_finite(text):
    assert nastran_float(text) is None


@pytest.mark.unit
def test_non_finite_fields_warn_and_keep_the_deck_json_safe():
    import json

    text = (f"{'MAT1':<8}{1:>8}{'NaN':>8}{'':>8}{0.3:>8}{'1.+999':>8}\n"
            f"{'PSHELL':<8}{2:>8}{1:>8}{'inf':>8}\n"
            f"{'PCOMP':<8}{3:>8}\n{'+':<8}{1:>8}{'1.+308':>8}{0.0:>8}{'YES':>8}{1:>8}{'1.+308':>8}{0.0:>8}{'YES':>8}\n"
            f"{'TABLES1':<8}{4:>8}\n{'+':<8}{0.0:>8}{'nan':>8}{0.1:>8}{150.0:>8}{'ENDT':>8}\n")
    deck = parse_optistruct_text(text)
    json.dumps(deck, allow_nan=False)                                          # would raise on NaN/Infinity
    material = deck["materials"][0]
    assert material["representative_e"] is None and material["density"]["value"] is None
    props = {item["id"]: item for item in deck["properties"]}
    assert props["2"]["thickness"] is None and props["3"]["thickness"] is None   # 1e308 + 1e308 overflows
    assert deck["functions"][0]["points"] == [{"x": 0.1, "y": 150.0}]
    invalid = [item for item in deck["warnings"] if item["code"] == "OPTISTRUCT_FIELD_INVALID"]
    assert {item["line"] for item in invalid} == {1, 2, 5}


@pytest.mark.unit
def test_long_lines_and_oversized_cards_fail_fast(monkeypatch):
    import io

    from app.parsers import optistruct_deck_parser

    long_line = b"$ " + b"x" * (70 * 1024) + b"\n"
    for stream in (io.BytesIO(b"MAT1           1   1.0\n" + long_line), iter([b"MAT1           1   1.0\n", long_line])):
        with pytest.raises(OptiStructParseError) as error:
            OptiStructDeckParser().parse(stream, "x.fem")
        assert error.value.code == "MATERIALS_LINE_TOO_LONG"
    # A single 64 MiB line without a newline is never read whole.
    started = time.perf_counter()
    with pytest.raises(OptiStructParseError):
        OptiStructDeckParser().parse(io.BytesIO(b"GRID" + b" " * (64 * 1024 * 1024)), "x.fem")
    assert time.perf_counter() - started < 2.0

    monkeypatch.setattr(optistruct_deck_parser, "MAX_CARD_LINES", 100)
    text = "TABLES1        7\n" + "".join(f"+       {i:>8}{i:>8}\n" for i in range(200))
    with pytest.raises(OptiStructParseError) as error:
        parse_optistruct_text(text)
    assert error.value.code == "MATERIALS_CARD_TOO_LARGE"


@pytest.mark.unit
def test_deadline_is_checked_by_bytes_not_only_by_line_count():
    import io

    calls = []
    data = (b"$ " + b"y" * 60_000 + b"\n") * 300                                  # ~18 MB in 300 lines
    OptiStructDeckParser(check=lambda: calls.append(1)).parse(io.BytesIO(data), "x.fem", len(data))
    assert len(calls) >= 3                                                        # every 8 MiB + the end


@pytest.mark.unit
def test_name_comment_regex_is_linear_on_long_blank_tails():
    from app.parsers.optistruct_deck_parser import _HM_STAR

    line = "$* Material: 12 name: Steel" + " " * 1_000_000 + "x"
    started = time.perf_counter()
    match = _HM_STAR.match(line)
    assert match is not None and match.group(3).strip().startswith("Steel")
    assert _HM_STAR.match("$* Material: 12 name:" + " " * 1_000_000) is not None
    parser = OptiStructDeckParser()
    parser._comment(("$* Material: 13 name:   Alu   " + " " * 1_000_000 + "\n").encode())
    assert time.perf_counter() - started < 1.0
    assert parser._hm_names[("MAT", "13")] == "Alu"


# --- API: local SPDM root ---------------------------------------------------------------------

SCENES = f"{USAGE}/Working/{USAGE_CASE}"


def _ids(registered):
    return registered["project_id"], registered["request_id"]


def _catalog(client, request_id):
    response = client.get(BASE + "/catalog", params={"request_id": request_id, "environment": "USAGE"})
    assert response.status_code == 200, response.text
    return response.json()


def _deck(client, request_id, scene_id, status=200, **extra):
    response = client.get(BASE + "/deck", params={"request_id": request_id, "environment": "USAGE",
                                                   "scene_id": scene_id, **extra})
    assert response.status_code == status, response.text
    return response.json()


def _ready(client, request_id, scene_id, **extra):
    body = _deck(client, request_id, scene_id, **extra)
    if body["analysis"]["status"] != "READY":
        assert body["analysis"]["status"] in {"QUEUED", "RUNNING"} and body["deck"] is None, body["analysis"]
        assert optistruct_materials.wait_idle(60)
        body = _deck(client, request_id, scene_id)
    return body


@pytest.fixture
def parse_counter(monkeypatch):
    optistruct_materials.reset_for_tests()
    calls = []
    original = optistruct_materials.run_job

    def counted(state):
        calls.append(state.spec.rel_path)
        return original(state)

    monkeypatch.setattr(optistruct_materials, "run_job", counted)
    yield calls
    optistruct_materials.wait_idle(60)


@pytest.mark.duckdb_integration
def test_usage_materials_parse_once_cache_and_reparse_on_change(admin_client, parse_counter):
    client, root = admin_client
    _build_usage(root)
    case = root / SCENES
    (case / "Settle" / "Settle_model.fem").write_text("BEGIN BULK\n" + _sample() + _mesh(2000) + "ENDDATA\n", encoding="utf-8")
    (case / "Case_shared.fem").write_text("PSHELL         1       2     0.8\nMAT1           2  5000.0         0.4     1.2-9\n",
                                          encoding="utf-8")
    _, _, registered = _register(client, "USAGE")
    _, request_id = _ids(registered)

    catalog = _catalog(client, request_id)
    assert catalog["environment"] == "USAGE"
    scenes = {item["label"]: item for item in catalog["scenes"]}
    assert set(scenes) >= {"Settle", "Wobble"} and all(item["has_deck"] for item in scenes.values())
    assert catalog["hierarchy"]["cases"] and not catalog["hierarchy"]["load_cases"]

    body = _ready(client, request_id, scenes["Settle"]["scene_id"])
    assert body["analysis"]["solver"] == "OPTISTRUCT" and body["analysis"]["relative_path"].endswith("Settle/Settle_model.fem")
    assert len(body["deck"]["parts"]) == 30 and body["deck"]["solver"] == "OPTISTRUCT"
    assert parse_counter == [f"{SCENES}/Settle/Settle_model.fem"]

    again = _deck(client, request_id, scenes["Settle"]["scene_id"])
    assert again["analysis"]["status"] == "READY" and again["analysis"]["cached"] is True
    assert parse_counter == [f"{SCENES}/Settle/Settle_model.fem"]          # cache hit: no new parse

    # Wobble has no .fem of its own: the Case folder file is used.
    wobble = _ready(client, request_id, scenes["Wobble"]["scene_id"])
    assert wobble["analysis"]["relative_path"] == f"{SCENES}/Case_shared.fem"
    assert wobble["deck"]["parts"][0]["material_id"] == "2"

    # A changed file (size/mtime) is parsed again.
    path = case / "Settle" / "Settle_model.fem"
    path.write_text(path.read_text(encoding="utf-8").replace("ENDDATA", "MAT1         777  1.0\nENDDATA"), encoding="utf-8")
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 5_000_000))
    changed = _ready(client, request_id, scenes["Settle"]["scene_id"])
    assert "777" in {item["id"] for item in changed["deck"]["materials"]}
    assert parse_counter.count(f"{SCENES}/Settle/Settle_model.fem") == 2
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM materials_deck_cache WHERE root_key=? AND rel_path=?",
                            [provider_for_root(root).root_key(), f"{SCENES}/Settle/Settle_model.fem"]).fetchone()[0] == 1


@pytest.mark.duckdb_integration
def test_usage_include_change_invalidates_and_failures_need_retry(admin_client, parse_counter, monkeypatch):
    client, root = admin_client
    _build_usage(root)
    settle = root / SCENES / "Settle"
    (settle / "model.fem").write_text("PSHELL        11       7     2.0\nINCLUDE 'inc/mats.inc'\n", encoding="utf-8")
    (settle / "inc").mkdir()
    (settle / "inc" / "mats.inc").write_text("MAT1           7  1000.0\n", encoding="utf-8")
    _, _, registered = _register(client, "USAGE")
    _, request_id = _ids(registered)
    scene = next(item for item in _catalog(client, request_id)["scenes"] if item["label"] == "Settle")

    body = _ready(client, request_id, scene["scene_id"])
    assert body["deck"]["materials"][0]["representative_e"] == 1000.0
    assert [item["relative_path"] for item in body["files"]] == [f"{SCENES}/Settle/model.fem", f"{SCENES}/Settle/inc/mats.inc"]

    include = settle / "inc" / "mats.inc"
    include.write_text("MAT1           7  2000.00\n", encoding="utf-8")
    os.utime(include, ns=(time.time_ns(), time.time_ns() + 5_000_000))
    body = _ready(client, request_id, scene["scene_id"])
    assert body["deck"]["materials"][0]["representative_e"] == 2000.0 and len(parse_counter) == 2

    # An INCLUDE cycle fails the parse; the failure is cached until an explicit retry.
    (settle / "inc" / "mats.inc").write_text("INCLUDE '../model.fem'\n", encoding="utf-8")
    failed = _ready(client, request_id, scene["scene_id"])
    assert failed["analysis"]["status"] == "FAILED" and failed["analysis"]["error_code"] == "MATERIALS_INCLUDE_CYCLE"
    assert _deck(client, request_id, scene["scene_id"])["analysis"]["status"] == "FAILED"
    assert len(parse_counter) == 3
    retried = _deck(client, request_id, scene["scene_id"], retry="true")
    assert retried["analysis"]["status"] in {"QUEUED", "RUNNING"}
    assert optistruct_materials.wait_idle(60) and len(parse_counter) == 4
    # L3: a second retry within a minute is refused (the cached failure is shown with a wait hint).
    again = _deck(client, request_id, scene["scene_id"], retry="true")
    assert again["analysis"]["status"] == "FAILED" and again["analysis"]["retry_after_seconds"] >= 1
    assert optistruct_materials.wait_idle(60) and len(parse_counter) == 4
    # L1: the failed parse recorded its INCLUDE dependencies, so fixing the include re-parses without a retry.
    with connect() as conn:
        row = optistruct_materials.load_row(conn, provider_for_root(root).root_key(), f"{SCENES}/Settle/model.fem")
    assert row["status"] == "FAILED" and f"{SCENES}/Settle/inc/mats.inc" in row["dependencies_json"]
    include.write_text("MAT1           7  3000.00\n", encoding="utf-8")
    os.utime(include, ns=(time.time_ns(), time.time_ns() + 9_000_000))
    fixed = _ready(client, request_id, scene["scene_id"])
    assert fixed["deck"]["materials"][0]["representative_e"] == 3000.0 and len(parse_counter) == 5

    monkeypatch.setenv("SIMDASH_OPTISTRUCT_MAX_BYTES", str(1024 * 1024))
    (settle / "model.fem").write_bytes(b"$" * (1024 * 1024 + 10) + b"\n")
    too_large = _deck(client, request_id, scene["scene_id"], status=413)
    assert too_large["detail"]["code"] == "MATERIALS_FILE_SIZE_LIMIT"


@pytest.mark.duckdb_integration
def test_usage_missing_include_added_later_invalidates_the_cache(admin_client, parse_counter):
    client, root = admin_client
    _build_usage(root)
    settle = root / SCENES / "Settle"
    (settle / "model.fem").write_text("PSHELL        11       7     2.0\nINCLUDE 'later.inc'\n", encoding="utf-8")
    _, _, registered = _register(client, "USAGE")
    _, request_id = _ids(registered)
    scene = next(item for item in _catalog(client, request_id)["scenes"] if item["label"] == "Settle")
    body = _ready(client, request_id, scene["scene_id"])
    assert body["deck"]["materials"] == [] and "MATERIALS_INCLUDE_NOT_FOUND" in {
        item["code"] for item in body["deck"]["warnings"]}
    assert [item["relative_path"] for item in body["files"]] == [f"{SCENES}/Settle/model.fem"]
    assert _deck(client, request_id, scene["scene_id"])["analysis"]["cached"] is True
    (settle / "later.inc").write_text("MAT1           7  1000.0\n", encoding="utf-8")
    added = _ready(client, request_id, scene["scene_id"])
    assert [item["id"] for item in added["deck"]["materials"]] == ["7"] and len(parse_counter) == 2


@pytest.mark.duckdb_integration
def test_usage_transient_storage_failure_is_not_cached(admin_client, parse_counter, monkeypatch):
    from app.services.storage.provider import SpdmStorageError

    client, root = admin_client
    _build_usage(root)
    (root / SCENES / "Settle" / "model.fem").write_text("MAT1           2   1.0\n", encoding="utf-8")
    _, _, registered = _register(client, "USAGE")
    _, request_id = _ids(registered)
    scene = next(item for item in _catalog(client, request_id)["scenes"] if item["label"] == "Settle")
    original = optistruct_materials._parse
    failures = {"left": 2}

    def flaky(state, deadline, limit, dependencies):
        if failures["left"]:
            failures["left"] -= 1
            raise SpdmStorageError("DRIVE_STAGING_FULL", "staging full")
        return original(state, deadline, limit, dependencies)

    monkeypatch.setattr(optistruct_materials, "_parse", flaky)
    failed = _ready(client, request_id, scene["scene_id"])
    assert failed["analysis"]["status"] == "FAILED" and failed["analysis"]["transient"] is True
    assert failed["analysis"]["error_code"] == "DRIVE_STAGING_FULL" and failed["analysis"]["cached"] is False
    with connect() as conn:
        assert optistruct_materials.load_row(conn, provider_for_root(root).root_key(), f"{SCENES}/Settle/model.fem") is None
    # Within the back-off the failure is shown without a new parse; a retry runs again (and fails again).
    assert _deck(client, request_id, scene["scene_id"])["analysis"]["transient"] is True and len(parse_counter) == 1
    assert _deck(client, request_id, scene["scene_id"], retry="true")["analysis"]["status"] in {"QUEUED", "RUNNING"}
    assert optistruct_materials.wait_idle(60) and len(parse_counter) == 2
    # After the back-off the next view re-queues by itself.
    monkeypatch.setattr(optistruct_materials, "TRANSIENT_BACKOFF_SECONDS", 0.0)
    with optistruct_materials._lock:
        for item in optistruct_materials._transient.values():
            item["until"] = 0.0
    body = _ready(client, request_id, scene["scene_id"])
    assert body["analysis"]["status"] == "READY" and len(parse_counter) == 3


@pytest.mark.duckdb_integration
def test_usage_polling_reuses_the_resolved_input(admin_client, parse_counter, monkeypatch):
    from app.services import materials_catalog

    client, root = admin_client
    _build_usage(root)
    (root / SCENES / "Settle" / "model.fem").write_text("MAT1           2   1.0\n", encoding="utf-8")
    _, _, registered = _register(client, "USAGE")
    _, request_id = _ids(registered)
    scene = next(item for item in _catalog(client, request_id)["scenes"] if item["label"] == "Settle")
    calls = []
    original = materials_catalog._resolve_scene
    monkeypatch.setattr(materials_catalog, "_resolve_scene", lambda *args: calls.append(1) or original(*args))
    _ready(client, request_id, scene["scene_id"])
    for _ in range(3):
        assert _deck(client, request_id, scene["scene_id"])["analysis"]["status"] == "READY"
    assert len(calls) == 1
    _deck(client, request_id, scene["scene_id"], retry="true")                     # a retry always resolves
    assert len(calls) == 2


@pytest.mark.duckdb_integration
def test_usage_include_outside_request_is_a_warning_not_a_read(admin_client, parse_counter):
    client, root = admin_client
    _build_usage(root)
    (root / "outside.fem").write_text("MAT1           1   1.0\n", encoding="utf-8")
    (root / SCENES / "Settle" / "model.fem").write_text(
        "INCLUDE '../../../../../outside.fem'\nINCLUDE 'C:\\models\\x.fem'\nMAT1           2   1.0\n", encoding="utf-8")
    _, _, registered = _register(client, "USAGE")
    _, request_id = _ids(registered)
    scene = next(item for item in _catalog(client, request_id)["scenes"] if item["label"] == "Settle")
    body = _ready(client, request_id, scene["scene_id"])
    assert [item["id"] for item in body["deck"]["materials"]] == ["2"]
    codes = {item["code"] for item in body["deck"]["warnings"]}
    assert {"MATERIALS_INCLUDE_OUTSIDE_REQUEST", "MATERIALS_INCLUDE_INVALID"} <= codes


@pytest.mark.duckdb_integration
def test_usage_scene_without_input_has_no_deck(admin_client, parse_counter):
    client, root = admin_client
    _build_usage(root)
    _, _, registered = _register(client, "USAGE")
    _, request_id = _ids(registered)
    scenes = _catalog(client, request_id)["scenes"]
    assert scenes and not any(item["has_deck"] for item in scenes)
    missing = _deck(client, request_id, scenes[0]["scene_id"], status=404)
    assert missing["detail"]["code"] == "MATERIALS_DECK_NOT_FOUND" and parse_counter == []


# --- API: SCX drive (fake adapter) -----------------------------------------------------------

@pytest.mark.duckdb_integration
def test_drive_usage_materials_download_once_and_reuse_the_blob(scx, parse_counter, monkeypatch):  # noqa: F811
    from app.services.drive import reads as drive_reads
    from app.services.storage.drive import DriveRoot
    from tests.test_depth_schema import COND

    monkeypatch.setattr(drive_reads, "BLOB_THRESHOLD_BYTES", 4096)
    case = f"{USAGE}/Working/{USAGE_CASE}"
    scx.add(f"{case}/Settle/{COND}_settle_result.json", b'{"Set Tilt Angle @ Settle (deg)": 1.18}')
    model = ("BEGIN BULK\n" + _sample() + _mesh(300) + "ENDDATA\n").encode("utf-8")
    scx.add(f"{case}/Settle/model.fem", model)
    scan = scx.post("/api/folder-discovery/environments/scan", {"environment": "USAGE", "relative_path": ""})
    preview = scx.post("/api/folder-discovery/environments/previews", {"scan_id": scan["id"], "assignments": []})
    registered = scx.post("/api/folder-discovery/environments/registrations",
                          {"preview_id": preview["id"], "idempotency_key": f"os-{uuid.uuid4()}", "capture": False})
    request_id = registered["request_id"]

    catalog = _catalog(scx.client, request_id)
    scene = next(item for item in catalog["scenes"] if item["label"] == "Settle")
    assert scene["has_deck"]
    body = _ready(scx.client, request_id, scene["scene_id"])
    assert len(body["deck"]["parts"]) == 30
    downloads = [rel for op, rel in scx.drive.calls if op == "download_to" and rel.endswith("model.fem")]
    assert len(downloads) == 1
    with connect() as conn:
        blob = conn.execute("SELECT blob_sha256, fingerprint FROM materials_deck_cache WHERE root_key=? AND rel_path=?",
                            [DriveRoot(scx.root, "scx.example.test").root_key(), f"{case}/Settle/model.fem"]).fetchone()
    assert blob[0] and blob[1].startswith("drive:")
    assert drive_reads.blob_store().find(blob[0]) is not None

    # Cache hit: no download, no parse.
    assert _deck(scx.client, request_id, scene["scene_id"])["analysis"]["cached"] is True
    # A parser upgrade re-parses from the blob of the unchanged version (no second download).
    monkeypatch.setattr(optistruct_materials, "PARSER_VERSION", "optistruct-test-next")
    again = _ready(scx.client, request_id, scene["scene_id"])
    assert again["analysis"]["status"] == "READY" and len(parse_counter) == 2
    assert len([rel for op, rel in scx.drive.calls if op == "download_to" and rel.endswith("model.fem")]) == 1

    # A new drive version is downloaded and parsed again.
    scx.drive.add_file(scx.path(f"{case}/Settle/model.fem"), model.replace(b"ENDDATA", b"MAT1         901  1.0\nENDDATA"))
    changed = _ready(scx.client, request_id, scene["scene_id"])
    assert "901" in {item["id"] for item in changed["deck"]["materials"]}
    assert len([rel for op, rel in scx.drive.calls if op == "download_to" and rel.endswith("model.fem")]) == 2

    # L2: a queued job whose drive version is stale stops before downloading and stores nothing.
    root_key = DriveRoot(scx.root, "scx.example.test").root_key()
    with connect() as conn:
        before = optistruct_materials.load_row(conn, root_key, f"{case}/Settle/model.fem")
    stale = optistruct_materials.JobSpec(key="stale", root=DriveRoot(scx.root, "scx.example.test"), root_key=root_key,
                                         rel_path=f"{case}/Settle/model.fem", fingerprint="drive:stale-version",
                                         size=len(model), request_relative_path=USAGE)
    optistruct_materials.run_job(optistruct_materials.JobState(spec=stale))
    assert len([rel for op, rel in scx.drive.calls if op == "download_to" and rel.endswith("model.fem")]) == 2
    with connect() as conn:
        after = optistruct_materials.load_row(conn, root_key, f"{case}/Settle/model.fem")
    assert after["updated_at"] == before["updated_at"] and after["status"] == "READY"
