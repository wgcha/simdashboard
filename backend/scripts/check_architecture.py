#!/usr/bin/env python3
"""Enforce the R1 backend boundary baseline without rewriting source files.

The baseline is deliberately checked in and read-only: a new router starts with
an execute-call ceiling of zero, and this tool has no update mode.  Moving
database access out of an existing router therefore lowers its allowance; it
never silently raises the allowed count.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections.abc import Iterable
from pathlib import Path


FORBIDDEN_DOMAIN_ROOTS = frozenset({"fastapi", "starlette", "sqlalchemy", "duckdb", "psycopg"})
FORBIDDEN_PERSISTENCE_ROOTS = frozenset({"fastapi", "starlette"})
FORBIDDEN_DOMAIN_MODULE_PREFIXES = (
    "app.database",
    "app.database_connection",
    "app.adapters.persistence",
    "app.repositories",
)
DEFAULT_EXECUTE_CALL_CEILINGS = {
    "app/main.py": 28,
    "app/routers/access_control.py": 0,
    "app/routers/modeling_catalog.py": 1,
    "app/routers/managed_local_execution.py": 0,
    "app/routers/security.py": 7,
    "app/routers/workbench.py": 49,
}
CHECKED_IN_BASELINE_PATH = Path(__file__).with_name("architecture_baseline.json").resolve()


def _relative(path: Path, backend_root: Path) -> str:
    return path.relative_to(backend_root).as_posix()


def _execute_calls(tree: ast.AST) -> int:
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for node in ast.walk(tree)
    )


def _parse(path: Path, violations: list[str]) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        violations.append(f"{path}: unable to parse Python source: {error}")
        return None


def _source_package(source: Path, backend_root: Path) -> tuple[str, ...]:
    """Return the import package containing an app source file."""
    relative = source.relative_to(backend_root).with_suffix("")
    return relative.parts[:-1]


def _imported_module_names(node: ast.Import | ast.ImportFrom, package: tuple[str, ...]) -> set[str]:
    """Resolve absolute and relative AST imports to their possible module names."""
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}

    if node.level:
        trim = node.level - 1
        base = package[: len(package) - trim] if trim <= len(package) else ()
        module_parts = tuple((node.module or "").split(".")) if node.module else ()
        resolved_module = ".".join((*base, *module_parts))
    else:
        resolved_module = node.module or ""

    names = {resolved_module} if resolved_module else set()
    for alias in node.names:
        names.add(".".join(part for part in (resolved_module, alias.name) if part))
    return names


def _forbidden_domain_import(node: ast.Import | ast.ImportFrom, package: tuple[str, ...]) -> str | None:
    for imported in sorted(_imported_module_names(node, package)):
        if imported.split(".", 1)[0] in FORBIDDEN_DOMAIN_ROOTS:
            return imported
        if any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in FORBIDDEN_DOMAIN_MODULE_PREFIXES):
            return imported
    return None


def _domain_files(app_root: Path) -> Iterable[Path]:
    for name in ("domain", "domains"):
        directory = app_root / name
        if directory.is_dir():
            yield from sorted(directory.rglob("*.py"))


def _persistence_files(app_root: Path) -> Iterable[Path]:
    directory = app_root / "adapters" / "persistence"
    if directory.is_dir():
        yield from (path for path in sorted(directory.rglob("*.py")) if path.name != "__init__.py")


def _application_files(app_root: Path) -> Iterable[Path]:
    directory = app_root / "application"
    if directory.is_dir():
        yield from (path for path in sorted(directory.rglob("*.py")) if path.name != "__init__.py")


def load_baseline(path: Path) -> dict[str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ceilings = payload.get("execute_call_ceilings")
    if payload.get("version") != 1 or not isinstance(ceilings, dict):
        raise ValueError("baseline must contain version 1 and execute_call_ceilings")
    if any(not isinstance(key, str) or not isinstance(value, int) or value < 0 for key, value in ceilings.items()):
        raise ValueError("baseline execute_call_ceilings values must be non-negative integers")
    return ceilings


def _router_files(app_root: Path) -> Iterable[Path]:
    """Yield both legacy and vertical-slice HTTP router modules."""
    for directory in (app_root / "routers", app_root / "adapters" / "http" / "routers"):
        if directory.is_dir():
            yield from (path for path in sorted(directory.rglob("*.py")) if path.name != "__init__.py")


def check_project(backend_root: Path, baseline_path: Path) -> list[str]:
    """Return all detected violations for a backend source tree."""
    violations: list[str] = []
    try:
        ceilings = load_baseline(baseline_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [f"{baseline_path}: invalid architecture baseline: {error}"]

    app_root = backend_root / "app"
    if baseline_path.resolve() == CHECKED_IN_BASELINE_PATH and ceilings != DEFAULT_EXECUTE_CALL_CEILINGS:
        violations.append(
            "checked-in architecture baseline differs from DEFAULT_EXECUTE_CALL_CEILINGS; "
            "do not raise the execute-call ceiling."
        )
    main_file = app_root / "main.py"
    source_files = [main_file]
    source_files.extend(_router_files(app_root))

    seen_paths: set[str] = set()
    for source in source_files:
        relative = _relative(source, backend_root)
        seen_paths.add(relative)
        tree = _parse(source, violations)
        if tree is None:
            continue
        actual = _execute_calls(tree)
        ceiling = ceilings.get(relative, 0)
        if actual > ceiling:
            violations.append(
                f"{relative}: .execute() calls increased to {actual}; ceiling is {ceiling}. "
                "Move new persistence work behind an application/repository boundary."
            )

    for relative in sorted(set(ceilings) - seen_paths):
        violations.append(f"baseline has no matching source file: {relative}")
    if "app/main.py" not in ceilings:
        violations.append("baseline must explicitly pin app/main.py")

    for source in _domain_files(app_root):
        relative = _relative(source, backend_root)
        tree = _parse(source, violations)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imported = _forbidden_domain_import(node, _source_package(source, backend_root))
                if imported is not None:
                    violations.append(f"{relative}:{node.lineno}: domain code must not import {imported}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute":
                violations.append(f"{relative}:{node.lineno}: domain code must not call .execute()")
    for source in _persistence_files(app_root):
        relative = _relative(source, backend_root)
        tree = _parse(source, violations)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for imported in sorted(_imported_module_names(node, _source_package(source, backend_root))):
                if imported.split(".", 1)[0] in FORBIDDEN_PERSISTENCE_ROOTS:
                    violations.append(
                        f"{relative}:{node.lineno}: persistence code must not import {imported}"
                    )
    for source in _application_files(app_root):
        relative = _relative(source, backend_root)
        tree = _parse(source, violations)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            imported = _forbidden_domain_import(node, _source_package(source, backend_root))
            if imported is not None:
                violations.append(f"{relative}:{node.lineno}: application code must not import {imported}")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="backend root containing app/ (default: this script's parent)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path(__file__).with_name("architecture_baseline.json"),
        help="checked-in architecture baseline (read only)",
    )
    args = parser.parse_args(argv)
    violations = check_project(args.root.resolve(), args.baseline.resolve())
    if violations:
        print("ARCHITECTURE_CHECK_FAILED")
        print("\n".join(f"- {violation}" for violation in violations))
        return 1
    print("ARCHITECTURE_CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
