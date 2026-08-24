# 개발 작업 절차

- 기준일: 2026-08-24
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
- 빈 PostgreSQL과 기존 revision PostgreSQL에서 upgrade를 검증한다.
- app 역할의 DDL 거부와 owner/app 자격 증명 분리를 유지한다.
- DuckDB와 PostgreSQL에서 placeholder, JSON, upsert, transaction 동작을 모두 테스트한다.

### 3.5 결과 폴더와 확장자

- 기준 계약은 [`storage-folder-and-file-contract.md`](storage-folder-and-file-contract.md)다.
- 서버는 `SIMDASH_IMPORT_ROOT` 아래 상대 경로만 읽는다.
- `manifest.json`의 ID와 DB의 Project/Request/LoadCase 관계를 확인한다.
- 확장자뿐 아니라 MIME, magic bytes, 크기, checksum, symlink와 traversal을 검사한다.
- 예제 파일은 README 전용 장식이 아니라 실제 parser/import 통합 테스트로 읽는다.
- 같은 bundle을 다시 처리했을 때의 `SKIPPED`, 새 Run, replace 정책을 명시한다.

### 3.6 Proxy와 배포

개발 브라우저는 `/api`, `/assets` 상대 URL만 사용하고 Vite가 `VITE_API_TARGET`으로 전달한다. 운영은 nginx가 같은 경로를 FastAPI loopback으로 전달한다.

```text
개발: Browser → Vite :5173 → FastAPI :8000
운영: Browser → HTTPS nginx :443 → FastAPI 127.0.0.1:8000
```

회사 outbound proxy(`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`)와 애플리케이션 reverse proxy는 다른 개념이다. 인증정보를 Git이나 로그에 남기지 않는다.

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

Playwright Chromium을 처음 준비할 때는 `./scripts/wsl/setup-e2e.sh --install-system-deps`를 사용한다.

## 5. 변경 유형별 필수 검증

| 변경 | 최소 검증 |
|---|---|
| 순수 domain policy | unit test |
| router/API schema | API test + OpenAPI 생성 후 diff 확인 |
| repository/SQL | DuckDB integration + PostgreSQL opt-in test |
| Alembic migration | blank/upgrade PostgreSQL + app-role preflight |
| 인증·권한 | 허용/거부/project-scope test + 감사 이벤트 |
| route·navigation | routing self-test + 관련 Playwright |
| 결과 parser/import | 정상 예제 + 잘못된 확장자/MIME/path + idempotency |
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
