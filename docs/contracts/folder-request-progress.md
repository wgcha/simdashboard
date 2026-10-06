# 계약: 폴더 등록 의뢰의 진척 단계 (폴더 상태 자동 계산)

- 상태: 확정 (2026-10-04)
- 대상: 폴더 탐색(깊이 스키마)으로 등록된 의뢰. 작업 유형·작업 항목(`request_work_items`)이 있는 기존 의뢰는 기존 화면을 유지한다.

## 1. 결정

| # | 결정 |
|---|---|
| P1 | 진척은 사람이 입력하지 않고 SPDM 폴더 상태와 앱 DB에서 계산한다(A안) |
| P2 | 표시 단위는 의뢰. Case별 목록은 표시하지 않는다 |
| P3 | 해석 모델링은 Scene 폴더 **바로 아래**의 대표 입력 파일로 판정한다. 확장자 `.rad`(Radioss), `.fem`(OptiStruct), 대소문자 무시. include 등 다른 파일은 보지 않는다 |
| P4 | Final은 대표 Case 1개만 지정한다. 의뢰 완료 = Final 지정 + 그 Final의 보고서 저장 |
| P5 | 파일은 이름·확장자만 본다. 본문은 읽지 않고 SPDM에 쓰지 않는다 |

## 2. 단계

| # | key | 이름 | 완료 조건 | 진행 표시 |
|---|---|---|---|---|
| 1 | REGISTERED | 의뢰 등록 | 살아 있는(DELETED 아님) 폴더 등록이 있음 | – |
| 2 | MODELING | 해석 모델링 | 모든 Case가 입력 있는 Scene을 1개 이상 가짐 | `Case n/m 입력 있음` |
| 3 | RESULTS | 해석 결과 | 모든 Case의 Scene 결과 수집 완료(최신 수집이 COMPLETED이고 결과 자산 있음) | `Case n/m 결과 있음` |
| 4 | FINAL | Final 지정 | 의뢰의 Case 중 1개 이상이 Final 지정 완료(`Final/.finalizations/<id>/complete.json` 존재, 앱이 이미 읽는 방식) | – |
| 5 | REPORT | 보고서 | 4의 최신 Final 지정에 대해 `Final/Report/<Case>/<id>/`에 `.pptx` 또는 `.html`이 1개 이상 | – |

- Case = 의뢰의 `dashboard_cases`(Working 아래 SIMULATION_CASE). Case가 0개면 2·3은 `대기`.
- 상태: `DONE` / `IN_PROGRESS`(n>0, n<m) / `WAITING`. 앞 단계가 완료되지 않아도 뒤 단계 조건이 충족되면 DONE으로 표시한다(폴더 사실 그대로).
- 현재 단계 = 첫 번째 DONE이 아닌 단계. "현재 할 일" 문구:
  - MODELING: `입력 파일 대기 Case k개` (Case가 0개면 `Working에 Case 폴더를 추가하세요`)
  - RESULTS: `결과 대기 Case k개`
  - FINAL: `대표 Case를 Final로 지정하세요`
  - REPORT: `Final 보고서를 저장하세요`
  - 모두 완료: `완료`

## 3. API

`GET /api/projects/{project_id}/requests/{request_id}/folder-progress`

```json
{
  "applicable": true,
  "environment": "USAGE",
  "completed": 2, "total": 5,
  "current_key": "RESULTS",
  "next_action": "결과 대기 Case 1개",
  "steps": [{"key": "REGISTERED", "label": "의뢰 등록", "status": "DONE", "detail": null}, ...],
  "checked_at": "2026-10-04T00:41:00+00:00"
}
```

- `applicable=false`: 폴더 등록이 없는 의뢰(구 방식). 프론트는 기존 작업 항목 화면을 쓴다.
- 권한: Case 결과 조회와 같은 권한(`PROJECT_DATA_VIEW`).
- 계산 비용: Scene 경로는 저장된 폴더 스냅샷·등록 미리보기에서만 얻는다(실시간 트리 스캔 없음). 각 Scene 바로 아래 이름·확장자만 조회(재귀 없음). 저장된 Scene이 없으면 MODELING은 대기와 사유. Final 판정은 기존 finalization 읽기 함수를 재사용. 결과는 의뢰별 30초 메모(폴더 동기화 주기와 같음).
- SPDM root가 없거나 폴더를 읽을 수 없으면 해당 단계 `detail`에 사유를 넣고 `WAITING`.

## 4. 화면 (의뢰 개요)

- `applicable=true`이면 기존 단계 표시줄에 위 5단계를 같은 스타일로 그린다. 요약은 `n / 5 단계 완료`, "현재 할 일"은 `next_action`.
- 수동 시작·완료 버튼과 "작업 이어하기"는 숨기고, 현재 단계에 맞는 화면으로 가는 버튼 1개를 둔다: MODELING·RESULTS → Case 결과, FINAL·REPORT → Case 결과(Final 지정 패널은 결과 화면 헤더에 항상 있음), 완료 → Case 결과.
- 30초마다 다시 조회한다(화면이 보일 때만).

## 5. Verifier 기준

1. 사용·유통 실제 트리 fixture에서 단계 계산이 표와 일치(입력 없음 → MODELING 진행, `.rad`/`.fem` Scene 바로 아래 → DONE, 하위폴더 속 `.rad`는 무시, `.inc` 무시, 대소문자 무시)
2. 결과 수집 일부만 완료 → RESULTS `IN_PROGRESS`와 n/m
3. Final 지정 후 FINAL DONE, Report에 pptx/html 저장 후 REPORT DONE
4. 구 방식 의뢰 → `applicable=false`, 기존 화면 유지
5. 파일 본문 읽기 0, SPDM 쓰기 0(파일 수·mtime 불변)
6. 권한은 Case 결과 조회(`/api/dashboard/catalog`)와 동일
