from __future__ import annotations

from typing import Any

from scripts import verify_postgres_0006_access_upgrade as fixture


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Any] | None]] = []

    def execute(self, statement: str, parameters: list[Any] | None = None) -> None:
        self.calls.append((" ".join(statement.split()), parameters))


def test_prepare_seeds_the_versioned_plan_parents_before_fixture_work_item() -> None:
    connection = _RecordingConnection()

    fixture.prepare(connection)  # type: ignore[arg-type]

    def call_index(fragment: str) -> int:
        return next(index for index, (statement, _) in enumerate(connection.calls) if fragment in statement)

    task_index = call_index("INSERT INTO task_type_versions")
    request_type_index = call_index("INSERT INTO request_type_versions")
    plan_index = call_index("INSERT INTO request_work_plans")
    item_index = call_index("INSERT INTO request_work_items")
    assert task_index < request_type_index < plan_index < item_index

    task_parameters = connection.calls[task_index][1]
    request_type_parameters = connection.calls[request_type_index][1]
    plan_parameters = connection.calls[plan_index][1]
    item_parameters = connection.calls[item_index][1]
    assert task_parameters and task_parameters[0] == fixture.FIXTURE_TASK_TYPE_ID
    assert request_type_parameters and request_type_parameters[:3] == [
        fixture.FIXTURE_REQUEST_TYPE_ID,
        fixture.FIXTURE_TASK_TYPE_ID,
        fixture.FIXTURE_TASK_TYPE_ID,
    ]
    assert plan_parameters and plan_parameters[:3] == [
        fixture.FIXTURE_REQUEST_ID,
        fixture.FIXTURE_REQUEST_TYPE_ID,
        fixture.FIXTURE_TASK_TYPE_ID,
    ]
    assert item_parameters == [fixture.FIXTURE_REQUEST_ID, fixture.FIXTURE_TASK_TYPE_ID]

    request_type_statement = connection.calls[request_type_index][0]
    plan_statement = connection.calls[plan_index][0]
    assert "jsonb_build_object('id', CAST(%s AS VARCHAR), 'version', 1)" in request_type_statement
    assert "'task_type_id', CAST(%s AS VARCHAR)" in request_type_statement
    assert "VALUES (CAST(%s AS VARCHAR), CAST(%s AS VARCHAR), 1" in plan_statement
    assert "'task_type_id', CAST(%s AS VARCHAR)" in plan_statement
