"""Create the Analysis Canvas PostgreSQL schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-28
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema_sql() -> str:
    return (Path(__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8")


def upgrade() -> None:
    schema_sql = _schema_sql()
    if op.get_bind().dialect.name == "postgresql":
        schema_sql = schema_sql.replace("content BLOB NOT NULL", "content BYTEA NOT NULL")
    for statement in schema_sql.split(";"):
        if statement.strip():
            op.execute(sa.text(statement))


def downgrade() -> None:
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS\s+([a-zA-Z0-9_]+)", _schema_sql(), flags=re.IGNORECASE)
    for table in reversed(tables):
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
