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

백업 도구는 PostgreSQL custom-format dump를 만들 때 dump와 같은 exported
repeatable-read snapshot에서 strict shared media inventory를 계산하고, `pg_restore
--list`와 archive SHA-256 manifest에 함께 기록한다. restore는 `--single-transaction`
과 `--exit-on-error`로 실행하며 `--create`를 사용하지 않아 `--clean`도 같은
transaction 경계에서 fail-closed한다. DB 비밀번호는 프로세스 인자나
manifest에 기록하지 않는다. archive SHA-256만으로는 부족하므로 restore는 manifest의
inventory를 app-role exact comparison하고 database-only verifier까지 실행한다.

권장 운영 주기는 일 1회, 배포 직전 1회이며 백업 파일과 manifest를 DB 서버와 다른 저장소에 함께 보관한다.

## 6. 복구

기본 복구는 빈 DB만 허용한다. 대상 DB명이 `--confirm-database`와 정확히 일치해야 한다.

```powershell
$env:DATABASE_URL='postgresql+psycopg://simdashboard_owner:OWNER_PASSWORD@127.0.0.1:5432/simulation_dashboard_restore'
$env:SIMDASH_APP_DATABASE_URL='postgresql+psycopg://simdashboard_app:APP_PASSWORD@127.0.0.1:5432/simulation_dashboard_restore'
.\scripts\postgres\restore-postgres.ps1 `
  -Backup 'D:\simulation-backups\analysis-canvas-YYYYMMDDTHHMMSSZ.dump' `
  -ConfirmDatabase 'simulation_dashboard_restore' `
  -AppRoleVerifyUrl $env:SIMDASH_APP_DATABASE_URL
```

```bash
# POSIX wrapper: the app-role URL is required for the same post-restore checks.
export DATABASE_URL='postgresql+psycopg://simdashboard_owner:OWNER_PASSWORD@127.0.0.1:5432/simulation_dashboard_restore'
export SIMDASH_APP_DATABASE_URL='postgresql+psycopg://simdashboard_app:APP_PASSWORD@127.0.0.1:5432/simulation_dashboard_restore'
./scripts/postgres/restore-postgres.sh \
  /var/backups/analysis-canvas-YYYYMMDDTHHMMSSZ.dump \
  simulation_dashboard_restore \
  --verify-database-url "$SIMDASH_APP_DATABASE_URL"
```

기존 DB를 덮어쓰는 복구는 사전 백업 후에만 `-Clean`을 명시한다. 복구 후 다음을 확인한다.

1. `alembic_version`이 최신 revision인지 확인한다.
2. 프로젝트·Run·결과·사용자·감사 이벤트 행 수를 원본과 비교한다.
3. restore script가 `pg_restore` 직후 owner URL로 `harden_postgres_privileges.py`를
   자동 재적용하므로, 운영자는 결과 권한을 확인한다.
4. 일반 사용자 로그인·본인 업무 실행, 파워 사용자 의뢰 편집, 프로젝트 관리자 변경, 전역관리자 메뉴 정책 변경과 감사 이벤트 기록을 smoke test한다.

### 6.1 P1-03 media inventory와 database-only 전환

`scripts/media_transfer_manifest.py`는 strict shared blob/chunk inventory와 checksum을
생성·비교하며, migration 도구는 dry-run/preflight/idempotent execute와 O_EXCL 예약형
no-overwrite `PENDING`→`COMPLETED`/`FAILED` recoverable receipt journal을 지원한다.
`backend/scripts/backup_postgres.py`의 `--label`은
경로가 아닌 bounded safe ID만 허용하며, 기존 dump·manifest 또는 symlink target을
덮어쓰지 않는다. 실패한 run의 충돌 없는 partial은 진단을 위해 보존한다. backup은 pg_dump와
같은 exported snapshot을 사용하고, `restore_postgres.py`는 app-role URL로 exact
inventory comparison과 `verify_media_database_only.py`를 실행한다. transfer bundle
v2는 blob-bound media asset을 ZIP에 중복 포함하지 않으며 v1 입력은 fail-closed한다.
이 상태는 **코드·disposable 자동검증 완료, 운영 증적 대기**다.

database-only 전환 승인은 실제 Rocky 운영 환경에서 다음 증적을 모두 남긴 뒤에만
가능하다.

1. 분리된 빈 PostgreSQL에 production backup을 restore하고, blob 수·chunk 수·총
   바이트·참조·checksum inventory가 backup 증적과 일치함을 확인한다.
2. 전체 blob/chunk checksum·길이·참조 무결성과 demo 정책을 verifier로 확인한다.
   reference demo load case(`loadcase-drop-bottom-001`)를 사용하는 DB는 exact
   allowlist 20개를, Rocky 기본 `SEED_MODE=empty` fresh production DB는 expected/
   actual 0개를 확인한다.
3. legacy source는 검증된 backup과 위 gate 통과 뒤 최소 7일 보존한다. cleanup은
   자동 작업이 아니며, migration receipt·실제 regular non-symlink backup dump·backup
   manifest·approval ID와 `--cleanup-receipt`/`--migration-id` confirmation을 포함한
   명시적 실행만 허용한다. manifest만으로는 충분하지 않다. 도구는 DB 접근이나 삭제 전에
   dump의 filename·bytes·streamed SHA-256을 manifest와 대조하고, private 0700 staging
   사본의 동일 bytes에 `pg_restore --list`를 실행해 실제 custom-format archive 구조를
   DB 접근 전에 확인한다. 이어 현재 DB inventory,
   receipt와 source checksum을 다시 확인한 뒤 삭제 evidence를 남긴다.
4. 500 MiB media와 동시 50 stream/10분 Range·seek 부하, DB volume recovery 절차를
   Rocky target에서 기록한다.

Rocky 기본 모드는 `SIMDASH_MEDIA_STORAGE_MODE=database-only`이며 설치 후와 systemd
startup에서 app-role preflight를 실행한다. 개발 기본은 `dual-read`이고 요청 시점에
mode를 해석한다. 실제 Rocky host/NFS·quota, production backup→빈 DB restore
rehearsal, 500 MiB/50 stream 부하와 5분 startup timeout 적정성, PowerShell 실실행은
외부 release gate다. 이 증적 전에는 legacy 파일 삭제나 database-only 완료 선언을 하지
않는다.

이관과 cleanup의 운영 명령은 receipt와 증적 경로를 생략하지 않는다.

```bash
PYTHONPATH=backend .venv/bin/python scripts/migrate_media_to_database.py \
  --execute --receipt /var/lib/simdashboard/evidence/migration-<ID>.json

PYTHONPATH=backend .venv/bin/python scripts/cleanup_migrated_media_files.py \
  --migration-receipt /var/lib/simdashboard/evidence/migration-<ID>.json \
  --backup /var/backups/analysis-canvas-<STAMP>.dump \
  --backup-manifest /var/backups/analysis-canvas-<STAMP>.manifest.json \
  --approval-id ops-<APPROVAL-ID> \
  --execute --confirm --migration-id <MIGRATION-ID> \
  --cleanup-receipt /var/lib/simdashboard/evidence/cleanup-<ID>.json
```

transfer bundle은 `backend/scripts/postgres_transfer.py export --output-dir
transfer-bundles`로 database dump와 media inventory를 같은 exported snapshot에서
묶어 v2를 생성하며, import는 `import BUNDLE --validate-only`로 먼저 검증한다.
blob-bound media asset은 `assets.zip`에 중복 저장하지 않고 v1 bundle은 fail-closed한다.

archive copy·SHA-256과 legacy cleanup fingerprint는 고정 크기 block으로 streaming하여
content memory를 파일 크기에 비례해 늘리지 않는다. `media_inventory()`의 aggregate
metadata 조회/list 구조 최적화는 별도 LOW 우선순위이며, 이 계약은 content payload
memory 경계만 보장한다.

## 7. canonical production 배포 차단 조건

- `AUTH_MODE`가 `password` 또는 `oidc`가 아님
- `DEPLOYMENT_PROFILE`이 `rocky8`이 아님
- 기본 또는 공유 비밀번호 사용
- `AUTH_SECRET_KEY` 32자 미만
- HTTPS 없이 `AUTH_COOKIE_SECURE=true`를 사용할 수 없는 상태
- 백업 생성과 빈 DB 복구를 한 번도 시험하지 않은 상태
- PostgreSQL·DuckDB 회귀 테스트 또는 인증 E2E 실패
