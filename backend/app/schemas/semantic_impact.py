"""Transport contract for bounded semantic bundle impact previews."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


ImpactCheckStatus = Literal["READY", "BLOCK"]
ImpactValidationStatus = Literal["READY", "BLOCK", "UNVERIFIED"]
RecipeChangeClassification = Literal["UNCHANGED", "PRESENTATION_ONLY", "INTERPRETATION", "UNKNOWN"]


class ImpactProposedPair(BaseModel):
    recipe_id: str
    recipe_version: int
    template_id: str
    template_version: int


class ImpactActiveVersions(BaseModel):
    recipe: int | None
    template: int | None


class RecipeChange(BaseModel):
    classification: RecipeChangeClassification
    reason_codes: list[str]


class ImpactPairCheck(BaseModel):
    """Compatibility of one actual recipe/template pair against its sample."""

    recipe_id: str | None = None
    recipe_version: int | None = None
    template_id: str | None = None
    template_version: int | None = None
    status: ImpactCheckStatus
    reason_code: str | None = None
    widgets: list[dict[str, Any]]


class ImpactBinding(BaseModel):
    id: str
    project_id: str
    request_id: str | None = None
    load_case_id: str | None = None
    relative_path: str
    recipe_ids: list[str]
    template_id: str | None = None
    revision: int
    binding_parse_error: bool | None = None
    compatibility: list[ImpactPairCheck]


class ProtectedPriorRun(BaseModel):
    analysis_run_id: str
    load_case_id: str
    recipe_id: str
    recipe_version: int
    template_id: str | None = None
    template_version: int | None = None
    created_at: datetime


class ProtectedPriorRuns(BaseModel):
    count: int
    items: list[ProtectedPriorRun]
    truncated: bool
    immutable: bool


class PendingReviewItems(BaseModel):
    count: int | None
    available: bool


class ImpactUnverified(BaseModel):
    reason_code: str


class SampleCoverage(BaseModel):
    source: Literal["STORED_RECIPE_SAMPLES_ONLY"]
    validated_pair_count: int
    unverified_pair_count: int


class ImpactValidation(BaseModel):
    status: ImpactValidationStatus
    checks: list[ImpactPairCheck]
    truncated: bool
    unverified: list[ImpactUnverified]
    sample_coverage: SampleCoverage


class BundleImpactResponse(BaseModel):
    proposed_pair: ImpactProposedPair
    current_active_versions: ImpactActiveVersions
    recipe_change: RecipeChange
    affected_bindings: list[ImpactBinding]
    protected_prior_runs: ProtectedPriorRuns
    pending_review_items: PendingReviewItems
    validation: ImpactValidation
    activation_allowed: bool
