from __future__ import annotations

from typing import Literal, TypedDict


ProjectMembershipRole = Literal["general", "power", "admin"]


class ProjectAssigneeCandidate(TypedDict):
    """A project member eligible to receive a request assignment."""

    user_id: str
    display_name: str
    employee_id: str | None
    department: str | None
    job_title: str | None
    role: ProjectMembershipRole


class ProjectAssigneeError(Exception):
    """Base class for project assignee query errors."""


class ProjectNotFoundError(ProjectAssigneeError):
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(project_id)
