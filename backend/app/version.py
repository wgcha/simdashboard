"""The dashboard's release version: the one value every other copy must equal.

Bumped together with ``frontend/package.json`` ``version`` and the top entry of
``RELEASES.md`` when a deployment is cut (``docs/features/version-board.md``).
``tests/test_releases.py`` fails when the three disagree.  The API exposes it as
``GET /api/releases`` and the UI shows it as the top-bar version button.
"""
from __future__ import annotations

__version__ = "0.3.0"
