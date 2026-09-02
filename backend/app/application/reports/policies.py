from __future__ import annotations

from typing import Any

from ...domains.reports.models import InvalidReportLayoutError


_HEX_CHARACTERS = frozenset("0123456789abcdefABCDEF")
_DESIGNS = frozenset({"plain", "frame", "header-band", "split"})


def validate_report_layout(definition: dict[str, Any]) -> dict[str, Any]:
    if definition.get("coverVariant") not in {"balanced", "executive", "evidence"}:
        _invalid("지원하지 않는 표지 형식입니다.")
    sections = definition.get("sectionOrder")
    if (
        not isinstance(sections, list)
        or len(sections) != 3
        or set(sections) != {"series", "scalar", "media"}
    ):
        _invalid("근거 페이지 순서는 series, scalar, media를 한 번씩 포함해야 합니다.")
    _validate_hex(definition.get("accentColor"), "강조색은 6자리 HEX 색상이어야 합니다.")
    _validate_slide_master(definition.get("slideMaster"))
    _validate_placements(definition.get("variablePlacements"))
    _validate_slides(definition.get("slides"))
    if definition.get("templateSource", "native") not in {"native", "pptx_upload"}:
        _invalid("지원하지 않는 템플릿 원본 형식입니다.")
    if definition.get("templateSource") == "pptx_upload" and not definition.get(
        "templateAssetId"
    ):
        _invalid("업로드 PPTX 템플릿 ID가 필요합니다.")
    if not isinstance(definition.get("templateBindings", {}), dict):
        _invalid("templateBindings는 객체여야 합니다.")
    return definition


def _validate_slide_master(slide_master: object) -> None:
    if slide_master is None:
        return
    if not isinstance(slide_master, dict) or slide_master.get("design") not in _DESIGNS:
        _invalid("슬라이드 마스터에는 올바른 디자인이 필요합니다.")
    for color_key in ("backgroundColor", "accentColor"):
        _validate_hex(
            slide_master.get(color_key),
            f"슬라이드 마스터 {color_key}은 6자리 HEX 색상이어야 합니다.",
        )


def _validate_placements(placements: object) -> None:
    if not isinstance(placements, list):
        _invalid("variablePlacements 배열이 필요합니다.")
    for placement in placements:
        if (
            not isinstance(placement, dict)
            or not placement.get("variableKey")
            or placement.get("presentation") not in {"chart", "table", "both"}
        ):
            _invalid("변수 배치에는 variableKey와 올바른 presentation이 필요합니다.")


def _validate_slides(slides: object) -> None:
    if slides is None:
        return
    if not isinstance(slides, list) or not slides:
        _invalid("slides는 1개 이상의 배열이어야 합니다.")
    slide_ids: set[str] = set()
    for slide in slides:
        if (
            not isinstance(slide, dict)
            or not slide.get("id")
            or slide.get("kind") not in {"cover", "series", "scalar", "media", "custom"}
        ):
            _invalid("각 슬라이드에는 고유 id와 올바른 kind가 필요합니다.")
        if slide["id"] in slide_ids:
            _invalid("슬라이드 id는 중복될 수 없습니다.")
        slide_ids.add(slide["id"])
        _validate_slide_style(slide.get("style"))
        _validate_elements(slide.get("elements"))


def _validate_slide_style(slide_style: object) -> None:
    if slide_style is None:
        return
    if not isinstance(slide_style, dict) or not isinstance(slide_style.get("useMaster"), bool):
        _invalid("슬라이드 스타일에는 useMaster 불리언 값이 필요합니다.")
    if slide_style.get("design") is not None and slide_style["design"] not in _DESIGNS:
        _invalid("지원하지 않는 슬라이드 디자인입니다.")
    for color_key in ("backgroundColor", "accentColor"):
        if slide_style.get(color_key) is None:
            continue
        _validate_hex(
            slide_style[color_key],
            f"슬라이드 {color_key}은 6자리 HEX 색상이어야 합니다.",
            empty_if_falsy=False,
        )


def _validate_elements(elements: object) -> None:
    if not isinstance(elements, list) or len(elements) > 80:
        _invalid("슬라이드 elements는 80개 이하의 배열이어야 합니다.")
    element_ids: set[str] = set()
    for element in elements:
        if (
            not isinstance(element, dict)
            or element.get("type")
            not in {"title", "text", "verdict", "scalar-card", "chart", "table", "image"}
        ):
            _invalid("지원하지 않는 보고서 위젯 형식입니다.")
        if not element.get("id") or element["id"] in element_ids:
            _invalid("슬라이드 안의 위젯 id는 고유해야 합니다.")
        element_ids.add(element["id"])
        if element.get("text") is not None and not isinstance(element["text"], str):
            _invalid("텍스트 상자 내용은 문자열이어야 합니다.")
        for key, limit in (("x", 32), ("w", 32), ("y", 18), ("h", 18)):
            value = element.get(key)
            if not isinstance(value, (int, float)) or value < 0 or value > limit:
                _invalid(f"위젯 {key} 좌표가 캔버스 범위를 벗어났습니다.")
        if (
            element["w"] <= 0
            or element["h"] <= 0
            or element["x"] + element["w"] > 32
            or element["y"] + element["h"] > 18
        ):
            _invalid("위젯 영역이 슬라이드 경계를 벗어났습니다.")


def _validate_hex(value: object, message: str, *, empty_if_falsy: bool = True) -> None:
    color = str((value or "") if empty_if_falsy else value)
    if len(color) != 6 or any(character not in _HEX_CHARACTERS for character in color):
        _invalid(message)


def _invalid(message: str) -> None:
    raise InvalidReportLayoutError(message)
