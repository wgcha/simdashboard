# Windows PostgreSQL 빠른 시작

- 기준일: 2026-09-09
- 대상: Git으로 받은 기존 Windows 배포 폴더를 PostgreSQL 기본 실행으로 사용하는 관리자
- 범위: 로컬 `127.0.0.1` 실행. 다중 사용자 상시 운영은 [Rocky 배포 안내](../deploy/rocky8/README.md)를 따른다.

신규 설치와 DB 종류 미지정 실행은 PostgreSQL을 기본으로 사용한다. 일반 Git 업데이트는 기존 `.env`, `.postgres-owner.env`, DuckDB 파일과 PostgreSQL 데이터를 보존한다. 기존 DuckDB를 PostgreSQL로 전환하려면 아래 별도 이관 절차를 따른다. DB 종류만 바꿔 기존 계정이 없는 새 DB에 연결하지 않는다.

## 이미 PostgreSQL로 이관한 배포: 평소 실행

이관을 이미 마쳤다면 `.env`의 `ANALYSIS_DB_BACKEND=postgresql`과 기존 `DATABASE_URL`을 유지한다. `setup-postgresql.ps1`을 다시 실행할 필요가 없다.

```powershell
.\stop.bat
.\start.bat
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

평소에는 `start.bat` 하나만 실행하면 된다. `stop.bat`은 실행 중인 앱을 정리하거나 업데이트 직전에만 사용한다. 아직 현재 코드의 schema 변경을 적용하지 않았다면 `start.bat`보다 먼저 `update.bat`를 실행한다. health 응답의 `database_backend`가 `postgresql`이면 현재 앱이 PostgreSQL을 사용 중임을 확인할 수 있다.

```json
{"status":"ok","database_backend":"postgresql"}
```

이 health 응답은 DB 사용 상태만 보여 주며 데이터 이관 완료를 증명하지 않는다. 실제 이관 실행이 성공했고 콘솔에 `copied and verified`와 `checksum_differences=0`이 표시되는지 확인한다. 저장한 manifest JSON에서는 `differences`가 빈 배열이고, `tables`와 `target_tables`의 테이블별 `count`·`checksum`이 일치해야 한다. dry-run 결과만으로 이관 완료라고 판단하지 않는다.

## 준비

1. 배포 폴더 전체를 Git으로 받는다. 이미 ZIP으로 설치한 폴더를 Git 업데이트 대상으로 연결하려면 [Windows Git 업데이트 안내](windows-git-update.md)를 먼저 따른다. 이후 평소 업데이트는 배포 폴더의 `update.bat`를 더블클릭한다.
2. PostgreSQL을 아직 준비하지 않았다면 `setup.ps1`로 고정 런타임과 의존성만 준비한다. `deploy.bat`은 DB 준비·계정 설정까지 검사하므로 연결 설정이 없으면 완료되지 않는다. `.env`가 없으면 `.env.example`을 복사해 PostgreSQL 기본 설정을 준비한다.
3. PostgreSQL 18을 설치하고 Windows 서비스를 시작한다. PostgreSQL `bin` 폴더가 PATH에 없으면 `.env`에 `POSTGRES_BIN=E:\PostgreSQL\18\bin` 형식으로 지정한다.
4. `.env.example`을 참고해 **관리자 연결만 일시적으로** `.env`에 넣는다. `POSTGRES_ADMIN_URL` 또는 `DATABASE_URL`은 대상 서버의 `postgres` 데이터베이스에 연결하는 CREATEDB·CREATEROLE 권한 계정이어야 한다. 실제 비밀번호나 URL은 Git, 문서, 채팅에 넣지 않는다.

`setup-postgresql.ps1`은 성공 후 서비스용 `.env`를 `simdashboard_app` 연결로 바꾸고, migration 전용 owner 연결은 Git에서 제외되는 `.postgres-owner.env`에 따로 저장한다. 일반 실행은 이 owner 파일을 읽지 않는다.

## 기존 배포의 Git 업데이트

PostgreSQL을 이미 사용 중인 배포는 DB 설정을 편집하지 않고 다음만 실행한다.

```powershell
.\update.bat
```

업데이트기는 Git 다운로드, 앱 중지, `deploy.ps1`의 기존 DB 자동 백업·Alembic migration·계정 준비, 재시작 순서로 처리한다. Git의 직접 수정·미추적 소스 파일이 있으면 앱을 중지하기 전에 멈춘다. `.env`, `.postgres-owner.env`, DB, 런타임과 결과 파일은 보존한다. 백업 실패 시 DB 변경을 중단한다. migration 또는 시작 실패 시 자동 DB rollback은 하지 않으므로 오류 단계와 `log\update-*.log`를 확인한 뒤 수정하고 다시 실행한다.

## 최초 PostgreSQL 설정

기존 데이터가 없는 신규 설치는 PostgreSQL 서비스와 관리자 연결을 준비한 후 빈 DB를 생성한다. 다음 명령은 DuckDB 파일을 요구하거나 복사하지 않는다.

```powershell
.\.venv-runtime\Scripts\python.exe backend\scripts\setup_local_postgres.py --seed-mode empty
.\deploy.bat
```

DB 준비가 끝나면 배포 창에서 최초 관리자 아이디·비밀번호를 입력한다. 이후에는 `start.bat`과 `update.bat`을 사용한다.

기존 DuckDB 데이터를 이전하는 경우에만 다음 전용 진입점을 사용한다.

```powershell
.\setup-postgresql.ps1
```

이 이전 도구의 기본 seed 모드는 `duckdb`이며 애플리케이션의 PostgreSQL 기본값과는 별개다. 원본 `backend\data\analysis_dashboard.duckdb`를 읽기 전용으로 검사한 뒤, 역할·DB·Alembic schema를 만들고 데이터를 복사해 행 수와 checksum을 검증한다. 원본 DuckDB 파일은 유지된다.

대상 `simulation_dashboard`에 관리 schema가 이미 있으면 이 명령은 기존 데이터를 보호하기 위해 중단한다. 내부 설정 스크립트의 실제 `--replace-existing`과 `--backup-dir` 옵션은 검증된 backup·staging 전환을 수행하는 관리자 작업이다. 기존 데이터를 유지하려면 사용하지 않는다.

신규 설치의 빈 DB 또는 별도 기준 데이터 DB는 내부 스크립트의 seed 옵션으로 구분한다.

```powershell
.\.venv-runtime\Scripts\python.exe backend\scripts\setup_local_postgres.py --seed-mode empty
# 선택값: empty, reference, demo, duckdb(기본값)
```

설정 도중 `.setup-recovery-required.json`이 생기면 `setup.ps1`과 시작이 차단된다. 파일의 복구 안내와 보관된 backup을 확인하기 전에는 파일을 지우거나 DB 이름을 바꾸지 않는다.

## 기본 PostgreSQL 실행과 상태 확인

최초 설정이 끝났거나, 이미 PostgreSQL 연결이 설정된 배포에서는 다음 명령으로 PostgreSQL을 명시해 실행할 수도 있다.

```powershell
.\start-postgresql.ps1
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

정상 응답에는 다음 값이 포함된다.

```json
{"status":"ok","database_backend":"postgresql"}
```

웹은 `http://127.0.0.1:5173/workspace/overview`에서 열리고 API 문서는 `http://127.0.0.1:8000/docs`에 있다. `start-postgresql.ps1`은 `start.ps1 -DatabaseBackend postgresql`의 짧은 진입점이다. 앱 역할 연결, Alembic revision, 권한을 검사하지만 schema migration, 역할 생성, DuckDB 복사, 비밀번호 변경은 실행하지 않는다. pending revision이면 fail-closed하므로 `update.bat`로 업데이트를 완료하거나, 승인된 schema 변경일 때만 owner 역할로 `backend\scripts\upgrade_postgres_schema.py`를 별도 실행한다. PowerShell 프로세스에 이미 설정된 `DATABASE_URL`은 `.env`보다 우선할 수 있으므로, 다른 DB를 가리키는 환경변수가 남아 있지 않은지 확인한다.

포트를 바꾸거나 브라우저를 열지 않으려면 `start.ps1`의 실제 매개변수를 사용한다.

```powershell
.\start.ps1 -DatabaseBackend postgresql -BackendPort 18080 -FrontendPort 18173 -NoBrowser -StartupTimeoutSeconds 300
```

중지는 `stop.bat` 또는 `stop.ps1`로 한다. 시작 실패 시 `backend\uvicorn-error.log`와 `frontend\vite-error.log`를 먼저 확인한다.

## 기존 DuckDB 데이터만 별도로 이관할 때

이미 준비한 **빈** PostgreSQL schema로 기존 DuckDB를 옮기는 경우에는 최초 설정과 별개로 아래 순서를 사용한다. 먼저 `stop.bat`로 실행 중인 앱을 중지하고, 원본 DuckDB를 다른 프로세스가 쓰지 않게 한다.

1. app/owner 역할과 최신 Alembic schema를 준비한다. 이관 도구가 읽을 대상 owner `DATABASE_URL`은 실행하는 PowerShell 프로세스에만 설정한다. 빈 DB가 아니면 이관 도구는 중단하며, 대상 truncate나 병합 옵션은 제공하지 않는다.
2. `backend`에서 `--execute` 없이 dry-run을 실행한다. 원본은 read-only로 열리고, schema·관계·recovery lease를 검사한다.

```powershell
Push-Location backend
try {
  ..\.venv-runtime\Scripts\python.exe scripts\migrate_duckdb_to_postgres.py `
    --source 'D:\simulation-data\analysis_dashboard.duckdb' `
    --manifest '..\backups\duckdb-postgres-dry-run.json' `
    --json-output
} finally { Pop-Location }
```

3. dry-run이 통과하고 백업·중단 시간·대상 DB가 빈 상태임을 확인한 뒤에만 `--execute`를 추가한다. 복사와 checksum 검증은 하나의 대상 transaction으로 처리되며, 불일치나 실패는 rollback한다.

```powershell
Push-Location backend
try {
  ..\.venv-runtime\Scripts\python.exe scripts\migrate_duckdb_to_postgres.py `
    --source 'D:\simulation-data\analysis_dashboard.duckdb' `
    --batch-size 5000 `
    --manifest '..\backups\duckdb-postgres-migration.json' `
    --execute
} finally { Pop-Location }
```

4. 완료된 manifest의 table count·checksum과 health 응답을 확인한 후 `start-postgresql.ps1`으로 전환한다.

다른 PC에서 PostgreSQL 자체를 옮기는 경우에는 이 DuckDB 도구를 사용하지 말고 [PostgreSQL PC 간 이관 가이드](postgresql-pc-transfer-guide.md)의 export/import 절차를 사용한다.
