from __future__ import annotations

from typing import Literal


RecalculationScope = Literal["CHASSIS_REAR", "OPEN_CELL"]


def scalar_recalculation_scope(criterion_key: str) -> RecalculationScope | None:
    if criterion_key == "chassis_rear_permanent_deformation_mm":
        return "CHASSIS_REAR"
    if criterion_key == "open_cell_stress_mpa":
        return "OPEN_CELL"
    return None
