from __future__ import annotations

import io
import re
import stat
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

from ...domains.reports.template_models import (
    InvalidReportTemplateError,
    ReportTemplateInspection,
)


PPTX_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
PPTX_TOKEN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
PPTX_SHAPE_TAG = re.compile(r"^(VAR|TEXT|CHART|IMAGE):\s*(.+)$", re.IGNORECASE)
PPTX_MAX_BYTES = 25 * 1024 * 1024
PPTX_MAX_ARCHIVE_ENTRIES = 2500
PPTX_MAX_UNCOMPRESSED_BYTES = 80 * 1024 * 1024


class PptxTemplateDocumentProcessor:
    def inspect(self, data: bytes) -> ReportTemplateInspection:
        with _safe_pptx_archive(data) as archive:
            try:
                presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
                size = presentation.find("p:sldSz", PPTX_NS)
                slide_width = int(size.attrib.get("cx", "12192000")) if size is not None else 12192000
                slide_height = int(size.attrib.get("cy", "6858000")) if size is not None else 6858000
                if slide_width <= 0 or slide_height <= 0:
                    raise ValueError("slide dimensions must be positive")
                slide_names = sorted(
                    (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                    key=_slide_number,
                )
                placeholders: list[dict[str, Any]] = []
                for slide_index, slide_name in enumerate(slide_names, start=1):
                    root = ET.fromstring(archive.read(slide_name))
                    for shape_index, shape in enumerate(root.findall(".//p:sp", PPTX_NS), start=1):
                        placeholder = _shape_placeholder(shape)
                        if not placeholder:
                            continue
                        name_node = shape.find("./p:nvSpPr/p:cNvPr", PPTX_NS)
                        shape_name = name_node.attrib.get("name", f"Shape {shape_index}") if name_node is not None else f"Shape {shape_index}"
                        transform = shape.find("./p:spPr/a:xfrm", PPTX_NS)
                        offset = transform.find("a:off", PPTX_NS) if transform is not None else None
                        extent = transform.find("a:ext", PPTX_NS) if transform is not None else None
                        x = int(offset.attrib.get("x", "0")) if offset is not None else 0
                        y = int(offset.attrib.get("y", "0")) if offset is not None else 0
                        w = int(extent.attrib.get("cx", str(slide_width))) if extent is not None else slide_width
                        h = int(extent.attrib.get("cy", str(slide_height))) if extent is not None else slide_height
                        kind, token = placeholder
                        placeholders.append({
                            "id": f"slide-{slide_index}-shape-{shape_index}", "slideIndex": slide_index,
                            "shapeName": shape_name, "token": token, "kind": kind,
                            "x": x / slide_width, "y": y / slide_height,
                            "w": w / slide_width, "h": h / slide_height,
                        })
            except (ET.ParseError, KeyError, ValueError) as exc:
                raise InvalidReportTemplateError("올바른 PPTX 압축 구조가 아닙니다.") from exc
        return {
            "slide_width": slide_width,
            "slide_height": slide_height,
            "slide_count": len(slide_names),
            "placeholders": placeholders,
        }

    def render(self, data: bytes, replacements: dict[str, str]) -> bytes:
        source = _safe_pptx_archive(data)
        output_buffer = io.BytesIO()
        try:
            with source, zipfile.ZipFile(output_buffer, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for item in source.infolist():
                    content = source.read(item.filename)
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", item.filename):
                        root = ET.fromstring(content)
                        changed = False
                        for shape in root.findall(".//p:sp", PPTX_NS):
                            key = _replacement_key(shape)
                            if not key:
                                continue
                            value = str(replacements.get(key, replacements.get(key.split(":", 1)[-1], "")))
                            text_nodes = shape.findall(".//a:t", PPTX_NS)
                            if not text_nodes:
                                continue
                            combined = "".join(node.text or "" for node in text_nodes)
                            token_matches = list(PPTX_TOKEN.finditer(combined))
                            if token_matches:
                                for match in reversed(token_matches):
                                    raw = match.group(1).strip()
                                    normalized = raw if ":" in raw else f"text:{raw}"
                                    replacement = str(replacements.get(normalized, replacements.get(raw, "")))
                                    combined = combined[:match.start()] + replacement + combined[match.end():]
                                text_nodes[0].text = combined
                            else:
                                text_nodes[0].text = value
                            for node in text_nodes[1:]:
                                node.text = ""
                            changed = True
                        if changed:
                            content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                    output.writestr(item, content)
        except ET.ParseError as exc:
            raise InvalidReportTemplateError("올바른 PPTX 압축 구조가 아닙니다.") from exc
        return output_buffer.getvalue()


def _safe_pptx_archive(data: bytes) -> zipfile.ZipFile:
    if len(data) > PPTX_MAX_BYTES:
        raise InvalidReportTemplateError("PPTX 템플릿은 25MB 이하여야 합니다.", status_code=413)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, ValueError) as exc:
        raise InvalidReportTemplateError("올바른 PPTX 압축 구조가 아닙니다.") from exc
    infos = archive.infolist()
    if (
        len(infos) > PPTX_MAX_ARCHIVE_ENTRIES
        or sum(item.file_size for item in infos) > PPTX_MAX_UNCOMPRESSED_BYTES
    ):
        archive.close()
        raise InvalidReportTemplateError("PPTX 압축 해제 크기 또는 파일 개수가 허용 범위를 초과합니다.")
    names = {item.filename.replace("\\", "/") for item in infos}
    if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
        archive.close()
        raise InvalidReportTemplateError("PowerPoint 프레젠테이션 필수 파일이 없습니다.")
    for item in infos:
        name = item.filename.replace("\\", "/")
        parts = name.split("/")
        lowered = name.lower()
        if stat.S_ISLNK(item.external_attr >> 16) or name.startswith("/") or ".." in parts or lowered.endswith("vbaproject.bin") or "/embeddings/" in lowered or "oleobject" in lowered:
            archive.close()
            raise InvalidReportTemplateError("매크로, OLE 또는 안전하지 않은 경로가 포함된 PPTX는 사용할 수 없습니다.")
        if lowered.endswith(".rels"):
            try:
                relations = ET.fromstring(archive.read(item.filename))
            except ET.ParseError as exc:
                archive.close()
                raise InvalidReportTemplateError("올바른 PPTX 압축 구조가 아닙니다.") from exc
            if any(
                attribute.lower() == "targetmode" and value.lower() == "external"
                for relation in relations.iter()
                for attribute, value in relation.attrib.items()
            ):
                archive.close()
                raise InvalidReportTemplateError("외부 링크 관계가 포함된 PPTX는 사용할 수 없습니다.")
    return archive


def _slide_number(name: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", name)
    return int(match.group(1)) if match else 0


def _shape_placeholder(shape: ET.Element) -> tuple[str, str] | None:
    name_node = shape.find("./p:nvSpPr/p:cNvPr", PPTX_NS)
    shape_name = name_node.attrib.get("name", "") if name_node is not None else ""
    name_match = PPTX_SHAPE_TAG.match(shape_name)
    if name_match:
        prefix = name_match.group(1).lower()
        token = name_match.group(2).strip()
        kind = {"var": "variable", "text": "field", "chart": "chart", "image": "image"}[prefix]
        return kind, f"{kind}:{token}"
    text = "".join(node.text or "" for node in shape.findall(".//a:t", PPTX_NS))
    token_match = PPTX_TOKEN.search(text)
    if not token_match:
        return None
    token = token_match.group(1).strip()
    prefix, separator, _ = token.partition(":")
    kind = prefix.lower() if separator and prefix.lower() in {"variable", "field", "text", "chart", "image"} else "text"
    return kind, token if separator else f"text:{token}"


def _replacement_key(shape: ET.Element) -> str | None:
    placeholder = _shape_placeholder(shape)
    return placeholder[1] if placeholder else None
