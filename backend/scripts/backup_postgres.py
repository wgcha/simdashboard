from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from postgres_cli import command_env, connection_args, executable, parse_target


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and verify an Analysis Canvas PostgreSQL custom-format backup.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "backups")
    parser.add_argument("--label", default="analysis-canvas")
    args = parser.parse_args()
    if not args.database_url:
        raise RuntimeError("DATABASE_URL이 필요합니다.")

    target = parse_target(args.database_url)
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = output_dir / f"{args.label}-{timestamp}.dump"
    temporary_path = final_path.with_suffix(".dump.partial")
    manifest_path = final_path.with_suffix(".manifest.json")
    environment = command_env(target)
    dump_command = [
        executable("pg_dump"), *connection_args(target), "--format=custom", "--compress=6",
        "--no-owner", "--no-privileges", "--file", str(temporary_path),
    ]
    subprocess.run(dump_command, env=environment, check=True)
    subprocess.run([executable("pg_restore"), "--list", str(temporary_path)], env=environment, check=True, stdout=subprocess.DEVNULL)
    temporary_path.replace(final_path)
    manifest = {
        "format": "postgresql-custom",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": target.database,
        "filename": final_path.name,
        "bytes": final_path.stat().st_size,
        "sha256": sha256(final_path),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PostgreSQL backup verified: file={final_path}, bytes={manifest['bytes']}, sha256={manifest['sha256']}")
    print(f"manifest={manifest_path}")


if __name__ == "__main__":
    main()
