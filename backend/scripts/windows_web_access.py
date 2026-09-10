"""Read the Windows web listener setting without loading application services.

This command intentionally exposes only the listener host and its derived mode.
It never imports the application configuration, connects to a database, or
prints environment values.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from argparse import ArgumentParser

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
TRUE_VALUES = {"1", "true", "yes", "on"}
ALLOWED_HOSTS = {"127.0.0.1", "0.0.0.0"}


def _load_environment() -> None:
    # Keep the same non-overriding precedence used by app.config: process
    # values first, followed by root .env, then backend .env.
    load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")
    load_dotenv(ROOT / "backend" / ".env", override=False, encoding="utf-8-sig")


def _is_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUE_VALUES


def main() -> int:
    parser = ArgumentParser(description="Read the Windows web listener setting.")
    parser.add_argument(
        "--check-auth",
        action="store_true",
        help="Validate that LAN HTTP can use the effective authentication settings.",
    )
    args = parser.parse_args()
    _load_environment()
    host = os.getenv("WINDOWS_WEB_HOST", "127.0.0.1").strip()
    if host not in ALLOWED_HOSTS:
        print("WINDOWS_WEB_HOST must be 127.0.0.1 or 0.0.0.0.", file=sys.stderr)
        return 2
    mode = "lan" if host == "0.0.0.0" else "local"
    if mode == "lan" and args.check_auth:
        auth_mode = os.getenv("AUTH_MODE", "disabled").strip().lower()
        if auth_mode not in {"password", "oidc"}:
            print("LAN access requires AUTH_MODE=password or AUTH_MODE=oidc.", file=sys.stderr)
            return 2
        if _is_true("AUTH_COOKIE_SECURE"):
            print("LAN HTTP access cannot use AUTH_COOKIE_SECURE=true; use an HTTPS reverse proxy.", file=sys.stderr)
            return 2
    print(json.dumps({"host": host, "mode": mode}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
