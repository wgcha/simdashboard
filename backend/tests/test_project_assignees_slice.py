from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.adapters.http.routers import project_assignees as assignee_router
from app.application.project_assignees.queries import list_assignee_candidates
from app.domains.project_assignees.models import ProjectAssigneeCandidate, ProjectNotFoundError
from app.domains.project_assignees.ports import ProjectAssigneeReader


pytestmark = pytest.mark.unit


class FakeReader:
    def __init__(self, events: list[str], *, exists: bool = True) -> None:
        self.events = events
        self.exists = exists

    def project_exists(self, project_id: str) -> bool:
        self.events.append(f"exists:{project_id}")
        return self.exists

    def authorize_request_edit(self, project_id: str) -> None:
        self.events.append(f"authorize:{project_id}")

    def list_candidates(
        self,
        project_id: str,
        search_term: str | None,
        exclude_local_admin: bool,
    ) -> list[ProjectAssigneeCandidate]:
        self.events.append(f"query:{project_id}:{search_term}:{exclude_local_admin}")
        return []


def _provider(reader: FakeReader, events: list[str]):
    @contextmanager
    def provider() -> Iterator[ProjectAssigneeReader]:
        events.append("open")
        try:
            yield reader
        finally:
            events.append("close")

    return provider


def test_candidate_query_checks_project_then_authorizes_before_listing() -> None:
    events: list[str] = []
    reader = FakeReader(events)

    assert list_assignee_candidates("project-1", "  Kim  ", True, _provider(reader, events)) == []
    assert events == [
        "open",
        "exists:project-1",
        "authorize:project-1",
        "query:project-1:kim:True",
        "close",
    ]


def test_candidate_query_treats_blank_search_as_no_filter() -> None:
    events: list[str] = []
    reader = FakeReader(events)

    list_assignee_candidates("project-1", "   ", False, _provider(reader, events))

    assert events[-2] == "query:project-1:None:False"


def test_candidate_query_missing_project_skips_authorization_and_listing() -> None:
    events: list[str] = []
    reader = FakeReader(events, exists=False)

    with pytest.raises(ProjectNotFoundError) as error:
        list_assignee_candidates("missing", None, True, _provider(reader, events))

    assert error.value.project_id == "missing"
    assert events == ["open", "exists:missing", "close"]


def test_assignee_http_adapter_has_no_direct_database_calls() -> None:
    tree = ast.parse(Path(assignee_router.__file__).read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "execute")
            or (isinstance(node.func, ast.Name) and node.func.id in {"connect", "rows"})
        )
        for node in ast.walk(tree)
    )
