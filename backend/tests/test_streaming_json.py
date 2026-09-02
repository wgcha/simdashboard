from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import ijson
from ijson.backends import python as python_backend
import pytest

from app.parsers import streaming_json
from app.parsers.streaming_json import (
    BoundedBinaryReader,
    StreamingJsonComplexityLimitError,
    StreamingJsonEncodingError,
    StreamingJsonRootArrayError,
    StreamingJsonSizeLimitError,
    StreamingJsonSyntaxError,
    StreamingJsonTrailingDataError,
    iter_json_array_items,
    iter_json_events,
)


pytestmark = pytest.mark.unit


def test_array_iterator_handles_utf8_and_escapes_across_one_byte_chunks(tmp_path: Path) -> None:
    source = tmp_path / "scalars.json"
    source.write_bytes(
        b'[{"label":"\xed\x95\x9c","escaped":"\\uD55C\\n","value":1.25,"nested":{"ratio":2.5}}]'
    )

    items = list(iter_json_array_items(source, max_bytes=source.stat().st_size, buffer_size=1))

    assert items == [{"label": "한", "escaped": "한\n", "value": 1.25, "nested": {"ratio": 2.5}}]
    assert isinstance(items[0]["value"], float)
    assert isinstance(items[0]["nested"]["ratio"], float)


def test_array_iterator_normalizes_non_array_root(tmp_path: Path) -> None:
    source = tmp_path / "object.json"
    source.write_text('{"value": 1}', encoding="utf-8")

    with pytest.raises(StreamingJsonRootArrayError):
        list(iter_json_array_items(source, max_bytes=1024, buffer_size=1))


def test_array_iterator_normalizes_trailing_data(tmp_path: Path) -> None:
    source = tmp_path / "trailing.json"
    source.write_bytes(b"[] trailing")

    with pytest.raises(StreamingJsonTrailingDataError):
        list(iter_json_array_items(source, max_bytes=1024, buffer_size=1))


def test_array_iterator_normalizes_invalid_utf8_and_json(tmp_path: Path) -> None:
    invalid_utf8 = tmp_path / "invalid-utf8.json"
    invalid_utf8.write_bytes(b'["\xff"]')
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_bytes(b"[{]")

    with pytest.raises(StreamingJsonEncodingError):
        list(iter_json_array_items(invalid_utf8, max_bytes=1024, buffer_size=1))
    with pytest.raises(StreamingJsonSyntaxError):
        list(iter_json_array_items(invalid_json, max_bytes=1024, buffer_size=1))


@pytest.mark.parametrize("backend", [ijson, python_backend], ids=["default", "pure-python"])
@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (b'["\xff"]', StreamingJsonEncodingError),
        (b"[] trailing", StreamingJsonTrailingDataError),
    ],
)
def test_backend_specific_ijson_errors_normalize_to_the_public_contract(
    backend: object,
    payload: bytes,
    expected_error: type[Exception],
) -> None:
    """Exercise real C/default and pure-Python backend exception shapes."""
    parse = getattr(backend, "parse")
    with pytest.raises(ijson.JSONError) as captured:
        list(parse(BytesIO(payload), use_float=True))

    normalized = streaming_json._normalize_ijson_error(captured.value)

    assert isinstance(normalized, expected_error)
    assert str(normalized) in {
        "structured JSON source is not UTF-8",
        "structured JSON contains trailing data",
    }


@pytest.mark.parametrize("backend", [ijson, python_backend], ids=["default", "pure-python"])
@pytest.mark.parametrize("payload", [b"[utf-8]", b"[additional data]", b"[trailing garbage]"])
def test_backend_source_echoes_do_not_change_json_syntax_error_classification(
    backend: object,
    payload: bytes,
) -> None:
    parse = getattr(backend, "parse")
    with pytest.raises(ijson.JSONError) as captured:
        list(parse(BytesIO(payload), use_float=True))

    normalized = streaming_json._normalize_ijson_error(captured.value)

    assert type(normalized) is StreamingJsonSyntaxError


@pytest.mark.parametrize("backend", [ijson, python_backend], ids=["default", "pure-python"])
@pytest.mark.parametrize(
    "payload",
    [
        b'["\\uD800"]',
        b'["\\uDC00"]',
        b'[{"\\uD800":1}]',
    ],
)
def test_raw_reader_rejects_unpaired_surrogate_escapes_across_backends(
    tmp_path: Path,
    backend: object,
    payload: bytes,
) -> None:
    source = tmp_path / "surrogate.json"
    source.write_bytes(payload)
    parse = getattr(backend, "parse")

    with BoundedBinaryReader(source, max_bytes=len(payload)) as reader:
        events = parse(reader, use_float=True, buf_size=1)
        try:
            try:
                list(events)
            except (UnicodeDecodeError, ijson.JSONError):
                # YAJL may reject a low surrogate first while the pure backend
                # emits one; the shared raw scanner below owns the contract.
                pass
        finally:
            close = getattr(events, "close", None)
            if close is not None:
                close()
        with pytest.raises(StreamingJsonSyntaxError) as error:
            reader.finish()

    assert type(error.value) is StreamingJsonSyntaxError


@pytest.mark.parametrize("backend", [ijson, python_backend], ids=["default", "pure-python"])
@pytest.mark.parametrize("payload", [b'["\\uD800"]', b'["\\uDC00"]', b'[{"\\uD800":1}]'])
def test_public_streaming_facade_always_rejects_unpaired_surrogates_as_syntax(
    tmp_path: Path,
    backend: object,
    payload: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "facade-surrogate.json"
    source.write_bytes(payload)
    monkeypatch.setattr(streaming_json.ijson, "parse", getattr(backend, "parse"))
    items = iter_json_array_items(source, max_bytes=len(payload), buffer_size=1)

    try:
        with pytest.raises(StreamingJsonSyntaxError) as error:
            next(items)
    finally:
        items.close()

    assert type(error.value) is StreamingJsonSyntaxError


@pytest.mark.parametrize("backend", [ijson, python_backend], ids=["default", "pure-python"])
def test_raw_reader_keeps_valid_pairs_literals_and_utf8_text(
    tmp_path: Path,
    backend: object,
) -> None:
    payload = '["\\uD83D\\uDE00","\\\\uD800","😀","한","\\n\\t"]'.encode("utf-8")
    source = tmp_path / "valid-surrogate.json"
    source.write_bytes(payload)
    parse = getattr(backend, "parse")

    with BoundedBinaryReader(source, max_bytes=len(payload)) as reader:
        event_stream = parse(reader, use_float=True, buf_size=1)
        try:
            events = list(event_stream)
        finally:
            close = getattr(event_stream, "close", None)
            if close is not None:
                close()
        reader.finish()

    assert [value for prefix, event, value in events if prefix == "item" and event == "string"] == [
        "😀",
        "\\uD800",
        "😀",
        "한",
        "\n\t",
    ]


def test_reader_does_not_treat_read_zero_as_surrogate_eof(tmp_path: Path) -> None:
    payload = b'["\\uD800'
    source = tmp_path / "read-zero.json"
    source.write_bytes(payload)

    with BoundedBinaryReader(source, max_bytes=len(payload)) as reader:
        assert reader.read(len(payload)) == payload
        assert reader.read(0) == b""
        assert reader.read(1) == b""
        with pytest.raises(StreamingJsonSyntaxError):
            reader.finish()


def test_size_limit_precedes_surrogate_validation_and_valid_input_keeps_checksum(tmp_path: Path) -> None:
    unpaired_with_extra_byte = b'["\\uD800"]x'
    oversized = tmp_path / "oversized-surrogate.json"
    oversized.write_bytes(unpaired_with_extra_byte)

    with pytest.raises(StreamingJsonSizeLimitError):
        list(iter_json_array_items(oversized, max_bytes=len(unpaired_with_extra_byte) - 1))

    valid = tmp_path / "checksummed-pair.json"
    valid.write_bytes(b'["\\uD83D\\uDE00"]')
    checksums: list[str] = []

    assert list(iter_json_array_items(valid, max_bytes=valid.stat().st_size, buffer_size=1, on_complete=checksums.append)) == ["😀"]
    assert checksums == [hashlib.sha256(valid.read_bytes()).hexdigest()]


def test_bounded_reader_probes_at_most_one_byte_after_limit(tmp_path: Path) -> None:
    source = tmp_path / "grown.json"
    source.write_bytes(b"[   ]")

    with BoundedBinaryReader(source, max_bytes=2) as reader:
        with pytest.raises(StreamingJsonSizeLimitError):
            reader.read()
        assert reader.bytes_read == 3


def test_event_budget_rejects_max_plus_one_before_yielding_it(tmp_path: Path) -> None:
    source = tmp_path / "events.json"
    source.write_bytes(b"[0,1]")
    events = iter_json_events(source, max_bytes=source.stat().st_size, buffer_size=1, max_events=2)

    assert next(events) == ("", "start_array", None)
    assert next(events) == ("item", "number", 0)
    with pytest.raises(StreamingJsonComplexityLimitError):
        next(events)
    events.close()


def test_item_budget_stops_before_building_the_max_plus_one_nested_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "padding.json"
    source.write_text("ignored because the public event seam is stubbed", encoding="utf-8")
    # One item map with ``padding: [{}, ...]`` has 65 item events here:
    # start_map, map_key, start_array, 30 empty map pairs, end_array, end_map.
    supplied_events = [("", "start_array", None), ("item", "start_map", None), ("item", "map_key", "padding"), ("item.padding", "start_array", None)]
    supplied_events.extend(
        event
        for _index in range(30)
        for event in (("item.padding.item", "start_map", None), ("item.padding.item", "end_map", None))
    )
    supplied_events.extend((("item.padding", "end_array", None), ("item", "end_map", None), ("", "end_array", None)))
    pulled = 0
    closed = False

    def nested_padding_events(*args: object, **kwargs: object):
        del args, kwargs
        nonlocal closed, pulled
        try:
            for event in supplied_events:
                pulled += 1
                yield event
            raise AssertionError("item limit must not request an event after the rejected one")
        finally:
            closed = True

    builder_events: list[tuple[str, object]] = []

    class SpyBuilder:
        def event(self, event: str, value: object) -> None:
            builder_events.append((event, value))

    monkeypatch.setattr(streaming_json, "iter_json_events", nested_padding_events)
    monkeypatch.setattr(streaming_json.ijson, "ObjectBuilder", SpyBuilder)

    with pytest.raises(StreamingJsonComplexityLimitError):
        list(iter_json_array_items(source, max_bytes=1024, max_item_events=64))

    # The source produces the root array plus the 65th item event only.  That
    # offending event is never given to ObjectBuilder, and the next root event
    # is not requested.
    assert pulled == 66
    assert len(builder_events) == 64
    assert closed
