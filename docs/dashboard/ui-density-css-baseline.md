# UI 밀도 개선 P0 — CSS 기준선

- 측정일: 2026-09-19
- 범위: `frontend/src/**/*.css` 38개. 앱 CSS/TSX는 변경하지 않았다.
- 파서: 설치된 Vite 전이 의존성 PostCSS 8.5.22. 모든 파일을 AST로 파싱했으며 오류는 0건이다.
- 관련 계획: [UI 밀도 개선 실행 계획](ui-density-improvement-plan.md), [P0 적용 계약](ui-density-p0-contract.md)

## 집계 정의와 한계

선언 수는 PostCSS `Declaration` 노드 전부다. `@media`와 `@supports` 내부 및 CSS custom property를 포함하고, 주석·at-rule 파라미터는 제외한다. rule 수는 `Rule` 노드 수다. 따라서 한 rule의 comma selector는 rule 하나, selector 문자열 하나로 계산한다.

`max-width`와 `border-radius`는 해당 property 선언을, `font-size !important`는 `font-size` 선언 중 important 플래그가 있는 것을 집계했다. breakpoint는 `@media` params에서 `min/max-width/height` 조건을 추출해 공백 표기를 통합했다. 실제 computed style, cascade 승자, JSX에서 동적으로 붙는 class와 외부 라이브러리 CSS의 적용 여부는 이 정적 기준선의 범위 밖이다.

컨트롤 집계는 selector에 native `button/input/select/textarea`, ARIA button/textbox/combobox/spinbutton, `type=button|submit|reset`, 또는 `button/control/input/select/textarea/picker/dropdown`을 포함한 class가 있는 선언만 포함하는 휴리스틱이다. 비표준 class명과 wrapper 상속은 놓칠 수 있으므로, P2 전 화면 computed-style 검증을 대신하지 않는다.

## 전체 규모

| 항목 | 수치 |
|---|---:|
| CSS 파일 | 38 |
| 바이트 | 560,893 |
| CSS rule | 5,061 |
| 선언 | 15,779 |
| `max-width` 선언 / 값 종류 | 75 / 35 |
| `font-size !important` 선언 | 61 |
| 휴리스틱 control `height` / `min-height` | 114 / 59 |
| 전체 `height` / `min-height` (참고) | 278 / 253 |
| `border-radius` 선언 / 값 종류 | 619 / 26 |
| media dimension 조건 / 종류 | 102 / 22 |

`styles.css`가 8,043 선언(51.0%)으로 가장 크고, `focused-shell.css`가 1,111 선언(7.0%)이다. 두 파일을 함께 바꿀 때에는 cascade 순서와 중복 legacy 선언을 먼저 확인한다.

## 파일별 rule·선언 수

| 파일 | rule | 선언 |
|---|---:|---:|
| `styles.css` | 2,509 | 8,043 |
| `focused-shell.css` | 375 | 1,111 |
| `features/data/SemanticMappingWorkspace.css` | 186 | 597 |
| `features/results/SimulationDashboard.css` | 148 | 520 |
| `features/workbench/local-programs/local-programs.css` | 151 | 469 |
| `features/data/FolderEnvironmentWorkspace.css` | 160 | 448 |
| `features/local-pc/local-pc-settings.css` | 129 | 435 |
| `features/result-overview/ResultOverviewDashboard.css` | 135 | 382 |
| `features/storage/StorageWorkspacePanel.css` | 135 | 381 |
| `features/workbench/modeling-templates/modeling-templates.css` | 105 | 354 |
| `features/data/FolderDiscoveryWorkspace.css` | 101 | 348 |
| `theme.css` | 95 | 278 |
| 나머지 26개 파일 | 932 | 2,413 |

나머지 파일별 완전 수치는 원시 산출물의 `files` 배열에 있다. 이 문서는 핵심 검토에 필요한 상위 파일만 표시해 기준선 자체가 원시 selector 목록이 되지 않게 했다.

## 폭: `max-width`

75건 중 `100%` 16건과 `none` 10건은 cap 제거 또는 responsive field 선언이다. 실제 상한 후보 중 작업 wrapper에 직접 연결되는 값은 아래와 같다. 값별 모든 selector·위치는 원시 `maxWidth` map에 보존했다.

| 값 | 선언 | 대표 위치 | P1 판단 |
|---|---:|---|---|
| `1120px` | 6 | `focused-shell.css:122` assigned-only hero/card/list | assigned-only 작업 화면 후보 |
| `1280px` | 1 | `styles.css:88` `.workflow-layout` | 작업 wrapper 후보 |
| `1440px` | 2 | `FolderDiscoveryWorkspace.css:1` `.folder-discovery`; `SemanticMappingWorkspace.css:8` `.semantic-flow` | 데이터 작업 화면 후보 |
| `1500px` | 4 | `styles.css:81` chassis, `:89` workflow board, `:105` data workspace, `:225` example gallery | 화면별 wrapper 판별 필요 |
| `1520px` | 1 | `styles.css:220` `.comparison-workspace` | 비교 작업 화면 후보 |
| `1920px` | 4 | `focused-shell.css:501` assigned-only children | responsive override와 함께 확인 |
| `2560px` | 1 | `modeling-templates.css:1` `.modeling-templates-page` | 모델링 작업 화면 후보 |
| `1450px` | 1 | `styles.css:217` `.help-center` | 읽기 폭 예외로 보존 검토 |
| `520px` | 3 | password form, table detail cell, empty/error text | 폼·cell·문구 예외 |
| `680px` | 1 | `modeling-templates.css:4` search | field 읽기 폭 예외 |
| `calc(100vw - 32px)` | 1 | `modeling-templates.css:11` create dialog | dialog viewport 안전폭 예외 |

나머지는 table cell/description/preview/field 단위(72px~460px), `48%`, `calc(100% - 10px)`, `min(520px, calc(100vw - 120px))`다. 이들은 wrapper cap과 같은 규칙으로 없애면 안 된다.

## 글자 강제 규칙

`font-size !important` 61건은 `styles.css` 45, `focused-shell.css` 15, `FolderDiscoveryWorkspace.css` 1에 있다. 값별로는 `var(--ui-font-size)` 36건, `calc(var(--ui-font-size) * …)` 16건, fixed `10/12/14px` 4건, custom widget/portfolio variable 2건, heading additive calc 3건이다.

핵심 blanket selector는 `styles.css:963` (light theme form/text tags), `:1105` 및 `:1129` (dark theme), `:1227` (`span/strong/small/code`), `:1228`~`:1231` (heading/chart)이다. P3 이전 scope에서는 이 selector군이 migrated subtree를 제외하도록 바꾸어야 하며, 새 `!important`로 맞덮어서는 안 된다. nav 축소와 custom widget font selectors는 별도 우선순위 검증 대상이다.

## 컨트롤 치수와 모서리

휴리스틱 control `height`는 33값에 114건, `min-height`는 30값에 59건이다. 가장 많은 고정 height는 `34px` 19, `36px` 12, `30px` 10, `32px` 9, `29px` 7이며, 가장 많은 min-height는 `34px` 7, `40px` 6, `32px` 5, `44px` 4다. 높이에는 `auto`, `max-content`, `100%`와 1/3/5px 같은 아이콘·장식 요소도 포함되어 있어, 단순 전체 치환은 금지한다.

대표 selector는 `SemanticMappingWorkspace.css:23` `.vocabulary-scope-select select`의 34px, `FolderEnvironmentWorkspace.css:5` form input/select의 36px, `ResultOverviewDashboard.css:53` filter select/search input의 44px, `StorageWorkspacePanel.css:27` action button의 min-height 44px다. P2의 32/38/44px token 소비는 이 기준선의 기존 30~44px control cluster와 화면별 비교 후 진행한다.

`border-radius`는 619건이다. 주요 값은 `8px` 113, `6px` 112, `7px` 109, `5px` 64, `10px` 42, `9px` 38, `4px` 31이며, `50%` 28과 `999px` 11은 원형/필 특수형이다. P2의 4/8/12px 토큰은 기존 value를 즉시 전역 정규화하지 않고 migrated control/card만 대상으로 삼아야 한다.

## Breakpoint 기준선

공백 표기를 통합한 22종 102개 dimension 조건은 다음과 같다.

| 조건 | 건수 | 조건 | 건수 |
|---|---:|---|---:|
| `max-width:600px` | 4 | `max-width:620px` | 23 |
| `max-width:640px` | 4 | `max-width:680px` | 2 |
| `max-width:700px` | 13 | `max-width:720px` | 7 |
| `max-width:760px` | 8 | `max-width:800px` | 2 |
| `max-width:900px` | 14 | `max-width:980px` | 3 |
| `max-width:1000px` | 3 | `max-width:1050px` | 2 |
| `max-width:1100px` | 6 | `max-width:1200px` | 2 |
| `max-width:1280px` | 1 | `max-width:1350px` | 1 |
| `min-width:621px` | 1 | `min-width:901px` | 1 |
| `min-width:1440px` | 2 | `min-width:1800px` | 1 |
| `min-height:680px` | 1 | `min-height:1100px` | 1 |

`620/621`, `900/901`, `1440`은 shell 또는 breakpoint pair로 이미 쓰이므로 P1에서 보존한다. 기능 CSS에 이미 600, 640, 680, 700, 720, 760, 800, 980, 1000, 1050, 1100, 1200, 1280, 1350, 1800이 공존한다. 새 breakpoint를 늘리기 전에 동일 기능의 기존 조건과 viewport 증거를 먼저 대조해야 한다.

## 재현과 원시 산출물

분석 스크립트와 전체 AST 집계 JSON은 작업공간 변경에 섞이지 않도록 다음 TEMP 경로에만 저장했다.

- `C:\Users\coolc\AppData\Local\Temp\simulation-workbench-css-baseline-20260919\analyze-css.mjs`
- `C:\Users\coolc\AppData\Local\Temp\simulation-workbench-css-baseline-20260919\css-baseline.json`

PowerShell에서 저장소 루트(`E:\simulation_workbench`)로 이동한 뒤 다음을 실행하면 같은 JSON을 재생성한다.

```powershell
node C:\Users\coolc\AppData\Local\Temp\simulation-workbench-css-baseline-20260919\analyze-css.mjs E:\simulation_workbench C:\Users\coolc\AppData\Local\Temp\simulation-workbench-css-baseline-20260919\css-baseline.json
```

이 재현은 현재 Vite 전이 의존성의 PostCSS 8.5.22 경로를 쓴다. P1에서 구조 검사에 편입할 때에는 설치 그래프의 우연한 경로에 의존하지 말고, parser를 명시적으로 고정할지 별도 결정한다.

P0 원시 산출물 식별용 SHA-256:

- analyze-css.mjs: `8229114FC797FB2F55FC9347855DF020AA6811F7B019291AC2ACFBB51CD5F819`
- css-baseline.json: `F1577A26AFDE7D8DE55FCA0513D20B7E4E141E3D972CAD01D42DE0E0F22555EC`

TEMP 파일은 영구 보관을 보장하지 않는다. P1에서 검사 스크립트와 fixture를 저장소에 편입해 이 문서의 절대 TEMP 경로 없이 재현 가능하게 만든다.
