# PostgreSQL 백엔드 전환·데이터 이전·프런트 호환 구현 사양서

문서 버전: 1.2

대상 프로젝트: Analysis Canvas / `simdashboard`

현재 데이터베이스: 파일 기반 DuckDB

목표 데이터베이스: PostgreSQL 15 이상

대상 운영체제: Windows 10/11·Windows Server 및 Linux(x86_64, systemd 계열 우선)

## 0. 반드시 먼저 읽을 내용

이 문서는 **현재 코드에서 즉시 실행할 수 있는 설치 설명서가 아니라, 다른 PC의 AI 개발 에이전트가 PostgreSQL 지원을 실제로 구현하기 위한 사양서**다.

현재 `backend/app/database.py`의 `connect()`는 `ANALYSIS_DB_BACKEND=postgresql`일 때 의도적으로 `RuntimeError`를 발생시킨다. 또한 현재 `backend/requirements.txt`에는 PostgreSQL 드라이버와 Alembic이 없다. 따라서 아래 환경변수만 설정해서는 연결되지 않는다.

다른 PC의 AI는 이 문서의 “필수 구현 산출물”을 먼저 완성하고 테스트한 다음 “운영 전환 명령”을 실행해야 한다.

## 1. 최종 목표

다음 명령만으로 동일한 FastAPI와 React 프런트엔드가 PostgreSQL을 사용하도록 만든다.

```powershell
$env:ANALYSIS_DB_BACKEND = "postgresql"
$env:DATABASE_URL = "postgresql+psycopg://simdashboard_app:비밀번호@localhost:5432/simdashboard"
$env:DB_SSLMODE = "prefer"
```

완료 후에는 다음 조건을 모두 만족해야 한다.

1. DuckDB와 PostgreSQL 중 하나를 환경변수로 선택한다.
2. FastAPI 엔드포인트와 JSON 응답 형식은 바뀌지 않는다.
3. React 프런트엔드 코드는 데이터베이스 종류를 알 필요가 없다.
4. 기존 DuckDB 데이터를 손실 없이 PostgreSQL로 복사한다.
5. 대시보드 레이아웃 JSON과 변수 바인딩이 그대로 유지된다.
6. PPT 보고서 레이아웃 JSON과 버전 이력도 그대로 유지된다.
7. 변수 카탈로그 CRUD가 PostgreSQL 트랜잭션으로 동작한다.
8. 마이그레이션 전후 테이블별 행 수와 핵심 키를 자동 검증한다.
9. 문제가 있으면 환경변수만 DuckDB로 되돌려 롤백할 수 있다.
10. 동일한 Python 소스와 migration이 Windows와 Linux에서 동작한다.

## 2. 변경하면 안 되는 외부 계약

### 프런트엔드 API 계약

아래 API 경로, HTTP 메서드, 필드 이름과 상태 코드를 유지한다.

- `GET /api/projects`
- `POST /api/projects`
- `GET/POST /api/projects/{project_id}/requests`
- `GET/POST /api/requests/{request_id}/load-cases`
- `GET /api/load-cases/{load_case_id}/overview`
- `POST /api/load-cases/{load_case_id}/results/import`
- `GET/POST /api/load-cases/{load_case_id}/variables`
- `PUT/DELETE /api/load-cases/{load_case_id}/variables/{variable_key}`
- `GET/PUT /api/dashboards/{dashboard_id}`
- 대시보드 복제·버전·복구 API
- `GET/POST/PUT/DELETE /api/report-layouts...`
- `GET/POST/DELETE /api/report-templates...` 및 템플릿 렌더링 API
- 운영 KPI, 워크플로, 임계값, 자동화 템플릿 API

프런트엔드는 계속 상대 경로 `/api/...`만 호출한다. PostgreSQL 주소, 계정 또는 SQL은 프런트엔드에 노출하지 않는다.

### 변수 연결 계약

```text
variable_definitions.variable_key
    = scalar_results.variable_key 또는 time_series_results.variable_key
    = dashboards.definition_json.widgets[].settings.variableId
```

`variable_key`와 `data_type`은 생성 후 불변이다. 표시 이름, 설명, 단위, 기준값, 허용 위젯과 집계는 수정할 수 있다. 변수 삭제는 물리 삭제가 아니라 `is_active=false`다.

### 폴더 스키마·결과 SQL·변수 카탈로그·화면의 역할

권장 데이터 흐름은 다음과 같다.

```text
해석 결과 폴더
  → import_schemas.definition_json의 파일 패턴·열 매핑 검증
  → scalar_results / time_series_results / curve_results / media_assets 적재
  → variable_definitions에서 같은 variable_key의 의미·단위·기준·허용 표현 조회
  → dashboards.definition_json.widgets[].settings.variableId로 화면 배치
  → report_layouts.definition_json.variablePlacements[].variableKey로 PPT 배치
```

중요한 구분:

- 폴더 스키마는 어떤 파일의 어떤 열을 어떤 결과 테이블과 `variable_key`로 읽을지 선언한다.
- 결과 테이블은 실제 측정값·해석값을 저장한다.
- 변수 카탈로그는 실제 값을 복제 저장하지 않는다. `variable_key`의 표시·검증·판정·허용 위젯 계약을 저장한다.
- 변수를 카탈로그에 선언했다고 대시보드에 자동으로 카드가 생기지는 않는다. 사용자가 대시보드 편집에서 위젯에 바인딩하거나, 사전에 정의된 레이아웃이 해당 키를 참조해야 한다.
- 선언은 되어 있지만 결과 데이터가 아직 없으면 `데이터 대기` 상태로 표시하며 0이나 추정값을 만들지 않는다.
- 폴더 적재 시 알 수 없는 `variable_key`는 기본적으로 검증 경고 또는 오류로 처리한다. 운영 정책에 따라 검토 대기 카탈로그 항목을 생성할 수 있으나 자동 활성화는 하지 않는다.
- PostgreSQL 연결 정보와 SQL 실행은 백엔드에만 둔다. 프런트엔드는 카탈로그와 결과 API만 사용한다.

### 대시보드 JSON 계약

PostgreSQL에서는 `definition_json`을 JSONB로 저장하지만 API에서는 현재와 동일한 JSON 객체를 반환해야 한다. JSON 문자열을 이중 인코딩하면 안 된다.

## 3. 현재 코드에서 확인할 파일

다른 PC의 AI는 수정 전에 다음 파일을 전부 읽고 현재 구현과 이 문서를 대조한다.

- `backend/app/config.py`
- `backend/app/database.py`
- `backend/app/main.py`
- `backend/app/repositories/*.py`
- `backend/app/repositories/variable_catalog.py`
- `backend/app/result_import.py`
- `backend/tests/test_api.py`
- `backend/tests/test_data_quality.py`
- `backend/requirements.txt`
- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/src/App.tsx`

현재 DuckDB 종속 요소:

- `import duckdb`
- `?` 위치 파라미터
- `INSERT OR IGNORE`
- 시작 시 직접 실행하는 `CREATE TABLE IF NOT EXISTS`
- DuckDB 연결 객체의 컨텍스트 관리자와 `cursor.description`
- JSON 값을 문자열로 저장하고 `json_value()`로 복원하는 부분

## 4. 목표 백엔드 구조

권장 구현은 SQLAlchemy 2.x Core + psycopg 3 + Alembic이다. ORM으로 전체 도메인을 재작성할 필요는 없다.

```text
backend/app/
├─ config.py
├─ db/
│  ├─ __init__.py
│  ├─ protocol.py
│  ├─ duckdb_adapter.py
│  ├─ postgres_adapter.py
│  ├─ session.py
│  └─ types.py
├─ repositories/
│  ├─ variable_catalog.py
│  └─ ...
├─ scripts/
│  ├─ migrate_duckdb_to_postgres.py
│  ├─ verify_postgres_migration.py
│  └─ seed_postgres_sample.py
└─ main.py

backend/alembic.ini
backend/alembic/
├─ env.py
└─ versions/
   └─ 0001_initial_postgresql_schema.py
```

### 데이터베이스 프로토콜

API와 비즈니스 로직은 구체적인 DuckDB 또는 psycopg 연결 객체를 직접 알지 않아야 한다. 최소 프로토콜은 다음 기능을 제공한다.

```python
class DatabaseConnection(Protocol):
    def execute(self, statement: str, params: Mapping[str, Any] | None = None) -> Result: ...
    def executemany(self, statement: str, params: Sequence[Mapping[str, Any]]) -> Result: ...
    def begin(self) -> ContextManager[None]: ...
    def close(self) -> None: ...
```

신규 또는 변경 SQL은 SQLAlchemy `text()`와 이름 기반 바인딩(`:load_case_id`)을 사용한다. 사용자 입력을 문자열 연결이나 f-string으로 SQL에 넣지 않는다. 테이블명을 동적으로 선택해야 하면 서버의 고정 allowlist만 사용한다.

### 연결 팩토리

```python
def database_session() -> ContextManager[DatabaseConnection]:
    settings = database_settings()
    if settings.backend == "duckdb":
        return DuckDbAdapter(settings.duckdb_path)
    return PostgresAdapter(settings.database_url, sslmode=settings.db_sslmode)
```

PostgreSQL 엔진은 앱 프로세스당 한 번 만들고 요청마다 새 트랜잭션/연결을 체크아웃한다. 권장 풀 설정은 다음과 같다.

- `pool_pre_ping=True`
- `pool_size=5`
- `max_overflow=10`
- `pool_recycle=1800`

이 값은 환경변수로 덮어쓸 수 있게 한다.

## 5. 추가 Python 패키지

`backend/requirements.txt` 또는 별도 운영 requirements에 버전을 고정한다.

```text
SQLAlchemy>=2.0,<2.1
psycopg[binary]>=3.2,<3.3
alembic>=1.14,<2
```

DuckDB 테스트를 유지하기 위해 기존 `duckdb` 의존성을 제거하지 않는다.

설치 명령:

Windows PowerShell:

```powershell
cd <프로젝트경로>\backend
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Linux bash:

```bash
cd <project-path>/backend
../.venv/bin/python -m pip install -r requirements.txt
```

## 6. 환경변수 사양

`backend/app/config.py`에 다음 설정을 추가한다.

| 환경변수 | 필수 여부 | 기본값 | 의미 |
|---|---:|---|---|
| `ANALYSIS_DB_BACKEND` | 선택 | `duckdb` | `duckdb` 또는 `postgresql` |
| `ANALYSIS_DUCKDB_PATH` | DuckDB | 기존 경로 | 원본/개발 DuckDB 파일 |
| `DATABASE_URL` | PostgreSQL 필수 | 없음 | SQLAlchemy psycopg URL |
| `DB_SSLMODE` | 선택 | `prefer` | 로컬 `prefer`, 운영 `require` 이상 |
| `DB_POOL_SIZE` | 선택 | `5` | 기본 연결 풀 |
| `DB_MAX_OVERFLOW` | 선택 | `10` | 추가 연결 수 |
| `DB_STATEMENT_TIMEOUT_MS` | 선택 | `30000` | 쿼리 제한 시간 |
| `AUTO_SEED_SAMPLE_DATA` | 선택 | `false` | PostgreSQL 샘플 자동 생성 금지 |
| `CORS_ALLOWED_ORIGINS` | 운영 필수 | localhost | 쉼표로 구분한 프런트 주소 |

`ANALYSIS_DB_BACKEND=postgresql`인데 `DATABASE_URL`이 없으면 앱 시작 즉시 이해하기 쉬운 오류를 발생시킨다. 비밀번호나 전체 URL을 로그에 출력하지 않는다.

예시 `.env.example`에는 실제 비밀번호를 넣지 않는다.

```dotenv
ANALYSIS_DB_BACKEND=postgresql
DATABASE_URL=postgresql+psycopg://simdashboard_app:CHANGE_ME@localhost:5432/simdashboard
DB_SSLMODE=prefer
AUTO_SEED_SAMPLE_DATA=false
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

## 6.1 Windows·Linux 공통 호환 사양

백엔드 Python 코드, Alembic migration, 데이터 이전 CLI와 검증 CLI는 운영체제별로 분기하지 않고 동일 소스를 사용해야 한다.

### 경로 처리

- 코드에서 `E:\...`, 드라이브 문자, 역슬래시를 하드코딩하지 않는다.
- 모든 로컬 경로는 `pathlib.Path`로 처리한다.
- CLI는 Windows 절대 경로와 Linux 절대 경로를 모두 받는다.
- 파일명 대소문자가 Linux에서 구분된다는 점을 테스트한다.
- 애셋 경로를 DB에 저장할 때 가능하면 프로젝트/데이터 루트 기준 상대 경로와 POSIX 구분자(`/`)를 사용한다.
- DB에 저장된 Windows 전용 절대 경로는 이전 시 자동 변경하지 말고 보고서에 `path_warning`으로 기록한다.
- UTF-8 파일명과 한국어 데이터를 보존한다.

### 가상환경

Windows:

```powershell
cd <프로젝트경로>
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
```

Linux:

```bash
cd <project-path>
python3.12 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r ./backend/requirements.txt
```

Linux에서 `venv` 모듈이 없다면 배포판 패키지 관리자에서 `python3-venv` 또는 해당 Python 버전의 venv 패키지를 설치한다.

### 환경변수

Windows PowerShell:

```powershell
$env:ANALYSIS_DB_BACKEND = "postgresql"
$env:DATABASE_URL = "postgresql+psycopg://simdashboard_app:비밀번호@localhost:5432/simdashboard"
$env:DB_SSLMODE = "prefer"
```

Linux bash:

```bash
export ANALYSIS_DB_BACKEND=postgresql
export DATABASE_URL='postgresql+psycopg://simdashboard_app:비밀번호@localhost:5432/simdashboard'
export DB_SSLMODE=prefer
```

운영 환경에서는 셸 history에 비밀번호가 남지 않도록 root만 읽을 수 있는 EnvironmentFile, systemd credential 또는 조직의 secret manager를 사용한다.

### 실행 스크립트

기존 `start.ps1`과 `stop.ps1`은 Windows용이다. PostgreSQL 전환 작업은 다음 Linux 파일도 제공해야 한다.

- `start.sh`
- `stop.sh`
- 선택: `deploy/simdashboard-api.service`

`start.sh`는 CRLF가 아니라 LF로 저장하고 실행 권한을 갖는다.

```bash
chmod +x start.sh stop.sh
./start.sh
```

스크립트는 `python`, `node`, `pnpm`이 PATH에 있다고 무조건 가정하지 말고 프로젝트 가상환경과 확인된 실행 파일을 사용한다. Windows와 Linux 모두 백엔드/프런트 PID 또는 서비스 상태를 추적하고 중복 실행을 방지한다.

### Linux systemd 예시

실제 사용자와 경로로 바꾼다.

```ini
[Unit]
Description=Simulation Dashboard FastAPI
After=network.target postgresql.service

[Service]
Type=simple
User=simdashboard
Group=simdashboard
WorkingDirectory=/opt/simdashboard/backend
EnvironmentFile=/etc/simdashboard/backend.env
ExecStart=/opt/simdashboard/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

프런트엔드는 개발 서버를 운영에 그대로 사용하지 않는다. `npm/pnpm run build` 결과를 Nginx 등으로 제공하고 `/api`를 FastAPI로 reverse proxy한다.

### OS별 검증 의무

- 최소 CI 행렬: `windows-latest`, `ubuntu-latest`
- Python 버전은 양쪽에서 동일하게 고정한다.
- DuckDB 회귀와 프런트 빌드를 양쪽 OS에서 실행한다.
- PostgreSQL 통합 테스트는 적어도 Ubuntu PostgreSQL service container에서 실행한다.
- Windows PostgreSQL 실제 설치 PC에서도 migration과 smoke test를 한 번 수행한다.
- 줄바꿈, 파일 권한, 경로 구분자 차이로 테스트를 건너뛰지 않는다.

## 7. PostgreSQL 데이터베이스 준비

다음은 PostgreSQL이 설치된 PC의 관리 계정으로 한 번만 실행한다. 실제 비밀번호는 조직 정책에 맞게 별도로 주입한다.

Windows 서비스 확인 예시:

```powershell
Get-Service *postgres*
psql --version
```

Linux 서비스 확인 예시:

```bash
sudo systemctl status postgresql
psql --version
sudo -u postgres psql
```

```sql
CREATE ROLE simdashboard_owner LOGIN PASSWORD 'CHANGE_ME_OWNER';
CREATE ROLE simdashboard_app LOGIN PASSWORD 'CHANGE_ME_APP';
CREATE DATABASE simdashboard OWNER simdashboard_owner ENCODING 'UTF8';
```

DB에 접속한 뒤:

```sql
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO simdashboard_app;
```

Alembic은 `simdashboard_owner`, 실행 앱은 `simdashboard_app`을 사용하는 것을 권장한다. 마이그레이션 완료 후 앱 역할에는 필요한 CRUD 권한만 부여한다.

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO simdashboard_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO simdashboard_app;
ALTER DEFAULT PRIVILEGES FOR ROLE simdashboard_owner IN SCHEMA public
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO simdashboard_app;
```

운영 환경에서는 `sslmode=require`, 가능하면 `verify-full`과 CA 인증서를 사용한다.

## 8. PostgreSQL 스키마 사양

Alembic `0001` 마이그레이션은 현재 DuckDB의 다음 25개 테이블을 모두 생성해야 한다.

1. `projects`
2. `product_information`
3. `analysis_requests`
4. `request_steps`
5. `load_cases`
6. `template_executions`
7. `analysis_runs`
8. `scalar_results`
9. `time_series_results`
10. `curve_results`
11. `curve_points`
12. `result_locations`
13. `qualitative_notes`
14. `media_assets`
15. `folder_import_jobs`
16. `import_schemas`
17. `import_schema_versions`
18. `validations`
19. `quality_thresholds`
20. `variable_definitions`
21. `dashboards`
22. `dashboard_versions`
23. `report_layouts`
24. `report_layout_versions`
25. `report_template_assets`

### 형식 변환

| DuckDB | PostgreSQL |
|---|---|
| `VARCHAR` | `TEXT` 또는 길이 제한 `VARCHAR(n)` |
| `BIGINT` | `BIGINT` |
| `INTEGER` | `INTEGER` |
| `DOUBLE` | `DOUBLE PRECISION` |
| `BOOLEAN` | `BOOLEAN` |
| `TIMESTAMP` | `TIMESTAMPTZ` |
| `JSON` | `JSONB` |

현재 DuckDB의 시간값은 UTC로 간주하고 PostgreSQL 이전 시 UTC timezone을 붙인다. 프런트 API의 ISO 문자열 표현은 기존과 호환되게 유지한다.

`report_template_assets.definition_json`도 JSONB로 이전한다. 실제 `.pptx` 바이트는 DB에 넣지 않고 서버의 관리형 자산 저장소에 유지하며, `file_path`는 자산 루트 기준 상대 경로만 저장한다. 다중 서버 운영에서는 같은 계약을 S3 호환 객체 저장소 어댑터로 교체하고, 이전 도구가 템플릿 파일의 존재·크기·SHA-256을 함께 검증해야 한다.

### 필수 키와 제약

- 현재 `PRIMARY KEY`는 모두 유지한다.
- `dashboard_versions`는 `(dashboard_id, version)` 복합 PK를 유지한다.
- `report_layout_versions`는 `(layout_id, version)` 복합 PK를 유지한다.
- `import_schema_versions`는 `(schema_id, version)` 복합 PK를 유지한다.
- `curve_points`는 `(curve_id, point_index)` 복합 PK를 유지한다.
- `variable_definitions`는 `(load_case_id, variable_key)` UNIQUE를 유지한다.
- `time_series_results`에는 `(analysis_run_id, variable_key, time_value)` UNIQUE를 추가한다.
- `result_locations`에는 `(analysis_run_id, variable_key)` UNIQUE를 추가한다.
- 상태·판정 필드는 기존 데이터가 모두 이전된 후 CHECK 제약을 추가한다.
- 초기 이전에서 `quality_thresholds.criterion_key`의 전역 PK 의미를 임의로 바꾸지 않는다. 프로젝트별 복합 키 전환은 별도 마이그레이션으로 수행한다.

외래키는 데이터 사전 검증이 통과한 후 추가한다. 운영 데이터의 우발적 연쇄 삭제를 피하기 위해 기본 삭제 정책은 `ON DELETE RESTRICT`로 한다.

### 필수 인덱스

```sql
CREATE INDEX ix_requests_project_requested
ON analysis_requests(project_id, requested_at DESC);

CREATE INDEX ix_load_cases_request
ON load_cases(request_id, created_at);

CREATE INDEX ix_analysis_runs_load_case_run
ON analysis_runs(load_case_id, run_no DESC);

CREATE INDEX ix_scalar_results_run_variable
ON scalar_results(analysis_run_id, variable_key);

CREATE INDEX ix_time_series_run_variable_time
ON time_series_results(analysis_run_id, variable_key, time_value);

CREATE INDEX ix_variable_definitions_active
ON variable_definitions(load_case_id, is_active);

CREATE UNIQUE INDEX uq_variable_definitions_key
ON variable_definitions(load_case_id, variable_key);

CREATE INDEX ix_dashboards_scope
ON dashboards(project_id, request_id, load_case_id);

CREATE INDEX ix_dashboard_versions_dashboard
ON dashboard_versions(dashboard_id, version DESC);

CREATE INDEX ix_report_layout_versions_layout
ON report_layout_versions(layout_id, version DESC);

CREATE INDEX ix_report_templates_active
ON report_template_assets(is_active, updated_at DESC);
```

대시보드 JSONB 내부 검색이 실제 병목으로 확인되기 전에는 GIN 인덱스를 추가하지 않는다.

## 9. DuckDB SQL 호환 변경

다른 PC의 AI는 전체 백엔드에서 다음을 검색하고 PostgreSQL에서도 동작하도록 수정한다.

```powershell
rg -n "INSERT OR IGNORE|\?|duckdb|CREATE TABLE|BEGIN TRANSACTION|JSON" backend\app
```

변환 원칙:

- `INSERT OR IGNORE` → `INSERT ... ON CONFLICT (...) DO NOTHING`
- `?` → SQLAlchemy 이름 기반 바인딩
- `BEGIN TRANSACTION/COMMIT/ROLLBACK` 문자열 실행 → `with connection.begin():`
- JSON 문자열 → PostgreSQL에서는 Python dict/list를 JSONB로 바인딩
- PostgreSQL JSONB 조회 결과가 dict/list이면 다시 `json.loads()`하지 않는다.
- `greatest`, `coalesce`, CASE 식은 양쪽 DB에서 동일 결과인지 테스트한다.
- `now()` 대신 애플리케이션 UTC 또는 `CURRENT_TIMESTAMP` 중 하나로 통일한다.

`rows()`는 DuckDB의 `cursor.description`뿐 아니라 SQLAlchemy `Result.mappings()`도 처리하도록 어댑터 내부로 이동한다.

## 10. 초기화와 샘플 데이터 정책

현재 `initialize_database()`는 DuckDB DDL과 샘플 데이터를 함께 처리한다. PostgreSQL에서는 이를 분리한다.

- 스키마 생성/변경: Alembic만 담당
- 운영 앱 시작: 현재 Alembic revision 확인과 연결 상태 확인만 수행
- 샘플 데이터: `AUTO_SEED_SAMPLE_DATA=true`이면서 DB가 비어 있을 때만 별도 스크립트로 생성
- 운영 기본값: `AUTO_SEED_SAMPLE_DATA=false`
- 기존 DuckDB의 `ensure_sample_evolutions()`와 `ensure_variable_definitions()`는 PostgreSQL 앱 시작마다 실행하지 않는다.

변수 정의 백필은 데이터 이전 스크립트가 담당한다. 이미 존재하는 `variable_definitions` 행을 우선 보존하고 누락된 결과 변수만 `ON CONFLICT DO NOTHING`으로 보충한다.

## 11. DuckDB → PostgreSQL 데이터 이전 도구 사양

다음 CLI 모듈을 구현한다.

```powershell
python -m app.scripts.migrate_duckdb_to_postgres `
  --source "E:\simulation_dashboard\backend\data\analysis_dashboard.duckdb" `
  --target-env DATABASE_URL `
  --dry-run
```

실제 실행:

Windows PowerShell:

```powershell
python -m app.scripts.migrate_duckdb_to_postgres `
  --source "E:\simulation_dashboard\backend\data\analysis_dashboard.duckdb" `
  --target-env DATABASE_URL `
  --require-empty-target `
  --batch-size 5000 `
  --report "migration-report.json"
```

Linux bash:

```bash
python -m app.scripts.migrate_duckdb_to_postgres \
  --source "/var/lib/simdashboard/analysis_dashboard.duckdb" \
  --target-env DATABASE_URL \
  --require-empty-target \
  --batch-size 5000 \
  --report "migration-report.json"
```

### CLI 안전 조건

- 기본값은 `--dry-run` 또는 사용자 확인 없는 비파괴 모드다.
- `--require-empty-target`은 대상 테이블에 데이터가 있으면 중단한다.
- `--truncate-target`은 별도 명시 없이는 제공하거나 사용하지 않는다.
- 원본 DuckDB를 읽기 전용으로 연다.
- 비밀번호와 전체 URL을 로그/보고서에 남기지 않는다.
- 하나의 테이블 실패 시 해당 실행을 실패로 표시하고 불완전 전환을 금지한다.
- 대용량 시계열은 `fetchmany(batch_size)`와 PostgreSQL batch insert/COPY를 사용한다.
- 각 테이블의 시작·완료 행 수, 소요 시간, 오류를 JSON 보고서에 기록한다.

### 복사 순서

외래키 의존성을 고려해 다음 순서를 사용한다.

```text
projects
product_information
analysis_requests
request_steps
load_cases
template_executions
analysis_runs
scalar_results
time_series_results
curve_results
curve_points
result_locations
qualitative_notes
media_assets
folder_import_jobs
import_schemas
import_schema_versions
quality_thresholds
variable_definitions
dashboards
dashboard_versions
report_layouts
report_layout_versions
report_template_assets
validations
```

### 값 변환

- DuckDB JSON 문자열은 `json.loads()` 후 JSONB로 넣는다.
- 빈 JSON과 SQL NULL을 구분한다.
- naive timestamp는 UTC로 간주해 timezone-aware 값으로 변환한다.
- `NaN`, `Infinity`, `-Infinity`는 이전 전에 오류로 보고한다.
- 불리언을 0/1 문자열로 변환하지 않는다.
- UTF-8 한국어를 그대로 보존한다.
- PK와 업무 ID 문자열을 새로 생성하지 않고 원본 그대로 복사한다.

### 재실행 정책

첫 운영 이전은 빈 PostgreSQL DB에 실행한다. 재실행이 필요하면 실패한 DB를 삭제 후 Alembic으로 다시 만들거나, 별도 검증된 `--resume` 체크포인트 기능을 구현한다. 무조건적인 upsert로 오류를 숨기지 않는다.

## 12. 이전 전 데이터 품질 검사

이전 스크립트는 복사 전에 최소 다음을 검사한다.

- 고아 `analysis_requests.project_id`
- 고아 `load_cases.request_id`
- 고아 `analysis_runs.load_case_id`
- 고아 결과의 `analysis_run_id`
- 중복 `(load_case_id, variable_key)`
- 중복 `(analysis_run_id, variable_key, time_value)`
- 대시보드 JSON 파싱 실패
- 위젯 `variableId`가 비활성 또는 없는 변수인지
- 숫자 결과의 NULL 단위/기준/판정
- 지원하지 않는 미디어 경로와 형식
- 비정상 시간값과 NaN/Infinity

검사 실패 시 기본 동작은 이전 중단이다. `--allow-warnings`는 치명적 오류를 무시하는 옵션으로 사용하지 않는다.

## 13. 이전 후 자동 검증

`verify_postgres_migration.py`는 DuckDB와 PostgreSQL을 동시에 읽어 다음을 비교한다.

1. 25개 테이블별 행 수
2. 모든 PK 또는 복합 PK 집합
3. 프로젝트별 의뢰·하중 경우·해석 실행 수
4. 실행별 scalar/time-series 행 수
5. 변수 정의의 활성 수와 키 집합
6. 대시보드별 버전 수
7. `definition_json` 정규화 후 SHA-256
8. KPI API 결과
9. Open Cell 및 Chassis Rear 기준 샘플의 전체 판정

부동소수점 값은 기본 허용오차 `1e-9`, 화면 지표는 기존 반올림 규칙을 사용한다.

검증 보고서 예시:

```json
{
  "status": "PASS",
  "source": "duckdb",
  "target": "postgresql",
  "tables": {
    "projects": {"source": 2, "target": 2, "match": true}
  },
  "dashboard_hashes_match": true,
  "api_smoke_tests": "PASS"
}
```

모든 검증이 통과하기 전에는 운영 프런트를 PostgreSQL 백엔드에 연결하지 않는다.

## 14. 프런트엔드 연결 유지 사양

프런트엔드는 DB에 직접 연결하지 않는다. 다음 원칙만 지키면 코드 변경이 없어야 한다.

- Vite 개발 프록시 또는 운영 리버스 프록시는 계속 `/api`를 FastAPI로 전달한다.
- FastAPI 응답의 snake_case 필드와 타입을 유지한다.
- PostgreSQL `Decimal`, UUID, datetime, JSONB는 Pydantic이 현재 JSON 형식으로 직렬화하도록 변환한다.
- `TIMESTAMPTZ` 응답은 ISO 8601이며 프런트의 `new Date(...)`에서 해석 가능해야 한다.
- JSONB는 문자열이 아니라 객체/배열로 응답한다.
- 오류 응답은 기존 `{ "detail": ... }` 형태를 유지한다.
- CORS 허용 주소는 환경변수로 지정한다.

필수 프런트 회귀 흐름:

1. 프로젝트·의뢰·하중 경우 선택
2. Open Cell 및 Chassis Rear 결과 표시
3. 변수 생성·수정·비활성화
4. 위젯 변수 목록에 신규 변수 표시
5. 레이아웃 저장 후 새로고침 복원
6. 대시보드 복제와 버전 복구
7. CSV 사전 검증과 실제 가져오기
8. 운영 KPI 필터와 CSV 내보내기

## 15. 트랜잭션과 동시성

PostgreSQL에서는 다음 작업을 각각 하나의 트랜잭션으로 묶는다.

- 해석 결과 가져오기: run + scalar + time-series + location + note + 상태 갱신
- 대시보드 저장: 현재 정의 갱신 + 버전 행 추가
- 대시보드 복구: 버전 읽기 + 현재 정의 갱신 + 새 버전 추가
- 변수 비활성화: 대시보드 참조 재확인 + 상태 갱신
- 임계값 변경: 기준값 + 관련 결과 판정 재계산

대시보드 저장은 현재 `version`을 조건으로 사용하는 낙관적 잠금으로 개선한다.

```sql
UPDATE dashboards
SET definition_json=:definition, version=:next_version, updated_at=:updated_at
WHERE id=:id AND version=:expected_version;
```

영향 행이 0이면 HTTP 409를 반환하고 사용자가 최신 버전을 다시 불러오게 한다.

## 16. 백업·컷오버·롤백

### 컷오버 전

1. 앱 쓰기 작업을 잠시 중단한다.
2. DuckDB 파일을 복사해 읽기 전용 백업한다.
3. `pg_dump`가 가능한지 확인한다.
4. Alembic으로 빈 PostgreSQL 스키마를 생성한다.
5. dry-run과 데이터 품질 검사를 통과한다.

### 컷오버

1. DuckDB → PostgreSQL 실제 복사를 실행한다.
2. 자동 검증 보고서가 PASS인지 확인한다.
3. PostgreSQL 백엔드를 별도 포트에서 시작한다.
4. API smoke test와 브라우저 회귀 검증을 수행한다.
5. 운영 환경변수를 PostgreSQL로 변경한다.

### 롤백

쓰기 재개 전에 문제가 발견되면:

```powershell
$env:ANALYSIS_DB_BACKEND = "duckdb"
$env:ANALYSIS_DUCKDB_PATH = "<백업 DuckDB 경로>"
```

PostgreSQL 전환 후 새 데이터가 입력된 상태에서 DuckDB로 단순 롤백하면 새 데이터가 손실된다. 그 경우 역방향 추출 도구 또는 점검된 운영 복구 계획이 필요하다. MVP 전환에서는 dual-write를 구현하지 않는다.

PostgreSQL 백업 예시:

Windows PowerShell 또는 Linux bash에서 동일한 `pg_dump`/`pg_restore` 프로그램을 사용할 수 있다.

```powershell
pg_dump --format=custom --file simdashboard-before-change.dump simdashboard
pg_restore --clean --if-exists --dbname simdashboard simdashboard-before-change.dump
```

## 17. 보안 요구사항

- DB 비밀번호, `DATABASE_URL`, dump 파일을 Git에 커밋하지 않는다.
- 앱 계정과 스키마 소유자 계정을 분리한다.
- 운영에서는 최소 권한과 TLS를 사용한다.
- SQL 로그에서 파라미터와 연결 문자열을 마스킹한다.
- 외부 접속이 필요하면 PostgreSQL을 인터넷에 직접 공개하지 않고 방화벽/VPN/사설망을 사용한다.
- 백업 파일을 민감 데이터로 취급한다.
- 자연어 대시보드 기능으로 임의 SQL을 실행하지 않는다.

## 18. 필수 구현 산출물

다른 PC의 AI는 최소 다음 파일과 변경을 만들어야 한다.

- PostgreSQL/duckdb 공통 연결 프로토콜과 어댑터
- PostgreSQL 설정 및 연결 풀
- Alembic 설정과 전체 초기 스키마 migration
- 저장소 쿼리의 이름 기반 파라미터 전환
- PostgreSQL용 conflict 처리
- DuckDB→PostgreSQL 이전 CLI
- 이전 전 품질 검사
- 이전 후 비교 검증 CLI와 JSON 보고서
- PostgreSQL 전용 통합 테스트 fixture
- README의 실제 실행 명령
- `.env.example`
- 이 문서에서 “현재 미구현”으로 표시된 문구의 상태 갱신

## 19. 테스트 사양

### DuckDB 회귀

Windows PowerShell:

```powershell
cd <프로젝트경로>\backend
..\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend
npm.cmd run build
```

Linux bash:

```bash
cd <project-path>/backend
../.venv/bin/python -m pytest -q

cd ../frontend
npm run build
```

기존 테스트가 모두 통과해야 한다.

### PostgreSQL 통합 테스트

테스트 DB를 별도로 사용한다.

```powershell
$env:ANALYSIS_DB_BACKEND = "postgresql"
$env:DATABASE_URL = "postgresql+psycopg://simdashboard_test:CHANGE_ME@localhost:5432/simdashboard_test"
alembic upgrade head
python -m pytest -q -m postgresql
```

최소 테스트:

- 앱 시작과 health check
- 전체 스키마 생성
- 샘플 seed idempotency
- 프로젝트/의뢰/하중 경우 CRUD
- 변수 CRUD와 대시보드 참조 삭제 차단
- 사용자 변수 결과 가져오기
- 대시보드 저장 동시성 충돌
- JSONB 왕복과 한국어 보존
- 트랜잭션 중간 오류 시 전체 rollback
- DuckDB 이전 및 행 수/해시 검증

### API smoke test

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/projects
Invoke-RestMethod http://127.0.0.1:8000/api/portfolio/overview
```

## 20. 완료 조건

다음 항목이 모두 참일 때만 “PostgreSQL 연결 완료”라고 보고한다.

- [ ] PostgreSQL 모드에서 `connect()` 차단 코드가 제거됐다.
- [ ] 잘못된 URL/인증/SSL 오류가 명확하게 표시된다.
- [ ] Alembic이 빈 DB를 최신 revision으로 만든다.
- [ ] 25개 테이블과 필수 인덱스가 생성된다.
- [ ] DuckDB 회귀 테스트가 통과한다.
- [ ] PostgreSQL 통합 테스트가 통과한다.
- [ ] 데이터 이전 dry-run이 통과한다.
- [ ] 실제 이전 후 행 수·키·JSON 해시 검증이 통과한다.
- [ ] 변수 카탈로그 CRUD와 위젯 연결이 동작한다.
- [ ] 대시보드 저장·복제·복구가 동작한다.
- [ ] 프런트엔드 빌드와 실제 화면 검증이 통과한다.
- [ ] 환경변수를 DuckDB로 되돌렸을 때 기존 모드가 정상 동작한다.
- [ ] 비밀번호, DB URL, dump 파일이 Git에 포함되지 않았다.
- [ ] Windows와 Linux CI가 모두 통과했다.
- [ ] Linux `start.sh`/`stop.sh`와 운영 서비스 예제가 제공됐다.
- [ ] 코드와 DB 데이터에 특정 PC의 드라이브 문자나 절대 경로가 강제되지 않는다.

## 21. 다른 PC의 AI에게 그대로 전달할 작업 프롬프트

아래 프롬프트와 이 문서 파일을 함께 제공한다.

```text
이 저장소를 PostgreSQL 15+에서 실제로 실행 가능하도록 전환해줘.

반드시 docs/backend-sql-integration-guide.md 전체를 먼저 읽고 그 문서를 구현 계약으로 사용해. 현재 코드는 PostgreSQL 환경변수만 설정해서는 동작하지 않으며 backend/app/database.py에서 PostgreSQL 연결을 차단하고 있다.

작업 순서:
1. 저장소 전체와 현재 DuckDB 스키마, API 테스트, 프런트 API 타입을 조사한다.
2. 기존 DuckDB 동작을 보존하면서 PostgreSQL 어댑터, SQLAlchemy Core 기반 공통 실행 계층, psycopg 연결 풀을 구현한다.
3. Alembic 초기 마이그레이션으로 문서에 명시된 25개 테이블, 제약과 인덱스를 만든다.
4. DuckDB 전용 SQL과 위치 파라미터를 안전한 이름 기반 바인딩과 PostgreSQL conflict 문법으로 전환한다.
5. 원본 DuckDB를 읽기 전용으로 열어 PostgreSQL로 복사하는 dry-run/실행 CLI를 구현한다.
6. 테이블별 행 수, PK, 대시보드 JSON 해시, 변수 키, 핵심 API를 비교하는 검증 보고서를 만든다.
7. FastAPI의 기존 URL, JSON 필드, 상태 코드를 유지하고 React 프런트엔드를 수정하지 않거나 최소 변경으로 유지한다.
8. DuckDB 회귀 테스트, PostgreSQL 통합 테스트, 프런트 빌드와 실제 브라우저 검증을 Windows와 Linux 기준으로 수행한다.
9. README와 .env.example을 실제 실행 가능한 명령으로 갱신한다.

안전 규칙:
- 원본 DuckDB 파일을 수정하거나 삭제하지 않는다.
- 대상 PostgreSQL이 비어 있지 않으면 기본적으로 중단한다.
- 비밀번호와 DATABASE_URL을 코드, 로그, 보고서, Git에 기록하지 않는다.
- 테스트가 통과하기 전에 운영 전환 완료라고 보고하지 않는다.
- 프런트엔드가 SQL이나 DB 자격 증명을 직접 알게 만들지 않는다.
- Windows 전용 경로와 PowerShell만 구현하지 말고 Linux bash/systemd 실행도 제공한다.
- 모호한 부분은 기존 API 테스트와 이 문서의 외부 계약을 우선한다.

먼저 현재 상태, 구현 계획, 필요한 PostgreSQL 접속 정보만 짧게 보고한 뒤 안전한 범위에서 구현을 계속해. PostgreSQL 계정 생성이나 실제 데이터 복사처럼 외부 상태를 변경하기 직전에만 승인 요청을 해.

완료 보고에는 다음을 포함해:
- 변경 파일과 아키텍처
- Alembic revision
- dry-run 및 실제 이전 보고서 경로
- 이전 전후 테이블 행 수 비교
- DuckDB/PostgreSQL 테스트 결과
- 프런트 빌드와 화면 검증 결과
- 실제 실행 명령
- 롤백 명령
- 남은 위험과 운영 전 확인 사항
```

## 22. 다른 PC에서 AI에게 제공해야 할 정보

비밀번호는 채팅에 직접 붙이지 말고 해당 PC의 환경변수 또는 비밀 저장소에 설정한다. AI에는 다음 정보만 전달한다.

- 저장소 로컬 경로
- 이 문서 경로
- PostgreSQL 버전
- host, port, database 이름
- migration owner 역할과 runtime app 역할의 이름
- SSL 요구 수준
- 원본 DuckDB 절대 경로
- 샘플만 이전할지 실제 데이터를 이전할지
- 허용된 서비스 중단 시간
- PostgreSQL 대상 DB가 비어 있는지 여부

이 정보가 없으면 AI는 코드·migration·dry-run 도구까지 구현할 수 있지만 실제 데이터 이전과 운영 컷오버는 진행하면 안 된다.
