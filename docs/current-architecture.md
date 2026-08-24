# 현재 구현 아키텍처

- 기준일: 2026-08-24
- 상태: 현재 코드 기준
- 대상: `backend/app`, `frontend/src`, DB migration, API 계약, 테스트 경계

이 문서는 목표 구조가 아니라 저장소에 실제로 존재하는 구조를 설명한다. 리팩터링의 배경과 장기 목표는 [`architecture-refactoring-roadmap-v2.md`](architecture-refactoring-roadmap-v2.md), 실행 우선순위는 [`program-consolidation-and-development-plan.md`](program-consolidation-and-development-plan.md), 저장 계약은 [`storage-folder-and-file-contract.md`](storage-folder-and-file-contract.md), 결정 근거는 [`adr/`](adr/)를 참고한다.

## 1. 한눈에 보는 구조

```text
Browser
  │
  ├─ React Router catch-all
  │    └─ App coordinator
  │         ├─ app/: shell, routing, bootstrap, workspace controller
  │         ├─ features/: 화면·기능 상태·기능 adapter
  │         └─ shared/api/: 인증 fetch + OpenAPI client + 오류 처리
  │
  └─ /api
       └─ FastAPI app
            ├─ main.py의 legacy endpoint
            ├─ routers/의 분리 router
            ├─ adapters/http/routers/의 vertical-slice adapter
            ├─ services/ 또는 application/ use case
            ├─ domains/ model·policy·port
            └─ repositories/ 또는 adapters/persistence/
                  ├─ DuckDB: 로컬 단일 프로세스
                  └─ PostgreSQL: 운영 source of truth
```

현재는 legacy 구조와 목표 구조가 공존하는 점진적 전환 상태다. `main.py → application → domain → persistence adapter`가 모든 기능에 일괄 적용된 것처럼 설명하면 안 된다.

## 2. 저장소 최상위 책임

| 경로 | 실제 책임 |
|---|---|
| `backend/app/` | FastAPI, 인증·인가, use case, SQL adapter, 결과 파서와 저장 |
| `backend/migrations/` | PostgreSQL canonical schema를 소유하는 Alembic revision |
| `backend/scripts/` | migration, seed, 계약·권한·DB preflight, 배포 보조 |
| `backend/tests/` | unit, DuckDB/PostgreSQL integration, API contract 검증 |
| `frontend/src/` | React 앱, workspace routing, 기능 화면, API adapter |
| `frontend/scripts/` | OpenAPI 생성, architecture/API/routing/preferences self-test, E2E 실행기 |
| `frontend/e2e/` | Playwright 사용자 흐름 |
| `scripts/wsl/` | 고정 런타임 설치, 환경 진단, Chromium/PostgreSQL 보조 |
| `scripts/postgres/` | PostgreSQL 설치·백업·복구의 공용 shell/PowerShell 구현 |
| `deploy/rocky8/` | 정적 프런트 + FastAPI + PostgreSQL 운영 번들·설치기 |
| `docs/` | 현재 기준 문서, ADR, 운영 문서, 기능별 설계·완료 기록 |
| `example/`, `examples/` | 등록용 예제와 형식·검토 자료 |

## 3. 백엔드

### 3.1 애플리케이션 조립

`backend/app/main.py`가 FastAPI 인스턴스, lifespan, CORS, 보안 middleware, 정적 asset mount와 router 등록을 소유한다. 동시에 결과 조회·import, workflow, dashboard, report template 등 많은 legacy endpoint와 SQL을 아직 포함한다.

등록된 router는 두 계열이다.

- `backend/app/routers/`: 인증, 접근 제어, workbench, 모델링 카탈로그, 마스터 결과 Refresh
- `backend/app/adapters/http/routers/`: `projects`, `requests`, `reports`의 대표 vertical slice HTTP adapter

새 기능은 가능한 한 얇은 router에서 입력/권한/응답 변환만 처리하고, orchestration과 SQL을 아래 계층으로 넘긴다.

### 3.2 비즈니스 로직과 데이터 접근

| 경로 | 사용 방식 |
|---|---|
| `backend/app/services/` | import, media, monitoring, verdict, OIDC, 배치 실행, 요청 결과 구성 같은 절차형 orchestration |
| `backend/app/application/` | `projects`, `products`, `requests`, `results`, `reports`의 명시적 command/query use case |
| `backend/app/domains/` | 같은 대표 slice의 프레임워크 독립 model, policy, repository port |
| `backend/app/repositories/` | 아직 이전되지 않은 기능의 SQL repository |
| `backend/app/adapters/persistence/` | 대표 vertical slice의 SQL repository provider/adapter |
| `backend/app/schemas/` | HTTP 요청·응답 Pydantic 계약 |
| `backend/app/parsers/` | manifest, scalar, 시계열, Open Cell, Chassis Rear 결과 해석 |

`materials`, `parts`는 로드맵에만 있으며 canonical table/API가 없으므로 현재 domain으로 문서화하지 않는다.

### 3.3 데이터베이스 경계

`backend/app/database_connection.py`가 두 DB를 공통 `connect()` 계약 뒤에 둔다.

- DuckDB는 로컬 개발 adapter다. FastAPI sync handler의 다중 thread가 같은 파일 handle을 충돌시키지 않도록 프로세스 단위로 직렬화한다.
- PostgreSQL은 동시 사용자 운영 source of truth다. 일반 요청용 pool과 미디어용 pool을 분리하고 환경변수로 budget을 제한한다.
- SQL 호환 adapter가 기존 `?` placeholder와 일부 DuckDB SQL을 PostgreSQL 문법으로 변환한다. 따라서 양쪽 DB 테스트가 없는 SQL 변경은 완료로 보지 않는다.

`backend/app/database.py`는 현재 다음 두 성격을 함께 가진 큰 compatibility 모듈이다.

1. DuckDB의 DDL 보정과 로컬 fixture 초기화
2. 공용 seed, JSON/row helper와 아직 이전되지 않은 데이터 초기화

PostgreSQL에서는 `initialize_database()`가 필수 table을 읽기 전용으로 확인할 뿐 schema를 생성하거나 변경하지 않는다. PostgreSQL schema 변경의 유일한 원본은 `backend/migrations/versions/`의 Alembic revision이다.

### 3.4 핵심 데이터 관계

```text
Project
├─ ProductInformation
├─ AnalysisRequest
│  ├─ RequestStep / RequestWorkPlan / RequestWorkItem
│  ├─ RequestResultLayoutSnapshot
│  └─ LoadCase
│     ├─ TemplateExecution / WorkflowRun / TaskRun / BatchAttempt
│     └─ AnalysisRun
│        ├─ ScalarResult / TimeSeriesResult / Curve / Location
│        ├─ MediaAsset / Blob
│        ├─ Validation / Trust metadata
│        └─ Review annotation / Note
├─ Dashboard / DashboardVersion / WorkspaceLayout
├─ ReportLayout / ReportLayoutVersion / ReportTemplate
└─ Membership / Invitation / AuditEvent
```

업무 유형과 분석 템플릿은 불변 버전으로 관리한다. 의뢰 생성 시 작업계획과 결과 레이아웃을 snapshot으로 고정해 이후 업무 유형 변경이 기존 의뢰 화면을 바꾸지 않도록 한다.

## 4. 프런트엔드

### 4.1 진입과 shell

`frontend/src/main.tsx`는 React Router의 catch-all route 하나를 만들고 `App.tsx`를 렌더링한다. canonical workspace URL은 `features/navigation/workspaceRouteRegistry.ts`가 단일 소유하며, URL 해석·권한 fallback·편집 이탈 방지는 `app/routing/useWorkspaceNavigation.ts`가 담당한다.

`App.tsx`는 현재 다음 공용 상태를 조정한다.

- 인증 session과 메뉴 정책
- 프로젝트·의뢰·하중 경우 선택
- 초기 workspace bootstrap
- dashboard와 workflow 편집 session
- 보고서와 자연어 개선 dialog 진입
- route renderer에 필요한 feature callback 조립

화면 자체는 `app/workspace/WorkspaceRouteRenderer.tsx`와 `features/`로 이동했지만 `App.tsx`는 아직 최종 wiring-only 수준은 아니다.

### 4.2 폴더 책임

| 경로 | 실제 책임 |
|---|---|
| `frontend/src/app/shell/` | 인증 후 공통 shell, sidebar, topbar |
| `frontend/src/app/routing/` | URL navigation과 lazy route module |
| `frontend/src/app/workspace/` | portfolio/request workspace controller와 route-to-feature adapter |
| `frontend/src/app/preferences/` | versioned UI preference |
| `frontend/src/features/` | access, auth, bootstrap, data, requests, results, reports, workbench 등 기능 화면 |
| `frontend/src/shared/api/` | 공통 fetch/auth/error, 생성 OpenAPI client, named adapter |
| `frontend/src/api.ts` | 이전 중인 공용 API facade와 runtime response validation |
| `frontend/src/types.ts` | 이전 중인 공용 UI model 계약 |
| `frontend/src/reportExport.ts` | 클라이언트 PPTX 변환·렌더링의 legacy 집중 모듈 |
| `frontend/src/styles.css`, `theme.css` | 전역/legacy feature 스타일과 theme token |

기능 폴더 간 새 상대 import는 architecture gate가 차단한다. 공용 계약은 `app`, `shared` 또는 명시적 공개 경계로 올린다.

### 4.3 초기 데이터 흐름

```text
auth status + current user
  → health, projects, workflows, menu policy 병렬 조회
  → 프로젝트별 request와 load case 탐색
  → 선택한 context의 threshold, overview, page, layout 병렬 조회
  → preferred analysis page 결정
  → dashboard definition 조회
  → workspace route 렌더링
```

`features/bootstrap/useWorkspaceBootstrap.ts`는 React StrictMode effect 재실행과 사용자 전환 중 오래된 응답이 현재 상태를 덮지 않도록 generation을 관리한다. 결과 화면 전환도 request/context generation guard를 사용한다.

## 5. 주요 기능 흐름

### 5.1 업무 유형에서 상세 분석까지

```text
RequestResultWidgetConfiguration
  → result_definition API payload
  → request_result_definition compiler
  → immutable request type + internal template + result profile 저장
  → 의뢰 생성
  → request result layout snapshot
  → PendingAnalysisWorkspace 또는 GenericResultLayoutWorkspace
  → 편집 시작 시 custom dashboard materialize
```

snapshot은 기대 결과 화면이고 Analysis Run은 실제 데이터다. 데이터가 없을 때 전체 오류로 처리하지 않고 위젯 단위 `WAITING/PARTIAL/READY` 상태를 표시한다.

### 5.2 마스터 결과 Refresh

```text
SIMDASH_IMPORT_ROOT
  → manifest.json 탐색
  → root/symlink/context/파일 검증
  → manifest + mapping 파일 bundle fingerprint 기반 중복 판정
  → normalized parser payload
  → ResultIngestionCommand
  → ResultIngestionUnitOfWork/provider의 single-connection transaction
  → AnalysisRun + 유형별 결과 + media blob 원자적 저장
  → request/work status 동기화
  → manifest별 IMPORTED/SKIPPED/FAILED 응답과 Refresh 집계 감사 이벤트
```

클라이언트는 서버 경로를 넘기지 않는다. 잘못된 manifest 하나는 다른 정상 bundle의 처리를 중단시키지 않는다.

`backend/app/parsers/manifest_format.py`가 공통 format detector/loader 경계다.
canonical `mappings`는 `scan_folder`와 Master Refresh로, legacy
`result_files`는 `ResultImportService`와 기존 `ManifestParser` alias로 명시적으로
분기한다. mixed/unknown manifest, 잘못된 importer, root escape와 symlink는 각
경계에서 fail-closed한다. `backend/app/application/results/commands.py`가
정규화된 canonical command를 orchestration하고,
`backend/app/adapters/persistence/result_ingestion.py`가 현재 DuckDB/PostgreSQL
공통 SQL UoW를 제공한다. Master Refresh와 `/folder-import/example`은 이 UoW를
공유한다.

일반 수동 `SUMMARY_RESULT` JSON/CSV upload도 같은 normalized payload와 UoW를
사용한다. target-qualified `source_name`과 content checksum으로 재시도를
`SKIPPED`하고, write transaction 안에서 `result.import` 권한을 재확인하며
`RESULT_IMPORTED` audit event를 원자적으로 기록한다. `Radioss` mesh CSV는
`result_locations` 저장이 canonical UoW에 아직 포함되지 않아 기존 direct-SQL
경로에 남아 있다.

구형 `result_files` 기반 `ResultImportService` persistence는 별도 legacy 경로다.
legacy repository가 현재 schema에 없는 `result_import_jobs`와 `analysis_runs`
확장 컬럼을 참조하므로 parser/manifest 호환을 위한 비운영 compatibility 경로다.
legacy run identity/replace와 verdict threshold 경계 통일은 잔여 과제다.

PostgreSQL canonical ingestion은 load case별 namespaced 64-bit transaction
advisory lock으로 `run_no` 할당을 직렬화하고, non-null exact identity
`(source_type, source_name, source_checksum)`를 migration `0016`의
`canonical_result_ingestion_sources` primary key로 예약한다. 실패한 transaction은
예약도 rollback한다. 현재 검증은 SQL 호출 순서·migration·PK를 확인하는 unit/contract
범위이며, live PostgreSQL concurrent ingestion test는 아직 없다.

실제 ID 계층·JSON·CSV·SVG·glTF 예제는 `examples/master-results/`에 있으며 backend 통합 테스트가 이를 직접 import한다.

### 5.3 대시보드와 보고서

- dashboard definition과 version은 서버에 저장한다.
- 프로젝트/의뢰/하중 경우 context와 권한을 서버가 다시 검증한다.
- 규칙 기반 자연어 요청은 구조화된 변경안을 미리 보여준 뒤 적용한다. 임의 SQL·shell·코드를 실행하지 않는다.
- 보고서 layout/version/template은 서버가 관리하고, 현재 PPTX 조립은 프런트의 `reportExport.ts`가 수행한다.

## 6. API 계약

```text
FastAPI app.openapi()
  → frontend/openapi.json
  → openapi-typescript
  → frontend/src/shared/api/generated/openapi.ts
  → shared/api/client.ts
  → named response adapter
  → UI model
```

`frontend/openapi.json`은 서버와 프런트 사이의 검토 가능한 snapshot이다. 생성 TypeScript 파일은 직접 수정하지 않는다. JSON success response 중 일부는 아직 익명 schema라서, `frontend/src/api.ts`의 runtime adapter가 필수 필드를 추가 검증한다.

## 7. 인증과 권한

- `SecurityMiddleware`가 principal과 인증 상태를 요청에 연결한다.
- `AUTH_MODE`는 `disabled`, `password`, `oidc` 중 하나다.
- UI 메뉴 숨김은 사용성 기능일 뿐 보안 경계가 아니다. 서버 router/use case가 permission과 project scope를 다시 확인한다.
- 프로젝트 권한, 시스템 권한, 회사 범위 권한과 메뉴 정책은 별도 계약이다.
- 변경, 인증, 권한 거절, Refresh 같은 중요 동작은 감사 이벤트를 남긴다.

## 8. 검증 경계

| 계층 | 위치/명령 | 확인 내용 |
|---|---|---|
| 백엔드 unit/통합/contract | `backend/tests`, `pytest` marker | policy, DB adapter, API, migration, OpenAPI |
| 백엔드 architecture | `backend/scripts/check_architecture.py` | domain 의존성, router SQL 호출 증가 방지 |
| 프런트 architecture | `pnpm run check:architecture` | 파일 ceiling, raw API, cross-feature import |
| 프런트 self-test | `test:architecture`, `test:preferences`, `test:routing`, `test:api` | checker, 저장 설정, URL, API adapter |
| TypeScript/build | `pnpm run build` | 타입과 production bundle |
| 브라우저 | `frontend/e2e`, `pnpm run test:e2e` | 실제 권한·편집·결과·routing 흐름 |
| PostgreSQL opt-in | `postgres_integration` marker와 preflight script | Alembic head, app-role 권한, 양 DB 호환 |

결과 수집 focused 검증은 현재 collection 기준 71개 test case다. canonical/legacy
manifest 경계, fingerprint idempotency와 mapping 변경 감지, 공통 UoW atomic
rollback, manual `SUMMARY_RESULT` JSON/CSV의 target-qualified source·retry
`SKIPPED`·audit/auth 재확인, scalar/curve/media/blob/catalog 저장, 모든 media
extension fixture의 정상/MIME mismatch/corrupt signature, PostgreSQL reservation
SQL/migration contract와 endpoint wiring을 포함한다. 이 collection에는 live
PostgreSQL concurrent ingestion test가 포함되지 않는다.

```bash
cd backend
../.venv-wsl/bin/python -m pytest --collect-only -q \
  tests/test_master_result_refresh.py \
  tests/test_master_result_folder_example.py \
  tests/test_result_ingestion_atomicity.py \
  tests/test_result_import_contract.py \
  tests/test_manifest_format.py \
  tests/test_manual_result_ingestion.py \
  tests/test_media_policy_fixtures.py \
  tests/test_result_ingestion_idempotency.py \
  tests/test_api.py::test_typed_folder_example_registers_scalars_curves_media_and_catalog
# 71 collected; live PostgreSQL concurrent test는 별도 미제공
```

Architecture ceiling은 목표 수치가 아니라 부채가 늘지 않게 하는 상한이다. 파일을 나누었다는 이유만으로 경계가 개선되었다고 보지 않는다.

## 9. 현재 구조에서 지켜야 할 규칙

1. 새 endpoint를 `main.py`에 추가하기 전에 독립 router와 service/use case 경계를 선택한다.
2. 새 router에서 SQL을 직접 호출하지 않는다. repository 또는 persistence adapter를 사용한다.
3. domain은 FastAPI, SQLAlchemy, DuckDB, repository 구현을 import하지 않는다.
4. 새 화면과 상태를 `App.tsx`에 직접 누적하지 않고 feature/controller가 소유하게 한다.
5. API URL은 생성 client를 통하고 raw `/api/` 문자열을 기능 코드에 추가하지 않는다.
6. PostgreSQL schema 변경은 Alembic revision으로만 수행한다.
7. DuckDB와 PostgreSQL의 SQL 차이를 모두 검증한다.
8. 의뢰 snapshot, dashboard version, 업무 유형 version의 불변성을 깨는 in-place update를 만들지 않는다.
9. 결과 파일 경로와 미디어는 root·형식·크기·checksum 정책을 거친다.
10. 구조나 명령이 바뀌면 이 문서와 [`development-workflow.md`](development-workflow.md)를 함께 갱신한다.

## 10. 알려진 구조 부채

- `backend/app/main.py`와 `database.py`가 여전히 크고 여러 책임을 가진다.
- `routers/access_control.py`, `routers/workbench.py`, `repositories/workbench.py`에 SQL과 orchestration이 집중되어 있다.
- `frontend/src/App.tsx`, `styles.css`, `reportExport.ts`, `api.ts`가 큰 전환 모듈이다.
- 생성 OpenAPI 계약의 여러 성공 응답이 익명 JSON schema라 UI adapter의 수동 검증이 남아 있다.
- 현재 URL은 workspace 진입점만 표현하고 project/request/load-case 선택을 deep-link로 보존하지 않는다.
- demo와 reference seed가 같은 fixture alias다.
- 외부 NAS/NFS/SMB `SIMDASH_IMPORT_ROOT`의 부팅 순서, mount context, 용량과
  재처리 운영 절차는 각 사내 인프라 환경에서 승인해야 한다.
- Master Refresh는 신뢰된 read-only root를 전제로 하지만 아직 bundle bytes를
  immutable snapshot으로 고정하지 않는다. producer atomic publish와 importer
  rehash/snapshot으로 fingerprint와 실제 parse bytes의 TOCTOU를 닫아야 한다.

우선순위와 완료 기준은 [`program-consolidation-and-development-plan.md`](program-consolidation-and-development-plan.md)에 정리한다.
