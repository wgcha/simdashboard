from __future__ import annotations

from pathlib import PurePosixPath


ALLOWED_MEDIA = {
    "CONTOUR_IMAGE": ({".png", ".jpg", ".jpeg", ".webp", ".svg"}, 25 * 1024 * 1024),
    "VIDEO": ({".mp4", ".webm"}, 500 * 1024 * 1024),
    "MODEL_3D": ({".glb", ".gltf"}, 100 * 1024 * 1024),
}


def validate_media_metadata(asset_type: str, file_path: str, file_size: int | None) -> None:
    if asset_type not in ALLOWED_MEDIA:
        raise ValueError("지원하지 않는 미디어 유형입니다.")
    normalized = file_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or normalized.startswith(("http://", "https://")):
        raise ValueError("미디어 경로는 프로젝트 내부의 안전한 상대 경로여야 합니다.")
    extensions, maximum = ALLOWED_MEDIA[asset_type]
    if path.suffix.lower() not in extensions:
        raise ValueError(f"{asset_type}에 허용되지 않는 파일 형식입니다.")
    if file_size is not None and (file_size < 0 or file_size > maximum):
        raise ValueError(f"{asset_type} 파일 크기 제한을 초과했습니다.")
