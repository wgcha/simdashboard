from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

from local_runner.picker import pick_paths


def test_frozen_picker_relaunches_its_own_executable_without_module_mode(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    result = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"paths":["C:\\\\tool.exe"]}', stderr="")
    with patch("local_runner.picker.subprocess.run", return_value=result) as run:
        assert pick_paths("program") == [r"C:\tool.exe"]
    assert run.call_args.args[0] == [sys.executable, "--picker-helper", "--kind", "program"]
