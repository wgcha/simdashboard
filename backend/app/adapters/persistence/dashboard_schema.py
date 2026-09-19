"""Additive development schema for immutable dashboard captures.

PostgreSQL schema ownership remains with migration 0029, never app startup.
"""


def ensure_dashboard_schema(connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_cases (
            id VARCHAR PRIMARY KEY,
            project_id VARCHAR NOT NULL REFERENCES projects(id),
            request_id VARCHAR NOT NULL REFERENCES analysis_requests(id),
            storage_root_id VARCHAR NOT NULL,
            relative_path VARCHAR NOT NULL,
            environment VARCHAR NOT NULL CHECK(environment IN ('USAGE','DISTRIBUTION')),
            source_name VARCHAR NOT NULL,
            metadata_json JSON NOT NULL,
            created_at TIMESTAMP NOT NULL,
            UNIQUE(storage_root_id, relative_path)
        );
        CREATE TABLE IF NOT EXISTS dashboard_captures (
            id VARCHAR PRIMARY KEY,
            case_id VARCHAR NOT NULL REFERENCES dashboard_cases(id),
            fingerprint VARCHAR NOT NULL,
            recipe_version VARCHAR NOT NULL,
            manifest_json JSON NOT NULL,
            payload_json JSON NOT NULL,
            created_by VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL,
            UNIQUE(case_id, fingerprint)
        );
        CREATE TABLE IF NOT EXISTS dashboard_assets (
            id VARCHAR PRIMARY KEY,
            capture_id VARCHAR NOT NULL REFERENCES dashboard_captures(id),
            relative_path VARCHAR NOT NULL,
            sha256 VARCHAR NOT NULL,
            media_type VARCHAR NOT NULL,
            content BLOB NOT NULL,
            metadata_json JSON NOT NULL,
            UNIQUE(capture_id, relative_path)
        );
        CREATE INDEX IF NOT EXISTS ix_dashboard_cases_request ON dashboard_cases(request_id);
        CREATE INDEX IF NOT EXISTS ix_dashboard_captures_case ON dashboard_captures(case_id, created_at);
    """)
