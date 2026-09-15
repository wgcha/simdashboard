"""Small, secret-safe diagnostics shared by PostgreSQL backup processes.

This module deliberately reports *types and bounded numeric codes only*.  It
must not be used to serialize an exception or a subprocess command.
"""

from __future__ import annotations

import json
import re
import sys
from contextlib import contextmanager
from subprocess import CalledProcessError
from typing import Iterator


DETAIL_PREFIX = "POSTGRES_BACKUP_DETAIL "
MAX_INTEGER = 2**31
MAX_LINE = 16 * 1024
MAX_SCAN = 1024 * 1024

BACKUP_STAGES = frozenset(
    {
        "config",
        "pg_dump_resolve",
        "pg_restore_resolve",
        "pg_dump_version",
        "pg_restore_version",
        "snapshot_inventory",
        "assets_bundle",
        "reserve_dump",
        "pg_dump",
        "pg_restore_list",
        "publish_dump",
        "write_manifest",
        "report_success",
    }
)
_SQLSTATE = re.compile(r"^[0-9A-Z]{5}$")

# These are the public exception names exposed by psycopg 3.  Keep this list
# explicit: using an exception's arbitrary class name would disclose library
# or application internals in child-process output.
PSYCOPG_EXCEPTION_TYPES = frozenset(
    {
        "Error",
        "Warning",
        "InterfaceError",
        "DatabaseError",
        "DataError",
        "OperationalError",
        "IntegrityError",
        "InternalError",
        "ProgrammingError",
        "NotSupportedError",
        "UndefinedFile",
        "UndefinedTable",
        "UndefinedColumn",
        "InvalidCatalogName",
        "InvalidAuthorizationSpecification",
        "InvalidPassword",
        "InsufficientPrivilege",
        "SerializationFailure",
        "DeadlockDetected",
        "LockNotAvailable",
        "UniqueViolation",
        "ForeignKeyViolation",
        "NotNullViolation",
        "CheckViolation",
        "ExclusionViolation",
        "StringDataRightTruncation",
        "NumericValueOutOfRange",
        "SyntaxError",
        "InvalidTextRepresentation",
        "ConnectionException",
        "ConnectionFailure",
        "ConnectionDoesNotExist",
        "ConnectionFailure",
        "AdminShutdown",
        "QueryCanceled",
    }
)
_BUILTIN_TYPES = frozenset(
    {
        "RuntimeError",
        "ValueError",
        "TypeError",
        "IndexError",
        "KeyError",
        "UnicodeEncodeError",
        "UnicodeDecodeError",
        "OSError",
        "PermissionError",
        "FileNotFoundError",
        "FileExistsError",
        "NotADirectoryError",
        "IsADirectoryError",
        "CalledProcessError",
        "ImportError",
        "ModuleNotFoundError",
    }
)
_EXCEPTION_TYPES = _BUILTIN_TYPES | PSYCOPG_EXCEPTION_TYPES | {"Exception"}
_NUMERIC_FIELDS = ("errno", "winerror", "returncode")


def _safe_attr(error: BaseException, name: str) -> object:
    try:
        return getattr(error, name)
    except BaseException:
        return None


def _exception_type(error: BaseException) -> str:
    error_type = type(error)
    name = error_type.__name__
    if (error_type.__module__ == "builtins" and name in _BUILTIN_TYPES) or error_type is CalledProcessError:
        return name
    if error_type.__module__.startswith("psycopg") and name in PSYCOPG_EXCEPTION_TYPES:
        return name
    return "Exception"


def _bounded_integer(value: object) -> int | None:
    # bool is an int subclass but is not a useful process/OS code.
    if type(value) is not int or not -MAX_INTEGER <= value <= MAX_INTEGER:
        return None
    return value


def _valid_sqlstate(value: object) -> str | None:
    if type(value) is str and _SQLSTATE.fullmatch(value):
        return value
    return None


def exception_details(error: BaseException) -> dict[str, object]:
    """Return an allowlisted, bounded description without reading exception text."""
    if not isinstance(error, BaseException):
        return {"exception_type": "Exception"}
    result: dict[str, object] = {"exception_type": _exception_type(error)}
    for name in _NUMERIC_FIELDS:
        value = _bounded_integer(_safe_attr(error, name))
        if value is not None:
            result[name] = value
    sqlstate = _valid_sqlstate(_safe_attr(error, "sqlstate"))
    if sqlstate is None:
        # psycopg 2 compatibility and a few DB-API wrappers use pgcode.
        sqlstate = _valid_sqlstate(_safe_attr(error, "pgcode"))
    if sqlstate is not None:
        result["sqlstate"] = sqlstate
    return result


@contextmanager
def backup_stage(stage: str) -> Iterator[None]:
    """Emit one safe detail event for a failed stage, then preserve the error."""
    if type(stage) is not str or stage not in BACKUP_STAGES:
        raise ValueError("unknown backup stage")
    try:
        yield
    except Exception as error:
        payload = {"stage": stage, **exception_details(error)}
        try:
            print(DETAIL_PREFIX + json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")), file=sys.stderr, flush=True)
        except (OSError, UnicodeError):
            pass  # Reporting failure must not replace the original exception.
        raise


def _valid_event(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    stage = payload.get("stage")
    exception_type = payload.get("exception_type")
    if type(stage) is not str or stage not in BACKUP_STAGES:
        return None
    if type(exception_type) is not str or exception_type not in _EXCEPTION_TYPES:
        return None
    result: dict[str, object] = {"stage": stage, "exception_type": exception_type}
    for name in _NUMERIC_FIELDS:
        value = _bounded_integer(payload.get(name))
        if value is not None:
            result[name] = value
    sqlstate = _valid_sqlstate(payload.get("sqlstate"))
    if sqlstate is not None:
        result["sqlstate"] = sqlstate
    return result


def child_failure_details(text: str) -> dict[str, object]:
    """Read the last valid canonical detail line from bounded child output."""
    if not isinstance(text, str):
        return {}
    latest: dict[str, object] = {}
    scanned = text[:MAX_SCAN]
    decoder = json.JSONDecoder()
    for line in scanned.splitlines():
        if len(line) > MAX_LINE or not line.startswith(DETAIL_PREFIX):
            continue
        encoded = line[len(DETAIL_PREFIX) :]
        try:
            payload, end = decoder.raw_decode(encoded)
        except (ValueError, RecursionError):
            continue
        if encoded[end:].strip():
            continue
        event = _valid_event(payload)
        if event is not None:
            latest = event
    return latest
