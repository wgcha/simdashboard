from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..database_connection import rows


DEMO_ARTIFACT_URL = "/assets/demo-workbench.svg"

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


def _decoded(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


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
        ("request-drop-001", "design-reliability-validation", {1: "COMPLETED", 2: "COMPLETED", 3: "IN_PROGRESS"}),
        ("request-clamp-001", "design-doe-exploration", {1: "COMPLETED", 2: "IN_PROGRESS"}),
    )
    for request_id, request_type_id, statuses in seeds:
        if not repository.analysis_request_exists(request_id) or repository.work_plan(request_id):
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
            source_type="DEPARTMENT_HEAD",
            source_reference="기존 데모 시드",
            requested_by="system",
            statuses=statuses,
        )
        conn.execute("UPDATE analysis_requests SET status='IN_PROGRESS' WHERE id=?", [request_id])


class WorkbenchRepository:
    def __init__(self, conn: Any):
        self.conn = conn

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
        return {
            **item,
            "allowed_task_types": allowed,
            "default_workflow": workflow,
            "match_rules": rules,
        }

    def list_task_types(self, *, all_versions: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM task_type_versions"
        if not all_versions:
            sql += " WHERE is_active=true AND (id, version) IN (SELECT id, max(version) FROM task_type_versions WHERE is_active=true GROUP BY id)"
        sql += " ORDER BY display_name, version DESC"
        return [self._task_item(item) for item in rows(self.conn.execute(sql))]

    def get_task_type(self, task_type_id: str, version: int) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM task_type_versions WHERE id=? AND version=?", [task_type_id, version]))
        return self._task_item(items[0]) if items else None

    def create_task_type_version(self, payload: dict[str, Any]) -> dict[str, Any]:
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

    def list_request_types(self, *, all_versions: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM request_type_versions"
        if not all_versions:
            sql += " WHERE is_active=true AND (id, version) IN (SELECT id, max(version) FROM request_type_versions WHERE is_active=true GROUP BY id)"
        sql += " ORDER BY display_name, version DESC"
        return [self._request_item(item) for item in rows(self.conn.execute(sql))]

    def get_request_type(self, request_type_id: str, version: int) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM request_type_versions WHERE id=? AND version=?", [request_type_id, version]))
        return self._request_item(items[0]) if items else None

    def latest_request_type(self, request_type_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT max(version) FROM request_type_versions WHERE id=? AND is_active=true",
            [request_type_id],
        ).fetchone()
        return self.get_request_type(request_type_id, int(row[0])) if row and row[0] is not None else None

    def create_request_type_version(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.conn.execute("SELECT COALESCE(max(version), 0) FROM request_type_versions WHERE id=?", [payload["id"]]).fetchone()[0]
        version = int(current) + 1
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
        return self.get_request_type(payload["id"], version)  # type: ignore[return-value]

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
        exact_keys = {"project_id", "status", "owner", "analysis_type", "product_name"}
        for key, expected in rules.items():
            if key in exact_keys:
                actual = str(context.get(key) or "")
                accepted = expected if isinstance(expected, list) else [expected]
                if actual.casefold() not in {str(value).casefold() for value in accepted}:
                    return False
            elif key == "title_contains":
                if str(expected).casefold() not in str(context.get("title") or "").casefold():
                    return False
            elif key == "overall_note_contains":
                if str(expected).casefold() not in str(context.get("overall_note") or "").casefold():
                    return False
            else:
                return False
        return True

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
             execution_mode, status, progress, created_by, created_at, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [item[key] for key in ("id", "name", "request_id", "request_type_id", "request_type_version", "definition_json", "execution_mode", "status", "progress", "created_by", "created_at", "started_at", "completed_at")],
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
        environment = _decoded(item.pop("environment_json")) or {}
        task_type_ids = _decoded(item.pop("task_type_ids_json")) or []
        return {**item, "environment": environment, "task_type_ids": task_type_ids}

    def list_batch_profiles(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM batch_path_profiles"
        if not include_inactive:
            sql += " WHERE is_active=true"
        sql += " ORDER BY name, id"
        return [self._batch_profile_item(item) for item in rows(self.conn.execute(sql))]

    def get_batch_profile(self, profile_id: str) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM batch_path_profiles WHERE id=?", [profile_id]))
        return self._batch_profile_item(items[0]) if items else None

    def upsert_batch_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = _utcnow()
        existing = self.conn.execute("SELECT version, created_at FROM batch_path_profiles WHERE id=?", [payload["id"]]).fetchone()
        version = int(existing[0] or 1) + 1 if existing else 1
        if existing:
            self.conn.execute(
                """
                UPDATE batch_path_profiles
                SET version=?, name=?, solver_path=?, working_directory=?, arguments_template=?,
                    environment_json=?, task_type_ids_json=?, is_active=?, updated_by=?, updated_at=?
                WHERE id=?
                """,
                [version, payload["name"], payload["solver_path"], payload["working_directory"], payload["arguments_template"], json.dumps(payload["environment"], ensure_ascii=False), json.dumps(payload["task_type_ids"], ensure_ascii=False), payload["is_active"], payload["updated_by"], now, payload["id"]],
            )
        else:
            self.conn.execute(
                """
                INSERT INTO batch_path_profiles
                    (id, version, name, solver_path, working_directory, arguments_template,
                     environment_json, task_type_ids_json, is_active, updated_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [payload["id"], version, payload["name"], payload["solver_path"], payload["working_directory"], payload["arguments_template"], json.dumps(payload["environment"], ensure_ascii=False), json.dumps(payload["task_type_ids"], ensure_ascii=False), payload["is_active"], payload["updated_by"], now, now],
            )
        self.conn.execute(
            """
            INSERT INTO batch_path_profile_versions
                (id, version, name, solver_path, working_directory, arguments_template,
                 environment_json, task_type_ids_json, is_active, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [payload["id"], version, payload["name"], payload["solver_path"], payload["working_directory"], payload["arguments_template"], json.dumps(payload["environment"], ensure_ascii=False), json.dumps(payload["task_type_ids"], ensure_ascii=False), payload["is_active"], payload["updated_by"], now],
        )
        return self.get_batch_profile(payload["id"])  # type: ignore[return-value]

    def list_batch_profile_versions(self, profile_id: str) -> list[dict[str, Any]]:
        items = rows(self.conn.execute("SELECT * FROM batch_path_profile_versions WHERE id=? ORDER BY version DESC", [profile_id]))
        return [self._batch_profile_item(item) for item in items]

    def insert_batch_dispatch(self, item: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO batch_dispatches
                (id, work_item_id, workflow_run_id, batch_profile_id, profile_snapshot_json,
                 command_preview, status, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [item[key] for key in ("id", "work_item_id", "workflow_run_id", "batch_profile_id", "profile_snapshot_json", "command_preview", "status", "created_by", "created_at")],
        )

    def batch_dispatch_for_run(self, run_id: str) -> dict[str, Any] | None:
        items = rows(self.conn.execute("SELECT * FROM batch_dispatches WHERE workflow_run_id=?", [run_id]))
        if not items:
            return None
        item = items[0]
        item["profile_snapshot"] = _decoded(item.pop("profile_snapshot_json")) or {}
        return item

    @staticmethod
    def _batch_attempt_item(item: dict[str, Any], events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        item["profile_snapshot"] = _decoded(item.pop("profile_snapshot_json")) or {}
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
