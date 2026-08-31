from __future__ import annotations

from ...domains.project_invitations.models import DirectoryEmployee, DirectoryUnavailableError
from ...services.directory_service import DirectoryUnavailableError as ServiceDirectoryUnavailableError
from ...services.directory_service import employee_directory


class EmployeeDirectoryGateway:
    """Translate the provider's operational failure into a domain error."""

    def search(self, query: str, limit: int) -> list[DirectoryEmployee]:
        try:
            return [item.model_dump() for item in employee_directory().search(query, limit)]
        except ServiceDirectoryUnavailableError as error:
            raise DirectoryUnavailableError(str(error)) from error

    def get_by_employee_id(self, employee_id: str) -> DirectoryEmployee | None:
        try:
            item = employee_directory().get_by_employee_id(employee_id)
        except ServiceDirectoryUnavailableError as error:
            raise DirectoryUnavailableError(str(error)) from error
        return item.model_dump() if item is not None else None
