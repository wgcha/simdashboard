# 작업 로그

이 파일은 완료된 개발 작업을 누적 기록한다. 이후 작업은 완료 시 최신 항목을 문서 상단에 추가하며, 변경 범위·검증 결과·남은 확인 사항을 함께 남긴다.

## 2026-10-06 — W6 폴더 이름 경고

- Case 결과 catalog에 추가 필드 `name_warnings: [{kind, severity, message, paths, case_id, run_option_id}]`를 넣었다(`backend/app/services/folder_name_warnings.py`). 이미 읽은 Folder Schema snapshot만 사용하며 폴더 스캔·DB 쓰기·폴더 변경은 없다. 같은 Run Option/Case 아래 대소문자·구분 기호·번호 접미사·한 글자 철자만 다른 Scene, Case 간 Scene 이름 불일치, 깊이 스키마 이탈·UNRESOLVED 노드와 번호로 시작하지 않는 Scene 깊이 폴더를 한국어 문구로 알린다. 계산 오류는 경고만 빈 목록으로 두고 catalog는 그대로 응답한다.
- 화면: Case 결과 머리줄 "폴더 이름 확인 n건" 배지와 경로 목록(`FolderNameWarnings.tsx`), Scene 비교 탭의 해당 Scene 경고 아이콘. 문서 [폴더 이름 경고](../docs/features/folder-name-warnings.md), 로드맵 W6 완료 표시.
- 검증: 신규 백엔드 15개 통과(합성 트리 `4_Edge`/`4_edge2`, `2_Face`/`2_face`, Case 간 `6_Corner`/`6_corner`, Run Option 아래 `backup`, 정상 트리), 관련 dashboard·materials·새 Scene 등록·OpenAPI 묶음 76개 통과. tsc·vite build 통과, check:architecture는 기존 4건만 실패. e2e `simulation-dashboard.spec.ts` 17개 통과·기존 1건 실패(폐기된 capture 고정 검사), 신규 2개(2560×1440·배율 1.5·18pt) 포함. `materials-dashboard.spec.ts`는 이 변경 전 코드에서도 6건, 변경 후 5건 실패(결과 환경 없음으로 탭 미표시; 동시 진행 중인 다른 작업 영향 추정, W6 무관). 독립 검수·Codex Security 스캔은 수행하지 않았다(보안 경계 변경 없음). migration·의존성·배포 변경 없음.

## 2026-10-04 — SPDM 저장소 공급자 1단계(LocalFsProvider) 코드 정리

- `docs/contracts/storage-provider.md`(dfb1dd0) 기준으로 `backend/app/services/storage/{provider,local,__init__}.py`를 추가하고, SPDM 루트 아래 목록·조회·읽기·쓰기를 공급자로 옮겼다. 대상은 서비스 12개(spdm_storage, folder_discovery_scan, folder_auto_discovery, folder_discovery, dashboard_capture, case_finalization, materials_catalog, folder_schema_resolver, folder_request_progress, result_registration_paths·result_registration·result_registration_locations)와 라우터 4개(spdm_storage, semantic_mapping, semantic_review, dashboard)다. reparse·대소문자 충돌 검사, 안정 읽기, 디렉터리 고정, 요청 잠금, no-follow 메타데이터 읽기는 결과·오류 코드·문구를 그대로 provider 모듈로 옮겼다. 모듈별 `_root()` 사본은 `get_storage_provider(conn)`(루트 해석은 `spdm_storage.storage_root` 한 곳)로 대체했다.
- 쓰기는 구역을 명시한다. FINAL은 `*/Final/`과 그 아래 CAE·Reports·.finalizations, LEGACY는 spdm_storage(`Project_*/WR_*`의 CAE·보고서와 상위 폴더 생성), result_registration_paths(준비·rmdir 보상), result_registration(결과 파일 복사) 호출 모듈만 허용하며 그 밖은 `NOT_ALLOWED_WRITE`다. SIMDASH_IMPORT_ROOT 계열(master_result_refresh·bundle_snapshot·result_bundle_publisher), 시맨틱 샘플 임시 폴더, 앱 데이터는 S5 범위 밖으로 정적 검사 예외 목록에 남겼다.
- 실제형 합성 트리(USAGE·DISTRIBUTION, 75R9J_PV·PR, mp4·rad·inc·fem, legacy Project_* 업로드)에서 자동 탐색→등록·수집→sync·refresh→진척→Final 미리보기·보고서 업로드·확정→legacy 업로드·다운로드 흐름의 응답·DB 행·파일 트리 비교가 리팩터 전후 동일했고 탐색·동기화 시간 차는 ±20% 안이었다. DuckDB 전체는 전후 모두 10건 실패(알려진 9건과 동시 실행 시 master_result_refresh 시간 경합 1건, 단독 재실행 통과)로 같고, 신규 공급자 단위·정적 경계 검사 26개를 추가했다. Postgres 주요 묶음은 새 DB에서 HEAD와 같은 결과(spdm_storage_workflow·single_request_registration의 기존 seed 의존 실패 동일)였다. OpenAPI·migration·의존성 변화 없음. 시험 4개의 monkeypatch 대상을 `storage.local`로 옮겼다. Windows 전용 경로(디렉터리 고정·잠금·공유 읽기)는 코드 이동만 했고 Windows에서 실행 검증하지 않았다. 독립 검수·Codex Security 스캔은 수행하지 않았다.

## 2026-10-02 — 소스 업데이트 Git 상태 정리

- 업데이트 로그의 `git-plan` 중단과 Git 사전 검사 코드를 확인했다. `docs/`의 로컬 변경은 허용하지만 미커밋 `log/work-log.md`는 차단 대상이다. 현재 수정 브랜치는 `origin/main`과 같은 커밋이면서 이전 수정 브랜치를 추적해, 미커밋 변경 해결 뒤에도 fast-forward 검사에서 중단될 상태였다.
- 기존 HTML 구조도, 문서 지도 링크, 누락된 과거 작업 기록을 함께 버전 관리하고 업데이트 기준을 `main`으로 맞춘다. 업데이트 스크립트의 변경 감지·데이터 보존 계약은 유지한다.
- 구조도의 SVG 2개 XML 문법, 상대 문서 링크 3개, 외부 리소스·스크립트 부재와 diff 공백 검사를 확인했다. 실제 사용자 DB·설정·서비스를 대상으로 `update.bat` 전체 실행은 수행하지 않았다.

## 2026-10-01 — GitHub #43 Folder Schema 공용 위치·scoped Refresh

- 확인된 Scene/INPUT/RESULTS 위치를 공용 투영으로 묶고 Case 결과·소재·결과 등록이 같은 Scene ID와 의뢰 경계를 사용하게 했다. 동일 부모 역할·깊이의 새 폴더 판정, 구조·내용 fingerprint, 원자적 snapshot 활성화와 과거 capture 보존, 명시 Refresh 및 결과 등록 후 자동 Refresh를 연결했다. 수동 재등록의 확정·제외 상태는 등록 순서대로 Refresh·캡처·재시도에 반영하며 EXCLUDED/UNRESOLVED 하위 파일은 수집하지 않는다.
- 합성 SPDM·격리 DB의 Folder Flow/결과 등록 최신 검사 17개, 공유 경계 회귀 23개 통과·1개 건너뜀. 이전 집중 검사 73개 통과·1개 건너뜀. 프런트 TypeScript와 Vite 생산 빌드, 데스크톱 E2E 2개 통과. Windows 배포 계약 로컬 self-test 10개와 CI 지정 백엔드 검사 179개 통과·2개 건너뜀, 영구 경로 검사 2개 통과. 데스크톱 E2E 실행기는 테스트 통과 후 Windows 자식 프로세스 정리 접근 거부로 exit 1이었고 PID·테스트 포트가 남지 않았음을 확인했다. CI 범위보다 넓게 돌린 첫 백엔드 검사는 기본 cp949 읽기 1건·별도 인증 초기화 4건으로 실패했으며, CI 지정 범위는 UTF-8 환경에서 통과했다.
- 독립 Sol·Astra 검수에서 찾은 Scene 누락, 제외 경로 수집, 중복 capture, 수동 재등록 누락, 결과 게시 payload 전달 누락을 수정하고 재검수했다. Codex Security diff scan `628435b1-1065-449a-bddd-8be446063c9c`는 수정 전 고정 snapshot에서 제외 경로 수집 취약점 1건(낮음)을 보고했고, 이후 코드에서 차단 경로를 고쳐 합성 회귀로 확인했다. 같은 scan을 수정 후 다시 실행하지 않았다.
- 새 DB migration·의존성·배포 진입점 변경은 없다. 선택한 결과 확장자만 내용 바이트를 해시하며 그 외 파일은 경로·크기·수정 시각으로 비교한다. 실제 사용자 DB·SPDM·운영 서비스, Server 2022 폐쇄망 설치·업데이트·재부팅, 원격 CI 전체는 검증하지 않았다. 기존 미커밋 문서·로그 변경을 보존했다.

## 2026-10-01 — GitHub #42 소스 업데이트 프런트엔드 빌드 잠금 대응

- 이슈의 `vite:esbuild-transpile` 임시 `esbuild-*` 파일 삭제 실패는 Windows 공유 잠금으로 프런트엔드 빌드가 중단된 사례다. 소스 런타임 준비 단계에서 실패해 서비스 중지·DB 백업·migration 이전에 배포가 중단됐다.
- 해당 오류 문구와 임시 경로가 일치할 때만 빌드를 한 번 재시도한다. 일반 빌드 오류는 즉시 중단하며, 재시도 후 잠금이 지속되면 원래 진단을 보존해 실패한다. Windows 배포 계약 CI에 격리 회귀 검사를 연결했다.
- Windows PowerShell 5.1 합성 테스트에서 일시 잠금 복구·일반 오류 즉시 실패·지속 잠금 제한, 공백 경로와 출력 보존을 확인했다. 계정 수명주기 self-test와 diff 검사도 통과했고 독립 Astra 검수에서 차단 결함이 없었다. Codex Security 변경분 검사 `77b76682-453d-4b8a-b52a-32ab52fa651a`는 변경 소스 3개에서 보고할 취약점 0개로 완료했다. 실제 사내 PC의 잠금 원인 및 Server 2022 현장 업데이트는 재현·검증하지 않았다.

## 2026-10-01 — 유통 폴더 #40 경로 탐색 갱신

- 이슈 코멘트의 `의뢰/Working/Package.../Drop/Run/INDIVIDUAL/Scene` 역할을 기준으로, 같은 프로필 버전의 최신 Folder Schema 조사가 다시 판정한 경로에서는 이전 등록 역할보다 최신 포함·제외 결정을 우선한다. 그 밖의 과거 등록 역할과 충돌 검사는 유지한다.
- 결과등록 후보는 현재 스캔에서 `EXCLUDED` 또는 `UNRESOLVED`인 경로를 과거 확정 역할로 되살리지 않는다. 소재·물성과 결과등록이 같은 현재 역할 트리를 사용한다.
- 격리 DuckDB와 합성 SPDM 경로 회귀에서 옛 `Pkg.../2_face` 제외, 실제 `Working/Package.../Scene` 및 다른 정상 Case 유지를 확인했다. 관련 백엔드 테스트 52개, 수정 후 새 회귀 1개, 결과등록 소유권 API 1개가 통과했고, 변경 소스 2개에 대한 Codex Security diff scan에서 보고할 취약점은 없었다. 실제 사용자 DB·SPDM·운영 서비스 및 전체 CI는 검사하지 않았다.

## 2026-10-01 — GitHub #41 소스 업데이트 migration 오류 수정

- `origin/main`의 `438b173`을 확인했다. 이슈의 `MIGRATION_COMMAND_FAILED_DATA_VALUE_TOO_LONG`은 39자 revision `0033_result_registration_location_links`가 Alembic 기본 `alembic_version.version_num VARCHAR(32)`에 기록될 때 발생하는 문제와 일치한다. 현장 상세 stage log가 없어 실제 SQL 오류 위치는 이 근거로 추정했다.
- `0033` migration 첫 단계에서 `version_num`을 `VARCHAR(64)`로 확장해 기존 revision 값과 사용자 테이블 데이터를 유지한다. PostgreSQL 트랜잭션 안에서 revision 기록 전 실행되므로 실패 시 함께 롤백되고 수정 후 업데이트를 재시도할 수 있다. 회귀 검사에서 SQL 생성 순서와 migration 체인을 확인했다.
- 관련 Python 검사 186개 통과, 2개 건너뜀. Windows Git 업데이트·PostgreSQL 초기화·계정 수명주기·업데이트 진입점 합성 self-test 통과. 구현과 분리된 Astra 검수에서 차단 결함 없음. 실제 사내 DB, PostgreSQL 신규 설치·기존 데이터 업데이트 실실행, Server 2022 현장 재시도는 수행하지 않았다. 기존 미커밋 문서 변경은 보존했다.

## 2026-09-29 — 결과 등록 저장 위치와 확정 Folder Schema 연결

- 새 Case 생성 기준을 사용자가 실제 탐색한 부모 폴더로 바꾸고, 서버에서 등록된 환경 스캔·현재 프로필·부모 체인의 실제 디렉터리 식별자·의뢰 소유권을 확인한다. `WR/Working`에서 시작하면 그 아래에 Case/하중경우/Run/Scene/results를 준비한다. SPDM 프로젝트·의뢰 등 상위 업무 객체는 만들지 않는다.
- Folder Schema 미리보기는 일반 중간 폴더의 확정·제외 상태도 보존한다. 제외·충돌·오래된 구조는 생성 전에 차단한다. 구형 등록이 중간 폴더의 검수 상태를 증명하지 못하면 재조사·재등록을 안내하고, 이후 확정 등록으로 복구된다. 수집 작업의 `COMPLETED`·`CAPTURING`·`FAILED` 상태에서도 등록된 구조를 읽는다. 기존 Scene/평가 경로의 결과 폴더 준비 후 탐색 역할 충돌도 수정했다.
- 격리 DuckDB 기반 결과 등록·환경 등록 API 22개, TypeScript 검사, 데스크톱 Playwright의 `Working` 부모 미리보기 1개 통과. 독립 Sol·Astra 검수에서 발견한 경로 충돌·제외 우회·구형 재등록 회귀를 수정하고 재검수했다. Codex Security 변경분 검사 `21cb5802-b962-4b44-94df-99165f817bc3`는 변경 소스 6개에서 확인된 취약점 0개로 완료했다. 실제 사내 DB·SPDM 원본과 Server 2022 배포는 미검증이며, 이 변경은 커밋·푸시하지 않았다.

## 2026-09-29 — 유통 스키마 기반 소재 위치와 내 작업 탭

- GitHub #40의 실제 `Working/Package.../Drop/Run/INDIVIDUAL/Scene...` 경로를 반영했다. 적용된 유통 폴더 등록의 확정 Scene 역할을 우선하고, 적용 Scene이 있으면 이름만 비슷한 미등록 경로를 제외한다. Scene 직속 `results`의 중복 위치도 제거했다.
- `101_parts.inc`의 공통 `/BEGIN`·`/PARAMETER`·`/SUBSET`을 소재 파일로 오인하던 판별을 수정했다. 합성 덱에서 101 Parts와 103 Material 파일을 각각 읽고 Part의 Material ID를 해소했다. 소재 화면은 내 작업의 `Case 결과` 오른쪽 탭으로 옮겨 선택 프로젝트·의뢰를 공유한다. 기존 단독 주소는 해당 탭으로 연결한다.
- 격리 소재 API 26개, TypeScript·Vite 빌드·라우팅 검사, 데스크톱 E2E assertion 4개 통과. E2E runner의 Windows 프로세스 종료 권한 오류로 명령 종료 코드는 1이었으나 임시 DB·서버 정리는 확인했다. 독립 재검수에서 차단 사항 없음. Codex Security 변경분 검사 `2d16735f-7c33-4db1-b011-4568c3ddb1f4`는 변경 소스 8개에서 확인된 취약점 0개로 완료했다. 실제 사내 전체 덱·운영 DB/파일과 폐쇄망 배포는 미검증이다.

## 2026-09-29 — 소재 덱의 유통환경 결과 폴더 탐색 보완

- 사내 화면의 `씬 없음` 제보를 분석해 Scene 역할이 없어도 유통 실행 계층 아래 `results` 폴더의 Parts·Materials 덱을 표시하도록 보완했다. 기존 Scene/results도 읽고 소재 화면은 유통환경 전용으로 정리했다. SPDM 폴더 스키마·DB·실제 파일은 변경하지 않았다.
- 결과 폴더 자체와 파일 본문 읽기 전 후보 디렉터리의 의뢰·환경 소유권을 확인하고, 중복 위치·경로 이탈·무관한 문서 폴더를 제외한다. 독립 검수의 소유권 누락·중복 지적을 수정하고 재검수에서 차단 사항이 없었다.
- 합성 API 테스트 25개, 프런트 빌드, 데스크톱 Playwright 2개 assertion 통과. E2E 실행기는 Windows taskkill 권한 오류로 종료 코드 1이었고 테스트 서버 잔류는 없었다. 정확한 사내 결과 폴더 경로와 원본 전체 덱은 미확인이다.

## 2026-09-29 — 결과 등록 현장 검수 UI 보완

- 유통환경에서 Run Case가 아직 없어도 결과 경로 준비 단계의 Run Option 폴더 이름을 지정할 수 있도록 조건을 수정했다. 이미 Scene을 선택한 경우에는 Run Option을 그 아래에 만들지 않도록 경로·화면 조건을 맞췄다.
- 파일을 업로드 영역으로 끌어놓으면 브라우저가 여는 대신 기존 파일 선택 검증·목록으로 전달한다. 개별 결과값 상태를 `값 인식됨`으로 표시하고, 다른 파일 오류가 게시를 차단할 때 검사 메모 확인 안내를 추가했다.
- TypeScript 검사와 격리 데스크톱 Playwright 3개 시나리오(새 Run Option 경로·드롭, 기존 경로 생성·게시, 값 인식과 전체 차단)가 통과했다. 독립 Sol 변경 검수에서 차단 지적 없음. Browser 플러그인이 없어 기존 Playwright 경로를 사용했다.
- 현장 CSV 오류의 정확한 원인은 검사 메모 코드와 환경 정보 확인을 기다린다. `.inc`는 현 결과 등록의 지원 형식에 없으며 물성조회는 SPDM Scene/INPUT 등 입력 덱 경로를 읽는다. 사내 사용자 DB·SPDM 파일에는 접근하지 않았다.
## 2026-09-29 — 모델별 소재·물성 대시보드 3단계

- 사용자 확인 후 데스크톱 화면·메뉴를 구현했다. Part 가변 목록/검색/정렬/CSV/딥링크와 우측 Material·Property·실패 모델·곡선 상세, 의뢰·환경·씬 선택을 연결했다. `project.data.view` 메뉴 정책을 PostgreSQL additive migration 0032와 DuckDB 기본값에 반영하고 기존 정책을 보존했다.
- 합성 데이터 기반 Playwright 2개(4 Part/128 Part), 관련 마이그레이션·정책 테스트 6개, TypeScript 및 Vite 빌드 통과. Windows E2E 실행기의 종료 단계에서 taskkill 권한 오류로 명령은 exit 1이었으며, 서버 종료와 포트 해제는 확인했다. 실제 사용자 DB/설정/서비스 미사용.
- 독립 migration/권한 검수와 별도 수동 보안 검수에서 차단할 지적 없음. Codex Security 플러그인 검사는 아직 미실행. 실제 PostgreSQL 빈/기존 DB migration, 원본 전체 덱·cp949, Windows Server 2022 폐쇄망 검증은 4단계에 남긴다. 사용자 확인 전 4단계는 진행하지 않는다.

## 2026-09-29 — 모델별 소재·물성 대시보드 2단계

- 의뢰 권한·씬 소속을 확인하는 읽기 전용 소재 catalog/deck API를 추가했다. 씬에서 실행 폴더까지 정해진 순서로 Parts·Materials 파일을 선택하고, `/INCLUDE`의 의뢰 밖 경로·reparse·순환 참조를 차단한다. 곡선 좌표는 deck 응답에 포함한다. OpenAPI와 프런트 타입을 갱신했다.
- 파일·총량·포함 깊이·파일 수·곡선 점·파싱 시간 한도를 적용했다. 격리 API·파서 테스트 28개와 최종 조정 관련 3개, TypeScript 검사 통과. 실제 사용자 DB·설정·서비스는 사용하지 않았다.
- Codex Security 변경분 검사 `93a3faa3-d17e-4478-8485-d5aabf20c2d5`에서 사후 함수 점·시간 제한의 자원 고갈 위험 1건을 발견했다. 검사 스냅샷 이후 파싱 중 조기 검사로 수정하고 해당 회귀를 통과했다. 수정 후 플러그인 재검사는 미수행. 원본 전체 덱·Windows Server 2022·화면은 미검증이며 사용자 확인 후 3단계로 진행한다.

## 2026-09-29 — 모델별 소재·물성 대시보드 1단계

- GitHub #34~#38과 현재 문서·코드 계약을 대조해 단계별 계획을 `docs/plans/materials-dashboard-implementation.md`에 작성했다. Part 수를 고정하지 않고 Part별 행 선택 → 우측 물성·함수·Property 상세 표시를 완료 조건으로 확정했다.
- Radioss 고정폭 카드 파서와 #35~#38 격리 발췌 테스트를 추가했다. 독립 검수에서 발견한 대량 스킵/미등록 카드 본문 보관을 수정하고 Property 고정폭 값 검증을 추가했다. 최종 관련 pytest 10개 통과.
- 현 단계는 파서만 완료했다. 실파일 안전 탐색·소속/권한·API·메뉴/화면·DB 변경과 실제 원본 전체/Windows Server 2022 검증은 미수행이며 사용자 확인 후 다음 단계로 진행한다.

## 2026-09-28 — 개발 문서 지도 단순화

- 작업별 시작점·조건부 읽기를 `docs/README.md`에 남기고 상세 문서 카탈로그를 분리했다. 결과 등록 현행 계약을 기능 문서로 추출하고, 완료된 #32 계획과 UI P1~P5 기록은 archive로 옮겼다. UI 데스크톱 정책/P0와 기존 SPDM API 계약은 원래 위치를 유지했다.
- 변경 Markdown 상대 링크와 이동 문서 내용 보존 확인, `git diff --check` 통과. 독립 Sol 검수 지적을 수정하고 재검수에서 차단 사항 없음을 확인했다. 프로그램 코드·DB·배포 계약은 변경하지 않았으며 실행 테스트는 수행하지 않았다.


## 2026-09-28 — #32 결과 등록·검수·DB 가시화

- 후속 사용자 요청으로 사내 검증용 커밋·원격 main 푸시를 진행한다. 원격과 로컬 기준 커밋의 일치 및 최종 diff 검사를 확인했으며, 운영 배포는 별도다.
- SPDM은 기존 프로젝트·의뢰·업무 하중경우를 관리하고, 결과 등록은 기존 대상 탐색 → 필요한 물리 결과 폴더 준비 → 업로드 → 자동 검사 → 사용자 승인 → DB capture 저장 → Case 가시화에 집중하도록 계획과 데스크톱 UI를 확정·구현했다. 업무 생성 버튼은 등록 화면에서 제거했다.
- 초안 파일은 격리 DB에 보관하고 승인한 정확한 바이트만 정식 수집 버전으로 저장한다. 제외 사유·검수 버전, 재승인 조건, 중복/동시 요청·권한 취소·실패 복구를 처리한다. 기존 폴더 역할·식별자와 과거 값·영상은 보존한다. DB 게시 후 SPDM 복사 실패는 별도 재시도로 처리하며 원본을 덮어쓰지 않는다.
- additive migration 0031을 빈 PostgreSQL 및 합성 기존 데이터의 업데이트에서 검증했다. 기존 asset 바이트, app 권한과 시작 시 스키마 검사 전용 계약을 확인했다. 신규 API·경로 DuckDB 20 통과·1 skip, PostgreSQL API 11개와 경로 3개 검증, 기존 배포 179 통과·2 skip, 관련 수집 53개와 권한/시작 등 102개 통과. 전체 기존 회귀에는 Windows/POSIX 및 기존 기대값 불일치 실패가 남아 있으며 상세는 계획서 8절에 기록했다.
- 프런트 build·OpenAPI·구조·CSS·preferences 및 E2E runner self-test 통과. 등록 브라우저 9개와 실제 API 1개 흐름에서 업로드→검수→승인→DB 게시→Case 이동→영상 206 응답·실제 재생을 확인했다. 실제 바이너리 업로드에서 발견한 줄바꿈 변환을 bounded binary feed parser로 수정하고 바이트 보존 회귀를 추가했다. runner는 임시 SPDM과 자체 프로세스 트리만 사용·정리한다.
- 독립 Sol 검수와 독립 Astra 고위험 검수를 완료하고 지적을 해소했다. Codex Security 변경분 검사 완료: 21개 소스 파일, 보고된 취약점 0개, scan ID 522678c4-83f4-4806-84fe-f294bcf70faf. 보고서는 Codex Security의 해당 검사에 보존된다. 이후 변경은 계획서·작업 로그의 완료 기록뿐이다.
- 실제 사용자 DB·설정·SPDM·서비스를 검증에 사용하지 않았다. Windows Server 2022 폐쇄망 설치·업데이트·재부팅, 운영 배포, commit/push는 미실행이다. 기존 chunk 크기 경고와 전체 회귀 제한을 유지한다. 확정 계획: docs/result-registration-review-plan.md, GitHub wgcha/simdashboard #32.

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

### 2026-09-19 환경별 폴더 규칙과 컴팩트 UI 개선계획

- 사용자 요청에 따라 구현 없이 docs/dashboard/folder-schema-ux-plan.md를 작성하고 문서 지도·대시보드 안내에 미구현 계획으로 연결했다. 사용환경 Case→평가와 유통환경 Case→하중경우→Run Case→선택적 Run Option을 분리한다.
- 하나의 폴더 연결 화면에서 환경별 규칙을 관리하고 폴더 선택→구조 확인→등록·결과 확인으로 이어지는 흐름을 제안했다. 후보 한 개는 표시로 축약하고 다중 후보만 선택하며 수집 버전·비교·상세 설정을 보조 영역으로 옮긴다.
- Run Option 원문/없음/미확인 구분, 기존 5종 카탈로그·규칙·불변 capture 보존, 등록과 수집의 멱등 재시도, 선택·버전 유지, 배포 계약 및 인수 시나리오를 포함했다. Astra 설계·최종 검수와 Sol 독립 검수 완료, 차단 사항 없음.
- 문서 변경만 수행했다. 앱 코드·DB·사내 원본·설정 변경, 구현 테스트, 배포·원격 push는 수행하지 않았다. 문서 diff 공백 검사를 확인했다.

### 2026-09-19 Run Option 결과 계약과 컴팩트 대시보드

- 유통환경 capture에 안정적인 Run Option ID, 원문 label, PRESENT/ABSENT/UNRESOLVED 상태를 추가했다. 직접 Scene과 명명 옵션의 동명 Scene을 option ID로 분리하고, 임의 옵션은 저장 규칙이 확인한 원문 목록에 있을 때만 PRESENT로 처리한다. 기존 UNKNOWN capture는 미확인 기존 자료로 보존한다.
- 조회 카탈로그·Run/Scene 상세·비교·context key와 프런트 API/URL에 option ID를 전달한다. 기존 mode 입력은 호환 경로로 유지하며 새 option ID가 있으면 서버가 해당 옵션을 직접 선택한다. 읽기 규칙은 dashboard-v3으로 갱신하고 환경 규칙 프로파일/옵션 배정을 fingerprint에 포함했다.
- 결과 화면은 단일 Case/하중경우/Run/Option/Component/Basis 후보를 배지로 축약하고 여러 후보만 선택한다. 최신 카탈로그 수집 버전을 자동 선택해 URL에 고정하며, 새로고침·카탈로그 갱신에서 유효한 선택을 유지한다. 과거 수집과 Reference는 접힌 수집 이력에서 연다.
- 합성 parser/query 회귀 34 passed와 프런트 production build를 확인했다. 단일 Run Option 축약·안정 ID URL 고정의 Playwright 시나리오도 통과했으며 화면 증거는 Windows TEMP의 `simdashboard-option-qa/compact-option-desktop.png`에 저장했다. 테스트 판정은 1 passed이나 Windows 러너의 기존 child cleanup 오류로 최종 프로세스 exit는 1이었다. 실제 사용자 DB·원본·실행 서비스 및 Windows Server 2022 폐쇄망 배포 실기는 사용하지 않았다.

### 2026-09-19 환경별 폴더 연결 계획 구현·최종 통합

- 최신 사용자 지시를 우선하여 Astra가 설계·통합·최종 검수, Terra가 환경 조사/등록/복구, Luna가 폴더 UI 초안과 브라우저 사양, Sol이 Run Option 수집/조회/결과 UI 구현 및 독립 검수를 담당했다. Astra가 실제 계약에 맞춰 폴더 UI/client를 통합하고 프로파일 CRUD·기존 규칙 복사·이력과 교차 API 검증을 구현했다.
- 사용환경은 프로젝트→의뢰→해석 Case→평가5종, 유통환경은 Case→하중경우→Run Case→선택적 Run Option→Scene으로 분리했다. 옵션 원문·안정 ID와 PRESENT/ABSENT/UNRESOLVED를 보존한다. 부모 역할/이름 패턴/선택 깊이 규칙, 명시 역할 확정·기존 업무 연결·하위 트리 제외, 미리보기 변경 검증을 추가했다. Scene/평가는 독립 업무나 registry 항목으로 만들지 않는다.
- 폴더 연결·규칙은 폴더 선택→구조 확인→등록·결과 확인으로 이어진다. 환경별 마지막 프로파일, 트리 접기/100행 페이지/확인 필요 필터, 선택 폴더 편집, 저장 규칙 복사·개정, 등록 이력과 실패 재시도를 제공한다. 390px에서는 트리와 상세 설정을 전환한다. 완료 링크는 각 job의 project/request/environment/Case/capture를 포함한다. 기존 하중경우·결과가 없는 최초 설치도 이 화면에 진입하도록 초기 구성 진입점을 보완했다.
- 업무 등록·대기 작업과 capture 트랜잭션을 분리했다. 수집 실패는 업무를 보존하고 부분 capture를 rollback한다. 재시도는 최초 preview의 Case별 부모·옵션·프로파일 버전을 재사용한다. 멱등 키 충돌, 변경된 저장소/트리/프로파일, 잘못된 부모·다른 환경을 확인한다. 교차 검수에서 발견한 PostgreSQL cursor 호환, Case registry ID 불일치, Scene target_id=None 충돌, 첫 URL 선택 소실을 수정했다.
- additive migration `0030_folder_environment_profiles`로 환경 프로파일·scan·preview·등록·registry·job을 추가했다. `deploy.bat`/`update.bat`, 비DDL 앱 시작 원칙과 기존 데이터 보존 계약을 유지하며 새 의존성은 없다. 문서 지도·설계와 `docs/dashboard/folder-environment-guide.md`를 갱신했다.
- 최종 백엔드 검사 173 passed: 배포/schema gate/시작/마이그레이션/프로파일/등록 93, 환경 API/복구 10, dashboard parser/query/capture/API/미처리파일/contour/기존 folder discovery 70. 실제 PostgreSQL 17.11 임시 인스턴스에서도 환경 API/복구 10 passed. 추가로 확장 실행한 과거 dashboard read/write 테스트 2개는 현재 인증 bootstrap이 없는 기존 fixture의 401/503 실패였으며 이번 focused 결과 계약의 통과로 대체했다고 주장하지 않는다.
- 임시 PostgreSQL에서 빈 DB→head, 기존 0029→0030, 비DDL 앱 계정의 schema gate와 startup을 검증했다. 기존 프로젝트/의뢰/5종 catalog/저장 규칙/Case/capture/assets 7개 테이블의 전체 행과 원본 bytes가 보존됨을 upgrade 전후 비교했다. 임시 인스턴스는 pg_ctl fast stop으로 정상 종료했으며 실제 사용자 DB·설정·운영 서비스는 사용하지 않았다.
- 타입 검사, API client 재생성, frontend architecture check(215 sources), production build 통과. 기본 Vite config 번들러의 `.vite-temp` EPERM을 피하기 위해 기존 E2E와 같은 `--configLoader runner`로 production build를 수행했다. 기존 큰 chunk 경고는 남아 있다.
- 실제 Playwright 9개 시나리오를 최종 통과했다(첫 전체 실행의 7개 + 수정 후 2개). 폴더 화면은 추가 최종 단독 실행 1 passed(7.8s)로 1366×768/390px, 모바일 트리↔상세, 가로 overflow 없음, 등록 링크·이력·page error 없음까지 확인했다. Astra 시각 검수 증거는 Windows TEMP `environment-folder-final-qa/structure-desktop.png`, `structure-mobile.png`; 규칙 API는 합성 서버 폴더로 별도 통합 검증했다. Windows E2E 러너의 기존 taskkill/child cleanup 오류는 재현되어 프로세스 exit 1이며, 시나리오 통과와 구분한다.
- 사내 실자료·실제 저장 규칙의 일치 여부와 Windows Server 2022 폐쇄망 설치/업데이트/재부팅 실기는 수행하지 않았다. 로컬 구현과 검증을 완료했으며 이번 작업의 commit/push·사내 배포는 수행하지 않았다.

### 2026-09-19 환경별 폴더 연결 main 반영

- 사용자의 완료 후 main 직접 push 지시에 따라 위에서 검증한 구현·회귀 테스트·migration·사용 안내를 main에 커밋하여 origin/main에 반영한다. 원격 main을 fetch해 분기 차이와 충돌 여부를 확인했고, push 전 diff 공백 검사를 통과했다. 사내 설치·업데이트 실행은 포함하지 않는다.

### 2026-09-19 MatNexus 참고 UI 밀도 개선 계획

- 사용자 제공 `docs/dashboard/ui개선_matnexus.md`를 현재 글자 설정·전역 CSS·결과 목록·구조 검사와 대조하여 `docs/dashboard/ui-density-improvement-plan.md`를 작성했다. 문서 지도와 대시보드 안내에 미구현 계획으로 연결하고 사용자 원문은 보존했다.
- 기본 14pt·11~18pt 및 저장 키/legacy migration을 유지한다. 기준선→폭/토큰→프리미티브/대시보드→시범 영역 글자 규칙→후속 화면→최종 root 전환 순서, Luna/Terra 구현 책임, Sol/Astra 검수와 CSS 가드레일·인수 조건을 정의했다.
- Sol 독립 검토의 단계 순서·root 어댑터·grid 기본값·페이지 경계 의견을 반영했다. 현재 grid rowHeight 84/70/20px와 보고서 maxRows 18을 확인하고 사용자 글자 배율 자동 연동을 금지했다.
- 문서만 작성했다. 앱 구현·브라우저 렌더·성능 측정·DB 변경·배포는 수행하지 않았다. 실제 측정은 계획 P0에 남기고 문서 링크·공백을 검사했다.
- 계획의 Sol 재검수 승인 및 Astra 최종 검수 완료. 추가 차단 사항 없음. 구현 검증 승인을 의미하지 않는다.

### 2026-09-19 UI 개선 P0 기준선 조사 착수

- 사용자가 각 단계 결과 확인 후 진행하도록 요청해 P0 조사·계약 단계부터 수행한다. P1 이후 UI 변경은 사용자 확인 전 적용하지 않는다.
- Astra가 적용 계약을 작성하고 Terra가 설치된 PostCSS AST로 CSS 38개/15,779 선언을 조사했다. Sol 검수에서 확인한 ResultsWorkspaceGrid 74px를 기존 84/70/20px grid 보존 계약에 추가하고 primitive 공개 API·토큰 scope를 명시했다.
- 기존 workspace preferences self-test와 architecture check(215 sources/5 reviewed cross-feature imports)를 통과했다. 앱 코드·사용자 DB·설정 변경은 없다. 화면 기준선 수집과 최종 검수 결과는 아래에 후속 기록한다.
- P0 최종: 11개 PNG/JSON 기준선(1366×768/14pt, 핵심 mobile 390×844/18pt, 대표 wide 2560×1440/14pt) 수집. 실제 데이터 표시와 안정 UNCONFIGURED Run을 구분했고 root 가로 넘침 없음, page/console 오류 없음(인증 전 예상 401 별도). Playwright 시나리오 통과와 Windows runner taskkill AggregateError를 구분했다. 임시 spec 제거·15173/18000 잔존 LISTEN 없음 확인. 문서 링크/공백 검사와 Sol 독립/Astra 최종 검수 완료. P0 사용자 확인 대기, P1 앱 변경 미착수.

### 2026-09-19 UI 개선 P1 토큰·작업 폭

- 사용자 확인 후 P1만 구현했다. Astra 설계·통합, Luna 폭·패딩, Terra 토큰·CSS 가드레일, Sol 독립 검수로 진행했다. `docs/dashboard/ui-density-p1-implementation.md`에 범위·검증·한계를 기록하고 문서 지도를 갱신했다. P2는 다음 사용자 확인까지 미착수다.
- `tokens.css` 선행 로드와 shell 좌우 clamp(24px,2vw,40px)/모바일14px를 적용했다. 주요 작업 wrapper cap과 assigned-only1120/1920px를 제거하고 light/dark·media query 재정의와 min-width0을 보완했다. 기본14pt/11~18pt, 도움말·dialog·report 폭, grid 저장 배치와 업무·API·DB 로직은 보존했다.
- PostCSS8.5.22를 기존 전이 버전과 같은 devDependency로 고정하고 lock을 갱신했다. AST 기반 legacy important/폭/breakpoint/global selector baseline과 고정 해시, 이전 컨트롤·radius 토큰 검사를 추가했다. Sol 발견의 혼합 selector·#root·일반 태그·pill radius 우회를 수정했다.
- IAB에서 desktop/wide/mobile·light/dark·18pt 설정 및 데이터 탭을 검수했다. 최종 폭 회귀 E2E3 passed(46.3s), exit0; architecture216 sources/5 imports 및 두 checker self-test, preferences self-test, TypeScript, /home production build 통과. 실제 Caddy와 TEMP 빌드의 offline web routing 검사도 통과했다.
- LAN proxy self-test는 LAN 주소 fetch timeout 실패. pnpm offline install은 다운로드0을 관찰했지만 metadata EACCES 경고와 exit code 기록 누락 때문에 완전한 오프라인 설치 통과로 판정하지 않았다. 기존 Vite cache EPERM/runner cleanup 오류 및 중단 후 검수 서버 CONNECTION_REFUSED를 기록하고, 원본 Vite 설정+TEMP 캐시의 격리 서버로 최종 E2E를 통과했다. 기존 큰 chunk 경고는 남아 있다.
- 합성 TEMP DB·검수 탭·임시 서버만 사용했고 검수 종료 후 정리했다. 실제 사용자 DB·설정·운영 서비스 변경, 전체 배포 CI, 실제 Server2022 폐쇄망 실기, commit/push/배포는 수행하지 않았다.
- P1 Sol 독립 검수 및 Astra 최종 검수 완료, 추가 차단 사항 없음. 전역 selector 회귀를 세 독립 fixture로 분리한 뒤 CSS self-test와 architecture를 재통과했다. 문서 링크·공백 검사 완료. 사용자 확인을 기다리며 P2는 구현하지 않았다.

### 2026-09-20 UI 개선 P2 공통 컴포넌트·결과 화면

- 사용자 P2 진행 승인 후 Astra가 설계·통합·브라우저 검수, Terra가 Button/Input/Select/Table와 독립 CSS, Luna가 결과 개요·Case 화면, Sol이 독립 검수를 담당했다. 필요한 문서·코드와 관련 테스트만 사용하고 P3는 미착수로 유지했다.
- native props/ref를 보존하는 공통 컴포넌트에 P1 컨트롤·행 토큰을 적용했다. 결과 개요는25건 기본/10·25·50 선택 및 검색·프로젝트·필터·data·페이지 크기 변경의 첫 페이지 reset과 clamp를 구현했다. Case/Run Option/capture·이력·URL 문맥, 전역 글자 설정·DB/API·배포 경로는 보존했고 새 의존성은 없다.
- 동일1366×768/14pt에서 첫 결과 행 y627.1→578.1px, 실제 두 줄 행78→74px, 첫 화면 완전 노출1→2행을 확인했다. 모바일 목록 헤더·select 잘림 및18pt 날짜/버튼 겹침을 육안 검수로 발견·수정했다.
- 최종 고유 E2E10개 통과: 결과 개요4, Case4, 신규 페이지경계/재설정 및 반응형2(분할 실행, 각각 exit0). TypeScript, architecture224sources/5imports, CSS checker self-test, /home production build와 diff 공백 검사 통과. Sol 독립/Astra 최종 검수 승인, 추가 차단 없음.
- 신규 테스트 label 정정, 반복 reload 테스트의90→180초 전체 제한 조정, 실제 글자 폭 측정 보완, 임시 viewer fixture 준비 후 실패 검사를 재실행해 통과했다. 기존 큰 chunk 경고와 P1 오프라인/LAN 미확인 사항은 해결됐다고 기록하지 않는다. 상세는 docs/dashboard/ui-density-p2-implementation.md.
- 별도 TEMP 합성 DB·브라우저·임시 포트만 사용하고 정리했다. 실제 사용자 DB·설정·서비스, 실제 Server2022 폐쇄망 실기·전체앱 회귀·commit/push/배포는 수행하지 않았다. P2 사용자 확인 후 다음 단계로 진행한다.

### 2026-09-20 UI 개선 P3 공통 글자 설정 범위

- `workspaceFontSizeStyle` 어댑터로 두 authenticated shell의 `--ui-font-size` 주입을 통합했다. 기존 v1/legacy 저장 키와 기본 14pt, 11~18 범위·소수값 허용을 유지하고, 잘못된 입력은 14pt로 안전하게 되돌린다. P5 전까지 `documentElement` 글자 크기는 변경하지 않는다.
- 전역 light/dark control·본문·제목·Recharts 강제 글자 규칙과 결과 개요 행 규칙은 `[data-ui-density="v1"]` root 및 후손을 zero-specificity exclusion으로 제외했다. nav와 별도 custom-widget 글자 규칙은 유지했다. legacy `font-size !important` 천장 61은 늘리지 않았고, 변경한 selector fingerprint만 기준선에 다시 고정했다.
- workspace preference self-test, CSS architecture self-test, architecture check(225 sources/5 reviewed imports), TypeScript build가 통과했다. Chromium fixture에서 기존 영역의 blanket rule 적용과 migrated subtree 제외를 확인했다. Vite build는 config bundle 단계의 기존 `.vite-temp` EPERM으로 앱 컴파일 전에 중단됐다.

### 2026-09-20 UI 개선 P3 결과 화면 글자 역할 시범 적용

- Luna가 ResultOverviewDashboard와 SimulationDashboard에 `data-ui-density="v1"` 시범 root를 추가하고 본문·caption·subheading·section·title 역할을 토큰으로 매핑했다. 결과 필터·페이지·행 작업·Case/Run/capture 선택·native dialog를 포함하며, SimulationResultGraph와 자산 확대 dialog는 root 하위 native DOM으로 변수 상속을 유지한다.
- 시범 scope의 native 버튼/select/input은 `--control-h-*`와 `--radius-*`를 사용하고, 차트 SVG/tooltip/legend는 widget 의미를 보존하는 caption 토큰을 적용했다. grid rowHeight와 저장 배치는 변경하지 않았다. 전역 CSS·html/body·portal은 수정하지 않았다.
- TypeScript와 production build, CSS architecture self-test를 통과했다. 전체 architecture check는 Terra의 새 `styles.css` scoped font important 선언이 baseline allowlist에 아직 반영되지 않아 해당 1건만 남아 있다. 실제 사용자 DB·설정·운영 서비스·배포는 사용하지 않았다.

### 2026-09-20 UI 개선 P3 통합 검증 결과

- 위 개별 구현 기록의 일시적 architecture 실패는 selector 기준선 반영 후 해소했다. 공통 전역 CSS는 scope 제외를 위해 수정했으며 html/body 글자 크기는 바꾸지 않았다.
- 고유 E2E6개, TypeScript, architecture225/5, CSS·preferences self-test 및 TEMP /home production build 통과. 작은 화면의 긴 버튼 문구를 위해 원래 고정 높이와 추가 고정 높이를 제거하고 최소 높이를 유지했다.
- Astra 독립 최종 코드 검수 승인. Sol은 모델 용량 오류2회 및 재시도 실행 제한으로 미완료이며 승인으로 간주하지 않는다. P4/P5 미착수. 상세: docs/dashboard/ui-density-p3-implementation.md.

### 2026-09-20 AGENTS.md 협업·토큰 효율 지침 통합

- 사용자 요청에 따라 기존 협업 요구와 토큰 효율 가이드를 루트 AGENTS.md에 병합했다. 규모·위험별 투입, Astra 지휘/최종 검수·Sol 독립 검수·Luna/Terra 기능별 구현, 위임·협의·추론 강도·실패 대응 기준을 명시했다.
- 오픈소스 재사용, 기능별 독립 구조, 선택적 문서 읽기, 변경 기록, 기존 미커밋 변경 및 실제 사용자 상태 보존 요구를 유지했다. 작은 작업의 직접 처리 예외로 일괄 다중 에이전트 투입 비용을 줄이도록 정리했다.
- 배포 계약 구간이 HEAD 원문과 동일함을 비교하고 참조 문서 존재 및 AGENTS.md diff 공백 검사를 확인했다. 저위험 문서 수정으로 주 에이전트가 직접 검증했으며 앱 테스트·독립 모델 검수·배포는 수행하지 않았다. 기존 작업 로그는 보존하고 이 항목만 추가했다.

### 2026-09-20 프로젝트 AGENTS.md 중복 지침 축약

- 최신 Codex 사용 지침에 맞춰 일반 개발의 지휘 Astra 최종 검수 겸임과 대규모·고위험 변경의 별도 Astra 독립 검수를 명시했다. 공통 운영 설명을 줄이고 프로젝트 고유 규칙과 문서 경로를 유지했다.
- AGENTS.md 문자 수를 4,322자에서 2,312자로 줄였다(약 47%, 실제 토큰 절감률 측정은 아님). 배포 계약 구간은 수정 직전 원문과 완전히 동일함을 확인했다.
- 참조 문서 존재와 diff 공백 검사를 통과했다. 저위험 문서 수정으로 직접 검증했으며 앱 테스트·독립 모델 검수는 수행하지 않았다. 기존 미커밋 변경과 작업 로그를 보존했다.

### 2026-09-20 UI 개선 P4-1 및 데스크톱 전용 영구 정책

- P3 미완료 Sol 검수를 재시도해 승인받았다. 사용자 다음 단계 승인 후 Luna가 폴더 연결/조사 scope·타이포그래피·컨트롤, Terra가 합성 E2E, Sol이 독립 검수, Astra가 수정 통합·최종 검수를 수행했다.
- 최종 데스크톱1366/1920 ×14/18pt ×light/dark E2E2개, TypeScript, architecture225/5, CSS self-test, /home production build 통과. 초기 dialog 역할 누락과 컨트롤/보조 문구 누락은 수정 후 재통과했다. 상세 docs/dashboard/ui-density-p4-implementation.md.
- 사용자 영구 지침: 앞으로 모바일 전용 UI 개선·최적화·검수·신규 모바일 테스트 제외. AGENTS.md와 실행 계획에 기록하고 과거P0 요구보다 우선하도록 연결했다. 기존 반응형 코드의 일괄 제거는 하지 않는다. 새 모바일 표시 우회 제거 및 P4테스트 데스크톱 전환.
- 지시 전 종료된 모바일 포함 검사 결과는 과거 기록으로만 남긴다. 최종 완료 조건은 데스크톱으로 적용했다. 실제 사용자 DB/서비스·배포는 건드리지 않았으며 P4-2 의뢰/등록은 확인 전 미착수다.

### 2026-09-20 UI 개선 P4 전체 완료

- 사용자 `p4해줘` 승인으로 Terra 의뢰/등록·Workflow, Luna 비교/모델링·Help/설정 구현, Sol 독립 검수 및 보완 승인, Astra 통합 검수 완료. 모바일 제외 영구 지침 유지. P5는 미착수.
- 역할 토큰의 하위 누락 보완과 전역 font important6개 v1 제외. 색상 규칙·important상한61·Workflow 사용자 글씨/좌표/rowHeight84 보존. API/인증/데이터/배포 로직 변경 없음.
- 최종 P4 E2E6개+P3 typography2개+기존 비교/템플릿 기능2개=10개 통과. TypeScript, /home build, architecture228/5, CSS 및 architecture self-tests, diff-check 통과. 기존 큰 chunk 경고 잔존.
- 기존 unified 첫 검사 실패는 HEAD부터 Case 결과/기존 Run 대시보드로 바뀐 버튼을 결과 검토로 찾는 낡은 기대값이다. 통과로 간주하지 않았고 현행 journey 이동/뒤로/새로고침/Case 결과 문맥은 새 검사로 통과했다.
- 격리 TEMP 합성 DB와 서버로만 검수, 로컬 helper 차단. IAB 도움말18pt/light 및 등록1366/dark 증거 확인. 실제 서비스·사용자 DB·설정 및 배포 미실행. 범위/예외/증거는 docs/dashboard/ui-density-p4-implementation.md 참조.

### 2026-09-20 Codex Security 활용 지침 추가

- 사용자 요청으로 AGENTS.md에 보안 경계 변경 시 diff 스캔, 전체·심층 스캔 적용 조건, Sol/Astra 검수 연계와 중복 스캔 방지 기준을 추가했다.
- 격리 재현, 근거 중심 보고, 도구 실패·미검수 범위 공개, 독립 실행 및 기존 배포 계약 유지를 명시했다. 기존 미커밋 변경을 보존했다.
- 문서 내용과 AGENTS.md diff 및 공백 검사를 확인했다. 저위험 문서 변경으로 직접 검수했으며 보안 스캔·앱 테스트·독립 에이전트 검수는 실행하지 않았다.

### 2026-09-21 사용환경 파일 선택·JSON 값 검수 개선계획

- 사용자 요청에 따라 docs/dashboard/usage-source-review-improvement-plan.md를 작성하고 문서 지도에 연결했다. 기존 영상 그리드/종합 표를 유지하며 영어 평가명·원문 JSON 키, 결과 JSON+영상/이미지 기본 선택, 폴더 연결 3단계의 파일·키·값 사전 검수, 기술 진단의 검수/이력 화면 이동을 설계했다. 데스크톱 전용이며 구현은 하지 않았다.
- 상세.md 3.2와 GitHub #30 사용Json 표를 대조했다. 이슈는 완전한 JSON 파일이 아니라 파일명/키/값 표이고 댓글은 없다. 순수 함수 합성 재현에서 해당 Settle 접미사·최상위 키는 1.18/READY, 같은 폴더에 settings.json={} 추가 시 AMBIGUOUS/null을 확인했다. 사내 결측의 원인 확정과 구분했다.
- 미선택 파일은 파싱 제외, 선택 원본의 오류/중복은 게시 전 검수, 결측은 0이나 이전값으로 대체하지 않는 계약을 포함했다. 과거 capture/프로파일 보존, 확정 선택·내용 해시·매핑 버전의 재시도 일관성, 유통환경 CSV 회귀와 향후 보안 변경분 검수를 계획에 명시했다.
- Sol 독립 계획 검수 승인(blocking 없음), Astra 최종 확인. 과거 capture 원문 키 표시의 출처 한계와 원시 진단 비노출/의미 상태 보존 회귀를 추가했다. 문서 링크·diff 공백 검사 완료. 앱/DB/설정 변경, 실제 사내 JSON 전체 검사, 브라우저/배포/보안 스캔, commit/push는 수행하지 않았다.
- 추가 사용자 지시에 따라 Slope_Angle의 방향별 `Slope Angle (deg)`와 `OK/NG`를 두 하위 행으로 독립 검수·표시하도록 계획을 구체화했다. 한 키의 결측이 다른 정상값을 숨기지 않고, 판정은 각도에서 재계산하지 않으며 두 키를 중복 파일로 취급하지 않는 인수 기준을 추가했다. 현장 오류 원인은 미확정으로 유지했다. 저위험 계획 문서 보완으로 주 에이전트가 직접 내용·diff를 확인했으며 구현·추가 독립 검수는 수행하지 않았다.


### 2026-09-21 사용환경 파일·값 검수 및 원문 결과 표시 구현

- 최신 사용자 지시(계획 구현, 검수 최소화)를 적용했다. Terra 수집/검수 계약, Luna 초기 화면, Terra 화면 통합, Sol 집중 변경분 검수, 지휘 Astra 결과 조회·통합으로 진행했다. 별도 Astra/다단계 보안 스캔·전체 회귀·모바일 검수는 수행하지 않았다.
- 기본 결과 JSON+영상/이미지 선택, CSV 명시 선택, 평가 폴더+결과 접미사 필터를 본문 읽기 전에 적용했다. 폴더 연결 3단계에서 Case별 원본/실제 JSON 키 segment/값/타입 검수, 후보 선택, 명시 제외·취소와 부분 게시 확인을 추가했다. 모든 Case가 검수되어야 새 화면에서 등록할 수 있다.
- 프로파일 JSON에 형식·키 경로를 재사용하고 preview JSON에 선택·해시·검수 스냅샷을 저장했다. 등록 후 스냅샷은 고정하며 이력에서 조회한다. 등록/재시도는 선택 원본 변경과 규칙 revision 변경을 차단한다. 기존 프로파일/캡처/유통환경을 보존하고 migration·의존성·배포 진입점 변경은 없다.
- 평가명과 원문 측정 키를 영어로 표시하고 Slope_Angle의 각도와 OK/NG를 독립 행·상태로 처리했다. 한 값의 오류가 다른 정상값이나 비교를 숨기지 않는다. 원시 오류/미지원 확장자 패널은 사용환경 결과 화면에서 숨기고 검수 정보는 유지했다. 추가 미디어가 정상 수치를 AMBIGUOUS로 만드는 경로를 분리했다.
- 검증: 조회 17개, 선택기/기존 파서·프로파일 32개 통과. 최종 서버 gate 보완 뒤 API 5개+선택기 5개, 추가 프로파일 1개 통과(선택기 중복 포함, 합산 아님). 누락 상수·추가 미디어 문제는 해당 실패만 수정 재검사했다. Sol의 게시 gate/재시도 revision 지적과 프런트 values 타입 지적을 해소했다. OpenAPI 재생성 및 TypeScript+Vite build 통과, Vite 임시 파일 EPERM 1회 후 재시도 통과.
- 데스크톱 1440x1000 Playwright 결과 화면 1개 통과: Settle/음수, Slope 두 행·부분 값, 원시 진단 비노출과 환경 전환 확인. 실행기 자식 프로세스 정리 오류로 전체 명령은 비정상 종료했으므로 테스트 통과와 구분한다. 종료 후 테스트 포트 리스너 없음. 폴더 검수 전체 브라우저 E2E·사내 원본/실서비스·폐쇄망 설치는 미실행.
- 상세.md 3.2와 문서 지도를 갱신하고 개선계획 9절에 사내 사용 순서를 기록했다. 실제 사용자 데이터·설정은 건드리지 않았다. 이번 단계 commit/push는 미실행.

- 최종 CSS 구조 검사에서 원문 키의 고정 max-width를 제거하고 caption 토큰·줄바꿈으로 보완했다. 프런트 architecture 검사(229 source files / 5 reviewed imports)와 수정 후 Vite build가 통과했다. 기존 큰 chunk 경고는 남아 있다.

### 2026-09-27 UI 개선 P5 완료

- 사용자 `응 p5 해줘` 승인. Astra 목표·계약·최종 통합, Sol 세부 설계/조정, Luna(max) 프론트 구현/국소 검사, 별도 Sol 독립 검수 승인. 기능·배포·사용자 데이터 계약 보존.
- 인증 작업공간 공통 root 글자 설정/복원과 rem 역할 토큰 적용. 기존 inline 값·priority·소수 설정·저장 키 유지. 전역 blanket font!important 제거, 실제60→15개 및 baseline상한15로 감축. 남은15개는 shell/nav/custom widget·portfolio 예외. 간격/그리드/보고서 px 기하 유지.
- 신규17 route×2조건(1366/18pt/dark,1920/11pt/light)과 fractional16.25/root priority 복원, P3/P4/초기화면 포함 관련 E2E14개 최종 통과. 첫 통합11통과/3실패는 본문역할 기대값과 비동기 대기 테스트를 고친 후 관련4개 재통과. 소스 결함 breadcrumb light13px와 여러 caption/selector/cascade 누락도 수정/확인했다.
- TypeScript, architecture/CSS/root self-tests, preferences/API/runner 검증, API 계약 unchanged 및 최종 /home build 통과. Node26의 제거된 transform flag는 번들Node24로 대체해 통과. 기존 chunk경고 유지. 별도 CI/전체suite/Server2022 실기를 수행한 것은 아님.
- 격리 TEMP 새 DuckDB/QA계정과 localhost15184/18103 사용, 실사용 helper 차단. IAB 18pt/light→dark 화면/조작 정상, 개발중router blocker경고1건은 최종reload에서 재발관측없음. 실제 사용자 DB·설정·서비스 및 모바일 검수·배포·commit/push 미실행.
- 독립 Sol 승인 및 Astra 최종 통합 완료. 상세와 예외/증거: docs/dashboard/ui-density-p5-implementation.md. 임시 검수서버와 브라우저는 종료한다.

### 2026-09-28 개인 PC 설정 지원 중단·화면 비노출

- 사용자 결정: PC도우미를 통한 개인 PC 환경 설정 지원을 중단하고 공용 업무·결과 관리에 집중한다. 중앙·클라우드 배치 설정은 후속 보강한다. `docs/execution-environment-policy.md`와 문서 지도에 기록하고 기존 설치·로컬 실행 문서에 최신 정책 우선 표시를 추가했다.
- Luna(max) 프런트 구현, 독립 Sol 변경분 검수, Astra 문서·통합 확인. `내 PC 설정` 메뉴·초기 진입 버튼·로그인 연결 권유를 제거하고 기존 URL은 허용 화면으로 이동시킨다. 프로젝트/메뉴 없는 계정의 무한 대기를 방지하고 비밀번호 변경은 공통 계정 dialog로 분리했다. OIDC/disabled 모드에는 변경 버튼을 표시하지 않는다.
- 도우미 API·배포본·기존 작업 화면의 로컬 실행·연결 데이터·실행 이력을 보존한다. 이번 변경은 설치된 도우미 중지·제거나 중앙 실계산 구현이 아니다. DB migration·의존성·배포 진입점·인증/권한 API 변경 없음.
- 검증: 최종 프런트 구조 검사(232 files / 5 approved imports), 라우팅 self-test, TypeScript+Vite build 통과. 기존 큰 chunk 경고 유지. 격리 데스크톱 `personal-onboarding-status.spec.ts` 1개 통과(프로젝트 없는 계정의 URL 이동·안내·비밀번호 dialog 닫기/재열기 초기화). Windows E2E 실행기는 테스트 후 자식 PID 정리 taskkill 오류로 exit 1이므로 전체 명령 성공으로 기록하지 않는다.
- 독립 Sol 지적(빈 메뉴 fallback, dialog 글자 크기 토큰, loopback 도우미 호출 감시, 과거 화면 테스트 기대값)을 해소했고 미해결 정적 지적 없음. 보안 경계 변경이 없는 UI 진입점 정리로 security-diff-scan은 실행하지 않았다. 문서 링크·diff 공백 확인 완료. 전체 E2E·실사내 PC·폐쇄망 배포·commit/push는 수행하지 않았다.
- E2E 명령은 `node scripts/run-e2e.mjs personal-onboarding-status.spec.ts`. 합성 계정 전용 `backend/data/e2e-playwright.duckdb`를 사용했다. 종료 오류 후 해당 PID 20880/27924 부재와 테스트 포트 15173/18000 해제를 확인했다. 일반 계정 legacy URL 별도 spec(`personal-pc.spec.ts`)은 갱신했으나 이번 실행에는 포함하지 않았다.


### 2026-09-28 작업 실행 PC도우미 패널 비노출

- 사용자 후속 요청으로 SimulationWorkbench의 LocalProgramPanel import·마운트를 제거했다. 연결·프로그램 등록·로컬 실행·패널 내 이력 UI와 해당 패널의 도우미/중앙 로컬 실행 요청을 함께 중단한다. 일반 작업 상태·DEMO_ONLY 배치와 기존 API·DB·이력 데이터는 유지한다.
- 실행 환경 정책과 관련 과거 구현 문서에 작업 실행 화면 비노출 범위를 반영했다. 기존 도우미 구동 E2E를 합성 환경의 패널 비노출·작업 선택·loopback/API 요청 0 검사로 대체했다. 영향이 명확한 두 줄의 화면 마운트 제거로 주 에이전트가 직접 구현·검증했으며 별도 에이전트 검수는 수행하지 않았다.
- 검증: 프런트 구조 검사(232 files / 5 reviewed imports), TypeScript+Vite build 통과. 기본 권한 빌드는 Vite 임시 파일 EPERM으로 실패했고 권한 확장 후 통과했다. 기존 chunk 크기 경고 유지. 초기 E2E는 이미 진행 중인 fixture를 READY로 가정해 실패하여 상태 독립적인 작업 선택 검사로 수정했다. 최종 local-programs.spec.ts 데스크톱 1개 통과(7초).
- E2E 전체 명령은 테스트 이후 Windows taskkill 정리 오류로 exit 1. 해당 PID 6732/13104 부재와 테스트 포트 15173/18000 리스너 없음 확인. 실제 사용자 DB·설정·서비스·도우미를 사용하지 않았다. 전체 회귀·모바일·폐쇄망 배포·commit/push 미실행. DB·인증/권한·실행 API 경계를 변경하지 않아 보안 스캔은 실행하지 않았다.
### 2026-09-30 기능 구조·데이터 연결 HTML 도식

- 현재 아키텍처, 결과 등록 계약, SPDM 연동 및 실제 라우터 구성을 대조해 `docs/current-system-diagram.html`에 기능 구조와 데이터 연결을 각각 블록·선으로 표시했다. 단일 HTML/SVG이며 외부 리소스는 없다. PC 도우미의 현재 화면 비노출과 새 결과 등록·기존 새로고침 경로 분리를 명시했다.
- 문서 지도에 HTML 링크를 추가했다. 정적 SVG 문법·상대 링크·diff를 확인했다. 로컬 `file://` 브라우저 미리보기는 브라우저 보안 정책으로 차단되어 수행하지 못했다. 실제 사용자 데이터·설정·서비스는 건드리지 않았다.

### 2026-09-30 결과 등록 저장 위치 Folder Schema 연결

- 결과 등록 02의 Case·결과 위치 후보를 현재 Folder Schema scan/profile과 적용된 역할에 연결했다. 저장 연결 정보는 별도 테이블에 보관하며 편집·삭제는 그 링크 행만 바꾼다. 실제 SPDM 폴더·파일과 기존 초안·검수·캡처 이력은 유지한다.
- 링크 API에 결과 등록 권한, 의뢰/프로젝트 소유권, 현재 scan/profile/path 검증, 수정 revision 및 충돌 검사를 적용했다. 새 초안은 현재 schema 문맥을 확인하고 과거 초안 검수·게시 경로는 기존 기록을 읽을 수 있도록 보존했다.
- additive 0033 migration, DuckDB mirror, PostgreSQL schema/export와 결과 등록 화면·API·문서를 갱신했다. Windows Server 2022 실제 폐쇄망 설치/업데이트는 수행하지 않았다.
- 검증: 등록 API/path, run identity migration, Postgres startup 테스트 109개 통과·1개 skip. 프런트 TypeScript와 Vite build 통과. 빌드의 기존 500 kB 초과 chunk 경고는 남아 있다. 테스트는 격리된 임시 DB와 합성 SPDM 경로를 사용했다.
- 독립 검수 후속 보완: 늦게 등록된 다른 업무의 결과 경로 소유권이 targets/folders/저장 링크 후보를 통과하지 않도록 다시 검사하고, 생성·수정 시 경로 lock 뒤에도 충돌을 확인한다. 전환된 target 응답은 UI의 새 선택 문맥에 덮어쓰지 않도록 비동기 후 재확인한다. 합성 소유권 충돌 포함 범위 테스트 4개와 TypeScript/Vite build 통과.
- 소재 표의 Part 이름·번호 표시 순서를 바꾸고 우측 상세 영역의 외곽선·그림자를 제거했다. 최종 통합 테스트 144개 통과·1개 skip, Windows 배포 DB 보존 테스트 179개 통과·2개 skip, 배포 자체점검 9개 통과, 프런트 빌드 통과. 독립 검수에서 Scene 간 덱 혼합 등 3건을 수정·재확인했다. Codex Security 변경분 검사 `3fc611a7-86ae-4ec9-bdfe-2174d643ab7f`는 원본 snapshot의 변경 소스 17개에서 확인된 취약점 0개로 완료했으며, 스캔 중 추가한 UI 표시 변경은 수동 확인했다. 실제 사내 SPDM·DB와 Windows Server 2022 폐쇄망 실설치는 미검증이다.

## 2026-10-02 SPDM 유통환경 예제 파일
- 사용자 지정 `E:\shared\SPDM (Admin)\75R9J_PV\[WR-0001]_[유통_환경]\Working`의 두 Case × 2_Face/3_Face에 합성 파일 24개를 추가했다. Scene마다 results CSV 4종과 Part/소재 .inc 2개; 기존 파일 덮어쓰기 없음.
- 실제 distribution/Radioss 파서 검증: Scene별 32개 관측, 결과 품질 오류 없음, Part→Property→Material 참조 정상, 3점 함수 곡선 확인. 밀도 단위 미선언 경고 및 응력 단위 UNCONFIRMED는 예제 한계로 안내. 복사본 24개 SHA256 일치 확인.
- 생성기·검증 보고·한국어 안내는 `tmp/spdm-distribution-example/`에 보관. 앱 코드·실제 DB·설정·서비스 변경 없음. UI 등록/조회는 미실행이며 Folder Schema Refresh 및 결과 등록 필요.

## 2026-10-02 초기 데이터 구성 화면 스타일 원인 분석
- 첨부 화면과 소스 대조: BootstrapWorkspaceShell은 bootstrap-workspace/data-theme만 제공하나, 결과 등록의 기존 밝은 배경은 styles.css의 .light-theme에, 저장소 설정은 DataWorkspace.css의 .app-shell에 의존한다. 초기 구성 shell에서는 해당 규칙이 매칭되지 않아 밝은 글자 토큰과 고정 짙은 배경·기본 details 표시가 혼재한다.
- App.tsx의 overview/dashboard 및 화면·문맥 조건에 따라 초기 구성 shell을 선택하므로 상태별로 간헐적으로 보일 수 있다. 실제 발생 시 API 실패·문맥 복원 여부는 브라우저/서버 로그 미수집으로 미확정.
- 분석만 수행. 앱 코드·실제 DB·설정·서비스 변경 및 런타임 재현/테스트 없음. 기존 미커밋 기록 보존.

## 2026-10-02 폴더 규칙·Working/Final 단계별 개선 시작
- 사용자 승인 범위를 docs/plans/folder-schema-working-final-implementation.md에 정리하고 문서 지도·현재 계획 목록에 연결했다. 기존 미커밋 작업 로그와 tmp 자료를 보존한다.
- 예제 구조는 읽기 전용 참고다. Working 깊이별 역할, Scene 직속 입력/결과 공존, 공통 schema 조회 복구, 규칙 편집·삭제 및 Case 최종확정으로 작업 범위를 나눴다. 구현·검증 결과는 완료 후 별도 기록한다.
- 배포 보존 gate: 격리 환경에서 관련 backend 검사 179 passed, 2 skipped. 실제 Server 2022 폐쇄망 설치/업데이트/재부팅은 미실행.

## 2026-10-02 폴더 규칙·공통 조회·최종확정 구현 완료
- 저장 규칙 불러오기·편집·복사·개정 저장·삭제(참조 이력 보존), Working 상대 깊이 역할 전파와 개별 예외를 구현했다. 의뢰 직속 Final과 그 하위는 조사·등록·capture에서 제외한다.
- Case 하위 목록이 capture.payload.runs에만 의존하던 문제를 수정했다. 확정 Folder Schema의 Case/하중/Run/Option/Scene ID·경로로 공통 조회하며 미수집 구조도 표시한다. 값·그래프는 선택 capture를 유지한다. Scene 직속 입력/결과와 빈·INC 전용 Scene 등록 후보를 지원하고, 연결된 Option ID와 Scene 필터의 불일치를 해소했다.
- Case 선택기 옆 최종확정→파일 미리보기→확정을 구현했다. RAD/INC는 의뢰 Final/CAE, 결과·보고서는 Final/Reports의 Case/확정 개정/상대 계층으로 복사한다. 원본·기존 Final을 보존하며 capture 호환 Scene만 사용한다. 서명된 기록, 해시·권한 재검사, Windows 목적 경로 고정, 부분 실패 재시도·완료 멱등과 구조 문맥 확정 이력 표시를 검증했다.
- 검증: 규칙 53개, catalog 집중 24개, 등록 API/path 30개(1 skip), 후속 Scene/경계 6개 및 Option ID 1개, 확장된 Windows 최종확정 API 2개 통과. TypeScript/Vite build·API 생성/self-test, 관련 데스크톱 UI 12개, 배포 계약 179개(2 skip) 통과. 건수는 중복 범위를 포함하며 합산하지 않는다. 실패 지점을 수정한 뒤 해당 검사를 재실행했고 최종 독립 Astra 기능 검수도 통과했다.
- Codex Security 고정 변경분 스캔 a5982897-a812-455d-9bbe-2996db3a512d 완료: 28개 소스, CWE-400 medium 1건. 후속 수정에서 metadata 4 MiB 및 상태 조회 항목·누적 읽기 한도, 파일 핸들/재파싱 검사·오류 격리를 적용하고 합성 회귀·주 에이전트 수동 검수로 확인했다. 기준 보고서는 수정 전 기록이며 최신 수정에 대한 플러그인 재스캔·독립 보안 재검수 완료로 기록하지 않는다. 앞선 독립 Windows junction 경계 검증은 통과했다.
- 계획·현재 기능 문서를 갱신했다. 기존 작업 로그·tmp 자료 보존, 이번 작업에서 실제 사용자 DB·설정·서비스·공유 폴더 변경 없음. 신규 의존성·migration·배포 진입점 변경 없음. 기존 architecture 검사 4건과 Vite chunk 경고는 남아 있다. 실제 Server 2022 폐쇄망 설치/업데이트/재부팅, SMB·POSIX 동시 경로 교체, commit/push/deploy 미실행.

## 2026-10-02 폴더 미리보기 가독성·결과 읽기 실패 후속 수정
- 사용자 화면의 CONFIRMED/확인 필요 0과 남은 경고가 모순되는 문제를 수정했다. 수동 확정 후 이전 조사 진단을 해소하고 실제 Scene 상위 역할 오류는 다시 진단한다. 경로·역할·긴 이름·상태는 열 내부에서 줄바꿈하고 경고는 별도 행에 표시한다. Refresh의 세 열은 구분했다.
- 실제 backend/uvicorn-error.log의 FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED를 읽기 전용으로 확인했다. 등록 이력의 다른 의뢰 역할까지 연결 후보에 섞이던 경로 복원을 현재 환경·프로젝트/의뢰 target ID로 제한했다. 진짜 다중 연결은 계속 차단하며 수집 준비 실패는 FAILED·오류 코드로 보존해 재시도한다.
- 독립 Astra가 발견한 다른 의뢰 Case의 빈 결과 완료·잘못된 소유권 저장 문제를 수집 전 Case/의뢰 경계 검사로 차단했다. 해당 Case는 CAPTURE_CONTEXT_MISMATCH로 안내하고 의뢰별로 등록한다. 기존 이력·데이터 삭제나 migration·의존성·배포 변경 없음.
- 최신 격리 백엔드 회귀 10개 통과(52.25초), TypeScript/Vite build 통과(기존 chunk 경고). 독립 Astra 최종 기능 및 수동 보안 변경 검수 통과. 유통환경 예제 형태의 두 Working Case + 빈 WR2에서 Scene CSV의 실제 관측과 실패 후 완료 재시도 확인. 실제 DB·서비스·공유 폴더는 검증 대상으로 사용하지 않았다.
- Codex Security 후속 고정 변경분 스캔 a059e8a2-b4b2-450d-b146-1519829ace04, baseline f2e7b39, digest b7938c88eafee093d137bca120027ef567f53283230b7725d57fc9f6a9de5f4d: 6개 파일 검수·보안 발견 0건으로 완료. 이후 Case 범위 guard·안내·강화 테스트 및 문서 변경은 독립 수동 검수·회귀 범위다. 전체 작업 트리 변경 경고는 이 후속 변경을 포함하므로 완료 스캔을 최신 코드 재스캔으로 주장하지 않는다.
- 최종 Playwright 데스크톱 1개 통과(5.8초), runner exit 0: Chromium 1440×1000·14pt, 라이트/다크 긴 이름·경로 줄바꿈 및 상태 열 겹침 없음, URL/제목·정상 화면·오류 overlay 없음·로그인 후 console/page 오류 없음. Browser 플러그인 미가용으로 기존 실행기 사용. 초기 정리 권한 오류 뒤 권한 확장 실행, 로그인 전 의도된 401을 검사 범위에 넣던 테스트 수정 후 통과했다. 증거 스크린샷은 OS temp의 environment-folder-preview-long[-light]-desktop.png에 보존한다.

## 2026-10-02 기존 의뢰 ID와 변경된 조사 경로 연결 복구
- 사용자 실패 이력의 읽기 전용 진단에서 기존 업무 ID와 SPDM (Admin) 상위 폴더를 포함한 새 조사 경로의 생성 ID 불일치를 확인했다. 여러 프로젝트·의뢰 자체가 충돌 원인은 아니다. 진단은 PostgreSQL READ ONLY transaction에서 SELECT만 실행했고 rollback했다.
- 현재 root/환경의 선택 업무에 적용된 미리보기와 과거 동일 업무의 확정 registry를 대조해 정확한 프로젝트·의뢰 이름 및 프로젝트 아래 의뢰 상대 경로가 일치하는 단일 경로만 복원한다. 미적용 미리보기, 기존 타 업무 target ID, 근거 없는 경로, 중복 후보는 거부한다. 복원된 REQUEST 경계는 선택 의뢰 ID를 유지한다. 실제 중복 AMBIGUOUS와 연결 없음 REQUIRED 안내 및 조회 picker 상태를 구분했다.
- 격리 백엔드 회귀 9개 통과(52.25초): 실제 서로 다른 두 임시 root에서 기존 실패 등록 재시도 후 선택 Case 완료·정확한 소유자·결과 값 33.0 확인, 타 의뢰/프로젝트 Case 저장 거부 및 중복/근거 없음/타 업무 ID/미적용 preview 검증. TypeScript/Vite build 통과(기존 chunk 크기 경고); 최초 빌드 캐시 EPERM은 허용된 권한 확장 실행으로 해결했다. Sol diff 검수와 독립 Astra 기능·보안 검수 완료.
- Codex Security diff e75ec0a5-7c82-4a5c-92f7-a95ddfbca01a 완료: baseline 7757f28, snapshot c2110a8a443bf45a7f9ebec34e2600ac93516cfc01872aa8142a7e6aba899492, 소스 3/3 및 변경 테스트·문서 검토, 보안 후보/발견/deferred 0건. 완결 문서 재확인. 도구의 연결 대화 누적 사용량 1,807,806 tokens, cached input 1,781,376이며 이번 수정의 증분 사용량으로 해석하지 않는다. 스캔 이후 변경은 이 작업 기록뿐이다.
- 사용자 DB·설정·실행 중 서비스는 변경하거나 검증 대상으로 사용하지 않았다. 실제 결과 재읽기와 Windows Server 2022 현장 배포는 미실행. migration·의존성·배포 계약 변경 없음; 프로그램 업데이트/재시작 후 기존 등록에서 미완료 결과 다시 읽기를 사용한다.

## 2026-10-02 소재물성 계층 선택 UI 계획
- 사용자 결과 읽기 정상 확인 후, Case 결과와 같은 해석 Case→하중경우→Run Case→Run Option→Scene 선택을 소재물성에 적용하는 후속 계획을 기존 소재물성 구현 계획에 추가했다. 현재 소재 catalog hierarchy 활용과 Scene 없는 확정 가지를 위한 상위 목록 확장, 탭/URL 문맥, 상위 변경 시 하위 초기화, 덱 없음·Option 생략·동명 폴더 및 데스크톱 가독성 검증을 정리했다.
- 문서 지도와 현재 계획 목록을 연결했다. 계획만 작성했으며 앱 코드·DB·설정·서비스 변경이나 런타임 검증은 수행하지 않았다.

## 2026-10-02 Case 결과 보고서 호환 계획 추가
- 기존 ReportExportDialog/controller의 분석 페이지 Overview·run 문맥과 Case 결과의 capture 문맥 차이를 확인해 UI 개선 계획에 보고서 원본 어댑터, 현재 선택 범위 표시·고정, 기존 레이아웃/회사 템플릿/편집 가능한 PPTX 재사용 및 선택 변경 시 초안 무효화를 추가했다. 기존 분석·비교 내보내기 회귀와 생성 PPTX의 값·미디어 범위 검증을 완료 조건으로 명시했다.
- 계획 문서와 목록만 갱신했다. 앱 코드 변경·PPTX 생성·실제 DB/서비스 검증은 수행하지 않았다.

## 2026-10-02 Refresh 목록과 폴더 이동 개선 계획 추가
- 사용자 피드백의 신규 의뢰 미표시, 이름 변경 후 Case 표시명 잔존, /home/ 배포의 폴더 연결 링크 오류를 UI 개선 계획의 우선 단계에 추가했다. 현재 Refresh의 의뢰 범위·catalogRevision 갱신, 상위 requests 전달, 저장 이름 overlay와 일반 a 링크의 basename 누락을 소스에서 확인했다. 이름 변경의 실제 실패 재현은 미실행으로 구분했다.
- 프로젝트 의뢰 동기화·상위 목록 재조회·현재/과거 이름 구분·모호한 rename 검수·직접 base URL 이동·권한 및 반복/부분 실패 검증을 계획했다. 문서만 변경했고 구현·실제 DB/서비스 검증은 수행하지 않았다.

## 2026-10-02 공용 폴더 새로고침 및 보고서 UI 계획 보완
- 최상단 저장 폴더 새로고침을 의뢰 목록·확정 폴더 계층·Case/소재물성 탭의 공용 갱신 진입점으로 정리하고, 페이지 내부 중복 Refresh 제거와 공용 진행/부분 실패 표시를 완료 조건에 추가했다. 실패한 결과 수집 재시도는 별도 동작으로 유지한다.
- 보고서 내보내기를 Run 대시보드와 Case 결과 양쪽의 공통 창·템플릿·PPTX 생성 기능으로 명시했다. 계획 문서만 수정했으며 앱 구현·런타임 검증은 수행하지 않았다.

## 2026-10-02 신규 Scene 결과 등록 즉시 반영 계획 추가
- 사용자 보고의 4_Edge 생성·업로드 후 Scene 미표시와 파일 부재, DB 미저장 추정을 후속 계획에 기록했다. 현재 결과 등록 계약의 초안 업로드/승인·게시 구분과 폴더 생성·mirror 후 자동 Refresh를 기준으로 단계별 저장·실패·재시도 및 Case 결과 즉시 갱신을 우선 완료 조건에 추가했다.
- 계획만 수정했다. 실제 파일·DB 실패 원인은 미확정이며 운영 데이터·서비스 검증과 앱 구현은 수행하지 않았다.

## 2026-10-02 Scene 깊이 규칙 일반화
- 신규 폴더 목록 반영 요구를 특정 4_Edge가 아닌 확정 Folder Schema의 Scene 상대 깊이에 생성되는 모든 해석 Scene으로 명확히 했다. 같은 규칙의 여러 Case/Run/Option 가지에 동일 역할을 적용하고 이름·파일 유무와 무관하게 목록에 포함하며, 여러 이름·가지 및 다른 깊이·제외 영역을 검증하도록 계획을 보완했다. 문서 변경만 수행했다.

## 2026-10-02 Case 결과 중심 사용 흐름 재설계 계획
- 사용자 시나리오(SPDM 폴더 정본, 즉시 반영, 환경별 Folder Schema, 레시피 PPTX/PDF, 영상 그리드, Final 보고서 저장)와 현재 코드를 대조해 `docs/plans/case-results-workflow-redesign.md`를 작성했다. 결정: UI A안, 서버 LibreOffice PDF 변환, 검수를 Final 지정 시점으로 이동, 자동 변경 확인 30초.
- 확인한 근거: 상단 저장 폴더 새로고침은 전역 관리자·전역 SPDM API이고 페이지 Refresh는 result.import 범위 API로 서로 다르다. Case 결과와 소재 catalog의 계층 투영·URL 상태 관리가 이원화돼 있다. PPTX는 브라우저(pptxgenjs)에서 생성하므로 Final/Reports에 앱 보고서가 저장되지 않는다. DropVideoGrid는 loadCaseId 전용이다.
- 소재물성 계획의 2026-10-02 후속 UI 절은 새 계획으로 대체 표시했다. 문서만 변경했으며 앱 코드·DB·설정·서비스 변경과 런타임 검증은 없다.

## 2026-10-02 Case 결과 재설계 0~1단계 구현 (Claude, 브랜치 claude/case-results-redesign)
- 역할: 리드(설계·통합·커밋), 원인 조사 에이전트(격리 재현), 프런트 구현 에이전트, 구현에 참여하지 않은 독립 검수 에이전트.
- 0단계: 4_Edge 업로드 실패 원인 두 가지(빈 새 Scene의 역할 미상속 → 409 RESULT_FOLDER_SCHEMA_STALE, 준비 응답 Scene ID 불일치 → 409 RESULT_CONTEXT_CHANGED)를 격리 합성 재현으로 확정·수정했다. 실제 서버 로그와 공유폴더는 읽기 전용으로만 확인했다. 폴더 연결 링크는 router basename을 쓴다.
- 1단계: 공용 Folder Schema 계층 투영, 소재 catalog hierarchy·Scene별 ID, 소재 경로 선택 UI, router 기반 공용 URL hook.
- 독립 검수 지적 4건(혼합 역할 CONFLICT 회귀, 임의 폴더 Option 승격, RESULTS ID 누락, 재해석 capture 추가) 수정. 프런트 지적(sentinel URL 노출)은 3단계에서 조회 문맥 선택기 제거로 해소 예정.
- 검증: 백엔드 관련 회귀 172 passed + 신규 11 passed(격리 DuckDB·임시 SPDM root). tsc·build·test:routing/api/preferences/architecture self-test 통과. check:architecture는 기준 커밋과 같은 기존 4건만 실패. e2e: materials 5/5 통과, simulation-dashboard·environment-folder-flow·spdm-storage-workflow의 실패는 기준 커밋 725f4f7에서도 동일하게 실패하는 기존 항목이며 신규 실패 없음(Chromium headless shell 1194 사용).
- 미실행: Codex Security 플러그인 스캔(도구 없음, 독립 수동 검수로 대체 — 플러그인 스캔 완료로 기록하지 않음), PostgreSQL 통합, Windows/Server 2022 실행, 실제 사용자 DB·서비스. migration·의존성·배포 진입점 변경 없음.

## 2026-10-02 Case 결과 재설계 2단계 자동 반영 (Claude, 브랜치 claude/case-results-redesign)
- 사용자 결정: 3단계 기준 화면은 32인치 4K(150% 배율 2560×1440 기본, 100% 3840×2160 보조).
- `POST /api/folder-discovery/environments/sync`와 화면의 진입 시·30초 주기 확인을 추가하고 페이지 안 Refresh/다시 읽기 버튼을 제거했다. 빠른 확인은 폴더 구조와 결과 관련 파일(결과·미디어·덱·보고서)의 이름·크기·수정 시각만 비교한다. 결과 무관 파일(로그) 변경은 갱신·capture를 만들지 않고, 결과 파일은 내용 digest로 판단한다(같은 mtime의 내용 변경은 전체 Refresh에서 capture).
- 독립 검수 지적 7건(로그 변경마다 전체 갱신·capture, 충돌 반복 조사, 구 snapshot 반복 판독, 수동 확인의 전체 판독, 재스캔 경합, 비 ValueError 처리, 중복 revision)과 계약 문서 누락을 수정했다. 기존 회귀 테스트가 같은 mtime 내용 변경 capture 누락을 잡아 결과 내용 지문으로 고쳤다.
- 검증: 백엔드 관련 회귀 188 passed(격리 DuckDB·임시 root). tsc·build·routing/api/architecture self-test 통과, check:architecture 기존 4건만. e2e materials 5/5, folder-working-final 3/3; simulation-dashboard·spdm-storage-workflow·request-centric-workspace·environment-folder-flow 실패는 기준 커밋 725f4f7과 동일한 기존 항목.
- 미실행: Codex Security 플러그인 스캔(수동 독립 검수로 대체), PostgreSQL·Windows·Server 2022, 다중 프로세스 부하 측정, 실제 공유폴더 30초 부하. migration·의존성·배포 진입점 변경 없음.

## 2026-10-02 사용자 확인 반영: Scene 폴더 저장·형식 제한 해제·최신 결과 통합 표시 (Claude)
- 사용자가 브랜치를 update.bat으로 적용해 6_corner 등록·실제 폴더 반영을 확인했다. 요청: results 하위 폴더 불필요, 업로드 형식 제한 해제, Run Option 아래 모든 Scene 결과를 함께 표시(버전별 조회 불필요).
- 조치: 유통환경 Scene 자체를 결과 위치로 제안, 실행 파일·스크립트 외 모든 형식 업로드 허용, Case별 Scene 최신 결과 병합(가상 버전 latest:<Case>)을 기본 표시하고 버전은 업데이트 이력으로만 표시, 게시 후 Refresh에서 Case 폴더 전체 수집(capture_cases=True). 등록 테스트 2건은 "게시당 버전 1개" 가정을 멱등성 확인으로 바꿨다.
- 검증: 관련 백엔드 102 passed(신규 포함), tsc 통과, e2e materials 5/5·folder-working-final 3/3·simulation-dashboard 9/10(남은 1건은 기준 커밋부터 실패하는 capture-pin 항목). simulation-dashboard의 첫 화면 대기 시간을 15초로 늘려 재로딩 지연에 따른 간헐 실패를 정리했다.
- 미실행: 독립 검수(이번 변경분), Codex Security 스캔, Windows/Server 2022. 최종확정의 병합 최신 결과 기준 처리는 6단계에서 정리한다.

## 2026-10-02 새 프로젝트·의뢰 폴더 자동 확인 (Claude, 브랜치 claude/case-results-redesign)
- `folder_auto_discovery.py`와 `POST /api/folder-discovery/environments/discover`: root → 포장 폴더(최대 2단계) → 프로젝트 → 의뢰를 얕게 나열하고, 새 의뢰만 기존 scan → preview → registration(capture)으로 등록한다. 환경은 의뢰 이름(사용/유통, SimType1/2)으로 정하고, 판단 불가·역할 미결정은 등록하지 않고 needs_review(관리자만 표시)로 돌려준다. 빈 Working 의뢰도 등록한다. 새 프로젝트는 첫 의뢰 등록 트랜잭션에서 멤버십 없이 만든다.
- 기존 파이프라인 확장(기본 동작 불변): `save_scan(skip_paths)`, 등록 재조사 시 저장된 조사의 `children_skipped` 경계 재사용, `preview(allow_without_cases)`, `register/materialize(creator_membership)`.
- 권한 `project.data.view`(목록 조회와 동일), 프로세스·root별 60초(force 10초) 합치기, 생성 시에만 감사 기록. 프런트 `useFolderDiscovery`(시작 시·60초, 숨김 탭 중지, "새 의뢰 n건 확인" 알림, 생성 시 `refreshOperationalData`).
- 검증: 신규 10 passed, 지정 회귀(environment_folder_flow_api·new_scene_registration·folder_auto_sync·result_registration_api) 포함 61 passed/2 skipped, environment_registration·folder_discovery·openapi_contract 통과. 백엔드 architecture baseline 테스트 실패는 기준 커밋에서도 동일한 기존 항목(dashboard.py·folder_discovery_environment.py sync의 execute). tsc·test:routing 통과, check:architecture는 기존 4건만(App.tsx 1089/1077, +1행).
- 미실행: e2e(지휘 담당), 독립 검수·Codex Security 스캔, PostgreSQL·Windows/Server 2022, 실제 공유폴더. migration·의존성·배포 진입점 변경 없음.

## 2026-10-02 자동 확인 독립 검수 지적 수정 (Claude)
- 비대기 실행 잠금·합치기 확인을 DB 연결 전에 수행(실행 중이면 RUNNING 즉시 반환), 실행 45초·새 의뢰 5개 상한(단계마다 확인). 정확한 SPDM 이름만 등록(NAME_NOT_STANDARD), 같은 WR 번호·환경 중복 차단(WR_ALREADY_LINKED), 재시도 실패 폴더별 backoff(force 무시), 관리자 제외 폴더 ADMIN_EXCLUDED 표시와 제외 집합 캐시, 같은 이름 미연결 프로젝트 PROJECT_NAME_EXISTS, 목록 오류 보고·하위 폴더 하나의 오류 격리, 경합 패배 시 생성 보고 안 함(미리보기 ID 비교). 화면은 선택을 유지한 채 프로젝트·의뢰 목록만 갱신, 401/403에서 확인 중지, 목록 갱신 실패 시 알림 보류.
- 검증: discovery 17 passed, environment_folder_flow_api·new_scene_registration·folder_auto_sync 포함 59 passed/1 skipped; architecture baseline은 기준과 같은 dashboard.py(7) 1건만 실패. tsc·test:routing 통과, check:architecture 기존 4건(App.tsx 1089, 증가 없음). e2e·독립 재검수 미실행.


## 2026-10-02 3단계 A안 화면 검수 반영 (Claude)
- Case 결과 A안(경로 바·탭·표시 옵션·영상 그리드 재사용·소재 탭) 독립 검수 지적 수정: `has_values` Component 우선, Case 변경 시 이력 초기화, 영상 Run Option 안내, 문제 코드 한글화, 토큰 색상, 줄바꿈·말줄임, 키보드 탭, 환경 복원.
- 검증: dashboard 관련 백엔드 29 passed, e2e simulation 13/14(capture-pin 폐기 항목), materials 6, case-video-grid 2, folder-working-final 3, tsc·build·routing 통과, check:architecture 기존 4건. Codex Security·Windows 검증은 해당 없음/미실행.

## 2026-10-02 소재·물성 탭 빈 화면 원인 확인 (Claude)
- 현장(읽기 전용 API 조회): 같은 `75R9J_PV/[WR-0001]_[유통_환경]` 폴더가 두 프로젝트(00:05 기존 등록, 13:31 UTC 관리자 폴더 조사 수동 등록)의 의뢰에 각각 연결되어, 소재 카탈로그의 소유권 검사가 모든 Scene을 조용히 제외했다. Case 결과는 소유권 검사가 없어 표시됐다. 자동 확인(discover)은 원인이 아님(감사 기록 FOLDER_ENVIRONMENT_REGISTERED, 회귀 테스트로 root 이동 후 중복 생성 안 함 확인).
- 조치: 소재 카탈로그가 제외한 Scene을 `conflicts`로 돌려주고 화면에 "다른 의뢰에도 연결" 안내 표시. 데이터 정리(중복 프로젝트·의뢰 해제)는 앱에 기능이 없어 사용자 결정 대기.
- 검증: shared_folder_hierarchy·auto_discovery·materials 58 passed, e2e materials 6 passed, tsc 통과.

## 2026-10-03 5단계 Case 보고서(PPTX·HTML) (Claude, 브랜치 claude/case-results-redesign)
- Case 결과 헤더에 `보고서` 버튼(Final 지정 옆)과 보고서 창 추가. 창을 연 순간의 범위(Case·`latest:<Case>`·하중경우·Run·Option·Component·Basis·엣지/라인)를 `ReportSource` `case_results`로 고정하고 자료를 한 번 읽어 레시피를 만든 뒤 PPTX·HTML로 그린다. 형식은 하나 이상 선택, 둘이면 두 파일. PDF 관련 구현 없음(23:58 결정). 백엔드 변경 없음.
- PPTX는 기존 pptxgenjs 렌더러·회사 레이아웃·편집 창 재사용(`renderReportPptxBlob` 추가, 표 12행 분할, 영상은 파일 이름). HTML은 단일 파일(인라인 CSS, data URI, 스크립트 없음, CSP로 외부 참조 차단, 모든 문자열 이스케이프). `영상 포함`(기본 꺼짐)은 영상당 20MB·전체 200MB 상한, 넘으면 파일 이름으로 대체하고 창에 목록 표시.
- 6단계 재사용용 순수 함수: `frontend/src/features/results/caseReport/caseReport.ts`. 문서: [Case 결과 보고서](../docs/features/case-report.md).
- 구조: 기능 간 import 금지 규칙에 맞추려고 `features/reports/api.ts`를 `shared/api/reportLayouts.ts`로, `ReportLayoutEditor`·`reportLayoutUtils`를 `shared/reports/`로 옮겼다(동작 변경 없음). 미디어는 `simulationDashboardApi.assetBlob`(크기 상한 스트리밍 읽기)로만 읽는다.
- 기존 버그 수정: Run Option이 둘 이상일 때 사용자가 고른 값이 자동 보정(이전 URL을 본 렌더)으로 지워지던 문제. 빈 값을 빈 값으로 바꾸는 보정을 건너뛴다(`SimulationDashboard` `choose`).

## 2026-10-03 6단계 Final 지정에 보고서·최신 결과 기준 (Claude, 브랜치 claude/case-results-redesign)
- 경로(DEPTH_V1 D11·D12): 기준 Scene의 입력·결과·이미지·영상·Scene 문서(PDF/PPT/PPTX/XLSX)를 모두 `Final/CAE/<Case>/<확정 ID>/<Working 미러>`로, `Final/Reports/<Case>/<확정 ID>/`에는 앱이 만든 `<Case>_report.pptx|html`만 둔다. 계획 `schema_version` 2. 1 형식 완료 기록(결과가 Reports 미러)은 상태 조회에서 계속 검증·표시한다. 기존 Final 파일은 옮기거나 덮어쓰지 않는다(D14).
- 기준: `capture_id=latest:<Case>`를 받아 Scene별 최신 수집본(`merge_latest_payload` 규칙)에서 해시 고정 목록을 만들고 Scene별 `source_capture_id`를 서명 계획에 기록. 구체 수집본 ID도 계속 허용. 미리보기 뒤 새 수집본이면 `FINALIZATION_CAPTURE_CHANGED`.
- 보고서: 브라우저가 5단계 빌더로 만든 파일을 형식별 raw `PUT /api/dashboard/finalizations/{id}/reports/{pptx|html}`로 올린 뒤 `confirm`에 `report_formats`(하나 이상, `FINALIZATION_REPORT_REQUIRED`). multipart 대신 별도 업로드 단계를 택해 새 실행 의존성(python-multipart)을 피했다. 서버가 파일 이름을 정하고 PPTX(zip 구조·매크로·경로 이탈·압축 폭탄, 64 MiB)·HTML(UTF-8·doctype, 320 MiB) 검사, SHA-256·크기를 서명된 `reports.json`·완료 기록에 남긴다. 보고서 실패 시 완료 없음, 완료 전 같은 확정의 보고서 교체 허용, 완료 후 불변(`FINALIZATION_ALREADY_COMPLETED`). Caddy 템플릿 `request_body 512MB`는 요청당이라 HTML 상한을 수용(문서화).
- 화면: Final 지정 창에 기준(최신 결과·Scene·결과 버전 수), Final/CAE 개수·접이식 목록, Final/Reports 형식 선택(PPTX·HTML·영상 포함), 진행 단계, 오류와 같은 바이트 재시도, 완료 경로·건너뛴 영상. 헤더의 재시도 버튼은 제거(미완료 건수는 배지 툴팁). 문서: [최종확정](../docs/features/case-finalization.md).
- 검증: 백엔드 신규 `test_case_finalization_reports.py` 15 passed, 관련 모듈 포함 53 passed/1 skipped(Windows 전용 1건 skip), OpenAPI 계약 검사 통과. 프런트 tsc·build·test:routing·test:api 통과, check:architecture 기존 4건만. e2e case-finalization 2·folder-working-final 3·case-report 7 통과, simulation-dashboard 13/14(폐기된 capture-pin 항목).
- 미실행: 독립 Sol/Astra 검수, `security-diff-scan`(업로드·경로 경계 변경이라 대상), PostgreSQL 프로필, 실제 Windows Server 2022·공유폴더·Caddy 경유 대용량 업로드. 사용환경은 보고서가 없어 Final 지정을 완료할 수 없다(결정 필요).

## 2026-10-03 사용환경 보고서·Final Case 전체 보고서·5단계 검수 반영 (Claude, 브랜치 claude/case-results-redesign)
- 사용환경 보고서: 같은 빌더에 사용환경 레시피 추가(`ReportSource` `case_usage`). 화면과 같은 "다섯 평가 종합" 행·원문 키·공통/전방/후방 값·상태·OK/NG(+고른 Reference)와 평가별 이미지·영상. 표 로직을 `features/results/usageEvaluations.ts`로 옮겨 화면과 보고서가 공유. 사용환경에서 `보고서`·Final 지정 활성(소재·물성 탭 제외 유지).
- Final 보고서 범위 = Case 전체: 유통은 카탈로그에서 모든 하중경우›Run Case›Run Option을 모아 구역별로 만들고(`case_final`, `loadCaseFinalSections`), 결과 없는 Option은 `결과 없음`. 화면 선택이 없어도 확정 가능. 헤더 `보고서`는 현재 선택 범위 유지.
- 5단계 검수: M1 보고서 창은 "새 레이아웃으로 저장"만(시스템·현재 레이아웃 새 버전·삭제 없음), 템플릿 필드는 정의에 유지하고 렌더링 때만 화면 레이아웃으로 대체. L1 템플릿 업로드·선택·출력 원본 숨김+안내. L2 만드는 중 `취소`·Esc 중단. L3 이미지 1회 읽기·공유, 이미지 전체 300MB 상한·건너뜀 목록, 상한은 원본 크기·HTML 약 1.33배 안내. L5 엣지 없음은 `선택 없음`.
- 검증: tsc·build·test:routing·test:api 통과, check:architecture 기존 4건(App.tsx 1089 증가 없음). e2e case-report 11, case-finalization 4, folder-working-final 3, case-video-grid 2 통과. simulation-dashboard 12/14: 폐기된 capture-pin 항목 1건 실패, `single Run Option` 1건은 전체 실행에서만 실패하고 단독 재실행 통과(불안정). 백엔드·migration·의존성·배포 변경 없음.
- 미실행: 독립 Sol/Astra 검수, 보안 스캔(보안 경계 변경 없음), Windows Server 2022 검증.

## 2026-10-03 Final 지정 백엔드 보강(독립 검수 지적 반영) (Claude, 브랜치 claude/case-results-redesign)
- 고아 보고서: 이전 시도가 게시한 형식을 재시도 `report_formats`에서 빼면 복사 전에 409 `FINALIZATION_REPORT_FORMATS_MISMATCH`. 확정 Reports 폴더의 남의 항목은 409 `FINALIZATION_REPORTS_UNEXPECTED_FILE`(보존, 삭제 안 함), `complete.json` 직전에 폴더 = 기록 보고서인지 재확인. 완료 후 이력 해시와 같은 보관본만 정리(`reports.json` 유지, 버린 미리보기 보관본은 그대로).
- PPTX 검사: `[Content_Types].xml`·모든 `.rels`를 BOM/선언으로 UTF-8/UTF-16 판별 후 표준 파서(DTD·ENTITY 거부, defusedxml 미의존)로 읽음. 매크로·ActiveX·OLE·control·attachedTemplate·외부(`TargetMode=External`) 관계·`ppt/activeX/`·xlsx 외 `ppt/embeddings/` 거부(`FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT`). pptxgenjs 차트 내장 xlsx는 허용하되 내부 재검사. 실제 pptxgenjs 4.0.1 fixture 추가.
- zip 목록 상한: `zipfile` 생성 전 EOCD·ZIP64 EOCD로 항목 10,000·중앙 목록 4 MiB 초과 거부(검수 재현 60 MB/70만 항목 zip 즉시 거부). 업로드·검사 동시 2건, 초과 429 `FINALIZATION_REPORT_BUSY`.
- 상태 조회: 모든 기록은 서명·범위·존재·크기, SHA-256은 표시 기록(의뢰 최근·선택 Case 최근)만, 실패 시 다음 기록. 표시되지 않는 과거 기록의 같은 크기 변조는 표시될 때까지 드러나지 않음(문서화한 절충).
- 업로드는 본문 전에 서명 계획·범위·미완료를 확인. 쓰기·권한 `OSError`는 503 `FINALIZATION_WRITE_FAILED`(임시 파일 정리).
- 최신 기준: Final의 Scene별 수집본을 `merge_latest_payload`(같은 순서) 결과에서 직접 정함. 최신 수집본이 현재 스키마와 안 맞으면 이전 수집본으로 대체하지 않고 `excluded_scenes`(`NO_CAPTURE`·`CAPTURE_SCHEMA_MISSING`·`CAPTURE_SCHEMA_INCOMPATIBLE`)로 미리보기·창에 표시. Final 보고서 범위도 `latest:<Case>`로 고정(사용환경 포함). 카탈로그 수집본 정렬에 `c.id DESC` 동률 기준 추가.
- 검증: 백엔드 finalization 42·folder flow·security·OpenAPI·latest results·dashboard queries 합계 87 passed/1 skipped, OpenAPI 계약 검사 통과(응답 스키마 변경 없음, 재생성 불필요). 프런트 tsc·build 통과, check:architecture 기존 4건만, e2e case-finalization 4·folder-working-final 3 통과.
- 보안 검수: 이 기록은 검수자 수동 검토와 재현 테스트 반영이며 Codex Security 스캔(`security-diff-scan`)을 대신하지 않고, 스캔은 실행하지 않았다. 독립 Sol/Astra 최종 검수, PostgreSQL 프로필, 실제 Windows Server 2022·공유폴더 검증도 미실행. migration·의존성·배포 변경 없음.

## 2026-10-03 깊이 기반 폴더 역할 스키마(DEPTH_V1)와 관리자 등록 삭제 (`d58a77a`)

- 계약: `docs/contracts/depth-schema.md`(D1–D17), 결정: `docs/decisions.md`.
- 변경: migration 0034(구 프로필 archive, 기본 세트 seed, EVALUATION→SCENE), 0035(DELETED 묘비, created_targets, 앱 역할 DELETE 권한). 자동 탐색은 상위 깊이 BFS·의뢰명 키워드 환경 판정, SimType 규칙 삭제. API: depth-schema GET/PUT/samples/check, 의뢰 재해석, 등록 delete-preview/delete, 구 프로필 CRUD 410. UI: 깊이표 편집기(확인/저장), 역할 읽기 전용+이탈 배지, 재해석 버튼, 등록 이력 다중 선택 삭제.
- 검증: DuckDB 전체 2021 passed/9 failed(기존 실패: rocky8 root 6, architecture ceiling, postgres_portability, load_case_create_slice), Postgres 16 신규 DB 대상 4개 파일 108 passed, migration 0033→0035 기존 데이터 업그레이드 확인. 프론트 tsc·build·test:api 통과, e2e 미실행.
- 독립 검수: Verifier 1회(Blocker 없음, 테스트 측 수정 3건 반영). Codex Security 스캔 미실행. 실제 Server 2022 폐쇄망 배포 검증 미실행.
- 남은 것: DuckDB 삭제는 FK 단계별 커밋(Postgres는 단일 트랜잭션), Final/CAD 용도 미확정.

## 2026-10-03 수동 등록 의뢰 1개 단위, 결과 환경 자동 판정, "선택" 처리

- 원인: 수동 등록에서 Root/프로젝트 수준 조사 시 여러 의뢰가 한 등록에 묶여 첫 의뢰 외 Case가 `CAPTURE_CONTEXT_MISMATCH`. 의뢰 수준 조사는 project_id NULL로 500. 시각은 naive UTC를 현지로 해석해 9시간 차이.
- 변경: 미리보기·등록에서 의뢰 0개(REQUEST_MISSING)·2개 이상(MULTIPLE_REQUESTS) 차단(등록 시 DB 쓰기 0), 의뢰 수준 조사 시 상위 스키마로 프로젝트 도출, API 시각 UTC 오프셋. `result-environments` API로 Case 결과 환경 자동 판정(라벨, Case 없음 안내+폴더 동기화 유지, 혼재 시 토글), 결과 등록 환경 자동 지정. 프로젝트 "선택"→내 작업, 의뢰 "선택"→선택 해제.
- 계약: `docs/contracts/depth-schema.md` §14, `docs/contracts/case-results-environment.md`.
- 검증: DuckDB 전체 2032 passed/9 failed(기존 9건), Postgres 16 신규 파일+삭제 24/24, 프론트 tsc·build·test:api, e2e simulation-dashboard 15/16(실패 1건은 기준 커밋에서도 실패), case-finalization·report·video-grid 17/17.
- 독립 검수: Verifier 1회(Blocker 1건: Case 없는 의뢰 동기화 중단 → 수정). E5·E6·E7 e2e 없음. Codex Security 스캔 미실행.

## 2026-10-04 사이드바 이동 시 구 결과 등록 화면이 뜨던 문제

- 원인: 폴더 등록 의뢰는 overview가 없어 `App.tsx` 준비 단계 대체 화면이 변수 카탈로그 등 무관한 페이지까지 가로챔. 사이드바 이동이 이전 쿼리를 그대로 가져가 의뢰가 다시 선택되며 간헐 재현.
- 변경: 대체 화면은 의뢰 작업 페이지에만, 변수 카탈로그 빈 상태, 페이지 전환 시 의뢰 화면 전용 쿼리 제거. 계약 `docs/contracts/workspace-navigation.md`.
- 검증: tsc·build·test:api·test:routing·architecture 자체 테스트 통과, check:architecture 기존 4건 외 추가 없음, 신규 e2e 5/5. simulation-dashboard 1건·workspace-routing 2건 실패는 기준 커밋에서도 실패. 독립 Verifier 미투입(저위험 UI 범위).

## 2026-10-04 폴더 등록 의뢰 진척 단계 (폴더 상태 자동 계산)

- 계약: `docs/contracts/folder-request-progress.md`. 등록→모델링(Scene 바로 아래 `.rad`/`.fem`)→결과→Final(대표 Case 1개)→보고서, 의뢰 단위, 수동 입력 없음.
- 변경: `GET /api/projects/{p}/requests/{r}/folder-progress`(30초 메모, 실시간 스캔 없음, 파일 본문 읽기·SPDM 쓰기 없음), `case_finalization.latest_completed`(읽기 전용, status 동작 불변), 의뢰 개요 단계 표시줄·현재 할 일·Case 결과 버튼.
- 검증: DuckDB 전체 2040 passed/9 failed(기존 9건), Postgres finalization·progress 통과, 프론트 tsc·build·test:api, e2e folder-request-progress 3/3. request-centric-workspace e2e 4건 실패는 기준 커밋에서도 동일.
- 독립 검수: Verifier 1회(Blocker: Scene 경로 위해 전체 트리 재스캔 → 저장 스냅샷만 사용으로 수정, Final/보고서 판정 분리). 권한은 catalog와 동일(전사 PROJECT_DATA_VIEW). Codex Security 미실행.

## 2026-10-06 Final/Report 폴더명 정정(D21)과 프로젝트 CAD/Report 무시(D22)

- 계약: `docs/contracts/depth-schema.md` §15. 역할 키 `FINAL_REPORTS`와 기록의 출력 키 `Reports`는 내부 식별자로 유지, 실제 폴더만 `Final/Report`.
- 변경: `case_finalization`(쓰기·읽기 경로 `Final/Report`, `Final/Reports`로 서명된 기존 기록은 CAE 출력으로만 유효·보고서 없음으로 표시, v1 결과 미러도 미인식), StorageProvider FINAL 구역 `CAE|Report|.finalizations`(`Reports` 쓰기 거부), 깊이 스키마 `FINAL_BLOCK` 상수 `Report`(해석은 서버 상수만 사용, 0034 seed의 저장값 `Reports`는 읽지 않으므로 migration 없음), Final 아래 `Reports`는 `UNEXPECTED_FINAL_CHILD` 경고. 의뢰 레벨 `CAD`/`Report`(대소문자 무시)는 `resolve_path`·자동 탐색 의뢰 후보·샘플/검사·등록 stale 비교에서 숨김 이름처럼 제외. 프론트 문구 `Final/Report`.
- 검증: DuckDB 전체 9 failed(기존 9건; 신규 실패 2건 `environment_folder_flow_api` Final 경로·0034 seed 비교는 테스트 갱신 후 통과), 관련 백엔드(finalization·progress·auto-discovery·depth_schema·storage_provider·single_request_registration) 통과, Postgres 16 신규 DB(0034 seed 포함)에서 finalization·auto-discovery·progress 72 passed. 프론트 tsc·build·test:api 통과, e2e case-finalization 통과, environment-folder-flow는 기존 실패 1건(desktop and mobile 등록 흐름) 외 통과.
- 독립 검수: Verifier 1회(Blocker 없음, should-fix 2건 수정 — ① 업그레이드 전 시도가 `Final/Reports/<Case>/<op>`에 보고서를 남긴 작업의 재시도는 `FINALIZATION_LEGACY_REPORTS_PRESENT`(409)로 거부하고 아무것도 쓰지 않음, ② 기존 경로를 문자열 치환 대신 구성요소로 생성(상위 폴더 이름이 `Report`여도 안전)). 수정 후 finalization·progress DuckDB/Postgres 신규 DB 각 52 passed. Codex Security 미실행.
- 남은 것: 기존 `Final/Reports`는 사용자가 이름을 바꿔야 보고서로 인식.

## 2026-10-06 Final 지정·Case 보고서 영상 목록 422 (이슈 #43 코멘트)

- 원인: `caseReport.ts`가 영상 목록을 `page_size=100`으로 요청, 서버 상한 `VIDEO_PAGE_SIZE_MAX=20` → 422.
- 수정: 20으로 요청하고 최대 쪽수를 250으로 늘려 상한 5000개 유지. e2e 모의 응답을 20으로 맞춤.
- 검증: tsc, e2e case-report·case-finalization 15/15.

## 2026-10-06 W2 Final 복사 개편 (조각 복사·상한 제거·임시 폴더 공개·작업 스레드·진행률·이어하기)

- 계약: `docs/features/case-finalization.md`(계획 3 형식). 근거 `docs/plans/improvement-roadmap.md` §4 W2, `docs/plans/case-results-workflow-redesign.md` §8.3.
- 변경: 저장소 계층에 추가 메서드만(`LocalFsProvider.copy_stream` 8 MiB 조각 복사+SHA-256·서버 측 복사 자리 `server_side_copy`, `hash_stable`, `rename_no_replace`, `append_bytes`, `try_lock`, `list_detailed`, `free_bytes`; 기존 `request_lock` 동작 불변). Final/CAE는 확정 Scene 아래 모든 파일(`~$*`·`*.tmp`·숨김·시스템 제외), CAE 개수·크기 상한 제거, 시작 전 남은 공간(필요량+max(5 %,1 GiB)) 확인 507. `.finalizations/<ID>/staging/{CAE,Report}`에 쓰고 재해시 후 폴더째 이름 바꾸기(대상 비덮어쓰기, 잠금 재시도), `complete.json`은 두 폴더 공개 뒤. 확정은 서명된 `job.json` 등록 후 즉시 응답, 프로세스 내 작업 스레드(동시 2, 같은 의뢰 1), 서명된 `progress.json`·`copied.jsonl`, 시작 시·상태 조회 시 이어하기. `GET /api/dashboard/finalizations/{id}/job` 추가, 상태 조회 `active_operations`·`verification`. Final 창 진행률·창 닫아도 계속·헤더 `Final 복사 중 n%`·실패 사유와 재시도. 새 DB 테이블·migration·의존성 없음.
- 의도적 차이: 비수집 파일은 미리보기에서 크기·수정 시각으로 고정하고 복사 중 해시를 계산(미리보기는 목록+lstat만). 의뢰 잠금은 복사 내내가 아니라 `complete.json` 쓰기에만 쓰고, 같은 의뢰 직렬화는 작업 대기열·작업 잠금으로 한다. 상태 조회의 해시 예산을 넘는 큰 기록은 존재·크기만 확인하고 `verification: SIZE`.
- 검증(격리 임시 root·합성 데이터): 백엔드 finalization reports·신규 `test_case_finalization_copy_jobs.py`(18)·environment flow·storage provider·boundary·folder progress 126 passed/1 skipped(Windows 전용 고정 테스트), 앞선 실행에서 depth_schema·registration delete 포함 통과. 전체 백엔드 스위트는 돌리지 않음. OpenAPI 재생성·`check_openapi_contract.py` OK. 프론트 tsc·build·test:routing·test:api 통과, check:architecture 기존 4건만. e2e case-finalization 5/5, folder-working-final Final 2건 통과(나머지 2건 깊이 스키마·Working 계층은 기준 커밋 HEAD에서도 실패 — 별도 worktree로 확인).
- 미수행: 실제 Windows Server 2022·SMB·HPC(이름 바꾸기 원자성, 백신 잠금, 디렉터리 고정 핸들과 폴더 이름 바꾸기 공존, SMB 디렉터리 캐시의 수정 시각) 검증(W9). 독립 검수·보안 검수 대기(Codex Security 미가용, 수동 대체 검수도 아직 안 함).

## 2026-10-06 W1 사이드바 숨김 (Claude)
- `AppSidebar.tsx`의 `SIDEBAR_HIDDEN_MENUS`로 새 의뢰·예제 및 참고·변수 카탈로그·프로젝트 결과 구성·작업 유형 관리를 사이드바에서 숨김. 화면·경로·권한 불변, 직접 URL 동작.
- e2e: access-policy 역할별 노출 검사 3곳을 비노출로 변경, `openWorkspaceRoute` 헬퍼에 숨김 경로의 앱 내 URL 이동 추가. materials-dashboard spec에 result-environments mock 추가(다른 세션 변경 이후 6건 중 5건 실패하던 것 복구, 6/6).
- 검증: access-policy·project-result-profiles·workbench-demo 11/12(남은 1건 DOE 접수는 기준 커밋에서도 실패), materials 6/6, simulation-dashboard·case-finalization·case-report 33/34(폐기된 capture-pin 1건), 백엔드 169 passed/1 skipped, tsc 통과, check:architecture 기존 4건.
