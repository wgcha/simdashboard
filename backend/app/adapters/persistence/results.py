from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast

from ...database_connection import ConnectionLike, connect, rows
from ...domains.results.models import AnalysisRun, RunEvidence, RunSummaryReadData
from ...domains.results.ports import AnalysisRunSummaryRepository


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


class SQLAnalysisRunSummaryRepository:
    """Build run summaries with a fixed load-case-level batch query set."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def read_for_load_case(self, load_case_id: str) -> RunSummaryReadData:
        run_rows = cast(
            list[AnalysisRun],
            rows(
                self._connection.execute(
                    "SELECT * FROM analysis_runs WHERE load_case_id=? ORDER BY run_no DESC",
                    [load_case_id],
                )
            ),
        )
        if not run_rows:
            return {"runs": [], "catalog_units": {}, "evidence_by_run": {}}
        count_rows = self._connection.execute(
            """
            SELECT s.analysis_run_id, 'scalar' AS result_type, count(*) AS result_count,
                   sum(CASE WHEN s.verdict='FAIL' THEN 1 ELSE 0 END) AS fail_count,
                   count(s.verdict) AS verdict_count
            FROM scalar_results s
            JOIN analysis_runs r ON r.id=s.analysis_run_id
            WHERE r.load_case_id=? GROUP BY s.analysis_run_id
            UNION ALL
            SELECT ts.analysis_run_id, 'time_series', count(*), 0, 0
            FROM time_series_results ts
            JOIN analysis_runs r ON r.id=ts.analysis_run_id
            WHERE r.load_case_id=? GROUP BY ts.analysis_run_id
            """,
            [load_case_id, load_case_id],
        ).fetchall()
        trace_rows = self._connection.execute(
            """
            SELECT metadata.analysis_run_id, 'metadata' AS source_kind
            FROM analysis_run_metadata metadata
            JOIN analysis_runs r ON r.id=metadata.analysis_run_id
            WHERE r.load_case_id=?
            UNION ALL
            SELECT jobs.analysis_run_id, 'import'
            FROM folder_import_jobs jobs
            JOIN analysis_runs r ON r.id=jobs.analysis_run_id
            WHERE r.load_case_id=? AND jobs.analysis_run_id IS NOT NULL
            """,
            [load_case_id, load_case_id],
        ).fetchall()
        result_rows = self._connection.execute(
            """
            SELECT s.analysis_run_id, s.variable_key, s.unit AS actual_unit, true AS has_unit,
                   CAST(NULL AS VARCHAR) AS metadata_json
            FROM scalar_results s JOIN analysis_runs r ON r.id=s.analysis_run_id
            WHERE r.load_case_id=?
            UNION
            SELECT ts.analysis_run_id, ts.variable_key, ts.value_unit, true, CAST(NULL AS VARCHAR)
            FROM time_series_results ts JOIN analysis_runs r ON r.id=ts.analysis_run_id
            WHERE r.load_case_id=?
            UNION
            SELECT curves.analysis_run_id, curves.variable_key, curves.y_unit, true, CAST(NULL AS VARCHAR)
            FROM curve_results curves JOIN analysis_runs r ON r.id=curves.analysis_run_id
            WHERE r.load_case_id=?
            UNION
            SELECT locations.analysis_run_id, locations.variable_key, CAST(NULL AS VARCHAR), false,
                   CAST(NULL AS VARCHAR)
            FROM result_locations locations JOIN analysis_runs r ON r.id=locations.analysis_run_id
            WHERE r.load_case_id=?
            UNION ALL
            SELECT media.analysis_run_id, CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), false,
                   CAST(media.metadata_json AS VARCHAR)
            FROM media_assets media JOIN analysis_runs r ON r.id=media.analysis_run_id
            WHERE r.load_case_id=?
            """,
            [load_case_id, load_case_id, load_case_id, load_case_id, load_case_id],
        ).fetchall()
        catalog_rows = self._connection.execute(
            "SELECT variable_key, unit FROM variable_definitions WHERE load_case_id=? AND is_active=true",
            [load_case_id],
        ).fetchall()
        validation_rows = self._connection.execute(
            """
            SELECT validations.analysis_run_id, validations.verdict
            FROM validations
            JOIN analysis_runs r ON r.id=validations.analysis_run_id
            WHERE r.load_case_id=?
            """,
            [load_case_id],
        ).fetchall()

        counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"scalar": 0, "time_series": 0, "fail": 0, "verdict": 0}
        )
        for run_id, result_type, result_count, fail_count, verdict_count in count_rows:
            counts[str(run_id)][str(result_type)] = int(result_count)
            if result_type == "scalar":
                counts[str(run_id)]["fail"] = int(fail_count)
                counts[str(run_id)]["verdict"] = int(verdict_count)

        traced_runs = {str(run_id) for run_id, _ in trace_rows}
        result_keys: dict[str, set[str]] = defaultdict(set)
        result_units: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
        for run_id, variable_key, actual_unit, has_unit, metadata_json in result_rows:
            identifier = str(run_id)
            if variable_key is not None:
                result_keys[identifier].add(str(variable_key))
            if has_unit and variable_key is not None:
                pair = (str(variable_key), actual_unit)
                if pair not in result_units[identifier]:
                    result_units[identifier].append(pair)
            metadata = _json_value(metadata_json) or {}
            if isinstance(metadata, dict) and metadata.get("variable_key"):
                result_keys[identifier].add(str(metadata["variable_key"]))

        catalog = {str(variable_key): unit for variable_key, unit in catalog_rows}
        validations: dict[str, list[Any]] = defaultdict(list)
        for run_id, verdict in validation_rows:
            validations[str(run_id)].append(verdict)

        evidence_by_run: dict[str, RunEvidence] = {}
        for run in run_rows:
            run_id = str(run["id"])
            run_counts = counts[run_id]
            keys = result_keys[run_id]
            evidence_by_run[run_id] = {
                "scalar_count": run_counts["scalar"],
                "series_count": run_counts["time_series"],
                "failed_scalar_verdicts": run_counts["fail"],
                "scalar_verdicts": run_counts["verdict"],
                "source_exists": run_id in traced_runs,
                "result_keys": keys,
                "result_units": result_units[run_id],
                "validation_verdicts": validations[run_id],
            }
        return {
            "runs": run_rows,
            "catalog_units": catalog,
            "evidence_by_run": evidence_by_run,
        }


class SQLAnalysisRunSummaryRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[AnalysisRunSummaryRepository]:
        with self._connection_provider() as connection:
            yield SQLAnalysisRunSummaryRepository(connection)
