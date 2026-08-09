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
                metadata_json JSONB
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
                parameters_json JSONB NOT NULL,
                created_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS template_executions (
                id VARCHAR PRIMARY KEY,
                load_case_id VARCHAR NOT NULL,
                template_name VARCHAR NOT NULL,
                template_version VARCHAR NOT NULL,
                input_json JSONB NOT NULL,
                generated_model_json JSONB,
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
                value_double DOUBLE PRECISION,
                value_integer BIGINT,
                value_text VARCHAR,
                unit VARCHAR,
                threshold_double DOUBLE PRECISION,
                verdict VARCHAR
            );

            CREATE TABLE IF NOT EXISTS time_series_results (
                analysis_run_id VARCHAR NOT NULL,
                variable_key VARCHAR NOT NULL,
                display_name VARCHAR NOT NULL,
                time_value DOUBLE PRECISION NOT NULL,
                value DOUBLE PRECISION NOT NULL,
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
                x_value DOUBLE PRECISION NOT NULL,
                y_value DOUBLE PRECISION NOT NULL,
                PRIMARY KEY(curve_id, point_index)
            );

            CREATE TABLE IF NOT EXISTS result_locations (
                analysis_run_id VARCHAR NOT NULL,
                variable_key VARCHAR NOT NULL,
                entity_type VARCHAR NOT NULL,
                entity_id VARCHAR NOT NULL,
                x DOUBLE PRECISION NOT NULL,
                y DOUBLE PRECISION NOT NULL,
                z DOUBLE PRECISION NOT NULL,
                time_value DOUBLE PRECISION NOT NULL,
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
                metadata_json JSONB
            );

            CREATE TABLE IF NOT EXISTS folder_import_jobs (
                id VARCHAR PRIMARY KEY,
                load_case_id VARCHAR NOT NULL,
                analysis_run_id VARCHAR,
                schema_id VARCHAR NOT NULL,
                schema_version INTEGER NOT NULL,
                source_folder VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                summary_json JSONB,
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
                metadata_json JSONB,
                created_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS import_schemas (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                description VARCHAR,
                definition_json JSONB NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                updated_by VARCHAR NOT NULL
            );

            CREATE TABLE IF NOT EXISTS import_schema_versions (
                schema_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                definition_json JSONB NOT NULL,
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
                sensor_json JSONB,
                ai_analysis_json JSONB,
                created_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS result_bookmarks (
                id VARCHAR PRIMARY KEY,
                analysis_run_id VARCHAR NOT NULL,
                variable_key VARCHAR,
                time_value DOUBLE PRECISION,
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
                criterion_key VARCHAR NOT NULL,
                project_id VARCHAR NOT NULL,
                analysis_key VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                threshold_double DOUBLE PRECISION NOT NULL,
                unit VARCHAR NOT NULL,
                updated_by VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (project_id, criterion_key)
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
                threshold_double DOUBLE PRECISION,
                allowed_widgets_json JSONB NOT NULL,
                allowed_aggregations_json JSONB NOT NULL,
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
                definition_json JSONB NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dashboard_versions (
                dashboard_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                definition_json JSONB NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                is_valid BOOLEAN NOT NULL DEFAULT true,
                PRIMARY KEY (dashboard_id, version)
            );

            CREATE TABLE IF NOT EXISTS workspace_layouts (
                layout_kind VARCHAR PRIMARY KEY,
                version INTEGER NOT NULL,
                definition_json JSONB NOT NULL,
                updated_by VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workspace_layout_versions (
                layout_kind VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                definition_json JSONB NOT NULL,
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
                definition_json JSONB NOT NULL,
                is_system BOOLEAN NOT NULL DEFAULT false,
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                updated_by VARCHAR NOT NULL
            );

            CREATE TABLE IF NOT EXISTS report_layout_versions (
                layout_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                definition_json JSONB NOT NULL,
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
                definition_json JSONB NOT NULL,
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
                detail_json JSONB
            );

            CREATE TABLE IF NOT EXISTS task_type_versions (
                id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                kind VARCHAR NOT NULL,
                display_name VARCHAR NOT NULL,
                description VARCHAR NOT NULL,
                supports_standalone BOOLEAN NOT NULL DEFAULT true,
                input_artifact_types_json JSONB NOT NULL,
                output_artifact_types_json JSONB NOT NULL,
                parameter_schema_json JSONB NOT NULL,
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
                allowed_task_types_json JSONB NOT NULL,
                default_workflow_json JSONB NOT NULL,
                match_rules_json JSONB NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY (id, version)
            );

            CREATE TABLE IF NOT EXISTS analysis_request_type_assignments (
                request_id VARCHAR PRIMARY KEY,
                request_type_id VARCHAR NOT NULL,
                request_type_version INTEGER NOT NULL,
                source VARCHAR NOT NULL,
                rule_snapshot_json JSONB NOT NULL,
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
                definition_snapshot_json JSONB NOT NULL,
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
                CHECK (status IN ('READY', 'IN_PROGRESS', 'WAITING', 'COMPLETED')),
                CHECK (progress BETWEEN 0 AND 100)
            );

            CREATE TABLE IF NOT EXISTS workflow_runs (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                request_id VARCHAR,
                request_type_id VARCHAR,
                request_type_version INTEGER,
                definition_json JSONB NOT NULL,
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
                depends_on_json JSONB NOT NULL,
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
                version INTEGER NOT NULL DEFAULT 1,
                name VARCHAR NOT NULL,
                solver_path VARCHAR NOT NULL,
                working_directory VARCHAR NOT NULL,
                arguments_template VARCHAR NOT NULL,
                environment_json JSONB NOT NULL,
                task_type_ids_json JSONB NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT true,
                updated_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS batch_path_profile_versions (
                id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                name VARCHAR NOT NULL,
                solver_path VARCHAR NOT NULL,
                working_directory VARCHAR NOT NULL,
                arguments_template VARCHAR NOT NULL,
                environment_json JSONB NOT NULL,
                task_type_ids_json JSONB NOT NULL,
                is_active BOOLEAN NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY (id, version)
            );

            CREATE TABLE IF NOT EXISTS batch_dispatches (
                id VARCHAR PRIMARY KEY,
                work_item_id VARCHAR NOT NULL,
                workflow_run_id VARCHAR NOT NULL UNIQUE,
                batch_profile_id VARCHAR NOT NULL,
                profile_snapshot_json JSONB NOT NULL,
                command_preview VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                CHECK (status = 'RECORDED_DEMO')
            );

            CREATE TABLE IF NOT EXISTS batch_execution_attempts (
                id VARCHAR PRIMARY KEY,
                work_item_id VARCHAR NOT NULL,
                workflow_run_id VARCHAR,
                batch_profile_id VARCHAR NOT NULL,
                batch_profile_version INTEGER NOT NULL,
                profile_snapshot_json JSONB NOT NULL,
                command_preview VARCHAR NOT NULL,
                idempotency_key VARCHAR NOT NULL,
                execution_mode VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                progress INTEGER NOT NULL,
                last_message VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                UNIQUE (work_item_id, idempotency_key),
                CHECK (execution_mode = 'DEMO_ONLY'),
                CHECK (status IN ('PREFLIGHT', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'REJECTED')),
                CHECK (progress BETWEEN 0 AND 100)
            );

            CREATE TABLE IF NOT EXISTS batch_execution_events (
                id VARCHAR PRIMARY KEY,
                attempt_id VARCHAR NOT NULL,
                event_index INTEGER NOT NULL,
                event_type VARCHAR NOT NULL,
                level VARCHAR NOT NULL,
                message VARCHAR NOT NULL,
                progress INTEGER NOT NULL,
                occurred_at TIMESTAMP NOT NULL,
                UNIQUE (attempt_id, event_index),
                CHECK (progress BETWEEN 0 AND 100)
            );

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
CREATE INDEX IF NOT EXISTS ix_batch_profile_versions_id ON batch_path_profile_versions(id, version DESC);
CREATE INDEX IF NOT EXISTS ix_batch_attempts_work_item ON batch_execution_attempts(work_item_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_batch_attempts_status ON batch_execution_attempts(status, created_at);
CREATE INDEX IF NOT EXISTS ix_batch_events_attempt ON batch_execution_events(attempt_id, event_index);
CREATE INDEX IF NOT EXISTS ix_batch_dispatches_work_item ON batch_dispatches(work_item_id, created_at DESC);

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
ALTER TABLE batch_dispatches ADD CONSTRAINT fk_batch_dispatches_work_item FOREIGN KEY (work_item_id) REFERENCES request_work_items(id);
ALTER TABLE batch_dispatches ADD CONSTRAINT fk_batch_dispatches_workflow_run FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(id);
ALTER TABLE batch_dispatches ADD CONSTRAINT fk_batch_dispatches_profile FOREIGN KEY (batch_profile_id) REFERENCES batch_path_profiles(id);
ALTER TABLE batch_execution_attempts ADD CONSTRAINT fk_batch_attempts_work_item FOREIGN KEY (work_item_id) REFERENCES request_work_items(id);
ALTER TABLE batch_execution_attempts ADD CONSTRAINT fk_batch_attempts_profile_version FOREIGN KEY (batch_profile_id, batch_profile_version) REFERENCES batch_path_profile_versions(id, version);
ALTER TABLE batch_execution_attempts ADD CONSTRAINT fk_batch_attempts_workflow_run FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(id);
ALTER TABLE batch_execution_events ADD CONSTRAINT fk_batch_events_attempt FOREIGN KEY (attempt_id) REFERENCES batch_execution_attempts(id);
