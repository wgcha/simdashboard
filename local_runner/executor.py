"""Safe, local process execution for the local-runner service.

The executor intentionally accepts a program record rather than a command line.
Only a registered executable and its tokenized arguments are ever passed to
``Popen``; it never delegates command construction to a shell.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Protocol, Sequence


class RunUpdater(Protocol):
    """Storage operations the asynchronous executor needs."""

    def mark_started(self, run_id: str) -> None: ...

    def mark_finished(self, run_id: str, *, status: str, exit_code: int | None, error: str | None = None) -> None: ...


def validate_executable(path_value: str) -> Path:
    """Return a usable executable path, rejecting shell-script entrypoints."""

    path = Path(path_value).expanduser()
    if not path.is_file():
        raise ValueError("The registered executable no longer exists.")
    suffix = path.suffix.lower()
    if suffix in {".bat", ".cmd", ".ps1", ".com", ".sh"}:
        raise ValueError("Shell and script files cannot be run by the local helper.")
    if os.name == "nt":
        if suffix != ".exe":
            raise ValueError("Windows local programs must be .exe files.")
    elif not os.access(path, os.X_OK):
        raise ValueError("The selected file is not executable.")
    return path.resolve()


def prepare_command(
    program: dict[str, Any], input_path: str, working_directory: str, *, allow_empty_input: bool = False
) -> tuple[list[str], str]:
    """Validate paths and substitute only the documented ``{input}`` token."""

    executable = validate_executable(str(program["executable_path"]))
    input_file = Path(input_path).expanduser() if input_path else None
    workdir = Path(working_directory).expanduser() if working_directory else executable.parent
    if input_file is None and not allow_empty_input:
        raise ValueError("An input file is required for a batch run.")
    if input_file is not None and not input_file.is_file():
        raise ValueError("The input path does not exist or is not a file.")
    if not workdir.is_dir():
        raise ValueError("The working directory does not exist or is not a directory.")

    arguments = program.get("arguments") or []
    if not allow_empty_input and not any("{input}" in str(argument) for argument in arguments):
        raise ValueError("파일별 일괄 실행에는 {input}을 포함한 실행 인자가 필요합니다. 프로그램 설정을 수정하세요.")
    command = [str(executable)]
    for argument in arguments:
        token = str(argument)
        if "{" in token or "}" in token:
            remaining = token.replace("{input}", "")
            if "{input}" not in token or "{" in remaining or "}" in remaining:
                raise ValueError("Only the {input} placeholder is allowed in program arguments.")
        if "{input}" in token:
            if input_file is None:
                raise ValueError("This program's arguments require an input file.")
            token = token.replace("{input}", str(input_file.resolve()))
        command.append(token)
    return command, str(workdir.resolve())


class RunExecutor:
    """Starts runs in daemon threads and persists their terminal transitions."""

    def __init__(self, updater: RunUpdater, *, max_processes: int = 1):
        self._updater = updater
        self._process_slots = threading.BoundedSemaphore(max_processes)

    def start_direct(self, run: dict[str, Any], program: dict[str, Any]) -> None:
        self._start_thread(run, program, direct=True)

    def start_batch(self, runs: Sequence[dict[str, Any]], program: dict[str, Any]) -> None:
        thread = threading.Thread(
            target=self._run_batch,
            args=(list(runs), program),
            daemon=True,
            name="local-runner-batch",
        )
        thread.start()

    def _start_thread(self, run: dict[str, Any], program: dict[str, Any], *, direct: bool) -> None:
        thread = threading.Thread(
            target=self._run_one,
            args=(run, program, direct),
            daemon=True,
            name=f"local-runner-{run['id']}",
        )
        thread.start()

    def _run_batch(self, runs: list[dict[str, Any]], program: dict[str, Any]) -> None:
        for run in runs:
            self._run_one(run, program, direct=False)

    def _run_one(self, run: dict[str, Any], program: dict[str, Any], direct: bool) -> None:
        run_id = str(run["id"])
        try:
            command, cwd = prepare_command(
                program,
                str(run["input_path"] or ""),
                str(run["working_directory"] or ""),
                allow_empty_input=direct,
            )
            # Queued runs wait here, limiting the process queue and preserving
            # predictable local resource use across batches.
            with self._process_slots:
                self._updater.mark_started(run_id)
                completed = subprocess.run(command, cwd=cwd, shell=False, check=False)
            if completed.returncode != 0:
                self._updater.mark_finished(run_id, status="FAILED", exit_code=completed.returncode)
            elif direct:
                self._updater.mark_finished(run_id, status="AWAITING_COMPLETION", exit_code=0)
            else:
                self._updater.mark_finished(run_id, status="SUCCEEDED", exit_code=0)
        except (OSError, ValueError) as exc:
            self._updater.mark_finished(run_id, status="FAILED", exit_code=None, error=str(exc))
