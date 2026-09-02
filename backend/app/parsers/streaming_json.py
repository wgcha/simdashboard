"""Bounded, incremental JSON readers for result-file parsers.

The helpers deliberately use the public :mod:`ijson` facade rather than
selecting a C or Python backend.  Deployments can therefore use the backend
available in their locked environment while callers retain one error contract.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import hashlib
from pathlib import Path
from typing import Any

import ijson


class StreamingJsonError(ValueError):
    """Base class for normalized streaming JSON failures."""


class StreamingJsonSourceError(StreamingJsonError):
    """The source could not be opened or read."""


class StreamingJsonSizeLimitError(StreamingJsonError):
    """The source exceeded its configured byte ceiling while being read."""


class StreamingJsonComplexityLimitError(StreamingJsonError):
    """The parser reached a configured event-work ceiling."""


class StreamingJsonEncodingError(StreamingJsonError):
    """The source contains invalid UTF-8."""


class StreamingJsonSyntaxError(StreamingJsonError):
    """The source is not one complete JSON document."""


class StreamingJsonTrailingDataError(StreamingJsonSyntaxError):
    """A complete JSON value is followed by non-whitespace input."""


class StreamingJsonRootArrayError(StreamingJsonError):
    """The document root is valid JSON, but is not an array."""


class _JsonSurrogateEscapeValidator:
    """Validate JSON ``\\u`` surrogate pairs directly from raw input bytes.

    JSON syntax itself remains the responsibility of the selected ``ijson``
    backend.  This narrow lexer only tracks string escape state because YAJL
    and the pure-Python backend otherwise disagree on unpaired surrogate
    escapes.  All state is scalar and persists safely across arbitrary read
    boundaries.
    """

    _QUOTE = ord('"')
    _BACKSLASH = ord("\\")
    _UNICODE_ESCAPE = ord("u")

    def __init__(self) -> None:
        self._in_string = False
        self._escape_pending = False
        self._escape_requires_low = False
        self._unicode_digits_remaining = 0
        self._unicode_value = 0
        self._unicode_requires_low = False
        self._pending_high_surrogate: int | None = None
        self._error_message: str | None = None

    def feed(self, data: bytes) -> None:
        if self._error_message is not None:
            return
        for value in data:
            self._consume(value)

    def finish(self) -> None:
        if self._error_message is not None or self._pending_high_surrogate is not None:
            raise StreamingJsonSyntaxError(
                self._error_message or "structured JSON contains an unpaired Unicode surrogate escape"
            )

    def raise_if_invalid(self) -> None:
        if self._error_message is not None:
            raise StreamingJsonSyntaxError(self._error_message)

    def _consume(self, value: int) -> None:
        if not self._in_string:
            if value == self._QUOTE:
                self._in_string = True
            return

        if self._unicode_digits_remaining:
            self._consume_unicode_hex_digit(value)
            return

        if self._escape_pending:
            self._consume_escape_character(value)
            return

        if self._pending_high_surrogate is not None:
            if value != self._BACKSLASH:
                self._mark_unpaired_surrogate()
                return
            self._escape_pending = True
            self._escape_requires_low = True
            return

        if value == self._QUOTE:
            self._in_string = False
        elif value == self._BACKSLASH:
            self._escape_pending = True

    def _consume_escape_character(self, value: int) -> None:
        requires_low = self._escape_requires_low
        self._escape_pending = False
        self._escape_requires_low = False
        if value == self._UNICODE_ESCAPE:
            self._unicode_digits_remaining = 4
            self._unicode_value = 0
            self._unicode_requires_low = requires_low
            return
        if requires_low:
            self._mark_unpaired_surrogate()
            return
        # Other escape grammar is still checked by ijson.  Treat it as one
        # complete ordinary escape so ``\\\\uD800`` remains a literal string.

    def _consume_unicode_hex_digit(self, value: int) -> None:
        digit = _hex_value(value)
        if digit is None:
            # Let the selected JSON parser retain ownership of malformed escape
            # grammar.  Reset enough state to avoid retaining stale memory.
            self._unicode_digits_remaining = 0
            self._unicode_value = 0
            if self._unicode_requires_low:
                self._mark_unpaired_surrogate()
            self._unicode_requires_low = False
            return
        self._unicode_value = (self._unicode_value << 4) | digit
        self._unicode_digits_remaining -= 1
        if self._unicode_digits_remaining:
            return
        value = self._unicode_value
        requires_low = self._unicode_requires_low
        self._unicode_value = 0
        self._unicode_requires_low = False
        if requires_low:
            if not 0xDC00 <= value <= 0xDFFF:
                self._mark_unpaired_surrogate()
                return
            self._pending_high_surrogate = None
            return
        if 0xD800 <= value <= 0xDBFF:
            self._pending_high_surrogate = value
        elif 0xDC00 <= value <= 0xDFFF:
            self._mark_unpaired_surrogate()

    def _mark_unpaired_surrogate(self) -> None:
        self._error_message = "structured JSON contains an unpaired Unicode surrogate escape"


class BoundedBinaryReader:
    """A binary reader that probes at most one byte past ``max_bytes``.

    A metadata preflight can race a producer appending to a live source.  This
    reader is the authoritative byte ceiling: it makes at most ``max_bytes +
    1`` bytes available to the parser and raises immediately upon seeing that
    final probe byte.  It intentionally exposes only the public ``read``
    contract required by :func:`ijson.parse`.
    """

    def __init__(self, path: Path, *, max_bytes: int) -> None:
        if max_bytes < 0:
            raise ValueError("max_bytes must not be negative")
        self.path = path
        self.max_bytes = max_bytes
        self.bytes_read = 0
        self._digest = hashlib.sha256()
        self._surrogate_validator = _JsonSurrogateEscapeValidator()
        self._source: Any | None = None

    def __enter__(self) -> "BoundedBinaryReader":
        try:
            self._source = self.path.open("rb")
        except OSError as exc:
            raise StreamingJsonSourceError("structured JSON source could not be opened") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        if self._source is not None:
            self._source.close()
            self._source = None

    def read(self, size: int = -1) -> bytes:
        if self._source is None:
            raise ValueError("I/O operation on a closed bounded reader")
        remaining = self.max_bytes + 1 - self.bytes_read
        if remaining <= 0:
            return b""
        requested = remaining if size is None or size < 0 else min(size, remaining)
        try:
            data = self._source.read(requested)
        except OSError as exc:
            raise StreamingJsonSourceError("structured JSON source could not be read") from exc
        self.bytes_read += len(data)
        if self.bytes_read > self.max_bytes:
            raise StreamingJsonSizeLimitError("structured JSON source exceeded byte limit")
        # ``read(0)`` produces b"" without probing EOF.  Finalization happens
        # only after the parser completes, avoiding backend-specific coroutine
        # teardown behavior while retaining one raw-stream validation pass.
        if not data:
            return data
        self._surrogate_validator.feed(data)
        self._digest.update(data)
        return data

    def finish(self) -> None:
        """Finalize lexical validation after a parser consumed the full stream."""
        self._surrogate_validator.finish()

    def raise_if_surrogate_invalid(self) -> None:
        """Reject a completed invalid escape before its parsed value escapes."""
        self._surrogate_validator.raise_if_invalid()

    @property
    def checksum(self) -> str:
        """SHA-256 for exactly the bytes accepted by this bounded reader."""
        return self._digest.hexdigest()


def iter_json_events(
    path: Path,
    *,
    max_bytes: int,
    buffer_size: int = 64 * 1024,
    max_events: int | None = None,
    on_complete: Callable[[str], None] | None = None,
) -> Iterator[tuple[str, str, Any]]:
    """Yield bounded public ``ijson.parse`` events for one UTF-8 JSON file."""
    _validate_event_limit(max_events, name="max_events")
    source: BoundedBinaryReader | None = None
    try:
        with BoundedBinaryReader(path, max_bytes=max_bytes) as source:
            event_count = 0
            for parsed_event in ijson.parse(source, buf_size=buffer_size, use_float=True):
                source.raise_if_surrogate_invalid()
                # Fetch the max+1 event only to reject it.  In particular, do
                # not yield it to a caller that could materialize more state.
                if max_events is not None and event_count >= max_events:
                    raise StreamingJsonComplexityLimitError("structured JSON event limit exceeded")
                event_count += 1
                yield parsed_event
            source.finish()
            if on_complete is not None:
                on_complete(source.checksum)
    except StreamingJsonError:
        raise
    except UnicodeDecodeError as exc:
        if source is not None:
            source.finish()
        raise StreamingJsonEncodingError("structured JSON source is not UTF-8") from exc
    except ijson.JSONError as exc:
        if source is not None:
            source.finish()
        raise _normalize_ijson_error(exc) from exc


def iter_json_array_items(
    path: Path,
    *,
    max_bytes: int,
    buffer_size: int = 64 * 1024,
    max_item_events: int | None = None,
    on_complete: Callable[[str], None] | None = None,
) -> Iterator[Any]:
    """Incrementally yield values from a complete top-level JSON array.

    ``ijson.items(..., 'item')`` alone does not make the root-type contract
    explicit.  Consuming public parse events here keeps that contract, and
    drains the event iterator after ``end_array`` so trailing data is detected.
    """
    _validate_event_limit(max_item_events, name="max_item_events")
    try:
        events = iter(
            iter_json_events(
                path,
                max_bytes=max_bytes,
                buffer_size=buffer_size,
                on_complete=on_complete,
            )
        )
        try:
            prefix, event, value = next(events)
        except StopIteration as exc:
            raise StreamingJsonSyntaxError("structured JSON is empty") from exc
        if prefix != "" or event != "start_array":
            # The first event alone only proves that this is not an array; drain
            # the document first so malformed objects are still classified as JSON
            # syntax errors rather than incorrectly reported as a root-type error.
            for _event in events:
                pass
            raise StreamingJsonRootArrayError("structured JSON root must be an array")

        for prefix, event, value in events:
            if prefix == "":
                if event != "end_array":
                    raise StreamingJsonSyntaxError("structured JSON array is invalid")
                break
            if prefix != "item":
                raise StreamingJsonSyntaxError("structured JSON array item is invalid")
            item_event_count = _consume_item_event(0, max_item_events)
            if event not in {"start_array", "start_map"}:
                yield value
                continue
            yield _build_container(
                events,
                event,
                value,
                max_item_events=max_item_events,
                event_count=item_event_count,
            )
        else:
            raise StreamingJsonSyntaxError("structured JSON array is incomplete")

        # Force the parser to consume its EOF/trailing-input state.  Most backends
        # raise on this next pull when non-whitespace follows the root array.
        try:
            next(events)
        except StopIteration:
            return
        raise StreamingJsonTrailingDataError("structured JSON contains trailing data")
    finally:
        close = getattr(locals().get("events"), "close", None)
        if close is not None:
            close()


def _build_container(
    events: Iterator[tuple[str, str, Any]],
    first_event: str,
    first_value: Any,
    *,
    max_item_events: int | None,
    event_count: int,
) -> Any:
    builder = ijson.ObjectBuilder()
    depth = 0
    event, value = first_event, first_value
    while True:
        builder.event(event, value)
        if event in {"start_array", "start_map"}:
            depth += 1
        elif event in {"end_array", "end_map"}:
            depth -= 1
        if depth == 0:
            return builder.value
        try:
            _prefix, event, value = next(events)
        except StopIteration as exc:
            raise StreamingJsonSyntaxError("structured JSON array item is incomplete") from exc
        event_count = _consume_item_event(event_count, max_item_events)


def _consume_item_event(event_count: int, max_item_events: int | None) -> int:
    """Count one item event, rejecting it before ObjectBuilder receives it."""
    if max_item_events is not None and event_count >= max_item_events:
        raise StreamingJsonComplexityLimitError("structured JSON item event limit exceeded")
    return event_count + 1


def _validate_event_limit(limit: int | None, *, name: str) -> None:
    if limit is not None and limit < 0:
        raise ValueError(f"{name} must not be negative")


def _normalize_ijson_error(error: BaseException) -> StreamingJsonError:
    """Map backend-specific parser failures to the public error contract.

    The YAJL C backend reports invalid UTF-8 in a byte-string diagnostic and
    trailing values as ``trailing garbage``.  The pure Python backend instead
    nests :class:`UnicodeDecodeError` in ``IncompleteJSONError`` and calls the
    latter condition ``Additional data found``.  Keep those implementation
    details internal while presenting one stable error taxonomy to callers.
    """
    if any(isinstance(value, UnicodeDecodeError) for value in _walk_exception_values(error)):
        return StreamingJsonEncodingError("structured JSON source is not UTF-8")

    if error.args == ("Additional data found",):
        return StreamingJsonTrailingDataError("structured JSON contains trailing data")

    header = _first_backend_diagnostic_header(error)
    if header is not None and header.rstrip(".").casefold() == "parse error: trailing garbage":
        return StreamingJsonTrailingDataError("structured JSON contains trailing data")
    if header is not None and header.rstrip(".").casefold() == "lexical error: invalid bytes in utf8 string":
        return StreamingJsonEncodingError("structured JSON source is not UTF-8")
    return StreamingJsonSyntaxError("structured JSON is invalid")


def _walk_exception_values(error: BaseException) -> Iterator[object]:
    """Yield nested exception values without exposing their diagnostics."""
    pending: list[object] = [error]
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        yield value
        if isinstance(value, BaseException):
            pending.extend(value.args)
            if value.__cause__ is not None:
                pending.append(value.__cause__)
            if value.__context__ is not None:
                pending.append(value.__context__)


def _first_backend_diagnostic_header(error: BaseException) -> str | None:
    """Read only a backend diagnostic header, never echoed source context."""
    if not error.args or not isinstance(error.args[0], (bytes, str)):
        return None
    first = error.args[0]
    text = first.decode("utf-8", errors="replace") if isinstance(first, bytes) else first
    return text.splitlines()[0].strip()


def _hex_value(value: int) -> int | None:
    if ord("0") <= value <= ord("9"):
        return value - ord("0")
    if ord("a") <= value <= ord("f"):
        return value - ord("a") + 10
    if ord("A") <= value <= ord("F"):
        return value - ord("A") + 10
    return None
