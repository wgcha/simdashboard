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
