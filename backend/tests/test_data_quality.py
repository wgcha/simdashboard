from app.database import connect, initialize_database


def test_seeded_database_keys_integrity_and_domains():
    initialize_database()
    with connect() as conn:
        for table in ("projects", "analysis_requests", "load_cases", "analysis_runs", "scalar_results", "dashboards"):
            total, distinct_ids = conn.execute(f"SELECT count(*), count(DISTINCT id) FROM {table}").fetchone()
            assert total == distinct_ids, f"{table} id 중복"
        assert conn.execute("SELECT count(*) FROM analysis_requests r LEFT JOIN projects p ON p.id=r.project_id WHERE p.id IS NULL").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM load_cases lc LEFT JOIN analysis_requests r ON r.id=lc.request_id WHERE r.id IS NULL").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM analysis_runs run LEFT JOIN load_cases lc ON lc.id=run.load_case_id WHERE lc.id IS NULL").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM scalar_results s LEFT JOIN analysis_runs run ON run.id=s.analysis_run_id WHERE run.id IS NULL").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM analysis_requests WHERE status NOT IN ('READY','IN_PROGRESS','COMPLETED','BLOCKED','FAILED')").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM load_cases WHERE analysis_type NOT IN ('DROP','SIDE_CLAMP')").fetchone()[0] == 0
        assert conn.execute("""
            SELECT count(*) FROM scalar_results
            WHERE (value_double IS NOT NULL OR value_integer IS NOT NULL)
              AND (unit IS NULL OR (verdict IS NOT NULL AND verdict NOT IN ('PASS','FAIL')))
        """).fetchone()[0] == 0
