from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any


def normalize_command(command: str) -> str:
    """Apply the legacy preview matching normalization exactly."""

    return command.strip().replace(" ", "").lower()


def preview_command(command: str, *, epoch: Callable[[], float] | None = None) -> dict[str, Any]:
    """Translate an allowlisted natural-language request into a safe proposal.

    This policy does not access persistence or mutate a dashboard.  ``epoch`` is
    an optional seam for deterministic tests; production keeps the legacy
    second-based timestamp ID behavior.
    """

    compact = normalize_command(command)
    widget: dict[str, Any] | None = None
    message = ""
    now_epoch = epoch or (lambda: datetime.now().timestamp())

    if "응력" in compact and ("시간" in compact or "시계열" in compact) and "기준선" in compact and ("추가" in compact or "넣" in compact):
        return {
            "recognized": True,
            "message": "기존 응력-시간 위젯에 기준선을 표시합니다.",
            "proposal": {
                "action": "update_widgets",
                "updates": [{"widget_type": "time_series", "settings": {"showThreshold": True}}],
            },
        }
    if ("판정" in compact or "패스" in compact or "실패" in compact) and ("오른쪽" in compact or "우측" in compact) and ("이동" in compact or "옮" in compact):
        return {
            "recognized": True,
            "message": "패스/실패 판정 카드를 맨 위 오른쪽으로 이동합니다.",
            "proposal": {
                "action": "update_widgets",
                "updates": [{"widget_type": "verdict", "x": 9, "y": 0}],
            },
        }
    if "컨투어" in compact and "의견" in compact and ("나란히" in compact or "옆" in compact):
        return {
            "recognized": True,
            "message": "컨투어 이미지와 수행자 의견을 같은 행에 배치합니다.",
            "proposal": {
                "action": "update_widgets",
                "updates": [
                    {"widget_type": "contour", "x": 4, "y": 18, "w": 4},
                    {"widget_type": "note", "x": 8, "y": 18, "w": 4},
                ],
            },
        }
    if "응력" in compact and ("시간" in compact or "시계열" in compact) and ("추가" in compact or "만들" in compact):
        widget = {
            "id": f"time-series-{int(now_epoch())}",
            "type": "time_series",
            "title": "Open Cell 엣지 응력-시간",
            "x": 0,
            "y": 20,
            "w": 8,
            "h": 5,
            "settings": {"showThreshold": True},
        }
        message = "상하좌우 엣지 응력 시계열과 기준선을 표시하는 위젯을 추가합니다."
    elif ("최대응력" in compact or "상하좌우" in compact) and ("추가" in compact or "막대" in compact):
        widget = {
            "id": f"edge-bar-{int(now_epoch())}",
            "type": "edge_bar",
            "title": "상하좌우 엣지 최대 응력",
            "x": 0,
            "y": 20,
            "w": 6,
            "h": 4,
            "settings": {"failColor": "#ff5d73", "showThreshold": True},
        }
        message = "엣지별 최대 응력과 기준값을 비교하는 막대그래프를 추가합니다."
    elif "판정" in compact and ("추가" in compact or "카드" in compact):
        widget = {
            "id": f"verdict-{int(now_epoch())}",
            "type": "verdict",
            "title": "패스/실패 판정",
            "x": 0,
            "y": 20,
            "w": 3,
            "h": 2,
            "settings": {},
        }
        message = "기준값에 따른 전체 판정 카드를 추가합니다."
    else:
        return {
            "recognized": False,
            "message": "요청을 안전한 변경 명세로 변환하지 못했습니다. ‘응력-시간 그래프 추가’, ‘상하좌우 최대 응력 막대그래프 추가’, ‘판정 카드 추가’처럼 요청해 주세요.",
            "proposal": None,
        }

    return {"recognized": True, "message": message, "proposal": {"action": "add_widget", "widget": widget}}
