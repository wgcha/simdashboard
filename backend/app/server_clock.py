"""Server clock reference for the UI (docs/features/server-time.md).

Every HTTP response carries

* ``X-Server-Time``: the server's current time, UTC, ISO 8601 with ``Z`` (ms);
* ``X-Server-UTC-Offset-Minutes``: the server's current UTC offset (KST: ``540``);
* ``X-Server-Timezone``: the server's IANA zone (``Asia/Seoul``) when it can be
  resolved (``SIMDASH_TIMEZONE``, ``TZ``, ``/etc/localtime``, Windows registry).

The UI measures its clock offset from these headers, so relative times
("3분 전") follow the server clock even when a PC clock is wrong, and absolute
times are shown in the server's zone.

Convention for stored times: naive ``TIMESTAMP`` columns hold UTC; API strings
carry an explicit offset (``Z``/``+00:00``); the UI converts for display.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SERVER_TIME_HEADER = "X-Server-Time"
SERVER_OFFSET_HEADER = "X-Server-UTC-Offset-Minutes"
SERVER_TIMEZONE_HEADER = "X-Server-Timezone"

# Windows time zone key -> IANA (the zones this deployment is likely to use).
_WINDOWS_ZONES = {
    "Korea Standard Time": "Asia/Seoul",
    "Tokyo Standard Time": "Asia/Tokyo",
    "China Standard Time": "Asia/Shanghai",
    "Taipei Standard Time": "Asia/Taipei",
    "Singapore Standard Time": "Asia/Singapore",
    "India Standard Time": "Asia/Kolkata",
    "SE Asia Standard Time": "Asia/Bangkok",
    "UTC": "UTC",
    "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Pacific Standard Time": "America/Los_Angeles",
    "Mountain Standard Time": "America/Denver",
    "Central Standard Time": "America/Chicago",
    "Eastern Standard Time": "America/New_York",
}


def _valid_zone(name: str | None) -> str | None:
    text = (name or "").strip().lstrip(":")
    if not text:
        return None
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    return text


def _localtime_zone() -> str | None:
    try:
        target = str(Path("/etc/localtime").resolve())
    except OSError:
        return None
    marker = "/zoneinfo/"
    return _valid_zone(target.split(marker, 1)[1]) if marker in target else None


def _windows_zone() -> str | None:
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation") as key:
            name = str(winreg.QueryValueEx(key, "TimeZoneKeyName")[0]).strip("\x00 ")
    except OSError:
        return None
    return _valid_zone(_WINDOWS_ZONES.get(name))


@lru_cache(maxsize=1)
def server_timezone_name() -> str | None:
    """IANA name of the server zone, or ``None`` when only the offset is known."""
    return (_valid_zone(os.getenv("SIMDASH_TIMEZONE")) or _valid_zone(os.getenv("TZ"))
            or _localtime_zone() or _windows_zone())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def server_utc_offset_minutes(now: datetime | None = None) -> int:
    moment = now or utc_now()
    name = server_timezone_name()
    local = moment.astimezone(ZoneInfo(name)) if name else moment.astimezone()
    offset = local.utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


def iso_utc(value: Any) -> str | None:
    """API form of a stored time: ISO 8601 UTC with ``Z``; naive values are UTC (the storage convention)."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return value
    if not isinstance(value, datetime):
        return str(value)
    stamp = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def clock_headers(now: datetime | None = None) -> list[tuple[bytes, bytes]]:
    moment = now or utc_now()
    headers = [
        (SERVER_TIME_HEADER.lower().encode(), moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z").encode()),
        (SERVER_OFFSET_HEADER.lower().encode(), str(server_utc_offset_minutes(moment)).encode()),
    ]
    name = server_timezone_name()
    if name:
        headers.append((SERVER_TIMEZONE_HEADER.lower().encode(), name.encode()))
    return headers


class ServerClockMiddleware:
    """Pure ASGI middleware (streaming-safe) that stamps every HTTP response with the server clock."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def stamped(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                names = {SERVER_TIME_HEADER.lower().encode(), SERVER_OFFSET_HEADER.lower().encode(), SERVER_TIMEZONE_HEADER.lower().encode()}
                headers = [item for item in message.get("headers", []) if item[0].lower() not in names]
                message = {**message, "headers": headers + clock_headers()}
            await send(message)

        await self.app(scope, receive, stamped)
