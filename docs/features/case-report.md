# Case 결과 보고서 (PPTX · HTML)

Case 결과 화면 헤더의 `보고서` 버튼으로 현재 선택한 Case 결과를 PPTX와 HTML 파일로 내려받는다. 파일은 브라우저에서 만들며 백엔드 변경은 없다. PDF는 사용자가 PPTX·HTML로 직접 만든다(2026-10-02 23:58 결정). 웹에는 PDF 변환·인쇄용 스타일 등 PDF 관련 구현을 두지 않는다. 계획 근거는 [Case 결과 흐름 재설계](../plans/case-results-workflow-redesign.md) §3.3·§3.6과 결정 기록 23:51·23:56·23:58이다.

## 범위 고정

- 버튼은 유통환경에서 결과가 있는 Run Case·Run Option·Component·Basis가 정해졌을 때 켜진다. 사용환경과 소재·물성 탭에서는 꺼진다.
- 창을 여는 순간의 범위를 복사한다: 프로젝트·의뢰, Case(대시보드 Case ID), 병합 최신 결과 `latest:<Case>`, 하중경우, Run Case, Run Option(mode), Component, Basis, 표시 엣지·라인. 창을 연 뒤 화면 선택이 바뀌어도 열린 보고서의 범위·자료는 바뀌지 않는다(`ReportSource` 분기 `case_results`).
- 자료는 창을 열 때 한 번 읽어 레시피를 만든다. 두 형식 모두 같은 레시피로 그린다.

## 레시피 (`CaseReportData`)

| 항목 | 내용 |
|---|---|
| 제목 | `<Case> 해석 결과 보고서`, 생성 일시(`YYYY.MM.DD HH:mm`, 창을 연 시각) |
| 범위 | 프로젝트, 의뢰, Case, 하중경우, Run Case, Run Option, Component · 기준, 생성 일시 |
| 요약 | 요약 탭과 같은 값: Scene별 선택 엣지 최대응력(단위 포함, 단위 없으면 `단위 미확인`)과 Scene별 최대 엣지, 전체 최대값과 Scene |
| Scene 비교 | Scene 순번·이름·자세·충돌, TOP/BOTTOM/LEFT/RIGHT 최대응력 |
| 결과 이미지 | 준비된 컨투어·거동 이미지 |
| 영상 | Run Option의 모든 Scene 영상(`/videos` 전체 페이지) |

## 형식

- 형식 선택: PPTX, HTML 체크박스. 하나 이상 골라야 다운로드가 켜진다. 둘 다 고르면 두 파일을 받는다.
- 파일 이름: `<Case>_<YYYYMMDD-HHmm>.pptx|.html`. Windows 금지 문자·제어 문자는 `_`로 바꾸고, 공백은 `_`, 끝의 점·공백 제거, 80자 제한, 예약 이름(CON, NUL, COM1 등)은 앞에 `_`를 붙인다.
- PPTX: 기존 pptxgenjs 렌더러(`reportExport.ts`)와 회사 레이아웃(`/api/report-layouts`), 레이아웃 편집 창을 재사용한다. 표지(제목·작성일·범위·요약 최대값) 다음에 요약 표, Scene 비교 표(12행 단위로 슬라이드 분할), 이미지 1장당 슬라이드 1장, 영상 목록 표가 이어진다. 영상은 대표 이미지 정보가 계약에 없어 파일 이름으로 표시한다. 업로드 PPTX 템플릿 바인딩은 이 보고서 형식을 지원하지 않아 화면 레이아웃으로 생성한다.
- HTML: 파일 하나로 완결된다. CSS는 문서 안에, 이미지·영상은 data URI로 넣는다. 스크립트를 쓰지 않고, `Content-Security-Policy`(`default-src 'none'; img-src data:; media-src data:; style-src 'unsafe-inline'`)로 외부 참조와 스크립트 실행을 막는다. 업로드한 HTML은 SPDM에 저장만 하고 앱이 페이지로 제공하지 않는다(6단계).

## HTML 영상 포함

- `영상 포함`은 기본 꺼짐이다. 끄면 영상 자리에 파일 이름을 표시한다.
- 켜면 `<video controls>`에 data URI로 넣는다. 영상은 보통 1MB 미만이며 상한은 영상당 20MB, 전체 200MB다. 응답 길이 또는 실제 읽은 크기가 남은 상한을 넘으면 읽기를 멈추고 그 영상은 파일 이름으로 대신한다. 넘었거나 읽지 못한 영상은 창에 목록으로 보여준다.
- 미디어 MIME은 허용 목록(이미지: png·jpeg·gif·webp·bmp·svg, 영상: mp4·webm·ogg)으로 정하고 서버가 준 형식을 그대로 쓰지 않는다.

## 안전 규칙

- Scene 이름·파일 이름·값 등 모든 문자열은 HTML 이스케이프(`& < > " '`) 후 넣는다. 원문 문자열을 HTML로 삽입하지 않는다.
- 미디어는 타입이 있는 API 클라이언트(`simulationDashboardApi.assetBlob`)로만 읽는다.
- 레시피·렌더링 함수는 React 상태와 무관한 순수 함수로 두어 6단계 Final 지정이 같은 파일을 만들어 올린다.

## 재사용 함수 (`frontend/src/features/results/caseReport/caseReport.ts`)

| 함수 | 용도 |
|---|---|
| `loadCaseReportSources(source, signal?)` | 고정 범위의 분포 결과와 전체 영상 목록 조회 |
| `buildCaseReportData({ scope, distribution, videos, generatedAt })` | 형식 무관 레시피(순수 함수) |
| `buildCaseReportPptx(data, { layout?, labels?, loadMedia?, signal? }): Promise<Blob>` | PPTX Blob |
| `buildCaseReportHtml(data, { includeVideos, loadMedia?, signal?, maxVideoBytes?, maxTotalVideoBytes? }): Promise<{ blob, skippedVideos }>` | 자체 완결 HTML Blob과 건너뛴 영상 |
| `caseReportFileName(caseLabel, generatedAt, 'pptx' \| 'html')` | 결정적 Windows 안전 파일 이름 |
| `escapeHtml(value)` | HTML 이스케이프 |

## 검증

- e2e `frontend/e2e/case-report.spec.ts`: 범위로 열기, 형식 없음 차단, PPTX 하나(zip·`slide1.xml`의 Case·Scene), HTML 하나(data URI 이미지, 기본 영상 없음, `<script>` Scene 이스케이프, 외부 참조 없음), 영상 포함·상한 초과 건너뜀 표시, 두 형식 두 파일, 열린 뒤 선택 변경에도 범위 유지, 2560×1440(배율 1.5) 화면.
