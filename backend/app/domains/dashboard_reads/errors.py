from __future__ import annotations


class DashboardReadError(Exception):
    """Base error for dashboard read use cases."""


class DashboardNotFoundError(DashboardReadError):
    pass


class DashboardVersionNotFoundError(DashboardReadError):
    pass
