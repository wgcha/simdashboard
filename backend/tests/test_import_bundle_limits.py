from __future__ import annotations

import pytest

from app.config import ImportBundleLimits, import_bundle_limits


pytestmark = pytest.mark.unit


_LIMIT_ENV = (
    "SIMDASH_IMPORT_MAX_MANIFEST_BYTES",
    "SIMDASH_IMPORT_MAX_MAPPING_COUNT",
    "SIMDASH_IMPORT_MAX_FILE_BYTES",
    "SIMDASH_IMPORT_MAX_TOTAL_BYTES",
    "SIMDASH_IMPORT_MAX_STRUCTURED_BYTES",
    "SIMDASH_IMPORT_MAX_SCALAR_RECORDS",
    "SIMDASH_IMPORT_MAX_CURVES",
    "SIMDASH_IMPORT_MAX_CURVE_POINTS",
)


def _clear_limit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LIMIT_ENV:
        monkeypatch.delenv(name, raising=False)


def test_import_bundle_limits_have_bounded_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_limit_env(monkeypatch)

    assert import_bundle_limits() == ImportBundleLimits(
        max_manifest_bytes=1 * 1024 * 1024,
        max_mapping_count=128,
        max_file_bytes=512 * 1024 * 1024,
        max_total_bytes=1 * 1024 * 1024 * 1024,
        max_structured_bytes=8 * 1024 * 1024,
        max_scalar_records=100_000,
        max_curves=128,
        max_curve_points=100_000,
    )


def test_import_bundle_limits_accept_deployment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "SIMDASH_IMPORT_MAX_MANIFEST_BYTES": "8192",
        "SIMDASH_IMPORT_MAX_MAPPING_COUNT": "7",
        "SIMDASH_IMPORT_MAX_FILE_BYTES": "1048576",
        "SIMDASH_IMPORT_MAX_TOTAL_BYTES": "2097152",
        "SIMDASH_IMPORT_MAX_STRUCTURED_BYTES": "524288",
        "SIMDASH_IMPORT_MAX_SCALAR_RECORDS": "23",
        "SIMDASH_IMPORT_MAX_CURVES": "7",
        "SIMDASH_IMPORT_MAX_CURVE_POINTS": "100",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    limits = import_bundle_limits()

    assert limits.max_manifest_bytes == 8192
    assert limits.max_mapping_count == 7
    assert limits.max_file_bytes == 1048576
    assert limits.max_total_bytes == 2097152
    assert limits.max_structured_bytes == 524288
    assert limits.max_scalar_records == 23
    assert limits.max_curves == 7
    assert limits.max_curve_points == 100


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SIMDASH_IMPORT_MAX_MANIFEST_BYTES", "1023"),
        ("SIMDASH_IMPORT_MAX_MAPPING_COUNT", "0"),
        ("SIMDASH_IMPORT_MAX_FILE_BYTES", "not-an-integer"),
        ("SIMDASH_IMPORT_MAX_TOTAL_BYTES", "1023"),
        ("SIMDASH_IMPORT_MAX_STRUCTURED_BYTES", "1023"),
        ("SIMDASH_IMPORT_MAX_STRUCTURED_BYTES", str(64 * 1024 * 1024 + 1)),
        ("SIMDASH_IMPORT_MAX_SCALAR_RECORDS", "0"),
        ("SIMDASH_IMPORT_MAX_CURVES", "0"),
        ("SIMDASH_IMPORT_MAX_CURVE_POINTS", "0"),
    ],
)
def test_import_bundle_limits_fail_closed_for_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    _clear_limit_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError):
        import_bundle_limits()


def test_import_bundle_limits_accepts_total_smaller_than_file(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_limit_env(monkeypatch)
    monkeypatch.setenv("SIMDASH_IMPORT_MAX_FILE_BYTES", "4096")
    monkeypatch.setenv("SIMDASH_IMPORT_MAX_TOTAL_BYTES", "2048")
    monkeypatch.setenv("SIMDASH_IMPORT_MAX_STRUCTURED_BYTES", "1024")

    limits = import_bundle_limits()

    assert limits.max_file_bytes == 4096
    assert limits.max_total_bytes == 2048


def test_import_bundle_limits_reject_structured_larger_than_file(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_limit_env(monkeypatch)
    monkeypatch.setenv("SIMDASH_IMPORT_MAX_FILE_BYTES", "4096")
    monkeypatch.setenv("SIMDASH_IMPORT_MAX_TOTAL_BYTES", "8192")
    monkeypatch.setenv("SIMDASH_IMPORT_MAX_STRUCTURED_BYTES", "8192")

    with pytest.raises(RuntimeError, match="SIMDASH_IMPORT_MAX_STRUCTURED_BYTES"):
        import_bundle_limits()
