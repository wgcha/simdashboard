from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

import psycopg

from postgres_cli import command_env, connection_args, executable, parse_target


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify and restore an Analysis Canvas PostgreSQL custom-format backup.")
    parser.add_argument("backup", type=Path)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--confirm-database", required=True, help="Must exactly match the target DB name.")
    parser.add_argument("--clean", action="store_true", help="Drop objects recorded in the archive before restoring.")
    args = parser.parse_args()
    if not args.database_url:
        raise RuntimeError("DATABASE_URL이 필요합니다.")
    backup = args.backup.expanduser().resolve()
    if not backup.is_file():
        raise RuntimeError(f"백업 파일을 찾지 못했습니다: {backup}")
    target = parse_target(args.database_url)
    if args.confirm_database != target.database:
        raise RuntimeError("--confirm-database가 실제 대상 DB명과 일치하지 않아 복구를 중단했습니다.")

    manifest_path = backup.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        raise RuntimeError(f"백업 manifest를 찾지 못했습니다: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_checksum = sha256(backup)
    if manifest.get("sha256") != actual_checksum:
        raise RuntimeError("백업 SHA-256이 manifest와 일치하지 않습니다.")

    connection_url = args.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(connection_url) as connection:
        user_tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'"
        ).fetchone()[0]
    if user_tables and not args.clean:
        raise RuntimeError(f"대상 DB에 테이블 {user_tables}개가 있습니다. 빈 DB를 사용하거나 명시적으로 --clean을 지정하세요.")

    command = [executable("pg_restore"), *connection_args(target), "--exit-on-error", "--no-owner", "--no-privileges"]
    if args.clean:
        command.extend(["--clean", "--if-exists"])
    command.append(str(backup))
    subprocess.run(command, env=command_env(target), check=True)
    print(f"PostgreSQL restore completed and checksum verified: database={target.database}, backup={backup}")


if __name__ == "__main__":
    main()
