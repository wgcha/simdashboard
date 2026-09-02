"""Typed boundary between result parsers and result persistence.

The legacy parsers pre-date a shared result contract and consequently return
slightly different dictionaries.  This module is the only place where those
parser dictionaries are interpreted.  Persistence receives dictionaries only
from the typed models' explicit conversion methods, never directly from a
parser.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from numbers import Real
from typing import Any, Mapping, Sequence

from ..schemas.result_import import ResultType


class ResultContractError(ValueError):
    """Raised when a parser output cannot be trusted for persistence."""


@dataclass(frozen=True)
class ResultProvenance:
    """Origin information carried with every normalized result row."""

    source_path: str
    result_type: str
    parser: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("source_path", "result_type", "parser"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ResultContractError(f"provenance.{name} must be a non-empty string")


@dataclass(frozen=True)
class ScalarResultPayload:
    variable_key: str
    display_name: str
    value_double: float
    unit: str | None
    threshold_double: float | None
    criterion_key: str | None
    provenance: ResultProvenance
    verdict: str | None = None

    def with_verdict(self, verdict: str) -> "ScalarResultPayload":
        if not isinstance(verdict, str) or not verdict.strip():
            raise ResultContractError("scalar verdict must be a non-empty string")
        return replace(self, verdict=verdict)

    def for_verdict(self) -> dict[str, Any]:
        """Return the narrow scalar shape used by compatibility evaluators."""

        return {
            "variable_key": self.variable_key,
            "display_name": self.display_name,
            "value_double": self.value_double,
            "unit": self.unit,
            "threshold_double": self.threshold_double,
            "criterion_key": self.criterion_key,
            "verdict": self.verdict,
        }

    def for_persistence(self) -> dict[str, Any]:
        """Return the DB row shape without leaking provenance into the schema."""

        return self.for_verdict()


@dataclass(frozen=True)
class TimeSeriesResultPayload:
    variable_key: str
    display_name: str
    time_value: float
    value_double: float
    time_unit: str | None
    value_unit: str | None
    provenance: ResultProvenance

    def for_persistence(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "variable_key": self.variable_key,
            "display_name": self.display_name,
            "time_value": self.time_value,
            "value_double": self.value_double,
        }
        # Keep the compatibility defaults for parsers that did not emit
        # units (open-cell history historically defaults time to seconds).
        if self.time_unit is not None:
            row["time_unit"] = self.time_unit
        if self.value_unit is not None:
            row["value_unit"] = self.value_unit
        return row


@dataclass(frozen=True)
class NormalizedResultPayload:
    """A parser-independent result bundle shared by legacy and canonical adapters."""

    scalars: tuple[ScalarResultPayload, ...] = ()
    time_series: tuple[TimeSeriesResultPayload, ...] = ()

    def extend(self, other: "NormalizedResultPayload") -> "NormalizedResultPayload":
        return NormalizedResultPayload(
            scalars=self.scalars + other.scalars,
            time_series=self.time_series + other.time_series,
        )

    def scalar_persistence_rows(self) -> list[dict[str, Any]]:
        return [item.for_persistence() for item in self.scalars]

    def time_series_persistence_rows(self) -> list[dict[str, Any]]:
        return [item.for_persistence() for item in self.time_series]


def _result_type_value(result_type: ResultType | str) -> str:
    value = result_type.value if isinstance(result_type, ResultType) else result_type
    if not isinstance(value, str) or not value.strip():
        raise ResultContractError("result_type must be a non-empty string")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResultContractError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ResultContractError(f"{label} must be an array")
    return value


def _text(value: Any, label: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ResultContractError(f"{label} must be a non-empty string")
    return value.strip()


def _number(value: Any, label: str, *, required: bool = True) -> float | None:
    if value is None and not required:
        return None
    # Do not coerce strings here.  A parser that emits a numeric string is
    # malformed and must fail closed before it reaches a database adapter.
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ResultContractError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ResultContractError(f"{label} must be a finite number")
    return result


def _normalize_scalar(
    raw: Any,
    index: int,
    provenance: ResultProvenance,
) -> ScalarResultPayload:
    item = _mapping(raw, f"scalars[{index}]")
    variable_key = _text(item.get("variable_key"), f"scalars[{index}].variable_key")
    display_name = _text(item.get("display_name", variable_key), f"scalars[{index}].display_name")
    value = item.get("value_double")
    if value is None and "value" in item:
        value = item["value"]
    threshold = item.get("threshold_double")
    if threshold is None and "threshold" in item:
        threshold = item["threshold"]
    return ScalarResultPayload(
        variable_key=variable_key or "",
        display_name=display_name or "",
        value_double=_number(value, f"scalars[{index}].value_double") or 0.0,
        unit=_text(item.get("unit"), f"scalars[{index}].unit", required=False),
        threshold_double=_number(threshold, f"scalars[{index}].threshold_double", required=False),
        criterion_key=_text(item.get("criterion_key"), f"scalars[{index}].criterion_key", required=False),
        provenance=provenance,
        verdict=_text(item.get("verdict"), f"scalars[{index}].verdict", required=False),
    )


def _normalize_time_series(
    raw: Any,
    index: int,
    provenance: ResultProvenance,
) -> TimeSeriesResultPayload:
    item = _mapping(raw, f"time_series[{index}]")
    variable_key = _text(item.get("variable_key"), f"time_series[{index}].variable_key")
    display_name = _text(item.get("display_name", variable_key), f"time_series[{index}].display_name")
    time_value = item.get("time_value")
    if time_value is None and "time" in item:
        time_value = item["time"]
    value = item.get("value_double")
    if value is None and "value" in item:
        value = item["value"]
    return TimeSeriesResultPayload(
        variable_key=variable_key or "",
        display_name=display_name or "",
        time_value=_number(time_value, f"time_series[{index}].time_value") or 0.0,
        value_double=_number(value, f"time_series[{index}].value_double") or 0.0,
        time_unit=_text(item.get("time_unit"), f"time_series[{index}].time_unit", required=False),
        value_unit=_text(item.get("value_unit"), f"time_series[{index}].value_unit", required=False),
        provenance=provenance,
    )


def normalize_parser_output(
    raw_output: Any,
    *,
    result_type: ResultType | str,
    provenance: ResultProvenance,
) -> NormalizedResultPayload:
    """Adapt one legacy parser output into the shared typed payload.

    The expected top-level shape is selected by result type because the
    historical parsers intentionally do not share one shape.  Every field is
    validated before a persistence row can be produced.
    """

    kind = _result_type_value(result_type)
    if provenance.result_type != kind:
        raise ResultContractError("provenance.result_type does not match result_type")

    if kind == ResultType.OPEN_CELL_STRESS.value:
        output = _mapping(raw_output, "open-cell parser output")
        raw_scalars = _sequence(output.get("scalars"), "open-cell scalars")
        raw_series = _sequence(output.get("time_series"), "open-cell time_series")
    elif kind == ResultType.CHASSIS_REAR_DEFORMATION.value:
        raw_scalars = _sequence(raw_output, "chassis-rear parser output")
        raw_series = ()
    elif kind == ResultType.GENERIC_TIME_HISTORY.value:
        raw_scalars = ()
        raw_series = _sequence(raw_output, "generic time-history parser output")
    elif kind == ResultType.SCALAR_RESULTS.value:
        raw_scalars = _sequence(raw_output, "scalar parser output")
        raw_series = ()
    else:
        raise ResultContractError(f"unsupported parser result type: {kind}")

    return NormalizedResultPayload(
        scalars=tuple(_normalize_scalar(item, index, provenance) for index, item in enumerate(raw_scalars)),
        time_series=tuple(
            _normalize_time_series(item, index, provenance) for index, item in enumerate(raw_series)
        ),
    )
