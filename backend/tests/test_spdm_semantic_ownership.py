from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.database import initialize_database
from app.database_connection import connect
from app.services.spdm_storage import SpdmStorageError, bind_existing_target, discover_bindings


RELATIVE = "Project_0002_MODEL_pv1/WR_0002_SimType1/CAE/Assy_Model/CMS"


def _semantic_binding(conn, relative_path: str) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn.execute(
        "INSERT INTO semantic_folder_bindings VALUES (?, ?, NULL, NULL, ?, 'PROJECT', ?, NULL, ?, ?, ?, 1)",
        ["semantic-owner", "project-tv-001", relative_path, json.dumps([]), now, now, "test"],
    )


@pytest.mark.duckdb_integration
def test_legacy_binding_rejects_semantic_owned_ancestor(tmp_path) -> None:
    root = tmp_path / "spdm"
    root.mkdir()
    initialize_database()
    with connect() as conn:
        # Windows path identity is case-insensitive, including ownership
        # boundaries supplied by a separately configured collector.
        _semantic_binding(conn, "PROJECT_0002_MODEL_PV1")
        with pytest.raises(SpdmStorageError) as error:
            bind_existing_target(conn, root, "loadcase-drop-bottom-001", RELATIVE)
    assert error.value.code == "SPDM_SEMANTIC_PATH_OWNED"


@pytest.mark.duckdb_integration
def test_legacy_discovery_rejects_semantic_owned_leaf_without_binding(tmp_path) -> None:
    root = tmp_path / "spdm"
    root.joinpath(*RELATIVE.split("/")).mkdir(parents=True)
    initialize_database()
    with connect() as conn:
        _semantic_binding(conn, RELATIVE)
        with pytest.raises(SpdmStorageError) as error:
            discover_bindings(conn, root)
        assert conn.execute("SELECT count(*) FROM spdm_storage_bindings").fetchone()[0] == 0
    assert error.value.code == "SPDM_SEMANTIC_PATH_OWNED"
