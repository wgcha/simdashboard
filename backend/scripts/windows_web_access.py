"""Read the Windows web listener setting without loading application services.

This command intentionally exposes only the listener host and its derived mode.
It never imports the application configuration, connects to a database, or
prints environment values.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from argparse import ArgumentParser

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
TRUE_VALUES = {"1", "true", "yes", "on"}
ALLOWED_HOSTS = {"127.0.0.1", "0.0.0.0"}
DEFAULT_FRONTEND_PORT = 80
DEFAULT_APP_BASE_PATH = "/home/"


def _load_environment() -> None:
    # Keep the same non-overriding precedence used by app.config: process
    # values first, followed by root .env, then backend .env.
    load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")
    load_dotenv(ROOT / "backend" / ".env", override=False, encoding="utf-8-sig")


def _is_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUE_VALUES


def _app_base_path() -> str | None:
    value = os.getenv("WINDOWS_WEB_BASE_PATH", DEFAULT_APP_BASE_PATH).strip()
    if not value.startswith("/") or "//" in value or "?" in value or "#" in value:
        return None
    if value == "/":
        return value
    segments = value.strip("/").split("/")
    if any(segment in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._~-]+", segment) for segment in segments):
        return None
    return "/" + "/".join(segments) + "/"


def _listener_host() -> str:
    """Use LAN for an authenticated Windows installation, local for dev."""
    configured_host = os.getenv("WINDOWS_WEB_HOST")
    if configured_host is not None and configured_host.strip():
        return configured_host.strip()
    auth_mode = os.getenv("AUTH_MODE", "disabled").strip().lower()
    return "0.0.0.0" if auth_mode in {"password", "oidc"} else "127.0.0.1"


def main() -> int:
    parser = ArgumentParser(description="Read the Windows web listener setting.")
    parser.add_argument(
        "--check-auth",
        action="store_true",
        help="Validate that LAN HTTP can use the effective authentication settings.",
    )
    args = parser.parse_args()
    _load_environment()
    host = _listener_host()
    if host not in ALLOWED_HOSTS:
        print("WINDOWS_WEB_HOST must be 127.0.0.1 or 0.0.0.0.", file=sys.stderr)
        return 2
    mode = "lan" if host == "0.0.0.0" else "local"
    app_base_path = _app_base_path()
    if app_base_path is None:
        print("WINDOWS_WEB_BASE_PATH must be an absolute safe URL path.", file=sys.stderr)
        return 2
    if mode == "lan" and args.check_auth:
        auth_mode = os.getenv("AUTH_MODE", "disabled").strip().lower()
        if auth_mode not in {"password", "oidc"}:
            print("LAN access requires AUTH_MODE=password or AUTH_MODE=oidc.", file=sys.stderr)
            return 2
        if _is_true("AUTH_COOKIE_SECURE"):
            print("LAN HTTP access cannot use AUTH_COOKIE_SECURE=true; use an HTTPS reverse proxy.", file=sys.stderr)
            return 2
    # Keep the Windows profile portable: enabling the LAN listener on a new
    # server is enough to expose the standard HTTP /home/ address.  The
    # launcher still accepts an explicit frontend port for existing setups.
    print(
        json.dumps(
            {
                "host": host,
                "mode": mode,
                "frontend_port": DEFAULT_FRONTEND_PORT,
                "app_base_path": app_base_path,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
