"""Canonical, request-scoped Folder Schema location projection."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ..database_connection import ConnectionLike


def final_branch_paths(schema: dict[str, Any]) -> list[str]:
    """Find request-level Final branches, including legacy role overlays."""
    request_path = str(schema.get("request_relative_path") or "").strip("/")
    request_key = request_path.casefold()
    roots = set(str(path) for path in schema.get("_final_paths", []) if path)
    for node in schema.get("nodes", []):
        path = str(node.get("relative_path") or "").strip("/")
        parent = str(node.get("parent_path") or "").strip("/")
        name = str(node.get("name") or PurePosixPath(path).name)
        is_request_child = parent.casefold() == request_key
        if path and (str(node.get("role_kind") or "").upper() == "FINAL" or
                     (is_request_child and name.casefold() == "final")):
            roots.add(path)
    return sorted(roots, key=str.casefold)


def is_final_branch(schema: dict[str, Any], relative_path: str) -> bool:
    """Whether a relative path is inside a request's Final branch."""
    from . import folder_schema_resolver as resolver

    path = resolver._normal(str(relative_path))
    return any(resolver._is_ancestor(root, path) for root in final_branch_paths(schema))


def blocked_paths_for_case(schema: dict[str, Any], case_relative_path: str) -> list[str]:
    """Return excluded, unresolved, and conflicting subtrees within one Case."""
    from . import folder_schema_resolver as resolver

    return sorted({
        str(node.get("relative_path") or "")
        for node in schema.get("nodes", [])
        if resolver._is_ancestor(case_relative_path, str(node.get("relative_path") or ""))
        and (node.get("status") in {"EXCLUDED", "UNRESOLVED"}
             or node.get("role_basis") == "CONFLICT")
        and node.get("relative_path")
    }, key=str.casefold)


@dataclass(frozen=True)
class EnvironmentLocations:
    schema: dict[str, Any]
    locations: tuple[dict[str, Any], ...]

    @property
    def project_id(self) -> str:
        return str(self.schema["project_id"])

    @property
    def request_id(self) -> str:
        return str(self.schema["request_id"])

    @property
    def environment(self) -> str:
        return str(self.schema["environment"])

    @property
    def request_relative_path(self) -> str:
        return str(self.schema["request_relative_path"])

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return self.schema["nodes"]

    @property
    def snapshot_id(self) -> str | None:
        return str((self.schema.get("scan") or {}).get("id") or "") or None

    @property
    def scan_id(self) -> str | None:
        return self.snapshot_id

    @property
    def profile_id(self) -> str | None:
        profile = self.schema.get("profile") or {}
        scan = self.schema.get("scan") or {}
        return str(profile.get("id") or scan.get("profile_id") or "") or None

    @property
    def profile_revision(self) -> int | None:
        profile = self.schema.get("profile") or {}
        scan = self.schema.get("scan") or {}
        value = profile.get("revision", scan.get("profile_revision"))
        return int(value) if value is not None else None

    @property
    def structure_fingerprint(self) -> str:
        return str(self.schema.get("structure_fingerprint") or "")

    @property
    def content_fingerprint(self) -> str:
        return str(self.schema.get("content_fingerprint") or "")

    def as_dict(self) -> dict[str, Any]:
        locations = []
        for item in self.locations:
            projected = dict(item)
            projected.setdefault("label", str(projected.get("name") or ""))
            locations.append(projected)
        return {**self.schema, "locations": locations,
                "folder_schema_snapshot_id": self.snapshot_id,
                "scan_id": self.scan_id, "profile_id": self.profile_id,
                "profile_revision": self.profile_revision,
                "structure_fingerprint": self.structure_fingerprint,
                "content_fingerprint": self.content_fingerprint}


def resolve_request_locations(conn: ConnectionLike, project_id: str, request_id: str,
                              environment: str, *, schema: dict[str, Any] | None = None) -> EnvironmentLocations:
    """Project the confirmed Scene, Evaluation, Input and Results locations.

    Scene identity comes from its confirmed target ID when available. The path
    fallback is deterministic for older schema rows without a target ID.
    Input and Results paths are attached to their nearest confirmed Scene with
    a source and priority that describes the shared Folder Schema relationship.
    A Scene is also its own input/result location: SPDM commonly places the
    solver files and result tables directly in that folder.
    """
    from . import folder_discovery, folder_discovery_environment
    from . import folder_schema_resolver as resolver

    environment = str(environment).upper()
    if schema is None:
        root = folder_discovery.configured_root(conn)
        root_key = folder_discovery_environment.root_identity(root)
        schema = resolver.resolve_request_schema(conn, root, root_key, project_id, request_id, environment)
    if (str(schema.get("project_id")) != project_id or str(schema.get("request_id")) != request_id
            or str(schema.get("environment")) != environment):
        raise resolver.FolderSchemaError("FOLDER_SCHEMA_SCOPE_MISMATCH", "폴더 위치 문맥이 선택한 의뢰와 일치하지 않습니다.")
    final_paths = final_branch_paths(schema)
    if final_paths:
        schema = {**schema, "_final_paths": final_paths,
                  "nodes": [node for node in schema.get("nodes", [])
                            if not any(resolver._is_ancestor(final, str(node.get("relative_path") or ""))
                                       for final in final_paths)],
                  "confirmed_roles": {
                      key: value for key, value in (schema.get("confirmed_roles") or {}).items()
                      if not any(resolver._is_ancestor(final, str(value.get("relative_path") or ""))
                                 for final in final_paths)
                  }}
    root_key = folder_discovery_environment.root_identity(folder_discovery.configured_root(conn))
    confirmed_nodes = [node for node in schema.get("nodes", [])
                       if node.get("status") in {"CONFIRMED", "LINKED"}]
    scenes: list[dict[str, Any]] = []
    other_locations: list[dict[str, Any]] = []
    for node in confirmed_nodes:
        role = str(node.get("role_kind") or "")
        if role not in {"SCENE", "EVALUATION", "INPUT", "RESULTS"}:
            continue
        relative_path = resolver._normal(str(node.get("relative_path") or ""))
        target_id = str(node.get("target_id") or "") or None
        location_id = target_id or folder_discovery_environment.stable(
            "folder-location-" + role.casefold(), root_key, relative_path, role,
        )
        item = {
            "id": location_id,
            "location_id": location_id,
            "target_id": target_id or location_id,
            "role_kind": role,
            "relative_path": relative_path,
            "parent_path": str(node.get("parent_path") or ""),
            "name": str(node.get("name") or PurePosixPath(relative_path).name),
            "label": str(node.get("name") or PurePosixPath(relative_path).name),
            "status": str(node.get("status")),
            "role_basis": node.get("role_basis"),
            "hierarchy": {key: dict(value) for key, value in (node.get("hierarchy") or {}).items()},
        }
        if role == "SCENE":
            item["scene_id"] = target_id or location_id
            item["input_paths"] = []
            item["result_paths"] = []
            item["input_paths"].append({"relative_path": relative_path, "source": "SCENE",
                                        "priority": 0, "location_id": location_id,
                                        "role_kind": "SCENE"})
            item["result_paths"].append({"relative_path": relative_path, "source": "SCENE",
                                         "priority": 0, "location_id": location_id,
                                         "role_kind": "SCENE"})
            scenes.append(item)
        else:
            other_locations.append(item)

    for scene in scenes:
        scene_hierarchy = scene.get("hierarchy") or {}
        for location in other_locations:
            hierarchy = location.get("hierarchy") or {}
            relation = None
            scene_context = hierarchy.get("scene")
            option_context = hierarchy.get("run_option")
            execution_context = hierarchy.get("execution_run")
            if isinstance(scene_context, dict) and str(scene_context.get("relative_path", "")).casefold() != scene["relative_path"].casefold():
                continue
            if isinstance(scene_context, dict):
                relation = ("SCENE", 0)
            elif (isinstance(scene_hierarchy.get("run_option"), dict)
                  and isinstance(option_context, dict)
                  and str(scene_hierarchy["run_option"].get("relative_path", "")).casefold()
                  == str(option_context.get("relative_path", "")).casefold()):
                relation = ("RUN_OPTION", 1)
            elif (option_context is None and isinstance(scene_hierarchy.get("execution_run"), dict)
                  and isinstance(execution_context, dict)
                  and str(scene_hierarchy["execution_run"].get("relative_path", "")).casefold()
                  == str(execution_context.get("relative_path", "")).casefold()):
                relation = ("EXECUTION_RUN", 2)
            if relation is None:
                continue
            role = str(location["role_kind"])
            key = "input_paths" if role == "INPUT" else "result_paths"
            scene[key].append({"relative_path": location["relative_path"],
                               "source": relation[0], "priority": relation[1],
                               "location_id": location["location_id"],
                               "role_kind": role})
        for key in ("input_paths", "result_paths"):
            scene[key].sort(key=lambda item: (item["priority"], item["relative_path"].casefold()))

    locations = scenes + other_locations
    locations.sort(key=lambda item: (str(item["relative_path"]).casefold(), str(item["role_kind"])))
    return EnvironmentLocations(schema=schema, locations=tuple(locations))
