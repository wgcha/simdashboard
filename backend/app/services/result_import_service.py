from pathlib import Path
import uuid
import hashlib
from typing import Dict, Any, List

from ..schemas.result_import import Manifest, ResultType
from ..parsers.manifest_parser import LegacyResultFilesManifestParser
from ..parsers.open_cell_parser import OpenCellParser
from ..parsers.chassis_rear_parser import ChassisRearParser
from ..parsers.generic_time_history_parser import GenericTimeHistoryParser
from ..parsers.scalar_result_parser import ScalarResultParser
from ..repositories.analysis_repository import AnalysisRepository
from ..repositories.result_repository import ResultRepository
from ..repositories.import_job_repository import ImportJobRepository
from ..services.result_validation_service import ResultValidationService
from ..services.verdict_service import VerdictService

class ResultImportService:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.analysis_repo = AnalysisRepository()
        self.result_repo = ResultRepository()
        self.job_repo = ImportJobRepository()
        self.validator = ResultValidationService(self.analysis_repo)
        self.verdict_service = VerdictService()

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
            all_scalars = []
            all_time_series = []
            
            manifest_dir = manifest_path.parent
            for rf in manifest.result_files:
                file_path = (manifest_dir / rf.path).resolve()
                if not file_path.exists(): continue
                
                if rf.type == ResultType.OPEN_CELL_STRESS:
                    res = OpenCellParser().parse(file_path)
                    all_time_series.extend(res["time_series"])
                    all_scalars.extend(res["scalars"])
                elif rf.type == ResultType.CHASSIS_REAR_DEFORMATION:
                    all_scalars.extend(ChassisRearParser().parse(file_path))
                elif rf.type == ResultType.GENERIC_TIME_HISTORY:
                    all_time_series.extend(GenericTimeHistoryParser().parse(file_path))
                elif rf.type == ResultType.SCALAR_RESULTS:
                    all_scalars.extend(ScalarResultParser().parse(file_path))

            # Verdicts
            thresholds = self.result_repo.get_thresholds(manifest.project_id)
            for scalar in all_scalars:
                scalar["verdict"] = self.verdict_service.evaluate_scalar(scalar, thresholds)
                
            overall_verdict = self.verdict_service.determine_overall_verdict(all_scalars)
            
            # Save
            self.result_repo.save_scalar_results(manifest.run_id, all_scalars)
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
