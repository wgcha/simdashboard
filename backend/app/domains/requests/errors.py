from __future__ import annotations


class RequestQueryError(Exception):
    """Base error for analysis-request read use cases."""


class ProjectMembershipRequiredError(RequestQueryError):
    """The caller has company-level access but no membership in the project."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(project_id)
