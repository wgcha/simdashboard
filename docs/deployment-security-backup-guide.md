# 외부 배포 보안·권한·감사·백업 가이드

> 운영 기준: canonical production target은 Rocky Linux 8 + nginx + systemd + PostgreSQL 18이다. 이 문서의 Windows VM 항목은 개발·DB 이관 compatibility profile의 보안 목표와 수동 preflight 절차다. HTTPS reverse proxy, Windows service, TLS binding, health rollback을 포함한 Windows one-command 운영 배포기는 아직 없다. 자동 설치가 구현된 운영 경로는 `deploy/rocky8/`이며 상세 결정은 [`adr/0004-canonical-production-deployment-target.md`](adr/0004-canonical-production-deployment-target.md)를 따른다.

## 1. 배포 전제

- Windows compatibility profile은 PostgreSQL, `DEPLOYMENT_PROFILE=windows-vm-intranet`, `AUTH_MODE=oidc`, `DIRECTORY_MODE=http`로 개발·DB 이관·사내 인증 preflight를 검증한다. 이 profile은 canonical production 배포 승인을 의미하지 않는다.
- `password`와 `local` 디렉터리는 가정용 Windows 개발·호환 테스트에만 사용하며 운영 설정 누락 시 자동 대체하지 않는다.
- Windows에서 사내 compatibility를 수동 검증할 때도 FastAPI는 HTTPS 리버스 프록시 뒤에서 실행하고 `AUTH_COOKIE_SECURE=true`로 설정한다. canonical production은 Rocky의 nginx가 TLS 경계를 담당한다.
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

## 3. Windows compatibility profile의 사내 인증과 역할

```powershell
$env:ANALYSIS_DB_BACKEND='postgresql'
$env:DATABASE_URL='postgresql+psycopg://simdashboard_app:APP_PASSWORD@127.0.0.1:5432/simulation_dashboard'
$env:DEPLOYMENT_PROFILE='windows-vm-intranet'
$env:AUTH_MODE='oidc'
$env:AUTH_SECRET_KEY='32자 이상의 암호학적 난수'
$env:AUTH_TOKEN_TTL_MINUTES='480'
$env:AUTH_COOKIE_SECURE='true'
$env:CORS_ALLOWED_ORIGINS='https://dashboard.example.com'
$env:OIDC_ISSUER_URL='https://idp.intranet.example'
$env:OIDC_CLIENT_ID='analysis-canvas'
$env:OIDC_CLIENT_SECRET='비밀 저장소에서 주입'
$env:OIDC_REDIRECT_URI='https://dashboard.example.com/api/auth/oidc/callback'
$env:DIRECTORY_MODE='http'
$env:DIRECTORY_API_BASE_URL='https://directory.intranet.example'
$env:DIRECTORY_API_TOKEN='비밀 저장소에서 주입'
```

위 `windows-vm-intranet` 설정은 호환성 검증과 DB 이관 전후 preflight를 위한
수동 profile이다. Windows 운영 자동 배포·TLS binding·service rollback은
구현 범위에 포함되지 않는다.

| 역할 | 허용 범위 |
|---|---|
| 일반 사용자 | 운영/분석 대시보드 조회, 본인 배정 업무 실행, 보고서 내보내기 |
| 파워 사용자 | 일반 기능 + 의뢰 생성·편집, 워크플로 편집, 결과 등록·검토 |
| 프로젝트 관리자 | 파워 기능 + 프로젝트 대시보드·레이아웃·기준·변수·멤버 관리 |
| 전역 관리자 | 모든 프로젝트 권한 + 계정 승인, 시스템 카탈로그, 메뉴 정책, 감사로그 |

최초 전역 관리자는 먼저 회사 SSO로 한 번 로그인해 `PENDING` 계정을 만든 다음, VM 콘솔에서 사용자 이름을 두 번 일치시켜 승인한다. 이 명령은 OIDC 계정만 허용하며 비밀번호나 토큰을 만들거나 출력하지 않는다.

```powershell
.\.venv-runtime\Scripts\python.exe .\backend\scripts\approve_oidc_global_admin.py `
  --username e12345 --confirm-user e12345 --reason '최초 운영 전역 관리자 승인'
```

OIDC Authorization Code + S256 PKCE의 state·nonce·issuer·audience·서명·만료를 검증한다. OIDC 토큰은 저장하거나 감사로그에 남기지 않고 검증 후 애플리케이션 HttpOnly SameSite=Strict 세션 쿠키만 발급한다. 계정을 중지하면 아직 만료되지 않은 세션도 다음 요청에서 거절되고 쿠키가 제거된다.

### 3.1 사내 이전 권한 preflight

권한 기능은 분석 데이터 이관과 분리된 읽기 전용 검사를 제공한다. 운영 DB를 변경하지 않으며 결과는 자동화 도구가 읽을 수 있는 JSON이다.

```powershell
$env:ANALYSIS_DB_BACKEND='postgresql'
$env:DATABASE_URL='postgresql+psycopg://simdashboard_app:APP_PASSWORD@127.0.0.1:5432/simulation_dashboard'
.\.venv-runtime\Scripts\python.exe .\backend\scripts\access_migration_preflight.py `
  | Set-Content -Encoding utf8 .\access-migration-preflight.json
if ($LASTEXITCODE -ne 0) { throw '권한 이전 preflight 차단 항목을 해결하세요.' }
```

보고서는 다음을 구분한다.

- `blockers`: 권한 스키마 누락, OIDC/사번/아이디 중복, 활성 프로젝트 관리자 누락, 메뉴 정책 상태 누락
- `warnings`: 전역관리자 승인 필요, 기존 문자열 담당자의 `owner_user_id` 재배정 필요
- `counts`: 사용자·프로젝트·멤버십·초대·메뉴 정책 버전 행 수

`status=blocked` 또는 `status=error`이면 사내 OIDC 전환과 서비스 시작을 진행하지 않는다. 원본 `owner` 문자열은 보존되며, 불명확한 담당자를 임의 계정에 연결하지 않는다.

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
4. 일반 사용자 로그인·본인 업무 실행, 파워 사용자 의뢰 편집, 프로젝트 관리자 변경, 전역관리자 메뉴 정책 변경과 감사 이벤트 기록을 smoke test한다.

## 7. canonical production 배포 차단 조건

- `AUTH_MODE`가 `password` 또는 `oidc`가 아님
- `DEPLOYMENT_PROFILE`이 `rocky8`이 아님
- 기본 또는 공유 비밀번호 사용
- `AUTH_SECRET_KEY` 32자 미만
- HTTPS 없이 `AUTH_COOKIE_SECURE=true`를 사용할 수 없는 상태
- 백업 생성과 빈 DB 복구를 한 번도 시험하지 않은 상태
- PostgreSQL·DuckDB 회귀 테스트 또는 인증 E2E 실패
