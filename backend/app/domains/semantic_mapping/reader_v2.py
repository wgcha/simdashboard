"""Deterministic readers for semantic recipe reader version 2.

This module deliberately only turns a bounded input file into rows.  Semantic
typing and result generation stay in ``engine`` so the reader can be used for
both inspection and recipe execution.
"""
from __future__ import annotations

import csv
import io
import itertools
import json
from pathlib import PurePath
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import ijson


INPUT_V2_MAX_BYTES = 64 * 1024 * 1024
LEGACY_CSV_FIELD_LIMIT = csv.field_size_limit()
csv.field_size_limit(INPUT_V2_MAX_BYTES)


class ReaderV2Error(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise ReaderV2Error(code, message)


class _Occurrences(list[Any]):
    """Marks duplicate CSV keys while retaining normal JSON-array semantics."""


class _UniqueObject(dict):
    """Apply the same duplicate-key contract to streamed and eager JSON."""
    def __setitem__(self, key: str, value: Any) -> None:
        if key in self:
            self.duplicate_key = key
        super().__setitem__(key, value)


class ReplayableRows(Sequence[dict[str, Any]]):
    """A countable, sliceable view which reparses bounded source on each pass."""
    def __init__(self, iterator: Callable[[], Iterator[dict[str, Any]]]):
        self._iterator = iterator
        self._count: int | None = None

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return self._iterator()

    def __len__(self) -> int:
        if self._count is None:
            self._count = sum(1 for _ in self._iterator())
        return self._count

    def __getitem__(self, index: int | slice) -> dict[str, Any] | list[dict[str, Any]]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            return list(itertools.islice(self._iterator(), start, stop, step))
        if index < 0:
            index += len(self)
        try:
            return next(itertools.islice(self._iterator(), index, index + 1))
        except StopIteration:
            raise IndexError(index) from None


def pointer_escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def pointer_unescape(token: str) -> str:
    # A malformed escape is deliberately literal.  It cannot silently select a
    # different key, and consequently simply does not resolve during mapping.
    return token.replace("~1", "/").replace("~0", "~")


def json_pointer_get(value: Any, pointer: str) -> Any:
    if pointer == "#":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("#/"):
        return None
    current = value
    for encoded in pointer[2:].split("/"):
        token = pointer_unescape(encoded)
        if isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit():
                return None
            index = int(token)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _decode(content: bytes, encoding: str | None = None) -> tuple[str, str]:
    if encoding is not None and encoding not in ("utf-8", "utf-8-sig", "utf-16", "cp949"):
        _fail("ENCODING_INVALID", "지원하지 않는 인코딩입니다.")
    if not isinstance(content, bytes) or not content:
        _fail("FILE_SIZE_LIMIT", "빈 파일은 읽을 수 없습니다.")
    if len(content) > INPUT_V2_MAX_BYTES:
        _fail("FILE_SIZE_LIMIT", "v2 입력 파일은 64 MiB 이하여야 합니다.")
    candidates = [encoding] if encoding else []
    if encoding == "utf-16" and not content.startswith((b"\xff\xfe", b"\xfe\xff")):
        probe = content[:512]
        candidates = ["utf-16-le" if probe[1::2].count(0) >= probe[::2].count(0) else "utf-16-be"]
    if not candidates:
        if content.startswith(b"\xef\xbb\xbf"):
            candidates.append("utf-8-sig")
        elif content.startswith((b"\xff\xfe", b"\xfe\xff")):
            candidates.append("utf-16")
        # UTF-16 files without a BOM are uncommon but NUL bytes make this
        # choice deterministic and avoid accidentally accepting CP949 text.
        elif b"\x00" in content[:512]:
            probe = content[:512]
            # UTF-16 without a BOM needs an endian decision before decoding.
            candidates.append("utf-16-le" if probe[1::2].count(0) >= probe[::2].count(0) else "utf-16-be")
        candidates.extend(["utf-8", "cp949"])
    for candidate in dict.fromkeys(candidates):
        try:
            return content.decode(candidate), "utf-16" if candidate.startswith("utf-16-") else candidate
        except (UnicodeDecodeError, LookupError):
            continue
    _fail("ENCODING_INVALID", "UTF-8, UTF-16 또는 CP949로 파일을 읽을 수 없습니다.")


def _delimiter(source: str, supplied: str | None = None) -> str:
    if supplied in {",", ";", "\t", "|"}:
        return supplied
    try:
        dialect = csv.Sniffer().sniff(source[:16_384], delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        # Comma is stable for a one-column file and is the historic default.
        return ","


def _csv_reader(source: str, delimiter: str) -> Iterator[list[str]]:
    try:
        yield from (line for line in csv.reader(io.StringIO(source, newline=""), delimiter=delimiter, strict=True) if line)
    except csv.Error:
        _fail("CSV_INVALID", "올바르지 않은 CSV 형식입니다.")


def _csv_probe(source: str, delimiter: str, limit: int = 256) -> list[list[str]]:
    return list(itertools.islice(_csv_reader(source, delimiter), limit))


def _key_value_row(lines: list[list[str]]) -> tuple[dict[str, Any], list[str]]:
    row: dict[str, Any] = {}
    warnings: list[str] = []
    for line_number, line in enumerate(lines, 1):
        # Some solver exports have a blank first cell (",Name,value").  The
        # first non-empty cell is the key; every following cell is its value.
        try:
            key_index = next(index for index, cell in enumerate(line) if cell != "")
        except StopIteration:
            warnings.append(f"CSV {line_number}행은 비어 있어 건너뛰었습니다.")
            continue
        key = line[key_index]
        values = line[key_index + 1:]
        if not values:
            values = [""]
        value: Any = values[0] if len(values) == 1 else values
        if key in row:
            previous = row[key]
            if isinstance(previous, _Occurrences):
                previous.append(value)
            else:
                row[key] = _Occurrences([previous, value])
            warnings.append(f"중복 CSV 키 '{key}'가 있어 occurrence 선택자가 필요합니다.")
        else:
            row[key] = value
    if not row:
        _fail("NO_RECORDS", "키-값 CSV에서 읽을 값이 없습니다.")
    return row, warnings


def _looks_key_value(lines: list[list[str]], header_row: int) -> bool:
    body = lines[header_row - 1:] if header_row > 1 else lines
    if not body:
        return False
    # The leading-empty first row is a known solver-export convention.  It is
    # unambiguously a key/value row because a table header cannot use the
    # following measurement label as its data value.
    if body[0] and body[0][0] == "" and len(body[0]) >= 2:
        return True
    # A conventional table header followed by numeric first-column values is
    # not a key/value export (for example ``time,value / 0,1 / 1,2``).
    if len(body) >= 3 and all(line and _numeric(line[0]) for line in body[1:]):
        return False
    # Repeated short rows are much more likely name/value exports than a table.
    short = sum(1 for line in body if 2 <= len(line) <= 3)
    labels = sum(1 for line in body if any(cell == "" for cell in line[:1]))
    ragged = len({len(line) for line in body}) > 1
    return len(body) >= 3 and (short / len(body) >= .8 or ragged) and (labels > 0 or len({line[0] for line in body if line}) == len(body))


def _numeric(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _csv_table(source: str, delimiter: str, header_row: int) -> tuple[ReplayableRows, list[str]]:
    reader = _csv_reader(source, delimiter)
    try:
        fields = next(itertools.islice(reader, header_row - 1, header_row))
    except StopIteration:
        _fail("HEADER_INVALID", "지정한 헤더 행을 찾을 수 없습니다.")
    if not fields:
        _fail("FIELDS_INVALID", "CSV 헤더가 없습니다.")
    # Empty names are valid literal columns in the v2 reader.  Duplicates are
    # preserved in a list, so no source data is overwritten.
    warnings: list[str] = []
    if len(set(fields)) != len(fields):
        warnings.append("중복 CSV 헤더가 있어 occurrence 선택자가 필요합니다.")
    def iterate() -> Iterator[dict[str, Any]]:
        full_reader = _csv_reader(source, delimiter)
        try:
            next(itertools.islice(full_reader, header_row - 1, header_row))
        except StopIteration:
            _fail("HEADER_INVALID", "지정한 헤더 행을 찾을 수 없습니다.")
        for number, line in enumerate(full_reader, header_row + 1):
            if len(line) > len(fields):
                _fail("ROW_INVALID", f"CSV {number}행의 열 수가 헤더보다 많습니다.")
            row: dict[str, Any] = {}
            for key, value in zip(fields, line):
                if key in row:
                    prior = row[key]
                    if isinstance(prior, _Occurrences):
                        prior.append(value)
                    else:
                        row[key] = _Occurrences([prior, value])
                else:
                    row[key] = value
            yield row
    rows = ReplayableRows(iterate)
    if not len(rows):
        _fail("NO_RECORDS", "CSV 데이터 행이 없습니다.")
    return rows, warnings


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("DUPLICATE_FIELD", f"JSON 필드가 중복됩니다: {key[:60]}")
        result[key] = value
    return result


def _find_records(value: Any, pointer: str = "#") -> tuple[str, list[dict[str, Any]]] | None:
    if isinstance(value, list) and value and all(isinstance(row, dict) for row in value):
        return pointer, value
    if isinstance(value, dict):
        for key, child in value.items():
            found = _find_records(child, pointer + "/" + pointer_escape(str(key)))
            if found:
                return found
    return None


def read(filename: str, content: bytes, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read with supplied options, or deterministically infer reader settings."""
    options = options or {}
    suffix = PurePath(filename).suffix.lower()
    requested_format = options.get("format")
    if requested_format not in (None, "csv", "json"):
        _fail("FORMAT_UNSUPPORTED", "CSV 또는 JSON 읽기 형식을 선택하세요.")
    source, encoding = _decode(content, options.get("encoding"))
    if requested_format:
        fmt = requested_format
    elif suffix not in {".csv", ".tsv", ".txt", ".json"}:
        _fail("FORMAT_UNSUPPORTED", "CSV, TSV, TXT 또는 JSON 파일을 선택하세요.")
    else:
        # Inspection is content-led.  Saved v2 recipes always pass ``format``
        # and therefore remain pinned to their approved parser.
        fmt = "json" if source.lstrip().startswith(("{", "[")) else "csv"
    warnings: list[str] = []
    if fmt == "json":
        requested_layout = options.get("input_layout")
        records_path = options.get("records_path", "")
        # A root array is the common large simulation result shape.  Replay it
        # with ijson so neither inspection nor each mapping pass retains all
        # normalized row dictionaries.  Nested paths still use the exact
        # pointer resolver below, because punctuation-bearing keys cannot be
        # safely translated to ijson's dotted prefix grammar.
        if records_path in ("", "#") and requested_layout != "json_object" and source.lstrip().startswith("["):
            json_bytes = source.encode("utf-8")
            def root_records() -> Iterator[dict[str, Any]]:
                try:
                    for row in ijson.items(io.BytesIO(json_bytes), "item", use_float=True, map_type=_UniqueObject):
                        if not isinstance(row, dict):
                            _fail("ROW_INVALID", "JSON 레코드는 객체여야 합니다.")
                        pending = [row]
                        while pending:
                            node = pending.pop()
                            if isinstance(node, _UniqueObject) and hasattr(node, "duplicate_key"):
                                _fail("DUPLICATE_FIELD", f"JSON 필드가 중복됩니다: {node.duplicate_key[:60]}")
                            if isinstance(node, dict):
                                pending.extend(node.values())
                            elif isinstance(node, list):
                                pending.extend(node)
                        yield row
                except (ijson.JSONError, UnicodeError):
                    _fail("JSON_INVALID", "올바르지 않은 JSON입니다.")
            streamed = ReplayableRows(root_records)
            if not len(streamed):
                _fail("NO_RECORDS", "읽을 JSON 레코드가 없습니다.")
            return {"format": "json", "encoding": encoding, "delimiter": None, "header_row": None, "input_layout": "json_records", "records_path": "#", "rows": streamed, "warnings": warnings}
        try:
            data = json.loads(source, object_pairs_hook=_json_pairs, parse_constant=lambda _: _fail("NONFINITE_VALUE", "JSON 숫자는 유한해야 합니다."))
        except (json.JSONDecodeError, RecursionError):
            _fail("JSON_INVALID", "올바르지 않거나 지나치게 중첩된 JSON입니다.")
        if records_path:
            selected = json_pointer_get(data, records_path)
            if not isinstance(selected, list) or any(not isinstance(row, dict) for row in selected):
                _fail("ROW_INVALID", "records_path는 객체 레코드 배열을 가리켜야 합니다.")
            rows = selected
            layout = "json_records"
        else:
            found = _find_records(data)
            if requested_layout == "json_object" or not found:
                if not isinstance(data, dict):
                    _fail("ROW_INVALID", "JSON 객체 레이아웃에는 최상위 객체가 필요합니다.")
                rows, layout, records_path = [data], "json_object", ""
            else:
                records_path, rows = found
                layout = "json_records"
        if requested_layout and requested_layout != layout:
            _fail("INPUT_LAYOUT_INVALID", "지정한 입력 레이아웃과 파일 구조가 일치하지 않습니다.")
        if not rows:
            _fail("NO_RECORDS", "읽을 JSON 레코드가 없습니다.")
        return {"format": "json", "encoding": encoding, "delimiter": None, "header_row": None, "input_layout": layout, "records_path": records_path, "rows": rows, "warnings": warnings}

    delimiter = _delimiter(source, options.get("delimiter"))
    probe = _csv_probe(source, delimiter)
    header_row = options.get("header_row", 1)
    if type(header_row) is not int or header_row < 1:
        _fail("HEADER_INVALID", "헤더 행은 1 이상의 정수여야 합니다.")
    layout = options.get("input_layout")
    if layout is None:
        layout = "csv_key_value" if _looks_key_value(probe, header_row) else "csv_table"
        if layout == "csv_key_value" and not (probe and probe[0] and probe[0][0] == ""):
            warnings.append("CSV를 키-값 형식으로 추정했습니다. 표 형식이면 입력 레이아웃을 재설정하세요.")
    if layout == "csv_key_value":
        rows_as_one, local_warnings = _key_value_row(list(_csv_reader(source, delimiter)))
        rows = [rows_as_one]
        records_path = ""
    elif layout == "csv_table":
        rows, local_warnings = _csv_table(source, delimiter, header_row)
        records_path = ""
    else:
        _fail("INPUT_LAYOUT_INVALID", "CSV 입력 레이아웃이 올바르지 않습니다.")
    warnings.extend(local_warnings)
    return {"format": "csv", "encoding": encoding, "delimiter": delimiter, "header_row": header_row, "input_layout": layout, "records_path": records_path, "rows": rows, "warnings": warnings}
