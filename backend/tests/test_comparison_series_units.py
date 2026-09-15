import duckdb
import pytest

from app.adapters.persistence.analysis_insights import SQLAnalysisInsightsRepository


@pytest.mark.unit
def test_common_series_requires_consistent_value_and_time_units():
    with duckdb.connect(':memory:') as connection:
        connection.execute('''CREATE TABLE time_series_results (
            analysis_run_id VARCHAR, variable_key VARCHAR, display_name VARCHAR,
            value_unit VARCHAR, time_unit VARCHAR
        )''')
        connection.executemany('INSERT INTO time_series_results VALUES (?, ?, ?, ?, ?)', [
            ('base', 'valid', 'Valid', 'MPa', 'ms'),
            ('target', 'valid', 'Valid', 'MPa', 'ms'),
            ('base', 'value_mismatch', 'Stress', 'Pa', 'ms'),
            ('target', 'value_mismatch', 'Stress', 'MPa', 'ms'),
            ('base', 'time_mismatch', 'Time', 'MPa', 's'),
            ('target', 'time_mismatch', 'Time', 'MPa', 'ms'),
            ('base', 'unknown', 'Unknown', None, 'ms'),
            ('target', 'unknown', 'Unknown', 'MPa', 'ms'),
            ('base', 'mixed_within_run', 'Mixed', 'MPa', 'ms'),
            ('base', 'mixed_within_run', 'Mixed', 'MPa', 's'),
            ('target', 'mixed_within_run', 'Mixed', 'MPa', 'ms'),
            ('target', 'target_only', 'Added', 'MPa', 'ms'),
        ])
        result = SQLAnalysisInsightsRepository(connection).comparison_series('base', 'target')
        assert [item['variable_key'] for item in result] == ['valid']
        assert result[0]['value_unit'] == 'MPa'
