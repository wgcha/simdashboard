from __future__ import annotations

import csv
import io
from typing import Any


def portfolio_csv(records: list[dict[str, Any]]) -> str:
    columns = ["project_name", "product_name", "request_title", "owner", "request_status", "requested_at", "load_case_name", "analysis_type", "load_case_status", "verdict", "result_count", "completed_at"]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)
    return "\ufeff" + output.getvalue()
