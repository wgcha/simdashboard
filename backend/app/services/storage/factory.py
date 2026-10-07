"""Single injection point for SPDM storage providers (scx-drive plan D1).

Code outside this package never constructs ``LocalFsProvider`` itself; it calls
``provider_for_root(root)`` (or ``get_storage_provider(conn)``, which resolves the
configured root first).  Today the factory returns ``LocalFsProvider(root)``
(no behaviour change); stage D2 makes it return the drive provider in ``scx``
mode.  Tests replace the factory with ``set_provider_factory`` or the
``override_provider_factory`` context manager.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .local import LocalFsProvider

ProviderFactory = Callable[[Path], Any]

_factory: ProviderFactory | None = None


def provider_for_root(root: Path | str) -> LocalFsProvider:
    """Provider for an already-resolved SPDM root (the only constructor seam)."""
    factory = _factory
    if factory is None:
        return LocalFsProvider(root)
    return factory(Path(root))


def set_provider_factory(factory: ProviderFactory | None) -> ProviderFactory | None:
    """Install ``factory`` (``None`` restores the default) and return the previous one."""
    global _factory
    previous = _factory
    _factory = factory
    return previous


@contextmanager
def override_provider_factory(factory: ProviderFactory) -> Iterator[None]:
    previous = set_provider_factory(factory)
    try:
        yield
    finally:
        set_provider_factory(previous)


__all__ = ["ProviderFactory", "override_provider_factory", "provider_for_root", "set_provider_factory"]
