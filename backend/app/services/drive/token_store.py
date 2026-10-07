"""Encrypted shared-account token storage (integration 02 §3, 04 §2.1).

``DbTokenStore`` implements the adapter ``TokenStore`` protocol.  The whole
``TokenBundle`` JSON is encrypted with ``Fernet(SIMDASH_SECRET_ENC_KEY)``;
only ``account_hint``/``obtained_at``/``updated_by``/``updated_at`` are kept
in clear.  ``save()`` is called from the worker notification thread after
every refresh-token rotation, so it opens its own connection and commits
before returning.  Token values are never logged or returned.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken

from ...database_connection import connect

logger = logging.getLogger("app.services.drive.token_store")

ROW_ID = "scx"
WORKER_ACTOR = "worker"


@dataclass(frozen=True)
class LocalTokenBundle:
    """Same fields as the adapter ``TokenBundle`` (contract §4.1), used without the adapter."""

    server_url: str
    access_token: str
    refresh_token: str | None
    obtained_at: datetime
    account_hint: str | None

    def __repr__(self) -> str:  # never expose token values through repr/logging
        return f"TokenBundle(server_url={self.server_url!r}, account_hint={self.account_hint!r}, obtained_at={self.obtained_at!r})"


def key_id(secret_key: str) -> str:
    return hashlib.sha256(secret_key.encode("utf-8")).hexdigest()[:8]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_obtained_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return _utc(datetime.fromisoformat(text))
    raise ValueError("obtained_at must be an ISO-8601 timestamp")


def bundle_to_json(bundle: Any) -> bytes:
    return json.dumps({
        "server_url": bundle.server_url,
        "access_token": bundle.access_token,
        "refresh_token": bundle.refresh_token,
        "obtained_at": _utc(bundle.obtained_at).isoformat(timespec="microseconds"),
        "account_hint": bundle.account_hint,
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class DbTokenStore:
    """``TokenStore`` over the single ``drive_credentials`` row (``id='scx'``)."""

    def __init__(self, secret_key: str, *, bundle_factory: Callable[..., Any] | None = None,
                 connect_fn: Callable[[], Any] = connect) -> None:
        if not secret_key:
            raise ValueError("SIMDASH_SECRET_ENC_KEY is required")
        self._fernet = Fernet(secret_key.encode("ascii"))
        self._key_id = key_id(secret_key)
        self._bundle_factory = bundle_factory or LocalTokenBundle
        self._connect = connect_fn
        self._lock = threading.Lock()
        self.last_save_error_at: datetime | None = None
        self.last_load_problem: str | None = None

    @property
    def key_id(self) -> str:
        return self._key_id

    def make_bundle(self, *, server_url: str, access_token: str, refresh_token: str | None,
                    obtained_at: datetime, account_hint: str | None) -> Any:
        return self._bundle_factory(server_url=server_url, access_token=access_token, refresh_token=refresh_token,
                                    obtained_at=_utc(obtained_at), account_hint=account_hint)

    # -- TokenStore protocol -------------------------------------------------------------------

    def load(self) -> Any | None:
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT ciphertext, key_id FROM drive_credentials WHERE id=?", [ROW_ID]).fetchone()
        except Exception:
            logger.error("SCX token load failed (database error)")
            self.last_load_problem = "DB_ERROR"
            return None
        if row is None:
            self.last_load_problem = "MISSING"
            return None
        ciphertext, stored_key_id = bytes(row[0]), str(row[1])
        if stored_key_id != self._key_id:
            self.last_load_problem = "KEY_CHANGED"
            logger.warning("SCX token key id mismatch: stored=%s current=%s; re-register the token", stored_key_id, self._key_id)
            return None
        try:
            payload = json.loads(self._fernet.decrypt(ciphertext).decode("utf-8"))
            bundle = self.make_bundle(
                server_url=str(payload["server_url"]),
                access_token=str(payload["access_token"]),
                refresh_token=payload.get("refresh_token"),
                obtained_at=parse_obtained_at(payload["obtained_at"]),
                account_hint=payload.get("account_hint"),
            )
        except (InvalidToken, ValueError, KeyError, TypeError):
            self.last_load_problem = "DECRYPT_FAILED"
            logger.warning("SCX token could not be decrypted; re-register the token")
            return None
        self.last_load_problem = None
        return bundle

    def save(self, bundle: Any) -> None:
        """Persist a rotated bundle from the worker; commits before returning."""
        try:
            self.write(bundle, updated_by=WORKER_ACTOR)
        except Exception:
            self.last_save_error_at = datetime.now(timezone.utc)
            # Do not raise into the adapter's notification thread; the admin card shows the failure.
            logger.error("SCX token save failed; the next restart may require re-registration")

    # -- Admin helpers ---------------------------------------------------------------------------

    def write(self, bundle: Any, *, updated_by: str) -> None:
        ciphertext = self._fernet.encrypt(bundle_to_json(bundle))
        obtained_at = _utc(bundle.obtained_at).replace(tzinfo=None)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO drive_credentials(id,ciphertext,key_id,account_hint,obtained_at,updated_by,updated_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET ciphertext=excluded.ciphertext, "
                "key_id=excluded.key_id, account_hint=excluded.account_hint, obtained_at=excluded.obtained_at, "
                "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                [ROW_ID, ciphertext, self._key_id, bundle.account_hint, obtained_at, updated_by, now],
            )
            if getattr(conn, "backend", "") == "postgresql":
                conn.execute("COMMIT")
        if updated_by == WORKER_ACTOR:
            self.last_save_error_at = None

    def clear(self) -> bool:
        with self._lock, self._connect() as conn:
            existed = conn.execute("SELECT 1 FROM drive_credentials WHERE id=?", [ROW_ID]).fetchone() is not None
            conn.execute("DELETE FROM drive_credentials WHERE id=?", [ROW_ID])
        return existed

    def metadata(self) -> dict[str, Any]:
        """Clear-text columns only; never the ciphertext or token values."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT account_hint, obtained_at, updated_by, updated_at, key_id FROM drive_credentials WHERE id=?",
                [ROW_ID],
            ).fetchone()
        if row is None:
            return {"present": False, "account_hint": None, "obtained_at": None, "updated_by": None,
                    "updated_at": None, "key_matches": None}

        def stamp(value: Any) -> str | None:
            return _utc(value).isoformat() if isinstance(value, datetime) else None

        return {"present": True, "account_hint": row[0], "obtained_at": stamp(row[1]), "updated_by": row[2],
                "updated_at": stamp(row[3]), "key_matches": str(row[4]) == self._key_id}
