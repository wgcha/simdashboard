"""Persistence queries for versioned semantic-mapping definitions."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from ..database_connection import ConnectionLike, rows


@contextmanager
def definition_transaction(connection: ConnectionLike) -> Iterator[None]:
    connection.execute("BEGIN TRANSACTION")
    try:
        yield
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def catalog(connection: ConnectionLike) -> dict[str, list[dict[str, Any]]]:
    def listed(table: str, version_table: str, id_column: str) -> list[dict[str, Any]]:
        return [dict(row) for row in rows(connection.execute(
            f"SELECT base.id, base.{'key' if table == 'semantic_result_items' else 'name'} AS name, base.latest_version, base.active_version, base.updated_at, base.updated_by, version.definition_json, version.lifecycle_status FROM {table} base JOIN {version_table} version ON version.{id_column}=base.id AND version.version=base.latest_version ORDER BY base.updated_at DESC, base.id"
        ))]
    return {"items": listed("semantic_result_items", "semantic_result_item_versions", "item_id"), "recipes": listed("semantic_recipes", "semantic_recipe_versions", "recipe_id"), "templates": listed("semantic_templates", "semantic_template_versions", "template_id"), "bindings": [dict(row) for row in rows(connection.execute("SELECT * FROM semantic_folder_bindings ORDER BY relative_path"))]}


def version(connection: ConnectionLike, kind: str, ident: str, *, active: bool) -> Any:
    base, versions, column = {"recipe": ("semantic_recipes", "semantic_recipe_versions", "recipe_id"), "template": ("semantic_templates", "semantic_template_versions", "template_id")}[kind]
    field = "active_version" if active else "latest_version"
    return connection.execute(f"SELECT b.{field}, v.definition_json, v.item_snapshot_json FROM {base} b JOIN {versions} v ON v.{column}=b.id AND v.version=b.{field} WHERE b.id=?", [ident]).fetchone()


def version_at(connection: ConnectionLike, kind: str, ident: str, version_number: int) -> Any:
    _, versions, column = {"recipe": ("semantic_recipes", "semantic_recipe_versions", "recipe_id"), "template": ("semantic_templates", "semantic_template_versions", "template_id")}[kind]
    return connection.execute(f"SELECT definition_json, item_snapshot_json FROM {versions} WHERE {column}=? AND version=?", [ident, version_number]).fetchone()


def save_version(connection: ConnectionLike, *, kind: str, ident: str, name: str, definition: dict[str, Any], expected: int | None, actor: str, now: datetime, item_snapshot: list[dict[str, Any]]) -> tuple[int, int]:
    base, versions, column = {"item": ("semantic_result_items", "semantic_result_item_versions", "item_id"), "recipe": ("semantic_recipes", "semantic_recipe_versions", "recipe_id"), "template": ("semantic_templates", "semantic_template_versions", "template_id")}[kind]
    lock_suffix = " FOR UPDATE" if getattr(connection, "backend", "duckdb") == "postgresql" else ""
    existing = connection.execute(f"SELECT latest_version FROM {base} WHERE id=?{lock_suffix}", [ident]).fetchone()
    if existing is not None:
        latest = int(existing[0])
        if expected is None or expected != latest:
            return latest, 0
        version_number = latest + 1
        if kind == "item":
            connection.execute(f"UPDATE {base} SET key=?, latest_version=?, updated_at=?, updated_by=? WHERE id=? AND latest_version=?", [str(definition["key"]), version_number, now, actor, ident, latest])
        else:
            connection.execute(f"UPDATE {base} SET name=?, latest_version=?, updated_at=?, updated_by=? WHERE id=? AND latest_version=?", [name.strip(), version_number, now, actor, ident, latest])
    else:
        if expected not in (None, 0):
            return 0, 0
        version_number = 1
        if kind == "item":
            connection.execute(f"INSERT INTO {base}(id, key, latest_version, active_version, created_at, updated_at, updated_by) VALUES (?, ?, ?, NULL, ?, ?, ?)", [ident, str(definition.get("key") or ident), version_number, now, now, actor])
        else:
            connection.execute(f"INSERT INTO {base}(id, name, latest_version, active_version, created_at, updated_at, updated_by) VALUES (?, ?, ?, NULL, ?, ?, ?)", [ident, name.strip(), version_number, now, now, actor])
    connection.execute(f"INSERT INTO {versions}({column}, version, definition_json, item_snapshot_json, lifecycle_status, created_at, created_by) VALUES (?, ?, ?, ?, 'DRAFT', ?, ?)", [ident, version_number, json.dumps(definition, ensure_ascii=False), json.dumps(item_snapshot, ensure_ascii=False), now, actor])
    return version_number, version_number


def lock_item(connection: ConnectionLike, item_id: str) -> None:
    if getattr(connection, "backend", "duckdb") == "postgresql":
        connection.execute("SELECT id FROM semantic_result_items WHERE id=? FOR UPDATE", [item_id]).fetchone()


def item_key_owner(connection: ConnectionLike, key: str) -> Any:
    return connection.execute("SELECT id FROM semantic_result_items WHERE key=?", [key]).fetchone()


def previous_item_definition(connection: ConnectionLike, item_id: str) -> Any:
    return connection.execute("SELECT definition_json FROM semantic_result_item_versions WHERE item_id=? ORDER BY version DESC LIMIT 1", [item_id]).fetchone()


def all_item_snapshots(connection: ConnectionLike) -> list[Any]:
    return [row[0] for row in connection.execute("SELECT item_snapshot_json FROM semantic_recipe_versions UNION ALL SELECT item_snapshot_json FROM semantic_template_versions").fetchall()]


def lock_items_for_definition_save(connection: ConnectionLike) -> None:
    if getattr(connection, "backend", "duckdb") == "postgresql":
        connection.execute("SELECT id FROM semantic_result_items ORDER BY id FOR SHARE").fetchall()


def store_recipe_sample(connection: ConnectionLike, *, filename: str, digest: str, content: bytes, recipe_id: str, version_number: int) -> None:
    connection.execute("UPDATE semantic_recipe_versions SET sample_filename=?, sample_sha256=?, sample_bytes=? WHERE recipe_id=? AND version=?", [filename, digest, content, recipe_id, version_number])


def lock_binding_tables(connection: ConnectionLike) -> None:
    if getattr(connection, "backend", "duckdb") == "postgresql":
        connection.execute("LOCK TABLE semantic_folder_bindings, spdm_storage_bindings IN SHARE ROW EXCLUSIVE MODE")


def binding_lineage(connection: ConnectionLike, request_id: str | None, load_case_id: str | None) -> Any:
    return connection.execute("SELECT ar.project_id, ar.id, lc.id FROM analysis_requests ar LEFT JOIN load_cases lc ON lc.request_id=ar.id WHERE ar.id=coalesce(?, (SELECT request_id FROM load_cases WHERE id=?)) AND (? IS NULL OR lc.id=?)", [request_id, load_case_id, load_case_id, load_case_id]).fetchone()


def legacy_binding_paths(connection: ConnectionLike) -> list[str]:
    return [str(row[0]).casefold() for row in connection.execute("SELECT relative_path FROM spdm_storage_bindings").fetchall()]


def semantic_bindings(connection: ConnectionLike) -> list[dict[str, Any]]:
    return [dict(row) for row in rows(connection.execute("SELECT b.id, b.project_id, coalesce(b.request_id, lc.request_id) AS request_id, b.load_case_id, b.relative_path FROM semantic_folder_bindings b LEFT JOIN load_cases lc ON lc.id=b.load_case_id"))]


def binding_revision(connection: ConnectionLike, binding_id: str) -> Any:
    suffix = " FOR UPDATE" if getattr(connection, "backend", "duckdb") == "postgresql" else ""
    return connection.execute("SELECT revision FROM semantic_folder_bindings WHERE id=?" + suffix, [binding_id]).fetchone()


def update_binding(connection: ConnectionLike, *, payload: Any, relative_path: str, revision: int, actor: str, now: datetime) -> None:
    connection.execute("UPDATE semantic_folder_bindings SET project_id=?, request_id=?, load_case_id=?, relative_path=?, role=?, recipe_ids_json=?, template_id=?, updated_at=?, created_by=?, revision=? WHERE id=? AND revision=?", [payload.project_id, payload.request_id, payload.load_case_id, relative_path, payload.role, json.dumps(payload.recipe_ids), payload.template_id, now, actor, revision, payload.id, int(payload.expected_revision)])


def create_binding(connection: ConnectionLike, *, binding_id: str, payload: Any, relative_path: str, actor: str, now: datetime) -> None:
    connection.execute("INSERT INTO semantic_folder_bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [binding_id, payload.project_id, payload.request_id, payload.load_case_id, relative_path, payload.role, json.dumps(payload.recipe_ids), payload.template_id, now, now, actor, 1])


def provenance_template(connection: ConnectionLike, run_id: str) -> Any:
    return connection.execute("SELECT template_id, template_version FROM semantic_import_provenance WHERE analysis_run_id=?", [run_id]).fetchone()


def binding(connection: ConnectionLike, binding_id: str) -> dict[str, Any] | None:
    entries = rows(connection.execute("SELECT * FROM semantic_folder_bindings WHERE id=?", [binding_id]))
    return dict(entries[0]) if entries else None


def review_exists(connection: ConnectionLike, binding_id: str, load_case_id: str, relative_path: str) -> bool:
    return connection.execute("SELECT id FROM semantic_import_review_items WHERE binding_id=? AND load_case_id=? AND relative_path=?", [binding_id, load_case_id, relative_path]).fetchone() is not None


def latest_provenance(connection: ConnectionLike, load_case_id: str, run_id: str | None) -> dict[str, Any] | None:
    query, args = "SELECT * FROM semantic_import_provenance WHERE load_case_id=?", [load_case_id]
    if run_id:
        query += " AND analysis_run_id=?"; args.append(run_id)
    query += " ORDER BY created_at DESC LIMIT 1"
    result = connection.execute(query, args)
    row = result.fetchone()
    return dict(zip([column[0] for column in result.description], row)) if row else None


def item_key_exists(connection: ConnectionLike, key: str) -> bool:
    return connection.execute("SELECT 1 FROM semantic_result_items WHERE key=?", [key]).fetchone() is not None
