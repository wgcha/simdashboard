from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LocalHelperDistributionReady(BaseModel):
    """A public, secret-free description of the Windows helper archive."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready"] = "ready"
    version: str = Field(min_length=1, max_length=100)
    filename: str = Field(pattern=r"^[A-Za-z0-9._-]+\.zip$")
    artifact_url: Literal["/api/local-helper/distribution/download"] = "/api/local-helper/distribution/download"
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0)
    released_at: datetime


class LocalHelperDistributionUnavailable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["unavailable"] = "unavailable"
    reason: str = Field(min_length=1, max_length=300)
