from __future__ import annotations

from typing import Protocol

from .models import ProductInformation


class ProductInformationRepository(Protocol):
    def list_for_load_case(self, load_case_id: str) -> list[ProductInformation]: ...
