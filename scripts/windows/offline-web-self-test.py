"""Exercise the installer Caddy template with real static files and a fake API.

No database or Windows service is touched. Requires a prepared Caddy executable
and the production frontend build; all test listeners bind to loopback.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import threading
import time
from urllib.request import ProxyHandler, build_opener


class Api(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok","database_backend":"postgresql"}')

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    installer = (root / "deploy/windows/offline/install.ps1").read_text(encoding="utf-8-sig")
    template = re.search(r'\$caddyText = @"\r?\n(.*?)\r?\n"@', installer, re.S)
    assert template, "Installer Caddy template missing"
    api = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        web_port = port_probe.getsockname()[1]
    base = f"http://127.0.0.1:{web_port}"
    config = template.group(1)
    for name, value in {
        "siteAddress": base,
        "tlsLine": "",
        "staticRoot": args.frontend.resolve().as_posix(),
        "apiPort": str(api.server_port),
    }.items():
        config = config.replace("$" + name, value)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    client = build_opener(ProxyHandler({}))
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="workbench-web-test-") as directory:
            config_path = Path(directory) / "Caddyfile"
            config_path.write_text(config, encoding="utf-8")
            with (Path(directory) / "caddy.log").open("w") as log:
                process = subprocess.Popen(
                    [str(args.caddy.resolve()), "run", "--config", str(config_path), "--adapter", "caddyfile"],
                    stdout=log, stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                for _ in range(100):
                    if process.poll() is not None:
                        raise AssertionError("Caddy exited: " + (Path(directory) / "caddy.log").read_text())
                    try:
                        with client.open(base + "/home/", timeout=1) as response:
                            html = response.read().decode()
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise AssertionError("Caddy did not become ready")
                assert html == (args.frontend / "index.html").read_text(encoding="utf-8")
                asset_paths = re.findall(r'(?:src|href)="(/home/assets/[^"?]+)', html)
                assert asset_paths, "Production build must contain local /home/assets references"
                for path in asset_paths:
                    with client.open(base + path, timeout=3) as response:
                        assert response.read() == (args.frontend / path.removeprefix("/home/")).read_bytes(), path
                for path in ("/api/health", "/assets/test-fixture"):
                    with client.open(base + path, timeout=3) as response:
                        assert b'"database_backend":"postgresql"' in response.read(), path
                for path in ("/", "/home", "/home/results/test"):
                    with client.open(base + path, timeout=3) as response:
                        assert response.read().decode() == html, path
                print(f"Offline web routing passed: HTML, {len(asset_paths)} assets, API, media, redirects and SPA.")
                process.terminate()
                process.wait(timeout=10)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        api.shutdown()
        api.server_close()


if __name__ == "__main__":
    main()
