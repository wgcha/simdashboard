from __future__ import annotations

import base64
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import main as main_module
from app.adapters.documents import pptx_templates as pptx_templates_module
from app.adapters.documents.pptx_templates import PptxTemplateDocumentProcessor
from app.adapters.storage.report_template_files import REPORT_TEMPLATE_STORAGE_ROOT
from app.domains.reports.template_models import InvalidReportTemplateError
from app.adapters.http.routers import report_templates as report_templates_router
from app.database_connection import connect
from app.main import app


PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.mark.contract
def test_default_template_storage_root_preserves_backend_assets_contract() -> None:
    assert REPORT_TEMPLATE_STORAGE_ROOT == (
        Path(main_module.__file__).resolve().parents[1] / "assets" / "report-templates"
    )


def _pptx(
    *,
    slide_xml: bytes | None = None,
    extras: dict[str, bytes] | None = None,
    slide_size: tuple[int, int] = (12192000, 6858000),
) -> bytes:
    """Build the smallest useful OOXML archive for parser/HTTP tests."""
    presentation = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        + f'<p:sldSz cx="{slide_size[0]}" cy="{slide_size[1]}"/></p:presentation>'.encode()
    )
    slide = slide_xml or (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        b'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        b'<p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="2" name="VAR:stress"/>'
        b'<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/>'
        b'<a:ext cx="3657600" cy="914400"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/>'
        b'<a:lstStyle/><a:p><a:r><a:t>{{variable:stress}}</a:t></a:r></a:p></p:txBody>'
        b'</p:sp></p:spTree></p:cSld></p:sld>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/slides/slide1.xml", slide)
        for name, content in (extras or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _insert_template(template_id: str, file_path: str, *, active: bool = True) -> None:
    now = datetime(2026, 1, 2, 3, 4, 5)
    with connect() as connection:
        connection.execute(
            "INSERT INTO report_template_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                template_id,
                "Fixture template",
                "fixture.pptx",
                file_path,
                1,
                json.dumps({"slideWidth": 12192000, "slideHeight": 6858000, "placeholders": []}),
                active,
                now,
                now,
                "fixture",
            ],
        )


def _cleanup_template(template_id: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM report_template_assets WHERE id=?", [template_id])


@pytest.mark.contract
def test_report_template_router_has_exact_four_routes_and_stable_operation_ids() -> None:
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.endpoint.__module__ == report_templates_router.__name__
    ]
    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/report-templates", ("GET",)),
        ("/api/report-templates", ("POST",)),
        ("/api/report-templates/{template_id}/render", ("POST",)),
        ("/api/report-templates/{template_id}", ("DELETE",)),
    ]
    assert [route.endpoint.__name__ for route in routes] == [
        "list_report_templates",
        "upload_report_template",
        "render_report_template",
        "delete_report_template",
    ]
    assert [route.operation_id or route.unique_id for route in routes] == [
        "list_report_templates_api_report_templates_get",
        "upload_report_template_api_report_templates_post",
        "render_report_template_api_report_templates__template_id__render_post",
        "delete_report_template_api_report_templates__template_id__delete",
    ]


@pytest.mark.contract
def test_main_no_longer_owns_report_template_helpers_or_sql() -> None:
    source = Path(main_module.__file__).read_text(encoding="utf-8")
    for token in (
        "PPTX_NS",
        "PPTX_TOKEN",
        "PPTX_SHAPE_TAG",
        "REPORT_TEMPLATE_DIR",
        "_safe_pptx_archive",
        "_shape_placeholder",
        "_inspect_pptx",
        "_render_pptx_template",
        "_report_template_item",
        "def list_report_templates",
        "def upload_report_template",
        "def render_report_template",
        "def delete_report_template",
        "SELECT * FROM report_template_assets",
        "INSERT INTO report_template_assets",
        "UPDATE report_template_assets",
    ):
        assert token not in source


@pytest.mark.unit
def test_authorization_runs_before_repository_provider() -> None:
    events: list[str] = []

    class Provider:
        def __call__(self):
            events.append("open")
            raise AssertionError("provider must not open after authorization failure")

    def deny(*_args: Any, **_kwargs: Any) -> object:
        events.append("authorize")
        raise PermissionError("denied")

    from app.application.reports.template_queries import list_report_templates

    with pytest.raises(PermissionError, match="denied"):
        list_report_templates(deny, Provider())
    assert events == ["authorize"]


@pytest.mark.contract
@pytest.mark.duckdb_integration
def test_upload_list_render_delete_preserves_actor_mime_disposition_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed = tmp_path / "managed"
    from app.adapters.storage.report_template_files import ReportTemplateFileStore

    monkeypatch.setattr(report_templates_router, "ReportTemplateFileStore", lambda: ReportTemplateFileStore(managed))
    payload = {
        "name": "검증 템플릿",
        "filename": "nested/path/original.pptx",
        "content_base64": base64.b64encode(_pptx()).decode("ascii"),
        "updated_by": "위조된 사용자",
    }
    template_id: str | None = None
    try:
        with TestClient(app) as client:
            uploaded = client.post("/api/report-templates", json=payload)
            assert uploaded.status_code == 201, uploaded.text
            body = uploaded.json()
            template_id = body["id"]
            assert body["filename"] == "original.pptx"
            assert body["updated_by"] == "로컬 관리자"
            assert body["definition"]["placeholders"][0]["token"] == "variable:stress"

            listed = client.get("/api/report-templates")
            assert listed.status_code == 200
            assert listed.json()[0]["id"] == template_id
            assert "file_path" not in listed.json()[0]
            assert listed.json()[0]["updated_by"] == "로컬 관리자"

            rendered = client.post(
                f"/api/report-templates/{template_id}/render",
                json={"replacements": {"variable:stress": "72.50 MPa"}, "filename": "해석 결과.pptx"},
            )
            assert rendered.status_code == 200
            assert rendered.headers["content-type"].startswith(PPTX_MIME)
            assert 'attachment' in rendered.headers["content-disposition"]
            assert "filename=\"___.pptx\"" in rendered.headers["content-disposition"]
            assert "filename*=UTF-8''%ED%95%B4%EC%84%9D_%EA%B2%B0%EA%B3%BC.pptx" in rendered.headers["content-disposition"]
            with zipfile.ZipFile(io.BytesIO(rendered.content)) as archive:
                assert "72.50 MPa" in archive.read("ppt/slides/slide1.xml").decode("utf-8")

            deleted = client.delete(f"/api/report-templates/{template_id}")
            assert deleted.status_code == 200
            assert deleted.json() == {"status": "deactivated", "id": template_id}
            assert client.get("/api/report-templates").json() == []
        assert not any(managed.rglob("*.pptx"))
        with connect() as connection:
            audit = connection.execute(
                "SELECT action, user_id, detail_json FROM audit_events "
                "WHERE action='REPORT_TEMPLATE_UPLOADED' ORDER BY occurred_at DESC LIMIT 1"
            ).fetchone()
        assert audit is not None and audit[1] == "local-admin"
        assert json.loads(audit[2])["template_id"] == template_id
    finally:
        if template_id:
            _cleanup_template(template_id)


@pytest.mark.contract
@pytest.mark.duckdb_integration
def test_manage_authorization_precedes_malformed_upload_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*_args: Any, **_kwargs: Any) -> object:
        from fastapi import HTTPException

        raise HTTPException(403, "permission denied")

    monkeypatch.setattr(report_templates_router, "require_permission", deny)
    with TestClient(app) as client:
        response = client.post(
            "/api/report-templates",
            json={"name": "권한 템플릿", "filename": "bad.pptx", "content_base64": "not-base64"},
        )
    assert response.status_code == 403


@pytest.mark.contract
@pytest.mark.duckdb_integration
@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_upload_failure_rolls_back_and_removes_promoted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    managed = tmp_path / "managed"
    events: list[str] = []
    from app.application.reports.template_commands import create_report_template
    from app.domains.reports.template_models import ReportTemplateInspection
    from contextlib import contextmanager

    class Files:
        def promote_upload(self, path: str, data: bytes) -> None:
            events.append("promote")

        def remove_upload(self, path: str) -> None:
            events.append("remove")

        def relative_path(self, template_id: str) -> str:
            return f"report-templates/{template_id}.pptx"

    class Processor:
        def inspect(self, data: bytes) -> ReportTemplateInspection:
            events.append("inspect")
            return {"slide_width": 1, "slide_height": 1, "slide_count": 1, "placeholders": []}

    class Repository:
        def transaction(self):
            @contextmanager
            def tx():
                events.append("begin")
                yield
                if failure == "commit":
                    events.append("commit")
                    raise RuntimeError("commit failed")
                events.append("rollback")

            return tx()

        def insert_template(self, **kwargs: Any) -> None:
            events.append("insert")

        def add_upload_audit(self, audit: dict[str, Any]) -> None:
            events.append("audit")
            if failure == "audit":
                raise RuntimeError("audit failed")

    class Provider:
        def __call__(self):
            @contextmanager
            def provider():
                events.append("open")
                yield Repository()

            return provider()

    def authorize() -> object:
        events.append("authorize")
        return object()

    with pytest.raises(RuntimeError, match=failure + " failed"):
        create_report_template(
            name="실패 템플릿", filename="failure.pptx", data=_pptx(), actor_name="actor",
            audit={"method": "POST", "path": "/api/report-templates", "request_id": "r", "user_agent": ""},
            authorize=authorize, repository_provider=Provider(), files=Files(), processor=Processor(),
        )
    assert events[0] == "authorize"
    assert events[-1] == "remove"
    assert "open" in events and "begin" in events and "insert" in events and "audit" in events
    if failure == "commit":
        assert "commit" in events
    assert events.index("authorize") < events.index("open")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("extra_name", "extra_content"),
    [
        ("ppt/slides/_rels/slide1.xml.rels", b"<Relationship TargetMode='External' Target='https://example.test'/>") ,
        ("../escape.txt", b"escape"),
    ],
)
def test_pptx_safety_rejects_external_relationship_quote_variants_and_zip_paths(
    extra_name: str, extra_content: bytes
) -> None:
    with pytest.raises(InvalidReportTemplateError) as caught:
        PptxTemplateDocumentProcessor().inspect(_pptx(extras={extra_name: extra_content}))
    assert caught.value.status_code == 422


@pytest.mark.unit
def test_pptx_safety_rejects_malformed_xml() -> None:
    malformed = _pptx(slide_xml=b"<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'>")
    with pytest.raises(InvalidReportTemplateError) as caught:
        PptxTemplateDocumentProcessor().inspect(malformed)
    assert caught.value.status_code == 422


@pytest.mark.unit
def test_pptx_safety_rejects_zero_canvas_before_division() -> None:
    with pytest.raises(InvalidReportTemplateError) as caught:
        PptxTemplateDocumentProcessor().inspect(_pptx(slide_size=(0, 0)))
    assert caught.value.status_code == 422


@pytest.mark.unit
def test_pptx_limits_are_injectably_small(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pptx_templates_module, "PPTX_MAX_BYTES", 1)
    with pytest.raises(InvalidReportTemplateError) as caught:
        PptxTemplateDocumentProcessor().inspect(_pptx())
    assert caught.value.status_code == 413


@pytest.mark.contract
@pytest.mark.duckdb_integration
def test_render_missing_traversal_and_symlink_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    outside = tmp_path / "outside.pptx"
    outside.write_bytes(_pptx())
    from app.adapters.storage.report_template_files import ReportTemplateFileStore

    monkeypatch.setattr(report_templates_router, "ReportTemplateFileStore", lambda: ReportTemplateFileStore(managed))
    cases = [("report-template-aaaaaaaaaaaa", "../outside.pptx")]
    linked = managed / "report-template-bbbbbbbbbbbb.pptx"
    try:
        linked.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    cases.append(("report-template-bbbbbbbbbbbb", "report-templates/report-template-bbbbbbbbbbbb.pptx"))
    for template_id, file_path in cases:
        _insert_template(template_id, file_path)
    try:
        with TestClient(app) as client:
            assert client.post("/api/report-templates/no-such-template/render", json={}).status_code == 404
            for template_id in (item[0] for item in cases):
                response = client.post(f"/api/report-templates/{template_id}/render", json={})
                assert response.status_code == 410
    finally:
        for template_id, _ in cases:
            _cleanup_template(template_id)


@pytest.mark.contract
@pytest.mark.duckdb_integration
def test_delete_unsafe_managed_path_does_not_deactivate_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.adapters.storage.report_template_files import ReportTemplateFileStore

    managed = tmp_path / "managed"
    monkeypatch.setattr(report_templates_router, "ReportTemplateFileStore", lambda: ReportTemplateFileStore(managed))
    template_id = "report-template-cccccccccccc"
    _insert_template(template_id, "../outside.pptx")
    try:
        with TestClient(app) as client:
            response = client.delete(f"/api/report-templates/{template_id}")
        assert response.status_code == 410
        with connect() as connection:
            assert connection.execute("SELECT is_active FROM report_template_assets WHERE id=?", [template_id]).fetchone() == (True,)
    finally:
        _cleanup_template(template_id)


@pytest.mark.contract
@pytest.mark.duckdb_integration
def test_delete_missing_file_still_deactivates_and_delete_missing_id_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed = tmp_path / "managed"
    from app.adapters.storage.report_template_files import ReportTemplateFileStore

    monkeypatch.setattr(report_templates_router, "ReportTemplateFileStore", lambda: ReportTemplateFileStore(managed))
    template_id = "report-template-dddddddddddd"
    _insert_template(template_id, "report-templates/report-template-dddddddddddd.pptx")
    try:
        with TestClient(app) as client:
            assert client.delete(f"/api/report-templates/{template_id}").status_code == 200
            assert client.delete(f"/api/report-templates/{template_id}").status_code == 404
            assert client.delete("/api/report-templates/no-such-template").status_code == 404
        with connect() as connection:
            assert connection.execute("SELECT is_active FROM report_template_assets WHERE id=?", [template_id]).fetchone() == (False,)
    finally:
        _cleanup_template(template_id)


@pytest.mark.unit
def test_delete_command_restores_quarantine_on_database_failure_and_purge_is_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.application.reports.template_commands import deactivate_report_template

    events: list[str] = []

    class Files:
        def quarantine_for_delete(self, path: str) -> object:
            events.append("quarantine")
            return path

        def restore_quarantine(self, value: object | None) -> None:
            events.append("restore")

        def purge_quarantine(self, value: object | None) -> None:
            events.append("purge")
            raise OSError("purge failed")

    class Repository:
        def get_active_template(self, template_id: str):
            return {"id": template_id, "file_path": "report-templates/x.pptx"}

        def transaction(self):
            from contextlib import contextmanager

            @contextmanager
            def tx():
                events.append("begin")
                yield
                events.append("commit")

            return tx()

        def deactivate(self, template_id: str, occurred_at: datetime) -> None:
            events.append("deactivate")
            raise RuntimeError("database failed")

    class Provider:
        def __call__(self):
            from contextlib import nullcontext

            return nullcontext(Repository())

    with pytest.raises(RuntimeError, match="database failed"):
        deactivate_report_template(
            template_id="x", authorize=lambda: object(), repository_provider=Provider(), files=Files()
        )
    assert events == ["quarantine", "begin", "deactivate", "restore"]

    from app.adapters.storage.report_template_files import ReportTemplateFileStore, _QuarantinedTemplate

    store = ReportTemplateFileStore(tmp_path)
    monkeypatch.setattr(
        ReportTemplateFileStore,
        "_unlink_if_regular",
        staticmethod(lambda _path: (_ for _ in ()).throw(OSError("purge failed"))),
    )
    quarantine = _QuarantinedTemplate(tmp_path / "source.pptx", tmp_path / "quarantine.pptx")
    # Purge occurs after the DB commit; a transient unlink failure must not
    # turn the already-successful deactivation into an unretryable 500.
    store.purge_quarantine(quarantine)
