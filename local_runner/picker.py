"""Bridge to the isolated Tk native picker helper."""

from __future__ import annotations

import json
import subprocess
import sys


def pick_paths(kind: str) -> list[str]:
    if kind not in {"program", "files", "directory"}:
        raise ValueError("Unsupported picker kind")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # A PyInstaller executable has no importable ``local_runner`` module in a
    # child process, so ``sys.executable -m …`` only works while developing
    # from source.  The frozen runner dispatches this narrow helper mode from
    # its own entry point instead.
    command = (
        [sys.executable, "--picker-helper", "--kind", kind]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "local_runner.picker_helper", "--kind", kind]
    )
    completed = subprocess.run(
        command,
        shell=False,
        capture_output=True,
        text=True,
        timeout=180,
        creationflags=creationflags,
        check=False,
    )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The native picker did not return a valid response.") from exc
    if completed.returncode != 0:
        raise RuntimeError(str(result.get("error", "The native picker could not open.")))
    paths = result.get("paths")
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise RuntimeError("The native picker returned invalid paths.")
    return paths
