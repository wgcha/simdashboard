"""Framework-independent semantic result ingestion transaction."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..adapters.persistence.result_ingestion import bound_result_ingestion_unit_of_work
from ..application.results.commands import ingest_result_bundle, utc_identifier


def semantic_source_run_id(
    recipe_id: str, recipe_version: int, digest: str, template_id: str | None,
    template_version: int | None, *, reuse_legacy: bool = False,
) -> str:
    """Name an immutable semantic run by parsing and presentation versions."""
    legacy = f"semantic:{recipe_id}:{recipe_version}:{digest}"
    if template_id is None or reuse_legacy:
        if len(legacy) <= 120:
            return legacy
    identity = json.dumps([recipe_id, recipe_version, digest, template_id, template_version], separators=(",", ":"), ensure_ascii=True)
    return "semantic-config:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


@contextmanager
def semantic_transaction(conn: Any):
    conn.execute("BEGIN TRANSACTION")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def persist_semantic_import(
    conn: Any, command: dict[str, Any], *, recipe_id: str, recipe_version: int,
    template_id: str | None, template_version: int | None, filename: str,
    source_bytes: bytes, authorize: Callable[[dict[str, Any], Any], object], now: Callable[[], datetime],
) -> tuple[dict[str, Any], str | None]:
    """Commit canonical results and immutable semantic provenance together."""
    conn.execute("BEGIN TRANSACTION")
    try:
        outcome, run_id = persist_semantic_import_in_transaction(
            conn, command, recipe_id=recipe_id, recipe_version=recipe_version,
            template_id=template_id, template_version=template_version, filename=filename,
            source_bytes=source_bytes, authorize=authorize, now=now,
        )
        conn.execute("COMMIT")
        return outcome, run_id
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def persist_semantic_import_in_transaction(
    conn: Any, command: dict[str, Any], *, recipe_id: str, recipe_version: int,
    template_id: str | None, template_version: int | None, filename: str,
    source_bytes: bytes, authorize: Callable[[dict[str, Any], Any], object], now: Callable[[], datetime],
) -> tuple[dict[str, Any], str | None]:
    """Persist an import on the caller's transaction; it never commits or rolls back."""
    outcome = ingest_result_bundle(
        command,
        bound_result_ingestion_unit_of_work(conn, utc_identifier, authorize=authorize),
        now,
        utc_identifier,
    )
    run_id = outcome["analysis_run_id"] or outcome["existing_analysis_run_id"]
    if run_id:
        conn.execute(
            "INSERT INTO semantic_import_provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(analysis_run_id) DO NOTHING",
            [run_id, command["load_case_id"], recipe_id, recipe_version, template_id, template_version,
             filename, command["source_checksum"], json.dumps(command["parsed"].get("observations", []), ensure_ascii=False),
             source_bytes if len(source_bytes) <= 256 * 1024 else None, now()],
        )
    return outcome, run_id
