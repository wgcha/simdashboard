import json
import hashlib
from datetime import datetime, timezone

import pytest

from app.adapters.persistence.analysis_insights import SQLAnalysisInsightsRepositoryProvider
from app.application.analysis_insights.queries import compare_analysis_runs
from app.database import connect
from app.services.master_result_refresh import _ingestion_command
from app.folder_import import scan_folder
from app.adapters.persistence.result_ingestion import SQLResultIngestionUnitOfWorkProvider
from app.application.results.commands import ingest_result_bundle, utc_identifier


@pytest.mark.duckdb_integration
def test_master_manifest_conditions_survive_parser_persistence_and_comparison(tmp_path):
    load_case = 'loadcase-drop-bottom-001'
    bundle = tmp_path / 'conditions'
    bundle.mkdir()
    (bundle / 'scalars.json').write_text(json.dumps([{
        'variable_key': 'top_edge_max_stress', 'display_name': '상단 최대 응력',
        'data_type': 'FLOAT', 'value': 42.5, 'unit': 'MPa',
        'threshold': 75, 'result_group': 'OPEN_CELL',
    }]), encoding='utf-8')
    manifest = {
        'schema_id': 'conditions-integration', 'version': 1, 'solver': 'pytest',
        'context': {'project_id': 'project-tv-001', 'request_id': 'request-drop-001', 'load_case_id': load_case},
        'mappings': [{'kind': 'typed_scalars', 'path': 'scalars.json'}],
        'metadata': {
            'run_conditions': {'material': 'SPCC', 'thickness_mm': 1.2, 'pressure_mpa': 0, 'contact': {'enabled': False}},
            'result_criteria': {'top_edge_max_stress': {'operator': 'LTE', 'upper': 75, 'unit': 'MPa', 'label': '상단 응력'}},
        },
    }
    manifest_path = bundle / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    def import_bundle():
        # Exercise the real master adapter, parser and transaction; OS locking is tested separately.
        checksum = hashlib.sha256(manifest_path.read_bytes() + (bundle / 'scalars.json').read_bytes()).hexdigest()
        command = _ingestion_command('conditions/manifest.json', checksum, checksum, ('project-tv-001', 'request-drop-001', load_case), scan_folder(bundle), manifest)
        return ingest_result_bundle(command, SQLResultIngestionUnitOfWorkProvider(utc_identifier), lambda: datetime.now(timezone.utc), utc_identifier)

    assert import_bundle()['status'] == 'IMPORTED'
    with connect() as connection:
        first = connection.execute('SELECT id FROM analysis_runs WHERE load_case_id=? ORDER BY run_no DESC LIMIT 1', [load_case]).fetchone()[0]
    manifest['metadata']['run_conditions']['thickness_mm'] = 1.5
    manifest['metadata']['result_criteria']['top_edge_max_stress']['upper'] = 40
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    assert import_bundle()['status'] == 'IMPORTED'
    with connect() as connection:
        second = connection.execute('SELECT id FROM analysis_runs WHERE load_case_id=? ORDER BY run_no DESC LIMIT 1', [load_case]).fetchone()[0]
        historical = connection.execute('SELECT metadata_json FROM analysis_run_metadata WHERE analysis_run_id=?', [first]).fetchone()[0]
    assert first != second
    assert json.loads(historical)['run_conditions']['thickness_mm'] == 1.2
    assert json.loads(historical)['result_criteria']['top_edge_max_stress']['upper'] == 75.0
    with connect() as connection:
        connection.execute('UPDATE scalar_results SET threshold_double=999, verdict=? WHERE analysis_run_id=?', ['FAIL', first])
    data = compare_analysis_runs(load_case, first, second, None, SQLAnalysisInsightsRepositoryProvider())
    rows = {item['key']: item for item in data['condition_comparison']['rows']}
    assert rows['thickness']['status'] == 'CHANGED'
    assert rows['thickness']['baseline']['value'] == 1.2
    assert rows['thickness']['target']['value'] == 1.5
    assert rows['pressure_mpa']['status'] == 'SAME'
    assert rows['contact']['status'] == 'SAME'
    assert rows['material']['status'] == 'SAME'
    scalar = {item['variable_key']: item for item in data['scalar_comparison']}['top_edge_max_stress']
    assert scalar['baseline_margin']['value'] == 32.5
    assert scalar['baseline_margin']['meets_criterion'] is True
    assert scalar['target_margin']['value'] == -2.5
    assert scalar['target_margin']['meets_criterion'] is False
    assert import_bundle()['status'] == 'SKIPPED'
