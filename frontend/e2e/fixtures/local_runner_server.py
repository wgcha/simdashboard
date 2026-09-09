"""Disposable real loopback companion for the browser regression tests."""

from pathlib import Path
import sys

import uvicorn

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from local_runner.server import create_app  # noqa: E402


if __name__ == "__main__":
    app = create_app(
        data_dir=Path(sys.argv[1]),
        server_url="http://127.0.0.1:18000",
        confirm_pairing=lambda account: True,  # Test seam only; production always asks on the PC.
        allowed_origins=["http://127.0.0.1:15173"],
    )
    uvicorn.run(app, host="127.0.0.1", port=8766, access_log=False, log_level="warning")
