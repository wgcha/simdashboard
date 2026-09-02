from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ReportVariablePlacement(BaseModel):
    """A stable variable placement while retaining layout-specific extensions."""

    model_config = ConfigDict(extra="allow")

    variableKey: str
    presentation: Literal["chart", "table", "both"]
    order: int


class ReportLayoutDefinition(BaseModel):
    """Stored layout JSON remains an extensible document contract."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: str
    version: int
    coverVariant: Literal["balanced", "executive", "evidence"]
    accentColor: str
    sectionOrder: list[Literal["series", "scalar", "media"]]
    variablePlacements: list[ReportVariablePlacement]
    includeMedia: bool


class ReportLayoutCatalogResponse(BaseModel):
    id: str
    name: str
    description: str | None
    version: int
    definition: ReportLayoutDefinition
    is_system: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    updated_by: str


class ReportLayoutSavedResponse(BaseModel):
    id: str
    name: str
    description: str
    version: int
    definition: ReportLayoutDefinition
    is_system: bool
    updated_at: datetime
    updated_by: str


class ReportLayoutVersionSummaryResponse(BaseModel):
    layout_id: str
    version: int
    created_by: str
    created_at: datetime
    is_valid: bool


class ReportLayoutVersionDetailResponse(BaseModel):
    layout_id: str
    version: int
    definition: ReportLayoutDefinition
    created_by: str
    created_at: datetime


class ReportLayoutDeactivationResponse(BaseModel):
    status: Literal["deactivated"]
    id: str
