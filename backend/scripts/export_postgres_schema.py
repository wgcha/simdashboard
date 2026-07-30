from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path


INDEXES = """
CREATE INDEX IF NOT EXISTS ix_product_information_project ON product_information(project_id);
CREATE INDEX IF NOT EXISTS ix_analysis_requests_project ON analysis_requests(project_id);
CREATE INDEX IF NOT EXISTS ix_request_steps_request_sequence ON request_steps(request_id, sequence_no);
CREATE INDEX IF NOT EXISTS ix_load_cases_request ON load_cases(request_id);
CREATE INDEX IF NOT EXISTS ix_analysis_runs_load_case_run ON analysis_runs(load_case_id, run_no);
CREATE INDEX IF NOT EXISTS ix_scalar_results_run_variable ON scalar_results(analysis_run_id, variable_key);
CREATE INDEX IF NOT EXISTS ix_time_series_run_variable_time ON time_series_results(analysis_run_id, variable_key, time_value);
CREATE INDEX IF NOT EXISTS ix_curve_results_run_variable ON curve_results(analysis_run_id, variable_key);
CREATE INDEX IF NOT EXISTS ix_media_assets_run ON media_assets(analysis_run_id);
CREATE INDEX IF NOT EXISTS ix_folder_import_jobs_load_case ON folder_import_jobs(load_case_id);
CREATE INDEX IF NOT EXISTS ix_validations_run ON validations(analysis_run_id);
CREATE INDEX IF NOT EXISTS ix_review_annotations_run ON review_annotations(analysis_run_id);
CREATE INDEX IF NOT EXISTS ix_variable_definitions_load_case ON variable_definitions(load_case_id, variable_key);
CREATE INDEX IF NOT EXISTS ix_dashboards_project ON dashboards(project_id);
CREATE INDEX IF NOT EXISTS ix_audit_events_occurred_at ON audit_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_events_user_id ON audit_events(user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_events_path ON audit_events(path, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_workflow_runs_request ON workflow_runs(request_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_request_type_assignments_type ON analysis_request_type_assignments(request_type_id, request_type_version);
CREATE INDEX IF NOT EXISTS ix_request_work_plans_type ON request_work_plans(request_type_id, request_type_version);
CREATE INDEX IF NOT EXISTS ix_request_work_items_request_status ON request_work_items(request_id, status, sequence_no);
CREATE INDEX IF NOT EXISTS ix_task_runs_workflow ON task_runs(workflow_run_id, started_at);
CREATE INDEX IF NOT EXISTS ix_task_run_events_task ON task_run_events(task_run_id, event_index);
""".strip()

CONSTRAINTS = """
ALTER TABLE product_information ADD CONSTRAINT fk_product_information_project FOREIGN KEY (project_id) REFERENCES projects(id);
ALTER TABLE analysis_requests ADD CONSTRAINT fk_analysis_requests_project FOREIGN KEY (project_id) REFERENCES projects(id);
ALTER TABLE request_steps ADD CONSTRAINT fk_request_steps_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
ALTER TABLE request_steps ADD CONSTRAINT ck_request_steps_progress CHECK (progress BETWEEN 0 AND 100);
ALTER TABLE load_cases ADD CONSTRAINT fk_load_cases_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
ALTER TABLE template_executions ADD CONSTRAINT fk_template_executions_load_case FOREIGN KEY (load_case_id) REFERENCES load_cases(id);
ALTER TABLE analysis_runs ADD CONSTRAINT fk_analysis_runs_load_case FOREIGN KEY (load_case_id) REFERENCES load_cases(id);
ALTER TABLE analysis_runs ADD CONSTRAINT fk_analysis_runs_template FOREIGN KEY (template_execution_id) REFERENCES template_executions(id);
ALTER TABLE scalar_results ADD CONSTRAINT fk_scalar_results_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE time_series_results ADD CONSTRAINT fk_time_series_results_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE curve_results ADD CONSTRAINT fk_curve_results_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE curve_points ADD CONSTRAINT fk_curve_points_curve FOREIGN KEY (curve_id) REFERENCES curve_results(id);
ALTER TABLE result_locations ADD CONSTRAINT fk_result_locations_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE qualitative_notes ADD CONSTRAINT fk_qualitative_notes_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE media_assets ADD CONSTRAINT fk_media_assets_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE validations ADD CONSTRAINT fk_validations_project FOREIGN KEY (project_id) REFERENCES projects(id);
ALTER TABLE validations ADD CONSTRAINT fk_validations_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
ALTER TABLE validations ADD CONSTRAINT fk_validations_load_case FOREIGN KEY (load_case_id) REFERENCES load_cases(id);
ALTER TABLE validations ADD CONSTRAINT fk_validations_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE result_bookmarks ADD CONSTRAINT fk_result_bookmarks_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE review_annotations ADD CONSTRAINT fk_review_annotations_bookmark FOREIGN KEY (bookmark_id) REFERENCES result_bookmarks(id);
ALTER TABLE review_annotations ADD CONSTRAINT fk_review_annotations_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE quality_thresholds ADD CONSTRAINT fk_quality_thresholds_project FOREIGN KEY (project_id) REFERENCES projects(id);
ALTER TABLE variable_definitions ADD CONSTRAINT fk_variable_definitions_load_case FOREIGN KEY (load_case_id) REFERENCES load_cases(id);
ALTER TABLE dashboards ADD CONSTRAINT fk_dashboards_project FOREIGN KEY (project_id) REFERENCES projects(id);
ALTER TABLE dashboards ADD CONSTRAINT fk_dashboards_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
ALTER TABLE dashboards ADD CONSTRAINT fk_dashboards_load_case FOREIGN KEY (load_case_id) REFERENCES load_cases(id);
ALTER TABLE dashboard_versions ADD CONSTRAINT fk_dashboard_versions_dashboard FOREIGN KEY (dashboard_id) REFERENCES dashboards(id);
ALTER TABLE workspace_layout_versions ADD CONSTRAINT fk_workspace_layout_versions_layout FOREIGN KEY (layout_kind) REFERENCES workspace_layouts(layout_kind);
ALTER TABLE report_layout_versions ADD CONSTRAINT fk_report_layout_versions_layout FOREIGN KEY (layout_id) REFERENCES report_layouts(id);
ALTER TABLE users ADD CONSTRAINT ck_users_role CHECK (role IN ('viewer', 'editor', 'admin'));
ALTER TABLE workflow_runs ADD CONSTRAINT fk_workflow_runs_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
ALTER TABLE analysis_request_type_assignments ADD CONSTRAINT fk_request_type_assignments_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
ALTER TABLE analysis_request_type_assignments ADD CONSTRAINT fk_request_type_assignments_type FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version);
ALTER TABLE request_work_plans ADD CONSTRAINT fk_request_work_plans_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
ALTER TABLE request_work_plans ADD CONSTRAINT fk_request_work_plans_type FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version);
ALTER TABLE request_work_items ADD CONSTRAINT fk_request_work_items_request FOREIGN KEY (request_id) REFERENCES request_work_plans(request_id);
ALTER TABLE request_work_items ADD CONSTRAINT fk_request_work_items_type FOREIGN KEY (task_type_id, task_type_version) REFERENCES task_type_versions(id, version);
ALTER TABLE request_work_items ADD CONSTRAINT fk_request_work_items_demo_run FOREIGN KEY (demo_run_id) REFERENCES workflow_runs(id);
ALTER TABLE workflow_runs ADD CONSTRAINT fk_workflow_runs_request_type FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version);
ALTER TABLE task_runs ADD CONSTRAINT fk_task_runs_workflow FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(id);
ALTER TABLE task_runs ADD CONSTRAINT fk_task_runs_type FOREIGN KEY (task_type_id, task_type_version) REFERENCES task_type_versions(id, version);
ALTER TABLE task_run_events ADD CONSTRAINT fk_task_run_events_task FOREIGN KEY (task_run_id) REFERENCES task_runs(id);
""".strip()


def extract_schema(source: Path) -> str:
    module = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        statement = node.args[0]
        if isinstance(statement, ast.Constant) and isinstance(statement.value, str) and "CREATE TABLE IF NOT EXISTS projects" in statement.value:
            ddl = statement.value.strip()
            ddl = re.sub(r"\bJSON\b", "JSONB", ddl)
            ddl = re.sub(r"\bDOUBLE\b(?!\s+PRECISION)", "DOUBLE PRECISION", ddl)
            return f"{ddl}\n\n{INDEXES}\n\n{CONSTRAINTS}\n"
    raise RuntimeError("database.py에서 기준 CREATE TABLE DDL을 찾지 못했습니다.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export PostgreSQL DDL from the canonical DuckDB schema.")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "app" / "database.py")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "migrations" / "schema.sql")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(extract_schema(args.source), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
