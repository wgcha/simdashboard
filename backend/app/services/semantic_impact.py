"""Bounded, stored-snapshot impact checks for semantic bundle publication.

This module deliberately never reads an import directory or creates runs.  Its
input is a saved recipe/template version and its evidence is limited to saved
definitions, saved samples, bindings, provenance, and (when installed) review
queue rows.
"""
from __future__ import annotations

import json
from typing import Any

from ..domains.semantic_mapping.engine import SemanticValidationError, preview_recipe, resolve_widgets


_BINDING_LIMIT = 200
_RUN_LIMIT = 50
_PRESENTATION_KEYS = frozenset({"title", "label", "description", "color", "decimals", "layout", "display_unit", "x_display_unit", "y_display_unit"})


class ActivationImpactBlocked(SemanticValidationError):
    """A fresh stored-snapshot check found a protected bound pair."""

    def __init__(self, impact: dict[str, Any]):
        self.impact = impact
        super().__init__("SEMANTIC_ACTIVATION_IMPACT_BLOCKED", "영향을 받는 활성 바인딩의 저장 샘플 호환성을 확인할 수 없습니다.")


def _decoded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _record(cursor: Any, row: Any) -> dict[str, Any]:
    columns = list(cursor.keys()) if hasattr(cursor, "keys") else [column[0] for column in cursor.description]
    return dict(zip(columns, row))


def _without_presentation(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_presentation(entry) for key, entry in value.items() if key not in _PRESENTATION_KEYS}
    if isinstance(value, list):
        return [_without_presentation(entry) for entry in value]
    return value


def _semantic_snapshot(value: Any) -> Any:
    """Keep immutable item meaning in recipe change classification."""
    if not isinstance(value, list):
        return None
    fields = ("id", "key", "kind", "data_type", "unit", "dimensions")
    return sorted(
        (
            {
                **{field: entry.get(field) for field in fields},
                # Missing legacy components and an explicit [] mean the same thing.
                "components": entry.get("components") or [],
            }
            for entry in value
            if isinstance(entry, dict)
        ),
        key=lambda entry: str(entry.get("id", "")),
    )


def _recipe_change(current: Any, proposed: Any, current_snapshot: Any, proposed_snapshot: Any) -> dict[str, Any]:
    if current is None:
        return {"classification": "UNKNOWN", "reason_codes": ["RECIPE_NOT_ACTIVE"]}
    try:
        old, new = _decoded(current), _decoded(proposed)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"classification": "UNKNOWN", "reason_codes": ["RECIPE_DEFINITION_UNREADABLE"]}
    if _semantic_snapshot(current_snapshot) != _semantic_snapshot(proposed_snapshot):
        return {"classification": "INTERPRETATION", "reason_codes": ["ITEM_SNAPSHOT_MEANING_CHANGED"]}
    if old == new:
        return {"classification": "UNCHANGED", "reason_codes": []}
    if _without_presentation(old) == _without_presentation(new):
        return {"classification": "PRESENTATION_ONLY", "reason_codes": ["PRESENTATION_FIELDS_CHANGED"]}
    return {"classification": "INTERPRETATION", "reason_codes": ["RECIPE_MAPPING_OR_PARSING_CHANGED"]}


def _table_exists(conn: Any, name: str) -> bool:
    try:
        # Both supported engines expose information_schema; failing closed here
        # keeps an older embedded schema readable during a rolling upgrade.
        return conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1", [name]).fetchone() is not None
    except Exception:
        return False


def _definition_base(conn: Any, kind: str, ident: str, lock: bool) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if lock and getattr(conn, "backend", "duckdb") == "postgresql" else ""
    cursor = conn.execute(
        f"SELECT id, active_version FROM semantic_{kind}s WHERE id=?{suffix}", [ident]
    )
    row = cursor.fetchone()
    return _record(cursor, row) if row else None


def _version(conn: Any, kind: str, ident: str, version: int) -> dict[str, Any] | None:
    column = f"{kind}_id"
    fields = "definition_json, item_snapshot_json" + (", sample_filename, sample_bytes" if kind == "recipe" else "")
    cursor = conn.execute(
        f"SELECT {fields} FROM semantic_{kind}_versions WHERE {column}=? AND version=?", [ident, version]
    )
    row = cursor.fetchone()
    return _record(cursor, row) if row else None


def _active_pair(conn: Any, recipe_id: str, template_id: str | None, proposed: dict[str, Any]) -> tuple[int | None, int | None]:
    recipe_version = proposed["recipe_version"] if recipe_id == proposed["recipe_id"] else None
    template_version = proposed["template_version"] if template_id == proposed["template_id"] else None
    if recipe_version is None:
        row = _definition_base(conn, "recipe", recipe_id, lock=False)
        recipe_version = int(row["active_version"]) if row and row["active_version"] is not None else None
    if template_id and template_version is None:
        row = _definition_base(conn, "template", template_id, lock=False)
        template_version = int(row["active_version"]) if row and row["active_version"] is not None else None
    return recipe_version, template_version


def _validate_pair(conn: Any, recipe_id: str, recipe_version: int | None, template_id: str | None, template_version: int | None) -> dict[str, Any]:
    pair = {"recipe_id": recipe_id, "recipe_version": recipe_version, "template_id": template_id, "template_version": template_version}
    if recipe_version is None:
        return {**pair, "status": "BLOCK", "reason_code": "ACTIVE_PAIR_INCOMPLETE", "widgets": []}
    if template_id is not None and template_version is None:
        return {**pair, "status": "BLOCK", "reason_code": "ACTIVE_PAIR_INCOMPLETE", "widgets": []}
    recipe = _version(conn, "recipe", recipe_id, recipe_version)
    template = _version(conn, "template", template_id, template_version) if template_id is not None and template_version is not None else None
    if recipe is None or (template_id is not None and template is None):
        return {**pair, "status": "BLOCK", "reason_code": "VERSION_NOT_FOUND", "widgets": []}
    if not recipe.get("sample_filename") or not recipe.get("sample_bytes"):
        return {**pair, "status": "BLOCK", "reason_code": "SAMPLE_REQUIRED", "widgets": []}
    try:
        parsed = preview_recipe(_decoded(recipe["definition_json"]), _decoded(recipe["item_snapshot_json"]), str(recipe["sample_filename"]), bytes(recipe["sample_bytes"]))
        widgets = resolve_widgets(_decoded(template["definition_json"]), _decoded(template["item_snapshot_json"]), parsed) if template is not None else []
    except SemanticValidationError as error:
        return {**pair, "status": "BLOCK", "reason_code": error.code, "widgets": []}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {**pair, "status": "BLOCK", "reason_code": "SNAPSHOT_UNREADABLE", "widgets": []}
    invalid = [widget for widget in widgets if widget.get("status") not in {"READY", "NO_VALUE"}]
    return {**pair, "status": "READY" if not invalid else "BLOCK", "reason_code": None if not invalid else "WIDGET_INPUT_INVALID", "widgets": widgets}


def _affected_bindings(conn: Any, payload: dict[str, Any], lock: bool) -> tuple[list[dict[str, Any]], bool]:
    suffix = " FOR UPDATE" if lock and getattr(conn, "backend", "duckdb") == "postgresql" else ""
    if getattr(conn, "backend", "duckdb") == "postgresql":
        where, args = "(recipe_ids_json @> CAST(? AS JSONB) OR template_id=?)", [json.dumps([payload["recipe_id"]]), payload["template_id"]]
    else:
        # A JSON scalar, rather than substring matching, preserves exact recipe
        # membership when IDs share prefixes.
        where, args = "(json_contains(recipe_ids_json, CAST(? AS JSON)) OR template_id=?)", [json.dumps(payload["recipe_id"]), payload["template_id"]]
    cursor = conn.execute(
        "SELECT id, project_id, request_id, load_case_id, relative_path, recipe_ids_json, template_id, revision "
        f"FROM semantic_folder_bindings WHERE {where} ORDER BY id LIMIT ?{suffix}", [*args, _BINDING_LIMIT + 1]
    )
    rows = [_record(cursor, row) for row in cursor.fetchall()]
    truncated = len(rows) > _BINDING_LIMIT
    selected = []
    for binding in rows[:_BINDING_LIMIT]:
        try:
            recipe_ids = _decoded(binding.pop("recipe_ids_json"))
        except (TypeError, ValueError, json.JSONDecodeError):
            recipe_ids = []
            binding["binding_parse_error"] = True
        binding["recipe_ids"] = recipe_ids
        if payload["recipe_id"] in recipe_ids or binding.get("template_id") == payload["template_id"]:
            selected.append(binding)
    return selected, truncated


def _protected_runs(conn: Any, payload: dict[str, Any]) -> dict[str, Any]:
    total = int(conn.execute(
        "SELECT count(*) FROM semantic_import_provenance WHERE recipe_id=? OR template_id=?", [payload["recipe_id"], payload["template_id"]]
    ).fetchone()[0])
    cursor = conn.execute(
        "SELECT analysis_run_id, load_case_id, recipe_id, recipe_version, template_id, template_version, created_at "
        "FROM semantic_import_provenance WHERE recipe_id=? OR template_id=? ORDER BY created_at DESC, analysis_run_id LIMIT ?",
        [payload["recipe_id"], payload["template_id"], _RUN_LIMIT],
    )
    return {"count": total, "items": [_record(cursor, row) for row in cursor.fetchall()], "truncated": total > _RUN_LIMIT, "immutable": True}


def _pending_count(conn: Any, payload: dict[str, Any], bindings: list[dict[str, Any]]) -> dict[str, Any]:
    if not _table_exists(conn, "semantic_import_review_items"):
        return {"count": None, "available": False}
    predicates = ["selected_recipe_id=?", "template_id=?"]
    args: list[Any] = [payload["recipe_id"], payload["template_id"]]
    binding_ids = [str(binding["id"]) for binding in bindings]
    if binding_ids:
        predicates.append("binding_id IN (" + ", ".join("?" for _ in binding_ids) + ")")
        args.extend(binding_ids)
    query = "SELECT count(*) FROM semantic_import_review_items WHERE review_state NOT IN ('IMPORTED', 'SKIPPED') AND (" + " OR ".join(predicates) + ")"
    return {"count": int(conn.execute(query, args).fetchone()[0]), "available": True}


def assess_bundle_impact(conn: Any, payload: dict[str, Any], *, lock: bool = False) -> dict[str, Any]:
    """Assess actual binding pairs.  ``lock`` is only for activation's tx."""
    # The activation caller already takes the binding table lock.  Bases are
    # locked deterministically so recipe/template pointer updates cannot race.
    base_keys = sorted((("recipe", payload["recipe_id"]), ("template", payload["template_id"])))
    bases = {(kind, ident): _definition_base(conn, kind, ident, lock) for kind, ident in base_keys}
    recipe_base, template_base = bases[("recipe", payload["recipe_id"])], bases[("template", payload["template_id"])]
    current = {"recipe": recipe_base["active_version"] if recipe_base else None, "template": template_base["active_version"] if template_base else None}
    proposed_recipe = _version(conn, "recipe", payload["recipe_id"], payload["recipe_version"])
    current_recipe = _version(conn, "recipe", payload["recipe_id"], int(current["recipe"])) if current["recipe"] is not None else None
    change = _recipe_change(
        current_recipe["definition_json"] if current_recipe else None,
        proposed_recipe["definition_json"] if proposed_recipe else None,
        current_recipe["item_snapshot_json"] if current_recipe else None,
        proposed_recipe["item_snapshot_json"] if proposed_recipe else None,
    )
    bindings, binding_scan_truncated = _affected_bindings(conn, payload, lock)
    checks: list[dict[str, Any]] = []
    checked_pairs: set[tuple[str, int | None, str | None, int | None]] = set()

    def check(recipe_id: str, template_id: str | None) -> dict[str, Any]:
        recipe_version, template_version = _active_pair(conn, recipe_id, template_id, payload)
        key = (recipe_id, recipe_version, template_id, template_version)
        if key not in checked_pairs:
            checked_pairs.add(key)
            checks.append(_validate_pair(conn, recipe_id, recipe_version, template_id, template_version))
        return next(entry for entry in checks if (entry["recipe_id"], entry["recipe_version"], entry["template_id"], entry["template_version"]) == key)

    check(payload["recipe_id"], payload["template_id"])
    affected = []
    for binding in bindings:
        binding_checks = []
        # A binding has a real active pair only with its configured template.
        # Do not manufacture recipe/template cross-products that it never uses.
        for recipe_id in binding["recipe_ids"]:
            if not isinstance(recipe_id, str):
                binding_checks.append({"status": "BLOCK", "reason_code": "BINDING_RECIPE_INVALID"})
                continue
            if recipe_id == payload["recipe_id"] or binding.get("template_id") == payload["template_id"]:
                recipe_version, template_version = _active_pair(conn, recipe_id, binding.get("template_id"), payload)
                key = (recipe_id, recipe_version, binding.get("template_id"), template_version)
                if key not in checked_pairs:
                    checked_pairs.add(key)
                    checks.append(_validate_pair(conn, recipe_id, recipe_version, binding.get("template_id"), template_version))
                binding_checks.append(next(check for check in checks if (check["recipe_id"], check["recipe_version"], check["template_id"], check["template_version"]) == key))
        affected.append({**binding, "compatibility": binding_checks})
    blocked = [check for check in checks if check["status"] != "READY"]
    unverified = ([{"reason_code": "BINDING_SCAN_TRUNCATED"}] if binding_scan_truncated else [])
    status = "BLOCK" if blocked else "UNVERIFIED" if unverified else "READY"
    validated_count = sum(check["status"] == "READY" for check in checks)
    return {
        "proposed_pair": {key: payload[key] for key in ("recipe_id", "recipe_version", "template_id", "template_version")},
        "current_active_versions": current,
        "recipe_change": change,
        "affected_bindings": affected,
        "protected_prior_runs": _protected_runs(conn, payload),
        "pending_review_items": _pending_count(conn, payload, bindings),
        "validation": {
            "status": status, "checks": checks, "truncated": binding_scan_truncated, "unverified": unverified,
            "sample_coverage": {
                "source": "STORED_RECIPE_SAMPLES_ONLY", "validated_pair_count": validated_count,
                "unverified_pair_count": len(checks) - validated_count + len(unverified),
            },
        },
        "activation_allowed": status == "READY",
    }


def guard_bundle_activation(conn: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Fresh transaction-time guard; browser previews and tokens are advisory."""
    if getattr(conn, "backend", "duckdb") == "postgresql":
        # Writers of folder bindings take a stronger conflicting lock first.
        # Holding this before definition locks closes binding phantoms.
        conn.execute("LOCK TABLE semantic_folder_bindings IN SHARE ROW EXCLUSIVE MODE")
    impact = assess_bundle_impact(conn, payload, lock=True)
    # The caller first preserves its established CAS and proposed-pair error
    # contracts, then blocks this fresh result immediately before publication.
    return impact
