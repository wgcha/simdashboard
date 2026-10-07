"""Static check: direct filesystem calls live only in the storage provider (docs/contracts/storage-provider.md §3.1).

Every module under ``backend/app`` is scanned.  Providers are obtained only through the storage
package factory (``provider_for_root`` / ``get_storage_provider``); constructing a provider class
directly outside the package fails (scx-drive plan D1).  Outside ``app/services/storage`` a call of a
filesystem primitive fails the test unless the module is listed as non-SPDM (S5: app data,
upload temp, backups, import root, deployment) or the exact call is an explicit non-SPDM use.
"""
from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

APP = Path(__file__).resolve().parents[1] / "app"
PROVIDER_PACKAGE = "services/storage/"

# Attribute calls that touch the filesystem (Path methods, os/shutil/tempfile helpers).
FS_ATTRIBUTES = frozenset({
    "scandir", "walk", "listdir", "iterdir", "glob", "rglob", "open", "stat", "lstat",
    "write_text", "write_bytes", "read_text", "read_bytes", "mkdir", "makedirs", "rmdir",
    "rename", "unlink", "link", "symlink", "exists", "lexists", "is_dir", "is_file",
    "is_symlink", "resolve", "realpath", "samefile", "fsync", "rmtree", "copyfile", "copy2",
    "touch", "chmod",
})
# ``replace`` is a filesystem call as ``os.replace(a, b)`` / ``Path.replace(target)`` (one argument);
# ``str.replace(old, new)`` takes two.
BUILTIN_FS = frozenset({"open"})

# S5: modules whose file access never targets the SPDM root.
NON_SPDM_MODULES = {
    "adapters/documents/pptx_templates.py": "packaged report templates",
    "adapters/filesystem/drop_videos.py": "app-managed drop video store",
    "adapters/persistence/result_ingestion.py": "media files of an import-root result bundle",
    "adapters/storage/report_template_files.py": "app data report template files",
    "config.py": "configuration/.env files",
    "database.py": "local DuckDB files and app data",
    "database_connection.py": "local DuckDB files",
    "folder_import.py": "SIMDASH_IMPORT_ROOT result bundles",
    "main.py": "static frontend bundle and app data",
    "media_policy.py": "app media store",
    "parsers/manifest_format.py": "import-root manifests",
    "parsers/radioss_deck_parser.py": "line streams handed over by callers",
    "parsers/streaming_json.py": "import-root files",
    "parsers/scalar_result_parser.py": "import-root files",
    "parsers/open_cell_parser.py": "import-root files",
    "parsers/generic_time_history_parser.py": "import-root files",
    "parsers/chassis_rear_parser.py": "import-root files",
    "radioss_csv.py": "import-root files",
    "routers/media.py": "packaged sample assets",
    "routers/result_ingestion.py": "packaged sample files",
    "security.py": "auth key files in app data",
    "services/batch_execution.py": "local runner workspace",
    "services/bundle_snapshot.py": "SIMDASH_IMPORT_ROOT snapshot capture",
    "services/demo_runner.py": "demo assets",
    "services/drop_video_demo.py": "demo assets",
    "services/drive/config.py": "SCX worker runtime/CA bundle files (scx mode settings)",
    "services/drive/gateway.py": "SCX worker work dir and dashboard.lock (scx mode)",
    "services/drive/check.py": "SCX drive gateway calls and the server staging folder (drive check)",
    "services/drive/reads.py": "SCX drive gateway calls (stat/list_dir/download_to); local files via storage.server_local",
    "routers/drive.py": "SCX drive gateway calls (admin connection test)",
    "services/import_snapshot_workspace.py": "service-owned snapshot workspace",
    "services/local_helper_distribution.py": "packaged local helper",
    "services/managed_local_execution.py": "local runner",
    "services/master_result_refresh.py": "SIMDASH_IMPORT_ROOT discovery",
    "services/media_http.py": "app media store",
    "services/media_integrity.py": "app media store",
    "services/media_storage_service.py": "app media store",
    "services/result_bundle_publisher.py": "SIMDASH_IMPORT_ROOT publication",
    "services/result_import_execution_gate.py": "import-root execution gate",
    "services/semantic_sample_uploads.py": "temporary sample uploads (system temp folder)",
}

# Exact non-SPDM calls inside modules that otherwise use the provider: (module, call text) -> count.
NON_SPDM_CALLS = Counter({
    # In-memory PPTX/XLSX zip members of an uploaded report (no filesystem).
    ("services/case_finalization.py", "workbook.open"): 1,
    ("services/case_finalization.py", "archive.open"): 3,
})


# Calls on a storage provider object are the sanctioned path (``fs.is_dir(...)``).
PROVIDER_RECEIVER = re.compile(r"^(?:.*\.)?(?:fs|base_fs|provider)$|^provider_for_root\(|^get_storage_provider\(")


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id if func.id in BUILTIN_FS else None
    if not isinstance(func, ast.Attribute):
        return None
    attr = func.attr
    if attr == "remove":
        # os.remove only; list/set.remove are not filesystem calls.
        if not (isinstance(func.value, ast.Name) and func.value.id == "os"):
            return None
    elif attr == "replace":
        is_os = isinstance(func.value, ast.Name) and func.value.id == "os"
        # Path.replace(target) has one positional argument; str/datetime.replace do not.
        if not is_os and (len(node.args) != 1 or node.keywords):
            return None
    elif attr not in FS_ATTRIBUTES:
        return None
    if PROVIDER_RECEIVER.search(ast.unparse(func.value)):
        return None
    return ast.unparse(func)


def _violations() -> tuple[list[str], Counter]:
    found: list[str] = []
    used = Counter()
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(APP).as_posix()
        if relative.startswith(PROVIDER_PACKAGE) or relative in NON_SPDM_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name is None:
                continue
            key = (relative, name)
            if used[key] < NON_SPDM_CALLS.get(key, 0):
                used[key] += 1
                continue
            found.append(f"{relative}:{node.lineno}: {name}(...)")
    return found, used


def test_spdm_filesystem_access_goes_through_the_storage_provider():
    found, _used = _violations()
    assert found == [], "direct filesystem calls outside the storage provider:\n" + "\n".join(found)


def test_non_spdm_exception_list_is_exact():
    _found, used = _violations()
    stale = {key: count for key, count in NON_SPDM_CALLS.items() if used[key] != count}
    assert stale == {}, f"exception list no longer matches the code: {stale}"
    missing = [module for module in NON_SPDM_MODULES if not (APP / module).is_file()]
    assert missing == [], f"non-SPDM module list names missing files: {missing}"


def test_spdm_storage_no_longer_carries_filesystem_helpers():
    from app.services import spdm_storage

    moved = ("_is_reparse", "_assert_safe_existing", "_case_collision", "open_stable_reader",
             "read_stable_bytes", "_publish_no_replace", "_assert_raw_root_path")
    assert [name for name in moved if hasattr(spdm_storage, name)] == []


PROVIDER_CLASSES = frozenset({"LocalFsProvider"})


def _direct_provider_constructions() -> list[str]:
    found: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(APP).as_posix()
        if relative.startswith(PROVIDER_PACKAGE):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name in PROVIDER_CLASSES:
                found.append(f"{relative}:{node.lineno}: {ast.unparse(func)}(...)")
    return found


def test_providers_are_constructed_only_inside_the_storage_package():
    found = _direct_provider_constructions()
    assert found == [], "construct providers through app.services.storage.provider_for_root:\n" + "\n".join(found)


def test_provider_factory_override_reaches_every_entry_point(tmp_path):
    from app.services import folder_discovery_scan, storage
    from app.services.storage import factory

    made: list[Path] = []

    class Marker(storage.LocalFsProvider):
        def __init__(self, root):
            made.append(Path(root))
            super().__init__(root)

    assert type(storage.provider_for_root(tmp_path)) is storage.LocalFsProvider
    with storage.override_provider_factory(Marker):
        assert isinstance(storage.provider_for_root(tmp_path), Marker)
        folder_discovery_scan.root_identity(tmp_path)
    assert made == [tmp_path, tmp_path]
    assert factory._factory is None
    assert type(storage.provider_for_root(tmp_path)) is storage.LocalFsProvider
