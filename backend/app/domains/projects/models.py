from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict


class Project(TypedDict):
    id: str
    name: str
    product_name: str
    description: str | None
    created_at: datetime


class ProjectCreator(TypedDict):
    user_id: str
    username: str
    role: str


class CreateProjectCommand(TypedDict):
    name: str
    product_name: str
    description: str
    manufacturer: str
    display_size_inch: float | None
    creator: ProjectCreator


class CreatedProject(TypedDict):
    id: str
    name: str
    product_name: str
    description: str
    manufacturer: str
    display_size_inch: float | None
    created_at: datetime


class ProjectAuditContext(TypedDict):
    user_id: str
    username: str
    role: str
    action: Literal["PROJECT_CREATED"]
    method: Literal["POST"]
    path: Literal["/api/projects"]
    status_code: Literal[201]
    request_id: str
    client_ip: str | None
    user_agent: str


class PersistedProjectAuditRecord(ProjectAuditContext):
    id: str
    occurred_at: datetime
    project_id: str
