"""Framework-neutral use cases for result summaries and ingestion read context."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from ...domains.results.models import (
    AnalysisRunSummary,
    ResultIngestionTargetRead,
    ResultMediaAssetRead,
    ResultMediaBlobRead,
)
from ...domains.results.ports import (
    AnalysisRunSummaryRepository,
    ResultIngestionQueryPort,
    ResultMediaQueryPort,
)
from ...domains.results.policies import summarize_run

AuthorizationCheck = Callable[[], object]
AnalysisRunSummaryRepositoryProvider = Callable[
    [], AbstractContextManager[AnalysisRunSummaryRepository]
]


@dataclass(frozen=True)
class ResultIngestionContext:
    project_id: str
    request_id: str
    load_case_id: str
    chassis_threshold: float
    open_cell_threshold: float
    catalog: Mapping[str, Any]


@dataclass(frozen=True)
class ResultMediaReadContext:
    """An authorized result-media asset with its optional stored blob."""

    asset: ResultMediaAssetRead
    blob: ResultMediaBlobRead | None


ResultMediaAuthorizationCheck = Callable[[ResultMediaAssetRead], object]


def get_result_media_read(
    query: ResultMediaQueryPort,
    asset_id: str,
    authorize: ResultMediaAuthorizationCheck,
) -> ResultMediaReadContext | None:
    """Load one asset in the established metadata → permission → blob order.

    The missing-asset result remains data so the HTTP adapter can retain the
    existing 404 response.  The caller binds the query adapter and authorization
    callback to one connection so all three steps share the same scope.
    """
    asset = query.get_result_media_asset(asset_id)
    if asset is None:
        return None
    authorize(asset)
    blob = query.get_result_media_blob(asset.blob_id) if asset.blob_id else None
    return ResultMediaReadContext(asset=asset, blob=blob)


def get_result_ingestion_target(
    query: ResultIngestionQueryPort,
    load_case_id: str,
) -> ResultIngestionTargetRead | None:
    return query.get_result_ingestion_target(load_case_id)


def get_manual_result_import_context(
    query: ResultIngestionQueryPort,
    load_case_id: str,
) -> ResultIngestionContext | None:
    """Load import context in the legacy query order on one connection."""
    target = query.get_result_ingestion_target(load_case_id)
    if target is None:
        return None
    chassis_threshold = query.get_quality_threshold(
        target.project_id, "chassis_rear_permanent_deformation_mm", 5.0
    )
    open_cell_threshold = query.get_quality_threshold(
        target.project_id, "open_cell_stress_mpa", 75.0
    )
    catalog = query.list_catalog(load_case_id)
    return ResultIngestionContext(
        project_id=target.project_id,
        request_id=target.request_id,
        load_case_id=load_case_id,
        chassis_threshold=chassis_threshold,
        open_cell_threshold=open_cell_threshold,
        catalog=catalog,
    )


def list_analysis_runs(
    load_case_id: str,
    authorize: AuthorizationCheck,
    repository_provider: AnalysisRunSummaryRepositoryProvider,
) -> list[AnalysisRunSummary]:
    """Read and summarize analysis runs for the existing results slice."""
    authorize()
    with repository_provider() as repository:
        read_data = repository.read_for_load_case(load_case_id)
    latest_id = read_data["runs"][0]["id"] if read_data["runs"] else None
    return [
        summarize_run(
            run,
            read_data["evidence_by_run"][run["id"]],
            read_data["catalog_units"],
            is_latest=run["id"] == latest_id,
        )
        for run in read_data["runs"]
    ]
