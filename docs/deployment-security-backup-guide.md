# 외부 배포 보안·권한·감사·백업 가이드

## 1. 배포 전제

- 외부 배포는 PostgreSQL과 `AUTH_MODE=password`를 사용한다.
- FastAPI는 HTTPS 리버스 프록시 뒤에서 실행하고 `AUTH_COOKIE_SECURE=true`로 설정한다.
- 관리자, DB 소유자, 앱 역할의 비밀번호는 서로 다르게 만들고 `.env`를 Git에 추가하지 않는다.
- `simdashboard_owner`는 Alembic·복구에만, `simdashboard_app`은 평상시 API에만 사용한다.

## 2. PostgreSQL 최초 구축과 DuckDB 이관

PowerShell 예시에서 값은 실제 비밀로 교체한다.

```powershell
$env:POSTGRES_ADMIN_URL='postgresql://postgres:ADMIN_PASSWORD@127.0.0.1:5432/postgres'
$env:SIM_DASH_OWNER_PASSWORD='OWNER_PASSWORD'
$env:SIM_DASH_APP_PASSWORD='APP_PASSWORD'
$env:DATABASE_URL='postgresql+psycopg://simdashboard_owner:OWNER_PASSWORD@127.0.0.1:5432/simulation_dashboard'
$env:POSTGRES_BIN='E:\PostgreSQL\18\bin'

.\scripts\postgres\setup-postgres.ps1 -MigrateSource '.\backend\data\analysis_dashboard.duckdb'
```

이 절차는 역할·DB 생성, Alembic 적용, 감사 로그 append-only 권한, 데이터 복사, 테이블별 행 수와 체크섬 검증을 수행한다. 서비스 시작 전 `DATABASE_URL`을 `simdashboard_app` 계정 URL로 변경한다.

### 2.1 안전한 PostgreSQL 시작과 추가 migration

최초 구축을 마친 PC에서 `start-postgresql.bat` 또는 PostgreSQL이 설정된 `start.bat`을 실행하면 시작 전 다음 절차를 수행한다.

1. 앱 계정으로 `alembic_version`과 코드의 단일 head를 비교한다.
2. 이미 head이면 `.postgres-owner.env`를 읽지 않고 앱 권한 및 핵심 테이블 접근을 확인한다.
3. 알려진 이전 revision일 때만 보호된 `.postgres-owner.env`의 `POSTGRES_OWNER_URL`을 읽는다.
4. 앱 URL과 owner URL의 host·port·database가 같고 owner 역할과 DB/스키마 소유권이 올바른지 확인한다.
5. owner advisory lock을 보유한 상태에서 revision을 다시 읽은 후 아직 pending일 때만 Alembic child process를 실행한다.
6. migration 후 앱 계정으로 head, 신규 테이블 CRUD 기본 권한, `initialize_database`를 다시 검증한다.
7. API와 프런트엔드가 지정 포트에서 실제로 응답한 뒤에만 시작 성공과 PID 파일을 기록한다.

일반 시작은 빈 DB의 최초 구축, 역할 생성·비밀번호 변경, DuckDB 복사를 수행하지 않는다. `alembic_version`이 없거나 revision이 코드 계보와 다르면 최초 구축/복구 절차를 확인하도록 중단한다. 앱 계정에는 DB·스키마 `CREATE` 권한을 부여하지 않는다. owner 파일의 ACL을 완화하거나 owner URL을 `.env`, 콘솔, 로그에 복사하지 않는다.

## 3. 인증과 역할

```powershell
$env:ANALYSIS_DB_BACKEND='postgresql'
$env:DATABASE_URL='postgresql+psycopg://simdashboard_app:APP_PASSWORD@127.0.0.1:5432/simulation_dashboard'
$env:AUTH_MODE='password'
$env:AUTH_SECRET_KEY='32자 이상의 암호학적 난수'
$env:AUTH_TOKEN_TTL_MINUTES='480'
$env:AUTH_COOKIE_SECURE='true'
$env:CORS_ALLOWED_ORIGINS='https://dashboard.example.com'
```

| 역할 | 허용 범위 |
|---|---|
| `viewer` | 조회, 차트·보고서 데이터 열람 |
| `editor` | 조회와 일반 레이아웃·워크플로·검토 변경 |
| `admin` | 변수·품질기준·Import 스키마·사용자 권한·삭제 |

최초 관리자는 비밀번호를 명령 인자로 넘기지 않고 생성한다.

```powershell
$env:SIM_DASH_USER_PASSWORD='12자 이상의 초기 비밀번호'
.\.venv-runtime\Scripts\python.exe .\backend\scripts\create_user.py `
  --username admin --display-name '시스템 관리자' --role admin
Remove-Item Env:SIM_DASH_USER_PASSWORD
```

로그인 토큰은 HMAC 서명되고, 브라우저에서는 HttpOnly SameSite 쿠키도 사용한다. 계정을 비활성화하면 아직 만료되지 않은 토큰도 다음 요청에서 거절된다.

## 4. 감사 로그

- 인증 실패, 로그인 성공, 권한 거절, 모든 API 변경 요청을 `audit_events`에 기록한다.
- 요청 본문과 비밀번호는 기록하지 않는다.
- 각 응답의 `X-Request-Id`와 감사 이벤트의 `request_id`로 장애를 추적한다.
- PostgreSQL 앱 역할에는 `audit_events`의 `SELECT`, `INSERT`만 허용하고 UPDATE·DELETE·TRUNCATE를 회수한다.
- 관리자만 `GET /api/audit-events`로 최근 이벤트를 조회할 수 있다.

## 5. 백업

```powershell
$env:DATABASE_URL='postgresql+psycopg://simdashboard_app:APP_PASSWORD@127.0.0.1:5432/simulation_dashboard'
$env:POSTGRES_BIN='E:\PostgreSQL\18\bin'
.\scripts\postgres\backup-postgres.ps1 -OutputDir 'D:\simulation-backups'
```

백업 도구는 PostgreSQL custom-format dump를 만든 뒤 `pg_restore --list`로 구조를 검증하고 SHA-256 manifest를 함께 기록한다. DB 비밀번호는 프로세스 인자나 manifest에 기록하지 않는다.

권장 운영 주기는 일 1회, 배포 직전 1회이며 백업 파일과 manifest를 DB 서버와 다른 저장소에 함께 보관한다.

## 6. 복구

기본 복구는 빈 DB만 허용한다. 대상 DB명이 `--confirm-database`와 정확히 일치해야 한다.

```powershell
$env:DATABASE_URL='postgresql+psycopg://simdashboard_owner:OWNER_PASSWORD@127.0.0.1:5432/simulation_dashboard_restore'
.\scripts\postgres\restore-postgres.ps1 `
  -Backup 'D:\simulation-backups\analysis-canvas-YYYYMMDDTHHMMSSZ.dump' `
  -ConfirmDatabase 'simulation_dashboard_restore'
```

기존 DB를 덮어쓰는 복구는 사전 백업 후에만 `-Clean`을 명시한다. 복구 후 다음을 확인한다.

1. `alembic_version`이 최신 revision인지 확인한다.
2. 프로젝트·Run·결과·사용자·감사 이벤트 행 수를 원본과 비교한다.
3. `harden_postgres_privileges.py`를 다시 실행한다.
4. Viewer 로그인, 관리자 변경, 감사 이벤트 기록을 smoke test한다.

## 7. 배포 차단 조건

- `AUTH_MODE=disabled`
- 기본 또는 공유 비밀번호 사용
- `AUTH_SECRET_KEY` 32자 미만
- HTTPS 없이 `AUTH_COOKIE_SECURE=true`를 사용할 수 없는 상태
- 백업 생성과 빈 DB 복구를 한 번도 시험하지 않은 상태
- PostgreSQL·DuckDB 회귀 테스트 또는 인증 E2E 실패
