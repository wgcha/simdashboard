"""Static contract for the optional external-wheels reinstall (ADR 0006).

The behaviour itself is exercised on Windows by
``scripts/windows/external-wheels-self-test.ps1`` and the source preparation
fixture in ``source-frontend-build-self-test.ps1``; these checks keep the
contract visible on any platform without running PowerShell.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_module_installs_only_local_wheels_without_dependencies() -> None:
    module = _read("scripts/windows/ExternalWheels.psm1")
    assert "pip install --python $Python --no-deps --no-index" in module
    assert "-m pip --isolated install --no-deps --no-index" in module
    for forbidden in ("--index-url", "--extra-index-url", "--find-links", "https://"):
        assert forbidden not in module
    assert "import $($script:AdapterModule)" in module and "'scx_drive_adapter'" in module
    # scx mode fails loudly; none mode only warns.
    assert "if ($DriveMode -eq 'scx') {" in module and "Write-Warning" in module


@pytest.mark.parametrize("script", ["setup.ps1", "scripts/windows/prepare-source-environment.ps1"])
def test_source_scripts_reinstall_external_wheels_after_lock_sync(script: str) -> None:
    text = _read(script)
    sync = text.index("pip sync --python")
    install = text.index("Install-ExternalWheels -Python")
    assert sync < install
    assert "(Join-Path $Root 'external-wheels')" in text
    assert "Get-ExternalWheelsDriveMode -EnvFiles @((Join-Path $Root '.env'), (Join-Path $Root 'backend\\.env'))" in text


def test_offline_installer_uses_persistent_state_folder_after_release_venv() -> None:
    text = _read("deploy/windows/offline/install.ps1")
    lock_install = text.index("'-r', (Join-Path $TargetRelease 'backend\\requirements.lock')")
    call = text.index("Install-ExternalWheels $python (Join-Path $StateRoot 'external-wheels')")
    assert lock_install < call
    assert "Get-EnvValue $stateEnv 'SIMDASH_DRIVE_GATEWAY'" in text[call : call + 200]
    assert "TargetRelease 'external-wheels'" not in text
    body = text[text.index("function Install-ExternalWheels") : text.index("function Get-OwnedService")]
    assert "install --no-deps --no-index" in body and "import scx_drive_adapter" in body
    assert "--find-links" not in body
    # The installer script stays ASCII for Windows PowerShell 5.1.
    (ROOT / "deploy/windows/offline/install.ps1").read_bytes().decode("ascii")


def test_external_wheels_folder_is_ignored_and_protected() -> None:
    gitignore = _read(".gitignore").splitlines()
    assert "/external-wheels/" in gitignore
    git_update = _read("scripts/windows/GitUpdate.psm1")
    assert re.search(r"\^\([^)]*\|external-wheels\)\(\$\|/\)", git_update)


def test_no_wheel_is_tracked_in_the_repository() -> None:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("git checkout not available")
    tracked = subprocess.run(
        [git, "ls-files", "*.whl"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert tracked == []


def test_windows_contract_ci_runs_the_self_test() -> None:
    workflow = _read(".github/workflows/windows-deployment-contract.yml")
    assert "scripts\\windows\\external-wheels-self-test.ps1" in workflow
    assert "tests/test_windows_external_wheels.py" in workflow
