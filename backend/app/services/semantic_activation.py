"""Validate and publish a recipe/presentation pair in one transaction."""
from __future__ import annotations

import json
from typing import Any, Callable

from ..domains.semantic_mapping.engine import SemanticValidationError, preview_recipe, resolve_widgets
from .semantic_impact import ActivationImpactBlocked, guard_bundle_activation


class ActivationConflict(ValueError):
    def __init__(self, current: dict):
        self.current = current
        super().__init__("활성 버전이 변경되었습니다. 정의를 다시 불러오세요.")


def activate_bundle(conn: Any, payload: dict, actor: str, now: Any, audit: Callable[[], None]) -> dict:
    def decoded(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    conn.execute("BEGIN TRANSACTION")
    try:
        # This rechecks persisted references under transaction locks.  A prior
        # impact preview is useful UX only and is never an activation permit.
        impact = guard_bundle_activation(conn, payload)
        definitions = {}
        current = {}
        for kind in ("recipe", "template"):
            ident, version = payload[f"{kind}_id"], payload[f"{kind}_version"]
            suffix = " FOR UPDATE" if getattr(conn, "backend", "duckdb") == "postgresql" else ""
            row = conn.execute(f"SELECT active_version FROM semantic_{kind}s WHERE id=?{suffix}", [ident]).fetchone()
            if row is None:
                raise SemanticValidationError("SEMANTIC_VERSION_NOT_FOUND", "선택한 정의가 없습니다.")
            current[kind] = row[0]
            if row[0] != payload[f"expected_{kind}_active_version"]:
                raise ActivationConflict(current)
            columns = "definition_json, item_snapshot_json" + (", sample_filename, sample_bytes" if kind == "recipe" else "")
            saved = conn.execute(f"SELECT {columns} FROM semantic_{kind}_versions WHERE {kind}_id=? AND version=?", [ident, version]).fetchone()
            if saved is None:
                raise SemanticValidationError("SEMANTIC_VERSION_NOT_FOUND", "선택한 정의 버전이 없습니다.")
            definitions[kind] = saved
        recipe, template = definitions["recipe"], definitions["template"]
        if not recipe[2] or not recipe[3]:
            raise SemanticValidationError("SEMANTIC_SAMPLE_REQUIRED", "검증 샘플을 저장한 레시피가 필요합니다.")
        parsed = preview_recipe(decoded(recipe[0]), decoded(recipe[1]), str(recipe[2]), bytes(recipe[3]))
        widgets = resolve_widgets(decoded(template[0]), decoded(template[1]), parsed)
        invalid = [widget for widget in widgets if widget["status"] not in {"READY", "NO_VALUE"}]
        if invalid:
            raise SemanticValidationError("SEMANTIC_WIDGET_VALIDATION_FAILED", "샘플의 위젯 연결을 확인하세요: " + ", ".join(f"{w['title']} ({w['status']})" for w in invalid))
        if not impact["activation_allowed"]:
            raise ActivationImpactBlocked(impact)
        for kind in ("recipe", "template"):
            ident, version = payload[f"{kind}_id"], payload[f"{kind}_version"]
            conn.execute(f"UPDATE semantic_{kind}_versions SET lifecycle_status='VALIDATED' WHERE {kind}_id=? AND lifecycle_status='ACTIVE'", [ident])
            conn.execute(f"UPDATE semantic_{kind}s SET active_version=?, updated_at=?, updated_by=? WHERE id=?", [version, now, actor, ident])
            conn.execute(f"UPDATE semantic_{kind}_versions SET lifecycle_status='ACTIVE' WHERE {kind}_id=? AND version=?", [ident, version])
        audit()
        conn.execute("COMMIT")
        return {"recipe_id": payload["recipe_id"], "recipe_version": payload["recipe_version"], "template_id": payload["template_id"], "template_version": payload["template_version"], "status": "ACTIVE", "widgets": widgets, "impact": impact}
    except BaseException:
        conn.execute("ROLLBACK")
        raise
