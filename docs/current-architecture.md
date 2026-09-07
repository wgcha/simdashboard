# 현재 구현 아키텍처

- 기준일: 2026-09-01
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

`backend/app/main.py`가 FastAPI 인스턴스, lifespan, CORS, 보안 middleware, 정적 asset mount와 router 등록을 소유한다. 동시에 workflow-step mutation, dashboard 등 많은 legacy endpoint와 SQL을 아직 포함한다. Report layout, PPTX report template, variable catalog, project workspace layout, import-schemas, result-review, analysis-insights, load-case-overview, quality-thresholds, workflow-queries, request-load-cases, feature-examples와 portfolio endpoint는 독립 router로 이동했다.

등록된 router는 두 계열이다.

- `backend/app/routers/`: 인증, 접근 제어, workbench, 모델링 카탈로그, 마스터 결과 Refresh, 수동 결과 import·예제 폴더 import를 소유하는 `result_ingestion`
- `backend/app/adapters/http/routers/`: `projects`, `requests`, `request_load_cases`, `reports`, `report_templates`, `variable_catalog`, `workspace_layouts`, `import_schemas`, `result_review`, `analysis_insights`, `load_case_overview`, `quality_thresholds`, `workflow_queries`, `feature_examples`, `portfolio`의 대표 vertical slice HTTP adapter

새 기능은 가능한 한 얇은 router에서 입력/권한/응답 변환만 처리하고, orchestration과 SQL을 아래 계층으로 넘긴다.

`report_templates`의 목록·업로드·render·비활성화 4개 API는 HTTP adapter,
application command/query, reports domain port, SQL metadata adapter, managed filesystem
adapter, ZIP/XML document adapter로 분리됐다. 기본 runtime root는
`backend/assets/report-templates`이며 DB에는 `report-templates/report-template-<12 hex>.pptx`
상대 경로만 저장한다. 업로드는 DB 실패 시 파일을 보상하고, render는 관리형 root 밖
경로와 child symlink를 읽지 않으며, delete는 파일 quarantine 뒤 DB를 갱신하고 실패 시
복원한다. Rocky의 runtime root·backup/restore 실검증은 사내 release gate다.

`variable_catalog`의 목록·생성/재활성화·수정·soft delete 4개 API도 HTTP adapter,
application command/query, domain policy/port, SQL persistence adapter로 분리됐다.
Mutation은 같은 DB connection에서 load-case resource 권한을 먼저 확인하고, 목록의
기존 공개 조회 계약을 유지한다. `repositories/variable_catalog.py`는 result ingestion
legacy caller를 위한 compatibility facade만 남고 실제 SQL·정규화 정책은 adapter/domain이
소유한다.

`workspace_layouts`는 canonical project route 3개와 deprecated alias 2개, 총 5개 API를
HTTP adapter, application command/query, domain policy/port, SQL persistence adapter로
분리했다. Read는 같은 open connection에서 project 존재 확인 뒤 `PROJECT_DATA_VIEW`를,
write는 `PROJECT_LAYOUT_EDIT`를 확인한다. Write는 `BEGIN → project → auth → live update →
version append → audit → COMMIT` 또는 rollback으로 수행한다. principal actor와 기존
validation/404/422/alias/operationId를 유지하며, live row가 없을 때 history 조회는 기존처럼
빈 목록을 반환한다.

`import_schemas`는 GET/POST/PUT/DELETE 4 route를 HTTP adapter, application command/query,
domain policy/port, SQL persistence adapter로 분리했다. 기존 anonymous OpenAPI response와
path·route order·operationId를 유지하며 GET은 기존처럼 explicit permission을 확인하지 않는다.
mappings validation은 provider open 전에 수행한다. Create/update는 같은 connection에서
`SYSTEM_CATALOG_MANAGE` → live row → version → audit transaction을 수행하고 실패 시 rollback한다.
principal actor와 embedded `schema_id`·version semantics를 유지한다. DELETE는 legacy처럼 별도
permission connection과 non-transactional usage check를 사용하고 명시적 domain delete audit을
추가하지 않는다.

`result_review`는 `GET/POST /api/analysis-runs/{run_id}/review-items`와
`PATCH /api/review-items/{annotation_id}` 3 route를 HTTP/application/domain policy·port/SQL
adapter로 분리했다. `trust`와 review는 framework-neutral
`adapters/persistence/result_keys.py`를 공유해 scalar/time-series/curve/location과 media
metadata variable key를 같은 SQL/JSON 규칙으로 읽는다. GET은 기존처럼 explicit
`PROJECT_DATA_VIEW`를 확인하지 않고 run 존재 확인 뒤 bookmark inner join을 `updated_at DESC`로
조회한다. Create는 같은 connection에서 run 존재 확인 전에 `RESULT_REVIEW` resource 권한을, update는
현재 annotation 조회 전에 같은 권한을 확인한 뒤 bookmark·annotation·audit을 하나의 transaction으로 기록하며, authorization/audit HTTP callback이
없으면 fail-closed한다. Pydantic/OpenAPI/route order와 principal actor 계약도 유지했다.

`analysis_insights`는 `GET /api/load-cases/{load_case_id}/run-comparison`과
`GET /api/analysis-runs/{run_id}/trust`를 HTTP/application/domain errors·port·policies/SQL
adapter로 분리했다. 두 read route는 기존대로 explicit permission, audit, transaction을 추가하지
않는다. Comparison의 run 검증, scalar classification, series fallback/merge와 trust의 run·source·catalog·unit·validation check 및 overall status 계산을 보존했다. Trust는 같은 open connection에서 neutral
`adapters/persistence/result_keys.py`를 계속 사용한다.

`load_case_overview`는 `GET /api/load-cases/{load_case_id}/overview`를 HTTP/application/domain
policy+errors+port/SQL adapter로 분리했다. Application은 기존 product query를 복제하지 않고
authorization → product provider → overview provider의 세 connection(A/B/C) 순서를 소유하며, HTTP는
asset/download URL만 응답에 매핑해 domain policy를 transport-neutral로 둔다. 9개 SQL, selected/latest/no-run
검증, 404, JSON/template, threshold/verdict 투영을 그대로 유지했다.

`quality_thresholds`는 `GET /api/projects/{project_id}/quality-thresholds`, canonical
`PUT /api/projects/{project_id}/quality-thresholds/{criterion_key}`, deprecated alias
`PUT /api/quality-thresholds/{criterion_key}`를 `HTTP → application → domain → persistence`로 분리했다. GET은
기존 explicit project permission 없는 company-wide `ACTIVE` read를 그대로 두며, PUT은 lookup 404 또는 alias
multi-project 409을 먼저 판정한 뒤 같은 connection의 `PROJECT_THRESHOLD_MANAGE` → principal/시간 → BEGIN →
threshold update·criterion-specific scalar recalc·audit → COMMIT → post-commit fetch 순서를 유지한다. generic
OpenAPI와 deprecated alias도 보존한다.

Focused **22 passed**, architecture·OpenAPI·compile gate와 full backend **1118 passed, 10 skipped**를 확인했다.
현재 `main.py`는 **1,322줄**이고 direct `.execute()` actual/ceiling은 **71**이다. 로컬 완료 범위는 unit·DuckDB·
OpenAPI·architecture·full 검증까지다. GET의 explicit project permission 부재와 alias global semantics, row
lock/CAS, post-commit fetch rollback seam은 보존 기술부채로 남긴다. provider construction purity, dict
mutation/storage normalization, unordered media/template, company-wide read와 single threshold semantics도 별도다.

PostgreSQL 18 app-role 권한과 audit INSERT, correlated update parity, OIDC active-nonmember/cross-project 정책,
동시 update locking/CAS, 현실 데이터의 EXPLAIN/index/lock latency는 사내 전용 release gate다. 다음 우선순위는
**재감사 후 확정**한다.

`workflow_queries`는 `GET /api/requests/{request_id}/workflow`와 `GET /api/workflows`를
HTTP/application/domain port/persistence로 분리해 `main.py`가 router composition만 소유하게 했다. Detail은
analysis request 404를 monitoring 전에 판정하고, detail/list 모두 기존처럼 같은 connection에서 monitoring을
조회한다. List의 join·`min`/`COALESCE`·group·`requested_at DESC` SQL과 기본값, status overwrite 및 10-field
projection을 그대로 유지했다. 두 GET에는 legacy와 같이 explicit permission·audit·transaction이 없다.

Focused **19 passed**, architecture·OpenAPI·compile gate와 full backend **1127 passed, 10 skipped**를 확인했다.
직접 `wc`로 측정한 `main.py`는 **1,263줄**, direct `.execute()` actual/ceiling은 **69**다. 이 slice는 개인
노트북 검증으로 완결되며 별도 PostgreSQL 필수 검증은 추가하지 않는다. 기존 office-only release gate는 유지한다.

`request_load_cases`는 `GET /api/requests/{request_id}/load-cases`를 HTTP/application/domain/persistence로
분리했다. exact `SELECT * FROM load_cases WHERE request_id = ? ORDER BY created_at`(ASC), 같은 connection,
missing request의 `200 []`, `parameters_json` pop 뒤 JSON 또는 malformed raw 문자열을 `parameters`로 투영하는
기존 동작을 보존했다. permission·audit·transaction·별도 error mapping은 추가하지 않았다.

Focused **11 passed**, workflow/request contract pair **15 passed**, architecture·OpenAPI·compile gate와 final
full backend **1133 passed, 10 skipped**를 확인했다. 직접 `wc`로 측정한 `main.py`는 **1,251줄**, direct
`.execute()` actual/ceiling은 **68**이다. 과거 workflow/request 테스트는 monotonic ceiling과 baseline equality를
검증하도록 보강했다. 이 slice도 개인 노트북 검증으로 완결되며 별도 PostgreSQL 필수 검증은 없다. 기존 office-only
release gate는 유지했고, 이후 feature-examples 재감사를 수행했다.

`feature_examples`는 `GET /api/feature-examples`를 HTTP/application/domain catalog+policy/persistence로 동작
변경 없이 분리했다. 정확한 12개 item의 content·순서·optional field는 요청마다 새 dict/list로 만들며 모든 item에
`data_profile`을 투영한다. load case가 없는 4개는 zero profile이고, 나머지 8개는 같은 connection에서 기존 `LEFT JOIN`
aggregate를 항목별로 한 번씩 호출한다. duplicate multitype도 두 번 호출하며 missing ID는 aggregate zero가 된다.
명시적 permission·audit·transaction·별도 error mapping은 legacy처럼 없다.

AST catalog exact equality, focused **9 passed**, architecture·OpenAPI·compile gate와 full backend **1140 passed,
10 skipped**를 확인했다. 직접 `wc`로 측정한 `main.py`는 **1,209줄**, direct `.execute()` actual/ceiling은 **67**이다.
개인 노트북 검증으로 완결되며 PostgreSQL 필수 검증은 없다. 후속 grouped query **8→1** 최적화의 실데이터
`EXPLAIN`/latency 검증은 office-only release gate에서 수행한다.

grouped 최적화에서는 application이 8개 reference를 stable dedupe한 7개 ID로 `data_profiles()`를 한 번 호출한다.
adapter도 defensive dedupe하며 empty 입력은 query 0회, nonempty는 dynamic bound placeholder와 grouped SQL 1회다.
SQL에서 빠진 ID 행은 application이 zero profile로 보완하고, duplicate multitype 카드의 profile은 값은 같되 독립 dict다.
DuckDB legacy per-ID exact equality, focused **12 passed**, architecture·OpenAPI·compile gate와 full backend **1143
passed, 10 skipped**를 확인했다. `main.py` **1,209줄** 및 direct `.execute()` actual/ceiling **67**은 변동 없다.
CI는 query-count/result parity를 보장한다. PostgreSQL/DuckDB 대표 실데이터의 `EXPLAIN ANALYZE`, fan-out, cold/warm
p50/p95, rows scanned, planning·lock impact는 office-only 검증이며 이후 portfolio read/export를 분리했다.

`portfolio`는 `GET /api/portfolio/overview`와 `GET /api/portfolio/export.csv`를 HTTP/application query/domain
port+policy/SQL adapter로 분리했다. date 범위·project·analysis type·status·`search`(max 120)의 6개 filter,
latest-run/monitoring/KPI/placeholder projection과 CSV BOM·12열 header·download filename을 그대로 유지한다. 기존
`repositories/portfolio.py`는 내부 사용처가 없어 제거했다. focused **17 passed**, full backend **1149 passed, 10 skipped**,
architecture·OpenAPI·compile gate를 확인했고 `main.py`는 **1,176줄**, direct `.execute()` actual/ceiling은 **67**로 불변이다. 개인 노트북에서 완결되며
별도 PostgreSQL 검증은 필요 없다.

`dashboard` read cluster는 detail/list/version list/version detail 4개 GET을 HTTP/application/domain errors+policy+ports/
SQL adapter로 분리했다. legacy global route order(detail/list → save PUT → versions → delete), active 공개 조회,
draft/archived 조건부 permission, 404 선판정, `include_invalid`, `definition_json` list exclusion과 exact response
wrapper를 유지한다. 같은 connection 조회를 사용하며 audit·transaction·write 동작은 변경하지 않았다. focused
**18 passed**, full backend **1153 passed, 10 skipped**, compile·architecture·OpenAPI gate를 확인했고 `main.py`는
**1,087줄**, direct `.execute()` actual/ceiling은 **60**으로 하향했다. 개인 노트북 검증으로 완결되며 별도 PostgreSQL은
필요 없다. 운영 권한·실데이터·성능 검증은 사내 release gate로 남긴다.

`GET /api/projects/{project_id}/requests`도 requests query router → application query → domain port/error → SQL adapter로
분리했다. global route order(GET → POST create → PATCH assignee/next load-case), 같은 connection에서의
`PROJECT_DATA_VIEW` 확인과 membership bool, 비멤버의 정확한 `PROJECT_MEMBERSHIP_REQUIRED` 403
(`authorization_detail`/audit 포함), global admin의 누락 project `200 []`, `requested_at DESC` 정렬을 그대로 보존한다.
audit·transaction·write 동작은 변경하지 않았다. focused **26 passed**, full backend **1159 passed, 10 skipped**,
compile·architecture·OpenAPI gate를 확인했고
`backend/app/main.py`는 **1,070줄**, direct `.execute()` ceiling은 **60→59**다. 개인 노트북 완결 범위이며 별도
PostgreSQL 검증은 필요 없다. dashboard command preview, drop-video catalog-only read, analysis runs GET,
health GET과 dashboard page read/admin command cluster까지 완료됐으며, 다음은 일반 dashboard 쓰기 4개다.

재감사 결과 dashboard command preview, drop-video catalog-only read, analysis runs GET, health GET과 dashboard page
read/admin command cluster까지 완료했다. 다음은 일반 dashboard 쓰기 4개이며 drop-video content/download streaming은 제외한다.

Phase 2의 `result_ingestion`은 세 안전 단위로 정리했다. 첫 단위는 결과-import template, 수동 결과
import, 예제 폴더 import endpoint를 `main.py`에서 `routers/result_ingestion.py`로 분리했다. 두 번째
단위는 framework-neutral `application/results/ingestion.py`가 parser·canonical command·ingestion
orchestration을 소유하고, router가 Request·connection·권한 재검증·audit sink·HTTP 응답만 조립하게
했다. 세 번째 단위는 framework-neutral query port/use case를 도입해 typed target-only read와
manual context·threshold·catalog read를 분리했다. 기존 persistence UoW/SQL facade, 권한·연결 수명과
query 순서는 유지한다. 결과 미디어 read 전용 HTTP(`GET/HEAD /api/assets/{asset_id}` 및
`/download`)는 `routers/media.py`로 분리했고, result-media read는 framework-neutral query use case와
typed domain read model/port, SQL persistence adapter를 통해 같은 연결에서 metadata → project-scope
authorization → blob 순서를 유지한다. DB blob의 ETag/Range/stream/download와 기존 audit 동작은
보존하고, media write/import/storage mode 전환은 후속 단계다.

`workbench`의 첫 read-only 단위는 업무 유형과 의뢰 유형 catalog(`GET /api/workbench/task-types`,
`GET /api/workbench/request-types`)다. 두 endpoint는 기존 `all_versions` 선택, 응답의 decoded JSON
shape와 route/OpenAPI 계약을 유지하면서 framework-neutral catalog query use case, typed domain read
model/port, SQL adapter로 이동했다. 이어서 `GET /api/workbench/requests/{request_id}/request-type`도 같은
경계로 이동해 repository의 context → assignment → assigned type 또는 active catalog rule 순서를 한 연결에서
그대로 사용한다. 네 resolution payload와 missing-request 404, 무인증 read 계약은 유지한다. 작업 계획 변경,
실행 정의, batch 실행과 다른 변경 endpoint는 다음 단위에 남긴다. request-type assignment `PUT`은 router가
principal source와 `REQUEST_EDIT` 권한, HTTP mapping을 유지하고 application command가 같은 connection에서
immutable work-plan guard → repository assignment 순서를 소유한다. repository의 request/type/admin-lock/upsert/
resolution SQL은 persistence adapter가 위임해 유지한다. `GET /api/workbench/requests/{request_id}/work-plan`도
같은 query boundary에서 request 존재 확인 후 기존 canonical monitoring projection을 같은 연결로 호출한다.
두 not-found 상태와 성공 payload는 HTTP adapter에서 기존 계약으로 변환하며, monitoring 계산 자체는 service의
single source of truth로 남긴다. 첫 lifecycle command 단위인 `PATCH /api/workbench/work-items/{item_id}/progress`는
typed command/state/error와 command port/use case/SQL adapter로 옮겼다. router는 principal actor, assigned-work
permission과 override audit, HTTP 오류 및 transaction commit/rollback만 유지한다. application은 item read → authorize
→ audit → IN_PROGRESS → no-op monitoring 또는 monotonic UPDATE → canonical status sync 순서를 소유하며, adapter는
기존 work-item read/UoW와 monitoring service를 같은 connection으로 감싼다. 이어서
`POST /api/workbench/work-items/{item_id}/start`도 같은 private lifecycle adapter 기반으로 분리했다. application은
item read → authorize → audit → idempotent monitoring 또는 current ready item → prior-incomplete → READY-to-IN_PROGRESS
transition → canonical status sync 순서를 소유한다. router의 principal actor, HTTP mapping 및 commit/rollback은 유지한다.
`POST /api/workbench/work-items/{item_id}/complete`도 같은 lifecycle UoW/monitoring facade에서 별도 complete
port/use case/SQL adapter로 옮겼다. application은 completed idempotency, current/started/prerequisite guard, optional
same-request succeeded demo-run 검증, principal-owned completion 기록, 다음 WAITING의 READY 승격과 canonical sync 순서를
유지한다. router는 기존 404/409 payload와 audit/transaction 경계를 유지한다. batch는 후속이다.
`PATCH /api/workbench/work-items/{item_id}/assignee`도 typed reassignment command/state/assignee read와 command
port/use case/SQL adapter로 분리했다. application은 joined item/request context read → workflow-edit authorization →
completed guard → canonical active project-member resolution → owner update → audit callback → response reread 순서를
소유한다. membership/account-state의 기존 422 payload와 reassignment audit detail, single rollback과 response shape은
router/adapter 경계에서 유지하며 batch는 후속이다.
`POST /api/workbench/work-items/{item_id}/batch-dispatch`는 마지막 execution slice로 command/port/application boundary를
도입했다. application은 item read 직후 router가 제공한 assigned-execution permission callback을 호출하고, idempotency replay,
profile/version guard, PREFLIGHT와 QUEUED commit, 독립 DEMO_ONLY run 생성, FAILED 또는 SUCCEEDED finalization의 세 단계
순서를 명시적으로 조정한다.
adapter는 같은 connection에서 단계별 query/persistence, preflight 및 기존 demo-run service를 제공한다. preflight rejection
record commit과 runner의 독립 transaction은 기존 실행 계약으로 보존하며, router는 permission/audit callback, sensitive run
projection sanitization과 HTTP mapping을 소유한다.
`0018_batch_attempt_run_identity`는 runner commit 뒤 finalization 전에 중단되어도 run을 정확히 식별할 수 있게 한다.
batch adapter는 attempt ID에서 결정적인 `demo-{attempt_id}`를 만들고 `workflow_runs.batch_attempt_id`와
`batch_dispatches.attempt_id`가 같은 attempt를 1:1로 보존한다. 재호출은 request·DEMO_ONLY mode·created-by·attempt ID가
모두 일치할 때만 기존 run을 반환하며 충돌하는 deterministic ID는 fail-closed한다. recovery identity는 public WorkflowRun
projection에 노출하지 않고, migration은 SUCCEEDED historical link만 backfill하며 legacy QUEUED orphan을 추정하지 않는다.
Duplicate idempotency-key가 과거 rejection record를 가리킬 때도 non-admin 409 detail의 attempt snapshot과 command preview는
`_sanitize_batch_attempt`로 비공개 처리하며, admin은 기존 원문을 유지한다. idempotency check/insert 경쟁과
QUEUED→runner→finalization의 lease claim, retry API, scheduler, 자동 recovery와 status 의미 변경은 현재 실행 경계 밖의
후속 release-gated reliability slice로 남긴다.

`0019_batch_recovery_lease`는 그 후속 slice를 위한 내부 claim/renew/release ownership만 추가했다. 전용 application/SQL
adapter는 DB UTC clock과 owner/token/generation CAS로 0018의 정확한 QUEUED crash-window만 점유하며, lease field는 admin을
포함한 모든 public attempt/run projection에서 제거된다. router·scheduler·worker·재실행·finalization은 연결하지 않았고,
future finalization은 같은 transaction에서 active lease CAS를 요구한다.

### 3.2 비즈니스 로직과 데이터 접근

| 경로 | 사용 방식 |
|---|---|
| `backend/app/services/` | import, media, monitoring, verdict, OIDC, 배치 실행, 요청 결과 구성 같은 절차형 orchestration |
| `backend/app/application/` | `projects`, `products`, `requests`, `results`, `reports`, `workbench`, `workspace_layouts`의 명시적 command/query use case |
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

미디어는 신규 canonical import와 reference/seed write 모두 `asset_blobs`/
`asset_blob_chunks`에 chunk 단위로 먼저 저장한 뒤 같은 transaction에서
`media_assets.blob_id`로 연결한다. `SIMDASH_MEDIA_STORAGE_MODE`는 명시적
`dual-read`(기본) 또는 `database-only`이며, dual-read에서만 blob이 없는 기존 asset과
허용 demo video에 내부 filesystem fallback을 허용한다. Rocky 설치 profile은
`database-only`를 기본으로 고정하고 설치 후와 systemd 시작 시 app-role preflight를
실행한다. 실제 Rocky/NFS·quota, 빈 DB restore rehearsal, 부하와 startup timeout
적정성은 별도 release gate로 남아 있다.

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

화면과 controller 일부가 `app/workspace/WorkspaceRouteRenderer.tsx` 및 `features/`로 추출되어 있다. 다만 실제 `main.tsx → App.tsx` 경로는 아직 App 안의 feature 조립부를 사용하므로 추출된 renderer만 수정해서는 화면에 반영되지 않는다. GUI 개편의 현재 기준과 기존 문서 대체 범위는 [`request-centric-workspace-ux.md`](request-centric-workspace-ux.md)를 따른다. `App.tsx`는 아직 최종 wiring-only 수준은 아니다.

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
  → process-local lock + cross-worker execution gate 획득
  → strict stale snapshot 정리와 manifest.json 탐색
  → root/symlink/context/파일 검증
  → 전용 0700 private snapshot root에서 manifest parse 뒤 capacity 확인
  → private snapshot (kind별 structured/media ceiling, unsupported kind open 전 거부)
  → manifest + mapping 파일 bundle fingerprint 기반 중복 판정
  → normalized parser payload
  → ResultIngestionCommand
  → ResultIngestionUnitOfWork/provider의 single-connection transaction
  → AnalysisRun + 유형별 결과 + media blob 원자적 저장
  → request/work status 동기화
  → manifest별 IMPORTED/SKIPPED/FAILED 응답과 Refresh 집계 감사 이벤트
```

클라이언트는 서버 경로를 넘기지 않는다. 잘못된 manifest 하나는 다른 정상 bundle의 처리를 중단시키지 않는다.
실패 bundle은 검증된 target이 있을 때만 DB failure job을 기록하고, target을 안전하게
확인할 수 없으면 job을 만들지 않은 채 sanitized `RefreshItem` 오류로 반환한다.

Master Refresh의 private snapshot workspace는 import root와 물리적으로 중첩될 수
없고, symlink가 아닌 service-owned `0700` directory여야 한다. POSIX
`dir_fd` 상대 open과 `O_NOFOLLOW`로 경로·symlink를 fail-closed한다. manifest를
파싱한 뒤 per-bundle reserve와 min-free를 확인하며, 이 app capacity gate는
kernel/filesystem quota가 아니다. 부족하거나 `ENOSPC`/`EDQUOT`가 발생하면
`BUNDLE_SNAPSHOT_RESERVE_UNAVAILABLE`로 안정적으로 실패한다. native Windows
compatibility profile은 POSIX secure traversal과 flock이 없으므로 이 endpoint를
`RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE`로 fail-closed한다. 수동 upload와 다른
compatibility 기능은 이 제한과 독립적으로 동작한다.

P1-03은 **코드·disposable 자동검증 완료, 운영 증적 대기** 상태다. 현재 Alembic
revision graph를 읽는 media verifier와 strict shared inventory를 사용하며, reference
demo load case(`loadcase-drop-bottom-001`)가 있을 때만 exact allowlist 20개를
검증하고 fresh `SEED_MODE=empty` production DB에서는 expected/actual demo 0개를
허용한다. backup은
`pg_dump`와 같은 exported snapshot에서 inventory를 만들고 restore는 app-role exact
comparison 뒤 database-only verifier를 실행한다. transfer bundle v2는 blob-bound
media asset을 ZIP에 중복 포함하지 않고 v1은 fail-closed한다. migration/cleanup
receipt/evidence는 O_EXCL 예약형 no-overwrite `PENDING`에서 `COMPLETED` 또는 `FAILED`로
남는 recoverable journal이다. cleanup은 migration receipt·실제 regular non-symlink backup
dump·backup manifest·approval/confirmation receipt와 7-day 보존 조건을 모두 확인하는
evidence-gated 명시 실행이다. manifest 단독은 허용하지 않으며 dump의 filename·bytes·
streamed SHA-256을 대조하고 private 0700 staging 사본의 동일 bytes에 `pg_restore --list`
archive parse를 DB 접근 및 삭제 전에 수행한다. 삭제 시에는 same-parent private quarantine으로 원자 rename한 뒤 inode와
hash가 같은 파일만 unlink하고, 불일치 대상은 quarantine에 보존한다.
실제 Rocky/NFS·quota, production backup→빈 DB restore rehearsal, 500 MiB/50 stream
부하와 5분 startup timeout 적정성, PowerShell 실실행은 외부 release gate다.

Refresh와 retry는 모두 process-local lock 뒤 동일한 global execution gate를
획득하고, 그 안에서만 strict stale snapshot을 정리한다. DuckDB/local은 private
workspace의 안전한 lock file에 POSIX nonblocking `flock`을 사용하고, PostgreSQL은
request/media pool 밖의 전용 session에서 import-root keyed advisory lock을 사용한다.
따라서 현재 계약은 `SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT=1`의 단일 refresh다.
gate 경합은 `RESULT_IMPORT_REFRESH_BUSY`, 획득·해제 불능은
`RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE`로 반환한다.
AP-2 PostgreSQL gate는 2026-08-25 disposable PostgreSQL 18.6 `127.0.0.1:55436`에서
두 전용 session의 BUSY, 정상 unlock/close 뒤 재획득, unlock 없이 session close한 뒤
재획득을 확인했다. 기존 5432/`.env` DB는 사용하지 않았고 cluster와 `/tmp` data/log를
정리했다.

`backend/app/services/canonical_result_bundle.py`가 marker v1과 canonical publication
path 계약을, `services/result_bundle_publisher.py`가 producer source→final no-replace
publication을, `services/bundle_snapshot.py`가 importer의 descriptor-relative immutable
capture를 맡는다. `backend/app/parsers/manifest_format.py`가 공통 format detector/loader 경계다.
canonical `mappings`는 `scan_folder`와 Master Refresh로, legacy
`result_files`는 parser schema와 기존 `ManifestParser` alias로만 명시적으로
분기한다. mixed/unknown manifest, 잘못된 importer, root escape와 symlink는 각
경계에서 fail-closed한다. `backend/app/application/results/commands.py`가
정규화된 canonical command를 orchestration하고,
`backend/app/adapters/persistence/result_ingestion.py`가 현재 DuckDB/PostgreSQL
공통 SQL UoW를 제공한다. Master Refresh와 `/folder-import/example`은 이 UoW를
공유한다.

`streaming_json.py`와 incremental manifest loader는 `ijson==3.5.1`
(`backend/requirements.txt`/`requirements.lock`)로 manifest mapping·scalar record
cap+1과 bounded checksum stream을 구현한다. canonical manifest에는
`256 + 64 * max_mapping_count` 전체 event ceiling이 있고, typed scalar 한 item에는
64 event ceiling이 있어 `ObjectBuilder` materialization 전에 복잡도를 제한한다.
private snapshot은 `typed_scalars`/`curve_csv`에 `max_structured_bytes`, media에
`max_file_bytes`를 copy 전에 적용하며, 지원하지 않는 mapping kind는 파일을 열기 전에
거부한다. 최종 normalized scalar 최대 100,000개 list는 여전히 메모리에
materialize된다. raw bounded stream은 unpaired Unicode surrogate escape를
backend-independent하게 거부하고 valid surrogate pair와 direct UTF-8은 유지하며,
ijson backend별 예외 진단은 공개 error taxonomy를 거쳐 importer의 고정 오류 코드로
정규화한다. Windows/Rocky wheel 설치 호환성은 target release gate에서 별도로 actual
wheel/offline smoke로 확인한다.

일반 수동 `SUMMARY_RESULT` JSON/CSV와 `Radioss` mesh CSV upload도 같은 normalized
payload와 UoW를 사용한다. target-qualified `source_name`과 content checksum으로
재시도를 `SKIPPED`하고, write transaction 안에서 `result.import` 권한을 재확인하며
`RESULT_IMPORTED` audit event를 원자적으로 기록한다. Radioss 전용 adapter는
scalar, time-series/curve, `result_locations`를 canonical contract로 변환해 같은
single-connection transaction에 저장한다.

구형 `result_files` 기반 persistence service/repository는 현재 schema에 없는
`result_import_jobs`와 `analysis_runs` 확장 컬럼을 참조해 동작하지 않았으므로
제거했다. legacy manifest schema, `ManifestParser` alias와 normalized parser adapter는
parser/manifest 호환 전용으로 남고, runtime 결과 쓰기는 canonical UoW만 사용한다.

PostgreSQL canonical ingestion은 load case별 namespaced 64-bit transaction
advisory lock으로 `run_no` 할당을 직렬화하고, migration `0017_run_identity_v2`의
`canonical_result_ingestion_source_versions`를 source identity 정본으로 사용한다.
ledger는 `(load_case_id, source_type, source_key, source_revision)`으로 범위화되며
checksum, producer `source_run_id`, server-assigned `analysis_run_id`, conflict policy와
immutable `supersedes_analysis_run_id`를 보존한다. `(load_case_id, run_no)`는 unique다.
이전 migration `0016` 예약 테이블은 backfill 입력인 historical compatibility일 뿐
runtime reservation의 정본이 아니다.

동일 checksum은 기존 run의 id/no를 반환하는 `SKIPPED/NOOP`이다. 명시적
`source_run_id`의 변경 checksum은 `SKIP`, `REJECT`, `REPLACE`를 적용한다. `REPLACE`도
기존 결과를 삭제하거나 변경하지 않고 새 immutable run을 만든다. source ID가 없는
legacy 경로는 `name:<source_name>` slot에 `LEGACY_APPEND` revision을 쌓는다.
수동 import의 `REJECT`는 terminal job과 audit를 commit한 뒤 HTTP 409
`SOURCE_RUN_CONFLICT`를 반환하며, Master Refresh는 producer `REPLACE`를 fail-closed하고
형제 manifest를 계속 처리한다.

### 5.3 결과 등록 이력·상태·재시도

`folder_import_jobs`는 Refresh가 생성하는 결과 수집 이력의 정본이다. 성공뿐 아니라
검증된 target의 실패도 source type/checksum, producer `source_run_id`, conflict policy,
reason code와 완료 시각을 함께 남긴다. 이력 조회는 V2 source-version ledger를
`analysis_run_id`로 read-only join해 source revision과 교체된 run을 운영자에게
보여준다.

- `GET /api/load-cases/{load_case_id}/result-imports?status=&limit=25&offset=0`은 해당
  load case의 `RESULT_IMPORT` resource scope를 요구한다. 응답은
  `{items,total,counts}`이며, item에는 상태·source 정보·policy·`operation`·reason·기존/
  교체 run·source revision·생성/완료 시각·`retryable`이 포함된다. `counts`는 상태
  필터와 무관하게 해당 load case 전체 이력의 상태별 수다.
- `POST /api/result-imports/{job_id}/retry`는 전역 `SYSTEM_CATALOG_MANAGE`와 그 job의
  load case에 대한 `RESULT_IMPORT`을 모두 요구한다. body나 client path를 받지 않고,
  DB에 남은 Master manifest 상대경로만 다시 사용한다. `FAILED`/`REJECTED` Master job만
  대상이며 그 밖의 경우 고정 HTTP 409 `RESULT_IMPORT_NOT_RETRYABLE`을 반환한다.
  원 job의 project/request/load case target에 고정해 manifest의 target drift는
  `RESULT_IMPORT_RETRY_TARGET_MISMATCH`로 실패시킨다. missing manifest나 snapshot 초기
  실패도 원 load case에 새 `FAILED` attempt를 남기며 원 job은 변경하지 않는다.
  재시도와 전체 Refresh는 process-local lock과 동일 cross-worker execution gate를
  공유한다. 따라서 backend/DB 종류와 무관하게 한 번에 하나만 실행하며, stale cleanup도
  gate 획득 뒤에만 수행한다.
- 프런트의 `features/data/ResultImportHistory.tsx`는 DataWorkspace의 선택 load case에
  이력, 상태 필터, 새로고침, loading/error/empty 상태를 표시한다. 재시도 버튼은
  서버 `retryable` 값과 전역 권한이 모두 충족될 때만 보이며, 결과 뒤 이력을 다시
  조회한다. 현재 UI는 필터/새로고침을 포함한 최근 25건만 표시하며 페이지 이동은
  다음 개선 범위다.

`backend/tests/test_postgres_result_ingestion_concurrency.py` 등 opt-in 검증은
`ANALYSIS_TEST_POSTGRES=1`, `ANALYSIS_TEST_POSTGRES_DATABASE`와 실제
`current_database()` 일치를 요구한다. 2026-08-25에는 loopback 55433의 disposable
PostgreSQL 18.6 test DB에서 빈 DB `0001→0017`, 기존 `0016→0017` backfill,
app-role DDL 거부, pool budget, reference seed와 동시성 test **2 passed**를 확인했다.
실제 SQL provider로 CREATED/exact NOOP/SKIP/REJECT/immutable REPLACE revision도
검증했다. 종료 후 cluster·DB·로그를 제거하고 residue 0과 port 종료를 확인했으며,
기존 5432 DB와 `.env` 연결은 사용하지 않았다.

실제 ID 계층·JSON·CSV·SVG·glTF 예제는 `examples/master-results/`에 있으며 backend 통합 테스트가 이를 직접 import한다.

### 5.4 대시보드와 보고서

- dashboard definition과 version은 서버에 저장한다.
- 프로젝트/의뢰/하중 경우 context와 권한을 서버가 다시 검증한다.
- 규칙 기반 자연어 요청은 구조화된 변경안을 미리 보여준 뒤 적용한다. 임의 SQL·shell·코드를 실행하지 않는다.
- 보고서 layout/version/template metadata는 서버가 관리한다. 일반 report export 조립은
  프런트의 `reportExport.ts`가 수행하고, 업로드된 native PPTX template의 placeholder
  render는 서버 document adapter가 수행한다.

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

결과 수집의 focused 계약 검증은 canonical/legacy
manifest 경계, fingerprint idempotency와 mapping 변경 감지, 공통 UoW atomic
rollback, manual `SUMMARY_RESULT` JSON/CSV의 target-qualified source·retry
`SKIPPED`·audit/auth 재확인, Radioss scalar/curve/location atomic 저장, streaming
JSON·folder import·bundle snapshot limits, 모든 media extension fixture의 정상/
MIME mismatch/corrupt signature, PostgreSQL reservation SQL/migration contract와
endpoint wiring을 포함한다. PostgreSQL 동시성 test는 아래 collection에 포함되지만
실행 시 opt-in marker라 기본 suite에서는 안전하게 skip된다.

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
  tests/test_api.py::test_typed_folder_example_registers_scalars_curves_media_and_catalog \
  tests/test_postgres_result_ingestion_concurrency.py \
  tests/test_streaming_json.py \
  tests/test_folder_import_limits.py \
  tests/test_import_bundle_limits.py \
  tests/test_bundle_snapshot.py \
  tests/test_result_bundle_readiness.py \
  tests/test_result_bundle_publisher.py \
  tests/test_publish_result_bundle_cli.py \
  tests/test_result_import_history.py \
  tests/test_run_identity_contracts.py \
  tests/test_run_identity_migration.py \
  tests/test_run_identity_v2.py
# dedicated migrated disposable test database only; never use the regular 5432 DB
ANALYSIS_DB_BACKEND=postgresql \
ANALYSIS_TEST_POSTGRES=1 \
ANALYSIS_TEST_POSTGRES_DATABASE=<dedicated_test_db> \
DATABASE_URL='postgresql+psycopg://<test_app_role>:<test_password>@<test_host>:5432/<dedicated_test_db>' \
../.venv-wsl/bin/python -m pytest -q \
  tests/test_postgres_result_ingestion_concurrency.py \
  tests/test_run_identity_v2.py
```

`DATABASE_URL`은 명령에서 명시한 전용 test DB여야 하며 `.env`의 현재
`simulation_dashboard` 연결은 이 test에 사용하지 않는다.

import history/status/retry slice의 별도 검증 기록에는 PostgreSQL 18.6 disposable
`127.0.0.1:55434`의
`simulation_dashboard_test_history`에서 blank Alembic head `0017`, app
privilege/DDL denial, history list/filter/pagination/counts, V2 revision join, GET,
missing retry `FAILED` append, target drift mismatch/no foreign run도 PASS했고
cluster/port를 정리했다.

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
- result review list GET은 explicit `PROJECT_DATA_VIEW`를 확인하지 않는 보안 부채다. 구조 refactor에
  섞지 않고 별도 security 변경으로 다룬다.
- 현재 URL은 workspace 진입점만 표현하고 project/request/load-case 선택을 deep-link로 보존하지 않는다.
- demo와 reference seed가 같은 fixture alias다.
- 외부 NAS/NFS/SMB `SIMDASH_IMPORT_ROOT`의 부팅 순서, mount context, 용량과
  재처리 운영 절차는 각 사내 인프라 환경에서 승인해야 한다.
- Master Refresh는 importer private snapshot/rehash와 parser workload limits로
  fingerprint·parse·media 저장 bytes를 고정한다. AP-2는 dedicated `0700` workspace,
  import-root 비중첩·symlink·소유권 검증, reserve+min-free app capacity gate,
  cross-worker single-refresh gate와 retry/full-refresh 공유를 코드·focused 계약
  검증으로 반영했다. PostgreSQL connection budget은 gate 전용 session을 worker당
  하나 포함하므로 기본 2 worker에서 62다. producer는 sibling staging → payload fsync
  → marker v1 last → `renameat2(RENAME_NOREPLACE)` → parent fsync로 publication하고,
  app service import root는 read-only다. local/default `legacy`와 Rocky `required`
  marker 정책, full-scan/retry strict 동작, WSL/Rocky ext4/XFS 한정과
  Windows/EXDEV/NFS/SMB fail-closed·미승인 범위는 storage contract에 따른다.
  실제 Rocky host deploy, NFS/SMB mount capability, filesystem quota 적용과 native
  Windows adapter/target wheel smoke는 release gate로 남아 있다.
- **2026-08-25 AP-2 검증 기록:** focused 통합은 `126 passed in 87.32s`, full backend는
  `573 passed, 5 skipped in 382.12s`였다. backend architecture/OpenAPI/compileall,
  Rocky validator, frontend architecture/API self-test/build도 통과했다.

dashboard_writes 일반 저장·버전 무효화·복제·복원 4개 API와 `request_load_cases` POST 생성 slice도 완료했다. 다음 구조 대상은 프로젝트 의뢰 생성 POST이며, 그 뒤 workflow writes를 진행한다. drop-video streaming은 후순위다.

### 2026-09-01 최신 vertical slice — dashboard_writes 일반 쓰기

4개 API를 `dashboard_writes` HTTP → application command → domain errors/policy/ports → persistence adapter로 분리했다. 입력·권한·오류 매핑, pre-connect ID mismatch 400, invalid history 포함 version `max+1`, clone의 `page` 제거, restore의 현재 name/description/page 보존과 기존 비명시 transaction semantics를 유지한다. Dedicated **6 passed**, root expanded focused **36 passed in 44.44s**, independent **5 passed/1 deselected in 6.43s**, full backend **1207 passed, 10 skipped in 914.50s (0:15:14)** 및 compile·architecture·OpenAPI·diff-check를 통과했다. `main.py`는 **628 → 487줄**, direct `.execute()`는 **43 → 30**이다. 로컬 검증 범위와 사내 office-only PostgreSQL·proxy·동시성·latency gate를 분리해 유지한다.

우선순위와 완료 기준은 [`program-consolidation-and-development-plan.md`](program-consolidation-and-development-plan.md)에 정리한다.

### 2026-09-01 최신 보강 — dashboard command preview

`POST /api/dashboard-commands/preview`를 HTTP router → application query → domain pure allowlist policy로 분리했다.
권한 검사는 policy 실행보다 먼저 수행하며 DB·audit·명시적 transaction은 추가하지 않고 generic
`SecurityMiddleware`의 `API_MUTATION` 기록을 유지한다. 기존 ASCII 공백 제거·소문자 정규화, branch precedence,
6개 인식 명령과 1개 미인식 응답, `datetime.now().timestamp()` 기반 widget ID와 테스트용 epoch seam을 보존했다.
restore 직후 마지막 route 위치, operationId, `2..500` 명령 길이와 optional `project_id` 계약도 그대로다. focused
root **15 passed**, 전체 backend 회귀는 **1164 passed, 10 skipped in 891.76s (0:14:51)**였고 compile·architecture·OpenAPI
gate도 확인했다. `main.py`는 **1,009줄**, direct `.execute()`는 **59**다. 개인 노트북 검증으로 완결되며 별도
PostgreSQL 검증은 필요 없다. dashboard page admin command cluster도 후속 완료했고 현재 다음은 일반 dashboard 쓰기 4개다.

### 2026-09-01 최신 보강 — drop-video catalog-only read

`GET /api/load-cases/{load_case_id}/drop-videos`를 HTTP router → application query → domain pure policy/ports →
SQL repository 및 filesystem example adapter로 분리했다. 같은 connection에서 load-case context 조회 →
`PROJECT_DATA_VIEW` resource authorization → stored list 조회 후 connection을 닫는 순서를 유지하고, 이후 context
fail-closed 404 → storage mode 1회 → stored가 없고 dual-read일 때만 example file probe를 수행한다. stored/demo 혼합
금지, sort order·전체 summary·pagination, generic resource 404, route order와 OpenAPI 계약을 보존했다. focused root
**35 passed**, 전체 backend 회귀는 **1176 passed, 10 skipped in 881.53s (0:14:41)**였고 compile·architecture·OpenAPI gate를 확인했으며
`main.py`는 **899줄**, direct `.execute()`는 **58**이다.
개인 노트북 검증으로 완결되며 PostgreSQL 실데이터·권한 범위·대용량 latency는 사내 release gate로 남긴다.
dashboard page admin command cluster도 후속 완료했고 현재 다음은 일반 dashboard 쓰기 4개다.

analysis runs GET은 완료했으며, `adapters/http/routers/analysis_runs.py`가 기존 application/results query, domain
policy/port, SQL provider를 그대로 연결한다. auth-before-provider, unknown `200 []`, `run_no DESC`, `is_latest`,
seeded mapping과 6-query 동작을 유지했고 focused root **26 passed**, 전체 backend 회귀는 **1179 passed, 10 skipped in 886.22s (0:14:46)**였으며 compile·architecture·OpenAPI gate를 확인했다.
`main.py`는 **888줄**, direct execute ceiling **58**은 불변이며 PostgreSQL 전용 검증은 필요 없다.

health GET도 `GET /api/health`를 router → application query → persistence callable probe로 분리했다. `connect()` →
`SELECT 1`(params 없음) → `fetchone` 결과 무시 → close 후 backend settings를 읽으며, public auth bypass/invalid token 허용,
request-id·audit 없음, general 500 semantics와 retry → health → feature-examples route adjacency를 유지한다. focused root
**21 passed**, 전체 backend 회귀는 **1189 passed, 10 skipped in 891.45s (0:14:51)**였고 compile·architecture·OpenAPI gate를 확인했으며 `main.py`는 **883줄**, direct execute는 **57**이다.
개인 노트북에서 완결되며 domain/UoW는 추가하지 않는다. 사내 PostgreSQL pool/app-role/nginx TLS/systemd timeout은
release gate로 검증한다. dashboard page admin command cluster도 후속 완료했고 현재 다음은 일반 dashboard
쓰기 4개이며 streaming routes는 후순위다.

### 2026-09-01 최신 vertical slice — dashboard page public/admin GET

`GET /api/dashboard-pages`와 `GET /api/admin/dashboard-pages`를 각각
`adapters/http/routers/analysis_pages.py → application/analysis_pages/queries.py →
domains/analysis_pages`의 policy/ports/errors → `adapters/persistence/analysis_pages.py`로
분리했다. 기존 `dashboard_reads`는 별도 경계로 유지하며 동작은 바꾸지 않았다.

Public read는 context check를 유지하고 explicit project permission을 추가하지 않는다. system page와
현재 load case에 대해 published 상태인 custom page를 반환한다. Admin read는 같은 connection에서
`context → DASHBOARD_EDIT → context 재확인 → candidates` 순서를 보존한다. context 누락은 기존 Korean
404이고, permission 실패는 두 번째 lookup 전에 fail-closed한다. `system`/`custom`/`status`/`include_archived`
filter, SQL `ORDER BY` 부재와 최종 `(display_order, name.casefold(), id)` 정렬, falsy description의 `''`
투영을 그대로 유지한다.

기존 main compatibility wrapper는 write/reorder에서 같은 open connection을 계속 사용하며
`_list_analysis_pages` context preflight도 보존한다. create/update/delete/reorder의 transaction·audit
동작은 변경하지 않았다. focused combined **23 passed in 43.35s**, 전체 backend **1195 passed, 10 skipped in
886.77s (0:14:46)**와 compile·architecture·OpenAPI gate를 확인했다. `main.py`는 **824줄**, direct execute
ceiling은 **57 → 55**다.

로컬 laptop에서는 policy/fake/DuckDB/security/full 테스트를 완료할 수 있다. 실제 PostgreSQL app-role/admin
permission, same-connection behavior/real rows, proxy/OpenAPI smoke, real-data latency는 office-only release
gate다. 같은 기능의 admin write cluster(POST/PATCH/DELETE/PUT order)도 아래 기록처럼 완료했다. create에 새
transaction을 추가하지 않고, update의 resource-auth-first/version max+1, delete의 explicit BEGIN/rollback,
reorder의 duplicate precheck/exact-set 및 same-connection final list 계약을 유지한다. Workbench 대규모
restructure와 drop-video streaming은 계속 후순위다.

### 2026-09-01 최신 vertical slice — dashboard page admin commands

분석 페이지의 `POST /api/admin/dashboard-pages`, `PATCH/DELETE
/api/admin/dashboard-pages/{dashboard_id}`, `PUT /api/admin/dashboard-pages/order`도 read와 같은
`analysis_pages` feature에 들어왔다. HTTP adapter는 schema·권한 callback·오류 상태만 매핑하고,
application command가 create/update/delete/reorder 순서를 소유한다. Domain은 분석 페이지 오류·정책·port를,
SQL adapter는 같은 open connection에서 실행하는 조회·버전 append·삭제 transaction primitive를 맡는다.

Create/update/reorder는 기존처럼 명시 transaction을 새로 추가하지 않았다. Delete만 기존의 explicit
`BEGIN/COMMIT/ROLLBACK`을 유지하며, versions 삭제 뒤 body 삭제가 실패하면 rollback한다. 시스템 페이지 보호,
archived custom page를 포함한 다음 표시 순서, active name 충돌, 게시 전 widget 필수, exact reorder set,
same-connection final list, route/OpenAPI/Korean 오류 계약은 유지된다. `dashboard_reads.policies`의 분석 페이지
판정은 canonical `domains.analysis_pages.policies`를 재사용한다.

Focused **15 passed**, 독립 확대 검토 **22 passed**, full backend **1201 passed, 10 skipped in 901.42s
(0:15:01)**와 compile·architecture·OpenAPI gate를 통과했다. `main.py`는 **628줄**, direct `.execute()`는
**43**이다. 로컬에서는 이 경계를 완료했으며, PostgreSQL app-role/admin 권한·실제 rollback·proxy smoke·동시성은
사내 release gate로 남긴다. 이 시점의 다음 구조 대상이던 일반 dashboard 저장·버전 무효화·복제·복원
4개와 `request_load_cases` POST 생성은 후속 완료했다. 현재 다음 대상은 프로젝트 의뢰 생성 POST다.

### 2026-09-01 최신 vertical slice — request_load_cases POST 생성

기존 GET feature에 별도 `create_router`를 추가하고 POST를 기존 위치인 drop-video content/download 뒤, result-ingestion 앞에 등록했다. HTTP → application → domain → persistence 경계와 ID/time 생성 → provider → 동일 connection auth → request 존재 확인 → insert 순서를 보존했다. exact 201/404/OpenAPI, Unicode JSON 저장을 유지하고 새 명시 transaction·audit은 추가하지 않았다. Dedicated/latest focused **24 passed in 10.18s**, root expanded **46 passed in 30.75s**, independent review unit/contract **5 passed** 및 DuckDB **2 passed**, full backend **1215 passed, 10 skipped in 928.25s (0:15:28)**와 compile·architecture·OpenAPI·diff-check를 통과했다. `main.py`는 **487 → 473줄**, direct `.execute()`는 **30 → 28**이다. 로컬 검증은 완료했고 PostgreSQL app-role·same-connection·proxy·동시성·latency는 사내 release gate다. 다음은 프로젝트 의뢰 생성 POST, 그 뒤 workflow writes이며 streaming은 후순위다.
