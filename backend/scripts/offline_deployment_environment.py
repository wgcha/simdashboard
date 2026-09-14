"""Stage and retain private Windows offline-deployment environment state.

The release directory is disposable.  ``stage`` copies its durable service
configuration into a newly extracted release before database/account scripts
run.  ``sync`` then merges the resulting service configuration back into the
durable state directory.  The JSON receipt intentionally records no values.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values


ENV_NAMES = (".env", ".postgres-owner.env")
RECEIPT_NAME = ".offline-deployment-environment.json"
TRANSIENT_ROOT_KEYS = {
    "POSTGRES_ADMIN_URL",
    "POSTGRES_OWNER_URL",
    "SIM_DASH_OWNER_PASSWORD",
    "SIM_DASH_APP_PASSWORD",
}
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _fail(message: str) -> None:
    raise RuntimeError(f"OFFLINE_DEPLOYMENT_ENVIRONMENT_FAILED: {message}")


def _regular(path: Path, *, required: bool) -> None:
    if not path.exists():
        if required:
            _fail(f"required state file is missing: {path.name}")
        return
    if path.is_symlink() or not path.is_file():
        _fail(f"environment state must be a regular file: {path.name}")


def _values(path: Path) -> dict[str, str]:
    _regular(path, required=True)
    try:
        parsed = dotenv_values(path, encoding="utf-8-sig")
    except Exception as error:
        _fail(f"could not parse {path.name}: {type(error).__name__}")
    result: dict[str, str] = {}
    for key, value in parsed.items():
        if value is None:
            continue
        if not _KEY.fullmatch(key):
            _fail(f"invalid environment key in {path.name}")
        result[key] = value
    return result


def _encode(values: dict[str, str]) -> bytes:
    # JSON strings are valid double-quoted dotenv values and cover quotes,
    # whitespace, #, and backslashes without exposing shell interpolation.
    lines = [f"{key}={json.dumps(values[key], ensure_ascii=False)}" for key in sorted(values)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    if path.exists() and (path.is_symlink() or not path.is_file()):
        _fail(f"refusing to replace non-regular environment file: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            try:
                os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            except OSError:
                pass
        else:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt(state_dir: Path) -> Path:
    return state_dir / RECEIPT_NAME


def _write_receipt(state_dir: Path, release_dir: Path, *, action: str) -> None:
    payload = {
        "format": "simulation-workbench-offline-environment-state",
        "format_version": 1,
        "action": action,
        "release_directory": release_dir.name,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": {name: {"sha256": _digest(state_dir / name)} for name in ENV_NAMES},
    }
    _atomic_write(_receipt(state_dir), (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))


def stage(state_dir: Path, release_dir: Path) -> None:
    """Copy durable state into a release without interpreting credentials."""
    if not release_dir.is_dir() or release_dir.is_symlink():
        _fail("release directory must be a physical directory")
    for name in ENV_NAMES:
        source = state_dir / name
        _regular(source, required=True)
        destination = release_dir / name
        _atomic_write(destination, source.read_bytes())
    _write_receipt(state_dir, release_dir, action="staged")


def _merge(state_values: dict[str, str], release_values: dict[str, str], *, root_file: bool) -> dict[str, str]:
    # Retain state-only site settings (custom CORS, OIDC, storage paths, etc.)
    # but allow release scripts to deliberately scrub one-time bootstrap values.
    merged = dict(state_values)
    if root_file:
        for key in TRANSIENT_ROOT_KEYS:
            if key not in release_values:
                merged.pop(key, None)
    merged.update(release_values)
    return merged


def sync(state_dir: Path, release_dir: Path) -> None:
    """Atomically retain release-mutated service state for the next release."""
    if not release_dir.is_dir() or release_dir.is_symlink():
        _fail("release directory must be a physical directory")
    receipt = _receipt(state_dir)
    _regular(receipt, required=True)
    try:
        metadata = json.loads(receipt.read_text(encoding="utf-8"))
    except Exception as error:
        _fail(f"state receipt is invalid: {type(error).__name__}")
    if metadata.get("format") != "simulation-workbench-offline-environment-state" or metadata.get("format_version") != 1:
        _fail("state receipt format is unsupported")
    if metadata.get("release_directory") != release_dir.name:
        _fail("state receipt belongs to a different release")
    for name in ENV_NAMES:
        state_file = state_dir / name
        release_file = release_dir / name
        state_values = _values(state_file)
        release_values = _values(release_file)
        merged = _merge(state_values, release_values, root_file=name == ".env")
        _atomic_write(state_file, _encode(merged))
    _write_receipt(state_dir, release_dir, action="synced")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage and retain offline deployment environment state.")
    parser.add_argument("action", choices=("stage", "sync"))
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--release-dir", required=True, type=Path)
    args = parser.parse_args()
    state_dir = args.state_dir.resolve()
    release_dir = args.release_dir.resolve()
    if state_dir == release_dir:
        _fail("state and release directories must differ")
    if args.action == "stage":
        stage(state_dir, release_dir)
    else:
        sync(state_dir, release_dir)
    print(f"OFFLINE_DEPLOYMENT_ENVIRONMENT_{args.action.upper()}_OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"[ERROR] {error}")
        raise SystemExit(1)
