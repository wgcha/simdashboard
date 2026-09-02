from __future__ import annotations

import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient


def main() -> int:
    app = FastAPI()
    backend_options = {"use_uvloop": True} if sys.platform != "win32" else {}
    with TestClient(app, backend_options=backend_options) as client:
        response = client.get("/")
    if response.status_code != 404:
        raise RuntimeError(f"Unexpected TestClient response: {response.status_code}")
    backend_name = "uvloop" if backend_options else "default"
    print(f"FastAPI TestClient compatibility check passed ({backend_name} backend).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
