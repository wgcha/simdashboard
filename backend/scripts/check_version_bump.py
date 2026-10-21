#!/usr/bin/env python3
"""Fail when a change that can be deployed does not change the version.

Rule (docs/features/version-board.md): every deployment changes ``backend/app/version.py``
``__version__``.  Between ``--base`` (the merge base with ``--head``) and ``--head``, if any file
outside the exempt set changed, ``__version__`` must be strictly greater than at the base.  Exempt:
documentation, logs, tests, GitHub workflows and any ``*.md`` (``RELEASES.md`` included, because the
version, not the text, is what a deployment must change).

    python scripts/check_version_bump.py --base <sha-or-ref> [--head HEAD]

A base that cannot be resolved (first push, force-push that dropped it) is reported and skipped:
there is nothing to compare, and failing would block the push for a reason the author cannot fix.
Exit 0 = fine or skipped, 1 = version not bumped, 2 = usage / git error.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

VERSION_FILE = "backend/app/version.py"
_VERSION_ASSIGNMENT = re.compile(r"""^__version__\s*=\s*["'](?P<version>[^"']+)["']\s*$""", re.MULTILINE)
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:\.[1-9]\d*)?(?:[-+][0-9A-Za-z.-]+)?$")

EXEMPT_PREFIXES = ("docs/", "log/", "backend/tests/", "frontend/e2e/", "local_runner/tests/", ".github/")
EXEMPT_SUFFIXES = (".md",)
EXEMPT_NAME_PARTS = ("self-test", ".test.", ".spec.")


def is_exempt(path: str) -> bool:
    """True for a changed file that does not by itself make a new deployment necessary."""
    name = path.rsplit("/", 1)[-1]
    return (
        path.startswith(EXEMPT_PREFIXES)
        or path.endswith(EXEMPT_SUFFIXES)
        or any(part in name for part in EXEMPT_NAME_PARTS)
        or path == ".gitignore"
    )


def deployable_changes(paths: list[str]) -> list[str]:
    return sorted(path for path in paths if path and not is_exempt(path))


def parse_version(text: str) -> str | None:
    match = _VERSION_ASSIGNMENT.search(text)
    return match["version"] if match and _VERSION.match(match["version"]) else None


def version_key(version: str) -> tuple[int, int, int, int]:
    core = re.split(r"[-+]", version, maxsplit=1)[0]
    major, minor, patch, *rest = (int(part) for part in core.split("."))
    return major, minor, patch, (rest[0] if rest else 0)


def evaluate(changed: list[str], old_version: str | None, new_version: str | None) -> tuple[bool, str]:
    """(ok, message) for the changed paths and the version before/after."""
    deployable = deployable_changes(changed)
    if not deployable:
        return True, "only documentation/test/workflow files changed: no version bump needed"
    if new_version is None:
        return False, f"cannot read __version__ from {VERSION_FILE}"
    if old_version is None:
        return True, f"no version at the base commit: nothing to compare (head is {new_version})"
    if version_key(new_version) > version_key(old_version):
        return True, f"version bumped {old_version} -> {new_version}"
    shown = ", ".join(deployable[:8]) + (f" and {len(deployable) - 8} more" if len(deployable) > 8 else "")
    return False, (
        f"{len(deployable)} deployable file(s) changed but the version is still {new_version} (base {old_version}): {shown}.\n"
        "Every deployment must change the version: raise __version__ in backend/app/version.py, add the matching entry on top of "
        "RELEASES.md, keep frontend/package.json at its x.y.z part, regenerate frontend/openapi.json (npm run generate:api). "
        "Small deployment: x.y.z -> x.y.z.1 -> x.y.z.2; whole release: x.y.(z+1) or x.(y+1).0. See docs/features/version-board.md."
    )


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="commit or ref the change is compared with (push 'before' SHA, PR base)")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]), help="repository root")
    args = parser.parse_args(argv)
    root = Path(args.repo)

    if not args.base.strip("0") or _git(root, "cat-file", "-e", f"{args.base}^{{commit}}").returncode != 0:
        print(f"version bump check skipped: base {args.base!r} is not a known commit")
        return 0
    merge_base = _git(root, "merge-base", args.base, args.head)
    base = merge_base.stdout.strip() if merge_base.returncode == 0 and merge_base.stdout.strip() else args.base
    diff = _git(root, "diff", "--name-only", "--no-renames", base, args.head)
    if diff.returncode != 0:
        print(diff.stderr.strip() or "git diff failed", file=sys.stderr)
        return 2
    old = _git(root, "show", f"{base}:{VERSION_FILE}")
    new = _git(root, "show", f"{args.head}:{VERSION_FILE}")
    ok, message = evaluate(
        diff.stdout.splitlines(),
        parse_version(old.stdout) if old.returncode == 0 else None,
        parse_version(new.stdout) if new.returncode == 0 else None,
    )
    print(("OK: " if ok else "FAIL: ") + message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
