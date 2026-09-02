from __future__ import annotations

from contextlib import AbstractContextManager
from collections.abc import Mapping, Sequence
from typing import Protocol, TypeAlias


DataProfile: TypeAlias = dict[str, int]


class FeatureExamplesRepository(Protocol):
    def data_profiles(self, load_case_ids: Sequence[str]) -> Mapping[str, DataProfile]: ...


class FeatureExamplesRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[FeatureExamplesRepository]: ...
