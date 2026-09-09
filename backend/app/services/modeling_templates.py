"""Immutable, database-backed CSV model-template snapshots."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4


MAX_FILES = 200
MAX_TOTAL_BYTES = 25 * 1024 * 1024
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class TemplateError(ValueError):
    status_code = 422


class TemplateMissing(TemplateError):
    status_code = 404


class VersionConflict(TemplateError):
    status_code = 409


@dataclass(frozen=True)
class IncomingFile:
    relative_path: str
    content: bytes


def now() -> datetime:
    return datetime.now(timezone.utc)


def validate_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise TemplateError("relative_path가 올바르지 않습니다.")
    if "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise TemplateError("CSV 경로는 상대 POSIX 경로여야 합니다.")
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise TemplateError("CSV 경로에 빈 폴더 또는 상위 폴더를 사용할 수 없습니다.")
    for part in parts:
        stem = part.split(".", 1)[0].upper()
        if part[-1:] in {" ", "."} or stem in _WINDOWS_RESERVED or re.search(r'[<>:"|?*\x00-\x1f]', part):
            raise TemplateError("Windows에서 안전하지 않은 CSV 경로입니다.")
    normalized = "/".join(parts)
    if not normalized.lower().endswith(".csv"):
        raise TemplateError("CSV 파일만 저장할 수 있습니다.")
    return normalized


def _validate_snapshot(files: Iterable[IncomingFile]) -> list[IncomingFile]:
    normalized: list[IncomingFile] = []
    seen: set[str] = set()
    for item in files:
        path = validate_relative_path(item.relative_path)
        key = path.casefold()
        if key in seen:
            raise TemplateError("대소문자를 구분하지 않는 중복 CSV 경로가 있습니다.")
        seen.add(key)
        normalized.append(IncomingFile(path, item.content))
    for path in seen:
        parts = path.split("/")
        if any("/".join(parts[:index]) in seen for index in range(1, len(parts))):
            raise TemplateError("파일과 폴더 경로가 충돌합니다.")
    if len(normalized) > MAX_FILES:
        raise TemplateError(f"스냅샷은 최대 {MAX_FILES}개 파일만 포함할 수 있습니다.")
    if sum(len(item.content) for item in normalized) > MAX_TOTAL_BYTES:
        raise TemplateError("스냅샷 크기는 25 MiB를 초과할 수 없습니다.")
    return normalized


def _card(row: Any) -> dict[str, Any]:
    return dict(zip(("id", "name", "product_name", "load_case_name", "description", "latest_version", "created_at", "updated_at"), row))


def _files(conn: Any, template_id: str, version: int, *, content: bool = False) -> list[dict[str, Any]]:
    columns = "id, relative_path, size_bytes, checksum" + (", content" if content else "")
    result = []
    for row in conn.execute(f"SELECT {columns} FROM modeling_template_files WHERE template_id=? AND version=? ORDER BY lower(relative_path), relative_path", [template_id, version]).fetchall():
        keys = ["id", "relative_path", "size_bytes", "checksum"] + (["content"] if content else [])
        item = dict(zip(keys, row))
        if content:
            item["content"] = bytes(item["content"])
        result.append(item)
    return result


def _version(conn: Any, template_id: str, version: int) -> dict[str, Any]:
    row = conn.execute("SELECT version, created_at, file_count, total_bytes FROM modeling_template_versions WHERE template_id=? AND version=?", [template_id, version]).fetchone()
    if not row:
        raise TemplateMissing("템플릿 버전을 찾을 수 없습니다.")
    return dict(zip(("version", "created_at", "file_count", "total_bytes"), row))


def detail(conn: Any, template_id: str, version: int | None = None) -> dict[str, Any]:
    row = conn.execute("SELECT id, name, product_name, load_case_name, description, latest_version, created_at, updated_at FROM modeling_templates WHERE id=?", [template_id]).fetchone()
    if not row:
        raise TemplateMissing("모델링 템플릿을 찾을 수 없습니다.")
    card = _card(row)
    selected = int(card["latest_version"] if version is None else version)
    card["versions"] = [dict(zip(("version", "created_at", "file_count", "total_bytes"), item)) for item in conn.execute("SELECT version, created_at, file_count, total_bytes FROM modeling_template_versions WHERE template_id=? ORDER BY version DESC", [template_id]).fetchall()]
    card["files"] = _files(conn, template_id, selected)
    card["selected_version"] = _version(conn, template_id, selected)
    card["file_count"] = card["selected_version"]["file_count"]
    card["total_bytes"] = card["selected_version"]["total_bytes"]
    return card


def list_cards(conn: Any, *, q: str | None, product_name: str | None, load_case_name: str | None) -> dict[str, Any]:
    terms, params = [], []
    if q and q.strip():
        escaped = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        terms.append("(lower(name) LIKE ? ESCAPE '\\' OR lower(product_name) LIKE ? ESCAPE '\\' OR lower(load_case_name) LIKE ? ESCAPE '\\')")
        params.extend([f"%{escaped}%"] * 3)
    if product_name and product_name.strip():
        terms.append("lower(product_name)=?"); params.append(product_name.strip().lower())
    if load_case_name and load_case_name.strip():
        terms.append("lower(load_case_name)=?"); params.append(load_case_name.strip().lower())
    where = " WHERE " + " AND ".join(terms) if terms else ""
    rows = conn.execute(
        "SELECT template.id, template.name, template.product_name, template.load_case_name, template.description, "
        "template.latest_version, template.created_at, template.updated_at, version.file_count, version.total_bytes "
        "FROM modeling_templates template JOIN modeling_template_versions version "
        "ON version.template_id=template.id AND version.version=template.latest_version" + where.replace("lower(name)", "lower(template.name)").replace("lower(product_name)", "lower(template.product_name)").replace("lower(load_case_name)", "lower(template.load_case_name)") +
        " ORDER BY lower(template.product_name), lower(template.load_case_name), lower(template.name)", params).fetchall()
    items = []
    for row in rows:
        item = _card(row[:8])
        item.update(file_count=row[8], total_bytes=row[9])
        items.append(item)
    products = [str(row[0]) for row in conn.execute("SELECT product_name FROM modeling_templates GROUP BY product_name ORDER BY lower(product_name)").fetchall()]
    load_cases = [str(row[0]) for row in conn.execute("SELECT load_case_name FROM modeling_templates GROUP BY load_case_name ORDER BY lower(load_case_name)").fetchall()]
    return {"items": items, "products": products, "load_cases": load_cases}


def create_card(conn: Any, *, name: str, product_name: str, load_case_name: str, description: str) -> dict[str, Any]:
    values = [value.strip() for value in (name, product_name, load_case_name)]
    if any(not value or len(value) > 160 for value in values) or len(description) > 2000:
        raise TemplateError("템플릿 이름, 제품명, 하중 경우명을 확인해 주세요.")
    template_id, created = f"model-template-{uuid4().hex}", now()
    conn.execute("INSERT INTO modeling_templates (id, name, product_name, load_case_name, description, latest_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)", [template_id, *values, description.strip(), created, created])
    conn.execute("INSERT INTO modeling_template_versions (template_id, version, created_at, file_count, total_bytes) VALUES (?, 1, ?, 0, 0)", [template_id, created])
    return detail(conn, template_id)


def create_card_atomic(conn: Any, **values: Any) -> dict[str, Any]:
    conn.execute("BEGIN TRANSACTION")
    try:
        result = create_card(conn, **values)
        conn.execute("COMMIT")
        return result
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def write_version(conn: Any, template_id: str, *, expected_version: int, mode: str, files: list[IncomingFile]) -> dict[str, Any]:
    if mode not in {"merge", "replace"}:
        raise TemplateError("mode는 merge 또는 replace여야 합니다.")
    card = conn.execute("SELECT latest_version FROM modeling_templates WHERE id=?", [template_id]).fetchone()
    if not card:
        raise TemplateMissing("모델링 템플릿을 찾을 수 없습니다.")
    current = int(card[0])
    if current != expected_version:
        raise VersionConflict("다른 사용자가 새 버전을 만들었습니다. 새로고침 후 다시 시도해 주세요.")
    submitted = _validate_snapshot(files)
    merged: dict[str, IncomingFile] = {}
    if mode == "merge":
        for item in _files(conn, template_id, current, content=True):
            merged[item["relative_path"].casefold()] = IncomingFile(item["relative_path"], item["content"])
    merged.update({item.relative_path.casefold(): item for item in submitted})
    snapshot = _validate_snapshot(merged.values())
    next_version, created = current + 1, now()
    updated = conn.execute("UPDATE modeling_templates SET latest_version=?, updated_at=? WHERE id=? AND latest_version=? RETURNING id", [next_version, created, template_id, current]).fetchone()
    if not updated:
        raise VersionConflict("다른 사용자가 새 버전을 만들었습니다. 새로고침 후 다시 시도해 주세요.")
    total = sum(len(item.content) for item in snapshot)
    conn.execute("INSERT INTO modeling_template_versions (template_id, version, created_at, file_count, total_bytes) VALUES (?, ?, ?, ?, ?)", [template_id, next_version, created, len(snapshot), total])
    for item in snapshot:
        conn.execute("INSERT INTO modeling_template_files (id, template_id, version, relative_path, size_bytes, checksum, content) VALUES (?, ?, ?, ?, ?, ?, ?)", [f"model-template-file-{uuid4().hex}", template_id, next_version, item.relative_path, len(item.content), hashlib.sha256(item.content).hexdigest(), item.content])
    return detail(conn, template_id)


def write_version_atomic(conn: Any, template_id: str, **values: Any) -> dict[str, Any]:
    conn.execute("BEGIN TRANSACTION")
    try:
        result = write_version(conn, template_id, **values)
        conn.execute("COMMIT")
        return result
    except BaseException as error:
        conn.execute("ROLLBACK")
        if "conflict" in str(error).lower() or "transaction" in str(error).lower():
            raise VersionConflict("다른 사용자가 새 버전을 만들었습니다. 새로고침 후 다시 시도해 주세요.") from error
        raise
