"""Offline fallback installer for separately supplied pure-Python wheels (ADR 0006).

``uv pip install`` writes a console-script launcher (``*.exe``) for every entry
point by patching an executable in %TEMP%.  Some company PCs (antivirus/AppLocker)
refuse that ("Failed to update Windows PE resources ... os error -2147024891").
The dashboard only needs ``import scx_drive_adapter`` in its own environment, so
this fallback unpacks the wheel's library files into the environment's
``purelib`` directory and skips console scripts.  Local files only, no network,
no dependency resolution.

Usage: python -I install_pure_wheels.py <wheel> [<wheel> ...]
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import sys
import sysconfig
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath

INSTALLER = "simdashboard-external-wheels"


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or ":" in path.parts[0]:
        raise ValueError(f"unsafe path in wheel: {name}")
    return path


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _remove_previous(purelib: Path, project: str) -> None:
    """Uninstall an earlier copy of the same project (files listed in its RECORD, inside purelib only)."""
    root = purelib.resolve()
    for info in purelib.glob("*.dist-info"):
        if _normalized(info.name.rsplit("-", 1)[0]) != project:
            continue
        record = info / "RECORD"
        if record.is_file():
            for row in csv.reader(io.StringIO(record.read_text(encoding="utf-8"))):
                if not row:
                    continue
                target = (purelib / row[0]).resolve()
                if target.is_file() and root in target.parents:
                    target.unlink()
        shutil.rmtree(info, ignore_errors=True)


def install(wheel: Path, purelib: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        infos = {_safe_member(name).parts[0] for name in names if _safe_member(name).parts[0].endswith(".dist-info")}
        if len(infos) != 1:
            raise ValueError(f"{wheel.name}: expected exactly one .dist-info directory")
        dist_info = infos.pop()
        metadata = Parser().parsestr(archive.read(f"{dist_info}/WHEEL").decode("utf-8"))
        if (metadata.get("Root-Is-Purelib") or "").strip().lower() != "true":
            raise ValueError(f"{wheel.name}: only pure-Python wheels are supported by this fallback")
        project = _normalized(dist_info.rsplit("-", 1)[0])
        data_dir = dist_info[: -len(".dist-info")] + ".data"
        _remove_previous(purelib, project)
        skipped = 0
        for name in names:
            member = _safe_member(name)
            if name.endswith("/"):
                continue
            parts = member.parts
            if parts[0] == data_dir:
                if len(parts) > 2 and parts[1] in {"purelib", "platlib"}:
                    member = PurePosixPath(*parts[2:])
                else:
                    skipped += 1          # scripts / headers / data: not needed for the import
                    continue
            target = purelib.joinpath(*member.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle)
        (purelib / dist_info / "INSTALLER").write_text(INSTALLER + "\n", encoding="utf-8")
    note = f" ({skipped} script/data file(s) skipped)" if skipped else ""
    return f"installed {wheel.name} into {purelib}{note}"


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: install_pure_wheels.py <wheel> [<wheel> ...]", file=sys.stderr)
        return 2
    purelib = Path(sysconfig.get_paths()["purelib"])
    for raw in argv:
        wheel = Path(raw)
        if wheel.suffix.lower() != ".whl" or not wheel.is_file():
            print(f"not a wheel file: {raw}", file=sys.stderr)
            return 2
        try:
            print(install(wheel, purelib))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
            print(f"{wheel.name}: {error}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
