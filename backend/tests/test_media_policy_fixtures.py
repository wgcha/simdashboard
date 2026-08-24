from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.media_policy import ALLOWED_MEDIA
from app.services.media_storage_service import inspect_file


_MEDIA_CASES = (
    ("contour.png", "image/png", "IMAGE", b"\x89PNG\r\n\x1a\nminimal-png"),
    ("contour.jpg", "image/jpeg", "IMAGE", b"\xff\xd8\xffminimal-jpeg"),
    ("contour.jpeg", "image/jpeg", "IMAGE", b"\xff\xd8\xffminimal-jpeg"),
    ("contour.webp", "image/webp", "IMAGE", b"RIFF\x10\x00\x00\x00WEBPminimal"),
    (
        "contour.svg",
        "image/svg+xml",
        "IMAGE",
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"></svg>',
    ),
    (
        "animation.mp4",
        "video/mp4",
        "VIDEO",
        b"\x00\x00\x00\x10ftypisom\x00\x00\x00\x00",
    ),
    ("animation.webm", "video/webm", "VIDEO", b"\x1a\x45\xdf\xa3minimal-webm"),
    (
        "model.glb",
        "model/gltf-binary",
        "MODEL_3D",
        b"glTF\x02\x00\x00\x00\x0c\x00\x00\x00",
    ),
    ("model.gltf", "model/gltf+json", "MODEL_3D", b'{"asset":{"version":"2.0"}}'),
)


def _fixture_path(tmp_path: Path, case: tuple[str, str, str, bytes]) -> Path:
    filename, _mime_type, _asset_type, payload = case
    path = tmp_path / filename
    path.write_bytes(payload)
    return path


def _cases_by_extension() -> dict[str, tuple[str, str, str, bytes]]:
    return {Path(filename).suffix: case for case in _MEDIA_CASES for filename in [case[0]]}


@pytest.mark.unit
def test_generated_fixture_matrix_covers_every_allowed_media_extension():
    allowed_extensions = {
        extension
        for extensions, _maximum in ALLOWED_MEDIA.values()
        for extension in extensions
    }
    assert set(_cases_by_extension()) == allowed_extensions


@pytest.mark.unit
@pytest.mark.parametrize("case", _MEDIA_CASES, ids=lambda case: case[0])
def test_generated_minimal_media_fixtures_are_accepted(tmp_path: Path, case):
    path = _fixture_path(tmp_path, case)
    filename, mime_type, asset_type, payload = case

    inspection = inspect_file(path, filename=filename, mime_type=mime_type, asset_type=asset_type)

    assert inspection.file_size == len(payload)
    assert inspection.sha256 == hashlib.sha256(payload).hexdigest()
    assert inspection.canonical_type == ("CONTOUR_IMAGE" if asset_type == "IMAGE" else asset_type)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("case", "wrong_mime_type"),
    [
        (case, "image/jpeg" if case[1] != "image/jpeg" else "image/png")
        for case in _MEDIA_CASES
    ],
    ids=lambda value: value[0] if isinstance(value, tuple) else value,
)
def test_generated_media_fixtures_reject_mismatched_mime_and_extension(
    tmp_path: Path,
    case,
    wrong_mime_type: str,
):
    path = _fixture_path(tmp_path, case)
    filename, _mime_type, asset_type, _payload = case

    with pytest.raises(ValueError, match="확장자와 MIME"):
        inspect_file(path, filename=filename, mime_type=wrong_mime_type, asset_type=asset_type)


_CORRUPT_SIGNATURES = {
    "png": b"\x89PNG\r\n\x1a\x00corrupt",
    "jpg": b"\x00\xd8\xffcorrupt",
    "jpeg": b"\x00\xd8\xffcorrupt",
    "webp": b"RIFX\x10\x00\x00\x00WEBPcorrupt",
    "svg": b"<not-an-svg />",
    "mp4": b"\x00\x00\x00\x10free12345678",
    "webm": b"\x1a\x45\xdf\xa2corrupt",
    "glb": b"gltf\x02\x00\x00\x00corrupt",
    "gltf": b"not-json-gl tf",
}


@pytest.mark.unit
@pytest.mark.parametrize("case", _MEDIA_CASES, ids=lambda case: case[0])
def test_generated_media_fixtures_reject_corrupt_signatures(tmp_path: Path, case):
    filename, mime_type, asset_type, _payload = case
    suffix = Path(filename).suffix.removeprefix(".")
    path = tmp_path / filename
    path.write_bytes(_CORRUPT_SIGNATURES[suffix])

    with pytest.raises(ValueError):
        inspect_file(path, filename=filename, mime_type=mime_type, asset_type=asset_type)
