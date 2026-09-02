from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ....adapters.persistence.feature_examples import SQLFeatureExamplesRepositoryProvider
from ....application.feature_examples.queries import feature_examples as feature_examples_query


router = APIRouter()


@router.get("/api/feature-examples")
def feature_examples() -> list[dict[str, Any]]:
    """Return curated, stable entry points for exercising product capabilities."""
    return feature_examples_query(SQLFeatureExamplesRepositoryProvider())
