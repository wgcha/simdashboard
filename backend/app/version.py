"""The dashboard's release version: the one value every other copy must equal.

Every deployment changes it: ``x.y.z`` for a whole release, ``x.y.z.n`` (n >= 1) for a small deployment of
release ``x.y.z``.  Bumped together with the top entry of ``RELEASES.md`` (same string) and
``frontend/package.json`` ``version`` (only the ``x.y.z`` part: npm semver has no fourth part); see
``docs/features/version-board.md``.  ``tests/test_releases.py`` fails when they disagree and
``scripts/check_version_bump.py`` (CI) fails when code changes without a new version.  The API exposes it
as ``GET /api/releases`` and the UI shows it as the top-bar version button.
"""
from __future__ import annotations

__version__ = "0.3.0.3"
