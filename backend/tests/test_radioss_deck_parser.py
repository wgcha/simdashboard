from __future__ import annotations

from io import BytesIO
import re
from pathlib import Path

from app.parsers.radioss_deck_parser import RadiossDeckParser, read_fields


FIXTURES = Path(__file__).parent / "fixtures" / "radioss"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _count_cards(text: str, keyword: str) -> int:
    count = 0
    for line in text.splitlines():
        if line.startswith("/END"):
            break
        if re.match(rf"^/{re.escape(keyword)}(?:/|$)", line, flags=re.IGNORECASE):
            count += 1
    return count


def test_fixed_width_cells_parse_touching_values_and_preserve_empty_fields():
    raw = "      10051.00000000000000E-04                 0.0"
    assert read_fields(raw, ("i", "r", "r")) == ["1005", "1.00000000000000E-04", "0.0"]

    move = "                 0.01.00000000000000E-03                 0.0                 0.0"
    assert read_fields(move, ("r", "r", "r", "r")) == ["0.0", "1.00000000000000E-03", "0.0", "0.0"]
    assert read_fields("         5                           0.8                                       1         1", ("i", "i", "r", "r", "i", "i", "i")) == ["5", None, "0.8", None, None, "1", "1"]


def test_issue38_two_original_comments_parse_dynamic_links_parameters_and_roles():
    parts_text = _text("fixture_parts.inc")
    materials_text = _text("fixture_materials.inc")
    assert "\n\n" in parts_text and "\n\n" in materials_text
    part_lines = parts_text.splitlines()
    parameter_index = next(index for index, line in enumerate(part_lines) if line.startswith("/PARAMETER/GLOBAL/REAL/"))
    assert part_lines[parameter_index + 1] == ""
    assert part_lines[parameter_index + 2].startswith("param")
    assert any(line == "" for line in materials_text.splitlines())
    result = RadiossDeckParser().parse_streams(
        BytesIO(parts_text.encode("utf-8")),
        BytesIO(materials_text.encode("utf-8")),
        parts_file="fixture_parts.inc",
        materials_file="fixture_materials.inc",
    )

    assert len(result["parts"]) == _count_cards(parts_text, "PART")
    assert len(result["materials"]) == _count_cards(materials_text, "MAT")
    assert len(result["properties"]) == _count_cards(materials_text, "PROP")
    assert result["parts"]
    assert result["materials"]
    assert all(part["property"] is not None for part in result["parts"])
    assert all(part["material"] is not None for part in result["parts"])
    assert all(part["references"]["property_status"] == "resolved" for part in result["parts"])
    assert all(part["references"]["material_status"] == "resolved" for part in result["parts"])

    parameters = {item["name"]: item for item in result["parameters"]}
    assert parameters["param1"]["value"] == 0.0572
    assert parameters["param2"]["value"] == 0.0572
    assert parameters["LC_E"]["value"] == 0.8
    assert result["unit_system"]["density_factor_to_g_cm3"] == 1e9
    assert result["unit_system"]["input"] == {"mass": "Mg", "length": "mm", "time": "s"}

    anisotropic = next(material for material in result["materials"] if material["subtype"] == "LAW93")
    assert anisotropic["fields"]["E11"] == 0.8
    assert anisotropic["fields"]["E22"] == 0.8
    assert anisotropic["fields"]["E33"] == 0.05
    assert anisotropic["fields"]["G12"] == 0.4
    assert anisotropic["fields"]["NU12"] is None

    by_id = {function["id"]: function for function in result["functions"]}
    assert by_id["61053"]["points"][0]["y"] == 144.150977
    assert by_id["1014"]["points"][0]["y"] == 0.054
    assert by_id["1015"]["points"][0]["y"] == 247.8
    assert by_id["1014"]["transform"]["ascale_x"] == 1.0
    assert by_id["1014"]["transform"]["fscale_y"] == 1e-3

    law70 = next(material for material in result["materials"] if material["subtype"] == "LAW70" and material["fields"].get("tension_function"))
    tension_id = str(law70["fields"]["tension_function"]["function_id"])
    tension_function = by_id[tension_id]
    tension_use = next(use for use in result["function_uses"] if use["function_id"] == tension_id and use["role"] == "tensile_scale_factor")
    assert tension_use["y_unit"] == "1"
    assert not result["orphan_failures"]
    assert all(failure["material_id"] is not None for failure in result["failures"])
    assert all(use["role"] == "failure_condition" and use["y_unit"] is None for use in result["function_uses"] if use["owner_type"] == "failure")


def test_issue38_fixed_width_property_columns_and_variable_thickness():
    result = RadiossDeckParser().parse_texts(_text("fixture_parts.inc"), _text("fixture_materials.inc"))
    properties = {item["id"]: item for item in result["properties"]}
    assert properties["3"]["thickness"] == 0.8
    assert properties["3"]["fields"]["Ithick"] == 1
    assert properties["3"]["fields"]["Iplas"] == 1
    assert properties["112"]["fields"]["Ip"] == 23
    assert properties["112"]["thickness"] == 8.0
    assert properties["128"]["thickness"] == 6.5


def test_issue35_parts_excerpt_keeps_variable_card_count_and_source_lines():
    source = _text("issue_35_parts.inc")
    result = RadiossDeckParser().parse_texts(source, "", parts_file="issue_35_parts.inc")
    assert result["parts"]
    assert len(result["parts"]) == _count_cards(source, "PART")
    assert all(part["source"]["file"] == "issue_35_parts.inc" for part in result["parts"])
    assert all(part["source"]["line"] > 0 for part in result["parts"])
    assert isinstance(result["unknown_cards"], list)


def test_issue36_law70_excerpt_separates_tension_ratio_from_stress_curves():
    source = _text("issue_36_eps.inc")
    result = RadiossDeckParser().parse_texts("", source, materials_file="issue_36_eps.inc")
    material = next(item for item in result["materials"] if item["subtype"] == "LAW70")
    assert len(material["fields"]["load_functions"]) > 0
    assert len(material["fields"]["unload_functions"]) > 0
    assert material["fields"]["tension_function"] is not None
    roles = {use["role"]: use for use in result["function_uses"]}
    assert roles["foam_loading"]["y_unit"] == "MPa"
    assert roles["foam_unloading"]["y_unit"] == "MPa"
    assert roles["tensile_scale_factor"]["y_unit"] == "1"


def test_issue37_material_excerpt_resolves_available_material_and_curve_records():
    source = _text("issue_37_plast.inc")
    result = RadiossDeckParser().parse_texts("", source, materials_file="issue_37_plast.inc")
    assert len(result["materials"]) == _count_cards(source, "MAT")
    assert len(result["functions"]) == _count_cards(source, "FUNCT")
    plastic_uses = [use for function in result["functions"] for use in function["uses"] if use["role"] == "plastic_stress_strain"]
    assert plastic_uses
    assert all(use["x_unit"] == "mm/mm" and use["y_unit"] == "MPa" for use in plastic_uses)


def test_real_expr_supports_bare_symbols_ampersand_references_and_rejects_code():
    blank = " " * 100
    parts = "\n".join((
        "/PARAMETER/GLOBAL/REAL/1", blank, "BASE      0.25",
        "/PARAMETER/GLOBAL/REAL_EXPR/2", blank, "TWICE     2*BASE",
        "/PARAMETER/GLOBAL/REAL_EXPR/3", blank, "COMBINED  &TWICE + 0.5",
        "/PARAMETER/GLOBAL/REAL_EXPR/4", blank, "INJECTION __import__('os').system('should_not_run')",
        "/END",
    ))
    result = RadiossDeckParser().parse_texts(parts, "")
    params = {item["name"]: item for item in result["parameters"]}
    assert params["TWICE"]["value"] == 0.5
    assert params["COMBINED"]["value"] == 1.0
    assert params["INJECTION"]["resolved"] is False
    assert any(warning["code"] == "MATERIAL_PARAMETER_UNRESOLVED" for warning in result["warnings"])
    assert result["parts"] == []


def test_parameter_cycles_warn_and_unknown_cards_do_not_abort_parsing():
    blank = " " * 100
    parts = "\n".join((
        "/PARAMETER/GLOBAL/REAL_EXPR/1", blank, "A         &B",
        "/PARAMETER/GLOBAL/REAL_EXPR/2", blank, "B         A",
        "/MYSTERY/CARD/17", "payload", "/END", "/PART/999", "ignored",
    ))
    result = RadiossDeckParser().parse_texts(parts, "")
    assert result["parts"] == []
    assert len(result["unknown_cards"]) == 1
    assert any(warning["code"] == "MATERIAL_PARAMETER_CYCLE" for warning in result["warnings"])
    assert any(warning["code"] == "MATERIAL_CARD_UNSUPPORTED" for warning in result["warnings"])


def test_skipped_card_payload_is_consumed_without_retaining_its_rows():
    def lines():
        yield b"/GRNOD/CSET/1\n"
        for _ in range(83_000):
            yield b"unused group-node payload\n"
        yield b"/PART/1\n"
        yield b"streamed part\n"
        yield b"         1         1         0\n"
        yield b"/END\n"

    parser = RadiossDeckParser()
    cards = parser._collect_cards(((lines(), "large_parts.inc"),))
    assert [card.keyword for card in cards] == ["PART"]
    assert len(cards[0].rows) == 2


def test_unknown_card_keeps_header_skips_large_body_and_parses_following_part():
    class InspectingParser(RadiossDeckParser):
        unknown_body_rows = -1

        def _unknown_cards(self, cards):
            unknown = next(card for card in cards if card.keyword == "MYSTERY")
            self.unknown_body_rows = len(unknown.rows)
            return super()._unknown_cards(cards)

    def lines():
        yield b"/MYSTERY/CARD/17\n"
        for _ in range(83_000):
            yield b"unregistered card payload\n"
        yield b"/PART/2\n"
        yield b"part after unknown card\n"
        yield b"         1         1         0\n"
        yield b"/END\n"

    parser = InspectingParser()
    result = parser.parse_lines(lines(), (), parts_file="unknown_then_part.inc")
    assert parser.unknown_body_rows == 0
    assert result["unknown_cards"] == [{"keyword": "/MYSTERY", "id": "17", "file": "unknown_then_part.inc", "line": 1}]
    assert len(result["parts"]) == 1
    assert result["parts"][0]["title"] == "part after unknown card"
