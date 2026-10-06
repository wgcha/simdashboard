"""Simulate a database before migration 0034 (legacy, pre-DEPTH_V1 profiles active).

Suites that create registrations with legacy folder layouts (``WR_x_SimType1``,
no Working, name-pattern roles) use this so they keep covering the legacy
refresh/capture path that existing registrations still depend on (contract
docs/contracts/depth-schema.md D9, §5.6).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator

import pytest

from app.database_connection import connect


def activate_legacy_profiles() -> None:
    with connect() as conn:
        for profile_id, raw in conn.execute("SELECT id,rules_json FROM folder_environment_profiles").fetchall():
            rules = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(rules, dict):
                continue
            metadata = dict(rules.get("profile_metadata") or {})
            if rules.get("format") == "DEPTH_V1":
                metadata["archived"] = True
            elif metadata.get("superseded_by"):
                metadata = {key: value for key, value in metadata.items()
                            if key not in {"archived", "archived_revision", "superseded_by"}}
            else:
                continue
            rules["profile_metadata"] = metadata
            if not metadata:
                rules.pop("profile_metadata")
            conn.execute("UPDATE folder_environment_profiles SET rules_json=? WHERE id=?",
                         [json.dumps(rules, ensure_ascii=False), profile_id])


@contextmanager
def legacy_profiles_active() -> Iterator[None]:
    """Activate legacy profiles and restore the stored rules afterwards.

    A PostgreSQL test database is shared across tests (no per-test copy), so a
    test that switches profiles must put them back for the tests that follow.
    """
    with connect() as conn:
        saved = conn.execute("SELECT id,rules_json FROM folder_environment_profiles").fetchall()
    activate_legacy_profiles()
    try:
        yield
    finally:
        with connect() as conn:
            for profile_id, raw in saved:
                value = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                conn.execute("UPDATE folder_environment_profiles SET rules_json=? WHERE id=?", [value, profile_id])


@pytest.fixture
def legacy_profiles():
    activate_legacy_profiles()
