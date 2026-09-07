from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess

import pytest


pytestmark = pytest.mark.unit


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "rocky8"


def _exe(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _copy_installer(tmp_path: Path) -> Path:
    source = DEPLOY / "install-offline.sh"
    if not source.exists():
        pytest.fail("offline installer has not been added yet")
    installer_dir = tmp_path / "installer"
    installer_dir.mkdir()
    target = installer_dir / "install-offline.sh"
    source_text = source.read_text(encoding="utf-8")
    os_release = installer_dir / "os-release"
    os_release.write_text('ID=rocky\nVERSION_ID=8.6\nPRETTY_NAME="Rocky Linux 8.6"\n', encoding="utf-8")
    # Make the copied fixture deterministic on the Ubuntu CI host and emulate
    # the root-only checks without invoking a real privileged operation.
    source_text = source_text.replace("/etc/os-release", str(os_release))
    source_text = source_text.replace('"${EUID}"', '"${TEST_EUID:-${EUID}}"')
    source_text = source_text.replace("$(stat -c '%u'", "$(stat -c '%u'")
    target.write_text(source_text, encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    return target


def _bundle(tmp_path: Path, *, missing: str | None = None, corrupt: str | None = None) -> Path:
    bundle = tmp_path / "bundle"
    (bundle / "rpm-repo" / "repodata").mkdir(parents=True)
    (bundle / "wheelhouse").mkdir()
    (bundle / "payload" / "backend").mkdir(parents=True)
    (bundle / "payload" / "frontend" / "dist").mkdir(parents=True)
    for relative, content in (
        ("runtime/python.tar.gz", b"python-runtime"),
        ("rpm-repo/repodata/repomd.xml", b"repodata"),
        ("rpm-repo/simdashboard-runtime.rpm", b"rpm"),
        ("wheelhouse/README", b"wheelhouse"),
        ("payload/backend/requirements.lock", b"requirement==1\n"),
        ("payload/.python-version", b"3.12.13\n"),
        ("payload/frontend/dist/index.html", b"<!doctype html>"),
        ("rpm-repo/RPM-GPG-KEY-Rocky-8", b"key"),
        ("RELEASE_ID", b"fixture\n"),
        ("install.sh", b"#!/usr/bin/env bash\nexit 0\n"),
    ):
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (bundle / "install.sh").chmod(0o755)
    (bundle / "payload/backend/app").mkdir()
    (bundle / "payload/backend/migrations").mkdir()
    files = [path for path in bundle.rglob("*") if path.is_file()]
    (bundle / "SHA256SUMS").write_text(
        "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{path.relative_to(bundle)}\n" for path in files),
        encoding="utf-8",
    )
    if missing:
        path = bundle / missing
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    if corrupt:
        path = bundle / corrupt
        path.write_bytes(path.read_bytes() + b"corrupt")
    return bundle


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "install.env"
    config.write_text("INSTALL_ROOT=/tmp/simdashboard-test\n", encoding="utf-8")
    config.chmod(0o600)
    return config


def _run(installer: Path, bundle: Path, config: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"TEST_EUID": "0", **extra}
    runner = bundle / "install-offline.sh"
    shutil.copy2(installer, runner)
    runner.chmod(runner.stat().st_mode | stat.S_IXUSR)
    with (bundle / "SHA256SUMS").open("a", encoding="utf-8") as manifest:
        manifest.write(f"{hashlib.sha256(runner.read_bytes()).hexdigest()}  ./install-offline.sh\n")
    return subprocess.run(
        [str(runner), "--config", str(config), *args],
        cwd=bundle,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _root_stat_bin(tmp_path: Path) -> Path:
    bindir = tmp_path / "root-bin"
    bindir.mkdir(exist_ok=True)
    _exe(bindir / "stat", """#!/usr/bin/env bash
if [[ "${1:-}" == -c && "${2:-}" == %u ]]; then printf '0\\n'; exit 0; fi
if [[ "${1:-}" == -c && "${2:-}" == %a ]]; then printf '600\\n'; exit 0; fi
exec /usr/bin/stat "$@"
""")
    for name in ("dnf", "rpm"):
        _exe(bindir / name, "#!/usr/bin/env bash\nexit 0\n")
    return bindir


def test_offline_scripts_are_parseable_and_expose_check_mode() -> None:
    for name in ("install-offline.sh", "build-offline-release.sh"):
        script = DEPLOY / name
        assert script.is_file(), name
        subprocess.run(["bash", "-n", str(script)], check=True)
    result = subprocess.run([str(DEPLOY / "install-offline.sh"), "--help"], check=True, capture_output=True, text=True)
    assert "--check" in result.stdout
    assert "--bundle" not in result.stdout


def test_offline_installer_uses_the_same_strict_rocky_8_6_gate(tmp_path: Path) -> None:
    installer = _copy_installer(tmp_path)
    source = installer.read_text(encoding="utf-8")
    assert "VERSION_ID:-}" in source and "== 8.6" in source
    bad_os = tmp_path / "bad-os-release"
    bad_os.write_text("ID=rocky\nVERSION_ID=8.10\n", encoding="utf-8")
    bad_dir = tmp_path / "bad-installer"
    bad_dir.mkdir()
    bad_installer = bad_dir / "install-offline.sh"
    bad_installer.write_text(source.replace(str(tmp_path / "installer" / "os-release"), str(bad_os)), encoding="utf-8")
    bad_installer.chmod(0o700)
    result = subprocess.run([str(bad_installer), "--config", str(_config(tmp_path))], cwd=_bundle(tmp_path / "badbundle"), env=os.environ | {"TEST_EUID": "0", "PATH": f"{_root_stat_bin(tmp_path)}:{os.environ['PATH']}"}, check=False, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Rocky Linux 8.6" in result.stderr


def test_offline_installer_rejects_missing_component_before_privileged_mutation(tmp_path: Path) -> None:
    installer = _copy_installer(tmp_path)
    bundle = _bundle(tmp_path, missing="wheelhouse")
    trace = tmp_path / "trace"
    config = _config(tmp_path)
    result = _run(installer, bundle, config, PATH=f"{_root_stat_bin(tmp_path)}:{os.environ['PATH']}", env_trace=str(trace))
    assert result.returncode != 0
    assert "wheelhouse" in (result.stdout + result.stderr) or "checksum" in (result.stdout + result.stderr).lower()
    assert not trace.exists(), "bundle validation must precede package/install commands"


def test_offline_installer_rejects_checksum_corruption_before_privileged_mutation(tmp_path: Path) -> None:
    installer = _copy_installer(tmp_path)
    bundle = _bundle(tmp_path, corrupt="runtime/python.tar.gz")
    trace = tmp_path / "trace"
    result = _run(installer, bundle, _config(tmp_path), PATH=f"{_root_stat_bin(tmp_path)}:{os.environ['PATH']}", env_trace=str(trace))
    assert result.returncode != 0
    assert "checksum" in (result.stdout + result.stderr).lower()
    assert not trace.exists()


def test_offline_check_does_not_install_packages_or_publish_runtime(tmp_path: Path) -> None:
    installer = _copy_installer(tmp_path)
    bundle = _bundle(tmp_path)
    runtime = tmp_path / "runtime-tree" / "python" / "bin"
    runtime.mkdir(parents=True)
    _exe(runtime / "python3.12", """#!/usr/bin/env bash
if [[ "${1:-}" == -c ]]; then printf '3.12.13\\n'; exit 0; fi
if [[ "${1:-}" == -m && "${2:-}" == venv ]]; then
  mkdir -p "$3/bin"; cp "$0" "$3/bin/python"; exit 0
fi
exit 0
""")
    archive = tmp_path / "python.tar.gz"
    subprocess.run(["tar", "-czf", str(archive), "-C", str(tmp_path / "runtime-tree"), "python"], check=True)
    shutil.copy2(archive, bundle / "runtime/python.tar.gz")
    installer_text = installer.read_text(encoding="utf-8")
    installer.write_text(
        installer_text.replace("3c3427e5628648478da2aa227472c350475a68bc58109f1b43849636a4aecb89", hashlib.sha256(archive.read_bytes()).hexdigest()),
        encoding="utf-8",
    )
    # Rebuild the exact manifest after replacing the runtime fixture.
    (bundle / "SHA256SUMS").write_text(
        "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{path.relative_to(bundle)}\n" for path in bundle.rglob("*") if path.is_file() and path.name != "SHA256SUMS"),
        encoding="utf-8",
    )
    trace = tmp_path / "trace"
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    _exe(mock_bin / "dnf", '#!/usr/bin/env bash\nprintf "dnf %s\\n" "$*" >> "$TRACE"\n')
    _exe(mock_bin / "install", '#!/usr/bin/env bash\nprintf "install %s\\n" "$*" >> "$TRACE"\n')
    runtime_target = tmp_path / "published-runtime"
    result = _run(
        installer,
        bundle,
        _config(tmp_path),
        "--check",
        PATH=f"{mock_bin}:{_root_stat_bin(tmp_path)}:{os.environ['PATH']}",
        TRACE=str(trace),
        SIMDASH_RUNTIME_ROOT=str(runtime_target),
    )
    assert result.returncode == 0, result.stderr
    assert not trace.exists()
    assert not runtime_target.exists()


def test_offline_package_install_uses_only_local_repo_and_gpg_check(tmp_path: Path) -> None:
    installer = _copy_installer(tmp_path)
    source = installer.read_text(encoding="utf-8")
    # This assertion is paired with the executable preflight tests above: it
    # guards the security boundary without requiring a Rocky host or root.
    assert "--disablerepo='*'" in source or '--disablerepo="*"' in source
    assert "--repofrompath" in source
    assert "gpgcheck=1" in source or "--setopt=gpgcheck=1" in source
    assert "https://" not in source


def _signature_check_block() -> str:
    source = (DEPLOY / "build-offline-release.sh").read_text(encoding="utf-8")
    match = re.search(r"rpm_count=0\nwhile IFS=.*?done < <\(find .*?\n", source, re.DOTALL)
    assert match, "builder RPM signature verification loop is missing"
    return match.group(0)


def _run_signature_check(tmp_path: Path, rpmkeys_body: str) -> subprocess.CompletedProcess[str]:
    repo = tmp_path / "repo"
    (repo / "rpm-repo").mkdir(parents=True)
    (repo / "rpm-repo" / "sample.rpm").write_bytes(b"rpm")
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    _exe(mock_bin / "rpmkeys", rpmkeys_body)
    harness = tmp_path / "signature-check.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -Eeuo pipefail\ndie() { printf '[ERROR] %s\\n' \"$*\" >&2; exit 1; }\n"
        f"bundle_root={repo}\n"
        + _signature_check_block()
        + "printf 'verified\\n'\n",
        encoding="utf-8",
    )
    harness.chmod(0o700)
    return subprocess.run([str(harness)], env=os.environ | {"PATH": f"{mock_bin}:{os.environ['PATH']}"}, check=False, capture_output=True, text=True)


def test_builder_signature_capture_handles_long_rpmkeys_output_under_pipefail(tmp_path: Path) -> None:
    result = _run_signature_check(
        tmp_path,
        "#!/usr/bin/env bash\nprintf 'pgp sha256 OK\\n'\nfor i in $(seq 1 10000); do printf 'digest line %s\\n' \"$i\"; done\n",
    )
    assert result.returncode == 0, result.stderr
    assert "verified" in result.stdout


def test_builder_signature_check_fails_closed_when_rpmkeys_fails(tmp_path: Path) -> None:
    result = _run_signature_check(
        tmp_path,
        "#!/usr/bin/env bash\nprintf 'pgp sha256 OK\\n'\nprintf 'signature unavailable\\n'\nexit 9\n",
    )
    assert result.returncode != 0
    assert "RPM signature verification failed" in result.stderr
