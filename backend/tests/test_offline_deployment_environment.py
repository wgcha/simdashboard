from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

from scripts import offline_deployment_environment as environment


def _state(tmp_path: Path) -> tuple[Path, Path]:
    state = tmp_path / "state"
    release = tmp_path / "releases" / "20260914"
    state.mkdir(parents=True)
    release.mkdir(parents=True)
    (state / ".env").write_text(
        "DATABASE_URL='postgresql://app:p@ss word@db/service'\nCUSTOM_SECRET='keep # this'\nPOSTGRES_ADMIN_URL=postgresql://admin:one-time@db/postgres\n",
        encoding="utf-8",
    )
    (state / ".postgres-owner.env").write_text("POSTGRES_OWNER_URL='postgresql://owner:old@db/service'\n", encoding="utf-8")
    return state, release


def test_stage_and_sync_preserve_site_values_and_apply_service_updates(tmp_path: Path) -> None:
    state, release = _state(tmp_path)

    environment.stage(state, release)
    assert dotenv_values(release / ".env")["CUSTOM_SECRET"] == "keep # this"
    assert (state / environment.RECEIPT_NAME).is_file()

    (release / ".env").write_text(
        "DATABASE_URL='postgresql://app:new@db/service'\nAUTH_SECRET_KEY='new value'\n",
        encoding="utf-8",
    )
    (release / ".postgres-owner.env").write_text("POSTGRES_OWNER_URL='postgresql://owner:new@db/service'\n", encoding="utf-8")
    environment.sync(state, release)

    values = dotenv_values(state / ".env")
    assert values["DATABASE_URL"] == "postgresql://app:new@db/service"
    assert values["AUTH_SECRET_KEY"] == "new value"
    assert values["CUSTOM_SECRET"] == "keep # this"
    assert "POSTGRES_ADMIN_URL" not in values
    assert dotenv_values(state / ".postgres-owner.env")["POSTGRES_OWNER_URL"] == "postgresql://owner:new@db/service"


def test_sync_rejects_release_other_than_staged_receipt(tmp_path: Path) -> None:
    state, release = _state(tmp_path)
    environment.stage(state, release)
    other = tmp_path / "releases" / "other"
    other.mkdir(parents=True)
    (other / ".env").write_text("A=B\n", encoding="utf-8")
    (other / ".postgres-owner.env").write_text("A=B\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="different release"):
        environment.sync(state, other)


def test_stage_rejects_linked_state_file(tmp_path: Path) -> None:
    state, release = _state(tmp_path)
    linked = state / ".env"
    target = state / "real.env"
    linked.replace(target)
    try:
        linked.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows test host")

    with pytest.raises(RuntimeError, match="regular file"):
        environment.stage(state, release)
