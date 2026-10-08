# Case 결과 보고서 (PPTX · HTML)

Case 결과 화면 헤더의 `보고서` 버튼으로 현재 선택한 Case 결과를 PPTX와 HTML 파일로 내려받는다. 파일은 브라우저에서 만들며 백엔드 변경은 없다. PDF는 사용자가 PPTX·HTML로 직접 만든다(2026-10-02 23:58 결정). 웹에는 PDF 변환·인쇄용 스타일 등 PDF 관련 구현을 두지 않는다. 계획 근거는 [Case 결과 흐름 재설계](../plans/case-results-workflow-redesign.md) §3.3·§3.6과 결정 기록 23:51·23:56·23:58이다.

## 범위 고정

- 버튼은 유통환경에서 결과가 있는 Run Case·Run Option·Component·Basis가 정해졌을 때, 사용환경에서 결과가 있는 Case가 정해졌을 때 켜진다(Reference를 고른 경우 Case와 결과가 모두 정해져야 한다). 소재·물성 탭에서는 꺼진다.
- 사용환경 범위는 `ReportSource` 분기 `case_usage`(Case, `latest:<Case>`, 선택한 Reference Case·결과)다.
- 창을 여는 순간의 범위를 복사한다: 프로젝트·의뢰, Case(대시보드 Case ID), 병합 최신 결과 `latest:<Case>`, 하중경우, Run Case, Run Option(mode), Component, Basis, 표시 엣지·라인. 창을 연 뒤 화면 선택이 바뀌어도 열린 보고서의 범위·자료는 바뀌지 않는다(`ReportSource` 분기 `case_results`).
- 자료는 창을 열 때 한 번 읽어 레시피를 만든다. 두 형식 모두 같은 레시피로 그린다.

## 레시피 (`CaseReportData`)

| 항목 | 내용 |
|---|---|
| 제목 | `<Case> 해석 결과 보고서`, 생성 일시(`YYYY.MM.DD HH:mm`, 창을 연 시각) |
| 범위 | 프로젝트, 의뢰, Case, 하중경우, Run Case, Run Option, Component · 기준, 생성 일시 |
| 요약 | 요약 탭과 같은 값: Scene별 선택 엣지 최대응력(단위 포함, 단위 없으면 `단위 미확인`)과 Scene별 최대 엣지, 전체 최대값과 Scene |
| Scene 비교 | Scene 순번·이름·자세·충돌, TOP/BOTTOM/LEFT/RIGHT 최대응력 |
| 그래프 | 화면의 그래프 전부(아래 [그래프](#그래프-2026-10-08)) |
| 결과 이미지 | 준비된 컨투어·거동 이미지. 애니메이션 컨투어(VIDEO)는 영상을 넣지 않을 때 첫 프레임 이미지 |
| 영상 | Run Option의 모든 Scene 영상(`/videos` 전체 페이지)과 애니메이션 컨투어(같은 자산은 컨투어로 한 번만) |

레시피는 구역(`sections`) 하나 이상으로 이뤄진다. 화면 선택 보고서와 사용환경 보고서는 구역 하나(제목 없음, 5단계와 같은 콘텐츠 ID)이고, Final 지정의 유통환경 보고서는 Run Case · Run Option마다 구역 하나다([Final 지정](case-finalization.md)).

### 사용환경 레시피

| 항목 | 내용 |
|---|---|
| 제목 | `<Case> 사용환경 해석 결과 보고서`, 생성 일시 |
| 범위 | 프로젝트, 의뢰, Case, 환경(사용환경), Reference(고른 경우), 생성 일시 |
| 다섯 평가 종합 | 사용환경 화면과 같은 표: Settle, Wobble, Horizontal_Force_Angle, Slope_Angle(값과 OK/NG 판정 두 행), Slope_Angle_360 × 원문 키·공통·전방·후방(값·단위·상태, OK/NG), Reference를 고르면 Reference 열. 행·키·값·상태 문구는 화면과 같은 함수(`features/results/usageEvaluations.ts`)로 만든다 |
| 그래프 | 평가별 값 막대그래프(Slope_Angle_360 제외, 공통·전방·후방 × 현재 Case·Reference), 화면과 같은 값(값 상태 READY만) |
| 결과 이미지·영상 | 각 평가(Scene)의 `media` 중 준비된 이미지와 영상 |

## 형식

- 형식 선택: PPTX, HTML 체크박스. 하나 이상 골라야 다운로드가 켜진다. 둘 다 고르면 두 파일을 받는다.
- 파일 이름: `<Case>_<YYYYMMDD-HHmm>.pptx|.html`. Windows 금지 문자·제어 문자는 `_`로 바꾸고, 공백은 `_`, 끝의 점·공백 제거, 80자 제한, 예약 이름(CON, NUL, COM1 등)은 앞에 `_`를 붙인다.
- PPTX: 기존 pptxgenjs 렌더러(`reportExport.ts`)와 회사 레이아웃(`/api/report-layouts`), 레이아웃 편집 창을 재사용한다. 표지(제목·작성일·범위·요약 최대값) 다음에 요약 표, 요약 그래프, Scene 비교 표(12행 단위로 슬라이드 분할), 엣지별 수준·Scene 상세 그래프, 이미지 1장당 슬라이드 1장, (영상 포함 시) 영상 1개당 슬라이드 1장, 영상 목록 표가 이어진다. 넣지 못한 이미지·영상은 그 슬라이드에 파일 이름과 사유를 표시한다. 업로드 PPTX 템플릿 바인딩은 이 보고서 형식을 지원하지 않아 렌더링할 때만 화면 레이아웃으로 바꾼다(`caseReportRenderLayout`). 편집 중인 정의에는 `templateSource`·`templateAssetId`·`templateBindings`를 그대로 둔다.
- 레이아웃 편집(보고서 창): **새 레이아웃으로 저장**만 제공한다. 현재 레이아웃(시스템 레이아웃 포함)의 새 버전 저장과 삭제는 이 창에서 할 수 없고, 저장한 사본에는 템플릿 연결이 남는다. 업로드 템플릿 선택·업로드·출력 원본 선택은 숨기고, 템플릿이 연결된 레이아웃이면 적용되지 않는다는 안내를 보인다. 템플릿 관리는 기존 보고서 화면에서 한다.
- HTML: 파일 하나로 완결된다. CSS는 문서 안에, 이미지·영상은 data URI로 넣는다. 스크립트를 쓰지 않고, `Content-Security-Policy`(`default-src 'none'; img-src data:; media-src data:; style-src 'unsafe-inline'`)로 외부 참조와 스크립트 실행을 막는다. 업로드한 HTML은 SPDM에 저장만 하고 앱이 페이지로 제공하지 않는다(6단계).

## 보고서 A안 (W7, 2026-10-06)

- **템플릿 미적용 안내:** 고른 레이아웃이 업로드 PPTX 템플릿에 연결돼 있으면(`templateSource: 'pptx_upload'`) 레이아웃 선택 바로 옆에 "업로드 PPTX 템플릿은 Case 보고서에 적용되지 않습니다…"를 항상 보인다(편집 창을 열지 않아도). 보고서 창과 Final 지정 창 모두 같다(`CaseReportFields.tsx`의 `TemplateNotAppliedNotice`).
- **마지막 사용 레이아웃:** 보고서 창에서 레이아웃을 고르거나 PPTX를 받으면, Final 지정 창에서 Final 보고서를 만들면 그 레이아웃 ID를 브라우저 `localStorage`에 사용자·의뢰별로 기억한다(키 `vdsim.caseReport.lastLayout.v1:<사용자 ID>:<의뢰 ID>`, 읽기·쓰기 실패는 무시). 두 창의 기본값은 기억한 레이아웃(아직 있으면) → 표준(`report-layout-standard`) → 첫 레이아웃이다(`reportPreferences.ts`의 `defaultReportLayout`).
- **Final 보고서 레이아웃:** Final 지정 창에도 PPTX 레이아웃 선택이 있고, 고른 레이아웃을 `buildFinalReports(..., { layoutId })`로 넘긴다(이전의 표준→첫 레이아웃 고정 선택 대체).
- **보고서 정보:** 두 창에 작성자(로그인 사용자의 표시 이름으로 미리 채움)와 선택 입력 개발단계·검토조건·결론이 있다. PPTX에는 `ReportExportOptions`의 `author`·`developmentStage`·`reviewConditions`·`reviewConclusion`으로 넘기고, 렌더링할 때만 표지 아래쪽에 값이 있는 항목을 상자로 덧붙인다(`withCaseReportMeta`, 저장한 레이아웃 정의는 바꾸지 않음), HTML에는 값이 있는 항목만 머리 범위 목록(`<dl>`)에 덧붙인다. 서버로는 보고서 파일 안에만 들어가며 `Final/current.json`이나 완료 기록에는 넣지 않는다.

## 만들기 취소

- 만드는 동안 `취소` 버튼(닫기 자리)과 Esc가 진행 중인 읽기를 `AbortController`로 멈추고 파일을 내려받지 않는다. 만들지 않을 때 Esc는 창을 닫는다.

## 이미지 상한과 공유

- 두 형식을 함께 고르면 이미지를 한 번만 읽어 PPTX와 HTML이 같이 쓴다(`loadCaseReportImages`).
- 이미지 상한은 이미지당 30MB, 전체 300MB(원본 크기)다. 넘거나 읽지 못한 이미지는 자리에 안내를 넣고 창에 목록으로 보여준다.

## 그래프 (2026-10-08)

사용자 결정(2026-10-08): 화면의 Case 결과 그래프는 모두 보고서에 넣는다. PPTX는 **편집 가능한 PowerPoint 기본 차트**(pptxgenjs `addChart`, 데이터는 차트 안 통합문서), HTML은 **스크립트 없는 인라인 SVG**(CSP 그대로)다. 데이터는 화면과 같은 API·값을 쓴다(`caseReportCharts.ts`).

| 환경 | 그래프 | 형식 | 데이터 |
|---|---|---|---|
| 유통 | 요약 · Scene별 엣지 최대응력 | 막대(묶은 세로) | `distribution.series`의 선택 엣지 envelope. 엣지 선택이 없으면 `선택 없음` 안내 |
| 유통 | Scene 비교 · 엣지별 수준 TOP/BOTTOM/LEFT/RIGHT | 막대 4개(2×2 한 슬라이드) | `distribution.edge_peaks` |
| 유통 | Scene 상세 · TOP/BOT/LH/RH (Scene마다) | XY 선 4개(2×2 한 슬라이드), 계열 L1–L4 | `/distribution/scenes/{id}?position=`(화면이 Scene을 누를 때 읽는 값, 표시 라인 범위). Scene × 위치를 동시 4개까지 읽고, 읽기 실패는 그 그래프 자리에 사유 |
| 사용 | 평가별 값(4개씩 한 슬라이드) | 막대 | `usage.evaluations` 공통·전방·후방 × 현재 Case(·Reference) |

- 모든 그래프에 제목, 축 제목(단위 포함, 없으면 `단위 미확인`), 계열 이름, 범례가 있다. 계열 색은 화면 차트 색(라이트 테마 값)이다.
- XY 계열은 `ref_coord`로 정렬하고 값이 없는 점은 뺀다. PPTX는 계열들의 X를 하나로 합쳐 빈칸을 잇는다(`displayBlanksAs: span`).
- 큰 계열: 계열당 2,000점을 넘으면 LTTB(Largest-Triangle-Three-Buckets, 결정적)로 2,000점으로 줄이고 그래프 아래에 `L1: 원본 N점 → 2,000점(LTTB 축약)`을 적는다.
- 화면 선택에 따라 보이는 방식(막대/점/선 전환, Case 범례 끄기, Scene 클릭 상세)은 보고서에서 모두 펼친다: 기본 막대, 모든 Scene의 상세.

## 영상 포함 (PPTX · HTML)

- `영상 포함`은 기본 꺼짐이고, 형식을 하나 이상 고르면 켤 수 있다(2026-10-08부터 PPTX도). 끄면 영상 자리에 파일 이름을 표시한다.
- 상한은 영상당 20MB, 전체 200MB(원본 크기)이며 두 형식이 한 번 읽은 영상을 같이 쓴다(`loadCaseReportVideos`). 응답 길이 또는 실제 읽은 크기가 남은 상한을 넘으면 읽기를 멈추고 그 영상은 파일 이름과 사유(`영상당 20MB 초과`·`전체 영상 200MB 초과`·`읽기 오류`)로 대신한다. 넣지 못한 영상은 창에 목록으로 보여준다.
- PPTX: mp4만 `addMedia({ type: 'video' })`로 넣어 PowerPoint에서 재생된다. 표지 이미지는 브라우저에서 첫 프레임을 PNG로 만든 것이고, 만들지 못하면 pptxgenjs 기본 재생 버튼 이미지다. webm·ogg는 PowerPoint 호환을 위해 넣지 않고 사유 `PowerPoint 호환 형식(mp4)이 아님`으로 목록에 남긴다(변환 도구는 넣지 않음). 영상 목록 표에 `PPTX` 열(포함/사유)이 붙는다.
- HTML: `<video controls>`에 data URI로 넣는다(mp4·webm·ogg). HTML 파일은 base64 때문에 약 1.33배 커진다(창에도 표시).
- 애니메이션 컨투어(컨투어 칸의 VIDEO 자산): 영상을 넣으면 `컨투어 영상` 슬라이드·영상으로, 넣지 않으면 첫 프레임(브라우저 `<video>` + canvas, PNG)을 `컨투어` 이미지 슬라이드로 넣는다. 첫 프레임을 만들지 못하면(코덱 미지원·손상·8초 초과) 자리에 안내를 넣고 건너뛴 이미지 목록에 보인다.
- Final 보고서는 업로드 상한(`report_limits`) 안에 들도록 이미지 크기를 뺀 만큼만 영상을 넣는다(PPTX 480 MiB, HTML 320 MiB의 base64 환산, 여유 16 MiB, `finalVideoBudget`).
- 미디어 MIME은 허용 목록(이미지: png·jpeg·gif·webp·bmp·svg, 영상: mp4·webm·ogg)으로 정하고 서버가 준 형식을 그대로 쓰지 않는다.

## 엣지 선택 없음

- 표시 엣지를 모두 끄면 요약 탭과 같은 문구를 쓴다: 값 칸은 `선택 없음`, 요약 문장은 `선택 없음: 표시 옵션에서 엣지를 하나 이상 선택하세요.`

## 안전 규칙

- Scene 이름·파일 이름·값 등 모든 문자열은 HTML 이스케이프(`& < > " '`) 후 넣는다. 원문 문자열을 HTML로 삽입하지 않는다.
- 미디어는 타입이 있는 API 클라이언트(`simulationDashboardApi.assetBlob`)로만 읽는다.
- 레시피·렌더링 함수는 React 상태와 무관한 순수 함수로 두어 6단계 Final 지정이 같은 파일을 만들어 올린다.

## 레이아웃 재사용과 슬라이드 다시 만들기

- 슬라이드 구성은 레시피와 `영상 포함`만으로 정해진다(읽기 성공 여부와 무관: 이미지·영상 슬라이드는 항상 이미지 요소이고 실패하면 사유를 표시). 이전에는 창을 열 때 이미지 슬라이드가 텍스트 요소로 만들어져 PPTX에 정적 컨투어·거동 이미지가 들어가지 않았다(2026-10-08 수정).
- 레이아웃 정의에 콘텐츠 서명(`contentSignature`, 콘텐츠 ID·표시 형식의 FNV-1a)을 둔다. 같은 범위라도 서명이 다르면(예: 그래프 추가 전에 저장한 레이아웃, `영상 포함` 전환) 슬라이드를 다시 만든다. 이때 그 창에서 한 슬라이드 편집은 버려지고 레이아웃 스타일(색·디자인)은 유지된다.

## 재사용 함수 (`frontend/src/features/results/caseReport/caseReport.ts`)

| 함수 | 용도 |
|---|---|
| `loadCaseReport(scope, { signal?, generatedAt? })` | 화면 범위(유통 선택 또는 사용환경 Case)의 자료를 읽어 레시피 생성 |
| `loadCaseFinalReport(scope, { signal? })` | Final 범위(사용환경 Case, 또는 유통 Case의 모든 Run Option) 레시피 |
| `loadCaseReportSources(source, signal?)` | 고정 범위의 분포 결과와 전체 영상 목록 조회 |
| `buildCaseReportData` · `buildCaseUsageReportData` · `buildCaseFinalReportData` | 형식 무관 레시피(순수 함수) |
| `loadCaseReportSceneDetails(distribution, lineIndices, signal?)` | Scene × TOP/BOT/LH/RH 상세(그래프용) |
| `distributionChartGroups` · `usageChartGroups` · `chartSvg` · `lttb` (`caseReportCharts.ts`) | 그래프 정의(순수), 인라인 SVG, LTTB 축약 |
| `loadCaseReportImages(data, { signal?, maxTotalImageBytes?, includeVideos? })` | 이미지를 한 번 읽어 두 형식이 공유(애니메이션 컨투어는 첫 프레임), 건너뛴 이미지 목록 |
| `loadCaseReportVideos(data, { signal?, posters?, maxVideoBytes?, maxTotalVideoBytes? })` · `skippedVideoNames(data, videos, format)` | 영상을 한 번 읽어 두 형식이 공유, 형식별 건너뛴 영상(사유 포함) |
| `buildCaseReportPptx(data, { layout?, labels?, images?, videos?, includeVideos?, loadMedia?, signal? }): Promise<Blob>` | PPTX Blob(기본 차트, mp4 영상) |
| `buildCaseReportHtml(data, { includeVideos, images?, videos?, loadMedia?, signal?, maxVideoBytes?, maxTotalVideoBytes? }): Promise<{ blob, skippedVideos, skippedImages }>` | 자체 완결 HTML Blob(SVG 그래프)과 건너뛴 영상·이미지 |
| `caseReportFileName(caseLabel, generatedAt, 'pptx' \| 'html')` | 결정적 Windows 안전 파일 이름 |
| `escapeHtml(value)` | HTML 이스케이프 |

## 검증

- 2026-10-08 그래프·영상: e2e `case-report.spec.ts` 새 시험 2건 — PPTX에 차트 17개(`ppt/charts/chart*.xml`: 막대 5, XY 12, 통합문서 17), 축 제목·단위·계열 이름, LTTB 안내, `영상 포함` 시 mp4 2개(`ppt/media/*.mp4`, `videoFile` 슬라이드 2장: Scene 영상·애니메이션 컨투어), 정적 컨투어 이미지(`<p:pic>`); HTML에 SVG 17개, 스크립트·외부 참조 없음, 영상 미포함 시 애니메이션 컨투어 첫 프레임 자리. 받은 PPTX를 python-pptx로 열어 차트 종류·제목·계열·축 제목과 미디어 도형을 확인하고 Final 업로드 검사(`validate_report('pptx')`)를 통과했다. 백엔드 `test_pptxgenjs_report_with_native_charts_and_embedded_video_passes_validation`(pptxgenjs로 XY·막대 차트와 mp4+표지 생성 → 검사 통과).

- W7 e2e: `case-report.spec.ts`(레이아웃 옆 템플릿 안내, 보고서 정보가 PPTX 슬라이드·HTML 범위 목록에 들어감, 고른 레이아웃 색이 PPTX에 쓰이고 다음 창 기본값), `case-finalization.spec.ts`(Final 창 레이아웃 선택·템플릿 안내·고른 레이아웃 색과 보고서 정보가 올린 PPTX·HTML에 들어감·기억).
- e2e `frontend/e2e/case-report.spec.ts`: 사용환경 보고서(PPTX·HTML에 다섯 평가 값·OK/NG·이미지·영상), 만드는 중 취소·Esc, 템플릿 연결 레이아웃의 새 레이아웃 저장(POST만, 템플릿 필드 유지)과 PPTX 생성, 엣지 선택 없음 문구, 범위로 열기, 형식 없음 차단, PPTX 하나(zip·`slide1.xml`의 Case·Scene), HTML 하나(data URI 이미지, 기본 영상 없음, `<script>` Scene 이스케이프, 외부 참조 없음), 영상 포함·상한 초과 건너뜀 표시, 두 형식 두 파일, 열린 뒤 선택 변경에도 범위 유지, 2560×1440(배율 1.5) 화면.
