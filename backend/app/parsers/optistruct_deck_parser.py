"""Streaming, read-only parser for the OptiStruct (Nastran bulk) cards used by the materials view.

Usage-environment solver inputs are single ``.fem`` files of 500–1000 MB that are
almost entirely mesh (``GRID``, ``CQUAD4``, ``CHEXA`` …).  The parser reads them
line by line as bytes, drops mesh lines with one C-level ``startswith`` check,
and keeps only the property, material and table cards plus HyperMesh name
comments (``$HMNAME COMP/PROP/MAT/CURVES``, ``$* Material: … name: …``).

Field formats: small (8-character fields), large (``KEY*`` with 16-character
fields, ``*`` continuations) and free (comma separated).  Continuation lines
start with ``+``, ``*``, ``,`` or a blank first field.  Nastran real shorthand
(``3.3-9``, ``1.+5``, ``1.5D3``) is accepted.

The output maps onto the shape the materials UI already uses for Radioss decks
(``parts`` = HyperMesh components, ``properties``, ``materials``, ``functions`` =
tables) with ``solver: "OPTISTRUCT"``.  Nothing here touches storage: callers
pass a stream and an ``open_include`` callback.
"""
from __future__ import annotations

import re
import time
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Callable, Iterable

PARSER_VERSION = "optistruct-1"

# Mesh, loads and other bulk cards nobody in the materials view needs.  Every
# entry is a prefix of the line, so ``CQUAD`` also drops ``CQUAD4``/``CQUAD8``
# and large-field ``CQUAD4*``.  None of them is a prefix of a kept card.
SKIP_PREFIXES: tuple[bytes, ...] = tuple(word.encode("ascii") for word in (
    "GRID", "CQUAD", "CTRIA", "CHEXA", "CTETRA", "CPENTA", "CPYRA", "CBUSH", "CBAR", "CBEAM", "CROD", "CONROD",
    "CELAS", "CDAMP", "CGAP", "CWELD", "CFAST", "CSHEAR", "CONM", "CMASS", "CSEAM", "CVISC", "CTUBE",
    "RBE", "RBAR", "RJOINT", "RROD", "RSPLINE", "PLOTEL", "SPC", "MPC", "FORCE", "MOMENT", "PLOAD", "TEMP",
    "CORD", "SET", "SURF", "CONTACT", "TIE", "GRAV", "DLOAD", "TLOAD", "RLOAD", "DAREA", "EIGR", "SPOINT",
    "ASET", "BSET", "CSET", "QSET", "OMIT", "SUPORT", "TIC", "NLPARM", "NLOUT", "BLSEG", "BCPROP",
))
_INCLUDE_LINE = re.compile(rb"^INCLUDE(?:\s|$)", re.I)
_HMNAME = re.compile(r"^\$HMNAME\s+([A-Za-z_]+)\s+(-?\d+)\s*\"([^\"]*)\"(.*)$")
_HM_COMP_REST = re.compile(r"^\s*(-?\d+)\s*\"([^\"]*)\"\s*(-?\d+)?")
_HM_STAR = re.compile(r"^\$\*\s*(Component|Property|Material|Curve|Table)\s*:\s*(-?\d+)\s+name\s*:\s*(.*?)\s*$", re.I)
_NASTRAN_EXPONENT = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))([+-]\d+)$")

PROPERTY_CARDS = frozenset({"PSHELL", "PSOLID", "PBEAM", "PBEAML", "PBAR", "PBARL", "PBUSH", "PCOMP", "PCOMPG",
                            "PCOMPP", "PELAS", "PGAP", "PWELD", "PFAST", "PROD", "PSHEAR", "PDAMP", "PVISC",
                            "PMASS", "PTUBE", "PCONT"})
MATERIAL_CARDS = frozenset({"MAT1", "MAT2", "MAT8", "MAT9", "MAT10", "MATS1", "MATT1", "MATT8", "MATT9"})
TABLE_CARDS = frozenset({"TABLES1", "TABLEST", "TABLED1", "TABLEMD", "TABLEM1"})
KEPT_CARDS = PROPERTY_CARDS | MATERIAL_CARDS | TABLE_CARDS
_MATERIAL_EXTENSIONS = frozenset({"MATS1", "MATT1", "MATT8", "MATT9"})
_MAX_UNKNOWN_KINDS = 200
_PROGRESS_LINES = 1 << 16

# Logical field names after the card name (Nastran field 2 onward).
_LAYOUTS: dict[str, tuple[str, ...]] = {
    "PSHELL": ("PID", "MID1", "T", "MID2", "12I/T**3", "MID3", "TS/T", "NSM", "Z1", "Z2", "MID4"),
    "PSOLID": ("PID", "MID", "CORDM", "IN", "STRESS", "ISOP", "FCTN"),
    "PBAR": ("PID", "MID", "A", "I1", "I2", "J", "NSM"),
    "PBEAM": ("PID", "MID", "A", "I1", "I2", "I12", "J", "NSM"),
    "PBARL": ("PID", "MID", "GROUP", "TYPE"),
    "PBEAML": ("PID", "MID", "GROUP", "TYPE"),
    "PROD": ("PID", "MID", "A", "J", "C", "NSM"),
    "PSHEAR": ("PID", "MID", "T", "NSM", "F1", "F2"),
    "PTUBE": ("PID", "MID", "OD", "T", "NSM", "OD2"),
    "PGAP": ("PID", "U0", "F0", "KA", "KB", "KT", "MU1", "MU2"),
    "PWELD": ("PID", "MID", "D", "", "", "MSET", "", "TYPE"),
    "PFAST": ("PID", "D", "MCID", "MFLAG", "KT1", "KT2", "KT3", "KR1", "KR2", "KR3", "MASS", "GE"),
    "PCOMP": ("PID", "Z0", "NSM", "SB", "FT", "TREF", "GE", "LAM"),
    "PCOMPG": ("PID", "Z0", "NSM", "SB", "FT", "TREF", "GE", "LAM"),
    "PCOMPP": ("PID", "Z0", "NSM", "SB", "FT", "TREF", "GE"),
    "MAT1": ("MID", "E", "G", "NU", "RHO", "A", "TREF", "GE", "ST", "SC", "SS", "MCSID"),
    "MAT2": ("MID", "G11", "G12", "G13", "G22", "G23", "G33", "RHO", "A1", "A2", "A3", "TREF", "GE", "ST", "SC",
             "SS", "MCSID"),
    "MAT8": ("MID", "E1", "E2", "NU12", "G12", "G1Z", "G2Z", "RHO", "A1", "A2", "TREF", "Xt", "Xc", "Yt", "Yc", "S",
             "GE", "F12", "STRN"),
    "MAT9": ("MID", "G11", "G12", "G13", "G14", "G15", "G16", "G22", "G23", "G24", "G25", "G26", "G33", "G34", "G35",
             "G36", "G44", "G45", "G46", "G55", "G56", "G66", "RHO", "A1", "A2", "A3", "A4", "A5", "A6", "TREF", "GE"),
    "MAT10": ("MID", "BULK", "RHO", "C", "GE"),
    "MATS1": ("MID", "TID", "TYPE", "H", "YF", "HR", "LIMIT1", "LIMIT2"),
    "MATT1": ("MID", "T(E)", "T(G)", "T(NU)", "T(RHO)", "T(A)", "", "T(GE)", "T(ST)", "T(SC)", "T(SS)"),
}
# Material id field by property card (the part's material comes from its property).
_PROPERTY_MATERIAL = {"PSHELL": "MID1", "PSOLID": "MID", "PBAR": "MID", "PBEAM": "MID", "PBARL": "MID",
                      "PBEAML": "MID", "PROD": "MID", "PSHEAR": "MID", "PTUBE": "MID", "PWELD": "MID"}
_REPRESENTATIVE_E = {"MAT1": "E", "MAT8": "E1"}
_HM_PROPERTY_TYPES = {"1": "PBAR", "2": "PBUSH", "3": "PBEAM", "4": "PSHELL", "5": "PSOLID"}


class OptiStructParseError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def nastran_float(text: str | None) -> float | None:
    """Nastran real: ``1.5``, ``1.5E3``, ``1.5D3``, ``3.3-9`` (= 3.3E-9), ``1.+5``; ``None`` for blank/non-numbers."""
    if text is None:
        return None
    value = text.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    value = value.upper().replace("D", "E")
    try:
        return float(value)
    except ValueError:
        pass
    match = _NASTRAN_EXPONENT.match(value)
    if match is None:
        return None
    try:
        return float(f"{match.group(1)}E{match.group(2)}")
    except ValueError:
        return None


def nastran_value(text: str | None) -> int | float | str | None:
    """Integer, real (Nastran shorthand included) or the stripped text; ``None`` when blank."""
    if text is None:
        return None
    value = text.strip()
    if not value:
        return None
    if re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    number = nastran_float(value)
    return number if number is not None else value


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _line_fields(line: str, first: bool) -> list[str]:
    """Data fields of one physical line (Nastran field 2…9 or the large/free equivalent)."""
    text = line.rstrip("\r\n")
    if "," in text:
        tokens = text.split(",")
        large = tokens[0].strip().endswith("*")
        count = 4 if large else 8
        data = [token.strip() for token in tokens[1:1 + count]]
        return data + [""] * (count - len(data))
    large = (text[:8].rstrip().endswith("*")) if first else text.startswith("*")
    if large:
        return [text[8 + 16 * index:24 + 16 * index].strip() for index in range(4)]
    return [text[8 + 8 * index:16 + 8 * index].strip() for index in range(8)]


@dataclass
class _Card:
    keyword: str
    file: str
    line: int
    lines: list[str] = field(default_factory=list)

    @property
    def source(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line}

    def fields(self) -> list[str]:
        values: list[str] = []
        for index, line in enumerate(self.lines):
            values.extend(_line_fields(line, index == 0))
        return values

    def rows(self) -> list[list[str]]:
        return [_line_fields(line, index == 0) for index, line in enumerate(self.lines)]


@dataclass
class _Stats:
    bytes: int = 0
    lines: int = 0
    skipped_lines: int = 0
    kept_cards: int = 0


class OptiStructDeckParser:
    """One parse of one root file (and its INCLUDE files)."""

    def __init__(self, *, open_include: Callable[[str, str], AbstractContextManager[tuple[BinaryIO, str, int]]] | None = None,
                 on_progress: Callable[[int, int], None] | None = None, check: Callable[[], None] | None = None,
                 max_include_depth: int = 5, max_include_files: int = 500, max_table_points: int = 2_000_000) -> None:
        self._open_include = open_include
        self._on_progress = on_progress
        self._check = check
        self._max_include_depth = max_include_depth
        self._max_include_files = max_include_files
        self._max_table_points = max_table_points
        self._cards: dict[tuple[str, str], _Card] = {}
        self._order: list[tuple[str, str]] = []
        self._extra: list[_Card] = []              # cards without a unique key (PELAS pairs are keyed later)
        self._hm_names: dict[tuple[str, str], str] = {}
        self._components: dict[str, dict[str, Any]] = {}
        self._component_order: list[str] = []
        self._unknown: dict[str, int] = {}
        self._warnings: list[dict[str, Any]] = []
        self._files: list[dict[str, Any]] = []
        self._active: list[str] = []
        self._stats = _Stats()
        self._total_hint = 0
        self._table_points = 0

    # -- reading -----------------------------------------------------------------------------
    def parse(self, stream: Iterable[bytes], filename: str, size: int | None = None) -> dict[str, Any]:
        started = time.monotonic()
        self._total_hint = int(size or 0)
        self._files.append({"file": filename, "size_bytes": size, "depth": 0})
        self._read(stream, filename, 0)
        result = self._assemble()
        result["stats"] = {
            "bytes": self._stats.bytes, "lines": self._stats.lines, "skipped_mesh_lines": self._stats.skipped_lines,
            "kept_cards": self._stats.kept_cards, "seconds": round(time.monotonic() - started, 3),
        }
        result["includes"] = self._files
        return result

    def _progress(self) -> None:
        if self._check is not None:
            self._check()
        if self._on_progress is not None:
            self._on_progress(self._stats.bytes, max(self._total_hint, self._stats.bytes))

    def _read(self, stream: Iterable[bytes], filename: str, depth: int) -> None:
        key = filename.casefold()
        if key in self._active:
            raise OptiStructParseError("MATERIALS_INCLUDE_CYCLE", "INCLUDE 파일 사이에 순환 참조가 있습니다.")
        self._active.append(key)
        stats = self._stats
        skip = SKIP_PREFIXES
        current: _Card | None = None
        number = 0
        for raw in stream:
            number += 1
            stats.lines += 1
            stats.bytes += len(raw)
            if not number & (_PROGRESS_LINES - 1):
                self._progress()
            if raw.startswith(skip):
                stats.skipped_lines += 1
                if current is not None:   # a mesh card ends the kept card before it
                    self._keep(current)
                    current = None
                continue
            first = raw[:1]
            if first == b"$":
                if raw.startswith(b"$HMNAME") or raw.startswith(b"$*"):
                    self._comment(raw)
                continue
            if first in (b"+", b"*", b",") or raw.startswith(b"        ") or first == b"\t":
                if current is not None and raw.strip():
                    current.lines.append(raw.decode("latin-1"))
                continue
            stripped = raw.strip()
            if not stripped:
                continue
            if current is not None:
                self._keep(current)
                current = None
            if _INCLUDE_LINE.match(raw):
                self._include(raw, filename, number, depth)
                continue
            text = raw.decode("latin-1")
            head = text.split(",", 1)[0] if "," in text[:10] else text[:8]
            words = head.split()
            keyword = words[0].rstrip("*").upper() if words else ""
            if keyword in KEPT_CARDS:
                current = _Card(keyword, filename, number, [text])
            elif keyword == "ENDDATA":
                break
            elif keyword and (keyword in self._unknown or len(self._unknown) < _MAX_UNKNOWN_KINDS):
                self._unknown[keyword] = self._unknown.get(keyword, 0) + 1
        if current is not None:
            self._keep(current)
        self._active.pop()
        self._progress()

    def _keep(self, card: _Card) -> None:
        self._stats.kept_cards += 1
        fields = card.fields()
        identifier = fields[0].strip() if fields else ""
        if card.keyword == "PELAS":
            self._extra.append(card)
            return
        if not identifier:
            self._warn("OPTISTRUCT_CARD_WITHOUT_ID", f"{card.keyword} 카드에 ID가 없어 무시했습니다.", card.source)
            return
        if card.keyword in _MATERIAL_EXTENSIONS:
            group = card.keyword          # MATS1/MATTx extend the material with the same ID
        elif card.keyword in MATERIAL_CARDS:
            group = "MAT"
        elif card.keyword in PROPERTY_CARDS:
            group = "PROP"
        else:
            group = "TABLE"
        key = (group, identifier)
        previous = self._cards.get(key)
        if previous is not None:
            self._warn("OPTISTRUCT_CARD_DUPLICATE",
                       f"{card.keyword} {identifier}가 다시 정의되어 나중 카드를 사용합니다.", card.source,
                       previous_source=previous.source)
        else:
            self._order.append(key)
        self._cards[key] = card

    def _comment(self, raw: bytes) -> None:
        text = _decode_text(raw).rstrip("\r\n")
        match = _HMNAME.match(text)
        if match is not None:
            kind, identifier, name, rest = match.group(1).upper(), match.group(2), match.group(3), match.group(4)
            if kind == "COMP":
                comp = _HM_COMP_REST.match(rest)
                if identifier not in self._components:
                    self._component_order.append(identifier)
                self._components[identifier] = {
                    "id": identifier, "name": name,
                    "property_id": comp.group(1) if comp else None,
                    "property_name": comp.group(2) if comp else None,
                    "property_type_code": comp.group(3) if comp and comp.group(3) else None,
                }
            elif kind in {"PROP", "MAT", "CURVES", "CURVE", "TABLE"}:
                self._hm_names[("CURVE" if kind.startswith("CURVE") or kind == "TABLE" else kind, identifier)] = name
            return
        star = _HM_STAR.match(text)
        if star is not None:
            kind = star.group(1).upper()
            identifier, name = star.group(2), star.group(3)
            if kind == "COMPONENT":
                if identifier not in self._components:
                    self._component_order.append(identifier)
                    self._components[identifier] = {"id": identifier, "name": name, "property_id": None,
                                                    "property_name": None, "property_type_code": None}
            else:
                slot = {"PROPERTY": "PROP", "MATERIAL": "MAT"}.get(kind, "CURVE")
                self._hm_names.setdefault((slot, identifier), name)

    def _include(self, raw: bytes, filename: str, number: int, depth: int) -> None:
        text = _decode_text(raw).rstrip("\r\n")
        value = text[7:].strip()
        if value[:1] in {"'", '"'}:
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0:
                self._warn("OPTISTRUCT_INCLUDE_UNSUPPORTED", "여러 줄에 걸친 INCLUDE 경로는 읽지 않았습니다.",
                           {"file": filename, "line": number})
                return
            value = value[1:end]
        else:
            value = value.split("$", 1)[0].strip()
        source = {"file": filename, "line": number}
        if not value:
            self._warn("OPTISTRUCT_INCLUDE_INVALID", "빈 INCLUDE 경로입니다.", source)
            return
        if self._open_include is None:
            self._warn("OPTISTRUCT_INCLUDE_SKIPPED", f"INCLUDE {value}는 읽지 않았습니다.", source, include=value)
            return
        if depth + 1 > self._max_include_depth:
            raise OptiStructParseError("MATERIALS_INCLUDE_DEPTH_LIMIT",
                                       f"INCLUDE 깊이는 {self._max_include_depth}단계 이하여야 합니다.", 413)
        if len(self._files) >= self._max_include_files:
            raise OptiStructParseError("MATERIALS_INCLUDE_FILE_LIMIT", "INCLUDE 파일 수가 허용 한도를 초과했습니다.", 413)
        stack = ExitStack()
        try:
            stream, resolved, size = stack.enter_context(self._open_include(filename, value))
        except OptiStructParseError as error:
            stack.close()
            if error.code in {"MATERIALS_INCLUDE_CYCLE", "MATERIALS_INCLUDE_DEPTH_LIMIT", "MATERIALS_INCLUDE_FILE_LIMIT",
                              "MATERIALS_FILE_SIZE_LIMIT", "MATERIALS_PARSE_TIME_LIMIT", "MATERIALS_FILE_CHANGED"}:
                raise
            self._warn(error.code, f"INCLUDE {value}: {error}", source, include=value)
            return
        with stack:
            self._files.append({"file": resolved, "size_bytes": size, "depth": depth + 1, "included_from": source})
            self._total_hint += int(size or 0)
            self._read(stream, resolved, depth + 1)

    def _warn(self, code: str, message: str, source: dict[str, Any] | None, **details: Any) -> None:
        if len(self._warnings) >= 500:
            return
        item = {"code": code, "message": message, "file": (source or {}).get("file"), "line": (source or {}).get("line")}
        item.update(details)
        self._warnings.append(item)

    # -- assembly ----------------------------------------------------------------------------
    @staticmethod
    def _named(card: _Card, values: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
        layout = _LAYOUTS.get(card.keyword, ())
        fields: dict[str, Any] = {}
        raw: dict[str, str] = {}
        for index, name in enumerate(layout):
            if not name or index >= len(values):
                continue
            text = values[index].strip()
            if not text:
                continue
            raw[name] = text
            fields[name] = nastran_value(text)
        return fields, raw

    def _property(self, card: _Card) -> list[dict[str, Any]]:
        values = card.fields()
        fields, raw = self._named(card, values)
        thickness: float | None = None
        material_id: str | None = None
        extra: dict[str, Any] = {}
        keyword = card.keyword
        if keyword in _PROPERTY_MATERIAL:
            material_id = raw.get(_PROPERTY_MATERIAL[keyword]) or None
        if keyword == "PSHELL":
            thickness = nastran_float(raw.get("T"))
        elif keyword in {"PBARL", "PBEAML"}:
            dims = [nastran_float(value) for value in values[8:] if value.strip() and value.strip().upper() != "NSM"]
            extra["DIM"] = [value for value in dims if value is not None][:40]
        elif keyword == "PBUSH":
            for row in range(0, len(values) // 8):
                chunk = values[row * 8:row * 8 + 8]
                flag = chunk[1].strip().upper()
                if not flag:
                    continue
                numbers = [nastran_value(value) for value in chunk[2:8]]
                labels = {"K": "K", "B": "B", "GE": "GE", "RCV": "RCV", "M": "M"}.get(flag, flag)
                for index, number in enumerate(numbers, start=1):
                    if number is not None:
                        fields[f"{labels}{index}"] = number
                        raw[f"{labels}{index}"] = chunk[1 + index].strip()
        elif keyword in {"PCOMP", "PCOMPG"}:
            plies: list[dict[str, Any]] = []
            last_mid: str | None = None
            last_t: float | None = None
            if keyword == "PCOMP":
                tail = values[8:]
                for start in range(0, len(tail), 4):
                    chunk = (tail[start:start + 4] + ["", "", "", ""])[:4]
                    if not any(part.strip() for part in chunk):
                        continue
                    mid = chunk[0].strip() or last_mid
                    t = nastran_float(chunk[1]) if chunk[1].strip() else last_t
                    plies.append({"MID": mid, "T": t, "THETA": nastran_float(chunk[2]), "SOUT": chunk[3].strip() or None})
                    last_mid, last_t = mid, t
            else:
                tail = values[8:]
                for start in range(0, len(tail), 8):
                    chunk = (tail[start:start + 8] + [""] * 8)[:8]
                    if not any(part.strip() for part in chunk[:5]):
                        continue
                    mid = chunk[1].strip() or last_mid
                    t = nastran_float(chunk[2]) if chunk[2].strip() else last_t
                    plies.append({"GPLYID": chunk[0].strip() or None, "MID": mid, "T": t,
                                  "THETA": nastran_float(chunk[3]), "SOUT": chunk[4].strip() or None})
                    last_mid, last_t = mid, t
            if plies:
                extra["plies"] = plies
                extra["ply_count"] = len(plies)
                thicknesses = [ply["T"] for ply in plies if isinstance(ply.get("T"), float)]
                if len(thicknesses) == len(plies):
                    thickness = sum(thicknesses)
                    if str(raw.get("LAM", "")).upper() == "SYM":
                        thickness *= 2
                material_id = next((str(ply["MID"]) for ply in plies if ply.get("MID")), None)
        elif keyword == "PELAS":
            entries = []
            for offset in (0, 4):
                chunk = (values[offset:offset + 4] + ["", "", "", ""])[:4]
                if not chunk[0].strip():
                    continue
                item_fields = {name: nastran_value(value) for name, value in zip(("PID", "K", "GE", "S"), chunk)
                               if value.strip()}
                item_raw = {name: value.strip() for name, value in zip(("PID", "K", "GE", "S"), chunk) if value.strip()}
                entries.append(self._property_entry(card, chunk[0].strip(), item_fields, item_raw, None, None, {}))
            return entries
        elif not _LAYOUTS.get(keyword):
            for index, value in enumerate(values):
                if value.strip():
                    fields[f"F{index + 2}"] = nastran_value(value)
                    raw[f"F{index + 2}"] = value.strip()
        identifier = values[0].strip() if values else ""
        return [self._property_entry(card, identifier, fields, raw, thickness, material_id, extra)]

    def _property_entry(self, card: _Card, identifier: str, fields: dict[str, Any], raw: dict[str, str],
                        thickness: float | None, material_id: str | None, extra: dict[str, Any]) -> dict[str, Any]:
        fields = {key: value for key, value in fields.items() if key != "PID"}
        raw = {key: value for key, value in raw.items() if key != "PID"}
        fields.update(extra)
        display = None
        if thickness is not None:
            display = f"{thickness:g}"
        elif card.keyword == "PSHELL" and raw.get("T"):
            display = raw["T"]
        return {
            "id": identifier, "subtype": card.keyword, "title": self._hm_names.get(("PROP", identifier)),
            "fields": fields, "raw_fields": raw, "thickness": thickness, "thickness_display": display,
            "material_id": material_id, "source": card.source,
        }

    def _material(self, card: _Card) -> dict[str, Any]:
        values = card.fields()
        fields, raw = self._named(card, values)
        identifier = values[0].strip()
        fields.pop("MID", None)
        raw.pop("MID", None)
        e_key = _REPRESENTATIVE_E.get(card.keyword)
        representative = fields.get(e_key) if e_key else None
        return {
            "id": identifier, "subtype": card.keyword, "title": self._hm_names.get(("MAT", identifier)),
            "fields": fields, "raw_fields": raw,
            "density": {"raw": raw.get("RHO"), "value": nastran_float(raw.get("RHO")), "unit": None,
                        "converted_value": None, "converted_unit": None},
            "representative_e": representative if isinstance(representative, (int, float)) else None,
            "law_id": None, "card_label": card.keyword, "failures": [], "source": card.source,
        }

    def _table(self, card: _Card) -> dict[str, Any]:
        identifier = card.fields()[0].strip()
        points: list[dict[str, float]] = []
        extra: dict[str, Any] = {}
        uses_tables: list[dict[str, Any]] = []
        x_label, y_label = "X", "Y"
        if card.keyword in {"TABLES1", "TABLED1", "TABLEM1", "TABLEST"}:
            values = card.fields()
            header = values[:8]
            tail = values[8:]
            numbers: list[str] = []
            for value in tail:
                text = value.strip()
                if text.upper() == "ENDT":
                    break
                if text.upper() == "SKIP":
                    continue
                if text:
                    numbers.append(text)
            if card.keyword == "TABLEST":
                for index in range(0, len(numbers) - 1, 2):
                    uses_tables.append({"temperature": nastran_float(numbers[index]), "table_id": numbers[index + 1]})
                extra["temperature_tables"] = uses_tables
            else:
                for index in range(0, len(numbers) - 1, 2):
                    x, y = nastran_float(numbers[index]), nastran_float(numbers[index + 1])
                    if x is not None and y is not None:
                        points.append({"x": x, "y": y})
                if len(numbers) % 2:
                    self._warn("OPTISTRUCT_TABLE_ODD_VALUES", f"{card.keyword} {identifier}의 값 개수가 홀수입니다.", card.source)
                if card.keyword == "TABLES1":
                    extra["TYPE"] = header[1].strip() or None
                    x_label, y_label = "변형률", "응력"
                elif card.keyword == "TABLED1":
                    extra["XAXIS"] = header[1].strip() or None
                    extra["YAXIS"] = header[2].strip() or None
        else:  # TABLEMD: one row per physical continuation line (HyperMesh export)
            rows = card.rows()
            dims = nastran_value(rows[0][1]) if rows and len(rows[0]) > 1 else None
            columns = (int(dims) + 1) if isinstance(dims, int) and 0 < dims < 8 else 3
            extra["NDIM"] = dims
            third: set[float] = set()
            for row in rows[1:]:
                numbers = [nastran_float(value) for value in row if value.strip()]
                numbers = [value for value in numbers if value is not None][:columns]
                if len(numbers) < 2:
                    continue
                points.append({"x": numbers[1], "y": numbers[0]})
                if len(numbers) > 2:
                    third.add(numbers[2])
            extra["columns"] = columns
            extra["third_column_values"] = sorted(third)[:20]
            if len(third) > 1:
                self._warn("OPTISTRUCT_TABLEMD_MULTI_CURVE",
                           f"TABLEMD {identifier}에 셋째 열 값이 {len(third)}개라 곡선을 한 줄로 이었습니다.", card.source)
            x_label, y_label = "2열(소성 변형률로 추정)", "1열(응력으로 추정)"
        self._table_points += len(points)
        if self._table_points > self._max_table_points:
            raise OptiStructParseError("MATERIALS_FUNCTION_POINT_LIMIT", "표 곡선 점 전체가 허용 한도를 초과했습니다.", 413)
        return {
            "id": identifier, "title": self._hm_names.get(("CURVE", identifier)), "card": card.keyword,
            "points": points, "point_count": len(points), "uses": [], "fields": extra,
            "x_label": x_label, "y_label": y_label, "source": card.source,
        }

    def _assemble(self) -> dict[str, Any]:
        properties: list[dict[str, Any]] = []
        materials: list[dict[str, Any]] = []
        functions: list[dict[str, Any]] = []
        nonlinear: list[tuple[_Card, dict[str, Any], dict[str, str]]] = []
        for key in self._order:
            card = self._cards[key]
            group = key[0]
            if group == "PROP":
                properties.extend(self._property(card))
            elif group == "MAT":
                materials.append(self._material(card))
            elif group == "TABLE":
                functions.append(self._table(card))
            else:
                fields, raw = self._named(card, card.fields())
                nonlinear.append((card, fields, raw))
        for card in self._extra:
            properties.extend(self._property(card))

        property_by_id: dict[str, dict[str, Any]] = {}
        for item in properties:
            if item["id"] in property_by_id:
                self._warn("OPTISTRUCT_CARD_DUPLICATE", f"Property {item['id']}가 다시 정의되어 나중 카드를 사용합니다.",
                           item["source"])
            property_by_id[item["id"]] = item
        properties = list(property_by_id.values())
        material_by_id = {item["id"]: item for item in materials}
        function_by_id = {item["id"]: item for item in functions}

        def use(function_id: str | None, owner_type: str, owner_id: str, role: str, source: dict[str, Any],
                x_unit: str | None = None, y_unit: str | None = None) -> None:
            if not function_id:
                return
            function = function_by_id.get(function_id)
            if function is None:
                self._warn("MATERIAL_FUNCTION_REFERENCE_UNRESOLVED",
                           f"{owner_type} {owner_id}가 없는 표 {function_id}를 참조합니다.", source, target_id=function_id)
                return
            function["uses"].append({"function_id": function_id, "owner_type": owner_type, "owner_id": owner_id,
                                     "role": role, "x_unit": x_unit or function.get("x_label"),
                                     "y_unit": y_unit or function.get("y_label")})
            for nested in function.get("fields", {}).get("temperature_tables", []) or []:
                use(str(nested.get("table_id") or ""), owner_type, owner_id, f"{role} · 온도 {nested.get('temperature')}",
                    source)

        for card, fields, raw in nonlinear:
            identifier = card.fields()[0].strip()
            material = material_by_id.get(identifier)
            if material is None:
                self._warn("MATERIAL_REFERENCE_UNRESOLVED", f"{card.keyword} {identifier}에 같은 ID의 재료가 없습니다.",
                           card.source, target_id=identifier)
                continue
            for name, value in fields.items():
                if name == "MID":
                    continue
                material["fields"][f"{card.keyword}.{name}"] = value
                material["raw_fields"][f"{card.keyword}.{name}"] = raw.get(name)
            if card.keyword == "MATS1":
                label = str(fields.get("TYPE") or "").upper() or "응력-변형률"
                use(raw.get("TID"), "material", identifier, f"MATS1 {label}", card.source)
            else:
                for name, value in raw.items():
                    if name.startswith("T(") and value:
                        use(value, "material", identifier, f"{card.keyword} {name}", card.source)

        unit_system = self._units(materials)
        factor = unit_system.pop("density_factor_to_g_cm3")
        for material in materials:
            density = material["density"]
            if density["value"] is not None and factor is not None:
                density["unit"] = unit_system["source_density_unit"]
                density["converted_value"] = density["value"] * factor
                density["converted_unit"] = "g/cm^3"

        parts: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        component_ids = list(self._component_order)
        if component_ids:
            entries = [self._components[identifier] for identifier in component_ids]
        else:
            # No HyperMesh component comments: one row per property (the element sets that carry it).
            entries = [{"id": item["id"], "name": item["title"], "property_id": item["id"], "property_name": item["title"],
                        "property_type_code": None, "from_property": True} for item in properties]
            if properties:
                self._warn("OPTISTRUCT_COMPONENTS_FROM_PROPERTIES",
                           "HyperMesh 컴포넌트 주석($HMNAME COMP)이 없어 Property마다 한 행으로 표시합니다.", None)
        for entry in entries:
            property_id = entry.get("property_id")
            prop = property_by_id.get(str(property_id)) if property_id else None
            material_id = prop.get("material_id") if prop else None
            material = material_by_id.get(str(material_id)) if material_id else None
            raw_fields = {"HyperMesh 컴포넌트": entry.get("name"), "HyperMesh Property 이름": entry.get("property_name")}
            if entry.get("property_type_code"):
                raw_fields["HyperMesh Property 유형"] = _HM_PROPERTY_TYPES.get(str(entry["property_type_code"]),
                                                                               entry["property_type_code"])
            if entry.get("from_property"):
                raw_fields = {"행 기준": "Property"}
            part = {
                "id": str(entry["id"]), "title": entry.get("name"), "property_id": str(property_id) if property_id else None,
                "material_id": str(material_id) if material_id else None,
                "thickness": prop.get("thickness") if prop else None, "raw_fields": raw_fields,
                "source": None, "property": prop, "material": material, "failure_models": [],
                "references": {
                    "property_status": "resolved" if prop else "unresolved" if property_id else "missing",
                    "material_status": "resolved" if material else "unresolved" if material_id else "missing",
                },
            }
            if property_id and prop is None:
                unresolved.append({"part_id": part["id"], "reference": "property_id", "target_id": property_id})
                self._warn("MATERIAL_REFERENCE_UNRESOLVED",
                           f"컴포넌트 {part['id']}가 없는 Property {property_id}를 참조합니다.", None, target_id=property_id)
            if material_id and material is None:
                unresolved.append({"part_id": part["id"], "reference": "material_id", "target_id": material_id})
                self._warn("MATERIAL_REFERENCE_UNRESOLVED",
                           f"Property {property_id}가 없는 Material {material_id}를 참조합니다.",
                           prop.get("source") if prop else None, target_id=material_id)
            parts.append(part)
        unknown = sorted(({"keyword": key, "count": count} for key, count in self._unknown.items()),
                         key=lambda item: (-item["count"], item["keyword"]))
        return {
            "solver": "OPTISTRUCT", "parser_version": PARSER_VERSION,
            "parts": parts, "properties": properties, "materials": materials, "functions": functions,
            "moves": [], "failures": [], "orphan_failures": [], "subsets": [], "parameters": [],
            "function_uses": [use_item for function in functions for use_item in function["uses"]],
            "unit_system": unit_system, "unknown_cards": unknown[:100], "unresolved_references": unresolved,
            "warnings": self._warnings,
        }

    def _units(self, materials: list[dict[str, Any]]) -> dict[str, Any]:
        """OptiStruct has no unit card: infer mm-t-s only when every density looks like t/mm^3."""
        densities = [item["density"]["value"] for item in materials if item["density"]["value"]]
        if densities and all(1e-12 <= value <= 1e-7 for value in densities):
            self._warn("OPTISTRUCT_UNITS_INFERRED",
                       "OptiStruct 입력에는 단위 카드가 없어 밀도 크기로 mm·t·s 단위계를 추정했습니다.", None)
            return {"runname": None, "input": {"mass": "t", "length": "mm", "time": "s"},
                    "work": {"mass": "t", "length": "mm", "time": "s"}, "inferred": True,
                    "source_density_unit": "t/mm^3 (추정)", "density_factor_to_g_cm3": 1e9}
        return {"runname": None, "input": {"mass": None, "length": None, "time": None},
                "work": {"mass": None, "length": None, "time": None}, "inferred": False,
                "source_density_unit": None, "density_factor_to_g_cm3": None}


def parse_optistruct_text(text: str, filename: str = "model.fem", **kwargs: Any) -> dict[str, Any]:
    """Convenience for tests: parse an in-memory deck."""
    data = text.encode("utf-8")
    return OptiStructDeckParser(**kwargs).parse(iter(data.splitlines(keepends=True)), filename, len(data))
