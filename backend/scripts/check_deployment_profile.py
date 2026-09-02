from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import database_settings, directory_settings, import_readiness_policy, media_storage_mode, security_settings


def main() -> None:
    profile = os.getenv("DEPLOYMENT_PROFILE", "local").strip().lower()
    if profile not in {"local", "windows-vm-intranet", "rocky8"}:
        raise RuntimeError("DEPLOYMENT_PROFILE_INVALID")
    database = database_settings()
    security = security_settings()
    directory = directory_settings()
    readiness_policy = import_readiness_policy()
    storage_mode = media_storage_mode()
    if profile == "windows-vm-intranet":
        failures: list[str] = []
        if database.backend != "postgresql":
            failures.append("POSTGRESQL_REQUIRED")
        if security.auth_mode != "oidc":
            failures.append("OIDC_REQUIRED")
        if not security.cookie_secure:
            failures.append("SECURE_COOKIE_REQUIRED")
        if directory.mode != "http":
            failures.append("HTTP_DIRECTORY_REQUIRED")
        if failures:
            raise RuntimeError("WINDOWS_VM_PROFILE_INVALID:" + ",".join(failures))
    if profile == "rocky8":
        failures = []
        if database.backend != "postgresql":
            failures.append("POSTGRESQL_REQUIRED")
        if security.auth_mode not in {"password", "oidc"}:
            failures.append("AUTH_REQUIRED")
        if not security.cookie_secure:
            failures.append("SECURE_COOKIE_REQUIRED")
        if readiness_policy != "required":
            failures.append("READINESS_MARKER_REQUIRED")
        if failures:
            raise RuntimeError("ROCKY8_PROFILE_INVALID:" + ",".join(failures))
    print(
        f"DEPLOYMENT_PROFILE_OK profile={profile} database={database.backend} "
        f"auth={security.auth_mode} directory={directory.mode} readiness={readiness_policy} "
        f"media_storage={storage_mode}"
    )


if __name__ == "__main__":
    main()
