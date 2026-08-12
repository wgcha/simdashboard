from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import initialize_database
from app.database_connection import connect


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve one existing OIDC account as the recovery global admin.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--confirm-user", required=True, help="Must exactly match --username.")
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    username = args.username.strip().lower()
    if args.confirm_user.strip().lower() != username:
        raise RuntimeError("RECOVERY_CONFIRMATION_MISMATCH")
    reason = args.reason.strip()
    if len(reason) < 2:
        raise RuntimeError("RECOVERY_REASON_REQUIRED")
    initialize_database()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        user = conn.execute(
            "SELECT id, oidc_issuer, oidc_subject, account_status, is_global_admin FROM users WHERE username=?",
            [username],
        ).fetchone()
        if not user:
            raise RuntimeError("OIDC_ACCOUNT_NOT_FOUND")
        if not user[1] or not user[2]:
            raise RuntimeError("ACCOUNT_IS_NOT_OIDC")
        conn.execute(
            """
            UPDATE users SET account_status='ACTIVE', is_active=true, is_global_admin=true,
                approved_by='recovery-cli', approved_at=?, updated_at=? WHERE id=?
            """,
            [now, now, user[0]],
        )
        conn.execute(
            """
            INSERT INTO audit_events
                (id, occurred_at, user_id, username, role, action, method, path,
                 status_code, request_id, client_ip, user_agent, detail_json)
            VALUES (?, ?, ?, ?, 'admin', 'GLOBAL_ADMIN_RECOVERY_APPROVED', 'CLI',
                    'backend/scripts/approve_oidc_global_admin.py', 200, ?, NULL, 'recovery-cli', ?)
            """,
            [str(uuid4()), now, user[0], username, str(uuid4()), '{"reason_recorded":true}'],
        )
    print(f"OIDC_GLOBAL_ADMIN_APPROVED username={username} user_id={user[0]}")


if __name__ == "__main__":
    main()
