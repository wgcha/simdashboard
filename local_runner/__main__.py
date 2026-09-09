from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import uvicorn

from .server import _load_connection_code, create_app
from .instance_lock import InstanceBusyError, acquire_instance_lock
from .picker_helper import main as picker_helper_main


def main(argv: Sequence[str] | None = None) -> int:
    received = list(sys.argv[1:] if argv is None else argv)
    if "--picker-helper" in received:
        received.remove("--picker-helper")
        return picker_helper_main(received)
    parser = argparse.ArgumentParser(description="Local Program Runner")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--origin", action="append", default=None)
    parser.add_argument("--server-url")
    parser.add_argument("--standalone", action="store_true", help="Use the legacy connection-code development API.")
    parser.add_argument("--print-token", action="store_true")
    args = parser.parse_args(received)
    data_dir = args.data_dir or Path.home() / ".simulation-workbench" / "local-runner"
    if args.print_token:
        if not args.standalone:
            parser.error("--print-token requires --standalone")
        data_dir.mkdir(parents=True, exist_ok=True)
        print(_load_connection_code(data_dir, None))
        return 0
    if args.standalone and args.server_url:
        parser.error("--standalone cannot be combined with --server-url")
    if not args.standalone and not args.server_url:
        parser.error("managed mode requires --server-url (use --standalone only for legacy development)")
    try:
        instance_lock = acquire_instance_lock(data_dir)
    except InstanceBusyError as exc:
        parser.exit(1, f"Local runner not started: {exc}\n")
    try:
        uvicorn.run(
            create_app(data_dir=data_dir, allowed_origins=args.origin, server_url=None if args.standalone else args.server_url),
            host="127.0.0.1", port=args.port,
        )
    finally:
        instance_lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
