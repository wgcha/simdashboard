from __future__ import annotations

from collections.abc import Callable


def health(probe: Callable[[], None], backend: Callable[[], str]) -> dict[str, str]:
    probe()
    return {"status": "ok", "database_backend": backend()}
