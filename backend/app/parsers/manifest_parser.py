import json
from pathlib import Path
from backend.app.schemas.result_import import Manifest

class ManifestParser:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)

    def parse(self, relative_path: str) -> Manifest:
        manifest_path = self.root_dir / relative_path
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")
        
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return Manifest(**data)
