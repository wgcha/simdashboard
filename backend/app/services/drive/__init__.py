"""SCX drive integration foundation (stage D0, docs/features/scx-drive.md).

Nothing here is active unless ``SIMDASH_DRIVE_GATEWAY=scx``.  The adapter
package ``scx_drive_adapter`` is installed separately and imported lazily by
:mod:`.gateway` only in scx mode.
"""
from __future__ import annotations

from .config import DriveConfigError, DriveSettings, drive_mode, drive_settings
from .gateway import (
    DRIVE_ERROR_MAP, DriveGatewayLike, DriveStartupError, current_settings, get_drive_gateway, map_drive_error,
    shutdown, startup, to_storage_error,
)

__all__ = [
    "DRIVE_ERROR_MAP", "DriveConfigError", "DriveGatewayLike", "DriveSettings", "DriveStartupError",
    "current_settings", "drive_mode", "drive_settings", "get_drive_gateway", "map_drive_error", "shutdown",
    "startup", "to_storage_error",
]
