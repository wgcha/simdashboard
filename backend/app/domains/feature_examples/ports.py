from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol


class FeatureExamplesRepository(Protocol):
    def data_profile(self, load_case_id: str) -> dict[str, int]: ...


class FeatureExamplesRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[FeatureExamplesRepository]: ...
