# 개발 작업 절차

- 기준일: 2026-08-25
- 상태: 현재 코드 기준

이 문서는 기능을 추가하거나 구조를 변경할 때 따라야 하는 공통 절차다. 구조의 실제 책임은 [`current-architecture.md`](current-architecture.md), 우선순위는 [`program-consolidation-and-development-plan.md`](program-consolidation-and-development-plan.md)를 따른다.

## 1. 개발 원칙

1. 새 기능은 가능한 한 독립된 frontend feature와 backend router/use case/repository 경계를 가진다.
2. 기존에 검증된 오픈소스 패키지와 표준 라이브러리를 먼저 검토한다. 작은 요구를 위해 자체 framework를 만들지 않는다.
3. API, DB, 권한, 파일 계약을 변경하면 코드·migration·OpenAPI·예제·테스트·문서를 같은 작업에서 갱신한다.
4. 사용자 변경이 있는 작업 트리에서는 관련 없는 파일을 되돌리거나 정리하지 않는다.
5. Sol은 범위·계약·검증을 지휘하고, 기능별 Luna/Terra 구현은 서로 겹치지 않는 파일 경계로 나눈다.

## 2. 환경 준비와 실행

```bash
./setup-wsl.sh
./scripts/wsl/doctor.sh
./start.sh
```

- Web: `http://127.0.0.1:5173`
- API: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/api/health`
- 종료: `./stop.sh`

DuckDB가 기본 로컬 profile이다. PostgreSQL 개발은 별도 DB와 app/owner 역할을 준비한 뒤 [`backend-sql-integration-guide.md`](backend-sql-integration-guide.md)를 따른다.

## 3. 기능 변경 순서

### 3.1 작업 시작

1. `git status --short`로 기존 변경을 확인한다.
2. 관련 현재 문서, ADR, API, migration과 테스트를 읽는다.
3. 입력·출력·권한·DB transaction·실패 형태를 먼저 정의한다.
4. 기존 라이브러리와 인접 모듈로 해결 가능한지 확인한다.
5. 변경 파일과 수용 테스트를 기능 단위로 제한한다.

### 3.2 Backend API

```text
Pydantic request/response schema
  → thin router
  → application service/use case
  → domain policy/port
  → repository or persistence adapter
```

- 새 endpoint를 `backend/app/main.py`에 직접 추가하지 않는다.
- router는 SQL을 직접 실행하지 않는다.
- project scope와 permission은 서버에서 확인한다.
- 여러 table을 바꾸는 작업은 하나의 명시적 transaction으로 묶는다.
- 성공 응답에는 가능한 한 named `response_model`을 지정한다.

API 계약을 바꾼 뒤:

```bash
cd frontend
pnpm run generate:api
```

갱신 대상은 `frontend/openapi.json`과 `frontend/src/shared/api/generated/openapi.ts`다. 생성 파일을 직접 편집하지 않는다.

### 3.3 Frontend feature

- canonical workspace URL은 `features/navigation/workspaceRouteRegistry.ts`에만 정의한다.
- route-to-screen 연결은 `app/workspace/WorkspaceRouteRenderer.tsx` 또는 lazy route module을 사용한다.
- feature의 화면·상태·API adapter·스타일을 가까이 둔다.
- 새 상태를 `App.tsx`에 먼저 추가하지 말고 feature hook/controller가 소유하게 한다.
- 서버 호출은 `shared/api`의 생성 client와 named adapter를 사용한다.
- 오래 걸리는 요청은 context/generation guard로 오래된 응답을 폐기한다.

### 3.4 DB와 migration

- PostgreSQL schema 변경은 새 Alembic revision으로만 수행한다.
- `backend/migrations/schema.sql`, DuckDB compatibility DDL과 관련 이관 script를 함께 검토한다.
- canonical result ingestion은 load case별 namespaced 64-bit transaction advisory lock과
  migration `0017_run_identity_v2`의 source-version ledger
  `(load_case_id, source_type, source_key, source_revision)`를 사용한다. checksum,
  server-assigned run 및 immutable supersede 관계를 ledger에 보존하고, 이전 `0016`
  reservation은 historical backfill 입력일 뿐 runtime identity 정본이 아니다. 실패
  transaction은 source-version reservation과 결과 write를 함께 rollback한다.
- 빈 PostgreSQL과 기존 revision PostgreSQL에서 upgrade를 검증한다.
- app 역할의 DDL 거부와 owner/app 자격 증명 분리를 유지한다.
- DuckDB와 PostgreSQL에서 placeholder, JSON, upsert, transaction 동작을 모두 테스트한다.
- `backend/tests/test_postgres_result_ingestion_concurrency.py`는 실제 PostgreSQL에서
  독립 연결 두 개를 사용한다. `ANALYSIS_TEST_POSTGRES=1`과
  `ANALYSIS_TEST_POSTGRES_DATABASE`를 모두 지정하고 `current_database()`가 일치할
  때만 실행되므로 일반 개발 DB에는 실행하지 않는다. 2026-08-25 disposable
  PostgreSQL 18.6 live gate에서 blank `0001→0017`, `0016→0017` backfill, app-role
  DDL 거부, pool budget, reference seed와 concurrency cases를 통과했고 cluster/DB/port를
  정리했다.

### 3.5 결과 폴더와 확장자

- 기준 계약은 [`storage-folder-and-file-contract.md`](storage-folder-and-file-contract.md)다.
- 서버는 `SIMDASH_IMPORT_ROOT` 아래 상대 경로만 읽는다.
- `manifest.json`의 ID와 DB의 Project/Request/LoadCase 관계를 확인한다.
- 확장자뿐 아니라 MIME, magic bytes, 크기, checksum, symlink와 traversal을 검사한다.
- 허용 media extension 전체(`.png`, `.jpg`, `.jpeg`, `.webp`, `.svg`, `.mp4`, `.webm`, `.glb`, `.gltf`)는 `backend/tests/test_media_policy_fixtures.py`의 generated minimal `tmp_path` fixture로 정상 signature, MIME/extension mismatch, corrupt signature를 검증한다. 영구 canonical folder example은 JSON/CSV/SVG/glTF만 유지한다.
- 예제 파일은 README 전용 장식이 아니라 실제 parser/import 통합 테스트로 읽는다.
- 같은 bundle을 다시 처리했을 때의 `SKIPPED`, 새 Run, replace 정책을 명시한다.
- local/default `SIMDASH_IMPORT_READINESS_POLICY=legacy`는 marker 없는 호환 bundle을
  읽고, Rocky profile은 `required`로 valid `.simdashboard-ready.json` v1 bundle만 읽는다.
- producer는 source bundle과 final import root를 분리하고
  `backend/scripts/publish_result_bundle.py --source-bundle … --import-root … --publication-id …`
  로 publish한다. app service는 import root에 write 권한을 받지 않는다.
- publication은 sibling staging, payload `fsync`, marker last, no-replace rename, parent
  `fsync` 순서다. WSL/Rocky ext4/XFS 외 플랫폼, `EXDEV`, unsupported rename은
  fail-closed이며 NFS/SMB는 mount probe/승인 전까지 사용하지 않는다.
- readiness/immutable publication 경계는 `services/canonical_result_bundle.py`, secure
  capture는 `services/bundle_snapshot.py`, source→final publication은
  `services/result_bundle_publisher.py`와 `scripts/publish_result_bundle.py`가 담당한다.

수동 결과 upload는 형식을 나누어 검토한다. `SUMMARY_RESULT` JSON/CSV와
`Radioss` mesh CSV는
정규화 adapter와 공통 UoW를 사용하고, `source_name`은
`{load_case_id}/{filename}`으로 target을 포함한다. content checksum으로 동일
재시도를 `SKIPPED`하며 write transaction 안에서 권한을 재확인하고 audit event를
함께 기록한다. Radioss adapter는 scalar, time-series/curve, `result_locations`를
하나의 UoW transaction으로 저장한다. schema와 맞지 않아 동작하지 않던 구형
`result_files` persistence service/repository는 제거했다. legacy manifest schema,
`ManifestParser` alias와 normalized parser adapter만 compatibility 전용으로 남기며,
운영 결과 쓰기는 모두 공통 UoW를 통한다.

### 3.6 Proxy와 배포

개발 브라우저는 `/api`, `/assets` 상대 URL만 사용하고 Vite가 `VITE_API_TARGET`으로 전달한다. 운영은 nginx가 같은 경로를 FastAPI loopback으로 전달한다.

```text
개발: Browser → Vite :5173 → FastAPI :8000
운영: Browser → HTTPS nginx :443 → FastAPI 127.0.0.1:8000
```

회사 outbound proxy(`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`)와 애플리케이션 reverse proxy는 다른 개념이다. [#15](https://github.com/wgcha/simdashboard/issues/15)의 Linux 사내 proxy·조직 CA 요구사항은 다음 절차로 적용한다.

1. site proxy input은 `http://168.219.61.252:8080`이고, `HTTP_PROXY`/`HTTPS_PROXY`에만 적용한다. 인증정보는 없다.
2. #15 bypass source scope는 loopback/localhost, `10.*`, `165.213.*`, `168.219.*`, `202.20.*`, `112.107.220.*`, `samsung.net`이다. wildcard 표현은 consumer별 지원 문법이 다르므로 그대로 복사하지 않고 `NO_PROXY`에 맞게 정규화·검증한다.
3. 사이트 제공 `DigitalCity.crt`는 Git에 넣지 않는다. source Debian 경로는 `/usr/share/ca-certificates/samsung/DigitalCity.crt`이고 `dpkg-reconfigure`/`update-ca-certificates` 절차를 기록한다. 이슈의 `/etc/ssl/cert` 언급으로 수동 복제하지 않으며 Rocky target의 trust-store/갱신 절차는 별도 구현 대상이다.
4. 대상 Rocky host에서 DNF, Python/Node package retrieval, systemd runtime 각각의 proxy/CA trust를 preflight한다.
5. proxy URL의 인증정보와 CA private key를 로그·문서·명령행에 남기지 않는다.

현재 `.env.example`과 Windows `setup.ps1`은 proxy 입력 형식과 로그 마스킹을 제공한다. Rocky 8의 proxy/CA 설치·갱신 자동화는 아직 없으므로, #15는 구현 완료가 아니라 배포 P1 요구사항이다.

## 4. 검증 명령

### 빠른 변경 검증

```bash
cd backend
../.venv-wsl/bin/python -m pytest -q <관련 테스트 파일>

cd ../frontend
pnpm run check:architecture
pnpm run test:api
pnpm run build
```

### 전체 기준선

```bash
cd backend
../.venv-wsl/bin/python -m pytest -q

cd ../frontend
pnpm run check:architecture
pnpm run test:architecture
pnpm run test:preferences
pnpm run test:routing
pnpm run test:api
pnpm run build
pnpm run test:e2e
```

전용으로 migration한 PostgreSQL test DB가 준비된 경우에만 동시성 test를 별도로
실행한다. 이 명령은 개발·운영 DB에 사용하지 않는다.

```bash
cd backend
ANALYSIS_DB_BACKEND=postgresql \
ANALYSIS_TEST_POSTGRES=1 \
ANALYSIS_TEST_POSTGRES_DATABASE=<dedicated_test_db> \
DATABASE_URL='postgresql+psycopg://<test_app_role>:<test_password>@<test_host>:5432/<dedicated_test_db>' \
../.venv-wsl/bin/python -m pytest -q tests/test_postgres_result_ingestion_concurrency.py
```

명령의 `DATABASE_URL`은 반드시 전용 test DB를 가리켜야 한다. `.env`에 설정된
현재 `simulation_dashboard` 연결은 prod-like 대상으로 간주하며 이 test에 사용하지 않는다.

Playwright Chromium을 처음 준비할 때는 `./scripts/wsl/setup-e2e.sh --install-system-deps`를 사용한다.

DuckDB 통합 테스트는 session seed를 복사해 테스트별 `test.duckdb`를 사용하고,
각 테스트 종료 시 해당 DB와 WAL만 즉시 제거한다. session seed와 실패 진단
파일은 보존하므로 전체 suite가 `/tmp` 용량을 누적 소모하지 않는다.

## 5. 변경 유형별 필수 검증

| 변경 | 최소 검증 |
|---|---|
| 순수 domain policy | unit test |
| router/API schema | API test + OpenAPI 생성 후 diff 확인 |
| repository/SQL | DuckDB integration + PostgreSQL opt-in test |
| Alembic migration | blank/upgrade PostgreSQL + app-role preflight |
| 인증·권한 | 허용/거부/project-scope test + 감사 이벤트 |
| route·navigation | routing self-test + 관련 Playwright |
| 결과 parser/import | canonical folder + SUMMARY_RESULT JSON/CSV + Radioss mesh CSV location + 잘못된 확장자/MIME/path + idempotency + auth/audit transaction |
| 미디어 | magic/MIME/크기 + DB blob + Range/HEAD/download |
| 배포 template | template validation + bundle check + 실제 target smoke |

## 6. 완료 정의

- 요청한 사용자 흐름이 실제 데이터로 동작한다.
- 실패·빈 데이터·권한 거부가 정의된 응답과 화면을 가진다.
- 새 구조 부채가 architecture ceiling을 올리지 않는다.
- 생성 OpenAPI 계약과 frontend adapter가 일치한다.
- migration과 rollback 제한이 문서화되었다.
- 관련 예제와 자동 테스트가 통과한다.
- 현재 기준 문서와 GitHub Issue 추적 정보가 갱신되었다.
