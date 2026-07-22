from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from ..database import rows


class PortfolioRepository:
    """운영 대시보드의 일관된 레코드 grain과 집계를 제공한다.

    Grain은 하중 경우 1건이며, 결과 판정은 가장 최근 해석 실행만 사용한다.
    """

    def __init__(self, conn: Any):
        self.conn = conn

    def overview(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        project_id: str | None = None,
        analysis_type: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        raw = rows(self.conn.execute(
            """
            WITH latest_run AS (
                SELECT *, row_number() OVER (PARTITION BY load_case_id ORDER BY run_no DESC, completed_at DESC) AS rn
                FROM analysis_runs
            ), verdicts AS (
                SELECT analysis_run_id,
                       CASE WHEN sum(CASE WHEN verdict = 'FAIL' THEN 1 ELSE 0 END) > 0 THEN 'FAIL'
                            WHEN count(*) > 0 THEN 'PASS' ELSE 'NO_DATA' END AS verdict,
                       count(*) AS result_count
                FROM scalar_results GROUP BY analysis_run_id
            )
            SELECT p.id AS project_id, p.name AS project_name, p.product_name,
                   ar.id AS request_id, ar.title AS request_title, ar.owner,
                   ar.status AS request_status, ar.requested_at, ar.due_at,
                   lc.id AS load_case_id, lc.name AS load_case_name,
                   lc.analysis_type, lc.status AS load_case_status,
                   lr.id AS run_id, lr.completed_at,
                   coalesce(v.verdict, 'NO_DATA') AS verdict,
                   coalesce(v.result_count, 0) AS result_count
            FROM projects p
            JOIN analysis_requests ar ON ar.project_id = p.id
            JOIN load_cases lc ON lc.request_id = ar.id
            LEFT JOIN latest_run lr ON lr.load_case_id = lc.id AND lr.rn = 1
            LEFT JOIN verdicts v ON v.analysis_run_id = lr.id
            ORDER BY ar.requested_at DESC, p.name, ar.title, lc.name
            """
        ))
        query = (search or "").strip().casefold()

        def selected(item: dict[str, Any]) -> bool:
            requested = item["requested_at"].date() if isinstance(item["requested_at"], datetime) else date.fromisoformat(str(item["requested_at"])[:10])
            haystack = " ".join(str(item.get(key) or "") for key in ("project_name", "product_name", "request_title", "load_case_name", "owner")).casefold()
            return (
                (date_from is None or requested >= date_from)
                and (date_to is None or requested <= date_to)
                and (not project_id or item["project_id"] == project_id)
                and (not analysis_type or item["analysis_type"] == analysis_type)
                and (not status or item["request_status"] == status)
                and (not query or query in haystack)
            )

        records = [item for item in raw if selected(item)]
        status_counts = Counter(item["request_status"] for item in records)
        type_counts = Counter(item["analysis_type"] for item in records)
        verdict_counts = Counter(item["verdict"] for item in records)
        trend: dict[str, dict[str, int]] = defaultdict(lambda: {"requests": 0, "completed": 0, "failed": 0})
        quality: dict[str, Counter[str]] = defaultdict(Counter)
        for item in records:
            day = str(item["requested_at"])[:10]
            trend[day]["requests"] += 1
            trend[day]["completed"] += int(item["run_id"] is not None)
            trend[day]["failed"] += int(item["verdict"] == "FAIL")
            quality[item["analysis_type"]][item["verdict"]] += 1

        judged = verdict_counts["PASS"] + verdict_counts["FAIL"]
        kpis = {
            "load_cases": len(records),
            "requests": len({item["request_id"] for item in records}),
            "in_progress": sum(1 for item in records if item["request_status"] == "IN_PROGRESS"),
            "completed_runs": sum(1 for item in records if item["run_id"] is not None),
            "failed": verdict_counts["FAIL"],
            "pass_rate": round(verdict_counts["PASS"] / judged * 100, 1) if judged else None,
        }
        return {
            "grain": "LOAD_CASE_LATEST_RUN",
            "source": "DuckDB · projects/analysis_requests/load_cases/latest analysis_run",
            "freshness": max((str(item["completed_at"]) for item in records if item["completed_at"]), default=None),
            "kpis": kpis,
            "trend": [{"date": key, **value} for key, value in sorted(trend.items())],
            "status_distribution": [{"name": key, "value": value} for key, value in sorted(status_counts.items())],
            "type_distribution": [{"name": key, "value": value} for key, value in sorted(type_counts.items())],
            "quality_by_type": [{"type": key, "pass": value["PASS"], "fail": value["FAIL"], "no_data": value["NO_DATA"]} for key, value in sorted(quality.items())],
            "records": records,
            "filter_options": {
                "projects": [{"id": item[0], "name": item[1]} for item in self.conn.execute("SELECT id, name FROM projects ORDER BY name").fetchall()],
                "analysis_types": sorted({item["analysis_type"] for item in raw}),
                "statuses": sorted({item["request_status"] for item in raw}),
            },
        }

    @staticmethod
    def to_csv(records: list[dict[str, Any]]) -> str:
        columns = ["project_name", "product_name", "request_title", "owner", "request_status", "requested_at", "load_case_name", "analysis_type", "load_case_status", "verdict", "result_count", "completed_at"]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
        return "\ufeff" + output.getvalue()
