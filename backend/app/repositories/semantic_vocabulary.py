"""SQL persistence for the semantic vocabulary aggregate."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from ..database_connection import ConnectionLike, rows


class TermConflictError(Exception):
    def __init__(self, term: str) -> None:
        self.term = term


@contextmanager
def vocabulary_transaction(connection: ConnectionLike) -> Iterator[None]:
    connection.execute("BEGIN TRANSACTION")
    try:
        yield
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def target_context(connection: ConnectionLike, kind: str, target_id: str) -> tuple[str, dict[str, str | None]] | None:
    missing = {"project_id": None, "request_id": None, "load_case_id": None}
    if kind == "FOLDER_ROLE":
        return target_id, missing
    if kind == "PROJECT":
        row = connection.execute("SELECT id, name FROM projects WHERE id=?", [target_id]).fetchone()
        return (str(row[1]), {"project_id": str(row[0]), "request_id": None, "load_case_id": None}) if row else None
    if kind == "REQUEST":
        row = connection.execute("SELECT id, project_id, title FROM analysis_requests WHERE id=?", [target_id]).fetchone()
        return (str(row[2]), {"project_id": str(row[1]), "request_id": str(row[0]), "load_case_id": None}) if row else None
    if kind == "LOAD_CASE":
        row = connection.execute("SELECT lc.id, ar.project_id, ar.id, lc.name FROM load_cases lc JOIN analysis_requests ar ON ar.id=lc.request_id WHERE lc.id=?", [target_id]).fetchone()
        return (str(row[3]), {"project_id": str(row[1]), "request_id": str(row[2]), "load_case_id": str(row[0])}) if row else None
    row = connection.execute("SELECT i.id, i.key, v.definition_json FROM semantic_result_items i JOIN semantic_result_item_versions v ON v.item_id=i.id AND v.version=i.latest_version WHERE i.id=?", [target_id]).fetchone()
    if not row:
        return None
    definition = json.loads(row[2]) if isinstance(row[2], str) else row[2]
    return str(definition.get("label") or row[1]), missing


def scope_exists(connection: ConnectionLike, project_id: str) -> bool:
    return connection.execute("SELECT 1 FROM projects WHERE id=?", [project_id]).fetchone() is not None


def entry_record(connection: ConnectionLike, entry_id: str) -> dict[str, Any] | None:
    records = rows(connection.execute("SELECT * FROM semantic_vocabulary_entries WHERE id=?", [entry_id]))
    return records[0] if records else None


def entry_ids(connection: ConnectionLike) -> list[str]:
    return [str(row["id"]) for row in rows(connection.execute("SELECT id FROM semantic_vocabulary_entries ORDER BY scope_project_id NULLS FIRST, target_kind, key"))]


def lock_vocabulary_tables(connection: ConnectionLike) -> None:
    if getattr(connection, "backend", "duckdb") == "postgresql":
        connection.execute("LOCK TABLE semantic_vocabulary_entries, semantic_vocabulary_terms IN SHARE ROW EXCLUSIVE MODE")


def entry_count(connection: ConnectionLike) -> int:
    return int(connection.execute("SELECT count(*) FROM semantic_vocabulary_entries").fetchone()[0])


def create_entry(connection: ConnectionLike, *, entry_id: str, body: Any, actor: str, now: datetime) -> None:
    connection.execute("INSERT INTO semantic_vocabulary_entries(id, key, label, description, target_kind, target_id, scope_project_id, aliases_json, revision, enabled, created_at, updated_at, created_by, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)", [entry_id, body.key, body.label, body.description, body.target_kind, body.target_id, body.scope_project_id, json.dumps(body.aliases, ensure_ascii=False), body.enabled, now, now, actor, actor])


def current_entry(connection: ConnectionLike, entry_id: str) -> Any:
    lock = " FOR UPDATE" if getattr(connection, "backend", "duckdb") == "postgresql" else ""
    return connection.execute(f"SELECT key, target_kind, target_id, scope_project_id, revision FROM semantic_vocabulary_entries WHERE id=?{lock}", [entry_id]).fetchone()


def rewrite_entry(connection: ConnectionLike, *, entry_id: str, body: Any, revision: int, actor: str, now: datetime) -> None:
    connection.execute("DELETE FROM semantic_vocabulary_terms WHERE entry_id=?", [entry_id])
    connection.execute("UPDATE semantic_vocabulary_entries SET label=?, description=?, aliases_json=?, enabled=?, revision=?, updated_at=?, updated_by=? WHERE id=? AND revision=?", [body.label, body.description, json.dumps(body.aliases, ensure_ascii=False), body.enabled, revision, now, actor, entry_id, body.expected_revision])


def insert_terms(connection: ConnectionLike, *, entry_id: str, target_kind: str, scope_project_id: str | None, enabled: bool, terms: list[str]) -> None:
    if not enabled:
        return
    scope_key = scope_project_id or ""
    for term in terms:
        try:
            connection.execute("INSERT INTO semantic_vocabulary_terms(entry_id, scope_key, target_kind, normalized_term) VALUES (?, ?, ?, ?)", [entry_id, scope_key, target_kind, term])
        except Exception as error:
            message = str(error).casefold()
            if "duplicate" in message or "unique" in message or "primary key" in message:
                raise TermConflictError(term) from error
            raise


def resolve_term_ids_for_normalized(connection: ConnectionLike, *, scope_key: str, target_kind: str, normalized_term: str) -> list[str]:
    return [str(row["entry_id"]) for row in rows(connection.execute("SELECT t.entry_id FROM semantic_vocabulary_terms t JOIN semantic_vocabulary_entries e ON e.id=t.entry_id WHERE t.scope_key=? AND t.target_kind=? AND t.normalized_term=? AND e.enabled=true", [scope_key, target_kind, normalized_term]))]
