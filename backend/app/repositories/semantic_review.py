"""Persistence operations for semantic-import review state."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator
from uuid import uuid4

from ..database_connection import ConnectionLike, rows


@contextmanager
def review_transaction(connection: ConnectionLike) -> Iterator[None]:
    connection.execute("BEGIN TRANSACTION")
    try:
        yield
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def item(connection: ConnectionLike, item_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if lock and getattr(connection, "backend", "duckdb") == "postgresql" else ""
    records = rows(connection.execute("SELECT * FROM semantic_import_review_items WHERE id=?" + suffix, [item_id]))
    return dict(records[0]) if records else None


def binding(connection: ConnectionLike, binding_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if lock and getattr(connection, "backend", "duckdb") == "postgresql" else ""
    records = rows(connection.execute("SELECT * FROM semantic_folder_bindings WHERE id=?" + suffix, [binding_id]))
    return dict(records[0]) if records else None


def run_target(connection: ConnectionLike, run_id: str) -> Any:
    return connection.execute("SELECT load_case_id FROM analysis_runs WHERE id=?", [run_id]).fetchone()


def recipe_version(connection: ConnectionLike, recipe_id: str, version: int) -> Any:
    return connection.execute("SELECT definition_json, item_snapshot_json FROM semantic_recipe_versions WHERE recipe_id=? AND version=?", [recipe_id, version]).fetchone()


def template_version(connection: ConnectionLike, template_id: str, version: int) -> Any:
    return connection.execute("SELECT definition_json, item_snapshot_json FROM semantic_template_versions WHERE template_id=? AND version=?", [template_id, version]).fetchone()


def stale_item(connection: ConnectionLike, *, item: dict[str, Any], code: str, actor: str, now: datetime) -> Any:
    return connection.execute("UPDATE semantic_import_review_items SET review_state='STALE', error_json=?, revision=revision+1, updated_at=?, updated_by=? WHERE id=? AND revision=? RETURNING revision", [json.dumps({"code": code}), now, actor, item["id"], item["revision"]]).fetchone()


def add_event(connection: ConnectionLike, *, item_id: str, old: str | None, new: str, revision: int, actor: str, detail: dict[str, Any], now: datetime, prior: str | None = None, current: str | None = None) -> None:
    connection.execute("INSERT INTO semantic_import_review_events VALUES (?,?,?,?,?,?,?,?,?,?)", [f"semantic-review-event-{uuid4().hex[:12]}", item_id, old, new, revision, prior, current, json.dumps(detail, ensure_ascii=False), now, actor])


def unresolved_item_id(connection: ConnectionLike, *, binding_id: str, load_case_id: str, relative_path: str) -> Any:
    suffix = " FOR UPDATE" if getattr(connection, "backend", "duckdb") == "postgresql" else ""
    return connection.execute("SELECT id FROM semantic_import_review_items WHERE binding_id=? AND load_case_id=? AND relative_path=?" + suffix, [binding_id, load_case_id, relative_path]).fetchone()


def create_unresolved_item(connection: ConnectionLike, *, ident: str, binding: dict[str, Any], relative_path: str, source_sha256: str | None, source_size: int | None, scan_status: str, candidates: list[dict[str, Any]], error: dict[str, Any] | None, actor: str, now: datetime) -> None:
    connection.execute("INSERT INTO semantic_import_review_items(id,binding_id,binding_revision,load_case_id,relative_path,source_sha256,source_size,scan_status,review_state,candidates_json,error_json,revision,created_at,updated_at,created_by,updated_by) VALUES (?,?,?,?,?,?,?,?,? ,?,?,1,?,?,?,?)", [ident, binding["id"], binding["revision"], binding["load_case_id"], relative_path, source_sha256, source_size, scan_status, "OPEN", json.dumps(candidates, ensure_ascii=False), json.dumps(error, ensure_ascii=False) if error else None, now, now, actor, actor])


def reopen_from_scan(connection: ConnectionLike, *, binding: dict[str, Any], previous: dict[str, Any], source_sha256: str | None, source_size: int | None, scan_status: str, candidates: list[dict[str, Any]], error: dict[str, Any] | None, prior_run: str | None, actor: str, now: datetime) -> Any:
    return connection.execute("UPDATE semantic_import_review_items SET binding_revision=?, source_sha256=?, source_size=?, scan_status=?, review_state='OPEN', candidates_json=?, selected_recipe_id=NULL, selected_recipe_version=NULL, template_id=NULL, template_version=NULL, validated_sha256=NULL, validation_summary_json=NULL, error_json=?, previous_confirmed_analysis_run_id=?, confirmed_analysis_run_id=NULL, revision=revision+1, updated_at=?, updated_by=? WHERE id=? AND revision=? RETURNING revision", [binding["revision"], source_sha256, source_size, scan_status, json.dumps(candidates, ensure_ascii=False), json.dumps(error, ensure_ascii=False) if error else None, prior_run, now, actor, previous["id"], previous["revision"]]).fetchone()


def page_items(
    connection: ConnectionLike,
    *,
    binding_id: str,
    load_case_id: str,
    state: str | None,
    cursor: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Read one stable review-item page for a binding and its current target."""

    query = "SELECT id FROM semantic_import_review_items WHERE binding_id=? AND load_case_id=?"
    arguments: list[Any] = [binding_id, load_case_id]
    if state:
        query += " AND review_state=?"
        arguments.append(state)
    query += " AND id>? ORDER BY id LIMIT ?"
    arguments.extend((cursor, limit))
    return [dict(row) for row in rows(connection.execute(query, arguments))]


def events(connection: ConnectionLike, item_id: str, limit: int) -> list[dict[str, Any]]:
    return [dict(row) for row in rows(connection.execute("SELECT * FROM semantic_import_review_events WHERE review_item_id=? ORDER BY occurred_at DESC, id DESC LIMIT ?", [item_id, limit]))]


def active_recipe(connection: ConnectionLike, recipe_id: str) -> Any:
    return connection.execute("SELECT active_version FROM semantic_recipes WHERE id=?", [recipe_id]).fetchone()


def active_template(connection: ConnectionLike, template_id: str) -> Any:
    return connection.execute("SELECT active_version FROM semantic_templates WHERE id=?", [template_id]).fetchone()


def mark_ready(connection: ConnectionLike, *, item_id: str, expected_revision: int, recipe_id: str, recipe_version: int, template_id: str | None, template_version: int | None, digest: str, summary: dict[str, Any], actor: str, now: datetime) -> Any:
    return connection.execute("UPDATE semantic_import_review_items SET review_state='READY', selected_recipe_id=?, selected_recipe_version=?, template_id=?, template_version=?, validated_sha256=?, validation_summary_json=?, error_json=NULL, revision=revision+1, updated_at=?, updated_by=?, validated_at=?, validated_by=? WHERE id=? AND revision=? RETURNING revision", [recipe_id, recipe_version, template_id, template_version, digest, json.dumps(summary, ensure_ascii=False), now, actor, now, actor, item_id, expected_revision]).fetchone()


def mark_confirmed(connection: ConnectionLike, *, item_id: str, expected_revision: int, state: str, run_id: str | None, actor: str, now: datetime) -> Any:
    return connection.execute("UPDATE semantic_import_review_items SET review_state=?, confirmed_analysis_run_id=?, confirmed_at=?, confirmed_by=?, revision=revision+1, updated_at=?, updated_by=? WHERE id=? AND revision=? RETURNING revision", [state, run_id, now, actor, now, actor, item_id, expected_revision]).fetchone()


def reopen_explicit(connection: ConnectionLike, *, item_id: str, expected_revision: int, digest: str, source_size: int, prior_run: str | None, actor: str, now: datetime) -> Any:
    return connection.execute("UPDATE semantic_import_review_items SET source_sha256=?, source_size=?, scan_status='PENDING', review_state='OPEN', selected_recipe_id=NULL, selected_recipe_version=NULL, template_id=NULL, template_version=NULL, validated_sha256=NULL, validation_summary_json=NULL, error_json=NULL, previous_confirmed_analysis_run_id=?, confirmed_analysis_run_id=NULL, revision=revision+1, updated_at=?, updated_by=? WHERE id=? AND revision=? RETURNING revision", [digest, source_size, prior_run, now, actor, item_id, expected_revision]).fetchone()
