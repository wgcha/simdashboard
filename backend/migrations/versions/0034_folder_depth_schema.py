"""Seed the default DEPTH_V1 folder role schema set and archive older profiles.

Contract: docs/contracts/depth-schema.md §4.4.

* Every active (non-archived) environment profile is archived in place; rows
  are kept because existing scans reference them (refresh keeps working).
* One default schema set (USAGE + DISTRIBUTION rows sharing ``schema_set_id``)
  is inserted. USAGE copies ``usage_sources`` from the current USAGE default
  profile with ``EVALUATION`` role references rewritten to ``SCENE`` (D8).
* The data change runs only when no DEPTH_V1 row exists yet, so re-running is
  a no-op and later admin saves are never touched.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0034_folder_depth_schema"
down_revision = "0033_result_registration_location_links"
branch_labels = None
depends_on = None

SCHEMA_SET_ID = "dss-00000000-0000-4000-8000-000000000034"
PROFILE_IDS = {
    "USAGE": "environment-profile-usage-depth-v1",
    "DISTRIBUTION": "environment-profile-distribution-depth-v1",
}
NAMES = {"USAGE": "기본 사용환경 깊이 스키마", "DISTRIBUTION": "기본 유통환경 깊이 스키마"}
KEYWORDS = {"USAGE": "사용", "DISTRIBUTION": "유통"}
UPPER = {"levels": [{"level": 1, "role": "PROJECT"}, {"level": 2, "role": "REQUEST"}]}
LOWER = {
    "USAGE": {"levels": [{"level": 1, "role": "WORKING", "fixed_name": "Working"},
                         {"level": 2, "role": "SIMULATION_CASE"}, {"level": 3, "role": "SCENE"}],
              "below_last": "CONTENT"},
    "DISTRIBUTION": {"levels": [{"level": 1, "role": "WORKING", "fixed_name": "Working"},
                                {"level": 2, "role": "SIMULATION_CASE"}, {"level": 3, "role": "LOAD_CASE"},
                                {"level": 4, "role": "EXECUTION_RUN"}, {"level": 5, "role": "RUN_OPTION"},
                                {"level": 6, "role": "SCENE"}],
                     "below_last": "CONTENT"},
}
FINAL = {
    "fixed": True,
    "children": [
        {"name": "CAE", "role": "FINAL_CAE"},
        {"name": "Reports", "role": "FINAL_REPORTS"},
        {"name": "CAD", "role": "FINAL_CAD", "below": "CONTENT"},
    ],
    "ignored": [".finalizations"],
    "below_child": ["SIMULATION_CASE", "FINAL_VERSION", "MIRROR_WORKING_FROM_LEVEL_3"],
}


def default_rules(environment: str) -> dict:
    return {
        "format": "DEPTH_V1",
        "schema_set_id": SCHEMA_SET_ID,
        "upper": UPPER,
        "environment_keyword": KEYWORDS[environment],
        "lower": LOWER[environment],
        "final": FINAL,
        "usage_sources": None,
        "profile_metadata": {"created_by": "migration-0034"},
    }


# A profile is active unless profile_metadata.archived is true.
def _active(alias: str = "") -> str:
    column = f"{alias}rules_json"
    return f"COALESCE(({column}->'profile_metadata'->>'archived')::boolean, false) = false"


def upgrade() -> None:
    # 1. Seed the default set before archiving so USAGE can copy usage_sources
    #    from the current USAGE default (the oldest active USAGE profile, as
    #    the pre-DEPTH_V1 default_profile() chose it). Skipped once any other
    #    DEPTH_V1 set exists (re-run safety); ON CONFLICT covers a re-run.
    for environment in ("USAGE", "DISTRIBUTION"):
        if environment == "USAGE":
            usage_sources = (
                "COALESCE((SELECT regexp_replace((src.rules_json->'usage_sources')::text, "
                "'\\mEVALUATION\\M', 'SCENE', 'g')::jsonb FROM folder_environment_profiles src "
                f"WHERE src.environment = 'USAGE' AND jsonb_typeof(src.rules_json) = 'object' AND {_active('src.')} "
                "AND src.rules_json->>'format' IS DISTINCT FROM 'DEPTH_V1' "
                "ORDER BY src.created_at, src.id LIMIT 1), 'null'::jsonb)"
            )
        else:
            usage_sources = "'null'::jsonb"
        op.execute(sa.text(
            "INSERT INTO folder_environment_profiles(id,environment,name,revision,rules_json,created_at,updated_at) "
            f"SELECT :profile_id, :environment, :name, 1, "
            f"jsonb_set(CAST(:rules AS JSONB), '{{usage_sources}}', {usage_sources}), "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM folder_environment_profiles other "
            "WHERE other.rules_json->>'format' = 'DEPTH_V1' AND other.rules_json->>'schema_set_id' <> :schema_set_id) "
            "ON CONFLICT(id) DO NOTHING"
        ).bindparams(
            profile_id=PROFILE_IDS[environment], environment=environment, name=NAMES[environment],
            rules=json.dumps(default_rules(environment), ensure_ascii=False), schema_set_id=SCHEMA_SET_ID,
        ))
    # 2. Archive every other active profile; rows stay for scan FKs and refresh.
    op.execute(sa.text(
        "UPDATE folder_environment_profiles SET "
        "rules_json = jsonb_set(rules_json, '{profile_metadata}', "
        "COALESCE(rules_json->'profile_metadata', '{}'::jsonb) || "
        "jsonb_build_object('archived', true, 'archived_revision', revision, "
        "'superseded_by', CAST(:schema_set_id AS TEXT))), "
        "updated_at = CURRENT_TIMESTAMP "
        f"WHERE jsonb_typeof(rules_json) = 'object' AND {_active()} "
        "AND rules_json->>'format' IS DISTINCT FROM 'DEPTH_V1'"
    ).bindparams(schema_set_id=SCHEMA_SET_ID))


def downgrade() -> None:
    raise RuntimeError("Depth schema profiles supersede archived rules; downgrade is not supported.")
