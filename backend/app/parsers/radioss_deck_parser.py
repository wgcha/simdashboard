"""Read-only parser for the Radioss Starter Deck cards used by the materials view.

Radioss numeric cards use 10-character cells: integer fields occupy one cell and
real fields occupy two.  Keeping that rule here avoids ambiguities where adjacent
fixed-width values have no separating whitespace.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence


CELL_WIDTH = 10
_NUMBER = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?")
_EXPR_TOKEN = re.compile(
    r"\s*(?:(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?)"
    r"|(?P<name>&?[A-Za-z_][A-Za-z0-9_.]*)|(?P<operator>[()+*/-]))"
)
_CARD_START = re.compile(r"^/([^\s]+)")
_SKIPPED_KEYWORDS = (
    "/SHELL", "/BRICK", "/TETRA4", "/TETRA10", "/SH3N", "/SPRING",
    "/BEAM", "/NODE", "/GRNOD", "/GRBRIC", "/GRSHEL", "/SURF",
    "/LINE", "/INTER", "/RBODY", "/RBE2", "/RBE3", "/TH", "/ANIM",
    "/SKEW", "/FRAME", "/TRANSFORM", "/BCS", "/GRAV", "/IMPVEL",
    "/INIVEL", "/ADMAS", "/DEF_", "/ANALY", "/IOFLAG", "/TITLE",
)
_SUPPORTED_KEYWORDS = frozenset({"BEGIN", "PARAMETER", "SUBSET", "PART", "PROP", "MAT", "FUNCT", "MOVE_FUNCT", "FAIL", "INCLUDE"})

_PROPERTY_LAYOUTS: dict[str, tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]] = {
    "SHELL": (
        (("i", "i", "i", "i", "i", "i", "r"), ("Ishell", "Ismstr", "Ish3n", "Idrill", "Ipinch", "reserved_1", "P_Thick_Fail")),
        (("r", "r", "r", "r", "r"), ("Hm", "Hf", "Hr", "Dm", "Dn")),
        (("i", "i", "r", "r", "i", "i", "i"), ("N", "reserved_2", "Thick", "Ashear", "reserved_3", "Ithick", "Iplas")),
    ),
    "SH_ORTH": (
        (("i", "i", "i", "i", "i", "i", "r"), ("Ishell", "Ismstr", "Ish3n", "Idrill", "reserved_1", "reserved_2", "P_Thick_Fail")),
        (("r", "r", "r", "r", "r"), ("Hm", "Hf", "Hr", "Dm", "Dn")),
        (("i", "i", "r", "r", "i", "i", "i"), ("N", "reserved_3", "Thick", "Ashear", "skew_ID", "Ithick", "Iplas")),
        (("r", "r", "r", "r", "r"), ("Vx", "Vy", "Vz", "Phi", "Ip")),
    ),
    "SOLID": (
        (("i",) * 8 + ("r",), ("Isolid", "Ismstr", "Iale", "Icpre", "Itetra10", "Inpts", "Itetra4", "Iframe", "Dn")),
        (("r",) * 5, ("qa", "qb", "h", "Lambda", "Mu")),
        (("r",) * 5, ("deltaT_min", "Vdef_min", "Vdef_max", "ASP_max", "COL_min")),
    ),
    "SOL_ORTH": (
        (("i",) * 8 + ("r",), ("Isolid", "Ismstr", "reserved_1", "Icpre", "Itetra10", "Inpts", "Itetra4", "Iframe", "Dn")),
        (("r", "r", "r"), ("qa", "qb", "h")),
        (("r", "r", "r", "i", "i", "i"), ("Vx", "Vy", "Vz", "skew_ID", "Ip", "Iorth")),
        (("r",), ("Phi",)),
        (("r",) * 5, ("deltaT_min", "Vdef_min", "Vdef_max", "ASP_max", "COL_min")),
    ),
    "CONNECT": (
        (("i",) * 8 + ("r",), ("Ismstr", "reserved_1", "reserved_2", "reserved_3", "reserved_4", "reserved_5", "reserved_6", "reserved_7", "True_thickness")),
    ),
    "SPRING": (
        (("r", "r", "i", "i", "i"), ("MASS", "reserved_1", "sens_ID", "Isflag", "Ileng")),
        (("r",) * 5, ("K1", "C1", "A1", "B1", "D1")),
        (("i",) * 6 + ("r", "r"), ("fct_ID11", "H1", "fct_ID21", "fct_ID31", "fct_ID41", "reserved_2", "DeltaMin", "DeltaMax")),
        (("r",) * 4, ("F1", "E1", "AScale1", "Hscale1")),
    ),
    "VOID": ((("r",), ("THICK",)),),
}

_MATERIAL_LAYOUTS: dict[str, tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]] = {
    "ELAST": ((("r",), ("RHO_I",)), (("r", "r"), ("E", "Nu"))),
    "VOID": ((("r", "r", "r"), ("RHO_I", "E", "Nu")),),
    "PLAS_TAB": (
        (("r",), ("RHO_I",)),
        (("r",) * 5, ("E", "Nu", "Eps_p_max", "Eps_t", "Eps_m")),
        (("i", "i", "r", "r", "r", "r"), ("N_funct", "F_smooth", "C_hard", "F_cut", "Eps_f", "VP")),
        (("i", "r", "i", "r", "r"), ("fct_IDp", "Fscale", "Fct_IDE", "EInf", "CE")),
        (("i",) * 5, ("func_ID1", "func_ID2", "func_ID3", "func_ID4", "func_ID5")),
        (("r",) * 5, ("Fscale_1", "Fscale_2", "Fscale_3", "Fscale_4", "Fscale_5")),
        (("r",) * 5, ("Eps_dot_1", "Eps_dot_2", "Eps_dot_3", "Eps_dot_4", "Eps_dot_5")),
    ),
    "PLAS_JOHNS": (
        (("r",), ("RHO_I",)),
        (("r", "r", "i", "i"), ("E", "Nu", "Iflag", "VP")),
        (("r",) * 5, ("SIG_Y", "UTS", "EUTS", "EPS_p_max", "SIG_max0")),
        (("r", "r", "i", "i", "r", "r"), ("c", "EPS_DOT_0", "ICC", "Fsmooth", "F_cut", "Chard")),
        (("r",) * 4, ("m", "T_melt", "rhoC_p", "T_r")),
    ),
    "FABRI": (
        (("r",), ("RHO_I",)),
        (("r", "r", "r"), ("E11", "E22", "NU12")),
        (("r", "r", "r"), ("G12", "G23", "G31")),
        (("r", "r", "r", "r", "i"), ("R_E", "reserved_1", "ZEROSTRESS", "FSCALE_POR", "SENS_ID")),
    ),
    "CONNECT": (
        (("r",), ("RHO_I",)),
        (("r", "r", "i", "i", "r"), ("E", "G", "Imass", "Icomp", "Ecomp")),
        (("i", "i", "r"), ("NB_funct", "Fsmooth", "Fcut")),
    ),
    "PAPER": (
        (("r",), ("RHO_I",)),
        (("r", "r", "r", "i", "i", "i"), ("E1", "E2", "E3", "Ires", "Itab", "Ismooth")),
        (("r",) * 4, ("nu21", "G12", "G23", "G13")),
        (("r",) * 3, ("K", "E3C", "CC")),
        (("r",) * 4, ("nu1p", "nu2p", "nu4p", "nu5p")),
        *(((("r",) * 4, (f"S0{k}", f"A0{k}", f"B0{k}", f"C0{k}")) for k in range(1, 6))),
        (("r",) * 3, ("ASIG", "BSIG", "CSIG")),
        (("r",) * 3, ("TAU0", "ATAU", "BTAU")),
    ),
    "LAW92": (
        (("r",), ("RHO_I",)),
        (("r", "r", "r"), ("Mu", "D", "LAM")),
        (("i", "i", "r", "r"), ("Itype", "Func_ID", "Nu", "Fscale")),
    ),
    "LAW93": (
        (("r",), ("RHO_I",)),
        (("r",) * 5, ("E11", "E22", "E33", "G12", "NU12")),
        (("r",) * 4, ("G13", "G23", "NU13", "NU23")),
        (("i", "i", "r"), ("NL", "VP", "Fcut")),
        (("r",) * 5, ("sigma_y", "QR1", "CR1", "QR2", "CR2")),
        (("r",) * 3, ("R11", "R22", "R12")),
        (("r",) * 3, ("R33", "R13", "R23")),
    ),
}

_LAW_INFO: dict[str, tuple[int | None, str, str | None]] = {
    "ELAST": (1, "LAW1_ELAST", "E"),
    "PLAS_JOHNS": (2, "LAW2_PLAS_JOHNS", "E"),
    "PLAS_TAB": (36, "LAW36_PLAS_TAB", "E"),
    "LAW70": (70, "LAW70_FOAM_TAB", "EO"),
    "LAW92": (92, "LAW92_HYPERELASTIC", None),
    "LAW93": (93, "LAW93_ORTH_PLAS", "E11"),
    "CONNECT": (59, "LAW59_CONNECT", "E"),
    "PAPER": (112, "LAW112_PAPER", "E1"),
    "VOID": (0, "LAW0_VOID", "E"),
    "FABRI": (None, "MAT_FABRI", "E11"),
}

_MASS_TO_GRAMS = {"Mg": 1e6, "mg": 1e-3, "g": 1.0, "gram": 1.0, "grams": 1.0, "kg": 1e3, "tonne": 1e6, "tonnes": 1e6, "lb": 453.59237, "slug": 14593.90294}
_LENGTH_TO_CM = {"cm": 1.0, "mm": 0.1, "m": 100.0, "um": 1e-4, "µm": 1e-4, "in": 2.54, "inch": 2.54, "ft": 30.48}


@dataclass(frozen=True)
class _Line:
    text: str
    number: int


@dataclass
class _Card:
    keyword: str
    subtype: str | None
    scope: str | None
    card_id: str | None
    file: str
    line: int
    rows: list[_Line] = field(default_factory=list)
    fail_name: str | None = None

    @property
    def source(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line}

    @property
    def key(self) -> tuple[str, str] | None:
        if self.card_id is None:
            return None
        qualified = f"{self.keyword}/{self.subtype}" if self.subtype else self.keyword
        return (qualified, self.card_id)


class _ExpressionParser:
    """A small arithmetic parser; it intentionally has no Python evaluation path."""

    def __init__(self, expression: str, resolve_name: Callable[[str], float | None]):
        self.expression = expression
        self.resolve_name = resolve_name
        self.tokens: list[tuple[str, str]] = []
        self.position = 0
        cursor = 0
        while cursor < len(expression):
            match = _EXPR_TOKEN.match(expression, cursor)
            if match is None:
                raise ValueError("unsupported expression token")
            kind = "number" if match.group("number") is not None else "name" if match.group("name") is not None else "operator"
            self.tokens.append((kind, match.group(kind)))
            cursor = match.end()
        if not self.tokens:
            raise ValueError("empty expression")

    def parse(self) -> float:
        value = self._sum(0)
        if self.position != len(self.tokens):
            raise ValueError("unexpected expression token")
        if not math.isfinite(value):
            raise ValueError("non-finite expression result")
        return value

    def _sum(self, depth: int) -> float:
        value = self._product(depth + 1)
        while self._peek("+") or self._peek("-"):
            operator = self._take()[1]
            right = self._product(depth + 1)
            value = value + right if operator == "+" else value - right
        return value

    def _product(self, depth: int) -> float:
        value = self._unary(depth + 1)
        while self._peek("*") or self._peek("/"):
            operator = self._take()[1]
            right = self._unary(depth + 1)
            value = value * right if operator == "*" else value / right
        return value

    def _unary(self, depth: int) -> float:
        if depth > 40:
            raise ValueError("expression nesting is too deep")
        if self._peek("+"):
            self._take()
            return self._unary(depth + 1)
        if self._peek("-"):
            self._take()
            return -self._unary(depth + 1)
        return self._atom(depth + 1)

    def _atom(self, depth: int) -> float:
        if depth > 40 or self.position >= len(self.tokens):
            raise ValueError("incomplete expression")
        kind, token = self._take()
        if kind == "number":
            return float(token.replace("D", "E").replace("d", "e"))
        if kind == "name":
            value = self.resolve_name(token.lstrip("&"))
            if value is None:
                raise ValueError(f"unresolved parameter {token}")
            return value
        if token == "(":
            value = self._sum(depth + 1)
            if not self._peek(")"):
                raise ValueError("unclosed parenthesis")
            self._take()
            return value
        raise ValueError("expected number, name or parenthesis")

    def _peek(self, token: str) -> bool:
        return self.position < len(self.tokens) and self.tokens[self.position][1] == token

    def _take(self) -> tuple[str, str]:
        value = self.tokens[self.position]
        self.position += 1
        return value


def cells(line: str, count: int) -> list[str]:
    """Split a row into 10-character cells, retaining empty cells."""
    if count < 0:
        raise ValueError("cell count must be non-negative")
    padded = line.ljust(CELL_WIDTH * count)
    return [padded[index * CELL_WIDTH:(index + 1) * CELL_WIDTH] for index in range(count)]


def read_fields(line: str, layout: Sequence[str]) -> list[str | None]:
    """Return stripped field text for an I10/F20 layout without whitespace splitting."""
    count = sum(1 if kind == "i" else 2 for kind in layout)
    row_cells = cells(line, count)
    values: list[str | None] = []
    position = 0
    for kind in layout:
        width = 1 if kind == "i" else 2
        raw = "".join(row_cells[position:position + width]).strip()
        values.append(raw or None)
        position += width
    return values


class RadiossDeckParser:
    """Parse two Radioss include files and normalize their material relationships."""

    def parse_texts(
        self,
        parts_text: str,
        materials_text: str,
        *,
        parts_file: str = "parts.inc",
        materials_file: str = "materials.inc",
    ) -> dict[str, Any]:
        """Fixture/convenience wrapper; production callers should pass safe streams."""
        return self.parse_lines(
            parts_text.splitlines(),
            materials_text.splitlines(),
            parts_file=parts_file,
            materials_file=materials_file,
        )

    def parse_streams(
        self,
        parts_stream: Iterable[str | bytes],
        materials_stream: Iterable[str | bytes],
        *,
        parts_file: str = "parts.inc",
        materials_file: str = "materials.inc",
        on_input_line: Callable[[str, int, str | bytes], None] | None = None,
        on_function_point: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]:
        """Parse line-iterable streams, such as handles yielded by open_stable_reader.

        The streams remain owned by the caller and are consumed once, line by line.
        Binary streams are decoded per line, so the parser never snapshots a file.
        """
        return self.parse_lines(
            parts_stream,
            materials_stream,
            parts_file=parts_file,
            materials_file=materials_file,
            on_input_line=on_input_line,
            on_function_point=on_function_point,
        )

    def parse_lines(
        self,
        parts_lines: Iterable[str | bytes],
        materials_lines: Iterable[str | bytes],
        *,
        parts_file: str = "parts.inc",
        materials_file: str = "materials.inc",
        source_streams: Iterable[tuple[Iterable[str | bytes], str]] | None = None,
        on_input_line: Callable[[str, int, str | bytes], None] | None = None,
        on_function_point: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]:
        """Parse line iterators without retaining source text or skipped bodies.

        ``source_streams`` lets a filesystem adapter supply an include closure
        while preserving the source filename attached to each parsed card.
        Existing two-stream callers keep their original behavior. Optional
        callbacks let bounded callers stop line intake or FUNCT point-row
        retention before the complete deck has been collected.
        """
        self._warnings: list[dict[str, Any]] = []
        sources = ((parts_lines, parts_file), (materials_lines, materials_file)) if source_streams is None else source_streams
        cards = self._collect_cards(sources, on_input_line=on_input_line, on_function_point=on_function_point)
        winners: dict[tuple[str, str], _Card] = {}
        ordered_keys: list[tuple[str, str]] = []
        unkeyed: list[_Card] = []
        for card in cards:
            if card.key is None:
                unkeyed.append(card)
                continue
            if card.key in winners:
                old = winners[card.key]
                self._warn(
                    "MATERIAL_CARD_DUPLICATE",
                    f"{card.keyword}/{card.subtype + '/' if card.subtype else ''}{card.card_id} is redefined; the later card is used.",
                    card,
                    overridden_by=card.source,
                    previous_source=old.source,
                )
            else:
                ordered_keys.append(card.key)
            winners[card.key] = card
        active_cards = [winners[key] for key in ordered_keys] + unkeyed

        parameter_cards = [card for card in active_cards if card.keyword == "PARAMETER"]
        parameters = self._parse_parameters(parameter_cards)
        resolve_parameter = self._parameter_resolver(parameters)

        units = self._parse_units([card for card in active_cards if card.keyword == "BEGIN"])
        properties = [self._parse_property(card, resolve_parameter) for card in active_cards if card.keyword == "PROP"]
        materials = [self._parse_material(card, resolve_parameter) for card in active_cards if card.keyword == "MAT"]
        functions = [self._parse_function(card, resolve_parameter) for card in active_cards if card.keyword == "FUNCT"]
        moves = [self._parse_move_function(card, resolve_parameter) for card in active_cards if card.keyword == "MOVE_FUNCT"]
        failures = [self._parse_failure(card, resolve_parameter) for card in active_cards if card.keyword == "FAIL"]
        subsets = [self._parse_subset(card, resolve_parameter) for card in active_cards if card.keyword == "SUBSET"]
        parts = [self._parse_part(card, resolve_parameter) for card in active_cards if card.keyword == "PART"]
        unknown_cards = self._unknown_cards(active_cards)

        property_by_id = {str(item["id"]): item for item in properties}
        material_by_id = {str(item["id"]): item for item in materials}
        failure_by_material: dict[str, list[dict[str, Any]]] = {}
        orphan_failures: list[dict[str, Any]] = []
        for failure in failures:
            material = material_by_id.get(str(failure["id"]))
            if material is None:
                failure["material_id"] = None
                orphan_failures.append(failure)
                self._warn("MATERIAL_FAILURE_ORPHAN", f"Failure model {failure['id']} has no material with the same ID.", failure["source"])
            else:
                failure["material_id"] = material["id"]
                failure_by_material.setdefault(material["id"], []).append(failure)

        unresolved: list[dict[str, Any]] = []
        for part in parts:
            prop = property_by_id.get(str(part["property_id"])) if part["property_id"] is not None else None
            material = material_by_id.get(str(part["material_id"])) if part["material_id"] is not None else None
            part["property"] = prop
            part["material"] = material
            part["failure_models"] = failure_by_material.get(material["id"], []) if material else []
            part["references"] = {
                "property_status": "resolved" if prop else "unresolved" if part["property_id"] is not None else "missing",
                "material_status": "resolved" if material else "unresolved" if part["material_id"] is not None else "missing",
            }
            for key in ("property_id", "material_id"):
                ref_id = part[key]
                if ref_id is not None and (property_by_id if key == "property_id" else material_by_id).get(str(ref_id)) is None:
                    issue = {"part_id": part["id"], "reference": key, "target_id": ref_id, "source": part["source"]}
                    unresolved.append(issue)
                    self._warn("MATERIAL_REFERENCE_UNRESOLVED", f"Part {part['id']} references missing {key} {ref_id}.", part["source"], target_id=ref_id)
            if prop is not None and prop.get("material_id") is not None and part["material_id"] != prop["material_id"]:
                self._warn("MATERIAL_PART_PROPERTY_MISMATCH", f"Part {part['id']} material {part['material_id']} differs from property material {prop['material_id']}.", part["source"], property_id=prop["id"])

        unit_system = self._density_units(units, materials)
        for material in materials:
            density = material.get("fields", {}).get("RHO_I")
            factor = unit_system.get("density_factor_to_g_cm3")
            material["density"] = {
                "raw": material.get("raw_fields", {}).get("RHO_I"),
                "value": density,
                "unit": unit_system.get("source_density_unit"),
                "converted_value": density * factor if density is not None and factor is not None else None,
                "converted_unit": "g/cm^3" if factor is not None else None,
            }
            law = _LAW_INFO.get(material["subtype"])
            if law is None:
                material["law_id"] = None
                material["card_label"] = f"MAT_{material['subtype']}"
                material["e_source_key"] = None
                self._warn("MATERIAL_LAW_UNKNOWN", f"No LAW mapping is defined for /MAT/{material['subtype']}.", material["source"])
            else:
                material["law_id"], material["card_label"], material["e_source_key"] = law
            material["representative_e"] = material.get("fields", {}).get(material.get("e_source_key")) if material.get("e_source_key") else None
            material["failures"] = failure_by_material.get(material["id"], [])

        move_by_id = {move["id"]: move for move in moves}
        function_by_id = {function["id"]: function for function in functions}
        for move in moves:
            if move["id"] not in function_by_id:
                self._warn("MATERIAL_MOVE_FUNCTION_ORPHAN", f"MOVE_FUNCT {move['id']} has no FUNCT with the same ID.", move["source"])
        for function in functions:
            move = move_by_id.get(function["id"])
            transform = self._effective_transform(move)
            function["transform"] = transform
            function["points"] = [
                {"x": point["x"] * transform["ascale_x"] + transform["ashift_x"], "y": point["y"] * transform["fscale_y"] + transform["fshift_y"]}
                for point in function["raw_points"]
            ]
            function["uses"] = []

        function_uses = self._function_uses(materials, properties, failures)
        for use in function_uses:
            function = function_by_id.get(use["function_id"])
            if function is None:
                self._warn("MATERIAL_FUNCTION_REFERENCE_UNRESOLVED", f"{use['owner_type']} {use['owner_id']} references missing function {use['function_id']}.", use["source"], target_id=use["function_id"])
                continue
            function["uses"].append(use)

        return {
            "parts": parts,
            "properties": properties,
            "materials": materials,
            "functions": functions,
            "moves": moves,
            "failures": failures,
            "orphan_failures": orphan_failures,
            "subsets": subsets,
            "parameters": list(parameters.values()),
            "function_uses": function_uses,
            "unit_system": unit_system,
            "unknown_cards": unknown_cards,
            "unresolved_references": unresolved,
            "warnings": self._warnings,
        }

    @staticmethod
    def _decode_line(raw_line: str | bytes) -> str:
        if isinstance(raw_line, str):
            return raw_line.rstrip("\r\n")
        if raw_line.startswith(b"\xef\xbb\xbf"):
            return raw_line.decode("utf-8-sig").rstrip("\r\n")
        for encoding in ("utf-8", "cp949", "latin-1"):
            try:
                return raw_line.decode(encoding).rstrip("\r\n")
            except UnicodeDecodeError:
                continue
        return raw_line.decode("latin-1").rstrip("\r\n")

    @staticmethod
    def _skip_body(card: _Card) -> bool:
        root = f"/{card.keyword}"
        return card.keyword not in _SUPPORTED_KEYWORDS or any(root.startswith(prefix) for prefix in _SKIPPED_KEYWORDS)

    @staticmethod
    def _discard_card(card: _Card) -> bool:
        root = f"/{card.keyword}"
        return any(root.startswith(prefix) for prefix in _SKIPPED_KEYWORDS)

    def _collect_cards(
        self,
        inputs: Iterable[tuple[Iterable[str | bytes], str]],
        *,
        on_input_line: Callable[[str, int, str | bytes], None] | None = None,
        on_function_point: Callable[[str, int], None] | None = None,
    ) -> list[_Card]:
        cards: list[_Card] = []
        for lines, filename in inputs:
            current: _Card | None = None
            pending_fail_name: str | None = None
            for number, raw_line in enumerate(lines, start=1):
                if on_input_line is not None:
                    on_input_line(filename, number, raw_line)
                line = self._decode_line(raw_line)
                if line.startswith("/END"):
                    if current is not None and not self._discard_card(current):
                        cards.append(current)
                        current = None
                    break
                if line.startswith("/"):
                    if current is not None and not self._discard_card(current):
                        cards.append(current)
                    current = self._new_card(line, filename, number, pending_fail_name)
                    if current.keyword == "FAIL":
                        pending_fail_name = None
                    continue
                if line.startswith("##HMNAME FAIL_MODEL"):
                    pending_fail_name = self._fail_name_from_meta(line)
                    continue
                if line.startswith("##") or line.startswith("#"):
                    continue
                if current is not None and not self._skip_body(current):
                    # Keep whitespace-only rows; they occupy fixed-width card positions.
                    if current.keyword == "FUNCT" and current.rows:
                        raw_point = read_fields(line, ("r", "r"))
                        if raw_point[0] is not None and raw_point[1] is not None and on_function_point is not None:
                            on_function_point(filename, number)
                    current.rows.append(_Line(line, number))
            if current is not None and not self._discard_card(current):
                cards.append(current)
        return cards

    @staticmethod
    def _new_card(line: str, filename: str, number: int, fail_name: str | None) -> _Card:
        match = _CARD_START.match(line)
        path = match.group(1) if match else "UNKNOWN"
        pieces = [piece for piece in path.split("/") if piece]
        if pieces and pieces[0].upper() == "END":
            keyword, subtype, scope, card_id = "END", None, None, None
        else:
            has_id = bool(pieces and re.fullmatch(r"[+-]?\d+", pieces[-1]))
            body = pieces[:-1] if has_id else pieces
            card_id = pieces[-1] if has_id else None
            keyword = body[0].upper() if body else "UNKNOWN"
            if keyword == "PARAMETER":
                scope = body[1].upper() if len(body) > 1 else None
                subtype = body[2].upper() if len(body) > 2 else None
            else:
                scope = None
                subtype = body[1].upper() if len(body) > 1 else None
        return _Card(keyword=keyword, subtype=subtype, scope=scope, card_id=card_id, file=filename, line=number, fail_name=fail_name if keyword == "FAIL" else None)

    @staticmethod
    def _fail_name_from_meta(line: str) -> str | None:
        tail = line[len("##HMNAME FAIL_MODEL"):].strip()
        pieces = tail.split()
        if not pieces:
            return None
        if pieces[-1].isdigit():
            pieces.pop()
        return " ".join(pieces) or None

    def _parse_parameters(self, cards: Sequence[_Card]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for card in cards:
            kind = card.subtype.upper() if card.subtype else ""
            if kind not in {"REAL", "INT", "REAL_EXPR"}:
                self._warn("MATERIAL_PARAMETER_TYPE_UNKNOWN", f"Unsupported parameter type {kind or 'unknown'}.", card)
                continue
            rows = list(card.rows)
            if kind in {"REAL", "INT"} and rows and not rows[0].text.strip():
                rows.pop(0)
            if not rows:
                self._warn("MATERIAL_PARAMETER_TRUNCATED", f"Parameter card {card.card_id} has no value row.", card)
                continue
            for row in rows:
                name = row.text[:10].strip()
                expression = row.text[10:].strip()
                if not name:
                    continue
                value_text = expression if kind == "REAL_EXPR" else row.text[10:30].strip()
                result[name.casefold()] = {
                    "name": name,
                    "id": card.card_id,
                    "kind": kind,
                    "expression": value_text if kind == "REAL_EXPR" else None,
                    "raw_value": None if kind == "REAL_EXPR" else value_text,
                    "value": None,
                    "resolved": False,
                    "source": {"file": card.file, "line": row.number},
                }
        return result

    def _parameter_resolver(self, parameters: dict[str, dict[str, Any]]) -> Callable[[str], float | None]:
        warned: set[str] = set()

        def resolve(name: str, stack: tuple[str, ...] = ()) -> float | None:
            key = name.casefold()
            parameter = parameters.get(key)
            if parameter is None:
                return None
            if key in stack or len(stack) >= 10:
                if key not in warned:
                    self._warn("MATERIAL_PARAMETER_CYCLE", f"Parameter reference cycle includes {parameter['name']}.", parameter["source"])
                    warned.add(key)
                return None
            if parameter["resolved"]:
                return parameter["value"]
            try:
                if parameter["kind"] == "REAL_EXPR":
                    value = _ExpressionParser(parameter["expression"] or "", lambda ref: resolve(ref, (*stack, key))).parse()
                else:
                    raw_value = parameter["raw_value"] or ""
                    value = self._evaluate_numeric(raw_value, lambda ref: resolve(ref, (*stack, key)))
                    if parameter["kind"] == "INT":
                        if not float(value).is_integer():
                            raise ValueError("integer parameter is not integral")
                        value = int(value)
                parameter["value"] = value
                parameter["resolved"] = True
                return float(value)
            except (ValueError, ZeroDivisionError, OverflowError):
                if key not in warned:
                    self._warn("MATERIAL_PARAMETER_UNRESOLVED", f"Parameter {parameter['name']} could not be safely resolved.", parameter["source"])
                    warned.add(key)
                return None

        def resolver(name: str) -> float | None:
            return resolve(name)

        for name in parameters:
            resolve(name)
        return resolver

    @staticmethod
    def _evaluate_numeric(raw: str, resolve_name: Callable[[str], float | None]) -> float:
        if not raw:
            raise ValueError("empty numeric field")
        return _ExpressionParser(raw, resolve_name).parse()

    def _number(self, raw: str | None, kind: str, card: _Card, row: _Line, resolve_name: Callable[[str], float | None]) -> int | float | None:
        if raw is None:
            return None
        try:
            value = self._evaluate_numeric(raw, resolve_name)
            if kind == "i":
                if not value.is_integer():
                    raise ValueError("non-integral integer field")
                return int(value)
            return value
        except (ValueError, ZeroDivisionError, OverflowError):
            self._warn("MATERIAL_FIELD_INVALID", f"Could not parse numeric field {raw!r}.", card, row=row.number)
            return None

    def _layout_row(self, card: _Card, row: _Line | None, layout: Sequence[str], names: Sequence[str], resolve_name: Callable[[str], float | None]) -> tuple[dict[str, Any], dict[str, str | None]]:
        if row is None:
            return ({name: None for name in names}, {name: None for name in names})
        raw_values = read_fields(row.text, layout)
        return (
            {name: self._number(raw, kind, card, row, resolve_name) for name, kind, raw in zip(names, layout, raw_values)},
            {name: raw for name, raw in zip(names, raw_values)},
        )

    @staticmethod
    def _title(card: _Card) -> str | None:
        for row in card.rows:
            if row.text.strip():
                return row.text[:100].rstrip()
        return None

    def _parse_part(self, card: _Card, resolve_name: Callable[[str], float | None]) -> dict[str, Any]:
        rows = [row for row in card.rows if row.text.strip()]
        title = rows[0].text[:100].rstrip() if rows else None
        data_row = rows[1] if len(rows) > 1 else None
        fields, raw_fields = self._layout_row(card, data_row, ("i", "i", "i", "r"), ("property_id", "material_id", "subset_id", "thickness"), resolve_name)
        return {"id": card.card_id, "title": title, **fields, "raw_fields": raw_fields, "source": card.source}

    def _parse_subset(self, card: _Card, resolve_name: Callable[[str], float | None]) -> dict[str, Any]:
        rows = list(card.rows)
        title = rows[0].text[:100].rstrip() if rows else None
        children: list[int] = []
        for row in rows[1:]:
            for raw in read_fields(row.text, ("i",) * max(1, (len(row.text) + CELL_WIDTH - 1) // CELL_WIDTH)):
                value = self._number(raw, "i", card, row, resolve_name)
                if value is not None:
                    children.append(value)
        return {"id": card.card_id, "title": title, "child_ids": children, "source": card.source}

    def _parse_property(self, card: _Card, resolve_name: Callable[[str], float | None]) -> dict[str, Any]:
        subtype = card.subtype or "UNKNOWN"
        rows = list(card.rows)
        title = rows[0].text[:100].rstrip() if rows else None
        fields: dict[str, Any] = {}
        raw_fields: dict[str, str | None] = {}
        layout = _PROPERTY_LAYOUTS.get(subtype)
        if layout is None:
            self._warn("MATERIAL_PROPERTY_TYPE_UNKNOWN", f"Unsupported property type {subtype}.", card)
        else:
            for index, (kinds, names) in enumerate(layout, start=1):
                parsed, raw = self._layout_row(card, rows[index] if index < len(rows) else None, kinds, names, resolve_name)
                fields.update(parsed)
                raw_fields.update(raw)
            if len(rows) < len(layout) + 1:
                self._warn("MATERIAL_CARD_TRUNCATED", f"Property {card.card_id} has fewer rows than /PROP/{subtype} requires.", card)
        thickness = fields.get("Thick")
        if thickness is None and subtype == "VOID":
            thickness = fields.get("THICK")
        if thickness is None:
            thickness_display = {"SOLID": "Solid", "SOL_ORTH": "Solid", "CONNECT": "Connect", "SPRING": "Spring", "VOID": "Void"}.get(subtype, subtype.title())
        else:
            thickness_display = self._format_thickness(thickness)
        return {"id": card.card_id, "subtype": subtype, "title": title, "fields": fields, "raw_fields": raw_fields, "thickness": thickness, "thickness_display": thickness_display, "source": card.source}

    def _parse_material(self, card: _Card, resolve_name: Callable[[str], float | None]) -> dict[str, Any]:
        subtype = card.subtype or "UNKNOWN"
        rows = list(card.rows)
        title = rows[0].text[:100].rstrip() if rows else None
        fields: dict[str, Any] = {}
        raw_fields: dict[str, str | None] = {}
        if subtype == "LAW70":
            self._parse_law70(card, rows, resolve_name, fields, raw_fields)
        else:
            layout = _MATERIAL_LAYOUTS.get(subtype)
            if layout is None:
                pass  # The LAW mapping below emits one warning while retaining the card.
            else:
                for index, (kinds, names) in enumerate(layout, start=1):
                    parsed, raw = self._layout_row(card, rows[index] if index < len(rows) else None, kinds, names, resolve_name)
                    fields.update(parsed)
                    raw_fields.update(raw)
                if subtype == "CONNECT":
                    count = fields.get("NB_funct") or 1
                    extra = (("i", "i", "r", "r"), ("YFun_IDN", "YFun_IDT", "SR_reference", "Fscale_yield"))
                    for index in range(min(count, 5000)):
                        row_index = len(layout) + 1 + index
                        parsed, raw = self._layout_row(card, rows[row_index] if row_index < len(rows) else None, extra[0], extra[1], resolve_name)
                        for key, value in parsed.items():
                            fields[f"{key}_{index + 1}"] = value
                        for key, value in raw.items():
                            raw_fields[f"{key}_{index + 1}"] = value
                    if len(rows) < len(layout) + count + 1:
                        self._warn("MATERIAL_CARD_TRUNCATED", f"CONNECT material {card.card_id} has fewer function rows than NB_funct.", card)
                if len(rows) < len(layout) + 1:
                    self._warn("MATERIAL_CARD_TRUNCATED", f"Material {card.card_id} has fewer rows than /MAT/{subtype} requires.", card)
        return {"id": card.card_id, "subtype": subtype, "title": title, "fields": fields, "raw_fields": raw_fields, "source": card.source}

    def _parse_law70(self, card: _Card, rows: Sequence[_Line], resolve_name: Callable[[str], float | None], fields: dict[str, Any], raw_fields: dict[str, str | None]) -> None:
        layouts = (
            (("r",), ("RHO_I",)),
            (("r", "r", "r", "r", "i"), ("EO", "NU", "E_max", "EPS_max", "Itens")),
            (("r", "i", "i", "i", "i", "r", "r"), ("F_cut", "Ismooth", "Nload", "Nunload", "Iflag", "Shape", "Hys")),
        )
        for index, (kinds, names) in enumerate(layouts, start=1):
            parsed, raw = self._layout_row(card, rows[index] if index < len(rows) else None, kinds, names, resolve_name)
            fields.update(parsed)
            raw_fields.update(raw)
        if len(rows) < 4:
            self._warn("MATERIAL_CARD_TRUNCATED", f"LAW70 material {card.card_id} is missing its load function rows.", card)
        nload = fields.get("Nload") or 1
        nunload = fields.get("Nunload") or 0
        itens = fields.get("Itens") or 0
        cursor = 3
        fields["load_functions"] = []
        fields["unload_functions"] = []
        for label, count, names in (
            ("load_functions", min(nload, 500000), ("function_id", "strain_rate", "scale")),
            ("unload_functions", min(nunload, 500000), ("function_id", "strain_rate", "scale")),
        ):
            for _ in range(count):
                row = rows[cursor] if cursor < len(rows) else None
                if row is None:
                    self._warn("MATERIAL_CARD_TRUNCATED", f"LAW70 material {card.card_id} has fewer {label} rows than declared.", card)
                    break
                values, raw = self._layout_row(card, row, ("i", "r", "r"), names, resolve_name)
                fields[label].append(values)
                cursor += 1
        fields["tension_function"] = None
        if itens:
            row = rows[cursor] if cursor < len(rows) else None
            if row is None:
                self._warn("MATERIAL_CARD_TRUNCATED", f"LAW70 material {card.card_id} declares a missing tension function row.", card)
            else:
                values, _raw = self._layout_row(card, row, ("i", "r"), ("function_id", "scale"), resolve_name)
                fields["tension_function"] = values

    def _parse_function(self, card: _Card, resolve_name: Callable[[str], float | None]) -> dict[str, Any]:
        rows = list(card.rows)
        title = rows[0].text[:100].rstrip() if rows else None
        points: list[dict[str, Any]] = []
        for row in rows[1:]:
            raw = read_fields(row.text, ("r", "r"))
            if raw == [None, None]:
                continue
            x = self._number(raw[0], "r", card, row, resolve_name)
            y = self._number(raw[1], "r", card, row, resolve_name)
            if x is None or y is None:
                self._warn("MATERIAL_FUNCTION_POINT_INVALID", f"Function {card.card_id} contains an invalid point.", card, row=row.number)
                continue
            points.append({"x": x, "y": y, "raw_x": raw[0], "raw_y": raw[1], "source": {"file": card.file, "line": row.number}})
        return {"id": card.card_id, "title": title, "raw_points": points, "points": [], "transform": None, "uses": [], "source": card.source}

    def _parse_move_function(self, card: _Card, resolve_name: Callable[[str], float | None]) -> dict[str, Any]:
        rows = list(card.rows)
        title = rows[0].text[:100].rstrip() if rows else None
        names = ("ascale_x", "fscale_y", "ashift_x", "fshift_y")
        fields, raw = self._layout_row(card, rows[1] if len(rows) > 1 else None, ("r",) * 4, names, resolve_name)
        if len(rows) < 2:
            self._warn("MATERIAL_CARD_TRUNCATED", f"MOVE_FUNCT {card.card_id} has no transform row.", card)
        return {"id": card.card_id, "title": title, **fields, "raw_fields": raw, "source": card.source}

    @staticmethod
    def _effective_transform(move: dict[str, Any] | None) -> dict[str, float]:
        if move is None:
            return {"ascale_x": 1.0, "fscale_y": 1.0, "ashift_x": 0.0, "fshift_y": 0.0}
        return {
            "ascale_x": move.get("ascale_x") or 1.0,
            "fscale_y": move.get("fscale_y") or 1.0,
            "ashift_x": move.get("ashift_x") or 0.0,
            "fshift_y": move.get("fshift_y") or 0.0,
        }

    def _parse_failure(self, card: _Card, resolve_name: Callable[[str], float | None]) -> dict[str, Any]:
        if card.subtype == "TENSSTRAIN":
            layout = (
                (("r", "r", "i", "r", "r", "i"), ("EPSILON_T1", "EPSILON_T2", "FCT_ID", "EPSILON_F1", "EPSILON_F2", "S_Flag")),
                (("i", "r", "r"), ("FCT_IDEL", "FSCALE_EL", "EI_REF")),
                (("i", "r"), ("FCT_ID_T", "FSCALE_T")),
                (("i",), ("FAIL_ID",)),
            )
        elif card.subtype == "CONNECT":
            layout = (
                (("r", "r", "r", "i", "i", "i", "i"), ("EPSILON_MAXN", "EXPONENT_N", "ALPHA_N", "R_FCT_IDN", "IFAIL", "IFAIL_SO", "ISYM")),
                (("r", "r", "r", "i"), ("EPSILON_MAXT", "EXPONENT_T", "ALPHA_T", "R_FCT_ID_T")),
                (("r",) * 5, ("EI_MAX", "EN_MAX", "ET_MAX", "N_N", "N_T")),
                (("r",) * 3, ("T_MAX", "N_SOFT", "AREA_SCALE")),
            )
        else:
            layout = ()
            self._warn("MATERIAL_FAILURE_TYPE_UNKNOWN", f"Unsupported failure model {card.subtype}.", card)
        fields: dict[str, Any] = {}
        raw_fields: dict[str, str | None] = {}
        for index, (kinds, names) in enumerate(layout):
            parsed, raw = self._layout_row(card, card.rows[index] if index < len(card.rows) else None, kinds, names, resolve_name)
            fields.update(parsed)
            raw_fields.update(raw)
        if layout and len(card.rows) < len(layout):
            self._warn("MATERIAL_CARD_TRUNCATED", f"Failure model {card.card_id} has fewer rows than required.", card)
        return {"id": card.card_id, "subtype": card.subtype, "title": card.fail_name, "fields": fields, "raw_fields": raw_fields, "material_id": None, "source": card.source}

    def _parse_units(self, cards: Sequence[_Card]) -> dict[str, Any]:
        if not cards:
            return {"runname": None, "input": {"mass": None, "length": None, "time": None}, "work": {"mass": None, "length": None, "time": None}}
        card = cards[0]
        rows = list(card.rows)
        def chars(row: _Line | None) -> list[str | None]:
            if row is None:
                return [None, None, None]
            return [row.text.ljust(60)[index * 20:(index + 1) * 20].strip() or None for index in range(3)]
        runname = rows[0].text[:100].rstrip() if rows else None
        input_units = chars(rows[2] if len(rows) > 2 else None)
        work_units = chars(rows[3] if len(rows) > 3 else None)
        return {
            "runname": runname,
            "input": {"mass": input_units[0], "length": input_units[1], "time": input_units[2]},
            "work": {"mass": work_units[0], "length": work_units[1], "time": work_units[2]},
            "source": card.source,
        }

    def _density_units(self, units: dict[str, Any], materials: Sequence[dict[str, Any]]) -> dict[str, Any]:
        mass = units.get("input", {}).get("mass")
        length = units.get("input", {}).get("length")
        mass_factor = _MASS_TO_GRAMS.get(mass or "")
        if mass_factor is None:
            mass_factor = _MASS_TO_GRAMS.get((mass or "").casefold())
        length_factor = _LENGTH_TO_CM.get((length or "").casefold())
        factor = float(f"{mass_factor / (length_factor ** 3):.15g}") if mass_factor is not None and length_factor is not None else None
        source_unit = f"{mass}/{length}^3" if mass and length else None
        if factor is None and any(material.get("raw_fields", {}).get("RHO_I") is not None for material in materials):
            self._warn("MATERIAL_UNITS_UNRESOLVED", f"Cannot convert density from {source_unit or 'unknown units'} to g/cm^3.", units.get("source", {"file": None, "line": None}))
        return {
            "input": units.get("input", {}),
            "work": units.get("work", {}),
            "runname": units.get("runname"),
            "source_density_unit": source_unit,
            "density_unit": "g/cm^3" if factor is not None else None,
            "density_factor_to_g_cm3": factor,
            "derived_from": f"{mass} to g and {length} to cm" if factor is not None else None,
        }

    def _function_uses(self, materials: Sequence[dict[str, Any]], properties: Sequence[dict[str, Any]], failures: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        uses: list[dict[str, Any]] = []
        def add(function_id: Any, owner_type: str, owner_id: str, role: str, x_unit: str | None, y_unit: str | None, source: dict[str, Any]) -> None:
            if function_id in (None, 0, "0"):
                return
            uses.append({"function_id": str(function_id), "owner_type": owner_type, "owner_id": owner_id, "role": role, "x_unit": x_unit, "y_unit": y_unit, "source": source})

        for material in materials:
            fields = material.get("fields", {})
            source = material["source"]
            if material["subtype"] == "PLAS_TAB":
                for key in ("func_ID1", "func_ID2", "func_ID3", "func_ID4", "func_ID5", "fct_IDp", "Fct_IDE"):
                    add(fields.get(key), "material", material["id"], "plastic_stress_strain", "mm/mm", "MPa", source)
            elif material["subtype"] == "LAW70":
                for group, role in (("load_functions", "foam_loading"), ("unload_functions", "foam_unloading")):
                    for entry in fields.get(group, []):
                        add(entry.get("function_id"), "material", material["id"], role, "mm/mm", "MPa", source)
                tension = fields.get("tension_function")
                if tension:
                    add(tension.get("function_id"), "material", material["id"], "tensile_scale_factor", "mm/mm", "1", source)
            elif material["subtype"] == "CONNECT":
                count = fields.get("NB_funct") or 1
                for index in range(1, min(count, 5000) + 1):
                    for suffix in ("N", "T"):
                        add(fields.get(f"YFun_ID{suffix}_{index}"), "material", material["id"], "connect_yield", None, None, source)
        for prop in properties:
            for key in ("fct_ID11", "fct_ID21", "fct_ID31", "fct_ID41"):
                add(prop.get("fields", {}).get(key), "property", prop["id"], "spring_response", "mm", "N", prop["source"])
        for failure in failures:
            for key in ("FCT_ID", "FCT_IDEL", "FCT_ID_T", "R_FCT_IDN", "R_FCT_ID_T"):
                add(failure.get("fields", {}).get(key), "failure", failure["id"], "failure_condition", None, None, failure["source"])
        return uses

    def _unknown_cards(self, cards: Sequence[_Card]) -> list[dict[str, Any]]:
        unknown: list[dict[str, Any]] = []
        for card in cards:
            root = f"/{card.keyword}"
            if card.keyword in _SUPPORTED_KEYWORDS or any(root.startswith(prefix) for prefix in _SKIPPED_KEYWORDS):
                continue
            item = {"keyword": root, "id": card.card_id, **card.source}
            unknown.append(item)
            self._warn("MATERIAL_CARD_UNSUPPORTED", f"Unsupported card {root}; its body was skipped.", card, card_id=card.card_id)
        return unknown

    def _warn(self, code: str, message: str, card_or_source: _Card | dict[str, Any], **details: Any) -> None:
        source = card_or_source.source if isinstance(card_or_source, _Card) else card_or_source
        warning = {"code": code, "message": message, "file": source.get("file"), "line": source.get("line")}
        if details:
            warning.update(details)
        self._warnings.append(warning)

    @staticmethod
    def _format_thickness(value: int | float) -> str:
        if float(value).is_integer():
            return f"{int(value)} mm"
        return f"{value:g} mm"


def parse_radioss_deck(parts_text: str, materials_text: str, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper for callers that already loaded the two include files."""
    return RadiossDeckParser().parse_texts(parts_text, materials_text, **kwargs)
