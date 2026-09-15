"""PyInstaller entry point for the standalone Windows local helper."""

from local_runner.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
