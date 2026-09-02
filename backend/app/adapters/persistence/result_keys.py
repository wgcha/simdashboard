from __future__ import annotations

import json

from ...database_connection import ConnectionLike


def run_result_keys(conn: ConnectionLike, run_id: str) -> set[str]:
    keys = {
        row[0]
        for row in conn.execute(
            """
            SELECT variable_key FROM scalar_results WHERE analysis_run_id=?
            UNION SELECT variable_key FROM time_series_results WHERE analysis_run_id=?
            UNION SELECT variable_key FROM curve_results WHERE analysis_run_id=?
            UNION SELECT variable_key FROM result_locations WHERE analysis_run_id=?
            """,
            [run_id, run_id, run_id, run_id],
        ).fetchall()
    }
    for (metadata_json,) in conn.execute("SELECT metadata_json FROM media_assets WHERE analysis_run_id=?", [run_id]).fetchall():
        metadata = _json_value(metadata_json) or {}
        if isinstance(metadata, dict) and metadata.get("variable_key"):
            keys.add(str(metadata["variable_key"]))
    return keys


def _json_value(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
