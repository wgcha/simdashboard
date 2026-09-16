from __future__ import annotations

from typing import Any, TypedDict


class SelectionContext(TypedDict):
    id: str
    name: str


class SelectionMetadata(TypedDict):
    """Additive, display-only context for entity selection controls.

    IDs remain on the enclosing resource and are the only values clients may
    submit.  Registry data is limited to the entity-creation row for that ID.
    """

    code: str | None
    name: str
    project: SelectionContext | None
    request: SelectionContext | None
    analysis_type: str | None
    relative_path: str | None


def attach_selection_metadata(
    item: dict[str, Any],
    *,
    name_key: str,
    project: SelectionContext | None = None,
    request: SelectionContext | None = None,
    analysis_type: str | None = None,
) -> dict[str, Any]:
    """Project internal query aliases into the public selection shape."""

    code = item.pop("selection_code", None)
    relative_path = item.pop("selection_relative_path", None)
    item["selection_metadata"] = {
        "code": str(code) if code is not None else None,
        "name": str(item[name_key]),
        "project": project,
        "request": request,
        "analysis_type": analysis_type,
        "relative_path": str(relative_path) if relative_path is not None else None,
    }
    return item
