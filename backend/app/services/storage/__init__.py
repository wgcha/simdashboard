"""SPDM storage provider package (docs/contracts/storage-provider.md).

``get_storage_provider(conn)`` is the single way to obtain the provider for
the configured SPDM root; the root itself is resolved only by
``spdm_storage.storage_root`` (S4).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .local import LocalFsProvider, provider_for_root
from .provider import (
    FINAL, LEGACY, NOT_ALLOWED_WRITE, Entry, SpdmStorageError, StorageError, StorageProvider,
)


def get_storage_provider(conn, *, on_unset: Callable[[], BaseException] | None = None) -> LocalFsProvider | None:
    """Provider for the configured SPDM root.

    Root resolution errors (``SpdmStorageError``) propagate unchanged.  When no
    root is configured this returns ``None``, or raises ``on_unset()`` so each
    caller keeps its existing error.
    """
    from .. import spdm_storage

    root = spdm_storage.storage_root(conn).root
    if root is None:
        if on_unset is not None:
            raise on_unset()
        return None
    return LocalFsProvider(root)


def provider_root(provider: LocalFsProvider) -> Path:
    return provider.root


__all__ = [
    "Entry", "FINAL", "LEGACY", "LocalFsProvider", "NOT_ALLOWED_WRITE", "SpdmStorageError", "StorageError",
    "StorageProvider", "get_storage_provider", "provider_for_root", "provider_root",
]
