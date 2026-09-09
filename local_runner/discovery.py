"""Bounded Windows application discovery.

This module only reads App Paths/uninstall registry values and a short list of
well-known executable locations.  It never recursively scans disks and it does
not cause discovered candidates to execute.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from .executor import validate_executable


_KNOWN_EXECUTABLES = {
    "HyperMesh": ("hmopengl.exe", "hypermesh.exe"),
}
_KEYWORDS = {"HyperMesh": ["hypermesh", "mesh", "메시", "메시 수정", "전처리"]}


def _candidate(name: str, executable: Path, version: str = "") -> dict[str, object] | None:
    try:
        executable = validate_executable(str(executable))
    except ValueError:
        return None
    inferred_version = version or _version_from_path(executable) or "버전 확인 필요"
    return {
        "name": name,
        "version": inferred_version,
        "keywords": _KEYWORDS.get(name, [name.lower()]),
        "executable_path": str(executable),
        "arguments": [],
    }


def _version_from_path(path: Path) -> str:
    match = re.search(r"(?:20\d{2}(?:\.\d+)*)", str(path))
    return match.group(0) if match else ""


def _registry_paths() -> Iterable[tuple[str, Path, str]]:
    if os.name != "nt":
        return []
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return []

    values: list[tuple[str, Path, str]] = []
    hives = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    wow_flags = (0, getattr(winreg, "KEY_WOW64_32KEY", 0), getattr(winreg, "KEY_WOW64_64KEY", 0))
    for hive in hives:
        for flag in wow_flags:
            access = winreg.KEY_READ | flag
            for name, executables in _KNOWN_EXECUTABLES.items():
                for executable_name in executables:
                    try:
                        with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable_name}", 0, access) as key:
                            path, _ = winreg.QueryValueEx(key, None)
                        values.append((name, Path(str(path).strip('"')), ""))
                    except OSError:
                        pass
            uninstall_roots = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",)
            for root in uninstall_roots:
                try:
                    with winreg.OpenKey(hive, root, 0, access) as key:
                        index = 0
                        while True:
                            try:
                                subkey_name = winreg.EnumKey(key, index)
                                index += 1
                            except OSError:
                                break
                            try:
                                with winreg.OpenKey(key, subkey_name) as item:
                                    display, _ = winreg.QueryValueEx(item, "DisplayName")
                                    if "hypermesh" not in str(display).lower():
                                        continue
                                    install, _ = winreg.QueryValueEx(item, "InstallLocation")
                                install_path = Path(str(install))
                                for executable_name in _KNOWN_EXECUTABLES["HyperMesh"]:
                                    for relative in (Path(executable_name), Path("hm") / "bin" / "win64" / executable_name, Path("bin") / "win64" / executable_name):
                                        values.append(("HyperMesh", install_path / relative, _version_from_path(Path(str(display)))))
                            except OSError:
                                pass
                except OSError:
                    pass
    return values


def _known_paths() -> Iterable[tuple[str, Path, str]]:
    if os.name != "nt":
        return []
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    paths: list[tuple[str, Path, str]] = []
    for root in filter(None, roots):
        altair_root = Path(root) / "Altair"
        version_roots: list[Path] = []
        if altair_root.is_dir():
            # One immediate, capped directory listing is a bounded known-vendor
            # lookup, not an operating-system-wide program scan.
            version_roots = [path for path in list(altair_root.iterdir())[:100] if path.is_dir() and re.fullmatch(r"20\d{2}(?:\.\d+)*", path.name)]
        for executable_name in _KNOWN_EXECUTABLES["HyperMesh"]:
            # A finite set of vendor layouts, deliberately without globs/scans.
            for relative in (
                Path("Altair") / "hm" / "bin" / "win64" / executable_name,
                Path("Altair") / "HyperWorks" / "bin" / "win64" / executable_name,
            ):
                paths.append(("HyperMesh", Path(root) / relative, ""))
            for version_root in version_roots:
                paths.append(("HyperMesh", version_root / "hm" / "bin" / "win64" / executable_name, version_root.name))
    return paths


def discover_programs() -> list[dict[str, object]]:
    """Return de-duplicated valid candidates discovered from bounded sources."""

    results: list[dict[str, object]] = []
    seen: set[str] = set()
    for name, path, version in [*_registry_paths(), *_known_paths()]:
        candidate = _candidate(name, path, version)
        if candidate is None:
            continue
        identity = str(candidate["executable_path"]).casefold()
        if identity not in seen:
            seen.add(identity)
            results.append(candidate)
    return results
