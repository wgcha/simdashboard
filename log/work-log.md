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
### main 통합 작업 문맥·방문 기록 회귀 수정

- 별도 새 DB 검증에서 결과 검토 이동 후 의뢰 개요로 돌아가는 경우와 도움말에서 뒤로 가도 같은 화면에 남는 경우를 재현했다. 목적 화면 이동에 목표 문맥을 함께 전달하고, 미지정 Run·페이지 query는 지우되 다른 URL query는 보존한다. 비의뢰 화면의 자동 문맥 갱신은 방문 기록을 교체한다.
- 기존 E2E assertion은 유지했다. 수정 후 통합 작업공간 9개 전부와 라우팅 4개가 통과했지만, 결과 등록으로 이동하는 별도 경로가 한 번 실패했다. 해당 경로는 단독 새 DB 재실행에서 통과했다. 이 중간 결과를 전체 통과로 취급하지 않으며 두 파일 전체를 재검증한다.
- Sol 및 독립 Astra가 최종 코드와 알려진 query 제거 보완을 승인했다. 실제 사용자 상태·배포 계약·의존성은 변경하지 않는다.
- 두 파일 전체 재검증에서 브라우저 시나리오 14개가 모두 통과했다(3.9분). 이 Windows sandbox 실행은 검사 후 종료 관측 오류로 runner가 1을 반환했으므로 브라우저 assertion 통과와 프로세스 종료 결과를 구분한다. 같은 실행기의 별도 승인된 실행에서는 정상 종료와 전용 포트 해제를 이미 확인했으며, 회원가입 후속 검증에서도 종료 코드를 확인한다.
- 98b1a1ac 원격 CI에서 E2E 36개 spec 중 35개가 통과했다. 남은 회원가입 390px 실패 artifact는 카드 내부 input의 최소 콘텐츠 폭이 grid를 늘려 버튼·본문까지 넘치는 모습이었다. 내부 grid 요소와 입력란의 최소 폭을 0으로 하고 입력 폭을 카드에 맞췄다. 기존 폭 assertion을 유지한다.
- 같은 원격 CI의 DuckDB 회귀는 658개 통과/1개 건너뜀/9개 실패였다. 실패 8개의 opt-in 초기 관리자 fixture 누락과 1개의 오래된 migration head 기대값만 보완한다. 단위·API 계약·PostgreSQL 18·Windows VM 및 배포 검사는 해당 커밋에서 통과했다.
### 2026-09-15 사용자 요청에 따른 최신본 main 통합

- 사용자가 추가 검수가 길어졌음을 지적하고 최신 개발본의 main 통합을 우선하도록 요청했다. 추가 CI 복구 범위 확장을 중단하고 완료된 개발·수정 내용을 병합한다. 최종 전체 CI 재통과를 병합 전제로 주장하지 않는다.
- 마지막 backend fixture 8건·migration 기대값 1건 및 모바일 입력 최소 폭 수정은 Sol 코드 검수와 지휘자의 변경 확인을 완료했다. 해당 후속 focused 실행과 독립 Astra 추가 검수는 에이전트 사용량 제한으로 완료 결과를 받지 못했으므로 통과로 기록하지 않는다. 앞선 navigation 독립 Astra 검수와 14개 브라우저 assertion 통과 기록은 유지한다.
- 직전 원격 CI 실패(backend 9개, 회원가입 모바일 1개)에 대한 수정은 포함하지만 최종 수정본의 전체 재검증은 남아 있다. QA 전용 Vite 설정은 복원했고 원본 미커밋 파일과 사용자 상태는 보존한다.

### 2026-09-15 폴더 전체 조사·규칙 기반 업무 생성 핵심 구현

- 사용자 요청대로 예제/기존 프로젝트 선택 없이 서버 저장소 최상위 폴더를 조사하고, 깊이·이름 시작 조건·구분자 토큰으로 프로젝트/의뢰/하중 경우의 번호와 이름을 추출하는 관리자 화면을 추가했다. 예제 데이터는 결과 표시용으로 유지한다. 규칙 저장·불러오기, 생성 미리보기, 명시적 적용과 재적용을 지원한다.
- 전체 탐색은 폴더/파일 개수와 확장자를 조사한다. 접근 실패·링크·한도 초과는 INCOMPLETE로 표시하고 적용을 막는다. 적용 전후 폴더 구조와 저장소 identity를 재검사한다. 번호는 문자열로 보존하고 DB 내부 ID 및 부모 연결과 분리한다. 중복 번호, 정규화 경로 충돌, 부모 누락과 변경된 기존 업무는 충돌로 처리한다.
- 적용은 업무 생성·폴더 registry·감사·확정 결과를 하나의 transaction으로 저장한다. 동일 미리보기 재적용은 저장된 결과를 반환한다. 저장 규칙 revision과 기존 연결 상태를 재검증한다. 기존 SPDM 자동 탐색도 부모 생성 전부터 새 폴더 소유 영역을 존중한다.
- migration 0027은 새 테이블만 추가한다. 신규 설치가 최신 schema.sql을 먼저 읽는 구조에 맞춰 IF NOT EXISTS를 적용하고 schema exporter를 함께 갱신했다. 런타임 PostgreSQL은 테이블/열 검사만 수행한다. deploy.bat/update.bat, 배포 정책, 의존성 및 사용자 DB/설정/서비스는 변경하지 않는다.
- 독립 Sol 1차 및 독립 최종 검수에서 부모 연결, 경로 정규화, 원자적 적용, 선택창 취소 시 조사 대상 유지 문제를 검토·수정했다. frontend-testing-debugging/react-best-practices를 적용했다. Browser plugin이 없어 기존 Playwright runner와 사용자 상태 없는 소스 사본/전용 DB·포트를 사용한다.
- 검증: 임시 PostgreSQL에서 기존 0026→0027과 빈 DB→head 적용, 두 DB의 신규 열·제약 동일성, 기존 프로젝트 보존, 사용자 지정 app role CRUD grants를 확인했다. 실제 PostgreSQL에서 프로젝트 2개와 동일 의뢰번호의 부모 분리, KEEP/신규 하중 생성, 재적용, 감사 실패 rollback, 폴더 변경 차단이 통과했다.
- 검증: 관련 SPDM/의미 매핑/결과 검토 API 47개 통과. 새 기능·스키마·migration·health focused 묶음은 106개 통과 후 기존 health 인접 router 순서 검사 1개 실패를 발견하여 새 router 등록 위치를 조정했고 health 10개 전부 재통과했다. 새 기능 11개와 PostgreSQL 시작 검사 73개가 이 검증에 포함된다. 배포 CI의 Python 검사에서 176개 통과/2개 건너뜀 후 발견한 startup fixture는 새 열 검사 계약에 맞춰 보완했다.
- Windows 배포 자체 검사 8개 및 PowerShell 구문 검사 34개 통과. TypeScript, Vite production build, frontend architecture 검사 통과. 실제 폐쇄망 Windows Server 2022 설치/재부팅이나 사내 공유 폴더 검증은 수행하지 않았다.
- 사용 순서와 시험 폴더는 docs/semantic-folder-discovery-guide.md에 정리했다. 주기 감시, 폴더 이동/이름 변경 자동 추적, 정규식 편집기는 후속 범위다.
- 최종 브라우저 검증은 실제 API/임시 저장소로 저장소 최초 설정, root 위치 선택, Escape 취소, 전체 조사, 규칙 저장, 생성 전 미리보기, 실제 부모별 DB 항목 조회, 재미리보기 KEEP까지 통과했다(최종 1개 통합 시나리오 16.2초, runner 종료 0). 라이트/다크/390px 캡처를 확인하고 긴 경로의 표·트리 스크롤 및 모바일 가로 넘침을 보완했다. 초기 화면의 로딩 전 선택 버튼 노출도 제거했다. QA 사본의 폰트 접근 허용 설정과 임시 산출물은 커밋에서 제외한다. 임시 PostgreSQL은 종료했다.


### 2026-09-15 폴더 역할·해석 종류 관리 및 키워드 인식 확장

- 사용자가 요청한 핵심 후속을 구현했다. 역할 키·표시명·처리 종류와 해석 종류 키·표시명을 관리자 카탈로그에서 추가·편집·삭제(비활성화)·복구한다. 기존 연결과 키를 보존하며 사용 중인 역할의 처리 종류 변경은 막는다. revision 비교와 transaction/공통 쓰기 잠금으로 동시 저장 및 오래된 미리보기 적용을 거부한다.
- 이름 시작 조건은 이름 어디에나 포함되는 키워드 조건으로 변경했다(NFKC·대소문자 정규화). 기존 prefix 저장값도 읽는다. 구분자는 사용자가 지정하고, 비어 있거나 폴더명에 없으면 전체 이름과 빈 코드를 사용한다. 같은 깊이·같은 부모의 하중 경우는 코드·이름이 반복되어도 경로별로 생성한다. 프로젝트·의뢰의 비어 있지 않은 코드는 사용자 역할 키가 달라도 처리 종류 전체에서 충돌 검사한다.
- 입력·결과 역할은 상위 하중 경우에 폴더 용도를 기록하며, 확정된 연결 행에서 기존 결과 연결 화면으로 경로와 프로젝트·의뢰·하중 경우를 전달한다. 레시피와 표시 템플릿 지정은 기존 화면에서 수행한다. 역할 추가 자체가 새 파일 해석기나 실행기를 생성하지는 않는다.
- Terra 백엔드/Luna 프론트엔드 구현, Sol 1차 검수 및 독립 Astra 최종 검수를 수행했다. 적용 전 CREATE/CONFLICT 행의 결과 연결 버튼 노출을 수정하여 KEEP 또는 실제 적용 성공한 정상 행만 연결을 전달한다. 최종 독립 검수에 차단 결함은 없었다.
- migration 0028_folder_catalog와 schema exporter·생성 API 타입을 갱신했다. 기존 0027 데이터가 있는 임시 PostgreSQL 업데이트와 빈 DB 설치를 최종 코드로 검증했고 열·제약·인덱스 동일성, 기존 registry 보존 및 사용자 지정 app role 권한을 확인했다. 실제 PG에서 사용자 역할/해석 종류, 키워드, 빈/누락 구분자, 중복 하중 코드, 결과 폴더 부모 연결, KEEP, 비활성 신규 생성 차단, 카탈로그 변경 차단이 통과했다. DuckDB 기존 registry 변환과 재시작 시 카탈로그 보존도 검증했다.
- 검증: 기능·startup·migration focused 96개, 추가 보존/역할 간 코드 충돌 2개 통과. 배포/portability 묶음은 108개 통과·2개 건너뜀 뒤 QA 사본의 누락된 최신 exporter 때문에 1개 실패했고, QA exporter 동기화 후 portability 4개 모두 통과했다. 제품 코드의 검사를 완화하지 않았다. Windows 배포 자체 검사 8개, TypeScript, Vite production build, frontend architecture 통과.
- 실제 브라우저는 사용자 데이터 없는 QA 사본/임시 DB에서 카탈로그 추가·편집·삭제, 사용자 구분자, 중간 키워드·대문자 하중 경우 2개, 결과 역할 2개, 적용 전 연결 버튼 비노출, 적용 후 실제 API 조회, KEEP 및 결과 연결 전달을 검증했다(1개 통합 시나리오 17.5초, runner 종료 0). 라이트·다크·390px 캡처와 모바일 가로 폭을 확인했다. 초기 테스트 선택자 중복을 exact로 수정했다.
- deploy.bat/update.bat·의존성·영구 경로·사용자 상태는 유지한다. 운영 PostgreSQL은 시작 시 스키마 검사만 수행한다. 임시 PostgreSQL을 종료하고 전용 25473/18000/15173 포트 해제를 확인했다. 실제 사내 공유 폴더 및 폐쇄망 Server 2022 설치·재부팅은 수행하지 않았다. 안내는 docs/semantic-folder-discovery-guide.md에 갱신했다.
- 충돌 항목 제외 기능은 검토 의견만 제공했다. 강제 검증 우회 대신 선택한 하위 트리 제외 후 나머지 재검증을 권장하며, 이번 구현에는 포함하지 않는다.


### 2026-09-15 생성 미리보기 제외·구분자 필수 일치

- 최신 요청에 따라 생성 미리보기 CREATE/CONFLICT/KEEP 행에서 이번 적용 제외·취소를 제공한다. 선택 경로의 하위 트리도 제외하고, 제외를 반영한 노드로 계획을 다시 계산한다. 기존 업무와 registry는 삭제하지 않으며 기존 코드 소유권도 유지한다. 제외는 저장 규칙의 영구 무시 목록이 아니다.
- 제외 경로는 조사에서 역할이 일치한 경로만 허용하고 정규화·상위 경로 병합을 수행한다. 빈 root 경로와 정규화 경로 충돌도 처리한다. immutable preview rows_json에 EXCLUDED/excluded_by를 보존하고 적용 시 동일 계획을 재계산한다. EXCLUDED는 생성·registry 기록에서 건너뛰고 제외 개수를 감사 결과에 남긴다. 기존 full scan, stale, rules/catalog revision, 원자적 적용 검사를 유지한다.
- 구분자를 지정했으면 실제 폴더 이름에 해당 구분자가 포함된 경우만 규칙에 일치한다. 빈 구분자는 전체 이름·빈 코드 동작을 유지한다. 구분자가 없는 일반 폴더도 전체 조사 결과에는 남는다. docs/shared_folder 사진과 저장 폴더 계약의 Project/WR/CAE/TEST 구조를 확인하고 안내에 최소 시험 예제와 실제 구조의 차이를 명시했다.
- Terra 구현·Luna UI 구현, Sol 1차 검수·독립 Astra 최종 검수를 수행했다. 실패한 제외 갱신 뒤 이전 계획을 적용할 수 있던 문제를 수정했다. 요청한 제외 선택을 유지하고 새 미리보기 성공까지 적용·결과 연결을 차단한다. 최종 검수에 남은 차단 사항은 없다.
- 최종 기능·배포 schema gate/profile 검사 47개 통과. 이 중 폴더 기능은 21개이며, 제외한 등록 프로젝트의 코드 소유권 보존·루트 제외·경로 정규화·API 제외 적용·모든 항목 제외 차단·변조 경로·조사 변경 차단을 포함한다. 초기 새 테스트 2개의 잘못된 depth/정규화 예제는 실제 폴더 조건에 맞게 수정했다.
- OpenAPI 계약·TypeScript·frontend architecture 및 production build 통과. Browser plugin이 없어 기존 Playwright를 사용자 상태 없는 소스 사본과 임시 DB에서 실행했다. 구분자 없는 dropNotes는 조사에 표시되고 생성 대상에서는 제외됨, 제외 요청의 의도적 500 실패 뒤 적용 차단과 재시도, CREATE 제외/취소, KEEP와 결과 하위 트리 제외 후 DB의 하중 경우 ID 보존, 복구 후 결과 연결을 검증했다(통합 시나리오 22.7초, runner 종료 0). 라이트/다크/390px 캡처와 모바일 폭 검사도 수행했다. 전용 18000/15173 포트 해제를 확인했다.
- DB 스키마·migration·의존성·배포 진입점·사용자 데이터는 변경하지 않는다. 실제 사내 공유 폴더/폐쇄망 Server 2022 실기 검증은 별도다. 사용 가이드와 개발 계획을 갱신했다.

### 2026-09-15 샘플 자동 감지·레시피 재사용·폴더 저장 이력

- GitHub 이슈 #23/#24의 실제 CSV/JSON 본문을 테스트 fixture로 보존하고 v2 읽기를 추가했다. CSV 표/키-값(스칼라·벡터·빈 값 혼합), JSON 객체/레코드, TSV/TXT를 내용으로 감지한다. UTF-8/BOM·UTF-16(무BOM BE 포함)·CP949, 점/공백/괄호가 있는 키와 배열 JSON Pointer, 중복 키 오류, 누락/null/빈 문자열/0/false 구분을 검증했다.
- v1 정의의 읽기 의미와 제한을 유지하고 새 레시피만 v2를 사용한다. v2의 임의 행·열·매핑·점 개수 제한을 제거하고 파일/결과 64 MiB 및 실행 시간 예산을 명시했다. CSV 표·최상위 JSON 레코드 배열은 재순회 읽기를 사용한다. 화면은 필드·행·매핑·정규화 결과를 페이지로 탐색한다.
- 파일 드롭, 즉시 키/값 미리보기, 초기화, 고급 자동 감지 재설정, 일괄 항목 생성/기존 빈 매핑 연결, 저장 레시피 후보, 여러 샘플의 동일 레시피 검증을 추가했다. 임시 샘플은 소유자·해시·만료를 검사하고 저장 버전의 샘플과 분리했다. 초기화·파일/레시피 전환 시 늦은 검사/미리보기/일괄 생성 결과가 이전 매핑을 복원하지 않도록 했다.
- 저장 규칙 위치·적용 당시 규칙 스냅샷·실제 업무 연결을 현재 root 범위에서 다시 조회하고 페이지 탐색한다. 규칙 저장 후 목록 갱신, 같은 위치의 이력 불러오기 후 다른 폴더로 이동하는 경합을 수정했다. 조사/불러오기 자체는 업무를 생성하지 않는다.
- 같은 깊이·역할 규칙은 실제 폴더별 효과가 같으면 합치고 기존 registry 역할/ID를 우선한다. 서로 다른 이름/번호/해석 종류 등 실제 충돌은 유지한다. 중첩 프로젝트는 새 업무 문맥이며 INPUT/RESULTS는 가까운 하중 경우·의뢰·프로젝트에 연결한다. 프로젝트 수준 CAD는 하중 경우를 억지로 만들지 않는다. 결과 적재에는 여전히 하중 경우가 필요하다.
- Terra 읽기/폴더 구현, Luna 화면 구현, Sol 1차 검수와 독립 Astra 최종 검수를 거쳤다. 검수의 일괄 연결 누락, 페이지 왕복, 초기화 경쟁 상태, v1 재검사 유지, 자동 위젯 32개에 의한 매핑 제한 문제를 수정했고 최종 코드 검수에서 남은 차단 사항이 없다.
- 격리 QA 소스와 임시 DB에서 새 엔진/샘플/API·배포 gate/profile 묶음 74개, 폴더 기능 25개를 통과했다. 기존 의미 매핑 API·활성화·영향 분석·검토함·SPDM 소유권·배포 gate/profile 회귀 59개, 샘플/의미 매핑 API 14개도 통과했다(검사 묶음 간 중복 포함). OpenAPI 생성, TypeScript, frontend architecture, Vite production build 통과. 기존 큰 번들 경고는 남아 있다.
- Browser plugin not available: 기존 Playwright와 사용자 상태 없는 QA 사본을 사용했다. 폴더 생성/재적용/제외/역할 카탈로그와 저장 규칙·이력·CAD 재접속 2개 시나리오 통과. 샘플 브라우저 최종 결과는 아래에 별도 기록한다.
- migration·잠긴 의존성·배포 진입점은 변경하지 않는다. 기존 ijson을 재사용하고 임시 샘플은 OS 임시 경로를 사용하며 배포 패키지에 넣지 않는다. 사용자 DB·설정·실행 서비스는 테스트에 사용하지 않았다. 폐쇄망 Server 2022 신규 설치/업데이트/재부팅과 실제 사내 공유 폴더 시험은 수행하지 않았다. 사용 가이드 2개와 개발 계획을 갱신했다.
- 최종 브라우저 회귀 8개 통과(2.1분): 기존 활성화/등록/Run 조회·권한·6종 위젯, 이슈 CSV 드롭, 305필드/60행 마지막 페이지 왕복, 늦은 검사 초기화, 일괄 항목 생성·여러 샘플 통과/오류·저장 재사용, 생성 도중 초기화 후 매핑 비복원을 확인했다. 새 정규화 미리보기 표로 기존 위젯 테스트의 값 선택자가 중복되어 위젯 영역으로 범위를 정확히 지정한 후 전체 통과했다.


### 2026-09-15 이슈 결과 파일→항목 편집→위젯→통합 설정 저장

- 이슈 #23/#24 예제에서 사용 가능한 단일값 43개를 선택 초안으로 연결한다. FLOAT 추천, null 기본 제외, 실제 0/false 보존, 좌표 성분과 조건 이름 유지, 호환되는 기존 항목 재사용을 구현했다. 페이지 이동이 명시적인 필드 선택 해제를 되돌리지 않도록 했다.
- 매핑 수정·복제·삭제·되돌리기, 미사용 항목 의미 편집, 사용 중 항목 표시명 개정과 참조 버전 개수, 보관/복원을 제공한다. 보관은 immutable revision을 추가하고 과거 snapshot·활성 설정·Run을 보존한다. 샘플 전환/초기화와 이후 위젯 편집이 삭제 되돌리기로 덮어써지지 않도록 했다.
- 별도 WidgetEditor에 항목 우선 선택, 종류 호환 검사, 한국어 표시 방식, 제목·단위 기본값, 소수 자릿수 설명과 표 반영, 일반 위치 필터·샘플 값 추천, 실제 서버 결과 자동 미리보기를 모았다. scalar 요약표는 64개 입력씩 분할하고 총32위젯 한도를 안내한다. 단일값/좌표를 임의의 곡선으로 만들지 않는다. 구버전의 선택 설정 생략과 짧은 화면의 모달 스크롤도 보완했다.
- configurations API가 템플릿과 레시피를 하나의 transaction/CAS 경계에서 저장하고 exact template version을 연결한다. 저장 샘플의 widget READY를 검증하며 파일 없는 개정은 이전 저장 샘플을 다시 검증해 보존한다. 활성화는 기존 영향 검토·명시적 bundle activation을 유지한다. 같은 맥락의 저장 중 편집은 새 ID/CAS를 반영하면서 초안을 보존한다.
- 초안 반입에서 표시 템플릿 ID를 재매핑한다. 최신 정의 내보내기로 보존할 수 없는 과거 표시 버전 연결은 생략하고 UI에 경고한다. 링크 버전을 양의 정수로 엄격히 검사한다. PostgreSQL 설정 저장의 잠금 순서를 recipe→template으로 맞춰 활성화와 역순 잠금을 피한다.
- 독립 1차·최종 코드 검수에서 발견한 보관 상태 대소문자, 저장 경합, JSON Pointer 우선순위, 의미 참조 오탐, 샘플 승계, 표시 연결 반입 문제를 수정했다. backend architecture 검사에서 발견된 기존 폴더 규칙의 router SQL도 동일 transaction 아래 service로 기계적으로 이동했다.
- 격리 QA 소스·일회용 DuckDB로 기존 의미 매핑 API/활성화/초기 설정 검사16개, 최종 설정5개+폴더25개=30개, 배포 schema gate/profile26개를 통과했다(초기 설정2개 중복, 고유70개). 추가 원자성 검사는 템플릿 저장 후 recipe 오류/CAS 실패에서 양쪽 version과 샘플 rollback을 확인하며 보관 후 기존 활성 import·Run 조회도 검증한다.
- OpenAPI 계약, backend/frontend architecture, TypeScript, production build를 통과했다. 기존 500kB 초과 번들 경고는 유지된다. Browser plugin not available로 기존 Playwright를 사용하며 실제 API와 1280×720/390×844 화면에서 검증한다. 최종 브라우저 결과는 아래에 추가한다.
- migration·의존성 lock·deploy.bat/update.bat·영구 경로는 유지하며 사용자 DB·설정·서비스는 테스트에 사용하지 않았다. 폐쇄망 Server 2022의 실제 설치/업데이트/재부팅 및 사내 공유 폴더 검증은 수행하지 않았다. 사용 가이드와 개발 계획을 함께 갱신했다.
- 브라우저 회귀 13개 통과(4.4분): CSV/JSON 43개 항목→편집/삭제/복구→위젯→통합 저장/불러오기→활성화/import/Run, 대량 필드·행 페이지 이동, 지연 응답과 저장 중 편집, 항목 보관/복원, 기존 권한 및 6종 위젯을 확인했다. 마지막 초기화·표 가독성 보완 후 핵심 5개를 다시 통과했다(2.3분). 예제 흐름에서 로그인 전 예상 401을 제외한 console.error와 pageerror가 없음을 검사했다.
- 화면 직접 검수에서 긴 좌표 이름 잘림과 모바일 편집 영역 폭 넘침을 발견해 표 내부 스크롤·이름 줄바꿈, grid 최소 너비와 입력/버튼 폭을 보완했다. 데스크톱/모바일 위젯 및 입력 컨트롤 경계 검사도 추가했다. 독립 최종 검수에서 추가 차단 결함은 없었다.
- 최종 반응형 보완 후 CSV/JSON 전체 흐름 2개가 다시 통과했다(1.8분). 1280×720 결과 표/카드와 390×844 위젯 편집/저장 버튼 캡처를 직접 확인했고, 최종 production build도 통과했다. 화면 증거와 테스트 로그는 사용자 상태 없는 임시 QA 폴더 `simdashboard-recipe-workflow-20260915/evidence`에 보관한다.

### 2026-09-15 벡터 결과와 값 없음 보존

- `vector` 항목은 순서 있는 고유 성분명과 FLOAT/INTEGER 자료형을 보존한다. v2 JSON Pointer는 부모 배열을 하나의 벡터 관측으로 연결하며 배열 길이·성분 자료형·원본 경로를 검증한다. 임의 성분 개수 제한은 두지 않고 파일/출력 바이트와 실행 시간 예산을 적용한다.
- `missing: preserve`는 새 v2 스칼라/벡터 선택지다. 명시적으로 존재하는 null·빈 값은 `value_status: MISSING`과 null 자리 배열로 보존한다. 기존 `skip`/`error` 의미와 알 수 없는 경로 차단은 유지한다. `NO_VALUE` 위젯은 구조적으로 유효하므로 저장·활성화·검토·import는 허용하지만 화면은 값 없음을 표시한다.
- 표는 벡터 전체 배열을 하나의 행으로 표시하고 KPI/게이지/막대는 `vector_component`로 한 성분을 투영한다. semantic provenance가 배열·성분·상태의 원본이며, 기존 정규 결과 저장소와 Run/variable catalog 호환을 위해 벡터 하나당 TEXT JSON 증거 행 하나만 추가한다. 성분별 스칼라 행으로 확장하지 않는다.
- 최신 사용자 정정에 따라 빈값 숨김/정의 제외 방안은 적용하지 않는다. CSV 한 값 칸은 스칼라, 여러 값 칸은 벡터이며 전부 비어 있어도 정의에 포함한다. 예제 원본 35개를 유지하고 실제 값이 있는 항목은 23개(스칼라 13+벡터 10)다. 화면 집계는 정규 저장소 증거 행 개수가 아닌 관측 유형으로 계산한다.
- 원본 배열과 성분별 경로를 모두 조사하되 기본 정의 선택은 부모 벡터 한 개를 사용한다. 여러 행의 scalar/array 혼합은 매핑 불가 경고로 표시하고, 빈 배열 다음의 길이가 있는 빈 벡터도 대표 구조를 갱신한다. 현재 샘플의 빈 값 때문에 이후 행의 0/false를 놓치지 않는다.
- 단일/일괄 자동 생성의 키 충돌은 의미가 같은 항목만 재사용하고, 다른 구조·보관 상태는 종류와 번호를 붙인 별도 키로 보존한다. 사용 중 성분 이름·순서는 변경할 수 없고 영향 검토에도 포함된다. 명시적으로 입력한 키의 충돌 검증은 유지한다.
- 독립 1차·최종 검수에서 발견한 null 단위 변환, scatter의 빈 좌표 쌍, NO_VALUE 표의 행 숨김, 벡터 집계, 배열 대표값 병합 오류를 수정했다. 기존 skip/error의 공백 처리 의미를 보존하고 새 preserve만 결측을 허용한다. 같은 빈 정의로 이후 값이 채워진 파일을 읽는 API/브라우저 회귀를 추가했다.
- DB migration·의존성·배포 진입점 변경은 없다. 원본 사용자 DB·설정·서비스는 테스트에 사용하지 않는다. 폐쇄망 Server 2022 신규 설치·업데이트·재부팅은 이번에 실기 검증하지 않았다. 사용자 가이드와 개발 계획 12.8을 함께 갱신했다.
- 격리 QA 소스와 일회용 DB에서 preserve/vector/기존 reader·engine/API·통합 설정·활성화·영향 검토·검토함·배포 schema gate/profile 회귀 147개가 통과했다(289.21초). 이후 빈 배열→길이 있는 빈 벡터의 대표값 선택을 추가 검증했고 vector 묶음 7개도 통과했다. TypeScript·backend/frontend architecture·OpenAPI 계약·production build가 통과했으며 기존 큰 번들 경고는 유지된다.
- 브라우저 고유 시나리오 16개 모두 통과했다(여러 실행 묶음의 중복 제외). 빈 스칼라/벡터 생성·통합 저장·후속 값 재사용, CSV/JSON 35개 항목과 실제 벡터 집계, 전체 벡터 표·Z 성분 카드·활성화·import·Run·재열기, 대량 페이지·저장 경합·초기화·권한 회귀를 확인했다. 옛 단일값 기본 위젯 가정과 저장된 옵션까지 잡는 모호한 테스트 선택자를 현재 사용자 조작에 맞게 수정했고 마지막 5개 재검증도 통과했다(1.2분).
- 1280×720/390×844 화면 증거를 임시 QA `simdashboard-vector-20260915/evidence`에 보관한다. 빈 값/후속 값 표를 직접 확인해 값 열의 말줄임표를 줄바꿈으로 바꾸고 모든 벡터 성분을 표시했다. 사용자 상태가 없는 QA 서버만 사용하고 종료했다. 독립 최종 코드 검수의 남은 차단 사항은 없다.
- Inspector는 배열 부모를 행마다 한 번만 합쳐 빈 배열·null·후속 배열의 형태 변화를 안전하게 반영한다. 스칼라/배열 혼합, 길이 불일치, 객체 배열은 `MIXED_FIELD_SHAPE` 경고와 비매핑 상태로 반환하며 검사 API를 실패시키지 않는다.

## 2026-09-16 — 대표 레시피 위젯을 결과 검토에 연결

- 사용자 승인에 따라 기존 카드·요약표를 결과 검토 공통 영역에 연결했다. 별도 의뢰 레이아웃이 없는 경우에도 저장된 Run의 표시 템플릿을 보여주며 기존 분석 화면과 중복 렌더링하지 않는다. 예제 전용 위젯 코드·업무 seed는 추가하지 않는다.
- 단일 파일 등록 완료와 폴더 처리 성공/기존 실행 재사용 행에 결과 검토 링크를 추가했다. 등록 당시 대상 또는 처리한 binding의 소유 문맥과 Run ID를 보존하고 실패·보류에는 링크를 만들지 않는다. 라우터 Link로 배포 basename을 유지하고 늦은 응답·다른 연결의 이전 결과 행을 분리한다.
- 가상 결과 레이아웃에서도 결과 버전 선택·URL 복원·재접속을 지원한다. 스냅샷 요약·스칼라·도메인 위젯의 최신 Run 혼합을 막기 위해 기존 result-layout API에 선택적 run_id를 추가했다. 프로젝트 권한 확인 후 의뢰/하중 경우 소속을 검증하고 잘못된 Run은 404로 거부한다. Run 미지정 시 기존 동작을 유지한다.
- Astra 설계, Terra 화면/문맥 구현, Luna 가져오기 연결/백엔드 구현, Sol 1차·재검수와 Astra 최종 검수를 진행했다. 검수에서 발견한 혼합 Run과 브라우저 검사에서 확인한 이전 선택 Run+새 하중 경우의 일시적 요청을 수정했다. 표시 Run은 현재 하중 경우와 일치하는 overview에서만 가져온다.
- 사용 가이드에 실제 이슈 CSV/JSON 업로드부터 35개 항목 표·상단 변위·무게중심 Z 카드·활성화·등록·결과 검토까지 순서를 추가하고 개발 계획 12.9를 갱신했다. DB migration·의존성·deploy.bat/update.bat·영구 경로 변경은 없다. 운영 DB·설정·서비스는 테스트 대상으로 사용하지 않았다.
- API/경계 검증 23개 통과(47.59초). 첫 실행의 503은 개발 환경 AUTH_MODE가 초기 관리자 설정을 요구한 것이며 테스트 프로세스에 AUTH_MODE=disabled를 지정하고 conftest의 일회용 DB로 재실행했다. 선택 이전 Run의 값·계약·run-only 조회, 다른 의뢰/하중 경우/없는 Run 거부를 확인했다. OpenAPI·프런트엔드 architecture·결과 레이아웃/라우팅 검사와 production build 통과. 기존 큰 번들 경고는 유지된다.
- Browser plugin not available: Playwright로 임시 소스·DB와 127.0.0.1:15173/18000에서 검증한다. Vite 파일 접근 제한은 테스트 실행 권한으로 해결했고 운영 배포 설정을 변경하지 않았다. 화면 선택자의 region 가정과 단위가 포함된 표시명 가정을 수정했으며 기존 결과 조회 테스트는 자동 첫 의뢰 대신 실제 검증할 의뢰·하중 경우를 명시하도록 보완했다. 상세 로그·화면 증거는 임시 디렉터리 `simdashboard-review-20260916`에 보관한다.
- 실제 폐쇄망 Windows Server 2022의 신규 설치·기존 DB 업데이트·재부팅 검증과 설치 패키지 제작은 수행하지 않았다.
- 최종 브라우저 고유 시나리오 11개 통과: 기존 결과 레이아웃, 빈 스칼라/벡터와 이후 값 채움, 설정 저장·활성화·기존 분석/비교 화면·조회자 권한, 대표 CSV/JSON의 실제 결과 검토 이동·정확한 과거 Run·새로고침·버전 전환·35행 표, 폴더 성공/중복/오류/보류 링크 경계를 확인했다. 전체 묶음 10개 통과 후 의뢰 자동 선택 가정을 고친 회귀와 그 선행 다중 의뢰 조건을 함께 재실행해 2개 통과(44.3초, 중복 제외 11개)했다.
- 대표 결과 화면의 URL/제목·실제 내용·Vite 오류 화면 없음·console/pageerror 없음과 1280×720/390×844 폭을 확인했다. 표는 내부 스크롤로 전체 항목을 확인하며 모바일에서 카드와 빈 벡터 성분 표시를 육안 검수했다. 결과 화면 캡처는 임시 QA `evidence/issue-csv-review*.png`, `issue-json-review*.png`에 보존한다. 테스트 러너가 소유한 서버를 종료했고 최종 Sol/Astra 검수의 남은 수정 사항은 없다.

### 2026-09-16 폴더 레시피 재검색·표시 템플릿 계약 보완

- 폴더 새로고침은 직접 자식 파일 범위를 유지하며, CSV 레시피가 solver의 `.txt` 구분자 표를 실제 CSV 헤더·행 검증 뒤 읽도록 했다. JSON은 여전히 `.json` 확장자를 요구하므로 내용 추측으로 다른 형식을 연결하지 않는다.
- import와 folder refresh는 binding override가 없을 때 레시피에 저장된 exact display template ID/version을 사용한다. 표시 설정이 달라지면 source run identity도 달라져 새 Run/provenance를 남기며, 동일 설정의 중복은 기존 immutable Run과 저장된 위젯을 재사용한다. 템플릿이 없는 기존 Run의 legacy identity는 유지한다.
- refresh 성공/중복 행도 `relative_path`를 항상 반환한다. UNMAPPED에는 레시피별 parser code/message을 저장·반환해 저장 규칙을 수정할 근거를 제공한다. semantic results 응답은 provenance 유무와 빈 위젯의 안정적 이유를 반환한다.
- migration·의존성·배포 진입점·운영 DB/설정/서비스는 변경하거나 사용하지 않았다. `AUTH_MODE=disabled`와 conftest 일회용 DB로 엔진 단위 33개와 template identity·legacy fallback·draft 링크 차단·refresh 경계·review 기본 링크·과거 Run 표시·파일 잠금 API 7개를 통과했다.
- 연결 저장·재연결 성공 뒤 반환된 binding 소유 문맥으로 자동 파일 처리를 실행한다. 파일명과 Run을 분리하고 위젯이 실제로 있는 성공 실행에만 결과 검토 링크를 제공한다. 원인 없는 빈 결과 패널에도 템플릿 재설정 절차를 안내한다.
- 활성 형식은 최신 초안과 구분한 active_format으로 표시한다. 레시피 편집·분석 대시보드·의뢰 위젯 목록의 공통 명칭을 공유하되 읽기 레시피가 지원하는 8종 위젯만 선택하게 한다. 공통 모듈의 Node 직접 검사에는 명시적 .ts 경로와 noEmit 환경의 allowImportingTsExtensions를 적용했다.
- Sol 1차 및 Astra 최종 검수에서 기존 동일 설정 Run 호환, 검토함의 기본 템플릿 누락, 확정 과거 Run의 위젯 상태, 파일 잠금 오류 경로, 활성/초안 형식 필드 위치, 지원하지 않는 위젯 별칭 노출을 수정했다. 관리자의 기존 검토·보류 결정과 과거 Run은 보존한다.
- 첫 통합 검사: 엔진·매핑 API·검토 API 60개 통과(204.45초). Browser plugin not available: 격리 소스·일회용 DB·독립 포트의 Playwright로 8개 중 7개 통과. 남은 검사는 검토함 완료 후 별도 설정 편집 단계가 reload 시 복원된 다른 작업 화면에서 저장 레시피를 찾던 테스트 경로 문제였다. 해당 단계의 진입 경로를 명시하고, 레시피 기본 템플릿으로 검토함을 통과하는 검증을 추가했다. 실제 결과의 reload/과거 Run 복원 검사는 대표 CSV/JSON에서 유지한다.
- 상세 로그·화면 증거는 임시 QA 디렉터리 simdashboard-folder-recipe-20260916에 보존한다. Windows Server 2022 폐쇄망 신규 설치·기존 DB 업데이트·재부팅 및 배포 패키지 제작은 이번에 실행하지 않았다.
- 후속 검토함 시나리오는 기본 템플릿으로 재검증·등록·동일 Run 재사용까지 통과했다. 이후 개별 레시피 저장을 시험하는 오래된 스크립트가 접힌 고급 설정을 열지 않던 부분도 현재 UI 절차에 맞게 수정했다.
- 최종 백엔드 재검증: 매핑/검토 API 31개 통과(217.38초), 앞선 엔진 33개와 합쳐 관련 고유 검사 64개 통과. production build, OpenAPI, 양측 architecture, 결과 레이아웃 및 경로 등록 검사가 통과했다. 기존 번들 크기 경고는 남아 있다.
- 최종 브라우저 검증의 고유 시나리오 8개 통과: 빈 스칼라/벡터 3개, 실제 디스크 CSV/TXT/다른 형식 파일의 저장 즉시 처리와 JSON 직접 등록·정확 Run 위젯 2개, 활성 형식/파일별 진단/검토 링크 소유권·검토함 명시적 재등록·늦은 응답 격리 3개. 마지막 검토 묶음은 3개 모두 통과(53.9초)했다.
- 실제 1280×720 및 390×844 결과 화면에서 예제 표와 카드(-311.643 mm, -159.909 mm), 빈 값, URL과 현재 Run, 오류 overlay·console/pageerror 없음 등을 확인했다. 파일별 상세 이유가 말줄임표에 가려진 부분을 폴더 결과 표에만 줄바꿈하도록 수정하고 화면 캡처로 검수했다. Sol 1차 및 Astra 최종 코드 검수 PASS, 후속 .ts 모듈 경로·상세 셀 수정도 Astra 검수 PASS다. 테스트 러너 소유 서비스는 정상 종료했다.

### 2026-09-16 폴더 확정→결과 조회 자동화 및 코드·이름 표시 계획

- 사용자 요청 범위에 맞춰 계획만 작성했다. 현재 registry 저장과 별도 semantic binding 생성 사이의 중복 단계를 확인하고, 결과 정책·자동 연결·결과 조회·위젯 검토 흐름과 기존 연결 일괄 설정을 설계했다.
- 코드의 구조화 원본과 내부 ID를 유지하는 공통 업무 선택 응답/표시, 중복 표시 구분, 권한 필터, 기존 수동 데이터 호환을 계획했다. 상세 내용은 docs/folder-result-automation-and-selection-plan.md와 전체 계획 12.11을 따른다.
- 기능 코드·DB·서비스·의존성·배포 설정은 변경하지 않았다. 이번 계획을 구현 완료나 실행 검증 완료로 표시하지 않는다.
- Sol의 계획 검토를 반영해 처리 응답의 정확한 display_run_id 사용, 자동 binding/적용 이력의 멱등성, 삭제·비활성 설정의 임의 fallback 금지, 개정 확인·감사 기록·안정된 파일별 결과 순서를 명시했다.

### 2026-09-16 폴더 결과 자동 연결과 코드·이름 선택 구현

- 사용자 구현 승인에 따라 결과 역할 규칙의 읽기 정책과 해석 종류 기본값, 생성 미리보기의 실제 적용 정책, 업무 확정과 결과 binding 저장, 기존 결과 폴더의 일괄 설정을 구현했다. 수동 연결을 자동 덮어쓰지 않으며 소유 업무·역할 충돌과 비활성 설정은 적용 전에 안내한다. 일괄 재설정은 영향 미리보기와 현재 binding 개정 확인을 사용한다.
- 하중 경우 결과 갱신 POST는 확정된 RESULTS 연결을 순회하고 파일별 상태와 정확한 display_run_id를 반환한다. 연결 변경 경합을 막기 위해 소유 문맥·경로·개정 스냅샷을 검증하고 최종 결과 등록 트랜잭션에서도 연결 개정을 잠금·확인한다. 기존 검토함 보류와 과거 Run/템플릿 버전은 보존한다.
- 일반 업무 목록의 권한 범위 안에서 registry 원본 코드를 selection_metadata로 제공한다. 공통 검색 선택기는 코드·이름과 중복 시 부모/해석 종류/경로를 표시하고 내부 ID로 선택한다. 코드 선행 0과 코드 없는 기존 업무를 보존한다. 없는 의뢰의 하중 경우 조회는 표준 404를 반환하도록 권한 경계를 맞췄다.
- Astra 설계·통합, Terra 백엔드/선택 메타데이터, Luna 프런트엔드, Sol 1차와 Astra 최종 검수로 진행했다. 이번 변경은 기존 JSON 설정·연결 테이블을 활용하며 신규 migration·의존성·배포 진입점 변경이 없다. 실제 사용자 DB·설정·서비스를 테스트하지 않는다.
- 1차 관련 백엔드 82개 통과. 병렬 테스트가 기본 pytest 임시 경로를 정리하는 충돌을 확인하여 이후 각 검사에 독립 --basetemp를 지정했다. 대표 브라우저 회귀에서 추가된 중첩 설정 summary와 업무별 refresh API에 맞지 않는 기존 테스트 선택자/응답 대기를 수정했다. 신규 자동 결과 조회 및 최종 회귀 결과는 아래에 기록한다.
- Browser plugin not available: 임시 소스·DB·독립 포트의 Playwright를 사용한다. 로그와 화면 증거는 시스템 TEMP의 simdashboard-automation-20260916에 보관하며 저장소에 테스트 산출물을 추가하지 않는다. Windows Server 2022 폐쇄망 신규 설치·기존 DB 업데이트·재부팅 검증은 이번에 수행하지 않았다.
- 최종 트랜잭션 보완 후 자동 연결/의미 매핑/검토함 API 회귀 42개 통과(177.43초). OpenAPI 및 양측 architecture, 라우팅 검사와 production build 통과. App.tsx의 크기 제한을 올리지 않고 조회 제어를 features/results/refreshCurrentLoadCaseResults.ts로 분리했다. 기존 번들 크기 경고는 유지된다. Node 26에서 제거된 transform-types 옵션으로 실행되지 않는 기존 API 오류 self-test는 저장소 TypeScript 컴파일러로 임시 JS를 생성해 동일 assertions를 통과했다.
- 실제 브라우저 고유 시나리오 8개 통과: 기존 폴더 생성·재적용 및 규칙/이력 복원 2개, 신규 자동 연결·동명/동코드 선택·정확 Run 위젯과 기존 연결 일괄 설정·조회 전용 권한 3개, 대표 CSV/JSON 레시피 결과 검토 2개, 늦은 결과 조회 응답 격리 1개. 6개 묶음(4.0분), 후속 승인 흐름 3개(1.2분), 마지막 모듈 분리/템플릿 표시 보완 후 2개(58.0초) 모두 통과했다. 중복 실행을 고유 시나리오 수에 더하지 않았다.
- 폴더 경로 재입력 없이 실제 파일의 42→84 변경을 읽고 새 Run 선택·의뢰 개요→결과 검토 이동·새로고침·모바일 표시를 확인했다. UNMAPPED 파일에는 다른 파일의 Run/검토 링크를 표시하지 않으며, 성공 링크는 해당 파일의 소유 업무와 Run으로 이동한다. 조회자 저장 결과 GET 200/파일 등록 POST 403을 확인했다. 1280×720/390×844 화면, Vite overlay 및 pageerror 없음, 긴 선택 이름의 보조 표시를 검수했다.
- Sol 1차/재검수와 Astra 최종 검수 PASS. 확인 중 발견한 연결 변경 경합, 처리 권한의 실제 선택 프로젝트 범위, 늦은 일괄 미리보기 응답, 실패한 Run 선택의 성공 안내, 원본 Run과 템플릿 선택창의 불일치를 수정했다. 테스트 러너의 격리 서버는 종료했다.

### 2026-09-16 항목 이름–값 그래프와 표·그래프 다중 항목 선택

- 사용자 요청에 따라 `name_value` 결과 위젯을 추가했다. 항목 이름을 가로축, 수치를 세로축으로 사용하며 막대·점·점과 선을 선택한다. 일반 대시보드와 의미 연결 결과, 작업 유형 결과 구성에 연결하고 설정의 저장·복원을 지원한다. 단위별 그래프 분리, 0·음수, 결측 값, 벡터 공통 성분, 긴 이름 툴팁·스크롤을 처리한다.
- 후속 사용자 요청을 우선해 표·그래프 항목 선택을 미적용/적용 두 목록으로 바꾼다. 검색·개별 추가/제외·일괄 추가/해제·작은 화면 세로 배치를 공통 컴포넌트로 구현한다. 일반 위젯의 `variableIds`와 기존 단일 `variableId`, 의미 연결 `item_ids`를 구분하여 명시적인 선택 없음과 기존 기본값을 보존한다.
- Astra 설계·통합, Terra 렌더링/공통 선택기/의미 연결, Luna API·카탈로그·요청 결과 계약, Sol 1차 검수로 진행했다. 숫자 변수 허용 목록 누락, 벡터 성분 교집합, 요청 결과 행 높이 충돌을 수정했다. 신규 의존성·DB migration·배포 진입점 변경은 없다. 사용법은 docs/name-value-widget.md에 기록했다.
- 그래프 1차 검증: 관련 백엔드 37개 통과, 요청 결과 스타일 회귀 1개 통과, production build·OpenAPI·양측 architecture 통과. 일회용 DB의 Playwright 2개(일반 그래프 표시 전환/저장/재조회, 실제 CSV·벡터 레시피 등록/결과/재조회) 통과. 실제 사용자 DB·설정·서비스는 사용하지 않았다. 초기 pytest는 로컬 인증 설정 영향으로 503이 발생하여 검사 프로세스에만 AUTH_MODE=disabled를 지정한 뒤 통과했다.
- Browser plugin not available: 저장소 Playwright를 사용했으며 127.0.0.1:15173/18000 격리 포트와 전용 e2e DB, 시스템 TEMP의 simdashboard-name-value-20260916에 로그·화면을 보관한다. Windows 테스트 러너의 종료 단계에서 taskkill 접근 문제가 보고됐으며 실제 테스트 결과와 별도로 기록한다. Windows Server 2022 폐쇄망 설치·업데이트·재부팅은 이번에 검증하지 않았다. 후속 다중 선택 최종 검증 결과는 아래에 추가한다.
- 다중 선택 최종 검증: 새 배열 형식/중복/변수 존재/위젯 호환 검사와 변수 사용·삭제 참조, 레거시 단일 선택 보존을 추가했다. 관련 대시보드/분석 페이지/변수 카탈로그 검사 25개 및 신규·추가 타깃 검사 7개가 통과했다. 기존 variableId의 저장 검증을 강화하지 않고 신규 variableIds에만 검증을 적용한다.
- 브라우저 최종 고유 시나리오 2개 통과(1.9분): 일반 위젯에서 검색 일괄 추가·개별 추가/제외·선택한 두 항목만 표/그래프 표시·설정 저장/게시/새로고침 후 복원, 실제 CSV 레시피에서 두 목록 선택·벡터 그래프·원본 Run 재조회. 단일 위젯 전환 안내와 명시적 전체 제외 빈 상태, X축 양끝 여백 보완 후 일반 시나리오를 다시 실행해 1개 통과(28.2초)했다.
- 1280×800 및 390×844 화면을 직접 확인했다. 좌우/상하 목록, 그래프 축 이름, 단위, 저장된 두 항목, 확대 화면을 검수했으며 관련 console/pageerror·Vite overlay 오류가 없었다. 유형 전환 시 비호환/추가 항목 제외 안내, 데이터 대기 선택, 기존 기본 표시와 적용 목록 일치, 혼합 단위 경고도 보완했다. Sol 최종 재검수 및 Astra 최종 검수 PASS.
- 최종 TypeScript, production build, OpenAPI, 양측 architecture, diff-check 통과. Windows node_modules/.vite-temp 쓰기 EPERM으로 기본 Vite config bundler가 실패한 뒤 기존 runner 방식(`vite build --configLoader runner`)으로 동일 production build를 통과했다. 기존 번들 크기 경고와 Windows e2e runner taskkill 종료 경고는 별도 환경 제약으로 남으며 테스트 본체 성공과 구분한다.

### 2026-09-19 Case·Run·수집 버전 기반 해석 결과 대시보드

- 사용자 요청 `docs/dashboard` 문서를 기준으로 Astra 설계·통합, Luna 파서/수집, Terra 화면, Sol 독립 검수로 구현했다. 사용자 원본 `구현.md`, `상세.md`를 보존하고 사용 안내 `docs/dashboard/README.md` 및 문서 지도를 추가했다.
- Altair One 표준 및 명시 Case 상대 경로 조사, 사용환경 JSON 우선/CSV 대체·다섯 평가·방향별 값/판정·영상·Reference, 유통환경 Case/하중경우/사용자 Run/Mode/수집 버전/Component/집계 기준을 기존 결과 검토에 연결했다.
- 선택 엣지 envelope, 네 엣지 패널의 크기/표시순서/Case 선택, 위치 범주 맵, Case×Scene 컨투어 전치, 역할 미확인 거동 슬롯, 원본 비율 확대/영상 제어, 위치별 네 라인 상세, 다중 Case 비교 및 문맥 변경 시 지연 응답 차단을 구현했다. 미확정 운송 프로파일의 Case Scene은 별도 행과 미대응 셀로 유지한다.
- 0029 additive migration은 기존 AnalysisRun 의미를 바꾸지 않고 dashboard_cases/captures/assets를 추가한다. 원본 수치/미디어 바이트·해시·규칙·관측값을 불변 수집 버전으로 저장하고 같은 파일 반복 게시 시 중복 Run/Scene을 만들지 않는다. 프로젝트 권한·경로/reparse 경계·안정된 파일 읽기·원본 변경 감지·트랜잭션 게시·영상 Range를 적용했다.
- 새 의존성 없음. deploy.bat/update.bat·검증 백업 후 migration·앱 시작 시 읽기 전용 검사 계약 유지. 실제 사용자 DB·설정·원본/서비스를 변경하지 않았다. 임시 PostgreSQL 17.11에서 전체 빈 설치 migration 및 0028 기존 프로젝트/의뢰 보존 업데이트, 시작 검사, 변경 전후 40/44 수치와 원본 바이트·Run/Scene ID·중복 방지를 확인하고 테스트 서버를 종료했다.
- 검증: 배포/마이그레이션 및 대시보드 묶음 192 passed, 2 skipped; dotenv 테스트 별도 29 passed. 후속 대시보드 최종 격리 테스트 37 passed(앞 묶음과 중복, 단순 합산하지 않음). Sol 독립 검수 차단 사항 없음. TypeScript·Vite 정적 빌드·프런트 구조 검사·OpenAPI 생성·diff 공백 검사 통과. 기본 Vite config 임시파일 권한 오류는 `--configLoader runner`로 우회하여 같은 production build를 검증했다. 기존 큰 번들 경고는 유지된다.
- 현재 자산 한도: 32 MiB/파일, 256 MiB/수집, 10,000파일; CSV 100,000행 및 Scene 400,000관측. 단위/Component 역할/최종 프레임·공통 범례·운송 프로파일은 근거 없으면 미확인이다. 실제 Clamping·실제 CAE 이미지 정합성·대용량 영상·Server 2022 폐쇄망 설치/업데이트/재부팅 검증을 완료했다고 기록하지 않는다.
- 최종 브라우저 검증: 합성 자료의 Playwright E2E 2 passed(20 Scene, 그래프→Scene20 상세, 컨투어 전치와 동일 셀 ID, 이미지 확대/ESC, 늦은 USAGE 응답의 DISTRIBUTION 덮어쓰기 방지). 별도 15473 화면 하네스에서 20 Scene 중 결측 1개의 실제 막대 공백(19 path), 이미지 모달·전치·1440/390px 화면을 확인하고 console/pageerror 0건을 확인했다. Astra가 저장 화면을 직접 검수했다. Browser plugin not available로 일반 Playwright를 사용했다. 합성 컨투어는 UI 동작용이며 실제 CAE 영상 검증이 아니다.
- 최종 파서/조회 변경 후 타깃 검사 31 passed(위 37개와 중복). 전체 E2E 러너는 2개 성공 후 자식 종료 AggregateError를 보고했으므로 테스트 본체 성공과 종료 문제를 구분한다. 운영 서비스에는 접근하지 않았다.
- 테스트 정리: 임시 PostgreSQL과 root 15473 Vite는 종료했다. 전체 E2E의 15173/18000 포트는 LISTENING 없음으로 확인했다. 해당 runner PID 명령줄 조회는 호스트 접근 거부였으므로 불확실한 프로세스를 임의 종료하지 않았다.

### 2026-09-19 한국어 Windows Git plan 경로 해석 오류

- 사내 업데이트에서 stash 후에도 GetFullPath의 잘못된 경로 문자 오류가 발생한다는 보고를 조사했다. 보고된 사내 로그 파일은 로컬에 없어 직접 열지 못했으나 Windows PowerShell 5.1 + CP949에서 현재 원격 소스와 같은 HEAD의 한글 파일 목록을 읽어 동일 예외를 재현했다. UTF-8 출력이 콘솔 코드페이지로 잘못 디코딩되는 원인이며 로컬 문서 변경이나 stash 손상으로 단정하지 않는다.
- Astra 원인 재현·설계·최종 검수, Terra Git 호출 수정, Luna 회귀 검사, Sol 독립 검수로 진행했다. Invoke-UpdateGit 실행 범위에서만 UTF-8 디코딩을 적용하고 finally에서 기존 콘솔 인코딩을 복원한다. 경로 경계·reparse·보호 파일·빠른 전진 검사와 사용자 stash를 유지한다. 새 의존성·DB migration 없음.
- 수정 전 CP949/ASCII는 GetFullPath 실패, 수정 후 CP65001/949/437/20127 모두 현재 HEAD의 1,198개 경로 처리 성공. Windows PowerShell 5.1 문법, Git module self-test, update-entry, 실제 임시 Git + 모의 서비스 update-integration, PostgreSQL 초기화 실패 경로, 계정 배포 순서/백업 실패 경로 self-test를 통과했다. 배포 schema/profile/offline 환경 타깃 Python 검사 28 passed, 1 skipped. 실사용 DB·설정·서비스는 테스트하지 않았다.
- 한글/공백/대괄호 경로와 stash 유지 회귀를 Windows 배포 계약 CI에 연결했다. 구버전 main 업데이트기의 복구 절차(git pull --ff-only origin main 후 update.bat), 미추적 문서 별도 보존과 stash 유지 안내를 docs/windows-git-update.md에 추가했다. Sol 검수 PASS. 사내 PC 재실행 및 실제 Server 2022 폐쇄망 실기는 별도 확인 대상이다.

### 2026-09-19 사내 log 폴더의 Markdown 문서만 변경 검사

- 후속 사용자 요청에 따라 log/ 아래 미추적 출력 파일은 제외하고 .md/.MD 문서만 Git 변경 검사 대상으로 유지했다. 하위 폴더에도 동일 규칙을 적용하며 .gitignore와 기존 설치의 .git/info/exclude 갱신을 함께 반영한다. 기존 추적 문서 log/work-log.md의 변경 감지와 덮어쓰기 방지, 다른 소스 경로의 검사는 유지한다.
- 임시 Git 업데이트 검사에 TXT/JSON/LOG/확장자 없는 로그가 업데이트를 막지 않고 내용이 보존되는지, 새 최상위·하위 Markdown과 기존 work-log 수정은 여전히 중단시키는지를 추가했다. git check-ignore로 실제 저장소 규칙에서도 일반 출력만 제외되는 것을 확인했다. 실제 사용자 문서·stash·DB·서비스는 변경하지 않았다.
- Windows PowerShell 5.1 Git update self-test와 Sol 독립 검수, Astra 최종 검수 PASS. 인코딩 수정 커밋과 이번 log 규칙 변경은 외부 전송 승인 전 로컬에 보관한다.

### 2026-09-19 docs 문서를 업데이트 사전 검사에서 제외

- 사용자 요청에 따라 docs/ 아래의 추적 문서 수정·삭제와 미추적 문서를 업데이트 사전 변경 검사에서 제외했다. Git pathspec으로 해당 루트만 제외하고 다른 소스와 log/ Markdown 검사는 유지한다. 문서 추적 자체를 없애거나 실제 사용자 파일을 삭제하지 않는다.
- 문서와 무관한 소스 fast-forward는 로컬 문서를 보존하며 진행한다. 같은 문서를 원격이 변경하는 경우에는 Git의 덮어쓰기 거부를 유지하고 원래 오류 및 문서 보존 안내를 표시한다. 호출에 한해 merge.autoStash=false를 지정하여 사용자 설정에 따른 묵시적 stash를 막는다. DB·설정·원본 자료는 변경하지 않는다.
- Astra 설계·최종 검수, Terra 구현, Luna 임시 Git 회귀, Sol 독립 검수로 진행한다. tracked/untracked 한글 문서 보존, docs 밖 소스 수정 중단, 동일 문서 충돌·autoStash 설정에서도 HEAD/내용/stash 보존 검사를 추가했다. 배포 진입점과 의존성은 유지한다.
- 최종 Windows PowerShell 5.1 Git update self-test 및 update-entry 실패 경로 검사 PASS. Sol 독립 검수와 Astra 최종 검수 PASS. 실제 사내 PC·Server 2022 실기는 수행하지 않았다.

### 2026-09-19 사용환경 Case 결과의 의뢰 단위 진입 수정

- 사용자 검수에서 사용환경이 프로젝트→의뢰→해석 Case→다섯 평가 결과 순서여야 하는데 기존 하중 경우 선택을 먼저 요구하는 문제를 확인했다. SimulationDashboard를 legacy ResultsWorkspace 내부에 넣어 overview/load_case/결과 레이아웃이 있어야 접근할 수 있던 프런트엔드 연결 오류가 원인이었다. 폴더 스키마가 잘못됐다고 단정하거나 사내 저장 규칙을 삭제하지 않는다.
- 기존 폴더 스키마/업무 생성 규칙/레시피와 신규 dashboard Case 수집은 별도 계약임을 확인했다. 과거 LOAD_CASE를 새 Simulation Case로 자동 변환하지 않으며 기존 프로젝트·의뢰·결과를 보존한다. 하중 경우가 0개인 새 의뢰에서도 Case 수집·카탈로그·다섯 평가 조회가 되는 API 회귀를 추가해 해당 파일 3 passed를 확인했다.
- Astra 설계·통합, Terra 의뢰 단위 화면/URL 문맥, Luna 브라우저 회귀, Sol 독립 검수로 진행한다. view=case_results 진입점과 별도 Case 결과 화면을 추가하여 기존 Run 결과와 구분하고, 하중 경우·Run·레이아웃 없는 의뢰에서도 Case를 선택하도록 한다. 새 DB migration·의존성·배포 진입점 변경은 없다. 최종 화면/라우팅 검증 결과는 아래에 기록한다.
- Sol 독립 코드 검수와 프런트엔드 production build, architecture check, workspace route registry self-test를 통과했다. Case 복원과 기존 Run 화면 진입 중 늦은 응답이 다른 프로젝트·의뢰 선택을 덮어쓰지 않도록 문맥 의도를 검사한다. Astra가 데스크톱·모바일 화면을 확인하고 Case와 기존 Run의 동시 활성 표시를 수정했다.
- 격리 브라우저 회귀 3 passed: 전체 의뢰에 하중 경우가 없는 초기 상태의 Case 진입·다섯 평가 조회, 새로고침과 프로젝트 전환, 기존 유통환경 20 Scene 및 지연 응답 차단을 확인했다. Browser 전용 플러그인 대신 Playwright 테스트 러너를 사용했다. 실제 사내 저장 스키마·원본·운영 DB는 검사하거나 변경하지 않았으며 사내 환경 호환을 확정한 것은 아니다.
- 최종 브라우저 재검증도 3 passed (33.2s). Case→의뢰 개요→Case 재진입 후 다섯 평가를 다시 조회하고, 활성 여정이 Case 하나뿐인지 검증했다. 데스크톱·모바일 캡처를 갱신했다. 변경은 로컬 작업 트리에 있으며 이번 수정의 원격 push·사내 배포는 수행하지 않았다.

### 2026-09-19 GitHub #27 logic bug — 기존 사양 내 수정

- 사용자 승인 범위에 따라 선택 버그와 기존 설계의 누락 구현만 반영했다. Astra 지휘·최종 통합, Terra 프런트 구현, Luna 수집 진단 구현, Sol 독립 검수로 진행했다. 작업 시작 시 존재하던 Case 결과 진입/라우팅 등의 미커밋 변경은 보존했다.
- Case 범례의 빈 선택을 전체로 해석하지 않으며 전체 선택·해제 버튼을 제공한다. 서버의 NO_SELECTION은 요약 envelope 영역에 선택 없음으로 표시하고 엣지 선택기·컨투어·거동·상세 문맥은 유지한다.
- SimulationResultGraph로 차트 기능을 분리했다. 막대·점·점과 선 전환 및 확대/ESC, Case 색·클릭 문맥·기존 막대 표시 순서를 유지한다. 점과 선은 같은 scene_id 기반 공통 범주축을 사용하며 순번/정렬 상태 미확인점과 결측에서는 선을 끊는다. 미확인 순번의 수치도 점으로는 표시한다. 시각 검수에서 발견한 Recharts 축 중복, 점 key 경고, 툴팁 단위/Case 연결, 확대 영역 높이를 보완했다.
- 컨투어에는 기존 location_peaks의 동일 문맥 추출값만 연결한다. basis/scope/단위·부분 상태를 보존하고 이미지 최종 프레임 값으로 간주하지 않는다. 원문 이름 툴팁·자세/충돌 설명·집계/시간·Scale bar 미확인 안내를 이미지 아래에 배치하여 원본 범례를 가리지 않는다. 전치 cell_id를 유지한다.
- 수집 허용 확장자·제외 경로·한도·안전 정책을 유지하면서 미지원/미처리 상대 경로와 이유를 quality_issues로 보존하고 조회 결과 및 한국어 안내에 전달한다. parser의 ignored_sources collector를 재사용해 CSV를 중복 파싱하지 않는다. report.csv/final.csv는 계속 수집되며 제외 디렉터리 내부를 추가 탐색하지 않는다.
- 읽기 규칙 기록 버전 dashboard-v2 및 정렬된 수집 제외 사유를 fingerprint에 반영한다. 기존 capture는 불변이며 같은 원본·문맥·사유 반복 게시에서 기존 새 버전을 재사용한다. 제외 사유 추가/삭제, 원본 manifest/바이트 보존 및 과거 payload 불변 회귀를 추가했다.
- 거동표 미제공 역할 행, capture PARTIAL 의미, 모호한 회차/Scene 대응, 최종 프레임·표면·설계설명 입력 계약은 변경하지 않았다. 새 DB migration·외부 의존성·배포 진입점 변경은 없다. 실제 사용자 DB·원본·설정·운영 서비스는 테스트에 사용하지 않았다.
- 최종 백엔드 검증: dashboard queries/parser/capture/API/unprocessed_files 39 passed (45.65s). Contour 실제 조회의 basis/scope/source_refs/null 및 선택 라인 값까지 단언한다. 타입 검사·정적 production build·frontend architecture check 통과. 일반 build의 .vite-temp 생성 EPERM 때문에 프로젝트가 E2E에서 쓰는 Vite --configLoader runner로 동등한 production build를 실행했으며 소스 설정은 바꾸지 않았다. 기존 큰 chunk 경고는 남는다.
- Browser plugin not available: 기존 Playwright+격리 DuckDB 러너를 사용했다. 브라우저 검증은 Case 결과 진입→선택 해제/복원→그래프 전환/선 분리/클릭/확대→컨투어 전치 흐름이며 최종 결과를 아래에 덧붙인다. 실제 CAE 자료 정합성 및 Windows Server 2022 폐쇄망 신규 설치/업데이트/재부팅 실기는 수행하지 않았다.
- 최종 검증 합계: 백엔드 41 passed (기존·수집 진단 39 + contour 값 계약 2). 브라우저 7개 시나리오는 최신 전체 실행의 6 passed와 최종 그래프 단독 재검사의 1 passed(11.9s)로 모두 검증했다. 마지막 그래프 검사는 툴팁, 순서 미확인점을 포함한 점 수, 결측/미확인 선 분리, 축 순서/중복 없음, 확대 SVG 실제 높이, ESC, URL/제목, blank/overlay 없음 및 페이지/콘솔 오류·경고 없음을 단언한다. 처음 추가한 테스트의 문구 범위/범례 SVG 중복 selector를 수정했으며 수락조건은 완화하지 않았다.
- Astra 최종 시각 검수: 1280×720 및 390×844에서 그래프 축·점/선 위치와 전환/확대 UI 확인. 합성 자료 화면 증거는 Windows TEMP의 simdashboard-issue27-qa/summary-desktop.png, summary-mobile.png, chart-desktop.png, chart-mobile.png에 저장했다. 실제 해석 이미지 검증이 아니다.
- Windows E2E 러너는 테스트 종료 후 taskkill/child cleanup 오류로 외부 프로세스 exit 1을 반환하는 기존 환경 문제가 재현됐다. Playwright의 시나리오 판정과 이 러너 오류를 구분하며 전체 러너가 무오류 종료했다고 기록하지 않는다. 배포/실행 스크립트를 이번 기능 수정에 섞어 변경하지 않았다. Sol 독립 검수 승인 및 Astra 최종 검수 완료. 원격 commit/push·사내 배포는 수행하지 않았다.
