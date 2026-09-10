import json

from scripts.media_backup_diagnostics import safe_media_diagnostics


def _line(payload: object) -> str:
    return "MediaIntegrityError: " + json.dumps(payload)


def test_extracts_bounded_inventory_without_ids_or_secrets() -> None:
    payload = {
        "code": "MEDIA_INTEGRITY_FAILED",
        "media_inventory": {
            "unbound_media_asset_count": 2,
            "missing_reference_count": 3,
            "orphan_blob_count": 4,
            "orphan_chunk_count": 5,
            "corrupt_blob_count": 1,
            "chunk_count": 8,
            "declared_chunk_count": 9,
            "demo_count": 18,
            "demo_expected_count": 20,
            "demo_exact": False,
            "content_integrity_verified": False,
            "missing_demo_ids": ["secret-id"],
            "unexpected_demo_ids": ["private-id", "another-id"],
            "password": "synthetic-password",
        },
    }
    result = safe_media_diagnostics(_line(payload))
    assert result == {
        "code": "MEDIA_INTEGRITY_FAILED",
        "unbound_media_asset_count": 2,
        "missing_reference_count": 3,
        "orphan_blob_count": 4,
        "orphan_chunk_count": 5,
        "corrupt_blob_count": 1,
        "chunk_count": 8,
        "declared_chunk_count": 9,
        "demo_count": 18,
        "demo_expected_count": 20,
        "demo_exact": False,
        "content_integrity_verified": False,
        "missing_demo_count": 1,
        "unexpected_demo_count": 2,
    }
    assert "secret-id" not in result


def test_schema_code_and_traceback_are_supported() -> None:
    text = "Traceback (most recent call last):\nMediaIntegrityError: " + json.dumps(
        {"code": "MEDIA_INVENTORY_SCHEMA_INVALID", "media_inventory": {"demo_exact": True}}
    )
    assert safe_media_diagnostics(text) == {"code": "MEDIA_INVENTORY_SCHEMA_INVALID", "demo_exact": True}


def test_invalid_types_are_omitted_and_booleans_are_not_counts() -> None:
    result = safe_media_diagnostics(
        _line(
            {
                "code": "MEDIA_INTEGRITY_FAILED",
                "media_inventory": {
                    "chunk_count": True,
                    "declared_chunk_count": -1,
                    "demo_count": "18",
                    "missing_demo_ids": "secret",
                    "unexpected_demo_ids": ["x"],
                    "demo_exact": "false",
                },
            }
        )
    )
    assert result == {"code": "MEDIA_INTEGRITY_FAILED", "unexpected_demo_count": 1}


def test_unknown_json_and_fabricated_password_text_are_empty() -> None:
    assert safe_media_diagnostics('password=MEDIA_INTEGRITY_FAILED {"password":"x"}') == {}
    assert safe_media_diagnostics('{"code":"UNKNOWN","media_inventory":{}}') == {}
    assert safe_media_diagnostics('{"code":"MEDIA_INTEGRITY_FAILED"') == {}


def test_deployment_reason_is_allowlisted_without_exception_text() -> None:
    assert safe_media_diagnostics("DeploymentMediaBackupError: MEDIA_BLOB_CORRUPT") == {
        "reason": "MEDIA_BLOB_CORRUPT"
    }
    assert safe_media_diagnostics("DeploymentMediaBackupError: path=C:\\secret") == {}
    assert safe_media_diagnostics("DeploymentMediaBackupError: ATTACKER_SECRET") == {}


def test_qualified_deployment_traceback_is_supported_and_keeps_prior_inventory() -> None:
    text = (
        _line({"code": "MEDIA_INTEGRITY_FAILED", "media_inventory": {"chunk_count": 4}})
        + "\n"
        + "scripts.deployment_media_backup.DeploymentMediaBackupError: UNBOUND_MEDIA_FILE_MISSING"
    )
    assert safe_media_diagnostics(text) == {
        "code": "MEDIA_INTEGRITY_FAILED",
        "chunk_count": 4,
        "reason": "UNBOUND_MEDIA_FILE_MISSING",
    }
    assert safe_media_diagnostics("__main__.DeploymentMediaBackupError: MEDIA_BLOB_CORRUPT") == {
        "reason": "MEDIA_BLOB_CORRUPT"
    }


def test_unhashable_code_does_not_raise() -> None:
    assert safe_media_diagnostics(_line({"code": [], "media_inventory": {}})) == {}
