# Case 비교 (W5)

근거: [개선 로드맵](../plans/improvement-roadmap.md) §4 W5, [Case 결과 흐름 재설계](../plans/case-results-workflow-redesign.md) §8.1. 해석자가 같은 의뢰·환경의 Case 여러 개를 나란히 보고 best를 골라 Final로 지정하는 화면.

## 위치와 선택
- Case 결과 화면의 보기 탭 `요약 · Scene 비교 · Case 비교 · 영상 · 소재·물성`(사용환경은 `요약 · Case 비교 · 소재·물성`). 헤더 버튼은 늘리지 않음(A안).
- 비교 대상: 최신 결과(`latest:<dashboard_case_id>`)가 있는 Case 중 2~4개. 기본은 4개 이하면 전부, 넘으면 저장된 결과 시각이 가장 늦은 4개. 5번째는 고를 수 없음.
- 기준 Case: 기본은 경로 표시줄의 Case(선택에 없으면 첫 열). 바꿀 수 있음.
- 유통환경은 경로 표시줄의 하중경우 · Run Case · Run Option과 표시 옵션의 Component · Basis · 엣지 · 라인을 그대로 쓴다. 다른 Case는 같은 **이름**의 경로를 찾고, 없으면 열 머리에 `Run Option … 없음`/`… 결과 없음`을 표시한다.

## 값의 출처 (요약과 같은 값)
- 백엔드 변경 없음. 각 Case마다 프런트의 typed client로 요약 탭과 같은 호출을 한다: 유통 `GET /api/dashboard/distribution/runs/{run}`(capture_id=최신 결과, 같은 Component·Basis·edge_keys·line_indices), 사용 `GET /api/dashboard/usage/cases/{case}`(capture_id=최신 결과). Case 4개면 호출 4번.
- 유통 값은 `distributionValues.ts`의 `sceneEnvelopes`(선택 엣지 envelope) — Case 보고서 요약 표(`buildDistributionSection`)도 같은 함수로 바꿈. 사용 값은 `usageEvaluations.ts`의 `usageEvaluationRows`·`usageCell` 그대로.
- 계산 모듈: `features/results/caseCompare.ts`(순수 함수), 화면: `CaseCompareView.tsx`.

## 표
- 유통: 행 = Scene 이름(모든 Case의 합집합, **정확히 같은 이름**끼리 맞춤 — 대소문자만 다른 이름은 W6 경고 대상이며 다른 행), 열 = Case. 칸 = 값·단위, 기준 대비 Δ(값과 %), 행별 최저값 `최저`(녹색), Case별 가장 높은 Scene `Case 최대`. 어떤 Case에 없는 Scene은 `없음`(주황)과 행 머리 `Case B에 없음`. W6 이름 경고가 있으면 Scene 이름 옆 아이콘. 마지막 행 `Case 최대 (Scene 전체)`에도 Δ·최저.
- 단위가 Case마다 다르면(값이 있는 Scene 기준, 단위 없음도 하나의 값) Δ를 계산하지 않고 경고를 표시. 현재 서버의 유통 값은 단위 미확인(null)이라 보통 `단위 미확인`으로 같게 취급.
- 사용: 행 = 다섯 평가(Settle, Wobble, Horizontal_Force_Angle, Slope_Angle 값, Slope_Angle 판정, Slope_Angle_360), 열 = Case, 칸 = 공통/전방/후방 값과 상태. OK 녹색, NG 빨강.

## Final 지정 연결
- 열마다 `이 Case를 Final 지정`. 누르면 표 아래에 `Final 지정 대상 · <Case>` 줄과 그 Case의 `CaseFinalizationPanel`이 나타나고 Final 창이 바로 열린다(기존 창·W3 재지정 확인 그대로). 패널에는 열기 요청 prop `openRequest`만 추가(내부 흐름 불변). 권한 없으면 버튼 비활성.

## 보고서 "후보 Case 비교"
- 보고서 창에 `Case 비교 포함` 체크(기본 꺼짐). Case 비교 탭에서 표가 완성된 상태로 창을 열 때만 활성. 비교 표는 창을 열 때 범위와 함께 고정된다.
- 켜면 PPTX 끝에 `후보 Case 비교` 표 슬라이드(12행 단위 분할), HTML 끝에 같은 표 구역을 추가. 꺼져 있으면 기존 출력과 같다(e2e로 HTML 앞부분 일치·슬라이드 수 확인). 레이아웃 편집 내용은 유지하고 비교 슬라이드만 붙였다 뗀다.
- Final 지정 보고서(`finalReports.ts`)에는 넣지 않음.

## 검증
- `e2e/case-compare.spec.ts` 7건(값·요청이 요약과 동일, Δ·최저·없음·Case 최대, 단위 불일치 경고, 사용 5개 중 최신 4개·OK/NG, 열 버튼으로 해당 Case Final 창, 보고서 포함/미포함, 2560×1440 dsf 1.5 14·18pt 가로 스크롤 없음).

## 한계
- 한 Case 안에 같은 이름 Scene이 둘이면 첫 번째만 쓴다.
- Case별 경로 매칭은 이름 기준이라 Run Option 이름이 다르면 비교되지 않는다(열 머리에 사유 표시).
