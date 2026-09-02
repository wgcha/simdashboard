from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...domains.dashboard_commands.policies import preview_command


def preview_dashboard_command(
    command: str,
    *,
    epoch: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Build a non-mutating dashboard proposal from an allowlisted command."""

    return preview_command(command, epoch=epoch)
