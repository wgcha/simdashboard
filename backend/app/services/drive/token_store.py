"""Encrypted shared-account token storage (integration 02 §3, 04 §2.1).

``DbTokenStore`` implements the adapter ``TokenStore`` protocol.  The whole
``TokenBundle`` JSON is encrypted with ``Fernet(SIMDASH_SECRET_ENC_KEY)``;
only ``account_hint``/``obtained_at``/``updated_by``/``updated_at`` are kept
in clear.  ``save()`` is called from the worker notification thread after
every refresh-token rotation, so it opens its own connection and commits
before returning.  Token values are never logged or returned.

Generation fencing: every admin registration (:meth:`DbTokenStore.write`)
gives the row a new, larger ``generation``.  The store handed to a gateway
(:meth:`DbTokenStore.for_gateway`) remembers the generation it last loaded and
``save()`` is ``UPDATE ... WHERE generation = <loaded>`` only (never an insert),
so a late rotation from an old token chain can neither recreate a deleted row
nor overwrite a freshly registered account; such a save is dropped with a
warning.  Internally bundles are always :class:`LocalTokenBundle` (redacting
``repr``); the adapter's ``TokenBundle`` type is used only for values returned
by ``load()`` to the adapter.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
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


class StoreStatus:
    """Admin-visible store state shared by the admin store and every gateway store."""

    def __init__(self) -> None:
        self.last_save_error_at: datetime | None = None
        self.last_load_problem: str | None = None
        self.last_stale_save_at: datetime | None = None


class DbTokenStore:
    """``TokenStore`` over the single ``drive_credentials`` row (``id='scx'``)."""

    def __init__(self, secret_key: str, *, adapter_bundle_type: Any | None = None,
                 connect_fn: Callable[[], Any] = connect, status: StoreStatus | None = None,
                 write_lock: Any | None = None) -> None:
        if not secret_key:
            raise ValueError("SIMDASH_SECRET_ENC_KEY is required")
        self._secret_key = secret_key
        self._fernet = Fernet(secret_key.encode("ascii"))
        self._key_id = key_id(secret_key)
        self._adapter_bundle_type = adapter_bundle_type
        self._connect = connect_fn
        self._lock = write_lock if write_lock is not None else threading.Lock()
        self._status = status if status is not None else StoreStatus()
        self._loaded_generation: int | None = None

    def for_gateway(self, adapter_bundle_type: Any | None = None) -> "DbTokenStore":
        """A fresh store for one gateway instance: own loaded generation, shared admin status."""
        return DbTokenStore(self._secret_key, adapter_bundle_type=adapter_bundle_type, connect_fn=self._connect,
                            status=self._status, write_lock=self._lock)

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def loaded_generation(self) -> int | None:
        return self._loaded_generation

    @property
    def last_save_error_at(self) -> datetime | None:
        return self._status.last_save_error_at

    @property
    def last_load_problem(self) -> str | None:
        return self._status.last_load_problem

    @property
    def last_stale_save_at(self) -> datetime | None:
        return self._status.last_stale_save_at

    @staticmethod
    def make_bundle(*, server_url: str, access_token: str, refresh_token: str | None,
                    obtained_at: datetime, account_hint: str | None) -> LocalTokenBundle:
        return LocalTokenBundle(server_url=server_url, access_token=access_token, refresh_token=refresh_token,
                                obtained_at=_utc(obtained_at), account_hint=account_hint)

    @classmethod
    def to_local(cls, bundle: Any) -> LocalTokenBundle:
        if isinstance(bundle, LocalTokenBundle):
            return bundle
        return cls.make_bundle(server_url=bundle.server_url, access_token=bundle.access_token,
                               refresh_token=bundle.refresh_token, obtained_at=bundle.obtained_at,
                               account_hint=bundle.account_hint)

    def _to_adapter(self, bundle: LocalTokenBundle) -> Any:
        """Convert only at the adapter boundary (the adapter type's repr may show tokens)."""
        if self._adapter_bundle_type is None:
            return bundle
        return self._adapter_bundle_type(server_url=bundle.server_url, access_token=bundle.access_token,
                                         refresh_token=bundle.refresh_token, obtained_at=bundle.obtained_at,
                                         account_hint=bundle.account_hint)

    # -- TokenStore protocol -------------------------------------------------------------------

    def load(self) -> Any | None:
        bundle = self.load_local()
        return None if bundle is None else self._to_adapter(bundle)

    def load_local(self) -> LocalTokenBundle | None:
        status = self._status
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT ciphertext, key_id, generation FROM drive_credentials WHERE id=?",
                                   [ROW_ID]).fetchone()
        except Exception:
            logger.error("SCX token load failed (database error)")
            status.last_load_problem = "DB_ERROR"
            return None
        if row is None:
            self._loaded_generation = None
            status.last_load_problem = "MISSING"
            return None
        ciphertext, stored_key_id, generation = bytes(row[0]), str(row[1]), int(row[2])
        self._loaded_generation = None
        if stored_key_id != self._key_id:
            status.last_load_problem = "KEY_CHANGED"
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
        except (InvalidToken, ValueError, KeyError, TypeError, RecursionError):
            status.last_load_problem = "DECRYPT_FAILED"
            logger.warning("SCX token could not be decrypted; re-register the token")
            return None
        self._loaded_generation = generation
        status.last_load_problem = None
        return bundle

    def save(self, bundle: Any) -> None:
        """Persist a rotated bundle from the worker; commits before returning.

        Only updates the row generation this store last loaded.  A save for a
        deleted or re-registered row is dropped (warning log, no token content).
        """
        generation = self._loaded_generation
        if generation is None:
            self._dropped("no registered token was loaded by this gateway")
            return
        try:
            local = self.to_local(bundle)
            ciphertext = self._fernet.encrypt(bundle_to_json(local))
            obtained_at = _utc(local.obtained_at).replace(tzinfo=None)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            with self._lock, self._connect() as conn:
                updated = conn.execute(
                    "UPDATE drive_credentials SET ciphertext=?, account_hint=?, obtained_at=?, updated_by=?, updated_at=? "
                    "WHERE id=? AND generation=? AND key_id=? RETURNING id",
                    [ciphertext, local.account_hint, obtained_at, WORKER_ACTOR, now, ROW_ID, generation, self._key_id],
                ).fetchone()
                if getattr(conn, "backend", "") == "postgresql":
                    conn.execute("COMMIT")
        except Exception:
            self._status.last_save_error_at = datetime.now(timezone.utc)
            # Do not raise into the adapter's notification thread; the admin card shows the failure.
            logger.error("SCX token save failed; the next restart may require re-registration")
            return
        if updated is None:
            self._dropped("the token row was deleted or re-registered since this gateway loaded it")
            return
        self._status.last_save_error_at = None

    def _dropped(self, reason: str) -> None:
        self._status.last_stale_save_at = datetime.now(timezone.utc)
        logger.warning("SCX rotated token not saved: %s (stale token chain dropped)", reason)

    # -- Admin helpers ---------------------------------------------------------------------------

    def write(self, bundle: Any, *, updated_by: str) -> int:
        """Admin registration: replace the row with a new, larger generation; returns it."""
        local = self.to_local(bundle)
        ciphertext = self._fernet.encrypt(bundle_to_json(local))
        obtained_at = _utc(local.obtained_at).replace(tzinfo=None)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT generation FROM drive_credentials WHERE id=?", [ROW_ID]).fetchone()
            # Monotonic across DELETE: never reuse a generation an old gateway may still hold.
            generation = max(int(row[0]) + 1 if row else 1, time.time_ns() // 1000, _next_generation_floor())
            _note_generation(generation)
            conn.execute(
                "INSERT INTO drive_credentials(id,ciphertext,key_id,account_hint,obtained_at,updated_by,updated_at,generation) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET ciphertext=excluded.ciphertext, "
                "key_id=excluded.key_id, account_hint=excluded.account_hint, obtained_at=excluded.obtained_at, "
                "updated_by=excluded.updated_by, updated_at=excluded.updated_at, generation=excluded.generation",
                [ROW_ID, ciphertext, self._key_id, local.account_hint, obtained_at, updated_by, now, generation],
            )
            if getattr(conn, "backend", "") == "postgresql":
                conn.execute("COMMIT")
        return generation

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


_generation_lock = threading.Lock()
_last_generation = 0


def _next_generation_floor() -> int:
    with _generation_lock:
        return _last_generation + 1


def _note_generation(value: int) -> None:
    global _last_generation
    with _generation_lock:
        _last_generation = max(_last_generation, value)
