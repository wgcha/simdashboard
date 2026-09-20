sim_workbench UI 크기·밀도 개선안 — MatNexus 참고 (https://github.com/4leaf321-nox321/MatNexus)
2026-09-19 · 색·테마는 다루지 않는다. 크기·밀도·폭만. 두 저장소 모두 수정하지 않았다. 모든 인용은 실제 파일에서 확인한 것.

0. 요약
원인은 셋이고, 크기 순서대로다.

| 원인 | 상태
-- | -- | --
1 | 전역 폰트 !important 5줄 — 모든 역할의 글자를 한 값(18.67px)으로 뭉갠다 | 손 안 댐
2 | 크기 토큰 부재 — 크기 CSS 변수가 3개뿐. 컨트롤 높이 9종, max-width 36종, 브레이크포인트 11종 | 손 안 댐
3 | 폭 정책이 화면마다 다름 | 이미 절반쯤 고쳐져 있다

sim_workbench 의 코드 상수 ↔ JSON 이중 pin 방식(test_checked_in_baseline_cannot_be_raised_by_editing_json)을 그대로 쓰면, JSON 만 고쳐 상한을 올리는 것도 막힌다.

6. 작업 체크리스트 (파일:줄)
U-1 폭

styles.css:88 .workflow-layout max-width 1280 제거

styles.css:217 .help-center max-width 1450 + margin:0 auto 제거

styles.css:220 .comparison-workspace max-width 1520 제거

styles.css:81,89,225 .chassis-dashboard/.workflow-board/.example-gallery 1500 제거

styles.css:153,224,759 나머지 1200/1350 확인

focused-shell.css:501,526,534,542 max-width 1920 제거

focused-shell.css:122,126,127,133,143,164 1120 + margin:0 auto 제거

features/data/FolderDiscoveryWorkspace.css:1, SemanticMappingWorkspace.css:8 1440 제거

features/workbench/modeling-templates/modeling-templates.css:1 2560 제거

좌우 패딩을 --shell-pad-x: clamp(24px,2vw,40px) 로 통일 (focused-shell.css:59,121,438,476-481, RequestWorkspaceHeader.css:1)

읽는 화면 예외 목록 확정 (voc.css:1, 도움말, 폼 다이얼로그)
U-2 토큰

tokens.css 신설, main.tsx 로드 순서 styles.css 앞에

컨트롤 높이 9종 → 3종: styles.css:39,52,993,1108 · focused-shell.css:74,101,153,621 · RequestWorkspaceHeader.css:10 · SimulationDashboard.css:1,2,7

radius 하드코딩 → --radius 3단
U-3 프리미티브

shared/components/Table.tsx — <table> 9개 파일이 쓸 수 있게

shared/components/Button.tsx / Input.tsx / Select.tsx

ResultOverviewDashboard.tsx:22 PAGE_SIZE 5 → 25

ResultOverviewDashboard.css:84 min-height:67px → var(--row-h)

focused-shell.css:103,135 min-height:58/68px 검토
U-4 rem

html { font-size: var(--ui-root, 14px) } + --text-* 정의

슬라이더를 --ui-root 로 (App.tsx:945, app/shell/WorkspaceShellLayout.tsx:62)

styles.css:963 에서 td,th 제거 → 표 CSS 를 토큰으로

styles.css:1129 동일 (다크)

button,input,select,textarea 제거 → 컨트롤 CSS 를 토큰으로

label,p,li 제거

styles.css:1227 span,strong,small,code 제거

styles.css:1228-1230 h1~h6 제거 → --text-xl 등으로

calc(var(--ui-font-size) * N) 14곳 흡수 (styles.css:20,21,22,1130 · focused-shell.css:328,369,373,379,385,394,415,416,429,432,608,666)
U-5 가드레일

check-architecture.mjs 에 CSS 검사 6종 추가

architecture-baseline.json 에 styles.css 라인 상한 + !important font-size 개수 pin
7. 하지 말 것
--ui-font-size 기본값만 14pt → 11pt 로 낮추기. 가장 빠르지만 문서화된 정책 위반이고, 근본 원인(!important 블랭킷 · 토큰 부재 · 폭 상한)은 하나도 안 고쳐진다. 슬라이더를 올린 사용자에게는 원래대로 돌아간다.
Tailwind 전면 도입으로 한 번에 갈아엎기. 263 KB CSS + 37개 파일 + react-grid-layout 전제를 한 번에 바꾸는 건 위험하다. U-1~U-4 가 Tailwind 없이 전부 가능하고, 나중에 Tailwind 로 가더라도 토큰이 먼저 있어야 이식이 싸다.
MatNexus 의 h-8 을 그냥 베끼기. MatNexus 가 조밀한 건 프리미티브 기본값이 일관되기 때문이지 값이 작아서가 아니다. sim_workbench 는 프리미티브 자체가 없으므로 U-2·U-3 으로 자리를 먼저 만들어야 한다.
white-space:nowrap 을 지워 표를 접기. MatNexus 의 반대 판단이 맞다 — "접지 않는다. 열은 내용만큼 넓어지고, 표가 넘치면 가로로 밀린다"(바깥이 overflow-x-auto). 접으면 행 높이가 들쭉날쭉해져 스캔이 안 된다. 대신 U-1 로 폭을 준다.
한 번에 전 화면 적용. U-3·U-4 는 새 화면(대시보드)부터 시작하는 것이 맞다. 대시보드는 지금 활발히 크는 중이라 어차피 손이 간다.
8. MatNexus 에서 가져오지 말아야 할 것 — sim_workbench 가 더 나은 지점
공정하게 적어 둔다. 이 둘은 지키는 게 맞다.

11~18pt 사용자 글자 크기 조절 — MatNexus 에는 없다. 접근성 기능이고 현장 요구였을 것이다. U-4 는 이걸 없애자는 게 아니라 제대로 동작하게 만드는 것이다(루트를 움직이면 여백·행 높이까지 따라온다).

한글 폰트를 지정한다 — sim_workbench 는 pretendard 를 npm 으로 자체 호스팅하고 styles.css:1 에 폴백 스택(Pretendard Variable, Pretendard, Apple SD Gothic Neo, Noto Sans KR, Noto Sans CJK KR, Malgun Gothic, Segoe UI)까지 적어 뒀다. MatNexus 는 Geist 만 지정하고 한글 폰트 스택이 없다 — Geist 에 한글 글리프가 없으므로 한글은 OS 기본(Windows 맑은 고딕)으로 떨어진다. 화면 문구가 거의 전부 한글인 앱에서 이건 sim_workbench 쪽이 낫다.

dashboard_captures 의 불변 수집 버전 설계 — UI 와 무관하지만, 이번 pull 의 대시보드 설계 자체는 좋다. 화면 밀도만 고치면 된다.

9. 근거 (전부 직접 확인)
sim_workbench

frontend/src/styles.css — :1(폰트 스택·--font-mono), :20-22(배율), :39,52(컨트롤 36/48px), :81,88,89,105,153,217,220,224,225,759(max-width), :956(--ui-font-size:14pt), :963, :993,1108, :1226-1231(전역 !important 5줄), :1129,1130,1131, :1191,1214
frontend/src/focused-shell.css — :54(topbar 60px), :59(max-width:none), :74,101,153,621(컨트롤), :103,135(행 높이), :122,126,127,133,143,164(1120 가운데), :310,316,321(사이드바 208/76), :328,369,373,379,385,394,415,416,429,432,608,666(배율), :434-439(data-workspace max-width:none), :475-549(≥1440 분기, :501,526,534,542 1920 상한, :512-514 주석)
frontend/src/theme.css:134
frontend/src/app/shell/WorkspaceShellLayout.tsx:62, frontend/src/App.tsx:945
frontend/src/features/result-overview/ResultOverviewDashboard.tsx:22(PAGE_SIZE = 5), .css:84
frontend/src/features/results/SimulationDashboard.css
frontend/src/features/request-workspace/RequestWorkspaceHeader.css:1,7,10,12
frontend/src/shared/components/(실질 4개), frontend/src/main.tsx:5-9(CSS 로드 순서)
frontend/architecture-baseline.json, frontend/scripts/check-architecture.mjs(크기 규칙 0건)
frontend/package.json(Tailwind 없음, pretendard 1.3.9, react-grid-layout 1.5.2)
docs/unified-request-workspace-plan.md:13,19,28,43,44, docs/request-centric-workspace-ux.md:75,102,115, docs/dashboard-usability-and-recommended-layout-plan.md:45, docs/lan-video-usability-followup.md:27,41
MatNexus (읽기 전용)

frontend/package.json(Tailwind v4, @fontsource-variable/geist), frontend/components.json("style": "radix-nova" — 버튼·인풋이 h-9 가 아니라 h-8 인 프리셋)
frontend/src/index.css(크기 커스터마이즈 = radius 7단 + font-family 뿐, html/body 에 font-size 없음)
frontend/src/shared/layout/AppShell.tsx(폭 상한 없음·좌측 정렬, 1600px 폐기 경위 주석), Sidebar.tsx(w-60), Header.tsx(h-14 px-3)
frontend/src/shared/components/ui/{table,button,input,select,card,badge,dialog,tabs}.tsx
frontend/src/shared/components/PageHeader.tsx, ColumnFilter.tsx
frontend/src/modules/materials/SpecimensPage.tsx(PAGE = 50, w-px 열 폭, 열 접힘 주석)
frontend/src/test/boundaries.test.ts(describe('본문 폭'), describe('옆패널 글자'), NARROW_BY_DESIGN)
backend/tests/architecture/test_frontend_table_tone.py
AGENTS.md「프론트」절, docs/개발계획.md D4 · §1.3(CSS 129줄 vs 20,240줄) · §0 v1.19/v1.24/v1.25
10. 확인하지 못한 것
styles.css 의 압축된 장문 라인(L61, L91, L103, L154, L761, L1062 등) 내부 — 추가 크기 선언이 더 있을 수 있다. U-1 착수 전에 이 줄들을 펼쳐 보는 것이 첫 작업이다
styles.css:153, 224, 759 의 1200/1350 상한이 가운데 정렬인지 (체크리스트에 "확인"으로 둠)
frontend/src/ 의 .css 파일 정확한 개수 — Grep 기준 37개(Glob 타임아웃으로 트리 직접 열람 불가)
shared/hooks/useRowFocus.ts 의 ROW_FOCUS_STYLE 상수 값
MatNexus docs/개발계획.md §5 GUI 구성의 45KB 이후 부분
MatNexus 가 한글 폰트를 지정하지 않는다는 것은 코드에 한글 폰트 스택이 전혀 없음을 확인한 결과의 추론이다(실제 렌더를 본 것은 아니다)
