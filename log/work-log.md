# 작업 로그

이 파일은 완료된 개발 작업을 누적 기록한다. 이후 작업은 완료 시 최신 항목을 문서 상단에 추가하며, 변경 범위·검증 결과·남은 확인 사항을 함께 남긴다.

## 2026-09-02 — 배포 진입점 정리

### 변경 내용

- PowerShell 또는 POSIX shell 진입점으로 완전히 대체된 7개 Windows wrapper(`export-postgresql-transfer.bat`, `import-postgresql-transfer.bat`, `setup-postgresql.bat`, `setup.bat`, `start-postgresql.bat`, `start.bat`, `stop.bat`)를 삭제했다.
- 실제 Windows 의존성 설치 로직을 보유하고 `setup.ps1`에서 호출되는 `setup-windows.bat`은 대체 진입점이 없어 보존했다.
- 사내 운영 배포의 canonical Rocky 8 경로를 문서에 명시했다: (1) `./deploy/rocky8/build-release.sh`로 bundle 생성, (2) 대상에서 `sudo ./install.sh --config /root/simdashboard-install.env --check` 실행, (3) 검증 통과 후 `sudo ./install.sh --config /root/simdashboard-install.env` 실행.
- README, PostgreSQL 이관/통합 문서, PowerShell 오류 안내와 startup 테스트를 삭제된 wrapper 없이 갱신했다.

### 검증 결과 및 남은 확인

- `tests/test_postgres_startup.py`: `70 passed`
- `bash deploy/rocky8/validate-templates.sh`: `ROCKY8_DEPLOY_TEMPLATES_OK`
- `git diff --check`: 통과
- WSL에서 Windows PowerShell parser를 호출하려 했으나 Windows interop의 `UtilBindVsockAnyPort` 오류로 실행하지 못했다. Rocky POSIX shell 정적 검증과 Python 테스트는 완료했다.

## 2026-08-09 — 보고서·판정·작업 배치 실행 미완료 범위 보완

### 담당

- Sol: 기존 구현과 사양 수용 기준 대조, 다중 프로젝트·결과 그룹·권한·보고서 snapshot·배치 이력 누락 감사
- Luna: 작업 종류별 실행 위젯, 작업 담당자 권한 안내, attempt/event 타임라인, 배치 경로 내부 탭, 키보드·모바일 UI와 E2E 구현
- 루트 통합: 프로젝트별 기준 스키마, 신규 프로젝트 seed, import 변수 의미 등록, preflight/멱등 attempt 제어면, 보안·migration·OpenAPI·보고서 범위·회귀 검증

### 완료 범위

- 일반 분석 보고서의 Run은 같은 하중 경우의 `<select>`로만 선택하며 `ReportSource.runId`를 필수화했다. 완료 Run이 없으면 내보내기를 열거나 생성할 수 없다.
- `quality_thresholds` 정본을 `(project_id, criterion_key)` 복합 키로 전환했다. 기존 DuckDB는 멱등 재구성하고 PostgreSQL은 `0006_batch_attempts`에서 전환하며, 시작 시와 신규 프로젝트 생성 즉시 Chassis/Open Cell 기준 두 건을 보장한다.
- 재판정·overview·위젯·보고서 snapshot은 변수 카탈로그 `result_group`까지 사용한다. Chassis는 `CHASSIS_REAR + permanent_deformation + mm`, Open Cell은 `OPEN_CELL + stress + MPa`만 포함하며 CUSTOM·타 단위는 제외한다.
- Open Cell/Chassis 판정 요약은 적용 개수와 값 범위를 표시하고 admin만 기준을 편집한다. Chassis summary는 저장된 `variableId`가 있어도 모든 대상 영구변형 mm로 계산·보고한다.
- 작업 행은 클릭/Enter/Space로 상세를 열고 16개 Task kind별 목적·입력·출력·실행 위젯을 제공한다. 선택 자체는 상태를 변경하지 않으며, 진행률·실행·배치는 admin 또는 해당 작업 담당자에게만 열린다.
- `작업 유형 관리` 안에 ARIA 내부 탭 `배치 경로 정의`를 두었다. 프로필 저장 때마다 불변 `version`을 만들고 허용 placeholder·절대 로컬 경로·Task Type 호환성을 preflight한다.
- 배치 클릭은 `idempotency_key`로 중복을 막고 profile version snapshot, `PREFLIGHT → QUEUED → SUCCEEDED/REJECTED/FAILED` attempt와 이벤트를 저장한다. 선택 작업 상세에서 상태·진행률·최근 이벤트를 조회한다.
- 수행자/viewer 응답에서는 solver 절대경로, working directory, 환경값, command snapshot을 숨긴다. 실제 solver, subprocess, scheduler, 외부 네트워크는 호출하지 않고 `DEMO_ONLY`만 유지한다.
- DuckDB 정본, Alembic `0006`, PostgreSQL `schema.sql`, OpenAPI JSON과 생성 TypeScript 계약을 동기화했다. downgrade는 프로젝트별 기준을 결정적으로 축약한 뒤 0005의 단일 키 계약을 복원한다.

### 검증과 남은 운영 경계

- 백엔드 전체 회귀: `80 passed` (pytest cache ACL 경고 2건만 발생)
- 프런트엔드 TypeScript 및 Vite production build: 통과 (2257 modules, 기존 500 kB chunk 경고만 발생)
- 새 DuckDB·backend·Vite를 자동 기동한 Playwright 전체 E2E: `15 passed`
- 인앱 브라우저: 보고서 Run이 combobox 1개/textbox 0개, Chassis 적용 대상 6개·3.70~6.30 mm 범위, reliability 전용 위젯과 비활성 사유, 배치 내부 탭/form, 배치 attempt 영역, 콘솔 warn/error 0 확인. Luna의 390×844 점검에서도 세로 배치를 확인했다.
- 실제 PostgreSQL DB에 `0006` upgrade/downgrade를 적용하는 검증은 이 세션에서 수행하지 못했다. 배포 전 owner 자격 증명으로 양방향 migration smoke test가 필요하다.
- 운영 Runner 활성화에는 승인 application alias, 허용 root, secret reference, scheduler·취소·timeout·retry 정책이 추가로 필요하다. 그 전까지 외부 실행은 활성화하지 않는다.

## 2026-08-02 — 보고서 Run·판정 요약·작업 배치 실행 구현 완료

### 담당

- Sol: 현행 구조 조사, 구현 사양 작성, Luna 결과 계약 검토 및 P1 보완점 3건 식별
- Luna: Run별 overview, 판정 기준/위젯, 작업 진행률, DEMO_ONLY 배치 프로필·dispatch, master-detail UI 구현
- 루트 통합: 비교 보고서의 기준/대상 Run 드롭다운, PostgreSQL 기준 seed, 배치 프로필-Task Type 호환 계약, 키보드 선택·업무 안내, 전체 회귀 검증

### 구현 완료 범위

- 일반 분석 보고서와 Run 비교 보고서 모두 Run UUID 텍스트 입력을 제거하고 현재 하중 경우의 Run 드롭다운을 사용한다. 선택 시 overview와 콘텐츠 snapshot, `ReportSource`를 다시 고정한다.
- Chassis 기준 변경은 프로젝트 내 `unit=mm`이면서 canonical key가 `permanent_deformation`인 모든 scalar 결과를 재판정한다.
- `open_cell_stress_mpa` 기준과 `open_cell_summary` 시스템 위젯을 추가했다. `unit=MPa`이면서 canonical key가 `stress`인 scalar 결과에만 적용하며, 최대 응력·전체 판정·기준 편집을 제공한다.
- 작업별 0~100 진행률과 갱신자/시각을 저장한다. 시작은 1%, 완료는 100%, 수동 갱신은 진행 중인 작업의 1~99 범위에서 단조 증가만 허용하고 의뢰 진행률은 작업 평균으로 계산한다.
- 작업 행은 마우스와 Enter/Space로 선택할 수 있고, 상세에서 업무 목적·필요 입력·예상 결과, 진행률 갱신, 호환 배치 경로와 실행 기록 버튼을 제공한다.
- `작업 유형 관리`에 HyperStudy 스타일의 `배치 경로 정의` 내부 영역을 추가했다. 실행 파일, 작업 폴더, 인수 템플릿, 환경 변수, 호환 Task Type을 관리한다.
- 배치 dispatch는 Task Type 호환성을 UI와 서버에서 모두 검증하고, 프로필 snapshot과 command preview를 `DEMO_ONLY` 실행 이력에 저장한다. 실제 solver, subprocess, scheduler, 외부 네트워크는 호출하지 않는다.
- DuckDB 기준 스키마, PostgreSQL Alembic `0005`, PostgreSQL schema export, OpenAPI JSON/TypeScript 생성물을 동기화했다. 기존 PostgreSQL 업그레이드에서는 Open Cell 기준과 기본 Radioss 프로필의 `hpc-submit` 호환성을 seed한다.

### 검증 결과

- 백엔드 전체 회귀: `79 passed` (204.48초)
- 신규 판정·Run·진행률·배치 집중 테스트: `3 passed` (비호환 Task Type dispatch 409 포함)
- 프런트엔드 TypeScript 검사 및 Vite production build: 통과
- OpenAPI 생성 및 PostgreSQL schema export: 통과
- DuckDB 임시 서버 HTTP 계약: health `ok`, 배치 프로필 1개, `hpc-submit` 호환성 확인
- `git diff --check`: 내용 오류 없음(Windows CRLF 안내만 발생)

### 환경상 미실행 확인

- 인앱 브라우저는 사용자 로컬 주소 정책으로 `127.0.0.1:5173` 제어가 차단되어 실제 클릭·스크린샷·콘솔 확인을 우회 실행하지 않았다.
- 현재 `.env`는 PostgreSQL이지만 owner 자격 증명이 이 세션에 없어 실제 PostgreSQL 시작 migration/화면 확인은 실행하지 않았다. 전체 migration/schema 테스트와 DuckDB 통합 검증은 통과했다.
- 실제 Batch Runner 운영 정보가 없으므로 구현은 의도적으로 `DEMO_ONLY`까지이며, 운영 solver 실행 활성화는 별도 승인 범위다.

## 2026-08-02 — 보고서 Run·판정 기준·배치 실행 계획 검토 시작

### 담당과 시작 기록

- 계획·검토·문서화: Sol
- 구현: Luna
- 사용자 요구 3건을 현재 저장소 구조와 `docs/integrated-simulation-workbench-plan.md` 0.8에 대조하고 구현 기준 문서 작성을 시작했다.
- 기존 더티 변경은 사용자 작업으로 간주해 보존하며, Sol은 구현 코드가 아닌 사양과 작업 로그만 수정한다.

### 계획 검토 결과

- 보고서 Run은 자유 텍스트가 아니라 현재 하중 경우의 기존 AnalysisRun 드롭다운이어야 하며, 선택 Run ID를 보고서 source snapshot에 고정해야 한다.
- Chassis 관리 기준은 `CHASSIS_REAR + 영구변형 + mm`의 교집합 전체에 적용하고, Open Cell에는 `OPEN_CELL + MPa`만 대상으로 하는 별도 `Open Cell 판정 요약`과 기준을 둔다.
- 작업 행 선택과 실행은 분리한다. 행 선택은 상세만 열고, 상세의 권한·dependency·프로파일·preflight 조건을 만족할 때만 진행률 갱신/배치 실행 버튼을 활성화한다.
- `작업 유형 관리`의 새 `배치 경로 정의`는 HyperStudy의 등록형 프로파일 개념을 따르되, 임의 셸 명령 입력창으로 만들지 않는다. 불변 profile version, 허용 root, 승인 application alias, tokenized argv, 로그/입출력 규칙을 사용한다.
- 기존 완료 작업 수 기반 진행률만으로는 배치 중 진행도를 표현할 수 없어, 작업별 0~100 진행률 평균을 공통 monitoring projection의 새 정본으로 정의했다. 마이그레이션은 완료=100/그 외=0으로 기존 표시를 보존한다.
- 실제 Runner/명령/경로 정보가 없는 이번 환경에서는 profile snapshot을 포함한 DEMO_ONLY dispatch/attempt/event만 저장하고 `subprocess`, Scheduler, 외부 네트워크를 호출하지 않는 안전 경계를 유지한다.

### 산출물과 다음 단계

- 구현 기준: `docs/batch-execution-and-verdict-controls-spec.md`
- 문서에는 데이터 모델, API, UI 흐름, 권한/보안, 수용 기준, 테스트 계획, 구현 순서와 토큰 한도 중단 시 재개 기록 형식을 포함했다.
- 다음 단계는 Luna 구현 후 Sol 체크리스트 기준 계약 검토와 루트 에이전트의 통합 회귀 검증이다.

## 2026-08-02 — 상세 분석 레이아웃·보고서·버전 관리 최종 완료 기록

### 완료 범위

- 저장 완료 알림을 마지막 메시지 기준 4초 뒤 자동 종료하고 `X` 버튼으로 즉시 닫을 수 있게 했다.
- 상세 분석 레이아웃의 버전 목록, 과거 버전 초안 불러오기, 편집 후 새 버전 저장, 보호 규칙을 적용한 과거 버전 삭제를 완료했다.
- custom 분석 페이지는 별도 개수 제한 없이 생성·이름/설명 편집·위젯 자유 배치·게시·영구 삭제할 수 있다.
- Open Cell, Chassis Rear, custom, Run 비교·검토를 동일한 상세 분석 페이지 체계로 연결하고 보고서 내보내기·자연어 개선·대시보드 편집을 공통 제공한다.
- 보고서는 기본적으로 표지 뒤에 보고서 포함 위젯 결과를 한 장씩 배치하며, 사용자가 콘텐츠 포함 여부와 슬라이드·요소 위치 및 크기를 편집할 수 있다.
- 엣지 필터는 글꼴 확대 시 문구가 줄바꿈되지 않도록 내용 기반 너비와 가로 스크롤을 적용했다.
- 기존 PostgreSQL 데이터베이스에서도 누락된 Run 비교 시스템 페이지를 시작 시 멱등 보강하며, 실제 재시작 후 3개 시스템 분석 페이지 노출을 확인했다.
- 상세 페이지 전환 응답 경합, custom 미연결 위젯 보고서, Run 비교 결측 시계열, 복수 콘텐츠 슬라이드 바인딩, 삭제 fallback을 최종 감사에서 보완했다.

### 최종 검증

- 백엔드 전체 테스트: `76 passed`
- 프런트엔드 TypeScript 검사 및 Vite production build: 통과
- 프런트엔드 전체 E2E: `14 passed`
- 실제 PostgreSQL 화면: Run 비교 콘텐츠와 보고서·자연어·대시보드 편집 활성화 확인
- 브라우저 콘솔 오류: 없음
- `git diff --check`: 통과

### 비차단 참고

- pytest cache 디렉터리 ACL 경고 2건과 Vite 500 kB 초과 chunk 경고는 테스트·빌드 실패가 아니다.
- 업로드 PPTX 템플릿은 페이지별 위젯 콘텐츠 바인딩을 아직 지원하지 않아, 잘못된 결과를 만들지 않고 안내 후 중단한다. 시각적 레이아웃 내보내기는 모든 상세 분석 페이지에서 지원한다.

## 2026-08-01 — 자유형 상세분석·실제 페이지 보고서·버전 안전관리 보완

### 담당

- 계획 총괄: Sol — 무제한 custom 분석 페이지, 보고서 콘텐츠 정규화, 페이지·버전 삭제 보호 계약 보완
- 구현 및 작은 검증: Luna — 백엔드 API/트랜잭션, 실제 Run 비교 시스템 페이지, 보고서 콘텐츠/편집기, 버전 UI와 E2E 구현

### 구현 내용

- custom 분석 페이지 수와 페이지 내부 위젯 수의 별도 상한을 두지 않고, 관리자 생성·이름/설명·상태·순서·위젯 배치 흐름을 유지했다.
- 관리자가 custom 분석 페이지 이름을 다시 입력해야만 실행되는 영구 삭제를 추가했다. 버전 이력을 먼저 지우고 페이지를 지우는 트랜잭션과 시스템 페이지·하중 경우 불일치·권한 보호, 활성 페이지 삭제 뒤 안전한 fallback을 포함한다.
- `Run 비교·검토`를 `dashboard-run-comparison-default` 시스템 분석 페이지와 `run_comparison` 위젯으로 등록했다. 기존 분석 페이지와 동일하게 자연어 개선, 대시보드 편집·저장, 보고서 내보내기를 사용한다.
- 보고서 원천을 `ReportContentItem[]`으로 정규화했다. 일반 분석 페이지는 보고서 포함 위젯을 위치 순서대로 보존하고, Run 비교는 클릭 시점의 기준/대상 Run 비교·정량 차이·시계열·신뢰도·검토 의견을 고정한다.
- 기본 보고서는 표지 1장과 콘텐츠/위젯당 1장을 만들며, 기존 32×18 편집기에서 포함 여부, 대상 슬라이드, 슬라이드 순서·삭제, 요소 위치·크기를 계속 편집한다. 기존 저장 레이아웃은 읽을 수 있고 신규 콘텐츠 레이아웃의 32장 제한은 제거했다.
- DashboardVersion은 기본적으로 유효 이력만 조회하고 필요 시 무효 이력을 포함한다. 과거 정의를 live 변경 없이 현재 편집 초안으로 불러오며, 분석 페이지의 현재 ID·이름·설명·page 메타데이터와 live 버전은 보존한다.
- 개별 과거 버전 삭제는 row 삭제 대신 `is_valid=false` 논리 삭제로 구현했다. live 버전, 시스템 v1, 최소 정상 과거 이력 1개를 보호하고 새 저장 번호는 전체 이력의 최대 번호 다음 값을 사용해 삭제 번호를 재사용하지 않는다.
- 공통 notice는 마지막 메시지 기준 4초 뒤 자동 종료하고 `X`로 즉시 닫을 수 있으며, 이전 타이머가 새 알림을 지우지 않도록 중앙 effect에서 타이머를 정리한다.
- 상세 분석 EdgeFilter는 데스크톱에서 내용 너비·줄바꿈 방지·가로 스크롤을 적용하고 기존 900px 이하 숨김 동작은 유지했다.
- FastAPI 변경 계약을 기준으로 `frontend/openapi.json`과 생성 TypeScript 타입을 갱신했다.
- 최종 통합 감사에서 상세 탭 전환 요청에 순번 가드를 추가해 늦게 도착한 이전 페이지 응답이 현재 대시보드를 덮어쓰지 못하게 했다. 같은 페이지를 다시 선택하는 경우에는 불필요한 로딩 상태를 만들지 않는다.
- custom 페이지 보고서는 변수 연결이 없는 요약·메모·전용 위젯도 원본 overview로 콘텐츠를 만들고, 한 슬라이드에 여러 콘텐츠 요소를 배치해도 각 요소의 `contentId` 결과를 독립적으로 사용한다.
- Run 비교 시계열의 결측값은 0으로 바꾸지 않고 차트에서 제외하며 제외 개수를 문구로 표시한다. 콘텐츠 보고서를 지원하지 않는 업로드 PPTX 경로는 잘못된 보고서를 만들지 않고 명확한 안내로 중단한다.
- 예전 콘텐츠 모드가 없는 저장 보고서 레이아웃은 사용자가 선택하면 그대로 편집·내보낼 수 있게 유지하고, 새 기본 구성만 위젯당 1슬라이드로 만든다.
- 활성 custom 페이지 삭제 후 fallback은 현재 하중 경우에서 실제 노출 가능한 페이지로만 제한해 NO_DATA 시스템 페이지가 선택되지 않도록 했다.
- 기존 PostgreSQL 데이터베이스에도 누락된 `Run 비교·검토` 시스템 대시보드를 시작 시 한 번만 생성하는 멱등 보강을 추가했다.

### 검증 결과

- 백엔드 전체: `76 passed` (pytest cache ACL 경고 2건만 발생)
- custom 페이지/권한/삭제 rollback/보고서/버전 집중: `6 passed`
- 40개 콘텐츠 슬라이드와 버전 논리 삭제 집중: `2 passed`
- 프런트엔드 `npm.cmd run build`: TypeScript 검사와 Vite production build 통과
- 프런트엔드 전체 회귀 E2E: 편집·PPT 분리·영상 위젯 `8 passed`, 분석 페이지 생성·게시·보고서·Run 비교·버전·알림·영구 삭제 `4 passed`, 작업 실행·Viewer 권한 `2 passed` — 합계 `14 passed`
- 실제 PostgreSQL 재시작: 분석 페이지 `open_cell`, `chassis_rear`, `run_comparison` 3개 확인. Run 비교 콘텐츠 표시와 보고서·자연어·대시보드 편집 활성화, 브라우저 콘솔 오류 없음
- OpenAPI 생성과 `git diff --check`: 통과

### 남은 확인 사항

- Vite의 기존 500 kB 초과 chunk 경고와 pytest cache 쓰기 권한 경고 2건은 결과 실패가 아니며 별도 성능·환경 정리 범위다.

## 2026-08-01 — PostgreSQL 상세 분석 버튼 활성화 회귀 수정

### 원인 및 수정

- PostgreSQL 시작 경로에서 기존 시스템 대시보드에 분석 페이지 메타데이터를 채우는 초기화가 누락되어, 상세 분석 페이지 API가 빈 목록을 반환하고 `상세 분석` 버튼이 비활성화되는 문제를 수정했다.
- PostgreSQL 카탈로그 초기화 직후 `ensure_system_analysis_page_metadata`를 실행하도록 연결하고, 같은 시작 경로가 다시 누락되지 않도록 회귀 테스트를 추가했다.

### 검증 결과

- PostgreSQL 시작 경로 집중 테스트: `12 passed`
- 서버 재시작 후 `loadcase-drop-bottom-001` 분석 페이지 API: 시스템 페이지 `2개` 확인
- 실제 화면 흐름: `해석 의뢰 현황 → 상세 분석 DROP` 버튼 활성화 및 클릭 후 분석 하위 탭·위젯 캔버스 표시 확인
- 브라우저 콘솔 오류 없음

## 2026-08-01 — 사용자 정의 상세분석 페이지와 단일 보고서 선택

### 요청 및 담당

- `docs/layouts`의 압축된 참고 이미지는 외형 복제보다 기능 구성과 배치 아이디어에만 사용하고, 기존 상단 컨텍스트·12열 캔버스·대시보드 버전 구조·운영 화면을 유지했다.
- 계획 총괄: Sol — [사용자 정의 상세분석 페이지 구현 계획](../docs/custom-analysis-pages-plan.md) 작성, 권한·데이터 연결·회귀 범위 확정
- 구현 및 작은 검증: Luna — 분석 페이지 메타데이터, API, 역할 권한, 저장·복구 무결성 검증
- 통합·프런트 구현·전체 회귀 검증: Codex 루트 에이전트

### 구현 내용

- 기존 `dashboards.definition_json`에 선택형 `page` 메타데이터를 추가해 별도 테이블이나 결과 복제 없이 사용자 분석 페이지를 저장한다.
- 페이지와 페이지 내부 위젯에 제품 차원의 개수 상한을 두지 않았다. 페이지는 하중 경우에 귀속되고 프로젝트·의뢰·하중 경우 전환 시 목록·결과·변수가 함께 갱신된다.
- Open Cell과 Chassis rear 시스템 페이지는 그대로 유지하고, 게시 사용자 페이지를 동적 상세분석 탭과 전체 페이지 선택 드롭다운에 함께 표시한다.
- 관리자는 빈 초안 생성, 이름·설명 편집, 게시·게시 해제, 순서 변경, 보관·복원을 수행한다. editor는 게시 페이지 위젯만 편집하고 viewer는 게시 페이지만 조회한다.
- 기존 12열 캔버스에서 위젯 추가·삭제·드래그 이동·크기 변경·제목·종류·변수·집계·색상·글자 크기·기준선·보고서 포함 속성을 편집한다.
- 실제 분석 렌더러가 있는 공통 위젯과 Open Cell/Chassis 특화 위젯을 재사용한다. `model3d`와 `workflow`는 분석 페이지 저장 허용 목록에서 제외했다.
- 위젯 ID 중복, 좌표·크기, 12열 범위, 지원 위젯 종류를 서버에서 검증한다. 빈 페이지 게시를 막고 시스템 페이지 생명주기 변경을 보호한다.
- 일반 대시보드 저장은 페이지 메타데이터를 바꿀 수 없고, 복제본은 페이지 메타데이터를 제거하며, 버전 복구는 현재 페이지 이름·상태·순서를 보존한다.
- 상세분석의 보고서 버튼은 하나로 통합하고, 현재 하중 경우의 게시 분석 페이지 하나를 선택해 내보내도록 했다. 사용자 페이지는 `보고서 포함` 위젯의 변수 키를 기존 PPTX 데이터 흐름에 전달한다.
- 운영 대시보드의 KPI·상태·CSV·레이아웃은 변경하지 않고 동일한 프로젝트·의뢰·하중 경우·실행 결과·변수 키를 계속 공유한다.
- 로그인 전에 활성 분석 정의를 조회해 인증 오류가 남던 초기화 경합을 함께 수정했다.
- OpenAPI JSON과 TypeScript 타입을 새 API 계약으로 재생성했다.

### 검증 결과

- 백엔드 전체: `..\.venv-runtime\Scripts\python.exe -m pytest -q` → `71 passed`
- 프런트엔드 빌드: `pnpm.cmd run build` → 타입 검사 및 Vite 프로덕션 빌드 성공
- 전체 브라우저 회귀: `node scripts/run-e2e.mjs` → `12 passed`
- 분석 페이지 집중 브라우저 검증: 관리자 생성→위젯 추가·속성 편집→저장→게시→보고서 선택, Viewer 읽기 전용 → `2 passed`
- OpenAPI 생성: `pnpm.cmd run generate:api` → 성공
- `git diff --check` → 통과

### 남은 제한 및 확인 사항

- 한 번의 보고서 내보내기는 분석 페이지 하나만 지원하며 여러 페이지 합본은 이번 범위에 포함하지 않았다.
- 사용자 분석 페이지의 임의 변수를 운영 대시보드의 새 KPI 위젯으로 승격하는 기능은 추가하지 않았다. 두 화면은 기존 동일 원천 결과와 판정 집계를 공유한다.
- `model3d`는 기존 카탈로그 선언과 일반 대시보드 호환을 유지하지만 사용자 분석 페이지에서는 렌더러 완성 전까지 추가할 수 없다.
- pytest의 `.pytest_cache` 쓰기 권한 경고 2건과 Vite의 500 kB 초과 chunk 경고가 남지만 테스트·빌드 실패는 아니다.

## 2026-08-01 — 사내 PC 통합 설치 및 PostgreSQL 이관

### 요청 및 담당

- GitHub에서 프로젝트를 받은 뒤 VS Code에서 `setup.bat` 하나로 설치를 시작하도록 통합했다.
- 구현 담당: Luna
- 계획 및 안전성 검증: Sol
- 통합·테스트·최종 안전 보강: Codex 루트 에이전트

### 구현 내용

- `setup.bat`과 `setup.ps1`을 추가해 Fresh, Transfer, Keep 설치 모드를 제공했다.
- 회사 HTTP/HTTPS 프록시 URL과 `NO_PROXY`를 `.setup-proxy.env`에 저장하고, 사용자 전용 ACL과 로그 인증정보 마스킹을 적용했다.
- PostgreSQL Empty/Demo 초기화, 전송 번들 검증, 기존 DB 백업 후 스테이징 DB 교체를 구현했다.
- 기존 DB 교체는 PostgreSQL superuser 연결만 허용하며, 기존 전용 역할이 다른 DB 또는 역할 관계에서 사용되면 중단한다.
- 기존 DB 백업을 임시 검증 DB에 실제 복원하고 테이블 수, dump, assets archive와 SHA-256을 검증한다.
- 전송 번들의 UUID, Alembic revision, 파일 경로, 파일 수·용량, checksum과 관리형 assets를 검증한다.
- DB OID를 기록하고 활성 연결을 차단한 상태에서 DB명을 전환하며, assets와 환경파일 활성화 실패 시 역순 롤백한다.
- 설치·교체 준비부터 완료까지 `.setup-recovery-required.json`을 사용한다. 마커가 남으면 재설치, 실제 import와 서비스 시작을 차단한다.
- `.env`, `.postgres-owner.env`, 프록시 파일은 임시 파일에 ACL을 먼저 적용한 뒤 원자적으로 교체하며 실패한 평문 임시 파일은 제거한다.
- `README.md`와 `docs/postgresql-pc-transfer-guide.md`를 새 통합 설치 절차에 맞게 갱신했다.

### 검증 결과

- 백엔드 전체 테스트: `69 passed`
- PostgreSQL 이관·시작 집중 테스트: `28 passed`
- 프런트엔드 `pnpm run build`: 성공
- PowerShell AST 구문 검사: 통과
- Python `py_compile`: 통과
- `git diff --check`: 통과

### 남은 확인 사항

- 실제 운영 DB를 보호하기 위해 현재 PC의 기존 PostgreSQL DB에서는 컷오버를 실행하지 않았다.
- 최초 현장 적용 전 폐기 가능한 PostgreSQL 테스트 클러스터에서 Fresh, Transfer, 기존 DB 교체와 장애 주입 복구를 확인한다.
- Vite 빌드의 500 kB 초과 chunk 경고는 기존 프런트엔드 최적화 항목으로 남아 있으며 빌드 실패는 아니다.

## 2026-09-10 결과 대시보드 사용성·추천 레이아웃 상세 제안

- 사용자 요청: 위젯 UI 방식을 유지하면서 결과를 한눈에 파악하고 정보를 간결하게 표시할 개선안 정리.
- docs/dashboard-usability-and-recommended-layout-plan.md 작성, 문서 지도 연결.
- 결과 요약형·Run 비교형·영상/위치 분석형 도식, 정보 표시 규칙, 추천 미리보기/저장 흐름, 단계별 개발 범위와 수용 기준을 기록.
- Sol 읽기 전용 검수에서 snapshot 불변성, 스칼라 바인딩, video_grid 하단 배치, 반응형 및 Run 계약을 확인하여 설계에 반영.
- 문서 작업만 수행. UI·API·DB는 변경하지 않았고 브라우저 검증·사용성 목표 달성을 주장하지 않음.

## 2026-09-10 결과 분석 사용성 1차 구현

- 사용자 승인에 따라 전체 대화 결론으로 실행 계획을 다시 작성. 목표는 기능 확장이 아닌 결과 → 근거 → 비교 → 설계 판단 흐름 지원.
- Luna: 영상 탐색/부품 분리. Terra: 위젯 확대/복귀 및 의견 안내. Sol: 데이터 의미·접근성·재생 상태 검수. 루트: 요약 정확성·시각 QA 보정·통합 검증.
- 기존 WidgetCard 확대/복귀, 4개씩 영상 표시, desktop 2×2/summary 독립 스크롤, 모바일 영상 우선, 파일 정보 접기 구현.
- FAIL 집계는 기준 미충족으로 변경하고 NO_DATA/첫 실패 항목/표시 기준의 의미를 명확히 표시. 기존 의견 입력에서 사실·가설·추가 확인·설계 방향 구분 안내.
- 기존 위젯·snapshot·API·DB 계약 유지. 자동 원인 추론이나 추천 배치 엔진은 추가하지 않음.
- 타입/빌드 및 아키텍처 검사 통과. 관련 E2E 9개 통과 후 시각 보정한 영상·확대 5개 최종 재검증 통과. 기존 번들 크기 경고 유지.
- 6개 표시 초안은 실제 화면에서 잘림을 확인하여 4개 표시로 수정. 1366×768 desktop 영상 프레임 화면 내 표시와 390×844 mobile 영상 우선 배치를 캡처로 확인.
- 상세 범위와 제한: docs/dashboard-usability-and-recommended-layout-plan.md.

## 2026-09-11 영상 위젯 가로·세로 설정 완료

- 중단됐던 videoGridLayout 모듈을 연결하고 VideoGridSettings 컴포넌트 추가. 가로 열·세로 행을 개별 선택하고 기존 레이아웃 저장으로 유지한다.
- 기본 2×2, 최대 20개, 5×4 저장/새로고침 복원 확인. 기존 settings와 위젯 위치 보존을 E2E에서 비교 검증.
- Luna: renderer·배치·선택 Scene 유지. 루트: 설정 UI·호출부·문서·통합/시각 테스트. Sol: 계약 검수.
- 3행 이상 확대 화면은 내부 스크롤 적용. 좁은 카드 지표명·값 줄바꿈 보정. 모바일은 표시 열만 줄이고 저장값 유지.
- 관련 기존 영상/확대 E2E 5개 통과, 새 배치 E2E 1개 최종 재검증 통과(35.5초). 1×3/5×4 변경, 최대 개수 제한, 마지막 행 접근, 저장·복원, 모바일 보존, pageerror 없음 확인.
- 최종 build와 architecture 검사 통과. 기존 500kB 번들 경고 유지. QA 파일은 Codex visualization 작업 폴더/video-layout-qa에 보관.

## 2026-09-11 결과 분석 사용성 2단계

- 사용자 요청에 따라 기존 Run 비교 위젯에서 문제 필터 → 선택 변수 근거 → 시계열/검토 의견 흐름을 개발했다. 기존 위젯 배치와 영상 열·행 설정을 유지한다.
- Luna: ComparisonEvidenceTable의 필터·선택·단위/결측 표시. Terra: ComparisonWorkspace 분리, 근거 초안·Run 문맥·시계열 이동. Sol: 서버 단위 계약과 비동기·검토 의미 검수. 루트: 설계 문서, 통합 수정, 서버 단위 보완, 반응형/브라우저 검증.
- 근거 초안은 실제 Run·변수·수치·판정을 사용하고 원인 가설/추가 확인/설계 변경 방향을 빈 항목으로 구분한다. 기존 초안과 변수 연결을 보존하고 반복 추가를 막는다. 초안 기억은 현재 비교 화면의 Run 조합 범위이며 새로고침 영속 저장은 아니다.
- 검수에서 공통 시계열이 서로 다른 값·시간 단위도 겹치던 문제를 확인했다. SQL에서 두 Run의 모든 point 단위 일치/비누락을 확인한다. API 응답 형식·DB 스키마 변경 없음.
- 저장 대기 중 새 편집 내용을 지우지 않도록 제출 시점 객체와 비교하며, Run 전환 때 늦은 응답을 폐기한다. 조회 중에도 Run 선택기를 유지한다. 입력 명시적 aria-label과 위젯 내부 textarea ref를 적용했다.
- 백엔드 단위/비교 계약 테스트 16개 통과. 최초 HTTP 테스트는 쉘의 인증 설정 때문에 503이었으며 격리 로컬 테스트의 AUTH_MODE=disabled를 명시해 재검증했다. 제품 인증 코드는 변경하지 않았다.
- 신규 브라우저 시나리오 최종 6개 통과(1.2분). 시계열 전환 재조회 방지와 기존 필터·선택 근거 유지 포함. 기존 영상 가로/세로 설정 회귀 1개도 통과했다.
- 캡처 기반 표 여백/모바일 KPI 배치/시계열 축 잘림 보정 후 시각 회귀 1개 최종 재검증 통과(13.7초). 최종 TypeScript·빌드·아키텍처 검사(152 source files) 통과, 기존 번들 경고 유지. Sol이 시계열 요청 분리를 최종 확인했고 추가 차단 사항은 없다.
- 상세 문서: docs/dashboard-usability-and-recommended-layout-plan.md 및 docs/priority-1-run-comparison-trust-review-spec.md. QA 캡처·로그는 저장소 밖 Codex visualization 작업 폴더/comparison-qa에 보관한다.

## 2026-09-11 Run 입력 조건 비교

- 사용자 승인한 후속 1번 기능 구현. 기존 비교 위젯에 조건 차이/확인 필요 요약, 기준·대상 값/단위·출처와 전체 보기 추가. 현재 loadcase 설정은 과거 Run에 대입하지 않는다.
- Terra: Run별 실행 입력/metadata 조회, 조건 비교 정책 및 계약 테스트. Luna: 조건 패널 및 master manifest metadata 보존/제한 검증. Sol: 기록 출처·alias·누락·단위·사용자 정의 조건 검수. 루트: 통합·문서·실제 파서/DB 연계 테스트 및 브라우저 QA.
- 명시적 manifest.metadata.run_conditions를 Run 적재 metadata에 저장한다. 조건 64개/16 KiB/깊이6 제한, 임의 outer metadata 병합 없음. 두께 alias 통합, 서로 다른 물리량 분리, 출처 충돌/빈 값/단위 불일치 UNKNOWN, 사용자 정의 경로와 구조화 값 보존.
- 루트 최종 보정: 빈 객체·공백·잘못된 단위 wrapper·중첩 비유한 숫자를 UNKNOWN으로 처리하여 조건 누락/JSON 직렬화 오류 방지.
- 백엔드 35개 통과(14.18초), 브라우저 9개 통과(1.9분: 신규3 + 기존비교6). 데스크톱/모바일 캡처와 overflow/pageerror 확인. 빌드·아키텍처154files 통과, 기존 번들 경고 유지.
- 기존 Windows snapshot POSIX 권한 검사와 DuckDB fcntl 잠금 때문에 전체 master refresh 서비스 테스트는 불가했다. 제품 보안/잠금을 우회하지 않고 실제 파서→command→DB→비교 경로로 기능을 검증했다. 해당 운영 제한을 docs/run-input-condition-comparison.md에 명시했다.
- 과거 Run 조건은 소급 생성하지 않으며 새 기록부터 비교 가능하다. 사용 경로: 결과 검토 → Run 비교 → 기록된 입력 조건 비교.

## 2026-09-11 결과 분석 4단계 — 기록 기준 여유와 공통 비교 축

- 사용자 승인에 따라 기존 Run 비교 위젯에 대상 여유 요약, 기준/대상 상세 기준식·출처·충족 여부를 추가했다. 저장 판정과 기록 기준을 명확히 구분하고 서로 다른 결과에는 안내를 표시한다.
- Terra: 기록 기준 API/적재 검증. Luna: 공통 축 컴포넌트·순수 계산 모듈. Sol: 출처·경계·호환성 검수. 루트: UI/문서/통합 및 시각 QA, 초대형 숫자·연산자 형식 예외와 고립 관측점 타입 보정.
- 기존 scalar threshold는 수정 가능한 값이므로 과거 실행 기준으로 추정하지 않는다. manifest.metadata.result_criteria를 새 Run metadata에 보존하여 LT/LTE/GT/GTE/닫힌 BETWEEN 여유를 계산한다. 누락·단위 불일치·잘못된 기준은 UNKNOWN이다.
- 저장 판정 기반 필터/회귀 의미는 유지한다. 새 기록 기준 여유를 과거 PASS/FAIL에 덮어씌우지 않는다. 비율·안전율·자동 원인/위험도 순위 없음.
- 기존 두 Run의 공통 Y축을 유지하고 숫자 시간축, 공통 전체 범위/0 포함 전환, 불규칙 시간과 null 간격, 고립 관측점·단일 시점 표시를 보완했다. 비교 선의 애니메이션을 끄고 설정은 Run/변수 변경 때 초기화한다.
- 백엔드 56개 통과(27.04초). 기존 브라우저 비교/조건 9개 통과와 새 4단계 4개 최종 통과(53.2초). 최초 미기록 사유 기대 불일치는 최종 모듈 확인 및 테스트 서버 재기동 후 재검증했다. 타입 포함 빌드·아키텍처159files·10만 점 포함 축 helper 검사 통과(기존 번들 경고 유지).
- 실제 파서→DB→비교 연계로 기록 기준 변경과 과거 scalar threshold 변경 후에도 기록 기준 여유 보존을 확인했다. Windows master refresh의 기존 권한/잠금 운영 제한은 별도이며 이번 변경에서 우회하지 않았다.
- 상세 문서: docs/result-analysis-phase4.md. QA는 저장소 밖 Codex visualization 작업 폴더/phase4-qa. 남은 5단계는 대표 업무 사용 흐름 검증 후 추천 레이아웃 미리보기이며 아직 개발하지 않았다.

## 2026-09-12 결과 분석 5단계 — 추천 배치 미리보기 완료

- 사용자 요청대로 4단계 회귀 검증과 5단계 개발을 완료했다. 기존 위젯 방식 안에서 현재/추천 도식을 비교하고, 편집안에 위치만 적용한 뒤 기존 레이아웃 저장으로 확정한다.
- Terra: RGL 자체 압축 함수 기반 순수 추천 정책·자체 검사. Luna: native dialog·적용/복원·기존 grid 분리. Sol: 권한/snapshot/콜백/문맥 검수. 루트: 대표 흐름 정의·통합/브라우저 검사·화면 보정.
- 위젯 ID/타입/제목/크기/배열/설정 및 영상 5×4 보존. 요약을 앞에, 영상을 모든 비영상 뒤에 배치하며 이미 정돈된 Chassis·단일 비교 위젯은 유지한다. 잘못된 기하/순서를 유지할 수 없는 크기는 추천 적용하지 않는다.
- 미리보기 닫기는 무변경, 적용은 미저장 편집안 변경이다. 적용 이후 다른 수정이 없을 때만 적용 전 배치 복원 가능. 페이지/하중 경우/편집 세션 변경 및 조회 권한을 구분한다.
- 초기 테스트의 문법 오류와 기존 저장 경로의 빈 settings 정규화 기대값을 보정했다. 신규 E2E 3개 최종 통과(1.1분), 4단계/영상/snapshot 회귀 6개 통과. 실제 저장 후 제안 좌표·설정과 일치하고 새로고침 시 보존됨을 확인했다.
- 최종 캡처에서 투명 dialog 배경을 테마 표면색으로 수정하고 중복 설명·내부 타입명을 제거했다. 데스크톱1505×1045/모바일390×844 내부 스크롤 및 고정 버튼 확인. build·architecture164files·정책 자체 검사 통과(기존 번들 경고 유지).
- 문서: docs/result-analysis-phase5.md 및 전체 실행 계획 갱신. QA는 저장소 밖 Codex visualization 작업 폴더/phase5-qa. 1~5단계 구현 및 정의한 기능 검증 완료이며 실제 고객 효과/선호도 조사를 수행했다고 주장하지 않는다.


## 2026-09-14 사내 접속·영상 배치·와이드 화면 후속 보완

- 최종 사용자 요청을 반영해 DNS/별칭 등록 없이 컴퓨터 이름과 사내 IP의 `/home`을 사용한다. Terra가 인증된 Windows 설치의 LAN 기본값·경로·우선순위 검사를 담당했고 Sol이 권한·연결을 검수했다. 명시한 loopback 및 기존 경로 설정은 보존한다.
- 루트가 시작 창의 컴퓨터 이름/IP 주소, LAN HTTP 검사, 현재 서버 재시작·실제 두 주소 로그인 화면을 확인했다. 회사 DNS/hosts/방화벽과 계정·DB 데이터는 변경하지 않았다.
- Luna가 영상 N×M 표시와 2×2/3×2/4×3/5×4 빠른 선택을 구현했다. 루트가 결과 화면의 영상 배치 편집 버튼, 설정 버튼 텍스트, 확대 창의 1440×860 제한 제거와 와이드 수치 병렬 배치를 통합했다.
- 기존 위젯 배치·설정 저장 경로와 조회 권한을 유지한다. 2560×1440에서 5×4 영상 20개 준비 상태·전체 카드 표시, 저장/새로고침, 노트북/모바일 표시를 검증했다. 원본 영상 해상도를 높였다고 주장하지 않는다.
- Python 29개, Windows 및 LAN 프록시 자체 검사, 영상 E2E 최종 2개(38.3초) 및 추천 배치 회귀 3개 통과. 빌드·아키텍처164files 통과. 초기 전체 테스트 오실행은 인자 구분자를 바로잡아 필요한 테스트만 재실행했고, 상태 텍스트의 테스트 선택자를 보정했다.
- 상세: docs/lan-video-usability-followup.md. 실제 다른 직원 PC의 이름 확인/방화벽 통과는 미검증. 기존 미커밋 배포·계정 관련 변경은 이번 기능과 혼합해 푸시하지 않았다.

- 후속 푸시: 기존 HEAD 실행기의 사용자 지정 포트·프로세스 소유 확인·준비 상태 검사를 유지하며 `/home`·포트80 기본값과 이름/IP 표시만 선별했다. 선별 실행기는 PowerShell 구문 검사와 격리 Windows 웹 설정 검사 통과. 별도 설치·계정·오프라인 배포 작업 및 환경 백업은 커밋에서 제외했다.

## 2026-09-14 기준 정의 기반 폴더·파일 매핑 설계 — 구현 보류

- 사용자 요청: 임의 폴더를 기준 개념/실제 프로젝트에 연결하고 신규 파일 포맷을 규칙으로 저장하여 재사용한다. 현재 배포 정책과 충돌이 예상되면 상세 문서만 작성한다.
- 배포 정책 자체와 설계는 양립하지만 같은 작업 폴더에서 배포/시작 schema gate/폐쇄망 패키지가 개발 중이다. 신규 migration, 영구 상태 백업, source 멱등성 변경의 통합 충돌 가능성을 확인해 문서 단계로 한정했다.
- Astra가 설계·통합하고 Sol이 읽기 전용 배포 충돌 및 설계 검수를 담당했다. 구현 에이전트 배정은 후속 계획에만 기록했다.
- docs/semantic-storage-mapping-plan.md에 개념·대상·위치 분리, 임의 폴더 연결·상속 경계·rename/복제 처리, CSV/JSON 프로필·버전, 기존 데이터 이관, 배포/복구 계약, 단계별 담당 및 수락 시나리오를 작성했다. docs/README.md에 미구현 계획으로 링크를 추가했다.
- 이번 작업은 문서만 수정한다. 기능 코드·migration·의존성·배포 스크립트·사용자 DB·실행 중 서비스는 변경하지 않았다. 기존 및 동시 작업의 미커밋 변경을 보존한다. 문서 검증은 계획 문서의 확인 기록에 남기며 기능/실서버 검증으로 표현하지 않는다.

## 2026-09-14 읽기 레시피·결과 항목·위젯 관리자 연결 계획 확장

- 사용자 요청대로 결과 샘플의 라벨/포맷 정의부터 폴더 확장자·내용 기반 레시피 선택, 공통 결과 항목, 위젯 입력 연결까지 상세 개발 계획에 반영했다. 제안 기능 전체를 추적표와 초기/후속 단계로 연결했다.
- 레시피·결과 항목·표시 템플릿 분리, 고정 항목 ID와 관측값 grain, 다중 출력/위젯 인스턴스, 입력 역할·단위·개수·짝짓기 검증, 통합 미리보기, 활성화 원자성·영향 분석·버전/뷰 재현을 추가했다. 표시 변경은 재적재하지 않는다.
- 기존 위젯 이관, 백업/영구 상태, 모듈·API·데이터, 단계별 담당 및 수락 시나리오를 갱신했다. AI 초안·파생 연산·다중 파일 Run 묶음은 선행 계약이 필요한 후속 범위로 포함했다.
- 변경 파일: docs/semantic-storage-mapping-plan.md, docs/README.md, 이 기록. 구현·migration·배포 정책·DB·서비스는 변경하지 않았다. 독립 검수 및 문서 검사 결과는 계획 문서 12.2절에 기록한다.

## 2026-09-14 의미 결과 매핑 구현

- 버전형 결과 항목·레시피·표시 템플릿, 명시적 SPDM 상대경로 바인딩, canonical Run의 읽기 레시피/관측값 provenance를 추가했다. 원본 샘플은 DB에서 256 KiB로 제한하며 새 파일 경로/자산 저장소를 만들지 않는다.
- 의미 레시피 import는 기존 canonical result ingestion UoW의 target 검증, 권한, source-run 멱등성 및 Run ID 생성을 그대로 사용한다. 같은 recipe version/source checksum 재시도는 기존 Run을 재사용한다.
- 기존 SPDM binding과 같은 경로의 새 의미 binding은 거부하여 legacy와 명시적 수집이 한 경로를 함께 스캔하지 않는다. 폴더 조회/새로고침은 기존 SPDM root identity, reparse-point 방지와 안정 경로 검사를 재사용한다.
- PostgreSQL migration 0024는 0023 뒤의 단일 head이며, 기존 app role의 default privileges가 없는 DB를 위해 표준 simdashboard_app 역할에 CRUD를 명시적으로 재부여한다. DuckDB 개발 bootstrap과 PostgreSQL catalog gate도 동일 테이블을 확인한다.

## 2026-09-14 소스·폐쇄망 Windows 배포 계약 및 설치기 구현

- 요청: PostgreSQL만 있는 소스 PC의 원클릭 최초 설치/기존 DB 업데이트와, Windows Server 2022 x64만 있는 폐쇄망의 설치 EXE를 같은 데이터 보존 정책으로 지원한다. 이후 소스 개발에도 같은 진입점과 정책을 유지한다.
- Astra가 설계·통합, Terra가 소스 배포/영구 자산 경로/설정 동기화, Luna가 오프라인 설치기 초안, Sol이 서비스·ACL·DB 연결 검증 및 안전성 검수를 담당했다. 기존 및 동시 작업의 미커밋 변경을 보존했다.
- deploy.bat은 런타임 준비 → 안전한 DB 분기 → 검증 백업 → migration → 계정 → 실행을 수행한다. 시작은 읽기 전용 스키마 검사만 수행하며 업데이트와 설치 실패를 숨기지 않는다. 시작·종료의 PID/서비스 소유권 검증을 복구했다.
- 폐쇄망 빌더는 현재 소스, /home/ 정적 빌드, 고정 requirements.lock wheel, Python/PostgreSQL/Caddy/WinSW/VC++ 런타임과 라이선스를 묶고 ZIP/EXE/SHA-256을 생성한다. 정상 배포는 깨끗한 Git 상태를 요구한다. 현재 작업 트리는 기존 변경이 있으므로 -AllowDirty 개발 후보로 생성했다. 소스 저장만으로 서버가 바뀌지는 않으며, 매 릴리스의 새 빌드와 반입·실행이 필요하다.
- 설치기는 최초/빈 DB/기존 DB를 구분하고, 기존 상태·계정·인증 키·첨부파일을 state 아래 보존한다. 기존 서비스 및 DB 연결의 대상 불일치, URL 쿼리의 대상 우회, 변조/누락/경로 이탈, 백업 실패를 차단한다. 버전별 실행 경로와 Windows 자동 서비스를 준비한다. 기존 PostgreSQL 메이저 업그레이드는 앱 업데이트와 분리한다.
- 영구 SIMDASH_ASSETS_ROOT, 설정의 stage/sync, 비밀번호 인증용 사내 HTTP 프로필을 추가했다. HTTPS 프로필의 secure-cookie 요건은 유지했다. Inno는 64-bit install mode로 x64 PowerShell을 실행한다. 근거: https://raw.githubusercontent.com/jrsoftware/issrc/is-6_7_3/ISHelp/isxfunc.xml (Exec).
- AGENTS.md, 배포 정책·시나리오·ADR 0005·소스/폐쇄망 사용자 문서를 갱신했다. 매 push/PR에 Windows 배포 계약 및 오프라인 wheel 설치를 검사하는 CI를 추가했다. 원격 CI 자체 실행은 이 작업에서 수행하지 않았다.
- 검증: 핵심 Python 테스트 177 passed / 2 skipped, 영구 미디어/템플릿 경로 2 passed. PowerShell 배포/초기화/업데이트/프로세스 소유권/오프라인 manifest/DB 연결·서비스 검사가 통과했다. 34개 배포 PowerShell 구문과 diff 검사를 통과했다. 프런트엔드 재빌드, 새 venv의 전체 wheel 오프라인 설치·pip check·네이티브 import를 통과했다. 실제 Caddy와 가짜 API로 HTML·JS/CSS 파일·API·미디어·리다이렉트·SPA 라우팅을 검증했다.
- 산출물: output/offline-release/simworkbench-windows-offline-20260914-preview.exe (146,677,912 bytes) 및 ZIP/각 SHA-256 파일. EXE SHA-256: ff41787ce8229ac1d08a3a6d70f90dd82c2d086044d0271072306a69c9f87d9d. dev1/dev2는 이전 초안이므로 배포 후보로 사용하지 않는다.
- 런타임: Python 3.12.13, PostgreSQL 17.11, Caddy 2.11.4, WinSW 2.12.0 NET461, 공식 서명 확인한 VC++ redist, Inno Setup 6.7.3. 라이선스/런타임 파일은 패키지 manifest 해시로 추적한다.
- 한계: 실제 사용자 DB/Windows 서비스에는 설치하지 않았다. 네트워크를 차단한 깨끗한 Server 2022 VM에서 신규 설치 → 업무 데이터 생성 → 업데이트 → 재부팅 자동 시작은 미검증이다. 이 산출물은 해당 실기 수락 검증용 개발 후보이며 운영 인증 릴리스가 아니다. 관련 없는 전체 미디어/보고서 테스트의 기존 인코딩·OpenAPI·인증 실패를 이 작업의 통과 결과에 포함하지 않았다.
- 최종 패키지 확인: 설치기의 실제 manifest 검증 함수로 7,266개 파일 해시를 검사했고 현재 소스/프런트엔드 파일 542개의 일치를 확인했다. 릴리스 안에 운영 데이터/비밀 상태가 없음을 검사했다.

## 2026-09-14 의미 매핑 관리자 화면

- `FolderSchemaWorkspace`의 기존 manifest 스키마 편집기를 유지하면서 의미 매핑 탭을 추가했다. 샘플 inspect → 결과 항목/단위/차원 매핑 → 레시피·템플릿 저장/활성화 → 파싱·위젯 통합 미리보기 → 임의 SPDM 폴더 바인딩·새로고침·단일 파일 import → 저장 결과 위젯 조회 흐름을 화면에서 수행한다.
- `shared/api/semanticMapping.ts`는 기존 인증 fetch 경계를 재사용하고 multipart inspect/preview/import, 버전 충돌·권한 오류 코드, JSON 정의 export/import를 처리한다. Recharts 기반 line/scatter/bar 미리보기와 MISSING_RESULT·AMBIGUOUS_RESULT·INCOMPATIBLE_RESULT 상태를 표시한다.
- 검증: `frontend/npm.cmd run build` 통과, `git diff --check` 통과. 실제 운영 서비스나 사용자 DB는 사용하지 않았다.

## 2026-09-14 의미 매핑 기본 흐름 통합 및 배포 호환 검증

- 배포 개발 완료 후 사용자 요청으로 구현 보류를 해제했다. 완료된 배포 소스의 격리 사본에서 작업하고 기준 해시와 원본을 비교해 기능 변경만 통합했다. 배포 진입점, 설치기, 환경 설정, 사용자 DB·계정·첨부파일과 기존 미커밋 작업을 보존한다.
- Astra 설계·통합, Terra 저장/API, Luna 관리자 화면 초안, Sol 무결성·배포·화면 검수. 고정 항목 ID, 임의 폴더 연결/재연결, CSV/JSON 레시피, 단위/측정 차원/곡선, 6종 위젯, 통합 샘플 미리보기, 원자적 활성화와 버전 충돌, 실행별 스냅샷·중복 방지, 정의 초안 반입/내보내기를 연결했다.
- PostgreSQL 0024는 `SIM_DASH_APP_ROLE`의 사용자 지정 역할에도 새 테이블 CRUD와 기존 SPDM 바인딩 잠금 권한을 부여한다. 의미 매핑과 기존 SPDM은 같은 순서의 트랜잭션 잠금과 상호 경로 소유 검사를 사용한다. 앱 시작 시 DDL 금지와 기존 배포 migration 계약을 유지한다.
- 기존 결과 화면에 선택 Run의 표시 템플릿을 연결했다. 관리자 조회의 늦은 응답/이전 대상 표시와 최신 초안·활성 버전 혼동을 Sol 검수 후 수정했다. 결과 조회/폴더 연결 화면을 독립 컴포넌트로 분리했다. OpenAPI 생성기는 Windows에서도 LF를 저장하여 계약 검사를 재현한다.
- 검사: 새 기능 46개, 기존 ingestion 계약·멱등성 12개, 배포 관련 206개 통과/5개 건너뜀. Playwright 4개 통과(실제 관리자 흐름, 실제 viewer 권한, 6종 위젯, 지연 조회 응답 회귀). TypeScript·프런트엔드 아키텍처·정적 빌드 통과, 6종 렌더링 화면을 확인했다. 격리 사본 Vite는 의존성 junction 제약 때문에 runner config loader를 사용했으며 배포 실행기는 수정하지 않았다.
- 임시 PostgreSQL에서 신규/기존 0023→0024/반복 migration, 기존 업무 데이터 보존, 기본·사용자 지정 앱 역할 CRUD와 DDL 차단·소유권 잠금, dump/restore 후 정의 버전·샘플·원본 해시·Run 복원을 확인했다. 실제 운영 데이터나 서비스를 사용하지 않았다.
- 전체 저장소 테스트 통과를 주장하지 않는다. 확대 검사에서 기존 POSIX 파일 권한의 Windows 전제와 기존 viewer fixture 설정 실패가 남았으며 이번 기능 검사와 구분한다. Server 2022 폐쇄망 VM 실기와 새 설치 EXE 제작·배포는 수행하지 않았다. 이전 오프라인 EXE에는 이번 기능이 없으므로 기존 정책대로 다음 릴리스를 새로 빌드해야 한다.
- `docs/semantic-storage-mapping-plan.md` 12.3과 `docs/semantic-mapping-guide.md`, `examples/semantic-mapping/`에 구현/미구현 범위, 사용법, CSV·JSON·정의 패키지 예제를 기록했다. 범용 별칭·규칙 검토함/영향 분석/이동 자동 추적과 선택 AI·파생 계산·다중 파일 조인은 후속이며 전체 온톨로지 계획 완료로 표시하지 않는다.

## 2026-09-15 기준 정의·별칭 사전 구현

- 사용자 요청 1번을 구현했다. 고정 키·표시명·설명·별칭을 폴더 역할/프로젝트/의뢰/하중 경우/결과 항목의 실제 ID에 연결하고 전역/프로젝트별로 재사용한다. 정확 정규화 검색 후 관리자가 폴더 또는 원본 필드에 후보를 명시적으로 적용한다.
- Astra가 설계·통합, 독립 Terra가 백엔드·DB, Luna가 프런트 구현을 담당했다. Sol 1차 및 별도 Astra 최종 검수를 거쳤다. 사용자 상태 없는 격리 소스 사본에서 검증하고 원본 해시를 대조하여 기능 변경만 통합했다.
- 사전 API/domain과 별도 사전 탭/후보 컴포넌트를 추가했다. scope 소속 검증, 용어 유일성, create/update 공통 PG 잠금, revision CAS, 감사 원자성, 비활성/삭제 대상 처리, 입력 상한을 적용했다. 후보 적용 직전 ID+revision을 재확인하며 화면 전환/행 교체 후 오래된 응답을 무시한다. 기존 레시피·폴더 연결·과거 실행은 별칭 변경으로 수정되지 않는다.
- 0024 다음 additive 0025 migration과 최신 PostgreSQL schema/DuckDB bootstrap/schema gate를 연결했다. 최초 설치와 기존 업데이트의 CHECK/FK/길이/컬럼을 일치시켰다. 기존 배포 진입점·앱 시작 DDL 금지·custom app role·영구 상태 보존을 유지하고 의존성/외부 서비스는 추가하지 않았다.
- 기능/매핑 회귀 63개 및 추가 NUL/제어문자 경계 1개 통과. 배포 회귀 206개 통과/5개 건너뜀. 임시 PostgreSQL 신규/0024 업데이트/반복/스키마 제약·컬럼 비교, 기존 데이터 보존, 실제 API 동시 용어 충돌/CAS/감사 rollback, 기본·사용자 지정 앱 역할 CRUD·DDL 차단, dump/restore를 통과했다.
- 실제 API Playwright 신규 5개와 기존 의미 매핑 4개 통과. TypeScript·architecture·정적 빌드 및 Windows 업데이트/오프라인 설치기/manifest/서비스 소유권 자체 검사 통과. 표의 table-cell 정렬을 시각 검수 후 수정했다. 검사 사본의 임시 Vite 설정과 테스트 DB/산출물은 기능 소스에 포함하지 않는다.
- 전체 저장소/실제 Server 2022 VM 실기 통과를 주장하지 않는다. 새 설치 패키지 제작·운영 배포·실제 사용자 DB/서비스 변경은 수행하지 않았다. 기존 미커밋 변경을 보존한다. 사용법/한도/검증은 docs/semantic-vocabulary.md, 전체 범위는 계획 12.4절에 기록했다.

## 2026-09-15 선행 기능 커밋과 후속 운영 기능 설계

- 사용자 요청에 따라 검증된 의미 매핑·별칭 기능 55개 파일을 `133e821`에 커밋했다. 기존의 무관한 미커밋 변경은 제외했다. 원격 목적지 `wgcha/simdashboard`, 브랜치 `codex/windows-one-click-deploy` 푸시는 자동 승인 검토가 목적지 승인을 요구하여 실행되지 않았다. 사용자에게 구체적인 목적지 승인 요청을 전달했다.
- Astra가 다음 단계 설계를 정리하고 Terra/Luna가 백엔드·화면의 기존 경계와 인터페이스를 읽기 전용으로 검토했다. `docs/semantic-review-impact-plan.md`에 영향 분석, 미처리 검토함, 명시적 재검증·등록, 원자성·권한·배포 보존과 검증 조건을 기록했다. 선행 푸시 완료 전 후속 기능 코드는 변경하지 않았다.
- 독립 Sol 1차 및 독립 Astra 최종 문서 검수에서 차단 사항이 없음을 확인했다. 계획 링크·후행 공백과 `git diff --check`를 확인했다. 이번 후속 작업은 설계 문서만 변경했으므로 기능 테스트나 DB migration을 실행하지 않았다.

## 2026-09-15 푸시 승인과 후속 구현 착수

- 사용자의 명시적 승인 후 `133e8218519ac5b70a1c525b7926c444c7cb78ea`를 `wgcha/simdashboard`의 `codex/windows-one-click-deploy`에 푸시하고 원격 해시를 확인했다. 이전 승인 차단은 해소됐다.
- 사용자 상태를 제외한 1,077개 소스 파일의 격리 사본에서 후속 구현을 시작했다. Terra 검토함/DB, 별도 Terra 영향 분석/활성화, Luna 관리자 화면을 독립 배정하고 Astra가 API·동시성·배포 호환 검증을 조율한다.

## 2026-09-15 변경 영향 분석·파일 검토함 구현과 검증

- 저장된 샘플과 실제 연결 조합으로 레시피/템플릿 활성화 영향을 조회한다. 호환성 실패·샘플 없음·한도 초과는 활성화를 차단하고, 활성화 트랜잭션에서 바인딩과 포인터를 다시 잠금·검증한다. 기존 Run은 변경하지 않는다.
- 미인식·다중 일치·검증 실패 파일을 폴더별 검토함에 저장한다. 재검증에서 원본 해시와 레시피/템플릿 버전을 고정하며 명시적 확정에서 Run/provenance/감사/상태 전이를 원자 저장한다. 완료 항목의 명시적 다시 검토와 버전별 이전 Run 관계, 페이지·이력 한도, 화면 문맥 변경 시 늦은 응답 차단을 추가했다.
- 0026 migration은 검토 항목·이력 두 테이블을 추가한다. 유일 키는 바인딩/하중 경우/상대 경로다. 재연결 시 새 대상 항목을 분리하여 과거 대상 권한을 우회하지 않으며, 오래된 요청이 새 개정을 STALE로 만들지 않도록 CAS를 우선한다. 일시 읽기 실패·활성 레시피 변경만으로 완료 상태를 재개하지 않는다. Sol 및 독립 Astra 검수에서 발견한 권한·동시성 경계를 수정했다.
- 검토함 최종 API 15개 통과(110.96초). 기존 의미 매핑 INVALID 반복 새로고침 회귀 1개 재검증 통과, UTF-8 환경의 기존 ingestion 경계 5개 통과. 기존 엔진/API/사전/활성화/영향/SPDM/ingestion 회귀와 최종 신규 검사를 확인했다. 전체 저장소 통과로 표현하지 않는다.
- 폐기 가능한 native PostgreSQL에서 신규/데이터 있는 0025→0026/반복 migration, 컬럼·CHECK·FK·인덱스 일치, 기본·사용자 지정 앱 역할 CRUD와 DDL 차단, 동시 새로고침/확정, 감사 실패 rollback, v3 재처리의 과거 값 43·새 값 43000과 이전/새 Run 관계, dump/restore, 빈 검증 DB downgrade/re-upgrade를 통과했다. 실행한 임시 클러스터는 종료했다.
- 배포 회귀 206개 통과·5개 건너뜀, 영구 경로 검사 2개 통과, PowerShell 자체 검사 8종·구문 검사 34개 통과. 신규 의존성·영구 경로·배포 진입점 변경은 없다. OpenAPI 계약·TypeScript·프런트 아키텍처(184개 소스)·정적 빌드를 통과했다.
- 실제 API Playwright 신규 2개와 기존 의미 매핑·사전 9개 시나리오를 통과했다. 원래 Run 화면의 초기 대기 시간 초과는 해당 시나리오를 단독 재실행하여 통과를 확인했다. 검토함 이력 필드 표시를 수정하고 영향 확인의 데스크톱/모바일 표시·버튼 위치를 검수했다. 검사 사본의 Vite 설정·실행기 수정·테스트 DB·산출물은 기능 소스에서 제외한다.
- 중요한 지원 한도와 프로젝트 통합 검토함·오류 필터 등 후속 범위는 docs/semantic-review-guide.md와 전체 계획 12.5절에 기록했다. 실제 사용자 DB·서비스, 새 EXE 제작·운영 배포, 실제 폐쇄망 Server 2022 설치·업데이트·재부팅은 수행하지 않았다.
- 독립 Sol 재검수와 Astra 최종 기능·코드 승인을 받았다. 마지막 CSS 수정 후 실제 운영 브라우저 시나리오를 다시 통과했고(43.0초), 1280×720/390×844에서 긴 식별자 줄바꿈·본문 스크롤·고정 확인 버튼을 확인했다. 31개 기능 소스·문서의 해시를 대조하여 통합하며 무관한 기존 변경과 사용자 상태는 보존한다. 이번 후속 구현은 로컬 반영이며 원격에 푸시된 선행 커밋은 `133e821`이다.

## 2026-09-15 사내 HTTP 작업대 UUID 오류 수정

- 사용자 오류 화면의 `crypto.randomUUID is not a function`을 확인했다. 사내 IP HTTP는 secure context가 아니어서 해당 API가 없는데, 작업대의 배치 요청 키 초기화에서 직접 호출하여 라우트가 중단됐다.
- 공통 `frontend/src/shared/identity/clientId.ts`에서 native UUID API가 있으면 사용하고, 없으면 `crypto.getRandomValues`의 난수 16바이트로 UUID v4를 생성한다. 시간·Math.random 기반 대체를 사용하지 않는다. 작업대 3곳, 진행 단계 추가 2곳, 로컬 실행 요청 키, 의미 매핑 위젯의 7개 호출을 전환하며 접두사·키 생성 시점·재시도 수명을 유지했다.
- Astra 설계, Luna 구현, Terra 회귀 검사, Sol 1차·별도 Astra 코드 검수를 완료했다. UUID 자체 검사는 native/fallback receiver, version/variant, 실제 Web Crypto 난수 중복·형식, 난수 API 부재 시 명확한 오류와 직접 호출 재유입을 검사한다.
- Browser 플러그인이 없어 저장소 Playwright 실행기를 사용했다. 사용자 DB·설정·실행 중 서비스 대신 격리 소스·임시 DB·별도 포트에서 검증했다. 실제 `http://192.168.45.146:15173`의 `isSecureContext=false`, native randomUUID 없음 상태에서 로그인→작업대→다른 작업 선택과 클라이언트/로컬 요청 UUID 생성을 검증했다. 페이지 제목·URL·화면·콘솔/pageerror·Vite overlay 검사 및 1280×720/390×844 화면 캡처를 통과했다(1개 시나리오, 7.7초). 초기 실패는 임베디드 화면과 맞지 않는 새 테스트의 제목 선택자를 수정한 뒤 통과했다.
- TypeScript·프런트 아키텍처(185개 소스)·정적 빌드(6.53초), 프로젝트 Node 22의 UUID/공유 API 자체 검사를 통과했다. 기존 LAN 프록시 검사는 제한된 실행 환경에서 LAN 연결이 막혀, 임시 서버만 사용하는 허용된 권한으로 재실행하여 `/home/`·루트 경로와 loopback API 프록시를 통과했다. 시스템 Node 26에서 제거된 transform-types 옵션 오류는 프로젝트 제공 Node 22로 검사했다.
- 새 의존성·DB migration·배포 진입점·인증/HTTPS 정책 변경은 없다. 기존 미커밋 변경을 보존했다. 사용자 서비스 재시작·새 배포 패키지 제작·실제 Server 2022 설치는 수행하지 않았다. QA 산출물은 저장소 밖의 세션별 http-uuid 디렉터리에 보관한다.

## 2026-09-15 전역 테마와 결과 위젯 색상 불일치 수정

- 전역 `theme.css` 토큰이 있어도 확대 버튼의 고정 어두운 배경, 레거시 라이트 모드의 `!important` 글자 규칙, 늦게 로드되는 의미 매핑 CSS의 공통 버튼 선택자가 함께 작용하여 색상이 섞였다. 위젯 확대/복귀 버튼·포커스·가림막과 기본 차트 색, 의미 매핑·별칭·검토함·영향 확인·의미 결과 카드의 색을 전역 역할 토큰에 연결했다. 공통 버튼 규칙은 해당 기능 화면/대화상자로 범위를 제한했다.
- 라이트 상태 토큰의 글자 대비를 보정하고 사용자가 저장한 차트 색상은 유지했다. 구조·모바일 규칙 누락을 검수에서 복원했다. Astra 설계, Luna 위젯 구현, Terra 의미 화면 구현, Sol 1차 및 독립 Astra 최종 검수를 완료했다. 전역 규칙과 새 검사 위치를 `docs/development-workflow.md`에 기록했다. 모든 레거시 화면의 고정 색상 제거를 완료한 것은 아니다.
- 사용자 상태 없는 격리 사본·임시 DB·별도 LAN HTTP 포트에서 Playwright 테마 2개 시나리오를 통과했다. 양 모드의 실제 토큰 색/일반·hover·focus 버튼 대비 4.5 이상, 확대·Escape 포커스 복귀, lazy 화면 방문 뒤 색상, 새로고침 후 테마, 390×844 모바일, pageerror/개발 오류 overlay와 화면 캡처를 확인했다. 기존 위젯 DOM 내용 보존 회귀 1개와 의미 검토·명시적 등록·영향 확인 운영 시나리오 1개도 각각 통과했다.
- 의미 등록 시나리오는 최신 Run과 기본 결과 화면을 바꾸므로 테마/위젯 검사를 같은 DB에서 뒤이어 실행하면 위젯 선택이 실패했다. 테마 검사는 새 seed DB의 독립 실행으로 검증했다(`node scripts/run-e2e.mjs theme-contract.spec.ts`). 초기 선택자 순서·불필요 탭 선택 및 모달 표면 기대값도 보정했다. 전체 조합 실행 통과를 주장하지 않는다.
- TypeScript·아키텍처 검사(185개 소스)·Vite 정적 빌드와 변경 파일 공백 검사를 통과했다. 격리 사본의 node_modules junction 임시 config 쓰기 제한은 Vite `--configLoader runner`로 검증했다. QA 실행기·설정·DB·이미지는 기능 소스에 포함하지 않는다. 새 의존성·DB·배포 진입점 변경, 사용자 서비스 재시작·운영 배포·Server 2022 실기는 수행하지 않았다. 기존 미커밋 변경을 보존했다.

## 2026-09-15 사내 테스트용 커밋 범위 확정

- 사용자 요청에 따라 완료·검수된 의미 검토/영향 분석 기능, HTTP UUID 호환 수정, 전역 테마 수정과 관련 검사·문서를 `codex/windows-one-click-deploy`에 커밋·푸시한다. 별도 진행 중인 설치/배포 스크립트 변경과 로컬 설정·DB·QA 산출물은 제외한다. 문서 지도는 신규 기능 링크만 포함하고 무관한 경로 변경은 보존한다.
- 사내 소스 PC는 기존 `update.bat`의 백업·0026 migration·프런트엔드 빌드 절차로 갱신한다. 새 오프라인 설치 패키지 제작이나 사내/Server 2022 실기 완료를 뜻하지 않는다.

## 2026-09-15 승인 개발분 main 통합 준비

- 사용자 요청에 따라 최신 원격 브랜치와 승인·검수 기록을 대조했다. `origin/main` 이후 완료 개발은 `codex/windows-one-click-deploy`의 `0f8407b5`부터 `b8b00e23`까지 20개 커밋에 모여 있다. 다른 일반 브랜치는 이미 main에 포함되어 있고, 과거 백업 브랜치의 변경은 patch-equivalent여서 추가 병합하지 않는다.
- 기존 작업 폴더의 미커밋 설치/배포 변경 12개 파일과 사용자 상태를 보존하고, 승인된 커밋의 별도 worktree에서 통합을 준비했다. 계정·도우미·VOC·분석/영상·Windows 배포·의미 매핑/사전/검토·HTTP UUID/테마의 기존 승인 범위가 대상이다.
- 원격 CI의 YAML plain scalar 안 `--only-binary=:all:` 파싱 문제를 block scalar로 고쳤다. Windows .NET의 loopback 프록시 우회를 고려하여 프록시 대조군을 문서용 주소로 변경하되 강제 프록시는 닫힌 loopback 포트를 유지한다. 배포 smoke의 deep route는 시작 스크립트 기본 base `/home/`와 일치시켰다.
- 격리 소스에서 배포 Python 검사 177개 통과/2개 건너뜀, 영구 경로 검사 2개 통과. PowerShell 배포 계약 자체 검사 8종, Git update/bootstrap/integration 검사와 수정한 local HTTP 검사를 통과했다. 최초 격리 사본의 런타임 부재 실패는 준비된 Python 참조 후 재검증했다.
- 이 기록 시점에는 GitHub PR CI 재검증과 main 병합이 남아 있다. 최종 원격 검증·병합 결과는 통합 PR에 기록한다. 실제 사용자 DB/서비스 및 폐쇄망 Server 2022 신규 설치·업데이트·재부팅을 실행한 것이 아니다.
### main 통합 CI 후속 복구

- 최초 PR CI에서 Windows setup-python manifest의 고정 Python 미지원, 영문 Windows의 한글 fixture 경로 ANSI 손상, 비밀번호 권한 계약 테스트의 초기 관리자 전제 누락을 확인했다.
- Windows 배포 계약은 기존 setup.ps1로 정확한 프로젝트 runtime을 준비하고 wheel 수집 전에 표준 ensurepip로 pip를 복구한다. 오프라인 설치 검사는 별도 venv와 --no-index를 유지한다. 업데이트 fixture JSON 입출력을 UTF-8로 명시했다.
- 6개 권한 테스트에만 독립 활성 관리자 fixture를 주입해 초기 설정이 완료된 상태의 권한/강등 계약을 검사한다. 운영 bootstrap guard와 bootstrap 전용 테스트에는 적용하지 않는다. 관련 계약 25개 통과, 업데이트 bootstrap/integration PowerShell 검사 통과. Sol 1차 및 독립 Astra 최종 검수 승인.
- SQL 경계와 스키마 생성기 관련 CI 실패는 별도 수정·검증 중이며 이번 부분 커밋에 포함하지 않는다.
### main 통합 backend·배포 계약 복구

- 새 네 router의 직접 SQL을 기능별 repository로 옮겼다. 기존 connection, transaction, CAS, PostgreSQL 잠금, 권한 확인 및 감사 rollback 순서를 유지하며, 검토함의 조건별 SQL과 권한 필터 페이지 진행도 repository 경계로 정리했다. 아키텍처 baseline을 늘리거나 검사를 우회하지 않았다.
- semantic API 31개, 계정 API/bootstrap 6개, 검토함 페이지·사전 focused 회귀 7개 통과. architecture 및 OpenAPI 검사 통과. Win32에서 전체 unit 실행은 666개 통과/50개 건너뜀/66개 실패였으며, POSIX 전용 기능의 Windows 미지원 실패와 오래된 migration head 기대값 실패를 확인했다. migration 순서 검사의 head를 현재 0026에 맞췄고, Linux 전체 검증은 원격 CI에서 확인한다.
- PostgreSQL 계약 테스트의 본문은 통과했지만 fixture 정리에서 감사 테이블 DELETE 권한 오류가 났다. 감사 삭제가 허용되지 않는 app 역할을 유지하고 기존 테스트 패턴대로 DuckDB에서만 해당 정리를 수행한다.
- PostgreSQL semantic bootstrap DDL을 독립 생성 원본에 연결하여 schema.sql 재생성 누락을 복구했다. portability 4개 통과 및 재생성 무diff를 확인했다. schema와 migration 자체는 변경하지 않았으며 기존 canonical과 migration의 모든 제약 일치를 확인한 것으로 기록하지 않는다.
- 영문 Windows PowerShell 5.1이 BOM 없는 deploy.ps1의 한글을 ANSI로 오해석했다. 파일 본문은 유지하고 UTF-8 BOM만 추가했다. 전체 배포 PowerShell ParseFile 및 account lifecycle 검사를 통과했고, 비ASCII 추적 PS 파일의 BOM 누락이 없음을 확인했다.
- 원격에서 Windows x64 잠금 의존성의 wheel 수집·별도 환경 오프라인 설치 검사가 통과했다. 원본 작업 폴더의 미커밋 12개 파일 SHA-256도 작업 전과 동일함을 재확인했다. 실제 사용자 DB/서비스와 Server 2022 폐쇄망 실기는 변경하거나 실행하지 않았다.
### main 통합 원격 검증과 마지막 CI 호스트 보정

- ed568536 기준 원격 PostgreSQL 18 통합, Windows VM 계약, 프런트 빌드, Windows 원클릭 설치·서비스 lifecycle smoke가 통과했다. 잠금 의존성의 Windows x64 오프라인 설치도 통과했다.
- Linux unit 780개 통과/1개 건너뜀에서 실패한 Windows OSError 생성자 fixture를 명시적 winerror=33 오류 객체로 바꿨다. 잠금 충돌과 ACL 오류 구분은 유지하며 해당 검사 13개 통과.
- 배포 계약 CI는 자체 검사가 성공한 뒤에도 의도된 실패 시나리오의 LASTEXITCODE가 호스트에 남아 실패로 판정했다. 8개 PowerShell 자체 검사를 각각 독립 child 프로세스로 실행하고 실제 종료 코드를 즉시 확인한다. 기존 검사 내용은 유지하며 실제 throw의 실패 전파를 확인했다. Sol 및 독립 Astra 검수 승인.
- 브라우저 전체 회귀는 아직 진행 중이며, 오래된 커밋의 중복 실행은 새 커밋 실행으로 대체하고 부분 로그·화면 증거를 확보했다. 최종 head 전체 CI 통과나 main 병합 완료를 이 중간 기록으로 주장하지 않는다.
### main 통합 화면 회귀 보완

- Browser plugin not available: frontend-testing-debugging 스킬의 Playwright 경로로 사용자 상태 없는 worktree, 임시 DuckDB 및 15173/18000 전용 포트에서 검증했다. 공유 의존성 캐시 충돌과 폰트 접근은 QA 전용 Vite cache/fs.allow로 격리했으며 이 설정은 커밋에서 제외한다.
- 모바일 회원가입 카드에 viewport 폭 상한을 적용했다. 실제 폰트 로드 상태의 390×844 화면을 직접 확인했고, 가입 전체 흐름 및 access-admin-refresh 2개 시나리오가 통과했다. CI에서 화면 복원이 5초 넘게 걸린 권한 새로고침 검사는 동일 관리자 표시를 최대 15초 기다리며 API·권한 검증을 유지한다.
- password 로그인 표제, 새 의미/스키마 탭, 영상의 명시적인 업무 문맥, 도우미 identity mock과 인증 전환 fixture를 현재 화면에 맞췄다. Linux 모델링 파일 검사는 E2E runner가 선택한 Python을 그대로 전달한다. 검사 assertion을 삭제하거나 건너뛰지 않았다.
- 분석 페이지·편집·빈 초기 화면·영상·HTTP UUID·라이트 테마·모델링 템플릿·개인 PC 안내·의뢰 문맥의 관련 35개 시나리오를 함께 실행해 전부 통과했다(7.4분). 기존 의뢰 미구성 결과 시나리오도 수정 없이 이 묶음에서 통과했다. 앞선 보조 Windows 전체 contract 실행은 원격 CI와 중복되어 완료 전 중단했으며 전체 통과로 기록하지 않는다.
- 전체 E2E는 현재 flat 36개 spec 파일을 한 DB에서 실행하고 있었다. 의미 검토의 실제 결과 등록이 뒤의 테마/위젯 seed 전제를 바꾸는 문제는 기존 작업 기록에도 있으므로, 기본 전체 실행을 spec별 새 DB·server lifecycle로 격리했다. 같은 spec 안의 시나리오와 명시적인 focused 인자는 유지한다.
- 의미 검토·테마·위젯을 각각 새 DB로 실행해 브라우저 시나리오 6개를 통과했다. 첫 실행에서는 Windows 종료 관측 timeout이 있어 전체 실행 성공으로 취급하지 않았고, 종료 관측 보완 뒤 테마 2개와 runner 종료 코드 0을 재확인했다. 전용 15173/18000 포트 및 해당 worktree의 Node/Python 프로세스가 남지 않음을 확인했다.
- E2E 실행기 자체 검사는 실제 Node child의 앞·중간·마지막 실패에도 36개 spec이 모두 실행된 뒤 실패를 집계하고, 빈 발견은 실패하는 것을 검증한다. 이 자체 검사를 CI에 연결했다. 서비스 시작 전 전용 포트 점검, Vite strictPort, 스펙별 증거 보존과 종료 실패 보고를 추가했다.
- 시스템 health 계약의 바로 앞 router 기대값을 현재 의미 사전 등록 순서로 맞췄고, 사용자 승인 권한 검사에 opt-in 초기 관리자 fixture를 적용했다. 관련 focused 7개 통과(17개 선택 제외). 운영 권한과 router 동작은 변경하지 않았다.
- Sol 1차 및 독립 Astra 최종 검수를 완료했다. 최종 검수에서 발견한 POSIX 상위 실행기의 종료 대기 부족을 보완하여 내부 서버 정리 예산보다 긴 40초를 제공한다. POSIX SIGTERM 자체 검사를 CI에 추가했으며, Windows 로컬에서는 해당 POSIX 분기를 실행한 것으로 기록하지 않는다. QA 전용 Vite 설정과 원본 미커밋 12개 파일은 통합 커밋에서 제외한다.
