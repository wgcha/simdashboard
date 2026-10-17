"""Time convention on a KST server (docs/features/server-time.md).

Stored times are naive UTC; API strings carry an explicit offset; every
response carries the server clock and zone; PostgreSQL sessions run in UTC.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import server_clock
from app.database_connection import POSTGRES_CONNECT_ARGS, POSTGRES_SESSION_OPTIONS
from app.services import notifications
from app.services.folder_discovery_environment import iso_utc as folder_iso_utc


@pytest.fixture
def kst(monkeypatch: pytest.MonkeyPatch):
    """Process local zone = Asia/Seoul, like the production Windows server."""
    monkeypatch.setenv("TZ", "Asia/Seoul")
    monkeypatch.delenv("SIMDASH_TIMEZONE", raising=False)
    time.tzset()
    server_clock.server_timezone_name.cache_clear()
    yield
    monkeypatch.undo()
    time.tzset()
    server_clock.server_timezone_name.cache_clear()


NAIVE_UTC = datetime(2026, 10, 17, 5, 2, 3, 456789)   # = 14:02 KST


@pytest.mark.parametrize("serialize", [server_clock.iso_utc, notifications._iso, folder_iso_utc])
def test_naive_db_value_is_utc_under_kst(kst, serialize) -> None:
    text = serialize(NAIVE_UTC)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    assert parsed.utcoffset() == timedelta(0)
    assert parsed.replace(tzinfo=None) == NAIVE_UTC, "a naive stored value must not be shifted by the local zone"


@pytest.mark.parametrize("serialize", [server_clock.iso_utc, notifications._iso, folder_iso_utc])
def test_aware_value_keeps_its_instant(kst, serialize) -> None:
    aware = datetime(2026, 10, 17, 14, 2, 3, tzinfo=timezone(timedelta(hours=9)))
    parsed = datetime.fromisoformat(serialize(aware).replace("Z", "+00:00"))
    assert parsed == aware and parsed.astimezone(timezone.utc).hour == 5


def test_iso_utc_strings_and_empty(kst) -> None:
    assert server_clock.iso_utc("2026-10-17 05:02:03") == "2026-10-17T05:02:03Z"
    assert server_clock.iso_utc("2026-10-17T14:02:03+09:00") == "2026-10-17T05:02:03Z"
    assert server_clock.iso_utc(None) is None and server_clock.iso_utc("") is None
    assert server_clock.iso_utc("not a time") == "not a time"


def test_notification_written_now_reads_back_as_now(kst) -> None:
    """The notification writer stores UTC: its API string is the current instant, not 9 or 18 h off."""
    written = notifications._now()
    assert written.tzinfo is None
    instant = datetime.fromisoformat(notifications._iso(written).replace("Z", "+00:00"))
    assert abs((instant - datetime.now(timezone.utc)).total_seconds()) < 5


def test_server_zone_resolution(kst, monkeypatch: pytest.MonkeyPatch) -> None:
    assert server_clock.server_timezone_name() == "Asia/Seoul"
    assert server_clock.server_utc_offset_minutes() == 540
    monkeypatch.setenv("SIMDASH_TIMEZONE", "Asia/Tokyo")
    server_clock.server_timezone_name.cache_clear()
    assert server_clock.server_timezone_name() == "Asia/Tokyo"
    monkeypatch.setenv("SIMDASH_TIMEZONE", "Not/AZone")
    server_clock.server_timezone_name.cache_clear()
    assert server_clock.server_timezone_name() == "Asia/Seoul"   # invalid override falls back to TZ


def _clock_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/denied")
    def denied() -> None:
        from fastapi import HTTPException
        raise HTTPException(status_code=401)

    app.add_middleware(server_clock.ServerClockMiddleware)
    return app


def test_every_response_carries_server_clock(kst) -> None:
    with TestClient(_clock_app()) as client:
        for path in ("/ping", "/denied"):
            response = client.get(path)
            stamp = response.headers[server_clock.SERVER_TIME_HEADER]
            assert stamp.endswith("Z")
            instant = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            assert abs((instant - datetime.now(timezone.utc)).total_seconds()) < 5
            assert response.headers[server_clock.SERVER_OFFSET_HEADER] == "540"
            assert response.headers[server_clock.SERVER_TIMEZONE_HEADER] == "Asia/Seoul"


def test_main_app_stamps_public_health_response(kst) -> None:
    from app.main import app
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert server_clock.SERVER_TIME_HEADER in response.headers
    assert response.headers[server_clock.SERVER_TIMEZONE_HEADER] == "Asia/Seoul"


def test_postgres_sessions_are_pinned_to_utc() -> None:
    assert POSTGRES_CONNECT_ARGS == {"options": "-c timezone=UTC"} and POSTGRES_SESSION_OPTIONS == "-c timezone=UTC"


@pytest.mark.skipif(not os.getenv("SIMDASH_TEST_PG_TZ_URL"), reason="set SIMDASH_TEST_PG_TZ_URL to a PostgreSQL whose server TimeZone is Asia/Seoul")
def test_postgres_kst_server_keeps_naive_utc(kst) -> None:
    """Real PostgreSQL with server TimeZone=Asia/Seoul: the app session is UTC, so no value is shifted."""
    from app.config import PostgresPoolSettings
    from app.database_connection import PostgresConnection, _postgres_engine
    engine = _postgres_engine(os.environ["SIMDASH_TEST_PG_TZ_URL"], PostgresPoolSettings(1, 0, 5, 1, 0, 5, 60))
    with PostgresConnection(engine) as conn:
        assert conn.execute("SELECT current_setting('TimeZone')").fetchone()[0] == "UTC"
        conn.execute("CREATE TEMP TABLE tz_probe (naive TIMESTAMP, aware TIMESTAMPTZ, dflt TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO tz_probe (naive, aware) VALUES (?, ?)", [NAIVE_UTC, NAIVE_UTC])
        naive, aware, default = conn.execute("SELECT naive, aware, dflt FROM tz_probe").fetchone()
    assert naive == NAIVE_UTC
    assert aware == NAIVE_UTC.replace(tzinfo=timezone.utc), "naive UTC bound to TIMESTAMPTZ must not be read as KST"
    assert abs((default.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()) < 60, \
        "CURRENT_TIMESTAMP default into TIMESTAMP must be UTC wall time"
