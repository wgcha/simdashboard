"""HTTP boundary for versioned semantic result mapping configuration."""
from __future__ import annotations

import hashlib
import json
import base64
from email import policy
from email.parser import BytesParser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..adapters.persistence.result_ingestion import SQLResultIngestionQuery
from ..database_connection import connect, rows
from ..domains.semantic_mapping.engine import (
    SemanticValidationError,
    inspect_sample,
    preview_recipe,
    resolve_widgets,
    validate_item,
    validate_recipe,
    validate_template,
)
from ..modules.access_control import PROJECT_DATA_VIEW, RESULT_IMPORT, SYSTEM_CATALOG_MANAGE, require_permission, require_resource_permission
from ..security import write_audit_event
from ..services import spdm_storage
from ..services.semantic_mapping import persist_semantic_import, semantic_transaction
from .semantic_body_limit import SemanticBodyLimitRoute


router = APIRouter(prefix="/api/semantic-mapping", tags=["semantic-mapping"], route_class=SemanticBodyLimitRoute)
_SAMPLE_LIMIT = 256 * 1024


class ItemSave(BaseModel):
    id: str | None = None
    definition: dict[str, Any]
    expected_version: int | None = None


class DefinitionSave(BaseModel):
    id: str | None = None
    name: str
    definition: dict[str, Any]
    expected_version: int | None = None
    sample_filename: str | None = None
    sample_content_base64: str | None = Field(default=None, max_length=349528)


class Activate(BaseModel):
    version: int = Field(ge=1)


class BindingSave(BaseModel):
    id: str | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    relative_path: str
    project_id: str
    request_id: str | None = None
    load_case_id: str | None = None
    role: str
    recipe_ids: list[str] = Field(default_factory=list)
    template_id: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _error(error: Exception) -> HTTPException:
    if isinstance(error, HTTPException):
        return error
    if isinstance(error, SemanticValidationError):
        return HTTPException(422, {"code": error.code, "message": str(error)})
    if isinstance(error, spdm_storage.SpdmStorageError):
        return HTTPException(422, {"code": error.code, "message": str(error)})
    return HTTPException(422, {"code": "SEMANTIC_MAPPING_INVALID", "message": "의미 매핑 정의가 올바르지 않습니다."})


def _catalog(conn: Any) -> dict[str, list[dict[str, Any]]]:
    def listed(table: str, version_table: str, id_column: str) -> list[dict[str, Any]]:
        records = []
        for row in rows(conn.execute(
            f"SELECT base.id, base.{ 'key' if table == 'semantic_result_items' else 'name' } AS name, base.latest_version, base.active_version, base.updated_at, base.updated_by, "
            f"version.definition_json, version.lifecycle_status "
            f"FROM {table} base JOIN {version_table} version ON version.{id_column}=base.id AND version.version=base.latest_version "
            "ORDER BY base.updated_at DESC, base.id"
        )):
            record = dict(row); record["definition"] = _json(record.pop("definition_json")); records.append(record)
        return records
    return {
        "items": listed("semantic_result_items", "semantic_result_item_versions", "item_id"),
        "recipes": listed("semantic_recipes", "semantic_recipe_versions", "recipe_id"),
        "templates": listed("semantic_templates", "semantic_template_versions", "template_id"),
        "bindings": [
            {**dict(row), "recipe_ids": _json(row["recipe_ids_json"])}
            for row in rows(conn.execute("SELECT * FROM semantic_folder_bindings ORDER BY relative_path"))
        ],
    }


def _items(conn: Any) -> list[dict[str, Any]]:
    return [entry["definition"] | {"id": entry["id"], "version": entry["latest_version"]} for entry in _catalog(conn)["items"]]


def _version(conn: Any, kind: str, ident: str, active: bool = True) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    base, versions, column = {
        "recipe": ("semantic_recipes", "semantic_recipe_versions", "recipe_id"),
        "template": ("semantic_templates", "semantic_template_versions", "template_id"),
    }[kind]
    field = "active_version" if active else "latest_version"
    row = conn.execute(
        f"SELECT b.{field}, v.definition_json, v.item_snapshot_json FROM {base} b JOIN {versions} v ON v.{column}=b.id AND v.version=b.{field} WHERE b.id=?",
        [ident],
    ).fetchone()
    if not row or row[0] is None:
        raise HTTPException(409, {"code": f"SEMANTIC_{kind.upper()}_NOT_ACTIVE", "message": "활성 정의가 없습니다."})
    return int(row[0]), _json(row[1]), _json(row[2])


def _version_at(conn: Any, kind: str, ident: str, version: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _, versions, column = {"recipe": ("semantic_recipes", "semantic_recipe_versions", "recipe_id"), "template": ("semantic_templates", "semantic_template_versions", "template_id")}[kind]
    row = conn.execute(f"SELECT definition_json, item_snapshot_json FROM {versions} WHERE {column}=? AND version=?", [ident, version]).fetchone()
    if not row: raise HTTPException(409, {"code": "SEMANTIC_VERSION_NOT_FOUND"})
    return _json(row[0]), _json(row[1])


def _save_version(conn: Any, kind: str, ident: str | None, name: str, definition: dict[str, Any], expected: int | None, actor: str) -> dict[str, Any]:
    base, versions, column = {
        "item": ("semantic_result_items", "semantic_result_item_versions", "item_id"),
        "recipe": ("semantic_recipes", "semantic_recipe_versions", "recipe_id"),
        "template": ("semantic_templates", "semantic_template_versions", "template_id"),
    }[kind]
    ident = ident or f"semantic-{kind}-{uuid4().hex[:12]}"
    now = _now()
    lock_suffix = " FOR UPDATE" if getattr(conn, "backend", "duckdb") == "postgresql" else ""
    existing = conn.execute(f"SELECT latest_version FROM {base} WHERE id=?{lock_suffix}", [ident]).fetchone()
    if existing is not None:
        latest = int(existing[0])
        if expected is None or expected != latest:
            raise HTTPException(409, {"code": "SEMANTIC_REVISION_CONFLICT", "current_version": latest})
        version = latest + 1
        if kind == "item":
            conn.execute(f"UPDATE {base} SET key=?, latest_version=?, updated_at=?, updated_by=? WHERE id=? AND latest_version=?", [str(definition["key"]), version, now, actor, ident, latest])
        else:
            conn.execute(f"UPDATE {base} SET name=?, latest_version=?, updated_at=?, updated_by=? WHERE id=? AND latest_version=?", [name.strip(), version, now, actor, ident, latest])
    else:
        if expected not in (None, 0):
            raise HTTPException(409, {"code": "SEMANTIC_REVISION_CONFLICT", "current_version": 0})
        version = 1
        key = str(definition.get("key") or ident)
        if kind == "item":
            conn.execute(f"INSERT INTO {base}(id, key, latest_version, active_version, created_at, updated_at, updated_by) VALUES (?, ?, ?, NULL, ?, ?, ?)", [ident, key, version, now, now, actor])
        else:
            conn.execute(f"INSERT INTO {base}(id, name, latest_version, active_version, created_at, updated_at, updated_by) VALUES (?, ?, ?, NULL, ?, ?, ?)", [ident, name.strip(), version, now, now, actor])
    snapshot = [] if kind == "item" else _items(conn)
    conn.execute(f"INSERT INTO {versions}({column}, version, definition_json, item_snapshot_json, lifecycle_status, created_at, created_by) VALUES (?, ?, ?, ?, 'DRAFT', ?, ?)", [ident, version, json.dumps(definition, ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False), now, actor])
    return {"id": ident, "version": version, "lifecycle_status": "DRAFT", "definition": definition, "item_snapshot": snapshot}


@router.get("/catalog")
def get_catalog(request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        return _catalog(conn)


@router.post("/items", status_code=201)
def save_item(payload: ItemSave, request: Request) -> dict[str, Any]:
    with connect() as conn, semantic_transaction(conn):
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        item_id = payload.id or payload.definition.get("id") or f"semantic-item-{uuid4().hex[:12]}"
        definition = dict(payload.definition); definition["id"] = item_id
        try: definition = validate_item(definition)
        except Exception as error: raise _error(error) from error
        if getattr(conn, "backend", "duckdb") == "postgresql":
            conn.execute("SELECT id FROM semantic_result_items WHERE id=? FOR UPDATE", [item_id]).fetchone()
        key_owner = conn.execute("SELECT id FROM semantic_result_items WHERE key=?", [definition["key"]]).fetchone()
        if key_owner and str(key_owner[0]) != item_id:
            raise HTTPException(409, {"code": "SEMANTIC_ITEM_KEY_CONFLICT"})
        previous = conn.execute("SELECT definition_json FROM semantic_result_item_versions WHERE item_id=? ORDER BY version DESC LIMIT 1", [item_id]).fetchone()
        if previous:
            old = _json(previous[0])
            changed = any(old.get(field) != definition.get(field) for field in ("key", "kind", "data_type", "unit", "dimensions"))
            if changed:
                snapshots = [row[0] for row in conn.execute("SELECT item_snapshot_json FROM semantic_recipe_versions UNION ALL SELECT item_snapshot_json FROM semantic_template_versions").fetchall()]
                if any(any(entry.get("id") == item_id for entry in _json(snapshot)) for snapshot in snapshots):
                    raise HTTPException(422, {"code": "SEMANTIC_ITEM_MEANING_IMMUTABLE", "message": "사용 중인 결과 항목의 의미는 변경할 수 없습니다."})
        result = _save_version(conn, "item", item_id, str(definition["label"]), definition, payload.expected_version, request.state.principal.user_id)
        write_audit_event(request=request, principal=request.state.principal, status_code=201, action="SEMANTIC_ITEM_SAVED", detail={"item_id": result["id"], "version": result["version"]}, connection=conn)
        return result


def _save_definition(kind: str, payload: DefinitionSave, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        conn.execute("BEGIN TRANSACTION")
        try:
            if getattr(conn, "backend", "duckdb") == "postgresql":
                conn.execute("SELECT id FROM semantic_result_items ORDER BY id FOR SHARE").fetchall()
            definitions = _items(conn)
            (validate_recipe if kind == "recipe" else validate_template)(payload.definition, definitions)
            if kind == "recipe" and payload.sample_content_base64 is not None:
                try: sample = base64.b64decode(payload.sample_content_base64, validate=True)
                except ValueError as error: raise HTTPException(422, {"code": "SEMANTIC_SAMPLE_INVALID"}) from error
                if not payload.sample_filename or not sample or len(sample) > _SAMPLE_LIMIT: raise HTTPException(422, {"code": "SEMANTIC_SAMPLE_INVALID"})
                preview_recipe(payload.definition, definitions, payload.sample_filename, sample)
            else: sample = None
            result = _save_version(conn, kind, payload.id, payload.name, payload.definition, payload.expected_version, request.state.principal.user_id)
            if sample is not None:
                conn.execute("UPDATE semantic_recipe_versions SET sample_filename=?, sample_sha256=?, sample_bytes=? WHERE recipe_id=? AND version=?", [payload.sample_filename, hashlib.sha256(sample).hexdigest(), sample, result["id"], result["version"]])
            write_audit_event(request=request, principal=request.state.principal, status_code=201, action=f"SEMANTIC_{kind.upper()}_SAVED", detail={f"{kind}_id": result["id"], "version": result["version"]}, connection=conn)
            conn.execute("COMMIT")
            return result
        except BaseException as error:
            conn.execute("ROLLBACK")
            if isinstance(error, SemanticValidationError):
                raise _error(error) from error
            raise


@router.post("/recipes", status_code=201)
def save_recipe(payload: DefinitionSave, request: Request) -> dict[str, Any]: return _save_definition("recipe", payload, request)


@router.post("/templates", status_code=201)
def save_template(payload: DefinitionSave, request: Request) -> dict[str, Any]: return _save_definition("template", payload, request)


def _activate(kind: str, ident: str, payload: Activate, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
    raise HTTPException(410, {"code": "SEMANTIC_ACTIVATION_BUNDLE_REQUIRED", "message": "레시피와 템플릿을 함께 검증·활성화하세요."})


@router.post("/recipes/{recipe_id}/activate")
def activate_recipe(recipe_id: str, payload: Activate, request: Request) -> dict[str, Any]: return _activate("recipe", recipe_id, payload, request)


@router.post("/templates/{template_id}/activate")
def activate_template(template_id: str, payload: Activate, request: Request) -> dict[str, Any]: return _activate("template", template_id, payload, request)


async def _multipart(request: Request) -> tuple[dict[str, str], str, bytes]:
    """Read the small, bounded browser multipart subset without a new runtime dependency.

    The application otherwise has no multipart endpoints.  Keeping this parser
    here avoids changing the locked offline dependency bundle merely for three
    CSV/JSON upload routes; every part is length-bounded and only one file is
    accepted.
    """
    content_type = request.headers.get("content-type", "")
    marker = "boundary="
    if "multipart/form-data" not in content_type or marker not in content_type:
        raise HTTPException(415, {"code": "SEMANTIC_MULTIPART_REQUIRED"})
    boundary = content_type.split(marker, 1)[1].strip().strip('"').encode("ascii", "strict")
    if not boundary or len(boundary) > 200:
        raise HTTPException(422, {"code": "SEMANTIC_MULTIPART_INVALID"})
    declared = request.headers.get("content-length")
    if declared and (not declared.isdigit() or int(declared) > 6 * 1024 * 1024):
        raise HTTPException(413, {"code": "SEMANTIC_SAMPLE_TOO_LARGE"})
    chunks=[]; total=0
    async for chunk in request.stream():
        total += len(chunk)
        if total > 6 * 1024 * 1024: raise HTTPException(413, {"code": "SEMANTIC_SAMPLE_TOO_LARGE"})
        chunks.append(chunk)
    try:
        message = BytesParser(policy=policy.default).parsebytes((f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode("ascii") + b"".join(chunks))
    except (ValueError, UnicodeError):
        raise HTTPException(422, {"code": "SEMANTIC_MULTIPART_INVALID"}) from None
    if not message.is_multipart() or message.defects:
        raise HTTPException(422, {"code": "SEMANTIC_MULTIPART_INVALID"})
    fields: dict[str, str] = {}; filename = ""; content = b""; seen=set(); file_count=0
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name or name in seen or part.get("Content-Transfer-Encoding"):
            raise HTTPException(422, {"code": "SEMANTIC_MULTIPART_INVALID"})
        seen.add(name); value=part.get_payload(decode=True) or b""
        if name == "file":
            file_count += 1; filename = part.get_filename() or ""; content = value
        else:
            try: fields[name] = value.decode("utf-8")
            except UnicodeError: raise HTTPException(422, {"code": "SEMANTIC_MULTIPART_INVALID"}) from None
    if file_count != 1:
        raise HTTPException(422, {"code": "SEMANTIC_MULTIPART_INVALID"})
    if not filename or not content or len(content) > 32 * 1024 * 1024:
        raise HTTPException(422, {"code": "SEMANTIC_SAMPLE_INVALID"})
    return fields, filename, content


@router.post("/inspect")
async def inspect(request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
    _, filename, content = await _multipart(request)
    try: return inspect_sample(filename, content)
    except Exception as error: raise _error(error) from error


@router.post("/preview")
async def preview(request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
    fields, filename, content = await _multipart(request)
    try:
        recipe_definition = json.loads(fields["recipe"]); template_definition = json.loads(fields["template"]) if fields.get("template") else None
        with connect() as conn:
            catalog_items = _items(conn)
        parsed = preview_recipe(recipe_definition, catalog_items, filename, content)
        return {"parsed": parsed, "widgets": resolve_widgets(template_definition, catalog_items, parsed) if template_definition else []}
    except Exception as error: raise _error(error) from error


@router.get("/folders")
def folders(relative_path: str | None, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        root = spdm_storage.storage_root(conn)
        if root.root is None: raise HTTPException(409, {"code": "SPDM_ROOT_UNSET"})
        base = root.root if not relative_path else root.root.joinpath(*spdm_storage._normalise_relative(relative_path).split("/"))
        spdm_storage._assert_safe_existing(base, root.root)
        if not base.is_dir(): raise HTTPException(404, {"code": "SPDM_PATH_MISSING"})
        return {"entries": [{"name": p.name, "relative_path": p.relative_to(root.root).as_posix(), "is_directory": p.is_dir()} for p in sorted(base.iterdir(), key=lambda p: p.name.casefold()) if not spdm_storage._is_reparse(p)]}


@router.post("/bindings", status_code=201)
def save_binding(payload: BindingSave, request: Request) -> dict[str, Any]:
    with connect() as conn, semantic_transaction(conn):
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        require_permission(request, RESULT_IMPORT, payload.project_id, conn=conn)
        if getattr(conn, "backend", "duckdb") == "postgresql":
            conn.execute("LOCK TABLE semantic_folder_bindings, spdm_storage_bindings IN SHARE ROW EXCLUSIVE MODE")
        if payload.load_case_id: require_resource_permission(request, RESULT_IMPORT, "load_case", payload.load_case_id, conn=conn)
        if payload.request_id or payload.load_case_id:
            lineage = conn.execute(
                "SELECT ar.project_id, ar.id, lc.id FROM analysis_requests ar LEFT JOIN load_cases lc ON lc.request_id=ar.id WHERE ar.id=coalesce(?, (SELECT request_id FROM load_cases WHERE id=?)) AND (? IS NULL OR lc.id=?)",
                [payload.request_id, payload.load_case_id, payload.load_case_id, payload.load_case_id],
            ).fetchone()
            if not lineage or str(lineage[0]) != payload.project_id or (payload.request_id and str(lineage[1]) != payload.request_id):
                raise HTTPException(422, {"code": "SEMANTIC_BINDING_TARGET_MISMATCH"})
            payload = payload.model_copy(update={"request_id": str(lineage[1])})
        if payload.role not in {"PROJECT", "REQUEST", "LOAD_CASE", "INPUT", "RESULTS"}:
            raise HTTPException(422, {"code": "SEMANTIC_BINDING_ROLE_INVALID"})
        if (payload.role == "PROJECT" and (payload.request_id or payload.load_case_id)) or (payload.role == "REQUEST" and (not payload.request_id or payload.load_case_id)) or (payload.role in {"LOAD_CASE", "RESULTS"} and not payload.load_case_id):
            raise HTTPException(422, {"code": "SEMANTIC_BINDING_TARGET_MISMATCH"})
        if payload.load_case_id and not payload.recipe_ids:
            raise HTTPException(422, {"code": "SEMANTIC_RECIPE_REQUIRED"})
        if len(payload.recipe_ids) > 32 or len(set(payload.recipe_ids)) != len(payload.recipe_ids):
            raise HTTPException(422, {"code": "SEMANTIC_RECIPE_SET_INVALID"})
        try: relative_path = spdm_storage._normalise_relative(payload.relative_path)
        except spdm_storage.SpdmStorageError as error: raise _error(error) from error
        root = spdm_storage.storage_root(conn)
        if root.root is None: raise HTTPException(409, {"code": "SPDM_ROOT_UNSET"})
        target_path = root.root.joinpath(*relative_path.split("/")); spdm_storage._assert_safe_existing(target_path, root.root)
        if not target_path.is_dir(): raise HTTPException(422, {"code": "SPDM_PATH_MISSING"})
        path_key = relative_path.casefold()
        legacy = [str(row[0]).casefold() for row in conn.execute("SELECT relative_path FROM spdm_storage_bindings").fetchall()]
        existing = [dict(row) for row in rows(conn.execute("SELECT b.id, b.project_id, coalesce(b.request_id, lc.request_id) AS request_id, b.load_case_id, b.relative_path FROM semantic_folder_bindings b LEFT JOIN load_cases lc ON lc.id=b.load_case_id"))]
        if any(path_key == path or path_key.startswith(path + "/") or path.startswith(path_key + "/") for path in legacy):
            raise HTTPException(409, {"code": "SEMANTIC_LEGACY_PATH_OWNED"})
        for prior in existing:
            if prior["id"] == payload.id: continue
            path = str(prior["relative_path"]).casefold()
            overlaps = path_key == path or path_key.startswith(path + "/") or path.startswith(path_key + "/")
            if not overlaps: continue
            current_target = payload.model_dump()
            parent, child = (prior, current_target) if path_key.startswith(path + "/") else (current_target, prior)
            rank = lambda target: 2 if target["load_case_id"] else 1 if target["request_id"] else 0
            compatible = str(prior["project_id"]) == payload.project_id and path_key != path and rank(parent) < rank(child) and (not parent["request_id"] or parent["request_id"] == child["request_id"])
            if not compatible:
                raise HTTPException(409, {"code": "SEMANTIC_BINDING_OVERLAP"})
        for recipe_id in payload.recipe_ids: _version(conn, "recipe", recipe_id)
        if payload.template_id: _version(conn, "template", payload.template_id)
        now = _now()
        if payload.id:
            suffix = " FOR UPDATE" if getattr(conn, "backend", "duckdb") == "postgresql" else ""
            current = conn.execute("SELECT revision FROM semantic_folder_bindings WHERE id=?" + suffix, [payload.id]).fetchone()
            if not current: raise HTTPException(404, {"code": "SEMANTIC_BINDING_NOT_FOUND"})
            if payload.expected_revision != int(current[0]): raise HTTPException(409, {"code": "SEMANTIC_BINDING_REVISION_CONFLICT", "current_revision": int(current[0])})
            revision = int(current[0]) + 1
            conn.execute("UPDATE semantic_folder_bindings SET project_id=?, request_id=?, load_case_id=?, relative_path=?, role=?, recipe_ids_json=?, template_id=?, updated_at=?, created_by=?, revision=? WHERE id=? AND revision=?", [payload.project_id, payload.request_id, payload.load_case_id, relative_path, payload.role, json.dumps(payload.recipe_ids), payload.template_id, now, request.state.principal.user_id, revision, payload.id, int(current[0])])
            binding_id = payload.id
        else:
            binding_id = f"semantic-binding-{uuid4().hex[:12]}"; revision = 1
            conn.execute("INSERT INTO semantic_folder_bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [binding_id, payload.project_id, payload.request_id, payload.load_case_id, relative_path, payload.role, json.dumps(payload.recipe_ids), payload.template_id, now, now, request.state.principal.user_id, revision])
        return {**payload.model_dump(), "id": binding_id, "relative_path": relative_path, "revision": revision}


@router.put("/bindings/{binding_id}")
def reconnect_binding(binding_id: str, payload: BindingSave, request: Request) -> dict[str, Any]:
    if payload.id not in (None, binding_id): raise HTTPException(422, {"code": "SEMANTIC_BINDING_ID_MISMATCH"})
    return save_binding(payload.model_copy(update={"id": binding_id}), request)


def _import(conn: Any, request: Request, filename: str, content: bytes, recipe_id: str, load_case_id: str, template_id: str | None) -> dict[str, Any]:
    require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)
    recipe_version, recipe, recipe_items = _version(conn, "recipe", recipe_id); template_version, template, template_items = _version(conn, "template", template_id) if template_id else (None, {"widgets": []}, recipe_items)
    target = SQLResultIngestionQuery(conn).get_result_ingestion_target(load_case_id)
    if target is None: raise HTTPException(404, {"code": "LOAD_CASE_NOT_FOUND"})
    try: parsed = preview_recipe(recipe, recipe_items, filename, content)
    except Exception as error: raise _error(error) from error
    try: widgets = resolve_widgets(template, template_items, parsed) if template_id else []
    except Exception as error: raise _error(error) from error
    if any(widget.get("status") != "READY" for widget in widgets):
        raise HTTPException(422, {"code": "SEMANTIC_WIDGET_INPUT_INVALID", "widgets": widgets})
    digest = hashlib.sha256(content).hexdigest(); now = _now()
    command = {"project_id": target.project_id, "request_id": target.request_id, "load_case_id": load_case_id, "source_type": "SEMANTIC_RECIPE", "source_name": f"semantic/{recipe_id}/{recipe_version}/{filename}", "source_checksum": digest, "source_run_id": f"semantic:{recipe_id}:{recipe_version}:{digest}", "conflict_policy": "SKIP", "parser_version": "semantic-mapping-v1", "parsed": parsed, "actor": request.state.principal.display_name, "metadata": {"recipe_id": recipe_id, "recipe_version": recipe_version, "template_id": template_id, "template_version": template_version, "observations": parsed.get("observations", []), "source_filename": filename}}
    outcome, run_id = persist_semantic_import(conn, command, recipe_id=recipe_id, recipe_version=recipe_version, template_id=template_id, template_version=template_version, filename=filename, source_bytes=content, authorize=lambda c, tx: require_resource_permission(request, RESULT_IMPORT, "load_case", c["load_case_id"], conn=tx), now=_now)
    if outcome["status"] == "SKIPPED" and run_id:
        stored = conn.execute("SELECT template_id, template_version FROM semantic_import_provenance WHERE analysis_run_id=?", [run_id]).fetchone()
        if stored and stored[0] is not None:
            template_id, template_version = str(stored[0]), int(stored[1]) if stored[1] is not None else None
            if template_version is not None:
                template, template_items = _version_at(conn, "template", template_id, template_version)
                widgets = resolve_widgets(template, template_items, parsed)
        else:
            template_version, widgets = None, []
    return {"status": outcome["status"], "run_id": run_id, "summary": parsed["summary"], "parsed": parsed, "widgets": widgets, "recipe_version": recipe_version, "template_version": template_version, "operation": outcome["operation"], "reason_code": outcome["reason_code"]}


@router.post("/import")
async def import_file(request: Request) -> dict[str, Any]:
    fields, filename, content = await _multipart(request)
    try:
        recipe_id, load_case_id = fields["recipe_id"], fields["load_case_id"]
    except KeyError as error:
        raise HTTPException(422, {"code": "SEMANTIC_IMPORT_CONTEXT_REQUIRED"}) from error
    with connect() as conn: return _import(conn, request, filename, content, recipe_id, load_case_id, fields.get("template_id"))


@router.post("/bindings/{binding_id}/refresh")
def refresh_binding(binding_id: str, request: Request) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM semantic_folder_bindings WHERE id=?", [binding_id]).fetchone()
        if not row: raise HTTPException(404, {"code": "SEMANTIC_BINDING_NOT_FOUND"})
        binding = dict(zip([c[0] for c in conn.execute("SELECT * FROM semantic_folder_bindings WHERE id=?", [binding_id]).description], row))
        if not binding["load_case_id"]: raise HTTPException(422, {"code": "SEMANTIC_BINDING_LOAD_CASE_REQUIRED"})
        require_resource_permission(request, RESULT_IMPORT, "load_case", str(binding["load_case_id"]), conn=conn)
        root = spdm_storage.storage_root(conn)
        if root.root is None: raise HTTPException(409, {"code": "SPDM_ROOT_UNSET"})
        directory = root.root.joinpath(*str(binding["relative_path"]).split("/")); spdm_storage._assert_safe_existing(directory, root.root)
        if not directory.is_dir(): raise HTTPException(422, {"code": "SPDM_PATH_MISSING"})
        recipes = []
        for recipe_id in _json(binding["recipe_ids_json"]):
            version, definition, snapshot = _version(conn, kind="recipe", ident=recipe_id)
            recipes.append((recipe_id, version, definition, snapshot))
        results=[]; total_bytes = 0
        paths=[]
        for entry_count, path in enumerate(directory.iterdir(), 1):
            if entry_count > 5000:
                raise HTTPException(422, {"code": "SEMANTIC_REFRESH_ENTRY_LIMIT"})
            if path.is_file() and path.suffix.lower() in {".csv", ".json"} and not spdm_storage._is_reparse(path):
                paths.append(path)
                if len(paths) > 500: raise HTTPException(422, {"code": "SEMANTIC_REFRESH_FILE_LIMIT"})
        for path in sorted(paths, key=lambda p: p.name.casefold()):
            try:
                content, _ = spdm_storage.read_stable_bytes(path, max_bytes=5 * 1024 * 1024)
                total_bytes += len(content)
                if total_bytes > 128 * 1024 * 1024: raise spdm_storage.SpdmStorageError("SEMANTIC_REFRESH_SIZE_LIMIT", "새로고침 파일 총량이 한도를 초과했습니다.")
                candidates = []
                for recipe_id, _, definition, snapshot in recipes:
                    try: preview_recipe(definition, snapshot, path.name, content)
                    except SemanticValidationError: continue
                    candidates.append(recipe_id)
                if len(candidates) != 1:
                    results.append({"relative_path": path.name, "status": "UNMAPPED" if not candidates else "AMBIGUOUS", "recipe_ids": candidates})
                    continue
                results.append(_import(conn, request, path.name, content, candidates[0], str(binding["load_case_id"]), binding["template_id"]))
            except spdm_storage.SpdmStorageError as error:
                results.append({"relative_path": path.name, "status": "PENDING", "code": error.code})
            except HTTPException as error:
                if error.status_code not in {409, 422}:
                    raise
                results.append({"relative_path": path.name, "status": "INVALID", "detail": error.detail})
        return {"binding_id": binding_id, "results": results, "partial": any(item.get("status") not in {"IMPORTED", "SKIPPED"} for item in results)}


@router.get("/results")
def results(load_case_id: str, request: Request, run_id: str | None = None, template_id: str | None = None) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "load_case", load_case_id, conn=conn)
        query = "SELECT * FROM semantic_import_provenance WHERE load_case_id=?"; args: list[Any] = [load_case_id]
        if run_id: query += " AND analysis_run_id=?"; args.append(run_id)
        query += " ORDER BY created_at DESC LIMIT 1"; row = conn.execute(query, args).fetchone()
        if not row: return {"run_id": run_id, "widgets": []}
        columns = [column[0] for column in conn.execute(query, args).description]; provenance = dict(zip(columns, row))
        selected = template_id or provenance["template_id"]
        if selected and template_id is None and provenance["template_version"] is not None:
            template, snapshot = _version_at(conn, "template", str(selected), int(provenance["template_version"]))
            response_template_version = provenance["template_version"]
        else:
            response_template_version, template, snapshot = _version(conn, "template", str(selected)) if selected else (None, {"widgets": []}, [])
        parsed = {"observations": _json(provenance["observations_json"]), "scalars": [], "curves": [], "media": [], "summary": {}, "warnings": [], "note": ""}
        return {"run_id": provenance["analysis_run_id"], "template_version": response_template_version, "widgets": resolve_widgets(template, snapshot, parsed) if selected else []}


@router.get("/export")
def export_definitions(request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        catalog = _catalog(conn); return {"format_version": 1, "items": catalog["items"], "recipes": catalog["recipes"], "templates": catalog["templates"]}


@router.post("/import-definitions", status_code=201)
def import_definitions(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        if payload.get("format_version") != 1 or any(not isinstance(payload.get(key, []), list) or len(payload.get(key, [])) > 64 for key in ("items", "recipes", "templates")):
            raise HTTPException(422, {"code": "SEMANTIC_DEFINITION_PACKAGE_INVALID"})
        item_entries = payload.get("items", [])
        remap: dict[str, str] = {}
        normalized_items=[]; imported_keys: set[str] = set()
        try:
            for entry in item_entries:
                definition = dict(entry.get("definition", entry)); old = str(entry.get("id") or definition.get("id") or "")
                if not old or old in remap: raise SemanticValidationError("ITEM_INVALID", "반입 항목 ID가 없거나 중복됩니다.")
                if conn.execute("SELECT 1 FROM semantic_result_items WHERE key=?", [definition.get("key")]).fetchone(): raise SemanticValidationError("ITEM_KEY_CONFLICT", "같은 결과 변수 키가 이미 존재합니다.")
                if definition.get("key") in imported_keys: raise SemanticValidationError("ITEM_KEY_CONFLICT", "반입 패키지에 같은 결과 변수 키가 중복됩니다.")
                imported_keys.add(definition.get("key"))
                new = f"semantic-item-{uuid4().hex[:12]}"; remap[old] = new; definition["id"] = new
                normalized_items.append(validate_item(definition))
            normalized_recipes=[]; normalized_templates=[]
            for kind, target in (("recipe", normalized_recipes), ("template", normalized_templates)):
                for entry in payload.get(kind + "s", []):
                    definition = json.loads(json.dumps(entry.get("definition", entry)))
                    if kind == "recipe":
                        for mapping in definition.get("mappings", []): mapping["result_item_id"] = remap.get(mapping.get("result_item_id"), mapping.get("result_item_id"))
                        validate_recipe(definition, normalized_items)
                    else:
                        for widget in definition.get("widgets", []):
                            widget["item_ids"] = [remap.get(item, item) for item in widget.get("item_ids", [])]
                            for field in ("x_item_id", "y_item_id"):
                                if widget.get(field): widget[field] = remap.get(widget[field], widget[field])
                        validate_template(definition, normalized_items)
                    target.append((str(entry.get("name") or definition.get("label") or kind), definition))
        except Exception as error:
            raise _error(error) from error
        conn.execute("BEGIN TRANSACTION")
        try:
            created=[]
            for definition in normalized_items: created.append(_save_version(conn, "item", definition["id"], definition["label"], definition, None, request.state.principal.user_id))
            for kind, entries in (("recipe", normalized_recipes), ("template", normalized_templates)):
                for name, definition in entries: created.append(_save_version(conn, kind, None, name, definition, None, request.state.principal.user_id))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK"); raise
        return {"status": "DRAFT_IMPORTED", "created": created, "item_id_remap": remap}
