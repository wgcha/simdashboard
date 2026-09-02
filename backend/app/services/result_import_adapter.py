"""Legacy parser adapter for the shared result import contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..parsers.chassis_rear_parser import ChassisRearParser
from ..parsers.generic_time_history_parser import GenericTimeHistoryParser
from ..parsers.open_cell_parser import OpenCellParser
from ..parsers.scalar_result_parser import ScalarResultParser
from ..schemas.result_import import ResultFile, ResultType
from .result_import_contract import (
    NormalizedResultPayload,
    ResultContractError,
    ResultProvenance,
    normalize_parser_output,
)


class LegacyResultParserAdapter:
    """Run a legacy parser and expose only normalized typed results."""

    _PARSERS: dict[ResultType, tuple[type[Any], str]] = {
        ResultType.OPEN_CELL_STRESS: (OpenCellParser, "OpenCellParser"),
        ResultType.CHASSIS_REAR_DEFORMATION: (ChassisRearParser, "ChassisRearParser"),
        ResultType.GENERIC_TIME_HISTORY: (GenericTimeHistoryParser, "GenericTimeHistoryParser"),
        ResultType.SCALAR_RESULTS: (ScalarResultParser, "ScalarResultParser"),
    }

    def parse_file(
        self,
        result_file: ResultFile,
        file_path: Path,
        *,
        source_path: str | None = None,
    ) -> NormalizedResultPayload:
        try:
            parser_type, parser_name = self._PARSERS[result_file.type]
        except KeyError as exc:
            raise ResultContractError(f"unsupported legacy result type: {result_file.type}") from exc

        raw_output = parser_type().parse(file_path)
        provenance = ResultProvenance(
            source_path=source_path or str(file_path),
            result_type=result_file.type.value,
            parser=parser_name,
            metadata=dict(result_file.metadata or {}),
        )
        return normalize_parser_output(
            raw_output,
            result_type=result_file.type,
            provenance=provenance,
        )
