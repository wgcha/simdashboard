"""Tiny native file picker launched independently from the FastAPI process."""

from __future__ import annotations

import argparse
import json
import sys


def select_paths(kind: str) -> list[str]:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "program":
            path = filedialog.askopenfilename(title="Select local program", filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
            return [path] if path else []
        if kind == "files":
            return list(filedialog.askopenfilenames(title="Select input files"))
        if kind == "directory":
            path = filedialog.askdirectory(title="Select working directory")
            return [path] if path else []
        raise ValueError("Unsupported picker kind")
    finally:
        root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("program", "files", "directory"), required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps({"paths": select_paths(args.kind)}))
        return 0
    except Exception as exc:  # The API returns an actionable failure to the caller.
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
