from __future__ import annotations

from typing import Any

from ...domains.feature_examples.catalog import feature_example_catalog
from ...domains.feature_examples.policies import zero_data_profile
from ...domains.feature_examples.ports import FeatureExamplesRepositoryProvider


def feature_examples(repository_provider: FeatureExamplesRepositoryProvider) -> list[dict[str, Any]]:
    items = feature_example_catalog()
    load_case_ids = list(dict.fromkeys(
        item.get("load_case_id") for item in items if item.get("load_case_id")
    ))
    if load_case_ids:
        with repository_provider() as repository:
            profiles = repository.data_profiles(load_case_ids)
            for item in items:
                load_case_id = item.get("load_case_id")
                item["data_profile"] = (
                    dict(profiles.get(load_case_id, zero_data_profile()))
                    if load_case_id
                    else zero_data_profile()
                )
    return items
