from backend.app.schemas.result_import import Manifest
from backend.app.repositories.analysis_repository import AnalysisRepository
from pathlib import Path

class ResultValidationService:
    def __init__(self, repo: AnalysisRepository):
        self.repo = repo

    def validate_manifest(self, manifest: Manifest, root_dir: Path, manifest_path: Path) -> None:
        if manifest.schema_version != "1.0":
            raise ValueError("UNSUPPORTED_SCHEMA_VERSION")
            
        # Verify relations
        project = self.repo.get_project(manifest.project_id)
        if not project:
            raise ValueError("ENTITY_NOT_FOUND: Project")
            
        request = self.repo.get_request(manifest.request_id)
        if not request or request["project_id"] != manifest.project_id:
            raise ValueError("ENTITY_RELATION_MISMATCH: Request")
            
        load_case = self.repo.get_load_case(manifest.load_case_id)
        if not load_case or load_case["request_id"] != manifest.request_id:
            raise ValueError("ENTITY_RELATION_MISMATCH: LoadCase")

        # Verify files exist and are within root
        manifest_dir = manifest_path.parent
        for rf in manifest.result_files:
            file_path = (manifest_dir / rf.path).resolve()
            if not str(file_path).startswith(str(root_dir)):
                raise ValueError("PATH_OUTSIDE_IMPORT_ROOT")
            if not file_path.exists():
                if rf.required:
                    raise ValueError(f"MISSING_RESULT_FILE: {rf.path}")
