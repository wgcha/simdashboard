"""Safely check that the configured PostgreSQL backup clients can start.

The check deliberately delegates to ``backup_postgres.py --check-tools`` so
the same executable resolution used by a real backup is exercised.  It never
opens a database connection or creates a backup artifact.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from scripts.backup_failure_details import child_failure_details, exception_details  # noqa: E402
from scripts import prepare_account_deployment as deployment  # noqa: E402


_STAGE = "postgres_tools_check"


def _run_check(project_root: Path) -> None:
    """Run the backup program's read-only tool check without relaying output."""
    environment = deployment._effective_env(project_root)
    command = [sys.executable, str(BACKEND / "scripts" / "backup_postgres.py"), "--check-tools"]
    try:
        result = subprocess.run(
            command,
            cwd=BACKEND,
            env=deployment._child_environment(environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as error:
        raise deployment.AccountBackupChildError(_STAGE, error) from error
    if result.returncode:
        error = subprocess.CalledProcessError(
            result.returncode,
            command,
            output=result.stdout,
            stderr=result.stderr,
        )
        raise deployment.AccountBackupChildError(_STAGE, error) from error


def _failure_details(error: BaseException) -> dict[str, object]:
    cause = error.cause if isinstance(error, deployment.AccountBackupChildError) else error
    if isinstance(cause, subprocess.CalledProcessError):
        return child_failure_details(deployment._child_text(error))
    return exception_details(cause)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check configured pg_dump and pg_restore without contacting PostgreSQL.")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        _run_check(args.project_root)
    except Exception as error:
        wrapped = error if isinstance(error, deployment.AccountBackupChildError) else deployment.AccountBackupChildError(_STAGE, error)
        code = deployment._safe_failure_code(wrapped)
        details = _failure_details(wrapped)
        print(f"ACCOUNT_BACKUP_FAILED code={code} stage={_STAGE}", file=sys.stderr)
        if details:
            print("ACCOUNT_BACKUP_DETAIL " + json.dumps(details, sort_keys=True), file=sys.stderr)
        print(f"ACCOUNT_BACKUP_ACTION {deployment._REMEDIATION[code]}", file=sys.stderr)
        return 1
    print("POSTGRES_BACKUP_TOOLS_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
