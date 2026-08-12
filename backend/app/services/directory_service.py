from __future__ import annotations

from typing import Any, Literal, Protocol
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, Field, ValidationError

from ..config import directory_settings
from ..database_connection import connect, rows


class DirectoryEmployee(BaseModel):
    employee_id: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    department: str | None = Field(default=None, max_length=160)
    job_title: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    employment_status: Literal["ACTIVE", "INACTIVE"]


class DirectoryUnavailableError(RuntimeError):
    pass


class EmployeeDirectory(Protocol):
    def search(self, query: str, limit: int) -> list[DirectoryEmployee]: ...

    def get_by_employee_id(self, employee_id: str) -> DirectoryEmployee | None: ...


class HttpEmployeeDirectory:
    def __init__(self) -> None:
        settings = directory_settings()
        if not settings.base_url or not settings.token:
            raise DirectoryUnavailableError("사내 임직원 디렉터리가 구성되지 않았습니다.")
        self._base_url = settings.base_url.rstrip("/") + "/"
        self._search_path = settings.search_path
        self._token = settings.token
        self._timeout = settings.timeout_seconds
        self._max_results = settings.result_limit

    def _items(self, payload: Any) -> list[DirectoryEmployee]:
        raw_items = payload.get("items", payload.get("results", [])) if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            raise DirectoryUnavailableError("디렉터리 응답 형식이 올바르지 않습니다.")
        items: list[DirectoryEmployee] = []
        try:
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                items.append(DirectoryEmployee.model_validate(item))
        except ValidationError as exc:
            raise DirectoryUnavailableError("디렉터리 응답 필드가 올바르지 않습니다.") from exc
        return items

    def _search(self, query: str, limit: int) -> list[DirectoryEmployee]:
        effective_limit = min(max(limit, 1), self._max_results, 50)
        url = urljoin(self._base_url, self._search_path.lstrip("/"))
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(
                    url,
                    params={"q": query, "limit": effective_limit},
                    headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                )
                response.raise_for_status()
                return self._items(response.json())[:effective_limit]
        except (httpx.HTTPError, ValueError) as exc:
            raise DirectoryUnavailableError("사내 임직원 디렉터리에 연결할 수 없습니다.") from exc

    def search(self, query: str, limit: int) -> list[DirectoryEmployee]:
        return [item for item in self._search(query, limit) if item.employment_status == "ACTIVE"]

    def get_by_employee_id(self, employee_id: str) -> DirectoryEmployee | None:
        normalized = employee_id.strip()
        return next((item for item in self._search(normalized, 10) if item.employee_id == normalized), None)


class LocalEmployeeDirectory:
    """Local development adapter; it never impersonates or falls back from the intranet directory."""

    def search(self, query: str, limit: int) -> list[DirectoryEmployee]:
        normalized = query.strip().casefold()
        effective_limit = min(max(limit, 1), directory_settings().result_limit, 50)
        if not 2 <= len(normalized) <= 80:
            return []
        pattern = f"%{normalized}%"
        with connect() as conn:
            items = rows(
                conn.execute(
                    """
                    SELECT employee_id, display_name, department, job_title, email
                    FROM users
                    WHERE account_status='ACTIVE' AND is_active=true AND employee_id IS NOT NULL
                      AND (lower(employee_id) LIKE ? OR lower(display_name) LIKE ?)
                    ORDER BY display_name, employee_id LIMIT ?
                    """,
                    [pattern, pattern, effective_limit],
                )
            )
        return [DirectoryEmployee(**item, employment_status="ACTIVE") for item in items]

    def get_by_employee_id(self, employee_id: str) -> DirectoryEmployee | None:
        normalized = employee_id.strip()
        with connect() as conn:
            items = rows(
                conn.execute(
                    """
                    SELECT employee_id, display_name, department, job_title, email, account_status, is_active
                    FROM users WHERE employee_id=? LIMIT 1
                    """,
                    [normalized],
                )
            )
        if not items:
            return None
        item = items[0]
        return DirectoryEmployee(
            employee_id=item["employee_id"],
            display_name=item["display_name"],
            department=item.get("department"),
            job_title=item.get("job_title"),
            email=item.get("email"),
            employment_status=(
                "ACTIVE" if item["account_status"] == "ACTIVE" and item["is_active"] else "INACTIVE"
            ),
        )


_directory_override: EmployeeDirectory | None = None


def set_employee_directory_for_tests(directory: EmployeeDirectory | None) -> None:
    global _directory_override
    _directory_override = directory


def employee_directory() -> EmployeeDirectory:
    if _directory_override is not None:
        return _directory_override
    return HttpEmployeeDirectory() if directory_settings().mode == "http" else LocalEmployeeDirectory()
