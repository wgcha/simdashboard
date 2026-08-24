from pathlib import Path
import uuid
import hashlib
from typing import Dict, Any, List

from ..schemas.result_import import ResultType
from ..parsers.manifest_parser import LegacyResultFilesManifestParser
from ..repositories.analysis_repository import AnalysisRepository
from ..repositories.result_repository import ResultRepository
from ..repositories.import_job_repository import ImportJobRepository
from ..services.result_validation_service import ResultValidationService
from ..services.verdict_service import VerdictService
from .result_import_adapter import LegacyResultParserAdapter
from .result_import_contract import NormalizedResultPayload

class ResultImportService:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir).resolve()
        self.analysis_repo = AnalysisRepository()
        self.result_repo = ResultRepository()
        self.job_repo = ImportJobRepository()
        self.validator = ResultValidationService(self.analysis_repo)
        self.verdict_service = VerdictService()
        self.parser_adapter = LegacyResultParserAdapter()

    def _calc_checksum(self, file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    def scan(self) -> List[str]:
        if not self.root_dir.exists():
            return []
        manifests = list(self.root_dir.rglob("manifest.json"))
        return [str(m.relative_to(self.root_dir)) for m in manifests]

    def import_result(self, relative_manifest_path: str) -> Dict[str, Any]:
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        manifest_path = self.root_dir / relative_manifest_path
        
        job_data = {
            "id": job_id,
            "project_id": "",
            "request_id": "",
            "load_case_id": "",
            "manifest_path": relative_manifest_path,
            "source_directory": str(manifest_path.parent.relative_to(self.root_dir)),
            "schema_version": "",
            "status": "FAILED",
            "overwrite_policy": "REJECT",
            "manifest_checksum": self._calc_checksum(manifest_path) if manifest_path.exists() else "",
        }

        try:
            # This service remains the explicit compatibility path for the
            # pre-canonical result_files manifest contract.
            parser = LegacyResultFilesManifestParser(str(self.root_dir))
            manifest = parser.parse(relative_manifest_path)
            
            job_data.update({
                "project_id": manifest.project_id,
                "request_id": manifest.request_id,
                "load_case_id": manifest.load_case_id,
                "schema_version": manifest.schema_version,
                "overwrite_policy": manifest.overwrite_policy.value,
                "analysis_run_id": manifest.run_id
            })

            self.validator.validate_manifest(manifest, self.root_dir, manifest_path)
            
            # Create or update run
            existing_run = self.analysis_repo.get_run(manifest.run_id)
            if not existing_run:
                self.analysis_repo.create_run(
                    manifest.run_id, manifest.load_case_id, manifest.run_no, 
                    manifest.solver, manifest.started_at, manifest.completed_at,
                    manifest.source_program, manifest.source_program_version
                )
            
            # Policy check
            if existing_run and existing_run.get("result_import_status") == "COMPLETED":
                if manifest.overwrite_policy.value == "REJECT":
                    raise ValueError("OVERWRITE_REJECTED")
                elif manifest.overwrite_policy.value == "REPLACE":
                    self.result_repo.delete_results_for_run(manifest.run_id)
            
            # Parse files
            normalized_results = NormalizedResultPayload()
            
            manifest_dir = manifest_path.parent
            for rf in manifest.result_files:
                file_path = (manifest_dir / rf.path).resolve()
                if not file_path.exists(): continue
                # Media is a valid manifest entry, but the legacy result
                # service never persisted media.  Leave that established
                # behavior to the media-specific import path.
                if rf.type == ResultType.MEDIA_ASSET:
                    continue
                
                normalized_results = normalized_results.extend(
                    self.parser_adapter.parse_file(
                        rf,
                        file_path,
                        source_path=str(file_path.relative_to(self.root_dir)),
                    )
                )

            # Verdicts
            thresholds = self.result_repo.get_thresholds(manifest.project_id)
            scalars = tuple(
                scalar.with_verdict(self.verdict_service.evaluate_scalar(scalar.for_verdict(), thresholds))
                for scalar in normalized_results.scalars
            )
            normalized_results = NormalizedResultPayload(
                scalars=scalars,
                time_series=normalized_results.time_series,
            )
            all_scalars = [scalar.for_verdict() for scalar in normalized_results.scalars]
            all_time_series = normalized_results.time_series_persistence_rows()
                
            overall_verdict = self.verdict_service.determine_overall_verdict(all_scalars)
            
            # Save
            self.result_repo.save_scalar_results(manifest.run_id, normalized_results.scalar_persistence_rows())
            self.result_repo.save_time_series_results(manifest.run_id, all_time_series)
            
            self.analysis_repo.update_run_import_status(manifest.run_id, "COMPLETED", overall_verdict)
            
            job_data.update({
                "status": "COMPLETED",
                "file_count": len(manifest.result_files),
                "row_count": len(all_scalars) + len(all_time_series)
            })
            
        except Exception as e:
            job_data.update({
                "status": "FAILED",
                "error_code": "IMPORT_ERROR",
                "error_message": str(e)
            })
            if job_data.get("analysis_run_id"):
                try:
                    self.analysis_repo.update_run_import_status(job_data["analysis_run_id"], "FAILED")
                except:
                    pass
                    
        self.job_repo.create_job(job_data)
        return job_data
