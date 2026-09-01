from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Callable, Protocol


class DropVideoRepository(Protocol):
    def load_case_context(self, load_case_id: str) -> tuple[Any, ...] | None: ...
    def authorize(self, callback: Callable[[object], None]) -> None: ...
    def list_drop_videos(self, load_case_id: str) -> list[dict[str, Any]]: ...


class DropVideoRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[DropVideoRepository]: ...


class DropVideoExampleSource(Protocol):
    def __call__(self, load_case_id: str) -> list[dict[str, Any]]: ...
