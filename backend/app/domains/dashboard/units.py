"""Unit system of Case results (user decision 2026-10-17, replaces D09).

All Case result files are produced in the ton-mm-s system, so the values are
labelled with its units as they are. Nothing is converted. An explicit unit in
the source data always wins over these defaults.
"""
from __future__ import annotations

from typing import Any

UNIT_SYSTEM_ID = "TON_MM_S"
UNIT_SYSTEM_LABEL = "ton-mm-s"
LENGTH_UNIT = "mm"
STRESS_UNIT = "MPa"
TIME_UNIT = "s"
FORCE_UNIT = "N"
MASS_UNIT = "ton"

# unit_status of a value labelled from the unit system rather than the source.
UNIT_STATUS_SYSTEM = "UNIT_SYSTEM"
UNIT_STATUS_SOURCE = "SOURCE"

UNIT_SYSTEM: dict[str, Any] = {
    "id": UNIT_SYSTEM_ID, "label": UNIT_SYSTEM_LABEL,
    "length": LENGTH_UNIT, "stress": STRESS_UNIT, "time": TIME_UNIT,
    "force": FORCE_UNIT, "mass": MASS_UNIT,
}


def unit_system() -> dict[str, Any]:
    """A fresh copy for response payloads."""
    return dict(UNIT_SYSTEM)


def stress_unit(observations: list[dict[str, Any]]) -> tuple[str | None, str]:
    """Unit of a stress value: one explicit source unit wins, else the system unit.

    Different explicit units cannot be one value, so they stay unlabelled.
    """
    explicit = {str(item["unit"]).strip() for item in observations
                if str(item.get("unit") or "").strip() and item.get("unit_status") != UNIT_STATUS_SYSTEM}
    if len(explicit) == 1:
        return next(iter(explicit)), UNIT_STATUS_SOURCE
    if explicit:
        return None, "MIXED"
    return STRESS_UNIT, UNIT_STATUS_SYSTEM
