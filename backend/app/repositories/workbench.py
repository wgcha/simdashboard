from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..database_connection import rows
from ..services.identifiers import slugify, unique_identifier
from ..services.request_result_definition import compile_request_result_definition


DEMO_ARTIFACT_URL = "/assets/demo-workbench.svg"
RESULT_PROFILE_OUTPUT_CONTRACTS: dict[str, frozenset[str]] = {
    "LOAD_CASE": frozenset({"RESULT_MANIFEST", "POST_RESULT", "ANALYSIS_RUN_REFERENCE"}),
    "RESULT_RUN": frozenset({"ANALYSIS_RUN_REFERENCE"}),
    "SCALAR_RESULT": frozenset({"ANALYSIS_RUN_REFERENCE"}),
    "TIME_SERIES": frozenset({"ANALYSIS_RUN_REFERENCE"}),
    "CURVE": frozenset({"ANALYSIS_RUN_REFERENCE"}),
    "MEDIA_ASSET": frozenset({"ANALYSIS_RUN_REFERENCE"}),
}


DEFAULT_TASK_TYPES: tuple[dict[str, Any], ...] = (
    {"id": "cad-prepare", "kind": "CAD_PREPARE", "display_name": "CAD/형상 준비", "inputs": ["CAD_SOURCE"], "outputs": ["CAD_GEOMETRY"]},
    {"id": "doe-generate", "kind": "DOE_GENERATE", "display_name": "DOE 생성", "inputs": ["DESIGN_VARIABLES"], "outputs": ["DOE_MANIFEST"]},
    {"id": "analysis-modeling", "kind": "ANALYSIS_MODELING", "display_name": "해석 모델링", "inputs": ["CAD_GEOMETRY"], "outputs": ["SOLVER_DECK"]},
    {"id": "hpc-submit", "kind": "HPC_SUBMIT", "display_name": "HPC 제출", "inputs": ["SOLVER_DECK"], "outputs": ["DEMO_JOB_RECEIPT"]},
    {"id": "execution-monitor", "kind": "EXECUTION_MONITOR", "display_name": "실행 감시", "inputs": ["DEMO_JOB_RECEIPT"], "outputs": ["DEMO_EXECUTION_TRACE"]},
    {"id": "result-collect", "kind": "RESULT_COLLECT", "display_name": "결과 수집", "inputs": ["RESULT_LOCATION"], "outputs": ["RESULT_MANIFEST"]},
    {"id": "post-process", "kind": "POST_PROCESS", "display_name": "후처리", "inputs": ["RESULT_MANIFEST"], "outputs": ["POST_RESULT"]},
    {"id": "analysis-db-publish", "kind": "ANALYSIS_DB_PUBLISH", "display_name": "해석 DB 발행", "inputs": ["POST_RESULT"], "outputs": ["ANALYSIS_RUN_REFERENCE"]},
    {"id": "training-dataset-publish", "kind": "TRAINING_DATASET_PUBLISH", "display_name": "학습 데이터셋 발행", "inputs": ["POST_RESULT"], "outputs": ["DATASET_VERSION"]},
    {"id": "physicsai-train-validate", "kind": "PHYSICSAI_TRAIN_VALIDATE", "display_name": "PhysicsAI 학습/검증", "inputs": ["DATASET_VERSION"], "outputs": ["MODEL_CANDIDATE"]},
    {"id": "model-approval", "kind": "MODEL_APPROVAL", "display_name": "모델 승인", "inputs": ["MODEL_CANDIDATE"], "outputs": ["APPROVED_MODEL"]},
    {"id": "realtime-predict", "kind": "REALTIME_PREDICT", "display_name": "실시간 예측", "inputs": ["APPROVED_MODEL", "CAD_GEOMETRY"], "outputs": ["PREDICTION_RESULT"]},
    {"id": "dashboard-visualize", "kind": "DASHBOARD_VISUALIZE", "display_name": "대시보드 시각화", "inputs": ["ANALYSIS_RUN_REFERENCE", "PREDICTION_RESULT"], "outputs": ["DASHBOARD_BINDING"]},
    {"id": "reliability-evaluation", "kind": "RELIABILITY_EVALUATION", "display_name": "오픈셀 파손 및 CHR 휨 평가 분석", "inputs": ["POST_RESULT"], "outputs": ["RELIABILITY_ASSESSMENT"]},
    {"id": "optimization-analysis", "kind": "OPTIMIZATION_ANALYSIS", "display_name": "최적화 분석", "inputs": ["POST_RESULT"], "outputs": ["OPTIMIZATION_RESULT"]},
    {"id": "design-performance-ranking", "kind": "PERFORMANCE_RANKING", "display_name": "설계 성능 순위 평가", "inputs": ["ANALYSIS_RUN_REFERENCE"], "outputs": ["PERFORMANCE_RANKING"]},
)


DEFAULT_REQUEST_TYPES: tuple[dict[str, Any], ...] = (
    {
        "id": "result-reprocessing",
        "display_name": "결과 재처리",
        "task_ids": ["result-collect", "post-process", "analysis-db-publish"], "is_active": False,
    },
    {
        "id": "doe-analysis",
        "display_name": "DOE 해석",
        "task_ids": ["doe-generate", "analysis-modeling", "hpc-submit", "execution-monitor"], "is_active": False,
    },
    {
        "id": "physicsai-training",
        "display_name": "PhysicsAI 학습",
        "task_ids": ["training-dataset-publish", "physicsai-train-validate", "model-approval"], "is_active": False,
    },
    {
        "id": "physicsai-prediction",
        "display_name": "PhysicsAI 예측",
        "task_ids": ["realtime-predict", "dashboard-visualize"], "is_active": False,
    },
    {
        "id": "design-reliability-validation",
        "display_name": "설계 신뢰성 검증",
        "task_ids": ["cad-prepare", "analysis-modeling", "hpc-submit", "post-process", "analysis-db-publish", "reliability-evaluation"],
        "display_names": ["CAD 작업", "해석 모델링", "HPC 수행", "결과 후처리", "해석 DB 저장", "오픈셀 파손 및 CHR 휨 평가 분석"],
        "is_active": True,
    },
    {
        "id": "design-doe-exploration",
        "display_name": "설계 DOE 탐색",
        "task_ids": ["cad-prepare", "doe-generate", "hpc-submit", "post-process", "optimization-analysis", "analysis-db-publish", "design-performance-ranking"],
        "display_names": ["CAD 작업", "DOE 파일 생성", "HPC 수행", "결과 후처리", "최적화 분석", "해석 DB 저장", "설계 성능 순위 평가"],
        "is_active": True,
    },
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BatchAttemptWorkflowRunLinkConflictError(ValueError):
    """A QUEUED attempt changed before its runner identity could be linked."""


def _decoded(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def _canonical_contracts(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(
        value.strip().upper()
        for value in values
        if isinstance(value, str) and value.strip()
    ))


def _canonicalize_page_contracts(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_pages: list[dict[str, Any]] = []
    for page in pages:
        normalized_page = {**page}
        normalized_widgets: list[dict[str, Any]] = []
        for widget in page.get("widgets", []):
            normalized_widget = dict(widget)
            settings = normalized_widget.get("settings")
            if isinstance(settings, dict) and isinstance(settings.get("data_contracts"), list):
                normalized_widget["settings"] = {**settings, "data_contracts": _canonical_contracts(settings["data_contracts"])}
            normalized_widgets.append(normalized_widget)
        normalized_page["widgets"] = normalized_widgets
        normalized_pages.append(normalized_page)
    return normalized_pages


def ensure_default_workbench_catalog(conn: Any) -> None:
    now = _utcnow()
    for item in DEFAULT_TASK_TYPES:
        conn.execute(
            """
            INSERT OR IGNORE INTO task_type_versions
            (id, version, kind, display_name, description, supports_standalone,
             input_artifact_types_json, output_artifact_types_json, parameter_schema_json,
             demo_artifact_url, is_active, created_at)
            VALUES (?, 1, ?, ?, ?, true, ?, ?, ?, ?, true, ?)
            """,
            [
                item["id"],
                item["kind"],
                item["display_name"],
                f"{item['display_name']} 계약을 외부 실행 없이 검토하는 데모 Task입니다.",
                json.dumps(item["inputs"], ensure_ascii=False),
                json.dumps(item["outputs"], ensure_ascii=False),
                json.dumps({"type": "object", "additionalProperties": False}, ensure_ascii=False),
                DEMO_ARTIFACT_URL,
                now,
            ],
        )

    for item in DEFAULT_REQUEST_TYPES:
        task_refs = [{"id": task_id, "version": 1} for task_id in item["task_ids"]]
        nodes = []
        for index, task_id in enumerate(item["task_ids"]):
            nodes.append(
                {
                    "node_key": task_id,
                    "task_type_id": task_id,
                    "task_type_version": 1,
                    "depends_on": [item["task_ids"][index - 1]] if index else [],
                    "display_name": item.get("display_names", [])[index] if item.get("display_names") else None,
                }
            )
        conn.execute(
            """
            INSERT OR IGNORE INTO request_type_versions
            (id, version, display_name, description, allowed_task_types_json,
             default_workflow_json, match_rules_json, is_active, created_at)
            VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                item["id"],
                item["display_name"],
                f"{item['display_name']}용 기본 데모 조합입니다. Task 순서는 관리자 정의로 교체할 수 있습니다.",
                json.dumps(task_refs, ensure_ascii=False),
                json.dumps({"nodes": nodes}, ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
                item.get("is_active", True),
                now,
            ],
        )
    conn.execute("UPDATE request_type_versions SET is_active=false WHERE id IN ('result-reprocessing', 'doe-analysis', 'physicsai-training', 'physicsai-prediction')")


def ensure_seed_request_work_plans(conn: Any) -> None:
    repository = WorkbenchRepository(conn)
    seeds = (
        (
            "request-drop-001",
            "design-reliability-validation",
            "SPDM-DEMO-2026-0001",
            {1: "COMPLETED", 2: "COMPLETED", 3: "IN_PROGRESS"},
        ),
        (
            "request-clamp-001",
            "design-doe-exploration",
            "SPDM-DEMO-2026-0002",
            {1: "COMPLETED", 2: "IN_PROGRESS"},
        ),
    )
    for request_id, request_type_id, spdm_reference, statuses in seeds:
        if not repository.analysis_request_exists(request_id):
            continue
        existing_plan = repository.work_plan(request_id)
        if existing_plan:
            # Only migrate the exact value previously written by this demo seed.
            # User- or administrator-edited origins are authoritative and must survive re-seeding.
            if existing_plan.get("source_type") == "DEPARTMENT_HEAD" and existing_plan.get("source_reference") == "기존 데모 시드":
                conn.execute(
                    "UPDATE request_work_plans SET source_type=?, source_reference=? WHERE request_id=?",
                    ["EXTERNAL_SYSTEM", spdm_reference, request_id],
                )
            continue
        request_type = repository.get_request_type(request_type_id, 1)
        if not request_type:
            continue
        owner = repository.request_context(request_id)["owner"] or "해석 담당자"
        repository.assign_request_type(request_id, request_type_id, 1, "ADMIN", "system")
        repository.create_work_plan(
            request_id,
            request_type,
            owner,
            "system",
            owner_user_id="local-admin",
            source_type="EXTERNAL_SYSTEM",
            source_reference=spdm_reference,
            requested_by="system",
            statuses=statuses,
        )
        conn.execute("UPDATE analysis_requests SET status='IN_PROGRESS' WHERE id=?", [request_id])

def ensure_seed_legacy_result_layout_assignment(conn: Any) -> None:
    """Persist the one known demo domain mapping; never infer it from result rows."""
    repository = WorkbenchRepository(conn)
    request_id = "request-drop-001"
    if not repository.analysis_request_exists(request_id) or repository.result_layout_snapshot(request_id):
        return
    request_type = repository.get_request_type("design-reliability-validation", 1)
    if not request_type:
        return
    snapshot = {
        "template_id": "legacy-domain-dashboard",
        "template_version": 1,
        "template_name": "기존 낙하 상세 분석",
        "request_type_id": request_type["id"],
        "request_type_version": request_type["version"],
        "pages": [],
        "required_data_contracts": [],
        "legacy_renderer": "LEGACY_DOMAIN",
    }
    conn.execute(
        """INSERT INTO request_result_layout_snapshots
            (request_id, source_request_type_id, source_request_type_version, source_template_id,
             source_template_version, snapshot_json, snapshot_reason, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'LEGACY_ASSIGNED', ?, ?)""",
        [request_id, request_type["id"], request_type["version"], "legacy-domain-dashboard", 1, json.dumps(snapshot, ensure_ascii=False), "seed-legacy-domain", _utcnow()],
    )



class WorkbenchRepository:
    def __init__(self, conn: Any):
        self.conn = conn

    def begin_transaction(self) -> None:
        """Begin an explicit unit of work without exposing connection SQL."""
        self.conn.execute("BEGIN TRANSACTION")

    def commit_transaction(self) -> None:
        """Commit an explicit unit of work."""
        self.conn.execute("COMMIT")

    def rollback_transaction(self) -> None:
        """Roll back an explicit unit of work."""
        self.conn.execute("ROLLBACK")

    @staticmethod
    def _task_item(item: dict[str, Any]) -> dict[str, Any]:
        input_types = _decoded(item.pop("input_artifact_types_json")) or []
        output_types = _decoded(item.pop("output_artifact_types_json")) or []
        parameter_schema = _decoded(item.pop("parameter_schema_json")) or {}
        return {
            **item,
            "input_artifact_types": input_types,
            "output_artifact_types": output_types,
            "parameter_schema": parameter_schema,
        }

    @staticmethod
    def _request_item(item: dict[str, Any]) -> dict[str, Any]:
        allowed = _decoded(item.pop("allowed_task_types_json")) or []
        workflow = _decoded(item.pop("default_workflow_json")) or {"nodes": []}
        rules = _decoded(item.pop("match_rules_json")) or {}
        rules.setdefault("labels", ["SPDM", "부서"])
        return {
            **item,
            "allowed_task_types": allowed,
            "default_workflow": workflow,
            "match_rules": rules,
        }

    @staticmethod
    def _template_item(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "page_definitions": _canonicalize_page_contracts(_decoded(item.pop("page_definitions_json")) or [])}

    def list_analysis_templates(
        self,
        *,
        all_versions: bool = False,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if all_versions:
            if project_id:
                sql = "SELECT * FROM analysis_template_versions WHERE (scope_kind='PROJECT' AND project_id=?) OR (scope_kind='SYSTEM' AND lifecycle_status='PUBLISHED')"
                parameters: list[Any] = [project_id]
            else:
                sql = "SELECT * FROM analysis_template_versions WHERE scope_kind='SYSTEM'"
                parameters = []
            return [
                self._template_item(item)
                for item in rows(self.conn.execute(sql + " ORDER BY display_name, version DESC", parameters))
            ]
        scope_sql = "scope_kind='SYSTEM'"
        scope_parameters: list[Any] = []
        if project_id:
            scope_sql = "(scope_kind='SYSTEM' OR (scope_kind='PROJECT' AND project_id=?))"
            scope_parameters.append(project_id)
        latest = (
            "SELECT template_id, max(version) FROM analysis_template_versions "
            f"WHERE lifecycle_status='PUBLISHED' AND {scope_sql} GROUP BY template_id"
        )
        sql = f"SELECT * FROM analysis_template_versions WHERE lifecycle_status='PUBLISHED' AND {scope_sql} AND (template_id, version) IN ({latest}) ORDER BY display_name, version DESC"
        return [self._template_item(item) for item in rows(self.conn.execute(sql, scope_parameters * 2))]

    def get_analysis_template(self, template_id: str, version: int) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM analysis_template_versions WHERE template_id=? AND version=?", [template_id, version]))
        return self._template_item(items[0]) if items else None

    def create_analysis_template_version(self, payload: dict[str, Any], created_by: str) -> dict[str, Any]:
        payload = dict(payload)
        payload["id"] = payload.get("id") or unique_identifier(self.conn, "analysis_template_versions", "template_id", slugify(f"analysis-template-{payload['display_name']}", fallback="analysis-template"))
        scope_kind = payload["scope_kind"]
        project_id = payload.get("project_id")
        if (scope_kind == "SYSTEM" and project_id is not None) or (scope_kind == "PROJECT" and not project_id):
            raise ValueError("ANALYSIS_TEMPLATE_SCOPE_PROJECT_MISMATCH")
        family = self.conn.execute("SELECT scope_kind, project_id FROM analysis_template_versions WHERE template_id=? ORDER BY version LIMIT 1", [payload["id"]]).fetchone()
        if family and (family[0] != scope_kind or family[1] != project_id):
            raise ValueError("ANALYSIS_TEMPLATE_FAMILY_SCOPE_IMMUTABLE")
        payload["page_definitions"] = _canonicalize_page_contracts(payload["page_definitions"])
        version = int(self.conn.execute("SELECT COALESCE(max(version), 0) FROM analysis_template_versions WHERE template_id=?", [payload["id"]]).fetchone()[0]) + 1
        self.conn.execute(
            """
            INSERT INTO analysis_template_versions
                (template_id, version, scope_kind, project_id, display_name, description,
                 lifecycle_status, page_definitions_json, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [payload["id"], version, payload["scope_kind"], payload.get("project_id"), payload["display_name"], payload["description"], payload["lifecycle_status"], json.dumps(payload["page_definitions"], ensure_ascii=False), created_by, _utcnow()],
        )
        return self.get_analysis_template(payload["id"], version)  # type: ignore[return-value]

    def _result_profile_item(
        self, item: dict[str, Any], *, profile_scope: str, project_id: str | None
    ) -> dict[str, Any] | None:
        """Decode a profile record without widening a project template's scope."""
        item = dict(item)
        template = self.get_analysis_template(item["template_id"], int(item["template_version"]))
        if not template:
            return None
        overrides = _decoded(item.pop("overrides_json")) or {}
        return {
            **item,
            "template": template,
            "included_widget_ids": overrides.get("included_widget_ids", []),
            "overrides": overrides,
            "required_data_contracts": _canonical_contracts(_decoded(item.pop("required_data_contracts_json")) or []),
            "profile_scope": profile_scope,
            "project_id": project_id,
        }

    def result_profile_for(self, request_type_id: str, request_type_version: int) -> dict[str, Any] | None:
        records = rows(self.conn.execute("SELECT * FROM request_type_result_profiles WHERE request_type_id=? AND request_type_version=?", [request_type_id, request_type_version]))
        return self._result_profile_item(records[0], profile_scope="SYSTEM_DEFAULT", project_id=None) if records else None

    def project_result_profile_for(self, project_id: str, request_type_id: str, request_type_version: int) -> dict[str, Any] | None:
        records = rows(self.conn.execute("SELECT * FROM project_request_type_result_profiles WHERE project_id=? AND request_type_id=? AND request_type_version=? ORDER BY binding_version DESC LIMIT 1", [project_id, request_type_id, request_type_version]))
        return self._result_profile_item(records[0], profile_scope="PROJECT_OVERRIDE", project_id=project_id) if records else None

    def resolved_result_profile_for(self, project_id: str, request_type_id: str, request_type_version: int) -> dict[str, Any] | None:
        return self.project_result_profile_for(project_id, request_type_id, request_type_version) or self.result_profile_for(request_type_id, request_type_version)

    @staticmethod
    def _widget_data_contracts(template: dict[str, Any], included: set[str]) -> set[str]:
        contracts: set[str] = set()
        for page in template["page_definitions"]:
            for widget in page.get("widgets", []):
                if included and widget.get("id") not in included:
                    continue
                settings = widget.get("settings") if isinstance(widget.get("settings"), dict) else {}
                for contract in settings.get("data_contracts", []):
                    if isinstance(contract, str) and contract.strip():
                        contracts.update(_canonical_contracts([contract]))
        return contracts

    def _validate_result_profile(
        self,
        template: dict[str, Any],
        payload: dict[str, Any],
    ) -> list[str]:
        available = {
            str(widget["id"])
            for page in template["page_definitions"]
            for widget in page.get("widgets", [])
        }
        raw_included = payload.get("included_widget_ids")
        included = (
            sorted(available)
            if raw_included is None
            else list(dict.fromkeys(str(item) for item in raw_included))
        )
        if not included:
            raise ValueError("RESULT_PROFILE_EMPTY_LAYOUT")
        if not set(included) <= available:
            raise ValueError("RESULT_PROFILE_UNKNOWN_WIDGET")
        selected = set(included)
        required = {
            str(widget["id"])
            for page in template["page_definitions"]
            for widget in page.get("widgets", [])
            if isinstance(widget.get("settings"), dict)
            and (
                widget["settings"].get("required") is True
                or widget["settings"].get("required_widget") is True
            )
        }
        if not required <= selected:
            raise ValueError("RESULT_PROFILE_REQUIRED_WIDGET_MISSING")
        requested = set(_canonical_contracts(payload.get("required_data_contracts") or []))
        requested.update(self._widget_data_contracts(template, selected))
        unknown = requested.difference(RESULT_PROFILE_OUTPUT_CONTRACTS)
        if unknown:
            raise ValueError(f"RESULT_PROFILE_UNKNOWN_DATA_CONTRACT:{','.join(sorted(unknown))}")
        # Result contracts describe what folder refresh may upload later. They are
        # intentionally independent from the workflow's declared task outputs.
        return included

    def save_result_profile(self, request_type_id: str, request_type_version: int, payload: dict[str, Any]) -> dict[str, Any]:
        template = self.get_analysis_template(payload["template_id"], int(payload["template_version"]))
        if not template or template["lifecycle_status"] != "PUBLISHED":
            raise LookupError("ANALYSIS_TEMPLATE_NOT_PUBLISHED")
        if template["scope_kind"] != "SYSTEM":
            raise ValueError("SYSTEM_RESULT_PROFILE_REQUIRES_SYSTEM_TEMPLATE")
        request_type = self.get_request_type(request_type_id, request_type_version)
        if not request_type:
            raise LookupError("REQUEST_TYPE_NOT_FOUND")
        included = self._validate_result_profile(template, payload)
        overrides = {**payload.get("overrides", {}), "included_widget_ids": included}
        required_contracts = _canonical_contracts(payload.get("required_data_contracts") or [])
        self.conn.execute(
            """INSERT INTO request_type_result_profiles
                (request_type_id, request_type_version, template_id, template_version, overrides_json, required_data_contracts_json)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (request_type_id, request_type_version) DO UPDATE SET
                 template_id=excluded.template_id, template_version=excluded.template_version,
                 overrides_json=excluded.overrides_json, required_data_contracts_json=excluded.required_data_contracts_json""",
            [request_type_id, request_type_version, payload["template_id"], payload["template_version"], json.dumps(overrides, ensure_ascii=False), json.dumps(required_contracts, ensure_ascii=False)],
        )
        return self.result_profile_for(request_type_id, request_type_version)  # type: ignore[return-value]

    def save_project_result_profile(self, project_id: str, request_type_id: str, request_type_version: int, payload: dict[str, Any], *, bound_by: str) -> dict[str, Any]:
        if not self.conn.execute("SELECT 1 FROM projects WHERE id=?", [project_id]).fetchone():
            raise LookupError("PROJECT_NOT_FOUND")
        template = self.get_analysis_template(payload["template_id"], int(payload["template_version"]))
        if not template or template["lifecycle_status"] != "PUBLISHED":
            raise LookupError("ANALYSIS_TEMPLATE_NOT_PUBLISHED")
        if template["scope_kind"] == "PROJECT" and template["project_id"] != project_id:
            raise PermissionError("PROJECT_RESULT_TEMPLATE_SCOPE_MISMATCH")
        request_type = self.get_request_type(request_type_id, request_type_version)
        if not request_type:
            raise LookupError("REQUEST_TYPE_NOT_FOUND")
        included = self._validate_result_profile(template, payload)
        overrides = {**payload.get("overrides", {}), "included_widget_ids": included}
        required_contracts = _canonical_contracts(payload.get("required_data_contracts") or [])
        binding_version = int(self.conn.execute(
            "SELECT COALESCE(max(binding_version), 0) + 1 FROM project_request_type_result_profiles WHERE project_id=? AND request_type_id=? AND request_type_version=?",
            [project_id, request_type_id, request_type_version],
        ).fetchone()[0])
        self.conn.execute(
            """INSERT INTO project_request_type_result_profiles
                (project_id, request_type_id, request_type_version, binding_version, template_id, template_version,
                 overrides_json, required_data_contracts_json, bound_by, bound_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [project_id, request_type_id, request_type_version, binding_version, payload["template_id"], payload["template_version"], json.dumps(overrides, ensure_ascii=False), json.dumps(required_contracts, ensure_ascii=False), bound_by, _utcnow()],
        )
        return self.project_result_profile_for(project_id, request_type_id, request_type_version)  # type: ignore[return-value]

    def result_layout_snapshot(self, request_id: str) -> dict[str, Any] | None:
        records = rows(self.conn.execute("SELECT * FROM request_result_layout_snapshots WHERE request_id=?", [request_id]))
        if not records:
            return None
        item = records[0]
        item["snapshot"] = _decoded(item.pop("snapshot_json")) or {}
        return item

    def load_case_belongs_to_request(self, request_id: str, load_case_id: str) -> bool:
        return bool(
            self.conn.execute(
                """
                SELECT 1
                FROM load_cases lc
                JOIN analysis_requests ar ON ar.id=lc.request_id
                WHERE lc.id=? AND lc.request_id=?
                """,
                [load_case_id, request_id],
            ).fetchone()
        )

    def result_layout_bindings(self, request_id: str, load_case_id: str | None = None) -> dict[str, Any]:
        load_case_filter = " AND lc.id=?" if load_case_id else ""
        parameters = [request_id, *([load_case_id] if load_case_id else [])]
        load_cases = rows(
            self.conn.execute(
                """
                SELECT lc.id, lc.name, lc.analysis_type, lc.status
                FROM load_cases lc
                WHERE lc.request_id=?
                """ + load_case_filter + " ORDER BY lc.created_at",
                parameters,
            )
        )
        runs = rows(
            self.conn.execute(
                """
                SELECT ar.id, ar.run_no, ar.solver, ar.status, ar.completed_at,
                       lc.id AS load_case_id, lc.name AS load_case_name
                FROM analysis_runs ar
                JOIN load_cases lc ON lc.id=ar.load_case_id
                WHERE lc.request_id=?
                """ + load_case_filter + """
                ORDER BY COALESCE(ar.completed_at, ar.started_at) DESC NULLS LAST, ar.run_no DESC
                """,
                parameters,
            )
        )
        latest_run = runs[0] if runs else None
        scalars: list[dict[str, Any]] = []
        if latest_run:
            scalar_rows = rows(
                self.conn.execute(
                    """
                    SELECT variable_key, display_name, value_double, value_integer, value_text,
                           unit, threshold_double, verdict
                    FROM scalar_results WHERE analysis_run_id=?
                    ORDER BY display_name, variable_key LIMIT 100
                    """,
                    [latest_run["id"]],
                )
            )
            for item in scalar_rows:
                value = item["value_double"]
                if value is None:
                    value = item["value_integer"]
                if value is None:
                    value = item["value_text"]
                scalars.append(
                    {
                        "variable_key": item["variable_key"],
                        "display_name": item["display_name"],
                        "value": value,
                        "unit": item["unit"],
                        "threshold": item["threshold_double"],
                        "verdict": item["verdict"],
                    }
                )
        available: set[str] = set()
        if load_cases:
            available.add("LOAD_CASE")
        if latest_run:
            available.add("RESULT_RUN")
        if scalars:
            available.add("SCALAR_RESULT")
        if latest_run:
            uploaded_contract_tables = {
                "TIME_SERIES": "time_series_results",
                "CURVE": "curve_results",
                "MEDIA_ASSET": "media_assets",
            }
            for contract, table in uploaded_contract_tables.items():
                if self.conn.execute(
                    f"SELECT 1 FROM {table} WHERE analysis_run_id=? LIMIT 1",
                    [latest_run["id"]],
                ).fetchone():
                    available.add(contract)
        failed = latest_run and str(latest_run["status"]).upper() in {"FAILED", "ERROR"}
        return {
            "available_data_contracts": sorted(available),
            "load_cases": load_cases,
            "latest_result_run": latest_run,
            "scalars": scalars,
            "error": "최근 결과 실행이 실패했습니다." if failed else None,
        }

    def create_result_layout_snapshot(self, request_id: str, request_type: dict[str, Any], created_by: str) -> dict[str, Any] | None:
        if self.result_layout_snapshot(request_id):
            raise FileExistsError("REQUEST_RESULT_LAYOUT_SNAPSHOT_EXISTS")
        project_row = self.conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()
        if not project_row:
            raise LookupError("REQUEST_NOT_FOUND")
        profile = self.resolved_result_profile_for(str(project_row[0]), request_type["id"], int(request_type["version"]))
        if not profile:
            return None
        included = set(profile.get("included_widget_ids") or [])
        pages = []
        for page in profile["template"]["page_definitions"]:
            copied = {**page, "widgets": [dict(widget) for widget in page.get("widgets", []) if not included or widget["id"] in included]}
            if copied["widgets"]:
                pages.append(copied)
        if not pages:
            raise ValueError("RESULT_PROFILE_EMPTY_LAYOUT")
        snapshot = {"template_id": profile["template_id"], "template_version": profile["template_version"], "template_name": profile["template"]["display_name"], "request_type_id": request_type["id"], "request_type_version": request_type["version"], "profile_scope": profile["profile_scope"], "profile_project_id": profile["project_id"], "profile_binding_version": profile.get("binding_version"), "pages": pages, "required_data_contracts": _canonical_contracts(profile["required_data_contracts"])}
        self.conn.execute(
            """INSERT INTO request_result_layout_snapshots
                (request_id, source_request_type_id, source_request_type_version, source_template_id,
                 source_template_version, snapshot_json, snapshot_reason, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'REQUEST_CREATED', ?, ?)""",
            [request_id, request_type["id"], request_type["version"], profile["template_id"], profile["template_version"], json.dumps(snapshot, ensure_ascii=False), created_by, _utcnow()],
        )
        return self.result_layout_snapshot(request_id)

    def _next_identifier(self, prefix: str, seed: str, table: str) -> str:
        return unique_identifier(self.conn, table, "id", slugify(f"{prefix}-{seed}", fallback=prefix))

    def list_task_types(self, *, all_versions: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM task_type_versions"
        if not all_versions:
            sql += " WHERE is_active=true AND (id, version) IN (SELECT id, max(version) FROM task_type_versions WHERE is_active=true GROUP BY id)"
        sql += " ORDER BY display_name, version DESC"
        return [self._task_item(item) for item in rows(self.conn.execute(sql))]

    def get_task_type(self, task_type_id: str, version: int) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM task_type_versions WHERE id=? AND version=?", [task_type_id, version]))
        return self._task_item(items[0]) if items else None

    def task_type_exists(self, task_type_id: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM task_type_versions WHERE id=? LIMIT 1", [task_type_id]).fetchone())

    def create_task_type_version(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        payload["id"] = payload.get("id") or self._next_identifier("task", f"{payload.get('kind', '')}-{payload.get('display_name', '')}", "task_type_versions")
        current = self.conn.execute("SELECT COALESCE(max(version), 0) FROM task_type_versions WHERE id=?", [payload["id"]]).fetchone()[0]
        version = int(current) + 1
        now = _utcnow()
        self.conn.execute(
            """
            INSERT INTO task_type_versions
            (id, version, kind, display_name, description, supports_standalone,
             input_artifact_types_json, output_artifact_types_json, parameter_schema_json,
             demo_artifact_url, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [payload["id"], version, payload["kind"], payload["display_name"], payload["description"], payload["supports_standalone"], json.dumps(payload["input_artifact_types"], ensure_ascii=False), json.dumps(payload["output_artifact_types"], ensure_ascii=False), json.dumps(payload["parameter_schema"], ensure_ascii=False), payload["demo_artifact_url"], payload["is_active"], now],
        )
        return self.get_task_type(payload["id"], version)  # type: ignore[return-value]

    def deactivate_task_type(self, task_type_id: str) -> bool:
        exists = self.conn.execute("SELECT 1 FROM task_type_versions WHERE id=? LIMIT 1", [task_type_id]).fetchone()
        if not exists:
            return False
        self.conn.execute("UPDATE task_type_versions SET is_active=false WHERE id=?", [task_type_id])
        # A task type without an active execution definition cannot be selected
        # for new work, while historical profile snapshots remain intact.
        self.conn.execute("UPDATE batch_path_profiles SET is_active=false WHERE task_type_id=?", [task_type_id])
        return True


    def list_request_types(self, *, all_versions: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM request_type_versions"
        if not all_versions:
            sql += " WHERE is_active=true AND (id, version) IN (SELECT id, max(version) FROM request_type_versions WHERE is_active=true GROUP BY id)"
        sql += " ORDER BY display_name, version DESC"
        return [self._request_item(item) for item in rows(self.conn.execute(sql))]

    def get_request_type(self, request_type_id: str, version: int) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM request_type_versions WHERE id=? AND version=?", [request_type_id, version]))
        return self._request_item(items[0]) if items else None

    def request_type_exists(self, request_type_id: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM request_type_versions WHERE id=? LIMIT 1", [request_type_id]).fetchone())

    def latest_request_type(self, request_type_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT max(version) FROM request_type_versions WHERE id=? AND is_active=true",
            [request_type_id],
        ).fetchone()
        return self.get_request_type(request_type_id, int(row[0])) if row and row[0] is not None else None

    def create_request_type_version(
        self, payload: dict[str, Any], *, created_by: str = "request-type-definition"
    ) -> dict[str, Any]:
        payload = dict(payload)
        if payload.get("result_profile") and payload.get("result_definition"):
            raise ValueError("RESULT_PROFILE_AND_DEFINITION_CONFLICT")
        payload["id"] = payload.get("id") or self._next_identifier("request", payload.get("display_name", ""), "request_type_versions")
        current = self.conn.execute("SELECT COALESCE(max(version), 0) FROM request_type_versions WHERE id=?", [payload["id"]]).fetchone()[0]
        version = int(current) + 1
        compiled_definition: dict[str, Any] | None = None
        if payload.get("result_definition"):
            compiled_definition = compile_request_result_definition(
                payload["result_definition"],
                request_type_id=payload["id"],
                request_type_display_name=payload["display_name"],
            )
            self._validate_result_profile(
                {"page_definitions": compiled_definition["template"]["page_definitions"]},
                {
                    **compiled_definition["profile"],
                    "template_id": compiled_definition["template"]["id"],
                    "template_version": 1,
                },
            )
        elif payload.get("result_profile"):
            template = self.get_analysis_template(
                payload["result_profile"]["template_id"],
                int(payload["result_profile"]["template_version"]),
            )
            if not template or template["lifecycle_status"] != "PUBLISHED":
                raise LookupError("ANALYSIS_TEMPLATE_NOT_PUBLISHED")
            if template["scope_kind"] != "SYSTEM":
                raise ValueError("SYSTEM_RESULT_PROFILE_REQUIRES_SYSTEM_TEMPLATE")
            self._validate_result_profile(template, payload["result_profile"])
        now = _utcnow()
        self.conn.execute(
            """
            INSERT INTO request_type_versions
            (id, version, display_name, description, allowed_task_types_json,
             default_workflow_json, match_rules_json, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [payload["id"], version, payload["display_name"], payload["description"], json.dumps(payload["allowed_task_types"], ensure_ascii=False), json.dumps(payload["default_workflow"], ensure_ascii=False), json.dumps(payload["match_rules"], ensure_ascii=False), payload["is_active"], now],
        )
        if compiled_definition:
            template = self.create_analysis_template_version(compiled_definition["template"], created_by)
            self.save_result_profile(
                payload["id"], version,
                {**compiled_definition["profile"], "template_id": template["template_id"], "template_version": template["version"]},
            )
        elif payload.get("result_profile"):
            self.save_result_profile(payload["id"], version, payload["result_profile"])
        return self.get_request_type(payload["id"], version)  # type: ignore[return-value]


    def deactivate_request_type(self, request_type_id: str) -> bool:
        exists = self.conn.execute(
            "SELECT 1 FROM request_type_versions WHERE id=? LIMIT 1",
            [request_type_id],
        ).fetchone()
        if not exists:
            return False
        self.conn.execute(
            "UPDATE request_type_versions SET is_active=false WHERE id=?",
            [request_type_id],
        )
        return True

    def analysis_request_exists(self, request_id: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM analysis_requests WHERE id=?", [request_id]).fetchone())

    def request_context(self, request_id: str) -> dict[str, Any] | None:
        items = rows(
            self.conn.execute(
                """
                SELECT ar.id AS request_id, ar.project_id, ar.title, ar.status, ar.owner,
                       ar.overall_note, p.product_name,
                       COALESCE(min(lc.analysis_type), 'UNASSIGNED') AS analysis_type
                FROM analysis_requests ar
                JOIN projects p ON p.id=ar.project_id
                LEFT JOIN load_cases lc ON lc.request_id=ar.id
                WHERE ar.id=?
                GROUP BY ar.id, ar.project_id, ar.title, ar.status, ar.owner, ar.overall_note, p.product_name
                """,
                [request_id],
            )
        )
        return items[0] if items else None

    @staticmethod
    def _rule_matches(rules: dict[str, Any], context: dict[str, Any]) -> bool:
        if not rules:
            return False
        matched_rule = False
        exact_keys = {"project_id", "status", "owner", "analysis_type", "product_name"}
        for key, expected in rules.items():
            if key == "labels":
                continue
            if key in exact_keys:
                matched_rule = True
                actual = str(context.get(key) or "")
                accepted = expected if isinstance(expected, list) else [expected]
                if actual.casefold() not in {str(value).casefold() for value in accepted}:
                    return False
            elif key == "title_contains":
                matched_rule = True
                if str(expected).casefold() not in str(context.get("title") or "").casefold():
                    return False
            elif key == "overall_note_contains":
                matched_rule = True
                if str(expected).casefold() not in str(context.get("overall_note") or "").casefold():
                    return False
            else:
                return False
        return matched_rule

    def request_type_resolution(self, request_id: str) -> dict[str, Any]:
        context = self.request_context(request_id)
        if not context:
            raise LookupError("REQUEST_NOT_FOUND")
        assigned = rows(self.conn.execute("SELECT * FROM analysis_request_type_assignments WHERE request_id=?", [request_id]))
        if assigned:
            item = assigned[0]
            request_type = self.get_request_type(item["request_type_id"], int(item["request_type_version"]))
            return {"resolution": "ASSIGNED", "request_id": request_id, "source": item["source"], "reason": "의뢰에 고정된 불변 버전", "request_type": request_type, "candidates": [], "decided_by": item["decided_by"], "decided_at": item["decided_at"]}

        matches = [item for item in self.list_request_types() if self._rule_matches(item["match_rules"], context)]
        if len(matches) == 1:
            return {"resolution": "RECOMMENDED", "request_id": request_id, "source": "RULE", "reason": f"의뢰 정보 규칙 일치: {matches[0]['match_rules']}", "request_type": matches[0], "candidates": matches, "decided_by": None, "decided_at": None}
        if len(matches) > 1:
            return {"resolution": "REVIEW_REQUIRED", "request_id": request_id, "source": "RULE", "reason": "둘 이상의 Request Type 규칙이 일치합니다.", "request_type": None, "candidates": matches, "decided_by": None, "decided_at": None}
        return {"resolution": "USER_SELECTION", "request_id": request_id, "source": None, "reason": "일치하는 규칙 또는 고정 유형이 없어 사용자가 선택합니다.", "request_type": None, "candidates": [], "decided_by": None, "decided_at": None}

    def assign_request_type(self, request_id: str, request_type_id: str, version: int, source: str, decided_by: str) -> dict[str, Any]:
        if not self.analysis_request_exists(request_id):
            raise LookupError("REQUEST_NOT_FOUND")
        request_type = self.get_request_type(request_type_id, version)
        if not request_type or not request_type["is_active"]:
            raise LookupError("REQUEST_TYPE_NOT_FOUND")
        current = rows(self.conn.execute("SELECT source FROM analysis_request_type_assignments WHERE request_id=?", [request_id]))
        if current and current[0]["source"] == "ADMIN" and source != "ADMIN":
            raise PermissionError("ADMIN_ASSIGNMENT_LOCKED")
        now = _utcnow()
        self.conn.execute(
            """
            INSERT INTO analysis_request_type_assignments
                (request_id, request_type_id, request_type_version, source, rule_snapshot_json, decided_by, decided_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (request_id) DO UPDATE SET
                request_type_id=excluded.request_type_id,
                request_type_version=excluded.request_type_version,
                source=excluded.source,
                rule_snapshot_json=excluded.rule_snapshot_json,
                decided_by=excluded.decided_by,
                decided_at=excluded.decided_at
            """,
            [request_id, request_type_id, version, source, json.dumps(request_type["match_rules"], ensure_ascii=False), decided_by, now],
        )
        return self.request_type_resolution(request_id)

    def work_plan(self, request_id: str) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM request_work_plans WHERE request_id=?", [request_id]))
        if not items:
            return None
        item = items[0]
        item["definition_snapshot"] = _decoded(item.pop("definition_snapshot_json")) or {"nodes": []}
        return item

    def work_items(self, request_id: str) -> list[dict[str, Any]]:
        return rows(self.conn.execute("SELECT * FROM request_work_items WHERE request_id=? ORDER BY sequence_no", [request_id]))

    def work_item(self, item_id: str) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM request_work_items WHERE id=?", [item_id]))
        return items[0] if items else None

    def create_work_plan(
        self,
        request_id: str,
        request_type: dict[str, Any],
        owner: str,
        assigned_by: str,
        *,
        owner_user_id: str | None = None,
        source_type: str,
        source_reference: str,
        requested_by: str,
        statuses: dict[int, str] | None = None,
    ) -> dict[str, Any]:
        if self.work_plan(request_id):
            raise FileExistsError("REQUEST_WORK_PLAN_EXISTS")
        normalized_nodes = []
        for index, node in enumerate(request_type["default_workflow"]["nodes"], start=1):
            task_type = self.get_task_type(node["task_type_id"], int(node["task_type_version"]))
            if not task_type:
                raise LookupError("TASK_TYPE_NOT_FOUND")
            normalized_nodes.append(
                {
                    "node_key": node["node_key"],
                    "task_type_id": node["task_type_id"],
                    "task_type_version": int(node["task_type_version"]),
                    "depends_on": list(node.get("depends_on") or []),
                    "display_name": node.get("display_name") or task_type["display_name"],
                    "sequence_no": index,
                }
            )
        now = _utcnow()
        snapshot = {"nodes": normalized_nodes}
        self.conn.execute(
            """
            INSERT INTO request_work_plans
                (request_id, request_type_id, request_type_version, scenario_name,
                 source_type, source_reference, requested_by,
                 definition_snapshot_json, assigned_by, assigned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [request_id, request_type["id"], request_type["version"], request_type["display_name"], source_type, source_reference, requested_by, json.dumps(snapshot, ensure_ascii=False), assigned_by, now],
        )
        for node in normalized_nodes:
            status = (statuses or {}).get(node["sequence_no"], "READY" if node["sequence_no"] == 1 else "WAITING")
            started = status in {"IN_PROGRESS", "COMPLETED"}
            completed = status == "COMPLETED"
            self.conn.execute(
                """
                INSERT INTO request_work_items
                    (id, request_id, node_key, task_type_id, task_type_version, sequence_no,
                     display_name, status, owner, owner_user_id, started_by, started_at,
                     completed_by, completed_at, demo_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                [f"work-item-{uuid4().hex[:12]}", request_id, node["node_key"], node["task_type_id"], node["task_type_version"], node["sequence_no"], node["display_name"], status, owner, owner_user_id, "system" if started else None, now if started else None, "system" if completed else None, now if completed else None],
            )
        return self.work_plan(request_id)  # type: ignore[return-value]

    def insert_workflow_run(self, item: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO workflow_runs
            (id, name, request_id, request_type_id, request_type_version, definition_json,
             execution_mode, status, progress, created_by, created_at, started_at, completed_at,
             batch_attempt_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [item[key] for key in ("id", "name", "request_id", "request_type_id", "request_type_version", "definition_json", "execution_mode", "status", "progress", "created_by", "created_at", "started_at", "completed_at")] + [item.get("batch_attempt_id")],
        )

    def insert_task_run(self, item: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO task_runs
            (id, workflow_run_id, node_key, task_type_id, task_type_version, status, progress,
             depends_on_json, demo_artifact_url, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [item[key] for key in ("id", "workflow_run_id", "node_key", "task_type_id", "task_type_version", "status", "progress", "depends_on_json", "demo_artifact_url", "started_at", "completed_at")],
        )

    def insert_task_event(self, item: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO task_run_events
            (id, task_run_id, event_index, event_type, level, message, progress, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [item[key] for key in ("id", "task_run_id", "event_index", "event_type", "level", "message", "progress", "occurred_at")],
        )

    def list_workflow_runs(self, request_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM workflow_runs"
        params: list[Any] = []
        if request_id:
            sql += " WHERE request_id=?"
            params.append(request_id)
        sql += " ORDER BY created_at DESC, id DESC"
        return rows(self.conn.execute(sql, params))

    def get_workflow_run(self, run_id: str) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM workflow_runs WHERE id=?", [run_id]))
        return items[0] if items else None

    def workflow_run_by_batch_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM workflow_runs WHERE batch_attempt_id=?", [attempt_id]))
        return items[0] if items else None

    def task_runs(self, run_id: str) -> list[dict[str, Any]]:
        return rows(
            self.conn.execute(
                """
                SELECT tr.*, tt.kind, tt.display_name
                FROM task_runs tr
                JOIN task_type_versions tt ON tt.id=tr.task_type_id AND tt.version=tr.task_type_version
                WHERE tr.workflow_run_id=? ORDER BY tr.started_at, tr.node_key
                """,
                [run_id],
            )
        )

    def task_events(self, task_run_id: str) -> list[dict[str, Any]]:
        return rows(self.conn.execute("SELECT event_index, event_type, level, message, progress, occurred_at FROM task_run_events WHERE task_run_id=? ORDER BY event_index", [task_run_id]))

    @staticmethod
    def _batch_profile_item(item: dict[str, Any]) -> dict[str, Any]:
        environment = _decoded(item.pop("environment_json", None)) or {}
        legacy_ids = _decoded(item.pop("task_type_ids_json", None)) or []
        task_type_id = item.get("task_type_id")
        task_type_version = int(item.get("task_type_version") or 1)
        if not task_type_id and len(legacy_ids) == 1:
            task_type_id = legacy_ids[0]
        ids = [task_type_id] if task_type_id else list(legacy_ids)
        migration_required = not task_type_id and bool(legacy_ids)
        return {**item, "environment": environment, "task_type_id": task_type_id, "task_type_version": task_type_version, "task_type_ids": ids, "migration_required": migration_required}

    def list_batch_profiles(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM batch_path_profiles"
        if not include_inactive:
            sql += " WHERE is_active=true"
        sql += " ORDER BY name, id"
        return [self._batch_profile_item(item) for item in rows(self.conn.execute(sql))]

    def get_batch_profile(self, profile_id: str) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM batch_path_profiles WHERE id=?", [profile_id]))
        return self._batch_profile_item(items[0]) if items else None

    def get_batch_profile_for_task(self, task_type_id: str, task_type_version: int, *, include_inactive: bool = False) -> dict[str, Any] | None:
        sql = "SELECT * FROM batch_path_profiles WHERE task_type_id=? AND task_type_version=?"
        if not include_inactive:
            sql += " AND is_active=true"
        items = rows(self.conn.execute(sql + " ORDER BY version DESC LIMIT 1", [task_type_id, task_type_version]))
        return self._batch_profile_item(items[0]) if items else None

    def deactivate_batch_profile(self, profile_id: str) -> bool:
        updated = self.conn.execute("UPDATE batch_path_profiles SET is_active=false, updated_at=? WHERE id=?", [_utcnow(), profile_id])
        return bool(getattr(updated, "rowcount", 0)) or bool(self.conn.execute("SELECT 1 FROM batch_path_profiles WHERE id=?", [profile_id]).fetchone())

    def upsert_batch_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        legacy_ids = list(payload.get("task_type_ids") or [])
        task_type_id = payload.get("task_type_id")
        if task_type_id and legacy_ids and (len(legacy_ids) != 1 or legacy_ids[0] != task_type_id):
            raise ValueError("배치 실행 정의는 하나의 작업 유형만 연결할 수 있습니다.")
        if not task_type_id:
            if len(legacy_ids) != 1:
                raise ValueError("배치 실행 정의에는 task_type_id가 필요합니다.")
            task_type_id = legacy_ids[0]
        task_type_version = int(payload.get("task_type_version") or 1)
        task_type = self.get_task_type(task_type_id, task_type_version)
        if not task_type or not task_type["is_active"]:
            raise ValueError(f"활성 Task Type 버전을 찾을 수 없습니다: {task_type_id} v{task_type_version}")
        payload["task_type_id"] = task_type_id
        payload["task_type_version"] = task_type_version
        payload["task_type_ids"] = [task_type_id]
        payload["id"] = payload.get("id") or self._next_identifier("batch", payload.get("name", ""), "batch_path_profiles")
        conflict = self.conn.execute("SELECT id FROM batch_path_profiles WHERE task_type_id=? AND task_type_version=? AND id<>? LIMIT 1", [task_type_id, task_type_version, payload["id"]]).fetchone()
        if conflict:
            raise ValueError(f"작업 유형에 이미 배치 실행 정의가 연결되어 있습니다: {task_type_id} v{task_type_version}")
        now = _utcnow()
        existing = self.conn.execute("SELECT version, created_at FROM batch_path_profiles WHERE id=?", [payload["id"]]).fetchone()
        version = int(existing[0] or 1) + 1 if existing else 1
        if existing:
            self.conn.execute(
                """UPDATE batch_path_profiles SET version=?, name=?, solver_path=?, working_directory=?, arguments_template=?, environment_json=?, task_type_id=?, task_type_version=?, task_type_ids_json=?, is_active=?, updated_by=?, updated_at=? WHERE id=?""",
                [version, payload["name"], payload["solver_path"], payload["working_directory"], payload["arguments_template"], json.dumps(payload["environment"], ensure_ascii=False), task_type_id, task_type_version, json.dumps([task_type_id], ensure_ascii=False), payload["is_active"], payload["updated_by"], now, payload["id"]],
            )
        else:
            self.conn.execute(
                """INSERT INTO batch_path_profiles (id, version, name, solver_path, working_directory, arguments_template, environment_json, task_type_id, task_type_version, task_type_ids_json, is_active, updated_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [payload["id"], version, payload["name"], payload["solver_path"], payload["working_directory"], payload["arguments_template"], json.dumps(payload["environment"], ensure_ascii=False), task_type_id, task_type_version, json.dumps([task_type_id], ensure_ascii=False), payload["is_active"], payload["updated_by"], now, now],
            )
        self.conn.execute(
            """INSERT INTO batch_path_profile_versions (id, version, name, solver_path, working_directory, arguments_template, environment_json, task_type_id, task_type_version, task_type_ids_json, is_active, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [payload["id"], version, payload["name"], payload["solver_path"], payload["working_directory"], payload["arguments_template"], json.dumps(payload["environment"], ensure_ascii=False), task_type_id, task_type_version, json.dumps([task_type_id], ensure_ascii=False), payload["is_active"], payload["updated_by"], now],
        )
        return self.get_batch_profile(payload["id"])  # type: ignore[return-value]

    def list_batch_profile_versions(self, profile_id: str) -> list[dict[str, Any]]:
        items = rows(self.conn.execute("SELECT * FROM batch_path_profile_versions WHERE id=? ORDER BY version DESC", [profile_id]))
        return [self._batch_profile_item(item) for item in items]

    def insert_batch_dispatch(self, item: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO batch_dispatches
                (id, work_item_id, workflow_run_id, batch_profile_id, profile_snapshot_json, attempt_id,
                 command_preview, status, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [item[key] for key in ("id", "work_item_id", "workflow_run_id", "batch_profile_id", "profile_snapshot_json")] + [item.get("attempt_id")] + [item[key] for key in ("command_preview", "status", "created_by", "created_at")],
        )

    def batch_dispatch_for_run(self, run_id: str) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM batch_dispatches WHERE workflow_run_id=?", [run_id]))
        if not items:
            return None
        item = items[0]
        item["profile_snapshot"] = _decoded(item.pop("profile_snapshot_json")) or {}
        item.pop("attempt_id", None)
        return item

    @staticmethod
    def _batch_attempt_item(item: dict[str, Any], events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        item["profile_snapshot"] = _decoded(item.pop("profile_snapshot_json")) or {}
        # Recovery ownership is persistence-only.  Keep every established
        # attempt/run API projection stable, including administrator views.
        item.pop("recovery_lease_owner_id", None)
        item.pop("recovery_lease_token", None)
        item.pop("recovery_lease_generation", None)
        item.pop("recovery_lease_acquired_at", None)
        item.pop("recovery_lease_expires_at", None)
        item["events"] = events or []
        return item

    def insert_batch_attempt(self, item: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO batch_execution_attempts
                (id, work_item_id, workflow_run_id, batch_profile_id, batch_profile_version,
                 profile_snapshot_json, command_preview, idempotency_key, execution_mode,
                 status, progress, last_message, created_by, created_at, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [item[key] for key in (
                "id", "work_item_id", "workflow_run_id", "batch_profile_id", "batch_profile_version",
                "profile_snapshot_json", "command_preview", "idempotency_key", "execution_mode",
                "status", "progress", "last_message", "created_by", "created_at", "started_at", "completed_at",
            )],
        )

    def update_batch_attempt(self, attempt_id: str, *, workflow_run_id: str | None, status: str, progress: int, message: str, started_at: Any = None, completed_at: Any = None) -> None:
        self.conn.execute(
            """
            UPDATE batch_execution_attempts
            SET workflow_run_id=?, status=?, progress=?, last_message=?, started_at=?, completed_at=?
            WHERE id=?
            """,
            [workflow_run_id, status, progress, message, started_at, completed_at, attempt_id],
        )

    def link_batch_attempt_workflow_run(self, attempt_id: str, workflow_run_id: str) -> None:
        """Durably attach the runner record without changing attempt status."""
        self.conn.execute(
            """UPDATE batch_execution_attempts
            SET workflow_run_id=?
            WHERE id=? AND status='QUEUED' AND workflow_run_id IS NULL""",
            [workflow_run_id, attempt_id],
        )
        linked = self.conn.execute(
            "SELECT workflow_run_id FROM batch_execution_attempts WHERE id=?",
            [attempt_id],
        ).fetchone()
        if not linked or linked[0] != workflow_run_id:
            raise BatchAttemptWorkflowRunLinkConflictError(
                "batch attempt workflow run link conflicts with the deterministic identity"
            )

    def insert_batch_attempt_event(self, item: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO batch_execution_events
                (id, attempt_id, event_index, event_type, level, message, progress, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [item[key] for key in ("id", "attempt_id", "event_index", "event_type", "level", "message", "progress", "occurred_at")],
        )

    def batch_attempt_by_key(self, work_item_id: str, idempotency_key: str) -> dict[str, Any] | None:
        items = rows(self.conn.execute(
            "SELECT * FROM batch_execution_attempts WHERE work_item_id=? AND idempotency_key=?",
            [work_item_id, idempotency_key],
        ))
        if not items:
            return None
        events = rows(self.conn.execute("SELECT * FROM batch_execution_events WHERE attempt_id=? ORDER BY event_index", [items[0]["id"]]))
        return self._batch_attempt_item(items[0], events)

    def batch_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM batch_execution_attempts WHERE id=?", [attempt_id]))
        if not items:
            return None
        events = rows(self.conn.execute("SELECT * FROM batch_execution_events WHERE attempt_id=? ORDER BY event_index", [attempt_id]))
        return self._batch_attempt_item(items[0], events)

    def list_batch_attempts(self, work_item_id: str) -> list[dict[str, Any]]:
        items = rows(self.conn.execute(
            "SELECT * FROM batch_execution_attempts WHERE work_item_id=? ORDER BY created_at DESC, id DESC",
            [work_item_id],
        ))
        if not items:
            return []
        attempt_ids = [item["id"] for item in items]
        placeholders = ",".join("?" for _ in attempt_ids)
        event_rows = rows(self.conn.execute(
            f"SELECT * FROM batch_execution_events WHERE attempt_id IN ({placeholders}) ORDER BY attempt_id, event_index",
            attempt_ids,
        ))
        events_by_attempt: dict[str, list[dict[str, Any]]] = {}
        for event in event_rows:
            events_by_attempt.setdefault(event["attempt_id"], []).append(event)
        return [self._batch_attempt_item(item, events_by_attempt.get(item["id"], [])) for item in items]

    def batch_attempt_for_run(self, run_id: str) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM batch_execution_attempts WHERE workflow_run_id=?", [run_id]))
        if not items:
            return None
        events = rows(self.conn.execute("SELECT * FROM batch_execution_events WHERE attempt_id=? ORDER BY event_index", [items[0]["id"]]))
        return self._batch_attempt_item(items[0], events)
