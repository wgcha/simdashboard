from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "analysis_dashboard.duckdb"


def connect() -> duckdb.DuckDBPyConnection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH))


def rows(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def initialize_database() -> None:
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
            """
        )

        conn.execute("ALTER TABLE request_steps ADD COLUMN IF NOT EXISTS is_optional BOOLEAN DEFAULT false")

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


def ensure_sample_evolutions(conn: duckdb.DuckDBPyConnection) -> None:
    """Add non-destructive sample fields introduced after the first database seed."""
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
