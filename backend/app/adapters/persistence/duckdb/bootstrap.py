from __future__ import annotations

from collections.abc import Callable


def initialize_duckdb_development_database(legacy_bootstrap: Callable[[], None]) -> None:
    """Run the embedded-only schema and compatibility bootstrap.

    Keeping the legacy implementation behind this explicit adapter makes the
    production boundary unambiguous without rewriting historic DuckDB DDL in a
    functional refactor. PostgreSQL must never call this entry point.
    """
    legacy_bootstrap()
