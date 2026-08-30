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
CREATE INDEX IF NOT EXISTS ix_media_assets_blob_id ON media_assets(blob_id);
CREATE INDEX IF NOT EXISTS ix_drop_video_assets_blob_id ON drop_video_assets(blob_id);
CREATE INDEX IF NOT EXISTS ix_drop_video_assets_load_case ON drop_video_assets(load_case_id, sort_order);
CREATE INDEX IF NOT EXISTS ix_folder_import_jobs_load_case ON folder_import_jobs(load_case_id);
CREATE INDEX IF NOT EXISTS ix_folder_import_jobs_source_identity ON folder_import_jobs(source_type, source_checksum);
CREATE INDEX IF NOT EXISTS ix_folder_import_jobs_source_run ON folder_import_jobs(source_run_id);
CREATE INDEX IF NOT EXISTS ix_folder_import_jobs_replaced_run ON folder_import_jobs(replaced_analysis_run_id);
CREATE INDEX IF NOT EXISTS ix_canonical_result_ingestion_source_versions_lookup ON canonical_result_ingestion_source_versions(load_case_id, source_type, source_key, source_run_id, source_revision DESC);
CREATE INDEX IF NOT EXISTS ix_canonical_result_ingestion_source_versions_supersedes_run ON canonical_result_ingestion_source_versions(supersedes_analysis_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_analysis_runs_load_case_run_no ON analysis_runs(load_case_id, run_no);
CREATE INDEX IF NOT EXISTS ix_validations_run ON validations(analysis_run_id);
CREATE INDEX IF NOT EXISTS ix_review_annotations_run ON review_annotations(analysis_run_id);
CREATE INDEX IF NOT EXISTS ix_variable_definitions_load_case ON variable_definitions(load_case_id, variable_key);
CREATE INDEX IF NOT EXISTS ix_dashboards_project ON dashboards(project_id);
CREATE INDEX IF NOT EXISTS ix_audit_events_occurred_at ON audit_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_events_user_id ON audit_events(user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_events_path ON audit_events(path, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_workflow_runs_request ON workflow_runs(request_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_runs_batch_attempt_id ON workflow_runs(batch_attempt_id);
CREATE INDEX IF NOT EXISTS ix_request_type_assignments_type ON analysis_request_type_assignments(request_type_id, request_type_version);
CREATE INDEX IF NOT EXISTS ix_request_work_plans_type ON request_work_plans(request_type_id, request_type_version);
CREATE INDEX IF NOT EXISTS ix_analysis_template_versions_status ON analysis_template_versions(template_id, lifecycle_status, version DESC);
CREATE INDEX IF NOT EXISTS ix_request_result_layout_snapshots_template ON request_result_layout_snapshots(source_template_id, source_template_version);
CREATE INDEX IF NOT EXISTS ix_project_result_profiles_template ON project_request_type_result_profiles(template_id, template_version);
CREATE INDEX IF NOT EXISTS ix_project_result_profiles_latest ON project_request_type_result_profiles(project_id, request_type_id, request_type_version, binding_version DESC);
CREATE INDEX IF NOT EXISTS ix_request_work_items_request_status ON request_work_items(request_id, status, sequence_no);
CREATE INDEX IF NOT EXISTS ix_task_runs_workflow ON task_runs(workflow_run_id, started_at);
CREATE INDEX IF NOT EXISTS ix_task_run_events_task ON task_run_events(task_run_id, event_index);
CREATE INDEX IF NOT EXISTS ix_batch_profile_versions_id ON batch_path_profile_versions(id, version DESC);
CREATE INDEX IF NOT EXISTS ix_batch_attempts_work_item ON batch_execution_attempts(work_item_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_batch_attempts_status ON batch_execution_attempts(status, created_at);
CREATE INDEX IF NOT EXISTS ix_batch_events_attempt ON batch_execution_events(attempt_id, event_index);
CREATE INDEX IF NOT EXISTS ix_batch_dispatches_work_item ON batch_dispatches(work_item_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_batch_dispatches_attempt_id ON batch_dispatches(attempt_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_employee_id ON users(employee_id) WHERE employee_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_oidc_identity ON users(oidc_issuer, oidc_subject) WHERE oidc_issuer IS NOT NULL AND oidc_subject IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_account_status ON users(account_status);
CREATE INDEX IF NOT EXISTS idx_users_oidc_identity ON users(oidc_issuer, oidc_subject);
CREATE INDEX IF NOT EXISTS idx_project_memberships_user_project ON project_memberships(user_id, project_id);
CREATE INDEX IF NOT EXISTS idx_project_memberships_project_role ON project_memberships(project_id, role, user_id);
CREATE INDEX IF NOT EXISTS idx_project_invitations_project_status ON project_invitations(project_id, status, invited_at DESC);
CREATE INDEX IF NOT EXISTS idx_project_invitations_employee ON project_invitations(employee_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_invitations_open ON project_invitations(project_id, employee_id) WHERE status IN ('PENDING_ACCOUNT', 'PENDING_APPROVAL', 'READY');
CREATE INDEX IF NOT EXISTS idx_analysis_requests_owner_user_id ON analysis_requests(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_request_steps_owner_user_id ON request_steps(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_request_work_items_owner_user_id ON request_work_items(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_project_workspace_layouts_project ON project_workspace_layouts(project_id, layout_kind);
CREATE INDEX IF NOT EXISTS idx_project_workspace_layout_versions_project ON project_workspace_layout_versions(project_id, layout_kind, version DESC);
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
ALTER TABLE folder_import_jobs ADD CONSTRAINT fk_folder_import_jobs_replaced_run FOREIGN KEY (replaced_analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE canonical_result_ingestion_source_versions ADD CONSTRAINT fk_canonical_result_ingestion_source_versions_load_case FOREIGN KEY (load_case_id) REFERENCES load_cases(id);
ALTER TABLE canonical_result_ingestion_source_versions ADD CONSTRAINT fk_canonical_result_ingestion_source_versions_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE canonical_result_ingestion_source_versions ADD CONSTRAINT fk_canonical_result_ingestion_source_versions_supersedes_run FOREIGN KEY (supersedes_analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE scalar_results ADD CONSTRAINT fk_scalar_results_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE time_series_results ADD CONSTRAINT fk_time_series_results_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE curve_results ADD CONSTRAINT fk_curve_results_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE curve_points ADD CONSTRAINT fk_curve_points_curve FOREIGN KEY (curve_id) REFERENCES curve_results(id);
ALTER TABLE result_locations ADD CONSTRAINT fk_result_locations_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE qualitative_notes ADD CONSTRAINT fk_qualitative_notes_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE media_assets ADD CONSTRAINT fk_media_assets_run FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id);
ALTER TABLE media_assets ADD CONSTRAINT fk_media_assets_blob FOREIGN KEY (blob_id) REFERENCES asset_blobs(id);
ALTER TABLE asset_blob_chunks ADD CONSTRAINT fk_asset_blob_chunks_blob FOREIGN KEY (blob_id) REFERENCES asset_blobs(id) ON DELETE CASCADE;
ALTER TABLE drop_video_assets ADD CONSTRAINT fk_drop_video_assets_load_case FOREIGN KEY (load_case_id) REFERENCES load_cases(id);
ALTER TABLE drop_video_assets ADD CONSTRAINT fk_drop_video_assets_blob FOREIGN KEY (blob_id) REFERENCES asset_blobs(id) ON DELETE RESTRICT;
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
ALTER TABLE project_workspace_layouts ADD CONSTRAINT fk_project_workspace_layouts_project FOREIGN KEY (project_id) REFERENCES projects(id);
ALTER TABLE project_workspace_layout_versions ADD CONSTRAINT fk_project_workspace_layout_versions_project FOREIGN KEY (project_id) REFERENCES projects(id);
ALTER TABLE report_layout_versions ADD CONSTRAINT fk_report_layout_versions_layout FOREIGN KEY (layout_id) REFERENCES report_layouts(id);
ALTER TABLE project_memberships ADD CONSTRAINT fk_project_memberships_project FOREIGN KEY (project_id) REFERENCES projects(id);
ALTER TABLE project_memberships ADD CONSTRAINT fk_project_memberships_user FOREIGN KEY (user_id) REFERENCES users(id);
ALTER TABLE project_invitations ADD CONSTRAINT fk_project_invitations_project FOREIGN KEY (project_id) REFERENCES projects(id);
ALTER TABLE project_invitations ADD CONSTRAINT fk_project_invitations_user FOREIGN KEY (resolved_user_id) REFERENCES users(id);
ALTER TABLE role_menu_policies ADD CONSTRAINT fk_role_menu_policies_menu FOREIGN KEY (menu_id) REFERENCES menu_definitions(id);
ALTER TABLE workflow_runs ADD CONSTRAINT fk_workflow_runs_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
ALTER TABLE workflow_runs ADD CONSTRAINT fk_workflow_runs_batch_attempt FOREIGN KEY (batch_attempt_id) REFERENCES batch_execution_attempts(id);
ALTER TABLE analysis_request_type_assignments ADD CONSTRAINT fk_request_type_assignments_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
ALTER TABLE analysis_request_type_assignments ADD CONSTRAINT fk_request_type_assignments_type FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version);
ALTER TABLE request_work_plans ADD CONSTRAINT fk_request_work_plans_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
ALTER TABLE request_work_plans ADD CONSTRAINT fk_request_work_plans_type FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version);
ALTER TABLE analysis_template_versions ADD CONSTRAINT fk_analysis_template_versions_project FOREIGN KEY (project_id) REFERENCES projects(id);
ALTER TABLE request_type_result_profiles ADD CONSTRAINT fk_result_profiles_request_type FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version);
ALTER TABLE request_type_result_profiles ADD CONSTRAINT fk_result_profiles_template FOREIGN KEY (template_id, template_version) REFERENCES analysis_template_versions(template_id, version);
ALTER TABLE project_request_type_result_profiles ADD CONSTRAINT fk_project_result_profiles_project FOREIGN KEY (project_id) REFERENCES projects(id);
ALTER TABLE project_request_type_result_profiles ADD CONSTRAINT fk_project_result_profiles_request_type FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version);
ALTER TABLE project_request_type_result_profiles ADD CONSTRAINT fk_project_result_profiles_template FOREIGN KEY (template_id, template_version) REFERENCES analysis_template_versions(template_id, version);
ALTER TABLE request_result_layout_snapshots ADD CONSTRAINT fk_result_layout_snapshots_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
ALTER TABLE request_work_items ADD CONSTRAINT fk_request_work_items_request FOREIGN KEY (request_id) REFERENCES request_work_plans(request_id);
ALTER TABLE request_work_items ADD CONSTRAINT fk_request_work_items_type FOREIGN KEY (task_type_id, task_type_version) REFERENCES task_type_versions(id, version);
ALTER TABLE request_work_items ADD CONSTRAINT fk_request_work_items_demo_run FOREIGN KEY (demo_run_id) REFERENCES workflow_runs(id);
ALTER TABLE workflow_runs ADD CONSTRAINT fk_workflow_runs_request_type FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version);
ALTER TABLE task_runs ADD CONSTRAINT fk_task_runs_workflow FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(id);
ALTER TABLE task_runs ADD CONSTRAINT fk_task_runs_type FOREIGN KEY (task_type_id, task_type_version) REFERENCES task_type_versions(id, version);
ALTER TABLE task_run_events ADD CONSTRAINT fk_task_run_events_task FOREIGN KEY (task_run_id) REFERENCES task_runs(id);
ALTER TABLE batch_dispatches ADD CONSTRAINT fk_batch_dispatches_work_item FOREIGN KEY (work_item_id) REFERENCES request_work_items(id);
ALTER TABLE batch_dispatches ADD CONSTRAINT fk_batch_dispatches_workflow_run FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(id);
ALTER TABLE batch_dispatches ADD CONSTRAINT fk_batch_dispatches_profile FOREIGN KEY (batch_profile_id) REFERENCES batch_path_profiles(id);
ALTER TABLE batch_dispatches ADD CONSTRAINT fk_batch_dispatches_attempt FOREIGN KEY (attempt_id) REFERENCES batch_execution_attempts(id);
ALTER TABLE batch_execution_attempts ADD CONSTRAINT fk_batch_attempts_work_item FOREIGN KEY (work_item_id) REFERENCES request_work_items(id);
ALTER TABLE batch_execution_attempts ADD CONSTRAINT fk_batch_attempts_profile_version FOREIGN KEY (batch_profile_id, batch_profile_version) REFERENCES batch_path_profile_versions(id, version);
ALTER TABLE batch_execution_attempts ADD CONSTRAINT fk_batch_attempts_workflow_run FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(id);
ALTER TABLE batch_execution_events ADD CONSTRAINT fk_batch_events_attempt FOREIGN KEY (attempt_id) REFERENCES batch_execution_attempts(id);
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
            ddl = re.sub(
                r"regexp_matches\(([^,()]+),\s*('(?:[^']|'')*')\)",
                r"\1 ~ \2",
                ddl,
            )
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
