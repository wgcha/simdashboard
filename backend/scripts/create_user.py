from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import initialize_database
from app.database_connection import connect
from app.security import ROLE_LEVEL, hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or reset an Analysis Canvas user without exposing passwords on the command line.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--role", choices=sorted(ROLE_LEVEL), default="viewer")
    parser.add_argument("--replace", action="store_true", help="Reset the existing user's password, display name, role, and active status.")
    args = parser.parse_args()

    username = args.username.strip().lower()
    if not username or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in username):
        raise RuntimeError("username은 영문 소문자, 숫자, 점, 밑줄, 하이픈만 사용할 수 있습니다.")
    password = os.getenv("SIM_DASH_USER_PASSWORD") or getpass.getpass("Password (12+ characters): ")
    encoded = hash_password(password)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    initialize_database()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username=?", [username]).fetchone()
        if existing and not args.replace:
            raise RuntimeError("이미 존재하는 사용자입니다. 재설정하려면 --replace를 사용하세요.")
        if existing:
            user_id = existing[0]
            conn.execute(
                "UPDATE users SET password_hash=?, display_name=?, role=?, is_active=true, updated_at=? WHERE id=?",
                [encoded, args.display_name.strip(), args.role, now, user_id],
            )
        else:
            user_id = f"user-{uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, true, ?, ?)",
                [user_id, username, encoded, args.display_name.strip(), args.role, now, now],
            )
    print(f"사용자 준비 완료: username={username}, role={args.role}, id={user_id}")
    print("비밀번호는 출력하거나 파일에 저장하지 않았습니다.")


if __name__ == "__main__":
    main()
