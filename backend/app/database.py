from __future__ import annotations

import json
import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import duckdb

from .config import database_settings
from .database_connection import connect, rows


def initialize_database() -> None:
    settings = database_settings()
    if settings.backend == "postgresql":
        with connect() as conn:
            migrated = conn.execute("SELECT to_regclass('public.workspace_layouts')").fetchone()[0]
            if migrated is None:
                raise RuntimeError("PostgreSQL 스키마가 준비되지 않았습니다. 먼저 alembic upgrade head를 실행하세요.")
            workbench_migrated = conn.execute("SELECT to_regclass('public.task_type_versions')").fetchone()[0]
            if workbench_migrated is None:
                raise RuntimeError("워크벤치 스키마가 준비되지 않았습니다. alembic upgrade head를 실행하세요.")
            from .repositories.workbench import ensure_default_workbench_catalog

            ensure_default_workbench_catalog(conn)
            ensure_system_analysis_page_metadata(conn)
        return
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                product_name VARCHAR NOT NULL,
                description VARCHAR,
                created_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS product_information (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                value_text VARCHAR,
                file_path VARCHAR,
                metadata_json JSON
            );

            CREATE TABLE IF NOT EXISTS analysis_requests (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                owner VARCHAR,
                requested_at TIMESTAMP NOT NULL,
                due_at TIMESTAMP,
                overall_note VARCHAR
            );

            CREATE TABLE IF NOT EXISTS request_steps (
                id VARCHAR PRIMARY KEY,
                request_id VARCHAR NOT NULL,
                sequence_no INTEGER NOT NULL,
                name VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                owner VARCHAR,
                planned_start TIMESTAMP,
                planned_end TIMESTAMP,
                actual_start TIMESTAMP,
                actual_end TIMESTAMP,
                progress INTEGER NOT NULL,
                is_optional BOOLEAN NOT NULL DEFAULT false,
                blocked_reason VARCHAR,
                note VARCHAR
            );

            CREATE TABLE IF NOT EXISTS load_cases (
                id VARCHAR PRIMARY KEY,
                request_id VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                analysis_type VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                parameters_json JSON NOT NULL,
                created_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS template_executions (
                id VARCHAR PRIMARY KEY,
                load_case_id VARCHAR NOT NULL,
                template_name VARCHAR NOT NULL,
                template_version VARCHAR NOT NULL,
                input_json JSON NOT NULL,
                generated_model_json JSON,
                status VARCHAR NOT NULL,
                executed_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analysis_runs (
                id VARCHAR PRIMARY KEY,
                load_case_id VARCHAR NOT NULL,
                template_execution_id VARCHAR,
                run_no INTEGER NOT NULL,
                solver VARCHAR,
                status VARCHAR NOT NULL,
                started_at TIMESTAMP,
                completed_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scalar_results (
                id VARCHAR PRIMARY KEY,
                analysis_run_id VARCHAR NOT NULL,
                variable_key VARCHAR NOT NULL,
                display_name VARCHAR NOT NULL,
                value_double DOUBLE,
                value_integer BIGINT,
                value_text VARCHAR,
                unit VARCHAR,
                threshold_double DOUBLE,
                verdict VARCHAR
            );

            CREATE TABLE IF NOT EXISTS time_series_results (
                analysis_run_id VARCHAR NOT NULL,
                variable_key VARCHAR NOT NULL,
                display_name VARCHAR NOT NULL,
                time_value DOUBLE NOT NULL,
                value DOUBLE NOT NULL,
                time_unit VARCHAR NOT NULL,
                value_unit VARCHAR NOT NULL
            );

            CREATE TABLE IF NOT EXISTS curve_results (
                id VARCHAR PRIMARY KEY,
                analysis_run_id VARCHAR NOT NULL,
                variable_key VARCHAR NOT NULL,
                display_name VARCHAR NOT NULL,
                series_key VARCHAR NOT NULL DEFAULT 'default',
                x_label VARCHAR NOT NULL,
                x_unit VARCHAR NOT NULL,
                y_label VARCHAR NOT NULL,
                y_unit VARCHAR NOT NULL,
                point_count INTEGER NOT NULL,
                source_file VARCHAR,
                source_checksum VARCHAR,
                created_at TIMESTAMP NOT NULL,
                UNIQUE(analysis_run_id, variable_key, series_key)
            );

            CREATE TABLE IF NOT EXISTS curve_points (
                curve_id VARCHAR NOT NULL,
                point_index INTEGER NOT NULL,
                x_value DOUBLE NOT NULL,
                y_value DOUBLE NOT NULL,
                PRIMARY KEY(curve_id, point_index)
            );

            CREATE TABLE IF NOT EXISTS result_locations (
                analysis_run_id VARCHAR NOT NULL,
                variable_key VARCHAR NOT NULL,
                entity_type VARCHAR NOT NULL,
                entity_id VARCHAR NOT NULL,
                x DOUBLE NOT NULL,
                y DOUBLE NOT NULL,
                z DOUBLE NOT NULL,
                time_value DOUBLE NOT NULL,
                time_unit VARCHAR NOT NULL,
                method VARCHAR NOT NULL
            );

            CREATE TABLE IF NOT EXISTS qualitative_notes (
                id VARCHAR PRIMARY KEY,
                analysis_run_id VARCHAR NOT NULL,
                author VARCHAR NOT NULL,
                body VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS media_assets (
                id VARCHAR PRIMARY KEY,
                analysis_run_id VARCHAR NOT NULL,
                asset_type VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                file_path VARCHAR NOT NULL,
                mime_type VARCHAR NOT NULL,
                file_size BIGINT,
                checksum VARCHAR,
                metadata_json JSON
            );

            CREATE TABLE IF NOT EXISTS folder_import_jobs (
                id VARCHAR PRIMARY KEY,
                load_case_id VARCHAR NOT NULL,
                analysis_run_id VARCHAR,
                schema_id VARCHAR NOT NULL,
                schema_version INTEGER NOT NULL,
                source_folder VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                summary_json JSON,
                created_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analysis_run_metadata (
                analysis_run_id VARCHAR PRIMARY KEY,
                source_type VARCHAR NOT NULL,
                source_name VARCHAR,
                source_checksum VARCHAR,
                schema_id VARCHAR,
                schema_version INTEGER,
                parser_version VARCHAR NOT NULL,
                metadata_json JSON,
                created_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS import_schemas (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                description VARCHAR,
                definition_json JSON NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                updated_by VARCHAR NOT NULL
            );

            CREATE TABLE IF NOT EXISTS import_schema_versions (
                schema_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                definition_json JSON NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_by VARCHAR NOT NULL,
                PRIMARY KEY(schema_id, version)
            );

            CREATE TABLE IF NOT EXISTS validations (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                request_id VARCHAR,
                load_case_id VARCHAR,
                analysis_run_id VARCHAR,
                validation_type VARCHAR NOT NULL,
                verdict VARCHAR,
                sensor_json JSON,
                ai_analysis_json JSON,
                created_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS result_bookmarks (
                id VARCHAR PRIMARY KEY,
                analysis_run_id VARCHAR NOT NULL,
                variable_key VARCHAR,
                time_value DOUBLE,
                entity_type VARCHAR,
                entity_id VARCHAR,
                title VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS review_annotations (
                id VARCHAR PRIMARY KEY,
                bookmark_id VARCHAR NOT NULL,
                analysis_run_id VARCHAR NOT NULL,
                variable_key VARCHAR,
                body VARCHAR NOT NULL,
                review_status VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quality_thresholds (
                criterion_key VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                analysis_key VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                threshold_double DOUBLE NOT NULL,
                unit VARCHAR NOT NULL,
                updated_by VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS variable_definitions (
                id VARCHAR PRIMARY KEY,
                load_case_id VARCHAR NOT NULL,
                variable_key VARCHAR NOT NULL,
                display_name VARCHAR NOT NULL,
                data_type VARCHAR NOT NULL,
                unit VARCHAR NOT NULL,
                description VARCHAR,
                filterable BOOLEAN NOT NULL DEFAULT true,
                source VARCHAR NOT NULL,
                threshold_double DOUBLE,
                allowed_widgets_json JSON NOT NULL,
                allowed_aggregations_json JSON NOT NULL,
                analysis_type VARCHAR NOT NULL,
                result_group VARCHAR NOT NULL DEFAULT 'CUSTOM',
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                updated_by VARCHAR NOT NULL,
                UNIQUE(load_case_id, variable_key)
            );

            CREATE TABLE IF NOT EXISTS dashboards (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                request_id VARCHAR,
                load_case_id VARCHAR,
                name VARCHAR NOT NULL,
                description VARCHAR,
                version INTEGER NOT NULL,
                definition_json JSON NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dashboard_versions (
                dashboard_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                definition_json JSON NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                is_valid BOOLEAN NOT NULL DEFAULT true,
                PRIMARY KEY (dashboard_id, version)
            );

            CREATE TABLE IF NOT EXISTS workspace_layouts (
                layout_kind VARCHAR PRIMARY KEY,
                version INTEGER NOT NULL,
                definition_json JSON NOT NULL,
                updated_by VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workspace_layout_versions (
                layout_kind VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                definition_json JSON NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                is_valid BOOLEAN NOT NULL DEFAULT true,
                PRIMARY KEY (layout_kind, version)
            );

            CREATE TABLE IF NOT EXISTS report_layouts (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                description VARCHAR,
                version INTEGER NOT NULL,
                definition_json JSON NOT NULL,
                is_system BOOLEAN NOT NULL DEFAULT false,
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                updated_by VARCHAR NOT NULL
            );

            CREATE TABLE IF NOT EXISTS report_layout_versions (
                layout_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                definition_json JSON NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                is_valid BOOLEAN NOT NULL DEFAULT true,
                PRIMARY KEY (layout_id, version)
            );

            CREATE TABLE IF NOT EXISTS report_template_assets (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                filename VARCHAR NOT NULL,
                file_path VARCHAR NOT NULL,
                slide_count INTEGER NOT NULL,
                definition_json JSON NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                updated_by VARCHAR NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id VARCHAR PRIMARY KEY,
                username VARCHAR NOT NULL UNIQUE,
                password_hash VARCHAR NOT NULL,
                display_name VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id VARCHAR PRIMARY KEY,
                occurred_at TIMESTAMP NOT NULL,
                user_id VARCHAR,
                username VARCHAR,
                role VARCHAR,
                action VARCHAR NOT NULL,
                method VARCHAR NOT NULL,
                path VARCHAR NOT NULL,
                status_code INTEGER NOT NULL,
                request_id VARCHAR NOT NULL,
                client_ip VARCHAR,
                user_agent VARCHAR,
                detail_json JSON
            );

            CREATE TABLE IF NOT EXISTS task_type_versions (
                id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                kind VARCHAR NOT NULL,
                display_name VARCHAR NOT NULL,
                description VARCHAR NOT NULL,
                supports_standalone BOOLEAN NOT NULL DEFAULT true,
                input_artifact_types_json JSON NOT NULL,
                output_artifact_types_json JSON NOT NULL,
                parameter_schema_json JSON NOT NULL,
                demo_artifact_url VARCHAR NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY (id, version)
            );

            CREATE TABLE IF NOT EXISTS request_type_versions (
                id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                display_name VARCHAR NOT NULL,
                description VARCHAR NOT NULL,
                allowed_task_types_json JSON NOT NULL,
                default_workflow_json JSON NOT NULL,
                match_rules_json JSON NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY (id, version)
            );

            CREATE TABLE IF NOT EXISTS analysis_request_type_assignments (
                request_id VARCHAR PRIMARY KEY,
                request_type_id VARCHAR NOT NULL,
                request_type_version INTEGER NOT NULL,
                source VARCHAR NOT NULL,
                rule_snapshot_json JSON NOT NULL,
                decided_by VARCHAR NOT NULL,
                decided_at TIMESTAMP NOT NULL,
                CHECK (source IN ('ADMIN', 'RULE', 'USER', 'DEFAULT'))
            );

            CREATE TABLE IF NOT EXISTS request_work_plans (
                request_id VARCHAR PRIMARY KEY,
                request_type_id VARCHAR NOT NULL,
                request_type_version INTEGER NOT NULL,
                scenario_name VARCHAR NOT NULL,
                source_type VARCHAR NOT NULL,
                source_reference VARCHAR NOT NULL,
                requested_by VARCHAR NOT NULL,
                definition_snapshot_json JSON NOT NULL,
                assigned_by VARCHAR NOT NULL,
                assigned_at TIMESTAMP NOT NULL,
                CHECK (source_type IN ('EXTERNAL_SYSTEM', 'DEPARTMENT_HEAD'))
            );

            CREATE TABLE IF NOT EXISTS request_work_items (
                id VARCHAR PRIMARY KEY,
                request_id VARCHAR NOT NULL,
                node_key VARCHAR NOT NULL,
                task_type_id VARCHAR NOT NULL,
                task_type_version INTEGER NOT NULL,
                sequence_no INTEGER NOT NULL,
                display_name VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                progress_updated_by VARCHAR,
                progress_updated_at TIMESTAMP,
                owner VARCHAR NOT NULL,
                started_by VARCHAR,
                started_at TIMESTAMP,
                completed_by VARCHAR,
                completed_at TIMESTAMP,
                demo_run_id VARCHAR,
                UNIQUE (request_id, sequence_no),
                UNIQUE (request_id, node_key),
                CHECK (status IN ('READY', 'IN_PROGRESS', 'WAITING', 'COMPLETED'))
            );

            CREATE TABLE IF NOT EXISTS workflow_runs (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                request_id VARCHAR,
                request_type_id VARCHAR,
                request_type_version INTEGER,
                definition_json JSON NOT NULL,
                execution_mode VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                progress INTEGER NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                CHECK (execution_mode = 'DEMO_ONLY'),
                CHECK (progress BETWEEN 0 AND 100)
            );

            CREATE TABLE IF NOT EXISTS task_runs (
                id VARCHAR PRIMARY KEY,
                workflow_run_id VARCHAR NOT NULL,
                node_key VARCHAR NOT NULL,
                task_type_id VARCHAR NOT NULL,
                task_type_version INTEGER NOT NULL,
                status VARCHAR NOT NULL,
                progress INTEGER NOT NULL,
                depends_on_json JSON NOT NULL,
                demo_artifact_url VARCHAR NOT NULL,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                UNIQUE (workflow_run_id, node_key),
                CHECK (progress BETWEEN 0 AND 100)
            );

            CREATE TABLE IF NOT EXISTS task_run_events (
                id VARCHAR PRIMARY KEY,
                task_run_id VARCHAR NOT NULL,
                event_index INTEGER NOT NULL,
                event_type VARCHAR NOT NULL,
                level VARCHAR NOT NULL,
                message VARCHAR NOT NULL,
                progress INTEGER NOT NULL,
                occurred_at TIMESTAMP NOT NULL,
                UNIQUE (task_run_id, event_index),
                CHECK (progress BETWEEN 0 AND 100)
            );

            CREATE TABLE IF NOT EXISTS batch_path_profiles (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                solver_path VARCHAR NOT NULL,
                working_directory VARCHAR NOT NULL,
                arguments_template VARCHAR NOT NULL,
                environment_json JSON NOT NULL,
                task_type_ids_json JSON NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT true,
                updated_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS batch_dispatches (
                id VARCHAR PRIMARY KEY,
                work_item_id VARCHAR NOT NULL,
                workflow_run_id VARCHAR NOT NULL UNIQUE,
                batch_profile_id VARCHAR NOT NULL,
                profile_snapshot_json JSON NOT NULL,
                command_preview VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                CHECK (status = 'RECORDED_DEMO')
            );
            """
        )

        conn.execute("ALTER TABLE request_steps ADD COLUMN IF NOT EXISTS is_optional BOOLEAN DEFAULT false")
        conn.execute("ALTER TABLE request_work_items ADD COLUMN IF NOT EXISTS progress INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE request_work_items ADD COLUMN IF NOT EXISTS progress_updated_by VARCHAR")
        conn.execute("ALTER TABLE request_work_items ADD COLUMN IF NOT EXISTS progress_updated_at TIMESTAMP")
        conn.execute("ALTER TABLE batch_path_profiles ADD COLUMN IF NOT EXISTS task_type_ids_json JSON DEFAULT '[]'")
        conn.execute("UPDATE request_work_items SET progress=CASE WHEN status='COMPLETED' THEN 100 ELSE COALESCE(progress, 0) END")

        ensure_default_content(conn)


def seed_current_database() -> None:
    with connect() as conn:
        ensure_default_content(conn)


def ensure_default_content(conn: Any) -> None:
    from .repositories.workbench import ensure_default_workbench_catalog, ensure_seed_request_work_plans

    count = conn.execute("SELECT count(*) FROM projects").fetchone()[0]
    if count == 0:
        conn.execute("BEGIN TRANSACTION")
        try:
            seed_database(conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    ensure_sample_evolutions(conn)
    ensure_drop_video_widget(conn)
    ensure_feature_examples(conn)
    ensure_variable_definitions(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO dashboard_versions
        SELECT id, version, definition_json, 'system', updated_at, true FROM dashboards
        """
    )
    ensure_report_layouts(conn)
    ensure_workspace_layouts(conn)
    ensure_default_workbench_catalog(conn)
    ensure_seed_request_work_plans(conn)
    chassis_layout = {
        "id": "dashboard-chassis-default",
        "name": "Chassis Rear 영구변형 기본 분석",
        "description": "엣지 이격과 모서리 영구변형 평가",
        "page": {
            "kind": "analysis_page",
            "analysis_key": "chassis_rear",
            "status": "published",
            "display_order": 20,
            "is_system": True,
        },
        "widgets": [
            {"id": "chassis-summary", "type": "chassis_summary", "title": "영구변형 판정 요약", "x": 0, "y": 0, "w": 12, "h": 2, "settings": {}},
            {"id": "chassis-map", "type": "chassis_diagram", "title": "Chassis Rear 변형 위치", "x": 0, "y": 2, "w": 7, "h": 5, "settings": {}},
            {"id": "chassis-bar", "type": "chassis_bar", "title": "엣지·모서리 영구변형 비교", "x": 7, "y": 2, "w": 5, "h": 5, "settings": {"showThreshold": True}},
            {"id": "chassis-table", "type": "chassis_table", "title": "영구변형 상세 결과", "x": 0, "y": 7, "w": 8, "h": 4, "settings": {}},
            {"id": "chassis-note", "type": "note", "title": "수행자 의견", "x": 8, "y": 7, "w": 4, "h": 3, "settings": {}},
        ],
    }
    encoded_chassis = json.dumps(chassis_layout, ensure_ascii=False)
    now = _iso(datetime.now(timezone.utc))
    conn.execute("INSERT OR IGNORE INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [chassis_layout["id"], "project-tv-001", "request-drop-001", "loadcase-drop-bottom-001", chassis_layout["name"], chassis_layout["description"], 1, encoded_chassis, now])
    conn.execute("INSERT OR IGNORE INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)", [chassis_layout["id"], 1, encoded_chassis, "system", now, True])
    ensure_system_run_comparison_dashboard(conn)
    ensure_system_analysis_page_metadata(conn)


def _run_comparison_dashboard_definition() -> dict[str, Any]:
    return {
        "id": "dashboard-run-comparison-default",
        "name": "Run 비교·검토",
        "description": "기준 Run과 대상 Run의 결과 차이, 신뢰도와 검토 의견",
        "page": {
            "kind": "analysis_page",
            "analysis_key": "run_comparison",
            "status": "published",
            "display_order": 30,
            "is_system": True,
        },
        "widgets": [
            {
                "id": "run-comparison-panel",
                "type": "run_comparison",
                "title": "Run 비교·검토",
                "x": 0,
                "y": 0,
                "w": 12,
                "h": 12,
                "settings": {"includeInReport": True},
            }
        ],
    }


def ensure_system_run_comparison_dashboard(conn: Any) -> None:
    dashboard_id = "dashboard-run-comparison-default"
    if conn.execute("SELECT 1 FROM dashboards WHERE id = ?", [dashboard_id]).fetchone():
        return
    if not conn.execute("SELECT 1 FROM load_cases WHERE id = ?", ["loadcase-drop-bottom-001"]).fetchone():
        return
    comparison_layout = _run_comparison_dashboard_definition()
    encoded_comparison = json.dumps(comparison_layout, ensure_ascii=False)
    now = _iso(datetime.now(timezone.utc))
    conn.execute(
        "INSERT OR IGNORE INTO dashboards (id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [dashboard_id, "project-tv-001", "request-drop-001", "loadcase-drop-bottom-001", comparison_layout["name"], comparison_layout["description"], 1, encoded_comparison, now],
    )
    conn.execute(
        "INSERT OR IGNORE INTO dashboard_versions (dashboard_id, version, definition_json, created_by, created_at, is_valid) VALUES (?, ?, ?, ?, ?, ?)",
        [dashboard_id, 1, encoded_comparison, "system", now, True],
    )


def ensure_system_analysis_page_metadata(conn: Any) -> None:
    ensure_system_run_comparison_dashboard(conn)
    page_definitions = {
        "dashboard-drop-default": {
            "kind": "analysis_page",
            "analysis_key": "open_cell",
            "status": "published",
            "display_order": 10,
            "is_system": True,
        },
        "dashboard-chassis-default": {
            "kind": "analysis_page",
            "analysis_key": "chassis_rear",
            "status": "published",
            "display_order": 20,
            "is_system": True,
        },
        "dashboard-run-comparison-default": {
            "kind": "analysis_page",
            "analysis_key": "run_comparison",
            "status": "published",
            "display_order": 30,
            "is_system": True,
        },
    }
    for dashboard_id, page in page_definitions.items():
        stored = conn.execute("SELECT definition_json, version FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not stored:
            continue
        definition = json_value(stored[0]) or {}
        if definition.get("page") == page:
            continue
        definition["page"] = page
        next_version = int(stored[1]) + 1
        now = _iso(datetime.now(timezone.utc))
        encoded = json.dumps(definition, ensure_ascii=False)
        conn.execute(
            "UPDATE dashboards SET definition_json = ?, version = ?, updated_at = ? WHERE id = ?",
            [encoded, next_version, now, dashboard_id],
        )
        conn.execute(
            "INSERT OR IGNORE INTO dashboard_versions VALUES (?, ?, ?, 'system-analysis-page-backfill', ?, true)",
            [dashboard_id, next_version, encoded, now],
        )


def ensure_workspace_layouts(conn: duckdb.DuckDBPyConnection) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    layouts = {
        "portfolio": {"fontSize": 10, "chartOrder": ["trend", "status", "quality", "type"]},
        "workflow": {"fontSize": 10, "accentColor": "#50d5ff", "items": []},
    }
    for kind, definition in layouts.items():
        encoded = json.dumps(definition, ensure_ascii=False)
        conn.execute(
            "INSERT OR IGNORE INTO workspace_layouts VALUES (?, 1, ?, 'system', ?)",
            [kind, encoded, now],
        )
        conn.execute(
            "INSERT OR IGNORE INTO workspace_layout_versions VALUES (?, 1, ?, 'system', ?, true)",
            [kind, encoded, now],
        )


def ensure_drop_video_widget(conn: Any) -> None:
    stored = conn.execute(
        "SELECT definition_json, version FROM dashboards WHERE id = ?",
        ["dashboard-drop-default"],
    ).fetchone()
    if not stored:
        return
    definition = json_value(stored[0]) or {}
    widgets = definition.get("widgets", [])
    if any(widget.get("type") == "video_grid" for widget in widgets):
        return
    history = conn.execute(
        "SELECT definition_json FROM dashboard_versions WHERE dashboard_id = ?",
        ["dashboard-drop-default"],
    ).fetchall()
    if any(
        any(widget.get("type") == "video_grid" for widget in (json_value(item[0]) or {}).get("widgets", []))
        for item in history
    ):
        return
    widgets.append(
        {
            "id": "drop-video-grid",
            "type": "video_grid",
            "title": "낙하 해석 영상 비교",
            "x": 0,
            "y": 16,
            "w": 12,
            "h": 10,
            "settings": {"pageSize": 20},
        }
    )
    next_version = stored[1] + 1
    now = _iso(datetime.now(timezone.utc))
    encoded = json.dumps(definition, ensure_ascii=False)
    conn.execute(
        "UPDATE dashboards SET definition_json = ?, version = ?, updated_at = ? WHERE id = ?",
        [encoded, next_version, now, "dashboard-drop-default"],
    )
    conn.execute(
        "INSERT OR IGNORE INTO dashboard_versions VALUES (?, ?, ?, 'system-video-grid-backfill', ?, true)",
        ["dashboard-drop-default", next_version, encoded, now],
    )


def ensure_report_layouts(conn: duckdb.DuckDBPyConnection) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    layouts = [
        {
            "id": "report-layout-standard",
            "name": "표준 검토 보고서",
            "description": "요약, 시간 이력, 정량 결과, 미디어 순서의 기본 형식",
            "coverVariant": "balanced",
            "accentColor": "1898D5",
            "sectionOrder": ["series", "scalar", "media"],
            "variablePlacements": [],
            "includeMedia": True,
        },
        {
            "id": "report-layout-executive",
            "name": "경영진 요약 보고서",
            "description": "결론과 판정을 먼저 강조하는 간결한 형식",
            "coverVariant": "executive",
            "accentColor": "16B8D4",
            "sectionOrder": ["scalar", "series", "media"],
            "variablePlacements": [],
            "includeMedia": True,
        },
        {
            "id": "report-layout-evidence",
            "name": "상세 근거 보고서",
            "description": "변수별 시간 이력과 근거 자료를 먼저 배치하는 형식",
            "coverVariant": "evidence",
            "accentColor": "FF9948",
            "sectionOrder": ["series", "media", "scalar"],
            "variablePlacements": [],
            "includeMedia": True,
        },
    ]
    for layout in layouts:
        encoded = json.dumps(layout, ensure_ascii=False)
        conn.execute(
            "INSERT OR IGNORE INTO report_layouts VALUES (?, ?, ?, 1, ?, true, true, ?, ?, 'system')",
            [layout["id"], layout["name"], layout["description"], encoded, now, now],
        )
        conn.execute(
            "INSERT OR IGNORE INTO report_layout_versions VALUES (?, 1, ?, 'system', ?, true)",
            [layout["id"], encoded, now],
        )


def ensure_variable_definitions(conn: duckdb.DuckDBPyConnection) -> None:
    """Backfill the editable semantic catalog from existing result tables."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    scalar_rows = conn.execute(
        """
        SELECT run.load_case_id, sr.variable_key, min(sr.display_name), min(sr.unit),
               max(sr.threshold_double), min(lc.analysis_type)
        FROM scalar_results sr
        JOIN analysis_runs run ON run.id = sr.analysis_run_id
        JOIN load_cases lc ON lc.id = run.load_case_id
        GROUP BY run.load_case_id, sr.variable_key
        """
    ).fetchall()
    for load_case_id, key, display_name, unit, threshold, analysis_type in scalar_rows:
        result_group = "CHASSIS_REAR" if key.startswith("chassis_rear_") else "OPEN_CELL"
        conn.execute(
            """
            INSERT OR IGNORE INTO variable_definitions
            VALUES (?, ?, ?, ?, 'NUMBER', ?, ?, true, 'scalar_results', ?, ?, ?, ?, ?, true, ?, ?, 'system')
            """,
            [
                f"variable-{load_case_id}-{key}", load_case_id, key, display_name, unit or "",
                f"{analysis_type} latest analysis result: {display_name}", threshold,
                json.dumps(["kpi", "gauge", "edge_bar", "scatter", "result_table", "chassis_bar", "chassis_table"]),
                json.dumps(["MAX", "MIN", "AVG", "LATEST"]), analysis_type, result_group, now, now,
            ],
        )
    series_rows = conn.execute(
        """
        SELECT run.load_case_id, ts.variable_key, min(ts.display_name), min(ts.value_unit), min(lc.analysis_type)
        FROM time_series_results ts
        JOIN analysis_runs run ON run.id = ts.analysis_run_id
        JOIN load_cases lc ON lc.id = run.load_case_id
        GROUP BY run.load_case_id, ts.variable_key
        """
    ).fetchall()
    for load_case_id, key, display_name, unit, analysis_type in series_rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO variable_definitions
            VALUES (?, ?, ?, ?, 'TIME_SERIES', ?, ?, true, 'time_series_results', NULL, ?, ?, ?, 'OPEN_CELL', true, ?, ?, 'system')
            """,
            [
                f"variable-{load_case_id}-{key}", load_case_id, key, display_name, unit or "",
                f"{analysis_type} time history: {display_name}",
                json.dumps(["time_series", "scatter", "result_table"]),
                json.dumps(["RAW", "MAX_BY_TIME"]), analysis_type, now, now,
            ],
        )
    conn.execute(
        """
        UPDATE variable_definitions
        SET allowed_widgets_json = ?
        WHERE data_type = 'NUMBER' AND updated_by = 'system'
        """,
        [json.dumps(["kpi", "gauge", "edge_bar", "scatter", "result_table", "chassis_bar", "chassis_table"])],
    )


def ensure_sample_evolutions(conn: duckdb.DuckDBPyConnection) -> None:
    """Add non-destructive sample fields introduced after the first database seed."""
    # Repair the single example registration created through a legacy PowerShell
    # client that replaced Korean characters with question marks.
    if conn.execute("SELECT count(*) FROM projects WHERE id = 'project-d2f8298b56ce'").fetchone()[0]:
        conn.execute("UPDATE projects SET name = ?, description = ? WHERE id = ?", ["Radioss CSV 등록 검토", "example 폴더의 합성 Radioss CSV 대리 등록 및 검산", "project-d2f8298b56ce"])
        conn.execute("UPDATE analysis_requests SET title = ?, owner = ?, overall_note = ? WHERE id = ?", ["TV 포장 낙하 예제 등록 검토", "Codex 검토", "합성 데이터 등록 흐름 검증", "request-babd3259f7fd"])
        conn.execute("UPDATE request_steps SET owner = ? WHERE request_id = ?", ["Codex 검토", "request-babd3259f7fd"])
        for row in [
            ("product-demo-model", "project-d2f8298b56ce", "MODEL", "제품 모델명", "ORION-65-OLED Example", None, {"source": "example_registration"}),
            ("product-demo-mfg", "project-d2f8298b56ce", "MANUFACTURER", "제조사", "NeoView Display", None, {"source": "example_registration"}),
            ("product-demo-size", "project-d2f8298b56ce", "SPEC", "화면 크기", "65 inch", None, {"diagonal_inch": 65}),
        ]:
            conn.execute("INSERT OR IGNORE INTO product_information VALUES (?, ?, ?, ?, ?, ?, ?)", [*row[:6], json.dumps(row[6], ensure_ascii=False)])
    product_rows = [
        ("product-mfg-001", "project-tv-001", "MANUFACTURER", "제조사", "NeoView Display", None, {"country": "KR"}),
        ("product-model-001", "project-tv-001", "MODEL", "제품 모델명", "ORION-65-OLED-C", None, {"series": "ORION"}),
        ("product-size-001", "project-tv-001", "SPEC", "화면 크기", "65 inch", None, {"diagonal_inch": 65}),
    ]
    for row in product_rows:
        conn.execute(
            "INSERT OR IGNORE INTO product_information VALUES (?, ?, ?, ?, ?, ?, ?)",
            [*row[:6], json.dumps(row[6], ensure_ascii=False)],
        )

    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT OR IGNORE INTO quality_thresholds
            (criterion_key, project_id, analysis_key, label, threshold_double, unit, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "chassis_rear_permanent_deformation_mm", "project-tv-001", "CHASSIS_REAR_PERMANENT_DEFORMATION",
            "Chassis Rear 영구변형 허용값", 5.0, "mm", "관리자", _iso(now),
        ],
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO quality_thresholds
            (criterion_key, project_id, analysis_key, label, threshold_double, unit, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "open_cell_stress_mpa", "project-tv-001", "OPEN_CELL_STRESS",
            "Open Cell 응력 허용값", 75.0, "MPa", "관리자", _iso(now),
        ],
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO batch_path_profiles
            (id, name, solver_path, working_directory, arguments_template,
             environment_json, task_type_ids_json, is_active, updated_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, true, ?, ?, ?)
        """,
        [
            "radioss-demo", "Radioss 배치 예시", "C:\\Altair\\hwsolvers\\radioss.exe",
            "C:\\Simulation\\runs\\{request_id}", "-i {input} -nt {cores}",
            json.dumps({"OMP_NUM_THREADS": "{cores}"}, ensure_ascii=False), json.dumps(["hpc-submit"]), "system", _iso(now), _iso(now),
        ],
    )
    conn.execute(
        """
        UPDATE batch_path_profiles
        SET task_type_ids_json=?
        WHERE id='radioss-demo' AND CAST(task_type_ids_json AS VARCHAR)='[]'
        """,
        [json.dumps(["hpc-submit"])],
    )

    target_started_row = conn.execute("SELECT started_at FROM analysis_runs WHERE id='run-drop-001'").fetchone()
    baseline_started = (target_started_row[0] - timedelta(days=4)) if target_started_row and target_started_row[0] else now - timedelta(days=7)
    conn.execute(
        "INSERT OR IGNORE INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "run-drop-baseline-001", "loadcase-drop-bottom-001", None, 0, "Explicit Solver", "COMPLETED",
            _iso(baseline_started), _iso(baseline_started + timedelta(hours=3)),
        ],
    )
    conn.execute(
        "UPDATE analysis_runs SET started_at=?, completed_at=? WHERE id='run-drop-baseline-001'",
        [_iso(baseline_started), _iso(baseline_started + timedelta(hours=3))],
    )
    metadata_rows = [
        ("run-drop-baseline-001", "SEED_SAMPLE", "orion65-drop-rev-b.json", "seed:orion65-drop-rev-b", "seed-v1", baseline_started + timedelta(hours=3)),
        ("run-drop-001", "SEED_SAMPLE", "orion65-drop-rev-c.json", "seed:orion65-drop-rev-c", "seed-v1", now - timedelta(days=3, hours=21)),
        ("run-clamp-001", "SEED_SAMPLE", "orion65-clamp-left.json", "seed:orion65-clamp-left", "seed-v1", now - timedelta(days=1, hours=20)),
    ]
    for run_id, source_type, source_name, checksum_seed, parser_version, created_at in metadata_rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO analysis_run_metadata
                (analysis_run_id, source_type, source_name, source_checksum, schema_id, schema_version,
                 parser_version, metadata_json, created_at)
            VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            [run_id, source_type, source_name, hashlib.sha256(checksum_seed.encode("utf-8")).hexdigest(), parser_version, json.dumps({"sample": True}), _iso(created_at)],
        )

    baseline_edge_values = {
        "top": ("상단 엣지", 65.2, 11.4),
        "bottom": ("하단 엣지", 72.1, 14.2),
        "left": ("좌측 엣지", 67.8, 12.6),
        "right": ("우측 엣지", 77.4, 13.3),
    }
    for index, (key, (label, maximum, peak_time)) in enumerate(baseline_edge_values.items(), start=1):
        verdict = "FAIL" if maximum > 75.0 else "PASS"
        conn.execute(
            "INSERT OR IGNORE INTO scalar_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [f"scalar-baseline-{key}", "run-drop-baseline-001", f"{key}_edge_max_stress", f"{label} 최대 응력", maximum, None, None, "MPa", 75.0, verdict],
        )
        series_count = conn.execute(
            "SELECT count(*) FROM time_series_results WHERE analysis_run_id=? AND variable_key=?",
            ["run-drop-baseline-001", f"{key}_edge_stress_time"],
        ).fetchone()[0]
        if series_count == 0:
            for point in range(101):
                time_ms = point * 0.25
                primary = maximum * math.exp(-((time_ms - peak_time) ** 2) / 8.5)
                rebound = maximum * 0.22 * math.exp(-((time_ms - (peak_time + 5.0)) ** 2) / 5.8)
                ripple = 1.1 * math.sin(time_ms * 1.7 + index) * math.exp(-time_ms / 16)
                conn.execute(
                    "INSERT INTO time_series_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ["run-drop-baseline-001", f"{key}_edge_stress_time", label, time_ms, round(max(0.0, primary + rebound + ripple), 3), "ms", "MPa"],
                )

    conn.execute(
        """
        INSERT OR IGNORE INTO template_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "template-exec-clamp-001", "loadcase-clamp-left-001", "TV Side Clamp Automation", "1.8.0",
            json.dumps({"pressure_mpa": 0.35, "hold_time_sec": 30}, ensure_ascii=False),
            json.dumps({"model": "orion65_side_clamp", "elements": 391540}, ensure_ascii=False),
            "COMPLETED", _iso(now - timedelta(days=2)),
        ],
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "run-clamp-001", "loadcase-clamp-left-001", "template-exec-clamp-001", 1, "Explicit Solver", "COMPLETED",
            _iso(now - timedelta(days=2)), _iso(now - timedelta(days=1, hours=20)),
        ],
    )

    chassis_results = {
        "run-drop-baseline-001": [
            ("top_edge_gap", "상단 엣지 Open Cell 이격 최대", 4.7),
            ("bottom_edge_gap", "하단 엣지 Open Cell 이격 최대", 4.5),
            ("corner_top_left", "좌상단 모서리 영구변형", 5.8),
            ("corner_top_right", "우상단 모서리 영구변형", 3.9),
            ("corner_bottom_left", "좌하단 모서리 영구변형", 4.6),
            ("corner_bottom_right", "우하단 모서리 영구변형", 5.1),
        ],
        "run-drop-001": [
            ("top_edge_gap", "상단 엣지 Open Cell 이격 최대", 5.8),
            ("bottom_edge_gap", "하단 엣지 Open Cell 이격 최대", 4.2),
            ("corner_top_left", "좌상단 모서리 영구변형", 6.3),
            ("corner_top_right", "우상단 모서리 영구변형", 3.7),
            ("corner_bottom_left", "좌하단 모서리 영구변형", 4.9),
            ("corner_bottom_right", "우하단 모서리 영구변형", 5.4),
        ],
        "run-clamp-001": [
            ("top_edge_gap", "상단 엣지 Open Cell 이격 최대", 3.8),
            ("bottom_edge_gap", "하단 엣지 Open Cell 이격 최대", 5.2),
            ("corner_top_left", "좌상단 모서리 영구변형", 4.6),
            ("corner_top_right", "우상단 모서리 영구변형", 5.7),
            ("corner_bottom_left", "좌하단 모서리 영구변형", 4.1),
            ("corner_bottom_right", "우하단 모서리 영구변형", 6.1),
        ],
    }
    for run_id, values in chassis_results.items():
        for key, label, value in values:
            verdict = "FAIL" if value >= 5.0 else "PASS"
            conn.execute(
                """
                INSERT OR IGNORE INTO scalar_results
                    (id, analysis_run_id, variable_key, display_name, value_double, value_integer,
                     value_text, unit, threshold_double, verdict)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"scalar-chassis-{run_id.replace('run-', '')}-{key}", run_id,
                    f"chassis_rear_{key}_permanent_deformation", label, value, None, None, "mm", 5.0, verdict,
                ],
            )

    clamp_open_cell_values = {
        "top": ("상단 엣지", 58.4),
        "bottom": ("하단 엣지", 64.1),
        "left": ("좌측 엣지", 79.6),
        "right": ("우측 엣지", 77.2),
    }
    for key, (label, maximum) in clamp_open_cell_values.items():
        verdict = "FAIL" if maximum > 75.0 else "PASS"
        conn.execute(
            """
            INSERT OR IGNORE INTO scalar_results
                (id, analysis_run_id, variable_key, display_name, value_double, value_integer,
                 value_text, unit, threshold_double, verdict)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                f"scalar-clamp-{key}", "run-clamp-001", f"{key}_edge_max_stress",
                f"{label} 최대 응력", maximum, None, None, "MPa", 75.0, verdict,
            ],
        )

    clamp_series_count = conn.execute(
        "SELECT count(*) FROM time_series_results WHERE analysis_run_id = ? AND variable_key = ?",
        ["run-clamp-001", "top_edge_stress_time"],
    ).fetchone()[0]
    if clamp_series_count == 0:
        for index, (key, (label, maximum)) in enumerate(clamp_open_cell_values.items(), start=1):
            for point in range(61):
                time_sec = point * 0.5
                ramp = min(1.0, time_sec / 7.5)
                settling = 1.0 - 0.025 * math.sin(time_sec * 0.7 + index) * math.exp(-time_sec / 18)
                value = maximum * ramp * settling
                conn.execute(
                    "INSERT INTO time_series_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ["run-clamp-001", f"{key}_edge_stress_time", label, time_sec, round(value, 3), "s", "MPa"],
                )

    conn.execute(
        "UPDATE request_steps SET name = '해석 전처리 모델링' WHERE name = '메시 및 조건 설정'"
    )
    conn.execute(
        "UPDATE request_steps SET is_optional = true WHERE lower(name) = 'validation'"
    )

    post_count = conn.execute(
        "SELECT count(*) FROM request_steps WHERE request_id = ? AND name = ?",
        ["request-drop-001", "후처리 작업"],
    ).fetchone()[0]
    if post_count == 0:
        conn.execute(
            "UPDATE request_steps SET sequence_no = sequence_no + 1 WHERE request_id = ? AND sequence_no >= 6",
            ["request-drop-001"],
        )
        conn.execute(
            "UPDATE request_steps SET status = 'WAITING', progress = 0, note = NULL WHERE request_id = ? AND name = ?",
            ["request-drop-001", "결과 검토"],
        )
        now = datetime.now(timezone.utc)
        conn.execute(
            """
            INSERT INTO request_steps
                (id, request_id, sequence_no, name, status, owner, planned_start, planned_end,
                 actual_start, actual_end, progress, is_optional, blocked_reason, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "step-drop-post-001", "request-drop-001", 6, "후처리 작업", "IN_PROGRESS", "김해석",
                _iso(now - timedelta(hours=5)), _iso(now + timedelta(hours=7)), _iso(now - timedelta(hours=4)),
                None, 55, False, None, "컨투어와 주요 응력 이력을 정리 중",
            ],
        )

    clamp_step_count = conn.execute(
        "SELECT count(*) FROM request_steps WHERE request_id = ?",
        ["request-clamp-001"],
    ).fetchone()[0]
    if clamp_step_count == 0:
        now = datetime.now(timezone.utc)
        clamp_steps = [
            ("의뢰 접수", "COMPLETED", 100, False),
            ("요구사항 검토", "COMPLETED", 100, False),
            ("모델 준비", "IN_PROGRESS", 70, False),
            ("해석 전처리 모델링", "IN_PROGRESS", 35, False),
            ("해석 실행", "WAITING", 0, False),
            ("후처리 작업", "WAITING", 0, False),
            ("결과 검토", "WAITING", 0, False),
            ("Validation", "WAITING", 0, True),
            ("승인", "WAITING", 0, False),
            ("완료", "WAITING", 0, False),
        ]
        for index, (name, status, progress, optional) in enumerate(clamp_steps, start=1):
            start = now - timedelta(days=3) + timedelta(hours=(index - 1) * 18)
            conn.execute(
                """
                INSERT INTO request_steps
                    (id, request_id, sequence_no, name, status, owner, planned_start, planned_end,
                     actual_start, actual_end, progress, is_optional, blocked_reason, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"step-clamp-{index:02d}", "request-clamp-001", index, name, status, "박검증",
                    _iso(start), _iso(start + timedelta(hours=14)), _iso(start + timedelta(hours=1)) if status != "WAITING" else None,
                    _iso(start + timedelta(hours=12)) if status == "COMPLETED" else None, progress, optional, None,
                    "클램프 접촉면 모델을 병렬 준비 중" if status == "IN_PROGRESS" else None,
                ],
            )

    stored = conn.execute(
        "SELECT definition_json, version FROM dashboards WHERE id = ?",
        ["dashboard-drop-default"],
    ).fetchone()
    if stored:
        definition = json_value(stored[0])
        widgets = definition.get("widgets", [])
        if not any(widget.get("type") == "open_cell_summary" for widget in widgets):
            for widget in widgets:
                widget["y"] = int(widget.get("y", 0)) + 2
            widgets.insert(0, {"id": "open-cell-summary", "type": "open_cell_summary", "title": "Open Cell 판정 요약", "x": 0, "y": 0, "w": 12, "h": 2, "settings": {}})
            next_version = int(stored[1]) + 1
            encoded = json.dumps(definition, ensure_ascii=False)
            conn.execute(
                "UPDATE dashboards SET definition_json = ?, version = ?, updated_at = ? WHERE id = ?",
                [encoded, next_version, _iso(datetime.now(timezone.utc)), "dashboard-drop-default"],
            )
            stored = (encoded, next_version)
        if not any(widget.get("type") == "open_cell_map" for widget in widgets):
            positions = {
                "max-stress": (5, 0, 7, 4),
                "verdict": (0, 4, 3, 2),
                "summary": (3, 4, 5, 2),
                "note": (8, 4, 4, 3),
                "time-series": (0, 7, 8, 5),
                "contour": (8, 7, 4, 3),
                "results": (0, 12, 12, 4),
            }
            for widget in widgets:
                if widget.get("id") in positions:
                    widget["x"], widget["y"], widget["w"], widget["h"] = positions[widget["id"]]
            widgets.insert(0, {"id": "open-cell", "type": "open_cell_map", "title": "Open Cell 엣지 맵", "x": 0, "y": 0, "w": 5, "h": 4})
            conn.execute(
                "UPDATE dashboards SET definition_json = ?, version = ?, updated_at = ? WHERE id = ?",
                [json.dumps(definition, ensure_ascii=False), stored[1] + 1, _iso(datetime.now(timezone.utc)), "dashboard-drop-default"],
            )


def ensure_feature_examples(conn: duckdb.DuckDBPyConnection) -> None:
    """Seed an additive, idempotent gallery that demonstrates the major product flows."""
    complete = (
        conn.execute("SELECT count(*) FROM projects WHERE id='project-feature-showcase'").fetchone()[0] == 1
        and conn.execute("SELECT count(*) FROM review_annotations WHERE analysis_run_id='run-showcase-review-2'").fetchone()[0] == 3
        and conn.execute("SELECT count(*) FROM variable_definitions WHERE load_case_id='loadcase-showcase-waiting'").fetchone()[0] == 5
        and conn.execute("SELECT count(*) FROM import_schemas WHERE id='import-schema-showcase-typed'").fetchone()[0] == 1
    )
    if complete:
        return
    now = datetime.now(timezone.utc)
    project_id = "project-feature-showcase"
    conn.execute(
        "INSERT OR IGNORE INTO projects VALUES (?, ?, ?, ?, ?)",
        [project_id, "Analysis Canvas 기능 예제 모음", "DEMO-65 Engineering TV", "비교·신뢰도·검토·결과형·데이터 대기·워크플로 기능을 안전하게 체험하는 예제 프로젝트", _iso(now - timedelta(days=40))],
    )
    for row in [
        ("showcase-product-model", project_id, "MODEL", "제품 모델", "DEMO-65-SHOWCASE", None, {"sample": True}),
        ("showcase-product-guide", project_id, "GUIDE", "예제 사용 안내", "예제 갤러리에서 확인할 기능을 선택하세요.", None, {"sample": True}),
    ]:
        conn.execute("INSERT OR IGNORE INTO product_information VALUES (?, ?, ?, ?, ?, ?, ?)", [*row[:6], json.dumps(row[6], ensure_ascii=False)])

    examples = [
        ("compare", "Run 비교: 회귀와 개선", "COMPLETED", "DROP", "낙하 설계안 A/B/C 비교", "COMPLETED"),
        ("trust", "신뢰도: 추적 가능한 폴더 Import", "COMPLETED", "DROP", "추적성 완비 결과", "COMPLETED"),
        ("warning", "신뢰도: 의도적인 경고", "IN_PROGRESS", "DROP", "카탈로그 매핑 누락 경고", "COMPLETED"),
        ("review", "협업 검토: 상태별 코멘트", "IN_PROGRESS", "SIDE_CLAMP", "검토 항목 상태 전환", "COMPLETED"),
        ("multitype", "다중 결과형: 수치·곡선·이미지", "COMPLETED", "SIDE_CLAMP", "혼합 결과형 시각화", "COMPLETED"),
        ("waiting", "변수 카탈로그: 데이터 대기", "READY", "DROP", "선언 후 데이터 연결 대기", "READY"),
        ("workflow", "워크플로: 진행·차단·대기", "IN_PROGRESS", "DROP", "실무 진행 상태 예제", "IN_PROGRESS"),
    ]
    step_names = ["요청 접수", "요구사항 검토", "모델 준비", "전처리", "해석 실행", "후처리", "결과 검토", "Validation", "승인", "완료"]
    for example_index, (key, title, request_status, analysis_type, load_case_name, load_case_status) in enumerate(examples):
        request_id = f"request-showcase-{key}"
        load_case_id = f"loadcase-showcase-{key}"
        requested_at = now - timedelta(days=30 - example_index)
        conn.execute(
            "INSERT OR IGNORE INTO analysis_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [request_id, project_id, title, request_status, "예제 운영자", _iso(requested_at), _iso(requested_at + timedelta(days=12)), f"{title} 기능을 확인하기 위한 비파괴 예제입니다."],
        )
        conn.execute(
            "INSERT OR IGNORE INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            [load_case_id, request_id, load_case_name, analysis_type, load_case_status, json.dumps({"sample": True, "example": key, "drop_height_mm": 800 if analysis_type == "DROP" else None, "pressure_mpa": 0.35 if analysis_type == "SIDE_CLAMP" else None}, ensure_ascii=False), _iso(requested_at + timedelta(days=1))],
        )
        if conn.execute("SELECT count(*) FROM request_steps WHERE request_id=?", [request_id]).fetchone()[0] == 0:
            for sequence_no, step_name in enumerate(step_names, start=1):
                if key == "workflow":
                    statuses = ["COMPLETED", "COMPLETED", "COMPLETED", "IN_PROGRESS", "BLOCKED", "WAITING", "WAITING", "WAITING", "WAITING", "WAITING"]
                elif request_status == "COMPLETED":
                    statuses = ["COMPLETED"] * 10
                else:
                    statuses = ["COMPLETED"] * 5 + ["IN_PROGRESS"] + ["WAITING"] * 4
                status = statuses[sequence_no - 1]
                progress = 100 if status == "COMPLETED" else 55 if status == "IN_PROGRESS" else 0
                start = requested_at + timedelta(hours=(sequence_no - 1) * 18)
                conn.execute(
                    """
                    INSERT INTO request_steps
                        (id, request_id, sequence_no, name, status, owner, planned_start, planned_end,
                         actual_start, actual_end, progress, blocked_reason, note, is_optional)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [f"step-showcase-{key}-{sequence_no:02d}", request_id, sequence_no, step_name, status, "예제 운영자", _iso(start), _iso(start + timedelta(hours=14)), _iso(start + timedelta(hours=1)) if status not in {"WAITING", "BLOCKED"} else None, _iso(start + timedelta(hours=12)) if status == "COMPLETED" else None, progress, "입력 모델 승인 대기" if status == "BLOCKED" else None, "서로 다른 상태와 진행률을 확인하세요." if key == "workflow" else None, False],
                )

    run_specs = {
        "compare": [(1, 68.0, 82.0), (2, 70.0, 80.0), (3, 81.0, 72.0)],
        "trust": [(1, 69.0, 73.0), (2, 66.0, 71.0)],
        "warning": [(1, 62.0, 70.0), (2, 64.0, 72.0)],
        "review": [(1, 68.0, 74.0), (2, 72.0, 78.0)],
        "multitype": [(1, 60.0, 68.0), (2, 58.0, 65.0)],
    }
    for key, runs in run_specs.items():
        load_case_id = f"loadcase-showcase-{key}"
        for run_no, top_value, bottom_value in runs:
            run_id = f"run-showcase-{key}-{run_no}"
            started = now - timedelta(days=18 - run_no, hours=run_no)
            conn.execute("INSERT OR IGNORE INTO analysis_runs VALUES (?, ?, NULL, ?, ?, 'COMPLETED', ?, ?)", [run_id, load_case_id, run_no, "Showcase Solver 2026.1", _iso(started), _iso(started + timedelta(hours=2))])
            values = [("top_edge_max_stress", "상단 엣지 최대 응력", top_value), ("bottom_edge_max_stress", "하단 엣지 최대 응력", bottom_value), ("left_edge_max_stress", "좌측 엣지 최대 응력", 67.0 + run_no), ("right_edge_max_stress", "우측 엣지 최대 응력", 71.0)]
            for value_index, (variable_key, display_name, value) in enumerate(values):
                conn.execute("INSERT OR IGNORE INTO scalar_results VALUES (?, ?, ?, ?, ?, NULL, NULL, 'MPa', 75.0, ?)", [f"scalar-showcase-{key}-{run_no}-{value_index}", run_id, variable_key, display_name, value, "FAIL" if value > 75 else "PASS"])
            if conn.execute("SELECT count(*) FROM time_series_results WHERE analysis_run_id=?", [run_id]).fetchone()[0] == 0:
                for point in range(21):
                    time_value = point * 0.5
                    value = top_value * math.exp(-((time_value - 5.0) ** 2) / 3.5)
                    conn.execute("INSERT INTO time_series_results VALUES (?, 'top_edge_stress_time', '상단 엣지 응력 이력', ?, ?, 'ms', 'MPa')", [run_id, time_value, round(value, 3)])
            conn.execute(
                "INSERT OR IGNORE INTO analysis_run_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, "FOLDER_IMPORT" if key in {"trust", "multitype"} else "SEED_SAMPLE", f"showcase/{key}/run-{run_no}", hashlib.sha256(run_id.encode()).hexdigest(), "import-schema-showcase-typed" if key in {"trust", "multitype"} else None, 1 if key in {"trust", "multitype"} else None, "showcase-parser-1.0", json.dumps({"example": key, "reproducible": True}), _iso(started + timedelta(hours=2))],
            )

    for key in ("compare", "trust", "review", "multitype"):
        run_id = f"run-showcase-{key}-{len(run_specs[key])}"
        conn.execute("INSERT OR IGNORE INTO validations VALUES (?, ?, ?, ?, ?, 'RESULT_QA', 'PASS', ?, ?, ?)", [f"validation-showcase-{key}", project_id, f"request-showcase-{key}", f"loadcase-showcase-{key}", run_id, json.dumps({"sample": True}), json.dumps({"summary": "예제 검증 통과"}, ensure_ascii=False), _iso(now - timedelta(days=3))])

    schema_definition = {"schema_id": "import-schema-showcase-typed", "version": 1, "context_mapping": {"mode": "folder_levels", "project_level": 0, "request_level": 1, "load_case_level": 2, "sample_path": "project/request/loadcase/results"}, "mappings": [{"pattern": "summary.csv", "data_type": "NUMBER"}, {"pattern": "curves/*.csv", "data_type": "CURVE"}, {"pattern": "media/*", "data_type": "IMAGE"}]}
    encoded_schema = json.dumps(schema_definition, ensure_ascii=False)
    conn.execute("INSERT OR IGNORE INTO import_schemas VALUES (?, ?, ?, ?, true, ?, ?, ?)", ["import-schema-showcase-typed", "다중 결과형 폴더 예제", "수치·곡선·이미지를 한 번에 등록하는 3단계 폴더 규칙", encoded_schema, _iso(now - timedelta(days=10)), _iso(now - timedelta(days=2)), "system"])
    conn.execute("INSERT OR IGNORE INTO import_schema_versions VALUES (?, 1, ?, ?, 'system')", ["import-schema-showcase-typed", encoded_schema, _iso(now - timedelta(days=10))])
    for key in ("trust", "multitype"):
        run_id = f"run-showcase-{key}-2"
        conn.execute("INSERT OR IGNORE INTO folder_import_jobs VALUES (?, ?, ?, ?, 1, ?, 'COMPLETED', ?, ?)", [f"folder-job-showcase-{key}", f"loadcase-showcase-{key}", run_id, "import-schema-showcase-typed", f"examples/showcase/{key}", json.dumps({"scalar": 4, "series": 1, "curve": 1, "media": 1}), _iso(now - timedelta(days=3))])

    for key in ("trust", "multitype"):
        run_id = f"run-showcase-{key}-2"
        curve_id = f"curve-showcase-{key}"
        conn.execute("INSERT OR IGNORE INTO curve_results VALUES (?, ?, 'load_displacement_curve', '하중-변위 곡선', 'default', 'Displacement', 'mm', 'Load', 'N', 11, 'curve.csv', ?, ?)", [curve_id, run_id, hashlib.sha256(curve_id.encode()).hexdigest(), _iso(now - timedelta(days=3))])
        for point in range(11):
            conn.execute("INSERT OR IGNORE INTO curve_points VALUES (?, ?, ?, ?)", [curve_id, point, point * 0.5, round(120 * math.sin(point / 10 * math.pi), 3)])
        conn.execute("INSERT OR IGNORE INTO media_assets VALUES (?, ?, 'IMAGE', '응력 컨투어 예제', 'sample-contour.svg', 'image/svg+xml', NULL, ?, ?)", [f"media-showcase-{key}", run_id, hashlib.sha256(f"media-{key}".encode()).hexdigest(), json.dumps({"variable_key": "stress_contour_image", "sample": True})])
        if conn.execute("SELECT count(*) FROM result_locations WHERE analysis_run_id=? AND variable_key='top_edge_max_stress'", [run_id]).fetchone()[0] == 0:
            conn.execute("INSERT INTO result_locations VALUES (?, 'top_edge_max_stress', 'ELEMENT', 'E-2048', 120.0, 5.0, 18.0, 5.0, 'ms', 'peak')", [run_id])
        for variable_key, display_name, data_type, unit, source, widgets, aggregations in [
            ("load_displacement_curve", "하중-변위 곡선", "CURVE", "N", "curve_results", ["time_series", "scatter", "result_table"], ["RAW", "MAX_BY_TIME"]),
            ("stress_contour_image", "응력 컨투어 예제", "IMAGE", "-", "media_assets", ["contour", "result_table"], ["LATEST"]),
        ]:
            conn.execute("INSERT OR IGNORE INTO variable_definitions VALUES (?, ?, ?, ?, ?, ?, ?, true, ?, NULL, ?, ?, ?, 'CUSTOM', true, ?, ?, 'system')", [f"variable-{key}-{variable_key}", f"loadcase-showcase-{key}", variable_key, display_name, data_type, unit, "다중 결과형 예제 변수", source, json.dumps(widgets), json.dumps(aggregations), "DROP" if key == "trust" else "SIDE_CLAMP", _iso(now), _iso(now)])

    warning_run = "run-showcase-warning-2"
    if conn.execute("SELECT count(*) FROM result_locations WHERE analysis_run_id=? AND variable_key='unmapped_hotspot'", [warning_run]).fetchone()[0] == 0:
        conn.execute("INSERT INTO result_locations VALUES (?, 'unmapped_hotspot', 'NODE', 'N-404', 0.0, 0.0, 0.0, 4.5, 'ms', 'intentional-warning')", [warning_run])

    review_run = "run-showcase-review-2"
    for index, (status, title, body, variable_key) in enumerate([
        ("OPEN", "상단 피크 원인 확인", "접촉 조건과 메시 민감도를 확인해 주세요.", "top_edge_max_stress"),
        ("IN_REVIEW", "하단 기준 초과 검토", "설계팀과 보강 리브 영향도를 검토 중입니다.", "bottom_edge_max_stress"),
        ("RESOLVED", "좌측 결과 승인", "재계산 결과 차이가 허용 범위 이내입니다.", "left_edge_max_stress"),
    ], start=1):
        bookmark_id = f"bookmark-showcase-review-{index}"
        conn.execute("INSERT OR IGNORE INTO result_bookmarks VALUES (?, ?, ?, ?, 'ELEMENT', ?, ?, '예제 검토자', ?)", [bookmark_id, review_run, variable_key, 5.0, f"E-{2000 + index}", title, _iso(now - timedelta(days=2, hours=index))])
        conn.execute("INSERT OR IGNORE INTO review_annotations VALUES (?, ?, ?, ?, ?, ?, '예제 검토자', ?, ?)", [f"annotation-showcase-review-{index}", bookmark_id, review_run, variable_key, body, status, _iso(now - timedelta(days=2, hours=index)), _iso(now - timedelta(hours=index))])

    waiting_variables = [
        ("planned_peak_acceleration", "예정 최대 가속도", "NUMBER", "g", ["kpi", "gauge", "result_table"], ["MAX", "LATEST"]),
        ("planned_acceleration_history", "예정 가속도 이력", "TIME_SERIES", "g", ["time_series", "scatter"], ["RAW", "MAX_BY_TIME"]),
        ("planned_contour", "예정 컨투어", "IMAGE", "-", ["contour"], ["LATEST"]),
        ("planned_motion", "예정 해석 동영상", "VIDEO", "-", ["video"], ["LATEST"]),
        ("planned_model", "예정 3D 모델", "MODEL_3D", "-", ["model3d"], ["LATEST"]),
    ]
    for variable_key, display_name, data_type, unit, widgets, aggregations in waiting_variables:
        conn.execute("INSERT OR IGNORE INTO variable_definitions VALUES (?, 'loadcase-showcase-waiting', ?, ?, ?, ?, '선언은 완료되었고 결과 파일 연결을 기다리는 예제', true, 'planned_sql_view', NULL, ?, ?, 'DROP', 'CUSTOM', true, ?, ?, 'system')", [f"variable-waiting-{variable_key}", variable_key, display_name, data_type, unit, json.dumps(widgets), json.dumps(aggregations), _iso(now), _iso(now)])


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def seed_database(conn: duckdb.DuckDBPyConnection) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
        [
            "project-tv-001",
            "Orion 65 TV 포장 신뢰성",
            "Orion 65 OLED TV",
            "Open Cell 유리 엣지의 포장 낙하 및 Side Clamp 안전성 평가",
            _iso(now - timedelta(days=12)),
        ],
    )

    product_rows = [
        ("product-cad-001", "project-tv-001", "CAD", "TV/포장 CAD", "rev.C", "assets/orion65-rev-c.glb", {"format": "GLB", "lightweight": True}),
        ("product-mat-001", "project-tv-001", "MATERIAL", "Open Cell Glass", "E=70 GPa, ν=0.23", None, {"supplier": "Sample Glass"}),
        ("product-rel-001", "project-tv-001", "RELIABILITY", "유리 허용 응력", "75 MPa", None, {"threshold_mpa": 75.0}),
        ("product-mfg-001", "project-tv-001", "MANUFACTURER", "제조사", "NeoView Display", None, {"country": "KR"}),
        ("product-model-001", "project-tv-001", "MODEL", "제품 모델명", "ORION-65-OLED-C", None, {"series": "ORION"}),
        ("product-size-001", "project-tv-001", "SPEC", "화면 크기", "65 inch", None, {"diagonal_inch": 65}),
    ]
    for row in product_rows:
        conn.execute("INSERT INTO product_information VALUES (?, ?, ?, ?, ?, ?, ?)", [*row[:6], json.dumps(row[6], ensure_ascii=False)])

    requests = [
        (
            "request-drop-001",
            "project-tv-001",
            "포장 낙하 시 Open Cell 엣지 응력 평가",
            "IN_PROGRESS",
            "김해석",
            _iso(now - timedelta(days=8)),
            _iso(now + timedelta(days=3)),
            "낙하 방향별 엣지 응력과 허용 기준을 비교한다.",
        ),
        (
            "request-clamp-001",
            "project-tv-001",
            "물류 Side Clamp 하중 안전성 평가",
            "READY",
            "박검증",
            _iso(now - timedelta(days=3)),
            _iso(now + timedelta(days=8)),
            "클램프 압력 변화에 따른 케이스 변형을 확인한다.",
        ),
    ]
    for request in requests:
        conn.execute("INSERT INTO analysis_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?)", request)

    step_names = [
        "의뢰 접수",
        "요구사항 검토",
        "모델 준비",
        "해석 전처리 모델링",
        "해석 실행",
        "후처리 작업",
        "결과 검토",
        "Validation",
        "승인",
        "완료",
    ]
    statuses = ["COMPLETED", "COMPLETED", "COMPLETED", "COMPLETED", "COMPLETED", "IN_PROGRESS", "WAITING", "WAITING", "WAITING", "WAITING"]
    for index, (name, status) in enumerate(zip(step_names, statuses), start=1):
        planned_start = now - timedelta(days=8) + timedelta(hours=(index - 1) * 20)
        planned_end = planned_start + timedelta(hours=16)
        actual_start = planned_start + timedelta(hours=1) if status != "WAITING" else None
        actual_end = planned_end - timedelta(hours=2) if status == "COMPLETED" else None
        progress = 100 if status == "COMPLETED" else 65 if status == "IN_PROGRESS" else 0
        conn.execute(
            """
            INSERT INTO request_steps
                (id, request_id, sequence_no, name, status, owner, planned_start, planned_end,
                 actual_start, actual_end, progress, is_optional, blocked_reason, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                f"step-drop-{index:02d}",
                "request-drop-001",
                index,
                name,
                status,
                "김해석" if index <= 6 else "이검증",
                _iso(planned_start),
                _iso(planned_end),
                _iso(actual_start) if actual_start else None,
                _iso(actual_end) if actual_end else None,
                progress,
                name == "Validation",
                None,
                "최대 응력 위치 재확인 중" if status == "IN_PROGRESS" else None,
            ],
        )

    clamp_statuses = ["COMPLETED", "COMPLETED", "IN_PROGRESS", "IN_PROGRESS", "WAITING", "WAITING", "WAITING", "WAITING", "WAITING", "WAITING"]
    for index, (name, status) in enumerate(zip(step_names, clamp_statuses), start=1):
        planned_start = now - timedelta(days=3) + timedelta(hours=(index - 1) * 18)
        planned_end = planned_start + timedelta(hours=14)
        progress = 100 if status == "COMPLETED" else 70 if index == 3 else 35 if status == "IN_PROGRESS" else 0
        conn.execute(
            """
            INSERT INTO request_steps
                (id, request_id, sequence_no, name, status, owner, planned_start, planned_end,
                 actual_start, actual_end, progress, is_optional, blocked_reason, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                f"step-clamp-{index:02d}", "request-clamp-001", index, name, status, "박검증",
                _iso(planned_start), _iso(planned_end), _iso(planned_start + timedelta(hours=1)) if status != "WAITING" else None,
                _iso(planned_end - timedelta(hours=2)) if status == "COMPLETED" else None, progress, name == "Validation", None,
                "클램프 접촉면 모델을 병렬 준비 중" if status == "IN_PROGRESS" else None,
            ],
        )

    load_cases = [
        (
            "loadcase-drop-bottom-001",
            "request-drop-001",
            "Bottom Face 450 mm Drop",
            "DROP",
            "COMPLETED",
            {"drop_height_mm": 450, "direction": "BOTTOM", "gravity_ms2": 9.80665},
            _iso(now - timedelta(days=4)),
        ),
        (
            "loadcase-clamp-left-001",
            "request-clamp-001",
            "Left/Right Side Clamp 0.35 MPa",
            "SIDE_CLAMP",
            "READY",
            {"pressure_mpa": 0.35, "hold_time_sec": 30, "faces": ["LEFT", "RIGHT"]},
            _iso(now - timedelta(days=2)),
        ),
    ]
    for load_case in load_cases:
        conn.execute(
            "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            [*load_case[:5], json.dumps(load_case[5], ensure_ascii=False), load_case[6]],
        )

    conn.execute(
        "INSERT INTO template_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "template-exec-drop-001",
            "loadcase-drop-bottom-001",
            "TV Packaging Drop Automation",
            "2.3.1",
            json.dumps({"mesh_size_mm": 8, "contact": "general", "drop_height_mm": 450}),
            json.dumps({"model": "orion65_drop_bottom", "elements": 428120}),
            "COMPLETED",
            _iso(now - timedelta(days=4, hours=2)),
        ],
    )
    conn.execute(
        "INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "run-drop-001",
            "loadcase-drop-bottom-001",
            "template-exec-drop-001",
            1,
            "Explicit Solver",
            "COMPLETED",
            _iso(now - timedelta(days=4)),
            _iso(now - timedelta(days=3, hours=21)),
        ],
    )

    edge_values = {
        "top": ("상단 엣지", 61.8, 11.8),
        "bottom": ("하단 엣지", 82.4, 14.6),
        "left": ("좌측 엣지", 69.2, 12.9),
        "right": ("우측 엣지", 73.6, 13.7),
    }
    threshold = 75.0
    for index, (key, (label, maximum, peak_time)) in enumerate(edge_values.items(), start=1):
        verdict = "FAIL" if maximum > threshold else "PASS"
        conn.execute(
            "INSERT INTO scalar_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [f"scalar-{key}", "run-drop-001", f"{key}_edge_max_stress", f"{label} 최대 응력", maximum, None, None, "MPa", threshold, verdict],
        )
        for point in range(101):
            time_ms = point * 0.25
            primary = maximum * math.exp(-((time_ms - peak_time) ** 2) / 8.5)
            rebound = maximum * 0.24 * math.exp(-((time_ms - (peak_time + 5.2)) ** 2) / 5.5)
            ripple = 1.4 * math.sin(time_ms * 1.8 + index) * math.exp(-time_ms / 16)
            value = max(0.0, primary + rebound + ripple)
            conn.execute(
                "INSERT INTO time_series_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                ["run-drop-001", f"{key}_edge_stress_time", label, time_ms, round(value, 3), "ms", "MPa"],
            )

    conn.execute(
        "INSERT INTO qualitative_notes VALUES (?, ?, ?, ?, ?)",
        [
            "note-drop-001",
            "run-drop-001",
            "김해석",
            "하단 엣지에서 허용 응력을 초과했다. 완충재 하단 코너의 국부 강성을 조정한 뒤 재해석이 필요하다.",
            _iso(now - timedelta(days=3, hours=20)),
        ],
    )
    conn.execute(
        "INSERT INTO media_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "media-contour-001",
            "run-drop-001",
            "CONTOUR_IMAGE",
            "Open Cell 최대주응력 컨투어",
            "assets/sample-contour.svg",
            "image/svg+xml",
            None,
            None,
            json.dumps({"result": "maximum_principal_stress", "unit": "MPa"}),
        ],
    )

    layout = {
        "id": "dashboard-drop-default",
        "name": "TV 포장 낙하 기본 분석",
        "description": "Open Cell 엣지 응력 및 판정",
        "page": {
            "kind": "analysis_page",
            "analysis_key": "open_cell",
            "status": "published",
            "display_order": 10,
            "is_system": True,
        },
        "widgets": [
            {"id": "open-cell", "type": "open_cell_map", "title": "Open Cell 엣지 맵", "x": 0, "y": 0, "w": 5, "h": 4},
            {"id": "max-stress", "type": "edge_bar", "title": "엣지별 최대 응력", "x": 5, "y": 0, "w": 7, "h": 4},
            {"id": "verdict", "type": "verdict", "title": "전체 판정", "x": 0, "y": 4, "w": 3, "h": 2},
            {"id": "summary", "type": "summary", "title": "하중 조건", "x": 3, "y": 4, "w": 5, "h": 2},
            {"id": "note", "type": "note", "title": "수행자 의견", "x": 8, "y": 4, "w": 4, "h": 3},
            {"id": "time-series", "type": "time_series", "title": "응력-시간 이력", "x": 0, "y": 7, "w": 8, "h": 5},
            {"id": "contour", "type": "contour", "title": "Open Cell 응력 컨투어", "x": 8, "y": 7, "w": 4, "h": 3},
            {"id": "results", "type": "result_table", "title": "상세 결과", "x": 0, "y": 12, "w": 12, "h": 4},
        ],
    }
    conn.execute(
        "INSERT INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "dashboard-drop-default",
            "project-tv-001",
            "request-drop-001",
            "loadcase-drop-bottom-001",
            layout["name"],
            layout["description"],
            1,
            json.dumps(layout, ensure_ascii=False),
            _iso(now),
        ],
    )


def json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
