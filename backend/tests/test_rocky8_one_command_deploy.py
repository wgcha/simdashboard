from __future__ import annotations

from pathlib import Path
import os
import stat
import subprocess

import pytest


pytestmark = pytest.mark.unit


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_one_command_deployer_requires_an_ignored_private_config() -> None:
    root = Path(__file__).resolve().parents[2]
    deployer_path = root / "deploy" / "rocky8" / "deploy-from-source.sh"
    subprocess.run(["bash", "-n", str(deployer_path)], check=True)
    deployer = deployer_path.read_text(encoding="utf-8")

    assert "ls-files --error-unmatch" in deployer
    assert "git -C \"${project_root}\" check-ignore -q" in deployer
    assert "Config file must not be a symbolic link." in deployer
    assert "Config file must not be accessible by group or others" in deployer
    assert 'sudo install -o root -g root -m 0600 "${config_file}" "${root_config}"' in deployer
    assert "sudo \"${bundle_root}/install.sh\" --config \"${root_config}\" --check" in deployer
    assert "sudo systemctl is-active --quiet nginx.service" in deployer
    assert "sudo /usr/local/sbin/simdashboard-healthcheck" in deployer
    assert "--no-runtime-bootstrap" in deployer
    assert "SIMDASH_NODE_RUNTIME_CACHE" in deployer
    assert "b294a556e639d64338823920e5866c21c02741742d2e1529ee1a225c1ec9252a" in deployer
    assert "013b59cfd2819703a6f4a14ab891fc46fc2a4e3f5bcd92de3fb4929b43e35b30" in deployer
    assert "tar --no-same-owner --no-same-permissions -xzf" in deployer
    assert "HTTPS_PROXY/NO_PROXY" in deployer
    assert "bootstrap-python-runtime.sh" in deployer
    assert "python_runtime_bin='/opt/simdashboard/runtime/python/bin/python3.12'" in deployer
    assert "--without-wheels" in deployer
    assert 'PYTHON_BIN="${python_runtime_bin}" "${script_root}/build-release.sh"' in deployer
    assert "PYTHON_BIN, when configured, must equal" in deployer
    assert "sudo -v" in deployer
    assert "--preserve-env=HTTP_PROXY,HTTPS_PROXY,NO_PROXY,http_proxy,https_proxy,no_proxy,CURL_CA_BUNDLE,SSL_CERT_FILE" in deployer

    builder = (root / "deploy" / "rocky8" / "build-release.sh").read_text(encoding="utf-8")
    assert 'python_bin="${PYTHON_BIN:-python3.12}"' in builder
    assert '"${python_bin}" -m pip download' in builder


def test_one_command_deployer_runs_verified_bundle_flow_with_mocked_privilege_boundary(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[2]
    project = tmp_path / "repo"
    rocky = project / "deploy" / "rocky8"
    rocky.mkdir(parents=True)
    deployer = rocky / "deploy-from-source.sh"
    deployer.write_text(
        (source_root / "deploy" / "rocky8" / "deploy-from-source.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    deployer.chmod(0o700)
    (project / ".node-version").write_text("v22.23.2\n", encoding="utf-8")
    (project / ".gitignore").write_text("*.local.env\n", encoding="utf-8")
    config = project / "simdashboard.local.env"
    config.write_text("DATABASE_URL='postgresql://secret@example.invalid/db'\n", encoding="utf-8")
    config.chmod(0o600)

    _write_executable(
        rocky / "build-release.sh",
        """#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    *) shift ;;
  esac
done
stage="$(mktemp -d)"
mkdir -p "$stage/simdashboard-rocky8-test"
printf '%s\\n' '#!/usr/bin/env bash' 'exit 0' > "$stage/simdashboard-rocky8-test/install.sh"
chmod 700 "$stage/simdashboard-rocky8-test/install.sh"
(cd "$stage/simdashboard-rocky8-test" && sha256sum install.sh > SHA256SUMS)
tar -C "$stage" -czf "$output" simdashboard-rocky8-test
(cd "$(dirname "$output")" && sha256sum "$(basename "$output")" > "$(basename "$output").sha256")
rm -rf "$stage"
""",
    )

    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    trace = tmp_path / "sudo.trace"
    _write_executable(
        mock_bin / "sudo",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$MOCK_SUDO_TRACE"
if [[ "${1:-}" == -v ]]; then exit 0; fi
if [[ "${1:-}" == stat ]]; then printf '%s\\n' '0:0:600'; exit 0; fi
exit 0
""",
    )
    _write_executable(mock_bin / "node", "#!/usr/bin/env bash\nprintf '%s\\n' v22.23.2\n")
    _write_executable(mock_bin / "corepack", "#!/usr/bin/env bash\nexit 0\n")

    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    environment = os.environ | {
        "PATH": f"{mock_bin}:{os.environ['PATH']}",
        "MOCK_SUDO_TRACE": str(trace),
        "HTTPS_PROXY": "http://proxy.test:8080",
        "CURL_CA_BUNDLE": "/tmp/company-ca.pem",
    }
    result = subprocess.run(
        [str(deployer), "--config", str(config), "--allow-dirty"],
        cwd=project,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Verifying release archive checksum." in result.stdout
    assert "Verifying extracted release contents." in result.stdout
    assert "Rocky deployment completed; nginx and simdashboard are healthy." in result.stdout
    assert "Downloading verified Node.js" not in result.stdout
    calls = trace.read_text(encoding="utf-8")
    assert "install -o root -g root -m 0600" in calls
    assert "install.sh --config /root/simdashboard-install.env --check" in calls
    assert "install.sh --config /root/simdashboard-install.env" in calls
    assert "--preserve-env=HTTP_PROXY,HTTPS_PROXY,NO_PROXY,http_proxy,https_proxy,no_proxy,CURL_CA_BUNDLE,SSL_CERT_FILE" in calls
    assert "postgresql://secret" not in calls
    assert "proxy.test" not in calls
    assert "company-ca.pem" not in calls


def test_one_command_deployer_rejects_config_with_group_permissions(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    config = root / "deploy" / "rocky8" / ".pytest-insecure.local.env"
    config.write_text("DATABASE_URL='postgresql://secret@example.invalid/db'\n", encoding="utf-8")
    config.chmod(0o640)
    try:
        result = subprocess.run(
            [str(root / "deploy" / "rocky8" / "deploy-from-source.sh"), "--config", str(config)],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        config.unlink(missing_ok=True)

    assert result.returncode != 0
    assert "Config file must not be accessible by group or others" in result.stderr


def test_one_command_deployer_rejects_a_git_tracked_config_before_sudo(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[2]
    project = tmp_path / "repo"
    rocky = project / "deploy" / "rocky8"
    rocky.mkdir(parents=True)
    deployer = rocky / "deploy-from-source.sh"
    deployer.write_text(
        (source_root / "deploy" / "rocky8" / "deploy-from-source.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    deployer.chmod(0o700)
    (project / ".gitignore").write_text("*.local.env\n", encoding="utf-8")
    config = project / "simdashboard.local.env"
    config.write_text("DATABASE_URL='postgresql://secret@example.invalid/db'\n", encoding="utf-8")
    config.chmod(0o600)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "-f", str(config)], cwd=project, check=True)

    result = subprocess.run(
        [str(deployer), "--config", str(config)],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Config file is tracked by Git" in result.stderr


def test_one_command_deployer_rejects_dirty_worktree_before_any_sudo(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[2]
    project = tmp_path / "repo"
    rocky = project / "deploy" / "rocky8"
    rocky.mkdir(parents=True)
    deployer = rocky / "deploy-from-source.sh"
    deployer.write_text(
        (source_root / "deploy" / "rocky8" / "deploy-from-source.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    deployer.chmod(0o700)
    (project / ".gitignore").write_text("*.local.env\n", encoding="utf-8")
    config = project / "simdashboard.local.env"
    config.write_text("DATABASE_URL='postgresql://secret@example.invalid/db'\n", encoding="utf-8")
    config.chmod(0o600)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    trace = tmp_path / "sudo.trace"
    _write_executable(mock_bin / "sudo", "#!/usr/bin/env bash\nprintf call >> \"$MOCK_SUDO_TRACE\"\n")
    result = subprocess.run(
        [str(deployer), "--config", str(config)],
        cwd=project,
        env=os.environ | {"PATH": f"{mock_bin}:{os.environ['PATH']}", "MOCK_SUDO_TRACE": str(trace)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Refusing to deploy from a dirty Git worktree" in result.stderr
    assert not trace.exists()


def test_one_command_deployer_can_disable_node_runtime_bootstrap(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[2]
    project = tmp_path / "repo"
    rocky = project / "deploy" / "rocky8"
    rocky.mkdir(parents=True)
    deployer = rocky / "deploy-from-source.sh"
    deployer.write_text(
        (source_root / "deploy" / "rocky8" / "deploy-from-source.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    deployer.chmod(0o700)
    (project / ".node-version").write_text("v22.23.2\n", encoding="utf-8")
    (project / ".gitignore").write_text("*.local.env\n", encoding="utf-8")
    config = project / "simdashboard.local.env"
    config.write_text("DATABASE_URL='postgresql://secret@example.invalid/db'\n", encoding="utf-8")
    config.chmod(0o600)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    _write_executable(mock_bin / "node", "#!/usr/bin/env bash\nprintf '%s\\n' v20.0.0\n")
    _write_executable(mock_bin / "corepack", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(mock_bin / "sudo", "#!/usr/bin/env bash\nexit 0\n")
    result = subprocess.run(
        [str(deployer), "--config", str(config), "--no-runtime-bootstrap", "--allow-dirty"],
        cwd=project,
        env=os.environ | {"PATH": f"{mock_bin}:{os.environ['PATH']}"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Node.js v22.23.2 and corepack are required" in result.stderr


@pytest.mark.skipif(os.uname().machine != "x86_64", reason="fixture models the x86_64 Node archive")
def test_one_command_deployer_reuses_verified_preseeded_node_archive(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[2]
    project = tmp_path / "repo"
    rocky = project / "deploy" / "rocky8"
    rocky.mkdir(parents=True)
    deployer = rocky / "deploy-from-source.sh"
    deployer.write_text(
        (source_root / "deploy" / "rocky8" / "deploy-from-source.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    deployer.chmod(0o700)
    (project / ".node-version").write_text("v22.23.2\n", encoding="utf-8")
    (project / ".gitignore").write_text("*.local.env\n", encoding="utf-8")
    config = project / "simdashboard.local.env"
    config.write_text("DATABASE_URL='postgresql://secret@example.invalid/db'\n", encoding="utf-8")
    config.chmod(0o600)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    cache = tmp_path / "runtime-cache"
    cache.mkdir(mode=0o700)
    archive_root = tmp_path / "node-v22.23.2-linux-x64"
    (archive_root / "bin").mkdir(parents=True)
    _write_executable(archive_root / "bin" / "node", "#!/usr/bin/env bash\nprintf '%s\\n' v22.23.2\n")
    _write_executable(archive_root / "bin" / "corepack", "#!/usr/bin/env bash\nexit 0\n")
    subprocess.run(
        ["tar", "-C", str(tmp_path), "-czf", str(cache / "node-v22.23.2-linux-x64.tar.gz"), archive_root.name],
        check=True,
    )

    _write_executable(
        rocky / "build-release.sh",
        """#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 ]]; do
  case "$1" in --output) output="$2"; shift 2 ;; *) shift ;; esac
done
stage="$(mktemp -d)"
mkdir -p "$stage/simdashboard-rocky8-test"
printf '%s\\n' '#!/usr/bin/env bash' 'exit 0' > "$stage/simdashboard-rocky8-test/install.sh"
chmod 700 "$stage/simdashboard-rocky8-test/install.sh"
(cd "$stage/simdashboard-rocky8-test" && sha256sum install.sh > SHA256SUMS)
tar -C "$stage" -czf "$output" simdashboard-rocky8-test
(cd "$(dirname "$output")" && sha256sum "$(basename "$output")" > "$(basename "$output").sha256")
rm -rf "$stage"
""",
    )
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    _write_executable(mock_bin / "node", "#!/usr/bin/env bash\nprintf '%s\\n' v20.0.0\n")
    _write_executable(mock_bin / "corepack", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(mock_bin / "curl", "#!/usr/bin/env bash\nexit 99\n")
    _write_executable(
        mock_bin / "sha256sum",
        """#!/usr/bin/env bash
if [[ "$*" == *'--check --strict --status -'* ]]; then cat >/dev/null; exit 0; fi
exec /usr/bin/sha256sum "$@"
""",
    )
    _write_executable(
        mock_bin / "sudo",
        """#!/usr/bin/env bash
if [[ "${1:-}" == stat ]]; then printf '%s\\n' '0:0:600'; fi
exit 0
""",
    )
    result = subprocess.run(
        [str(deployer), "--config", str(config), "--allow-dirty"],
        cwd=project,
        env=os.environ | {"PATH": f"{mock_bin}:{os.environ['PATH']}", "SIMDASH_NODE_RUNTIME_CACHE": str(cache)},
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Downloading verified Node.js" not in result.stdout
    assert (cache / "node-v22.23.2-linux-x64" / "bin" / "node").exists()
