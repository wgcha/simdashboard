from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from local_runner.executor import RunExecutor, prepare_command, validate_executable


class MemoryUpdater:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, int | None, str | None]] = []

    def mark_started(self, run_id: str) -> None:
        self.events.append((run_id, "RUNNING", None, None))

    def mark_finished(self, run_id: str, *, status: str, exit_code: int | None, error: str | None = None) -> None:
        self.events.append((run_id, status, exit_code, error))


def wait_for(updater: MemoryUpdater, expected: int) -> None:
    deadline = time.monotonic() + 5
    while len(updater.events) < expected and time.monotonic() < deadline:
        time.sleep(0.02)
    if len(updater.events) < expected:
        raise AssertionError(f"Timed out waiting for {expected} events: {updater.events!r}")


class ExecutorTests(unittest.TestCase):
    def test_direct_exit_requires_explicit_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_file = root / "case.inp"
            input_file.write_text("fixture")
            updater = MemoryUpdater()
            program = {"executable_path": sys.executable, "arguments": ["-c", "import sys; sys.exit(0)"]}
            RunExecutor(updater).start_direct(
                {"id": "direct", "input_path": str(input_file), "working_directory": str(root)}, program
            )
            wait_for(updater, 2)
            self.assertEqual(updater.events, [("direct", "RUNNING", None, None), ("direct", "AWAITING_COMPLETION", 0, None)])

    def test_batch_runs_in_order_and_keeps_partial_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_file = root / "case.inp"
            input_file.write_text("fixture")
            updater = MemoryUpdater()
            program = {"executable_path": sys.executable, "arguments": ["-c", "import sys; sys.exit(0 if sys.argv[-1].endswith('ok.inp') else 5)", "{input}"]}
            ok_file = root / "ok.inp"
            ok_file.write_text("ok")
            bad_file = root / "bad.inp"
            bad_file.write_text("bad")
            RunExecutor(updater).start_batch(
                [
                    {"id": "bad", "input_path": str(bad_file), "working_directory": str(root)},
                    {"id": "ok", "input_path": str(ok_file), "working_directory": str(root)},
                ],
                program,
            )
            wait_for(updater, 4)
            self.assertEqual(
                updater.events,
                [("bad", "RUNNING", None, None), ("bad", "FAILED", 5, None), ("ok", "RUNNING", None, None), ("ok", "SUCCEEDED", 0, None)],
            )

    def test_shell_files_and_missing_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "unsafe.cmd"
            script.write_text("echo unsafe")
            with self.assertRaises(ValueError):
                validate_executable(str(script))
            with self.assertRaises(ValueError):
                prepare_command({"executable_path": sys.executable, "arguments": []}, str(root / "missing.inp"), str(root))
            with self.assertRaises(ValueError):
                prepare_command({"executable_path": sys.executable, "arguments": ["{shell}"]}, "", "", allow_empty_input=True)

    def test_direct_can_open_program_without_an_input_or_working_directory(self) -> None:
        command, workdir = prepare_command({"executable_path": sys.executable, "arguments": ["--version"]}, "", "", allow_empty_input=True)
        self.assertEqual(command[0], str(Path(sys.executable).resolve()))
        self.assertEqual(workdir, str(Path(sys.executable).resolve().parent))


if __name__ == "__main__":
    unittest.main()
