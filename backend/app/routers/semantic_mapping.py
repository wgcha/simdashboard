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

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from tempfile import SpooledTemporaryFile
from pydantic import BaseModel, Field

from ..adapters.persistence.result_ingestion import SQLResultIngestionQuery
from ..database_connection import connect
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
from ..repositories import semantic_mapping as mapping_repository
from ..security import write_audit_event
from ..services import spdm_storage, semantic_sample_uploads as sample_uploads
from ..services import semantic_result_refresh
from ..services.semantic_mapping import persist_semantic_import, semantic_source_run_id, semantic_transaction
from .semantic_body_limit import SemanticBodyLimitRoute
from .semantic_review import record_unresolved_refresh


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
    sample_upload_id: str | None = Field(default=None, min_length=32, max_length=32)
    sample_filename: str | None = None
    sample_content_base64: str | None = Field(default=None, max_length=349528)


class ConfigurationSave(BaseModel):
    recipe: DefinitionSave
    template: DefinitionSave


class ItemLifecycle(BaseModel):
    expected_version: int = Field(ge=1)


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
    catalog = mapping_repository.catalog(conn)
    for kind in ("items", "recipes", "templates"):
        for record in catalog[kind]:
            record["definition"] = _json(record.pop("definition_json"))
    # The catalog definition is the editable latest revision.  Folder matching
    # executes the active revision, so expose its format separately instead of
    # letting a draft make an active CSV rule look like a different format.
    for recipe in catalog["recipes"]:
        active_version = recipe.get("active_version")
        if active_version is None:
            recipe["active_format"] = None
            continue
        definition, _snapshot = _version_at(conn, "recipe", str(recipe["id"]), int(active_version))
        recipe["active_format"] = definition.get("format") if isinstance(definition.get("format"), str) else None
    catalog["bindings"] = [{**entry, "recipe_ids": _json(entry["recipe_ids_json"])} for entry in catalog["bindings"]]
    return catalog


def _items(conn: Any) -> list[dict[str, Any]]:
    return [entry["definition"] | {"id": entry["id"], "version": entry["latest_version"]} for entry in _catalog(conn)["items"] if entry["lifecycle_status"] != "ARCHIVED"]


def _version(conn: Any, kind: str, ident: str, active: bool = True) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    row = mapping_repository.version(conn, kind, ident, active=active)
    if not row or row[0] is None:
        raise HTTPException(409, {"code": f"SEMANTIC_{kind.upper()}_NOT_ACTIVE", "message": "활성 정의가 없습니다."})
    return int(row[0]), _json(row[1]), _json(row[2])


def _version_at(conn: Any, kind: str, ident: str, version: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row = mapping_repository.version_at(conn, kind, ident, version)
    if not row: raise HTTPException(409, {"code": "SEMANTIC_VERSION_NOT_FOUND"})
    return _json(row[0]), _json(row[1])


def _display_template(
    conn: Any, recipe: dict[str, Any], recipe_items: list[dict[str, Any]], requested_template_id: str | None,
) -> tuple[str | None, int | None, dict[str, Any], list[dict[str, Any]]]:
    """Resolve an explicit binding override or the recipe's immutable display link."""
    if requested_template_id:
        version, definition, items = _version(conn, "template", requested_template_id)
        return requested_template_id, version, definition, items
    linked_id = recipe.get("display_template_id")
    linked_version = recipe.get("display_template_version")
    if linked_id is None and linked_version is None:
        return None, None, {"widgets": []}, recipe_items
    if not isinstance(linked_id, str) or not linked_id or type(linked_version) is not int or linked_version < 1:
        raise HTTPException(409, {"code": "SEMANTIC_TEMPLATE_LINK_INVALID"})
    row = mapping_repository.published_version_at(conn, "template", linked_id, linked_version)
    if not row:
        raise HTTPException(409, {"code": "SEMANTIC_TEMPLATE_LINK_INVALID"})
    definition, items = _json(row[0]), _json(row[1])
    return linked_id, linked_version, definition, items


def _widget_metadata(template_id: str | None, widgets: list[dict[str, Any]]) -> dict[str, Any]:
    if widgets:
        return {"review_available": True}
    return {
        "review_available": False,
        "clear_reason": "NO_DISPLAY_TEMPLATE" if template_id is None else "TEMPLATE_HAS_NO_WIDGETS",
    }


def _confirmed_run_widget_metadata(conn: Any, load_case_id: str, run_id: str | None) -> dict[str, Any]:
    """Describe widgets from immutable provenance, never a later binding rule."""
    if not run_id:
        return {"review_available": False, "clear_reason": "NO_CONFIRMED_RUN"}
    provenance = mapping_repository.latest_provenance(conn, load_case_id, run_id)
    if not provenance or not provenance.get("template_id") or provenance.get("template_version") is None:
        return {"review_available": False, "clear_reason": "NO_DISPLAY_TEMPLATE"}
    template, items = _version_at(conn, "template", str(provenance["template_id"]), int(provenance["template_version"]))
    payload = {"observations": _json(provenance["observations_json"])}
    return _widget_metadata(str(provenance["template_id"]), resolve_widgets(template, items, payload))


def _save_version(conn: Any, kind: str, ident: str | None, name: str, definition: dict[str, Any], expected: int | None, actor: str) -> dict[str, Any]:
    ident = ident or f"semantic-{kind}-{uuid4().hex[:12]}"
    now = _now()
    snapshot = [] if kind == "item" else _items(conn)
    conflict, version = mapping_repository.save_version(conn, kind=kind, ident=ident, name=name, definition=definition, expected=expected, actor=actor, now=now, item_snapshot=snapshot)
    if version == 0:
        raise HTTPException(409, {"code": "SEMANTIC_REVISION_CONFLICT", "current_version": conflict})
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
        mapping_repository.lock_item(conn, item_id)
        key_owner = mapping_repository.item_key_owner(conn, definition["key"])
        if key_owner and str(key_owner[0]) != item_id:
            raise HTTPException(409, {"code": "SEMANTIC_ITEM_KEY_CONFLICT"})
        previous = mapping_repository.previous_item_definition(conn, item_id)
        if previous:
            old = _json(previous[0])
            changed = any(old.get(field) != definition.get(field) for field in ("key", "kind", "data_type", "unit", "dimensions", "components"))
            if changed:
                usage = mapping_repository.item_usage(conn, item_id)
                if usage["recipes"] or usage["templates"]:
                    raise HTTPException(422, {"code": "SEMANTIC_ITEM_MEANING_IMMUTABLE", "message": "사용 중인 결과 항목의 의미는 변경할 수 없습니다."})
        result = _save_version(conn, "item", item_id, str(definition["label"]), definition, payload.expected_version, request.state.principal.user_id)
        write_audit_event(request=request, principal=request.state.principal, status_code=201, action="SEMANTIC_ITEM_SAVED", detail={"item_id": result["id"], "version": result["version"]}, connection=conn)
        return result


@router.post("/items/{item_id}/archive")
def archive_item(item_id: str, payload: ItemLifecycle, request: Request) -> dict[str, Any]:
    with connect() as conn, semantic_transaction(conn):
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        version = mapping_repository.save_item_lifecycle(conn, item_id, payload.expected_version, "ARCHIVED", request.state.principal.user_id, _now())
        if version is None: raise HTTPException(409, {"code": "SEMANTIC_REVISION_CONFLICT"})
        write_audit_event(request=request, principal=request.state.principal, status_code=200, action="SEMANTIC_ITEM_ARCHIVED", detail={"item_id": item_id, "version": version}, connection=conn)
        return {"id": item_id, "version": version, "lifecycle_status": "ARCHIVED"}


@router.post("/items/{item_id}/restore")
def restore_item(item_id: str, payload: ItemLifecycle, request: Request) -> dict[str, Any]:
    with connect() as conn, semantic_transaction(conn):
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        version = mapping_repository.save_item_lifecycle(conn, item_id, payload.expected_version, "DRAFT", request.state.principal.user_id, _now())
        if version is None: raise HTTPException(409, {"code": "SEMANTIC_REVISION_CONFLICT"})
        write_audit_event(request=request, principal=request.state.principal, status_code=200, action="SEMANTIC_ITEM_RESTORED", detail={"item_id": item_id, "version": version}, connection=conn)
        return {"id": item_id, "version": version, "lifecycle_status": "DRAFT"}


@router.get("/items/{item_id}/usage")
def get_item_usage(item_id: str, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        return {"id": item_id, **mapping_repository.item_usage(conn, item_id)}


def _save_definition(kind: str, payload: DefinitionSave, request: Request) -> dict[str, Any]:
    with connect() as conn, mapping_repository.definition_transaction(conn):
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        try:
            mapping_repository.lock_items_for_definition_save(conn)
            definitions = _items(conn)
            (validate_recipe if kind == "recipe" else validate_template)(payload.definition, definitions)
            sample_filename = payload.sample_filename
            if payload.sample_upload_id and payload.sample_content_base64 is not None:
                raise HTTPException(422, {"code": "SEMANTIC_SAMPLE_AMBIGUOUS"})
            if kind == "recipe" and payload.sample_upload_id:
                sample_filename, sample = sample_uploads.load(request.state.principal.user_id, payload.sample_upload_id)
                preview_recipe(payload.definition, definitions, sample_filename, sample)
            elif kind == "recipe" and payload.sample_content_base64 is not None:
                try: sample = base64.b64decode(payload.sample_content_base64, validate=True)
                except ValueError as error: raise HTTPException(422, {"code": "SEMANTIC_SAMPLE_INVALID"}) from error
                if not payload.sample_filename or not sample or len(sample) > _SAMPLE_LIMIT: raise HTTPException(422, {"code": "SEMANTIC_SAMPLE_INVALID"})
                preview_recipe(payload.definition, definitions, payload.sample_filename, sample)
            else: sample = None
            result = _save_version(conn, kind, payload.id, payload.name, payload.definition, payload.expected_version, request.state.principal.user_id)
            if sample is not None:
                mapping_repository.store_recipe_sample(conn, filename=sample_filename, digest=hashlib.sha256(sample).hexdigest(), content=sample, recipe_id=result["id"], version_number=result["version"])
            write_audit_event(request=request, principal=request.state.principal, status_code=201, action=f"SEMANTIC_{kind.upper()}_SAVED", detail={f"{kind}_id": result["id"], "version": result["version"]}, connection=conn)
            return result
        except BaseException as error:
            if isinstance(error, SemanticValidationError):
                raise _error(error) from error
            raise


@router.post("/recipes", status_code=201)
def save_recipe(payload: DefinitionSave, request: Request) -> dict[str, Any]: return _save_definition("recipe", payload, request)


@router.post("/templates", status_code=201)
def save_template(payload: DefinitionSave, request: Request) -> dict[str, Any]: return _save_definition("template", payload, request)


@router.post("/configurations", status_code=201)
def save_configuration(payload: ConfigurationSave, request: Request) -> dict[str, Any]:
    """Save a template and its linked recipe in one rollback boundary."""
    try:
        with connect() as conn, mapping_repository.definition_transaction(conn):
            require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
            mapping_repository.lock_items_for_definition_save(conn)
            mapping_repository.lock_configuration(conn, payload.recipe.id, payload.template.id)
            definitions = _items(conn)
            validate_template(payload.template.definition, definitions)
            template = _save_version(conn, "template", payload.template.id, payload.template.name, payload.template.definition, payload.template.expected_version, request.state.principal.user_id)
            recipe_definition = dict(payload.recipe.definition)
            recipe_definition["display_template_id"] = template["id"]
            recipe_definition["display_template_version"] = template["version"]
            validate_recipe(recipe_definition, definitions)
            if payload.recipe.sample_upload_id and payload.recipe.sample_content_base64 is not None:
                raise HTTPException(422, {"code": "SEMANTIC_SAMPLE_AMBIGUOUS"})
            if payload.recipe.sample_upload_id:
                filename, content = sample_uploads.load(request.state.principal.user_id, payload.recipe.sample_upload_id)
                parsed = preview_recipe(recipe_definition, definitions, filename, content)
            elif payload.recipe.sample_content_base64 is not None:
                try: content = base64.b64decode(payload.recipe.sample_content_base64, validate=True)
                except ValueError as error: raise HTTPException(422, {"code": "SEMANTIC_SAMPLE_INVALID"}) from error
                if not payload.recipe.sample_filename or not content or len(content) > _SAMPLE_LIMIT: raise HTTPException(422, {"code": "SEMANTIC_SAMPLE_INVALID"})
                parsed = preview_recipe(recipe_definition, definitions, payload.recipe.sample_filename, content)
                filename = payload.recipe.sample_filename
            else:
                prior = mapping_repository.previous_recipe_sample(conn, payload.recipe.id) if payload.recipe.id else None
                if prior:
                    filename, digest, content = str(prior[0]), str(prior[1]), bytes(prior[2])
                    parsed = preview_recipe(recipe_definition, definitions, filename, content)
                else:
                    content = None
            if content is not None and any(widget.get("status") not in {"READY", "NO_VALUE"} for widget in resolve_widgets(payload.template.definition, definitions, parsed)):
                raise HTTPException(422, {"code": "SEMANTIC_WIDGET_NOT_READY"})
            recipe = _save_version(conn, "recipe", payload.recipe.id, payload.recipe.name, recipe_definition, payload.recipe.expected_version, request.state.principal.user_id)
            if content is not None:
                mapping_repository.store_recipe_sample(conn, filename=filename, digest=hashlib.sha256(content).hexdigest(), content=content, recipe_id=recipe["id"], version_number=recipe["version"])
            write_audit_event(request=request, principal=request.state.principal, status_code=201, action="SEMANTIC_CONFIGURATION_SAVED", detail={"recipe_id": recipe["id"], "template_id": template["id"]}, connection=conn)
            return {"recipe": recipe, "template": template}
    except SemanticValidationError as error:
        raise _error(error) from error


@router.get("/configurations/{recipe_id}")
def get_configuration(recipe_id: str, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        version, definition, snapshot = _version(conn, "recipe", recipe_id, active=False)
        base = mapping_repository.definition_metadata(conn, "recipe", recipe_id)
        recipe = {"id": recipe_id, "name": str(base[0]), "version": version, "active_version": base[1], "definition": definition, "item_snapshot": snapshot}
        template_id, template_version = definition.get("display_template_id"), definition.get("display_template_version")
        if template_id is None and template_version is None: return {"recipe": recipe, "template": None}
        if type(template_version) is not int or template_version < 1 or not isinstance(template_id, str) or not template_id: raise HTTPException(409, {"code": "SEMANTIC_TEMPLATE_LINK_INVALID"})
        template_definition, template_snapshot = _version_at(conn, "template", template_id, template_version)
        template_base = mapping_repository.definition_metadata(conn, "template", template_id)
        if not template_base: raise HTTPException(409, {"code": "SEMANTIC_TEMPLATE_LINK_INVALID"})
        return {"recipe": recipe, "template": {"id": template_id, "name": str(template_base[0]), "version": template_version, "active_version": template_base[1], "definition": template_definition, "item_snapshot": template_snapshot}}


def _activate(kind: str, ident: str, payload: Activate, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
    raise HTTPException(410, {"code": "SEMANTIC_ACTIVATION_BUNDLE_REQUIRED", "message": "레시피와 템플릿을 함께 검증·활성화하세요."})


@router.post("/recipes/{recipe_id}/activate")
def activate_recipe(recipe_id: str, payload: Activate, request: Request) -> dict[str, Any]: return _activate("recipe", recipe_id, payload, request)


@router.post("/templates/{template_id}/activate")
def activate_template(template_id: str, payload: Activate, request: Request) -> dict[str, Any]: return _activate("template", template_id, payload, request)


async def _multipart(request: Request, *, allow_reference: bool = False) -> tuple[dict[str, str], str, bytes]:
    """Spool a bounded browser multipart request before MIME decoding.

    One file or one owned upload reference is accepted. The existing stdlib
    parser preserves the offline dependency contract.
    """
    content_type = request.headers.get("content-type", "")
    marker = "boundary="
    if "multipart/form-data" not in content_type or marker not in content_type:
        raise HTTPException(415, {"code": "SEMANTIC_MULTIPART_REQUIRED"})
    boundary = content_type.split(marker, 1)[1].strip().strip('"').encode("ascii", "strict")
    if not boundary or len(boundary) > 200:
        raise HTTPException(422, {"code": "SEMANTIC_MULTIPART_INVALID"})
    limit = sample_uploads.MAX_SAMPLE_BYTES + 6 * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared and (not declared.isdigit() or int(declared) > limit):
        raise HTTPException(413, {"code": "SEMANTIC_SAMPLE_TOO_LARGE"})
    total = 0
    with SpooledTemporaryFile(max_size=1024 * 1024) as upload:
        upload.write((f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode("ascii"))
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise HTTPException(413, {"code": "SEMANTIC_SAMPLE_TOO_LARGE"})
            upload.write(chunk)
        upload.seek(0)
        try:
            message = await run_in_threadpool(BytesParser(policy=policy.default).parse, upload)
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
    if allow_reference and file_count == 0 and fields.get("upload_id"):
        return fields, "", b""
    if file_count != 1 or fields.get("upload_id"):
        raise HTTPException(422, {"code": "SEMANTIC_MULTIPART_INVALID"})
    if not filename or not content or len(content) > sample_uploads.MAX_SAMPLE_BYTES:
        raise HTTPException(422, {"code": "SEMANTIC_SAMPLE_INVALID"})
    return fields, filename, content


def _inspection_options(fields: dict[str, str]) -> dict:
    try:
        options = {key: int(fields[key]) for key in ("row_offset", "row_limit", "field_offset", "field_limit") if key in fields}
        if any(value < 0 for value in options.values()) or any(options.get(key, 1) < 1 or options.get(key, 1) > 500 for key in ("row_limit", "field_limit")):
            raise ValueError()
        if fields.get("overrides"):
            overrides = json.loads(fields["overrides"])
            if not isinstance(overrides, dict):
                raise ValueError()
            options["overrides"] = overrides
        return options
    except (ValueError, TypeError):
        raise HTTPException(422, {"code": "SEMANTIC_INSPECT_OPTIONS_INVALID"}) from None


@router.post("/inspect")
async def inspect(request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
    fields, filename, content = await _multipart(request)
    try:
        result = await run_in_threadpool(inspect_sample, filename, content, **_inspection_options(fields))
        upload_id = await run_in_threadpool(sample_uploads.save, request.state.principal.user_id, filename, content)
        return {**result, "upload_id": upload_id, "filename": filename, "expires_in_seconds": sample_uploads.UPLOAD_TTL_SECONDS}
    except Exception as error:
        raise _error(error) from error


@router.get("/sample-uploads/{upload_id}")
def inspect_upload(upload_id: str, request: Request, row_offset: int = Query(default=0, ge=0),
                   row_limit: int = Query(default=50, ge=1, le=500), field_offset: int = Query(default=0, ge=0),
                   field_limit: int = Query(default=100, ge=1, le=500), overrides: str | None = None):
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
    filename, content = sample_uploads.load(request.state.principal.user_id, upload_id)
    options = _inspection_options({"row_offset": str(row_offset), "row_limit": str(row_limit),
                                   "field_offset": str(field_offset), "field_limit": str(field_limit),
                                   **({"overrides": overrides} if overrides else {})})
    try:
        return {**inspect_sample(filename, content, **options), "upload_id": upload_id, "filename": filename}
    except Exception as error:
        raise _error(error) from error


@router.delete("/sample-uploads/{upload_id}")
def delete_upload(upload_id: str, request: Request):
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
    sample_uploads.delete(request.state.principal.user_id, upload_id)
    return {"status": "DELETED"}


@router.post("/preview")
async def preview(request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
    fields, filename, content = await _multipart(request, allow_reference=True)
    if fields.get("upload_id"):
        filename, content = sample_uploads.load(request.state.principal.user_id, fields["upload_id"])
    try:
        recipe_definition = json.loads(fields["recipe"]); template_definition = json.loads(fields["template"]) if fields.get("template") else None
        with connect() as conn:
            catalog_items = _items(conn)
        parsed = await run_in_threadpool(preview_recipe, recipe_definition, catalog_items, filename, content)
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
        mapping_repository.lock_binding_tables(conn)
        if payload.load_case_id: require_resource_permission(request, RESULT_IMPORT, "load_case", payload.load_case_id, conn=conn)
        if payload.request_id or payload.load_case_id:
            lineage = mapping_repository.binding_lineage(conn, payload.request_id, payload.load_case_id)
            if not lineage or str(lineage[0]) != payload.project_id or (payload.request_id and str(lineage[1]) != payload.request_id):
                raise HTTPException(422, {"code": "SEMANTIC_BINDING_TARGET_MISMATCH"})
            payload = payload.model_copy(update={"request_id": str(lineage[1])})
        if payload.role not in {"PROJECT", "REQUEST", "LOAD_CASE", "INPUT", "RESULTS"}:
            raise HTTPException(422, {"code": "SEMANTIC_BINDING_ROLE_INVALID"})
        if (payload.role == "PROJECT" and (payload.request_id or payload.load_case_id)) or (payload.role == "REQUEST" and (not payload.request_id or payload.load_case_id)) or (payload.role == "LOAD_CASE" and not payload.load_case_id):
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
        legacy = mapping_repository.legacy_binding_paths(conn)
        existing = mapping_repository.semantic_bindings(conn)
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
            current = mapping_repository.binding_revision(conn, payload.id)
            if not current: raise HTTPException(404, {"code": "SEMANTIC_BINDING_NOT_FOUND"})
            if payload.expected_revision != int(current[0]): raise HTTPException(409, {"code": "SEMANTIC_BINDING_REVISION_CONFLICT", "current_revision": int(current[0])})
            revision = int(current[0]) + 1
            mapping_repository.update_binding(conn, payload=payload, relative_path=relative_path, revision=revision, actor=request.state.principal.user_id, now=now)
            binding_id = payload.id
        else:
            binding_id = f"semantic-binding-{uuid4().hex[:12]}"; revision = 1
            mapping_repository.create_binding(conn, binding_id=binding_id, payload=payload, relative_path=relative_path, actor=request.state.principal.user_id, now=now)
        return {**payload.model_dump(), "id": binding_id, "relative_path": relative_path, "revision": revision}


@router.put("/bindings/{binding_id}")
def reconnect_binding(binding_id: str, payload: BindingSave, request: Request) -> dict[str, Any]:
    if payload.id not in (None, binding_id): raise HTTPException(422, {"code": "SEMANTIC_BINDING_ID_MISMATCH"})
    return save_binding(payload.model_copy(update={"id": binding_id}), request)


def _import(conn: Any, request: Request, filename: str, content: bytes, recipe_id: str, load_case_id: str, template_id: str | None,
            binding_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)
    recipe_version, recipe, recipe_items = _version(conn, "recipe", recipe_id)
    template_id, template_version, template, template_items = _display_template(conn, recipe, recipe_items, template_id)
    target = SQLResultIngestionQuery(conn).get_result_ingestion_target(load_case_id)
    if target is None: raise HTTPException(404, {"code": "LOAD_CASE_NOT_FOUND"})
    try: parsed = preview_recipe(recipe, recipe_items, filename, content)
    except Exception as error: raise _error(error) from error
    try: widgets = resolve_widgets(template, template_items, parsed) if template_id else []
    except Exception as error: raise _error(error) from error
    if any(widget.get("status") not in {"READY", "NO_VALUE"} for widget in widgets):
        raise HTTPException(422, {"code": "SEMANTIC_WIDGET_INPUT_INVALID", "widgets": widgets})
    digest = hashlib.sha256(content).hexdigest(); now = _now()
    reuse_legacy = template_id is not None and mapping_repository.has_legacy_provenance(
        conn, load_case_id=load_case_id, recipe_id=recipe_id, recipe_version=recipe_version,
        template_id=template_id, template_version=template_version, source_sha256=digest,
    )
    command = {"project_id": target.project_id, "request_id": target.request_id, "load_case_id": load_case_id, "source_type": "SEMANTIC_RECIPE", "source_name": f"semantic/{recipe_id}/{recipe_version}/{filename}", "source_checksum": digest, "source_run_id": semantic_source_run_id(recipe_id, recipe_version, digest, template_id, template_version, reuse_legacy=reuse_legacy), "conflict_policy": "SKIP", "parser_version": "semantic-mapping-v2" if recipe.get("reader_version") == 2 else "semantic-mapping-v1", "parsed": parsed, "actor": request.state.principal.display_name, "metadata": {"recipe_id": recipe_id, "recipe_version": recipe_version, "template_id": template_id, "template_version": template_version, "observations": parsed.get("observations", []), "source_filename": filename}}
    def authorize_import(command_context: dict[str, Any], tx: Any) -> None:
        require_resource_permission(request, RESULT_IMPORT, "load_case", command_context["load_case_id"], conn=tx)
        if binding_snapshot is not None:
            _assert_binding_snapshot(tx, binding_snapshot, lock=True)

    outcome, run_id = persist_semantic_import(conn, command, recipe_id=recipe_id, recipe_version=recipe_version, template_id=template_id, template_version=template_version, filename=filename, source_bytes=content, authorize=authorize_import, now=_now)
    if outcome["status"] == "SKIPPED" and run_id:
        stored = mapping_repository.provenance_template(conn, run_id)
        if stored and stored[0] is not None:
            template_id, template_version = str(stored[0]), int(stored[1]) if stored[1] is not None else None
            if template_version is not None:
                template, template_items = _version_at(conn, "template", template_id, template_version)
                widgets = resolve_widgets(template, template_items, parsed)
        else:
            template_id, template_version, widgets = None, None, []
    return {"status": outcome["status"], "run_id": run_id, "summary": parsed["summary"], "parsed": parsed, "widgets": widgets,
            "recipe_id": recipe_id, "recipe_version": recipe_version, "template_id": template_id, "template_version": template_version,
            "operation": outcome["operation"], "reason_code": outcome["reason_code"], **_widget_metadata(template_id, widgets)}


@router.post("/import")
async def import_file(request: Request) -> dict[str, Any]:
    fields, filename, content = await _multipart(request)
    try:
        recipe_id, load_case_id = fields["recipe_id"], fields["load_case_id"]
    except KeyError as error:
        raise HTTPException(422, {"code": "SEMANTIC_IMPORT_CONTEXT_REQUIRED"}) from error
    with connect() as conn: return _import(conn, request, filename, content, recipe_id, load_case_id, fields.get("template_id"))


def _refresh_binding_snapshot(binding: dict[str, Any], request: Request) -> dict[str, Any]:
    """Refresh one immutable binding snapshot; never follow a later reconnect."""
    with connect() as conn:
        current = mapping_repository.binding(conn, str(binding["id"]))
        if current is None: raise HTTPException(404, {"code": "SEMANTIC_BINDING_NOT_FOUND"})
        if not _binding_snapshot_matches(current, binding):
            raise HTTPException(409, {"code": "SEMANTIC_BINDING_STALE"})
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
            if path.is_file() and path.suffix.lower() in {".csv", ".json", ".tsv", ".txt"} and not spdm_storage._is_reparse(path):
                paths.append(path)
                if len(paths) > 500: raise HTTPException(422, {"code": "SEMANTIC_REFRESH_FILE_LIMIT"})
        for path in sorted(paths, key=lambda p: p.name.casefold()):
            content: bytes | None = None
            try:
                content, _ = spdm_storage.read_stable_bytes(path, max_bytes=sample_uploads.MAX_SAMPLE_BYTES)
                total_bytes += len(content)
                if total_bytes > 128 * 1024 * 1024: raise spdm_storage.SpdmStorageError("SEMANTIC_REFRESH_SIZE_LIMIT", "새로고침 파일 총량이 한도를 초과했습니다.")
                candidates = []
                candidate_errors = []
                for recipe_id, recipe_version, definition, snapshot in recipes:
                    try:
                        preview_recipe(definition, snapshot, path.name, content)
                    except SemanticValidationError as error:
                        # The match decision must remain strict, but callers
                        # need the exact per-recipe reason to correct a saved
                        # folder rule instead of seeing an opaque UNMAPPED.
                        candidate_errors.append({"recipe_id": recipe_id, "recipe_version": recipe_version, "code": error.code, "message": error.message})
                        continue
                    candidates.append({"recipe_id": recipe_id, "recipe_version": recipe_version})
                if len(candidates) != 1:
                    status = "UNMAPPED" if not candidates else "AMBIGUOUS"
                    diagnostic = {"code": "SEMANTIC_RECIPE_NO_MATCH", "candidate_errors": candidate_errors} if not candidates else {"code": "SEMANTIC_RECIPE_AMBIGUOUS"}
                    review = record_unresolved_refresh(conn, binding, relative_path=path.name, scan_status=status, candidates=candidates, source_sha256=hashlib.sha256(content).hexdigest(), source_size=len(content), error=diagnostic, actor=request.state.principal.user_id)
                    results.append({"relative_path": path.name, "status": status, "recipe_ids": [candidate["recipe_id"] for candidate in candidates], "candidate_errors": candidate_errors, "review_item_id": review["id"], "review_available": False, "clear_reason": "NO_RENDERABLE_WIDGETS"})
                    continue
                # A previously unresolved file must still satisfy the pinned
                # recipe/template display contract before it is shown as
                # pending.  Otherwise a recurring widget incompatibility
                # loses its INVALID reason merely because its parser matched.
                matched_id = candidates[0]["recipe_id"]
                matched = next(entry for entry in recipes if entry[0] == matched_id)
                parsed = preview_recipe(matched[2], matched[3], path.name, content)
                _template_id, _template_version, template, template_items = _display_template(conn, matched[2], matched[3], binding.get("template_id"))
                widgets = resolve_widgets(template, template_items, parsed) if _template_id else []
                if any(widget.get("status") not in {"READY", "NO_VALUE"} for widget in widgets):
                    raise HTTPException(422, {"code": "SEMANTIC_WIDGET_INPUT_INVALID", "widgets": widgets})
                prior_review = mapping_repository.review_exists(conn, binding["id"], binding["load_case_id"], path.name)
                if prior_review:
                    review = record_unresolved_refresh(conn, binding, relative_path=path.name, scan_status="PENDING", candidates=candidates, source_sha256=hashlib.sha256(content).hexdigest(), source_size=len(content), error=None, actor=request.state.principal.user_id)
                    if review["review_state"] in {"IMPORTED", "SKIPPED"}:
                        run_id = review.get("confirmed_analysis_run_id")
                        results.append({"relative_path": path.name, "status": review["review_state"], "run_id": run_id, "review_item_id": review["id"], **_confirmed_run_widget_metadata(conn, str(binding["load_case_id"]), run_id)})
                    else:
                        results.append({"relative_path": path.name, "status": "PENDING", "recipe_ids": [candidates[0]["recipe_id"]], "review_item_id": review["id"], "review_available": False, "clear_reason": "PENDING_REVIEW"})
                    continue
                _assert_binding_snapshot(conn, binding)
                results.append({"relative_path": path.name, **_import(conn, request, path.name, content, candidates[0]["recipe_id"], str(binding["load_case_id"]), binding["template_id"], binding)})
            except spdm_storage.SpdmStorageError as error:
                review = record_unresolved_refresh(conn, binding, relative_path=path.name, scan_status="PENDING", candidates=[], source_sha256=None, source_size=None, error={"code": error.code}, actor=request.state.principal.user_id)
                if review["review_state"] in {"IMPORTED", "SKIPPED"}:
                    run_id = review.get("confirmed_analysis_run_id")
                    results.append({"relative_path": path.name, "status": review["review_state"], "run_id": run_id, "review_item_id": review["id"], **_confirmed_run_widget_metadata(conn, str(binding["load_case_id"]), run_id)})
                else:
                    results.append({"relative_path": path.name, "status": "PENDING", "code": error.code, "review_item_id": review["id"], "review_available": False, "clear_reason": "PENDING_REVIEW"})
            except HTTPException as error:
                detail_code = error.detail.get("code") if isinstance(error.detail, dict) else None
                # The reviewed binding was reconnected after this refresh took
                # its snapshot.  Do not turn that stale scan into an INVALID
                # item against the newly bound target.
                if error.status_code == 409 and detail_code in {"SEMANTIC_BINDING_STALE", "SEMANTIC_REVIEW_BINDING_STALE", "SEMANTIC_REVIEW_TARGET_CHANGED"}:
                    raise
                if error.status_code not in {409, 422}:
                    raise
                review = record_unresolved_refresh(conn, binding, relative_path=path.name, scan_status="INVALID", candidates=candidates, source_sha256=hashlib.sha256(content).hexdigest() if content is not None else None, source_size=len(content) if content is not None else None, error={"detail": error.detail}, actor=request.state.principal.user_id)
                results.append({"relative_path": path.name, "status": "INVALID", "detail": error.detail, "review_item_id": review["id"]})
        return {"binding_id": binding["id"], "results": results, "partial": any(item.get("status") not in {"IMPORTED", "SKIPPED"} for item in results)}


def _binding_snapshot_matches(current: dict[str, Any] | None, snapshot: dict[str, Any]) -> bool:
    fields = ("role", "project_id", "request_id", "load_case_id", "revision", "relative_path")
    return bool(current and all(str(current.get(field) or "") == str(snapshot.get(field) or "") for field in fields))


def _assert_binding_snapshot(conn: Any, snapshot: dict[str, Any], *, lock: bool = False) -> None:
    """Verify the binding again inside an import transaction when requested."""
    if lock:
        locked = mapping_repository.binding_revision(conn, str(snapshot["id"]))
        if not locked or int(locked[0]) != int(snapshot["revision"]):
            raise HTTPException(409, {"code": "SEMANTIC_BINDING_STALE"})
    current = mapping_repository.binding(conn, str(snapshot["id"]))
    if not _binding_snapshot_matches(current, snapshot):
        raise HTTPException(409, {"code": "SEMANTIC_BINDING_STALE"})


@router.post("/bindings/{binding_id}/refresh")
def refresh_binding(binding_id: str, request: Request) -> dict[str, Any]:
    with connect() as conn:
        binding = mapping_repository.binding(conn, binding_id)
        if binding is None:
            raise HTTPException(404, {"code": "SEMANTIC_BINDING_NOT_FOUND"})
    return _refresh_binding_snapshot(binding, request)


@router.post("/load-cases/{load_case_id}/results/refresh")
def refresh_load_case_results(load_case_id: str, request: Request) -> dict[str, Any]:
    """Refresh every exact load-case result binding and pin the chosen run."""
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)
        target = SQLResultIngestionQuery(conn).get_result_ingestion_target(load_case_id)
        if target is None:
            raise HTTPException(404, {"code": "LOAD_CASE_NOT_FOUND"})
        binding_snapshots = semantic_result_refresh.load_case_binding_snapshots(conn, load_case_id)
    all_results: list[dict[str, Any]] = []
    binding_details: dict[str, dict[str, Any]] = {}
    for snapshot in binding_snapshots:
        binding_id = str(snapshot["id"])
        with connect() as conn:
            current = mapping_repository.binding(conn, binding_id)
        if not _binding_snapshot_matches(current, snapshot):
            all_results.append({"binding_id": binding_id, "binding_revision": snapshot["revision"],
                                "source_relative_path": snapshot["relative_path"], "status": "FAILED",
                                "detail": {"code": "SEMANTIC_BINDING_STALE"}})
            continue
        binding_details[binding_id] = current
    for binding_id, binding in binding_details.items():
        try:
            outcome = _refresh_binding_snapshot(binding, request)
            folder = str(binding["relative_path"])
            all_results.extend({"binding_id": binding_id, "binding_revision": binding_details[binding_id]["revision"],
                                "source_relative_path": f"{folder}/{item['relative_path']}", **item} for item in outcome["results"])
        except HTTPException as error:
            all_results.append({"binding_id": binding_id, "binding_revision": binding_details[binding_id]["revision"],
                                "source_relative_path": binding["relative_path"], "status": "FAILED", "detail": error.detail})
    all_results.sort(key=lambda item: (str(item.get("source_relative_path") or item.get("relative_path") or "").casefold(), str(item["binding_id"])))
    completed = [str(item["run_id"]) for item in all_results if item.get("run_id") and item.get("status") in {"IMPORTED", "SKIPPED"} and item.get("review_available")]
    with connect() as conn:
        candidates = mapping_repository.provenances_for_runs(conn, load_case_id, completed)
        if not candidates:
            candidates = mapping_repository.recent_provenances(conn, load_case_id)
        display_run_id = next((str(entry["analysis_run_id"]) for entry in candidates
                               if _confirmed_run_widget_metadata(conn, load_case_id, str(entry["analysis_run_id"])).get("review_available")), None)
    response = {"load_case_id": load_case_id, "project_id": target.project_id, "request_id": target.request_id,
                "display_run_id": display_run_id, "results": all_results,
                "partial": any(item.get("status") not in {"IMPORTED", "SKIPPED"} for item in all_results),
                "binding_count": len(binding_snapshots)}
    with connect() as conn:
        write_audit_event(
            request=request, principal=request.state.principal, status_code=200,
            action="SEMANTIC_LOAD_CASE_RESULTS_REFRESHED",
            detail={"load_case_id": load_case_id, "display_run_id": display_run_id,
                    "files": [{key: item.get(key) for key in ("source_relative_path", "binding_id", "binding_revision", "status", "run_id", "recipe_id", "recipe_version", "template_id", "template_version", "detail")}
                              for item in all_results]},
            connection=conn,
        )
    return response


@router.get("/results")
def results(load_case_id: str, request: Request, run_id: str | None = None, template_id: str | None = None) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "load_case", load_case_id, conn=conn)
        provenance = mapping_repository.latest_provenance(conn, load_case_id, run_id)
        if provenance is None: return {"run_id": run_id, "widgets": [], "has_provenance": False, "empty_reason": "NO_SEMANTIC_PROVENANCE"}
        selected = template_id or provenance["template_id"]
        if selected and template_id is None and provenance["template_version"] is not None:
            template, snapshot = _version_at(conn, "template", str(selected), int(provenance["template_version"]))
            response_template_version = provenance["template_version"]
        else:
            response_template_version, template, snapshot = _version(conn, "template", str(selected)) if selected else (None, {"widgets": []}, [])
        parsed = {"observations": _json(provenance["observations_json"]), "scalars": [], "curves": [], "media": [], "summary": {}, "warnings": [], "note": ""}
        widgets = resolve_widgets(template, snapshot, parsed) if selected else []
        empty_reason = None
        if not widgets:
            empty_reason = "NO_DISPLAY_TEMPLATE" if not selected else "TEMPLATE_HAS_NO_WIDGETS"
        return {"run_id": provenance["analysis_run_id"], "template_id": selected, "template_version": response_template_version, "widgets": widgets, "has_provenance": True, "empty_reason": empty_reason}


@router.get("/export")
def export_definitions(request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        catalog = _catalog(conn)
        # A package contains latest templates only.  Do not falsely preserve a
        # recipe link to an older version that is absent from this package.
        template_versions = {entry["id"]: entry["latest_version"] for entry in catalog["templates"]}
        warnings = []
        for recipe in catalog["recipes"]:
            definition = recipe["definition"]
            linked, version = definition.get("display_template_id"), definition.get("display_template_version")
            if linked and template_versions.get(linked) != version:
                definition.pop("display_template_id", None); definition.pop("display_template_version", None)
                warnings.append({"recipe_id": recipe["id"], "code": "DISPLAY_TEMPLATE_LINK_OMITTED"})
        return {"format_version": 1, "items": catalog["items"], "recipes": catalog["recipes"], "templates": catalog["templates"], "warnings": warnings}


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
                if mapping_repository.item_key_exists(conn, definition.get("key")): raise SemanticValidationError("ITEM_KEY_CONFLICT", "같은 결과 변수 키가 이미 존재합니다.")
                if definition.get("key") in imported_keys: raise SemanticValidationError("ITEM_KEY_CONFLICT", "반입 패키지에 같은 결과 변수 키가 중복됩니다.")
                imported_keys.add(definition.get("key"))
                new = f"semantic-item-{uuid4().hex[:12]}"; remap[old] = new; definition["id"] = new
                normalized_items.append(validate_item(definition))
            normalized_recipes=[]; normalized_templates=[]; template_remap: dict[str, str] = {}
            for entry in payload.get("templates", []):
                old = str(entry.get("id") or "")
                if not old or old in template_remap: raise SemanticValidationError("TEMPLATE_INVALID", "반입 템플릿 ID가 없거나 중복됩니다.")
                template_remap[old] = f"semantic-template-{uuid4().hex[:12]}"
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
                    if kind == "recipe":
                        linked = definition.get("display_template_id")
                        if linked in template_remap:
                            definition["display_template_id"] = template_remap[linked]; definition["display_template_version"] = 1
                        elif linked:
                            definition.pop("display_template_id", None); definition.pop("display_template_version", None)
                    target.append((str(entry.get("name") or definition.get("label") or kind), definition, template_remap.get(str(entry.get("id") or ""))))
        except Exception as error:
            raise _error(error) from error
        with mapping_repository.definition_transaction(conn):
            created=[]
            for definition in normalized_items: created.append(_save_version(conn, "item", definition["id"], definition["label"], definition, None, request.state.principal.user_id))
            for name, definition, template_id in normalized_templates: created.append(_save_version(conn, "template", template_id, name, definition, None, request.state.principal.user_id))
            for name, definition, _template_id in normalized_recipes: created.append(_save_version(conn, "recipe", None, name, definition, None, request.state.principal.user_id))
        return {"status": "DRAFT_IMPORTED", "created": created, "item_id_remap": remap}
