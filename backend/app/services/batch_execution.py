from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from string import Formatter
from typing import Any


ALLOWED_TEMPLATE_FIELDS = {"input", "cores", "request_id"}
SHELL_CONTROL_CHARACTERS = set(";&|<>`")


@dataclass(frozen=True)
class BatchPreflightResult:
    command_preview: str
    working_directory_preview: str


class BatchPreflightError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _template_fields(value: str) -> set[str]:
    try:
        return {field for _, field, _, _ in Formatter().parse(value) if field}
    except ValueError as exc:
        raise BatchPreflightError("BATCH_TEMPLATE_INVALID", "배치 템플릿의 중괄호 형식이 올바르지 않습니다.") from exc


def _validate_template(value: str, *, label: str) -> None:
    fields = _template_fields(value)
    unsupported = sorted(fields - ALLOWED_TEMPLATE_FIELDS)
    if unsupported:
        raise BatchPreflightError(
            "BATCH_TEMPLATE_FIELD_UNSUPPORTED",
            f"{label}에 지원하지 않는 자리표시자가 있습니다: {', '.join(unsupported)}",
        )
    if any(character in SHELL_CONTROL_CHARACTERS for character in value):
        raise BatchPreflightError(
            "BATCH_SHELL_CONTROL_REJECTED",
            f"{label}에는 셸 제어 문자(;, &, |, <, >, `)를 사용할 수 없습니다.",
        )


def _is_absolute_local_path(value: str) -> bool:
    return PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()


def _validate_local_path(value: str, *, label: str) -> None:
    normalized = value.replace("\\", "/")
    if value.startswith(("\\\\", "//")) or value.startswith("\\\\?\\"):
        raise BatchPreflightError("BATCH_REMOTE_PATH_REJECTED", f"{label}에는 UNC 또는 device 경로를 사용할 수 없습니다.")
    if ".." in normalized.split("/"):
        raise BatchPreflightError("BATCH_PATH_TRAVERSAL_REJECTED", f"{label}에는 상위 경로(..)를 사용할 수 없습니다.")
    if not _is_absolute_local_path(value):
        raise BatchPreflightError("BATCH_PATH_NOT_ABSOLUTE", f"{label}은 절대 경로여야 합니다.")


def validate_profile_definition(profile: dict[str, Any]) -> None:
    _validate_template(profile["solver_path"], label="Solver 실행 파일")
    _validate_template(profile["working_directory"], label="Working directory")
    _validate_template(profile["arguments_template"], label="Arguments template")
    solver_preview = profile["solver_path"].format(input="input.deck", cores="1", request_id="request")
    workdir_preview = profile["working_directory"].format(input="input.deck", cores="1", request_id="request")
    _validate_local_path(solver_preview, label="Solver 실행 파일")
    _validate_local_path(workdir_preview, label="Working directory")


def preflight_batch_profile(profile: dict[str, Any], work_item: dict[str, Any]) -> BatchPreflightResult:
    validate_profile_definition(profile)
    task_type_id = str(work_item.get("task_type_id") or "")
    task_type_version = int(work_item.get("task_type_version") or 1)
    profile_task_type_id = str(profile.get("task_type_id") or "")
    profile_task_type_version = int(profile.get("task_type_version") or 1)
    if (profile_task_type_id, profile_task_type_version) != (task_type_id, task_type_version):
        raise BatchPreflightError(
            "BATCH_PROFILE_TASK_MISMATCH",
            f"{profile['name']} 프로필은 {task_type_id} v{task_type_version} 작업 유형과 호환되지 않습니다.",
        )
    values = {
        "input": "<input>",
        "cores": "<cores>",
        "request_id": str(work_item["request_id"]),
    }
    solver = profile["solver_path"].format_map(values)
    arguments = profile["arguments_template"].format_map(values).strip()
    working_directory = profile["working_directory"].format_map(values)
    return BatchPreflightResult(
        command_preview=f'"{solver}" {arguments}'.strip(),
        working_directory_preview=working_directory,
    )
