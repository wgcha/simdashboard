from pathlib import Path

from ..schemas.result_import import Manifest
from .manifest_format import ManifestFormat, load_manifest, resolve_manifest_path


class LegacyResultFilesManifestParser:
    """Compatibility parser for the pre-canonical ``result_files`` contract."""

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)

    def parse(self, relative_path: str) -> Manifest:
        manifest_path = resolve_manifest_path(self.root_dir, relative_path)

        loaded = load_manifest(manifest_path, expected_format=ManifestFormat.LEGACY_RESULT_FILES)
        return Manifest(**loaded.data)


# Keep the historical import name for callers outside this repository.
ManifestParser = LegacyResultFilesManifestParser
