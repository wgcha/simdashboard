from __future__ import annotations

import errno
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import ImportBundleLimits, ImportSnapshotSettings, import_snapshot_settings
from app.services.bundle_snapshot import BundleSnapshotError, capture_bundle
from app.services.import_snapshot_workspace import (
    ImportSnapshotWorkspace,
    ImportSnapshotWorkspaceError,
    _reject_overlap,
    prepare_import_snapshot_workspace,
)


pytestmark = pytest.mark.unit


def _limits() -> ImportBundleLimits:
    return ImportBundleLimits(
        max_manifest_bytes=4096,
        max_mapping_count=8,
        max_file_bytes=4096,
        max_total_bytes=8192,
        max_structured_bytes=4096,
    )


def _source(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    (source / "summary.json").write_text(
        '[{"variable_key":"peak","data_type":"FLOAT","value":1}]', encoding="utf-8"
    )
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "schema_id": "workspace-test",
                "version": 1,
                "context": {"project_id": "p", "request_id": "r", "load_case_id": "l"},
                "mappings": [{"kind": "typed_scalars", "path": "summary.json"}],
            }
        ),
        encoding="utf-8",
    )
    return source


def _settings(root: Path, *, stale_seconds: int = 60) -> ImportSnapshotSettings:
    return ImportSnapshotSettings(
        root=root,
        reserve_bytes=8192,
        min_free_bytes=0,
        stale_seconds=stale_seconds,
    )


def test_workspace_is_secure_and_temp_is_inside_root(tmp_path: Path) -> None:
    import_root = tmp_path / "import"
    import_root.mkdir()
    workspace = prepare_import_snapshot_workspace(
        import_root, settings=_settings(tmp_path / "workspace"), limits=_limits()
    )
    assert stat.S_IMODE(os.stat(workspace.root).st_mode) == 0o700
    temporary = workspace.create_temporary_directory()
    try:
        path = Path(temporary.name)
        assert path.parent == workspace.root
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
    finally:
        temporary.cleanup()


def test_concurrent_first_prepare_same_root_is_safe(tmp_path: Path) -> None:
    import_root = tmp_path / "import"
    import_root.mkdir()
    workspace_root = tmp_path / "workspace"

    def prepare() -> Path:
        workspace = prepare_import_snapshot_workspace(
            import_root,
            settings=_settings(workspace_root),
            limits=_limits(),
        )
        return workspace.root

    with ThreadPoolExecutor(max_workers=2) as executor:
        roots = list(executor.map(lambda _item: prepare(), (1, 2)))
    assert roots == [workspace_root, workspace_root]
    assert stat.S_IMODE(os.stat(workspace_root).st_mode) == 0o700


def test_symlink_resolution_failure_is_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import_root = tmp_path / "import"
    import_root.mkdir()
    workspace_root = tmp_path / "workspace"

    def loop(_path: Path, *args: object, **kwargs: object) -> Path:
        del args, kwargs
        raise RuntimeError("symlink loop")

    monkeypatch.setattr(Path, "resolve", loop)
    with pytest.raises(ImportSnapshotWorkspaceError) as error:
        _reject_overlap(workspace_root, import_root)
    assert error.value.code == "BUNDLE_SNAPSHOT_WORKSPACE_UNSAFE"


def test_root_lstat_race_is_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import_root = tmp_path / "import"
    import_root.mkdir()
    workspace_root = tmp_path / "workspace"
    real_lstat = os.lstat

    def disappears(path: Path | str) -> os.stat_result:
        if Path(path) == workspace_root:
            raise FileNotFoundError(path)
        return real_lstat(path)

    monkeypatch.setattr("app.services.import_snapshot_workspace.os.lstat", disappears)
    with pytest.raises(ImportSnapshotWorkspaceError) as error:
        prepare_import_snapshot_workspace(import_root, settings=_settings(workspace_root), limits=_limits())
    assert error.value.code == "BUNDLE_SNAPSHOT_WORKSPACE_UNSAFE"


def test_prune_revalidates_mode_drift(tmp_path: Path) -> None:
    import_root = tmp_path / "import"
    import_root.mkdir()
    workspace_root = tmp_path / "workspace"
    workspace = prepare_import_snapshot_workspace(
        import_root, settings=_settings(workspace_root), limits=_limits()
    )
    os.chmod(workspace_root, 0o755)
    with pytest.raises(ImportSnapshotWorkspaceError) as error:
        workspace.prune_stale()
    assert error.value.code == "BUNDLE_SNAPSHOT_WORKSPACE_UNSAFE"


@pytest.mark.parametrize("layout", ["same", "workspace-inside-import", "import-inside-workspace"])
def test_workspace_rejects_overlap(tmp_path: Path, layout: str) -> None:
    import_root = tmp_path / "import"
    import_root.mkdir()
    if layout == "same":
        workspace_root = import_root
    elif layout == "workspace-inside-import":
        workspace_root = import_root / "snapshots"
    else:
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        import_root = workspace_root / "import"
        import_root.mkdir()
    with pytest.raises(ImportSnapshotWorkspaceError) as error:
        prepare_import_snapshot_workspace(import_root, settings=_settings(workspace_root), limits=_limits())
    assert error.value.code == "BUNDLE_SNAPSHOT_WORKSPACE_OVERLAP"


def test_stale_pruning_only_removes_owned_prefix_directories(tmp_path: Path) -> None:
    import_root = tmp_path / "import"
    import_root.mkdir()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(mode=0o700)
    old = workspace_root / "simdashboard-result-bundle-old"
    old.mkdir()
    os.utime(old, (0, 0))
    valid_old = workspace_root / "simdashboard-result-bundle-abcdefgh"
    valid_old.mkdir()
    os.utime(valid_old, (0, 0))
    prefix_similar = workspace_root / "simdashboard-result-bundle-abcdefghi"
    prefix_similar.mkdir()
    os.utime(prefix_similar, (0, 0))
    unknown = workspace_root / "unknown"
    unknown.mkdir()
    os.utime(unknown, (0, 0))
    regular_file = workspace_root / "simdashboard-result-bundle-file"
    regular_file.write_text("keep", encoding="utf-8")
    os.utime(regular_file, (0, 0))
    workspace = prepare_import_snapshot_workspace(
        import_root,
        settings=_settings(workspace_root, stale_seconds=60),
        limits=_limits(),
        clock=lambda: 1000,
    )
    assert old.exists()
    assert valid_old.exists()
    workspace.prune_stale()
    assert not valid_old.exists()
    assert prefix_similar.exists()
    assert unknown.exists()
    assert regular_file.exists()
    assert workspace.root == workspace_root


def test_capacity_is_checked_after_manifest_parse_and_attaches_manifest(tmp_path: Path) -> None:
    source = _source(tmp_path)
    import_root = tmp_path / "import"
    import_root.mkdir()
    workspace = prepare_import_snapshot_workspace(
        import_root,
        settings=_settings(tmp_path / "workspace"),
        limits=_limits(),
        disk_usage=lambda _path: SimpleNamespace(free=8191),
    )
    with pytest.raises(BundleSnapshotError) as error:
        capture_bundle(import_root=source, manifest_relative_path="manifest.json", limits=_limits(), workspace=workspace)
    assert error.value.code == "BUNDLE_SNAPSHOT_RESERVE_UNAVAILABLE"
    assert error.value.manifest is not None
    assert error.value.manifest["schema_id"] == "workspace-test"


def test_enospc_during_mapping_snapshot_maps_to_reserve_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path)
    import_root = tmp_path / "import"
    import_root.mkdir()
    workspace = prepare_import_snapshot_workspace(
        import_root,
        settings=_settings(tmp_path / "workspace"),
        limits=_limits(),
        disk_usage=lambda _path: SimpleNamespace(free=8192),
    )
    real_open = Path.open

    def fail_mapping_write(path: Path, *args: object, **kwargs: object):
        mode = args[0] if args else kwargs.get("mode", "r")
        if "x" in str(mode) and path.name == "summary.json":
            raise OSError(errno.ENOSPC, "no space")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_mapping_write)
    with pytest.raises(BundleSnapshotError) as error:
        capture_bundle(source, "manifest.json", _limits(), workspace=workspace)
    assert error.value.code == "BUNDLE_SNAPSHOT_RESERVE_UNAVAILABLE"


def test_workspace_symlink_is_rejected(tmp_path: Path) -> None:
    import_root = tmp_path / "import"
    import_root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "workspace-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    with pytest.raises(ImportSnapshotWorkspaceError) as error:
        prepare_import_snapshot_workspace(import_root, settings=_settings(link), limits=_limits())
    assert error.value.code == "BUNDLE_SNAPSHOT_WORKSPACE_UNSAFE"


def test_snapshot_settings_require_reserve_at_least_bundle_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMDASH_IMPORT_MAX_TOTAL_BYTES", "4096")
    monkeypatch.setenv("SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES", "1024")
    with pytest.raises(RuntimeError):
        import_snapshot_settings(_limits())
