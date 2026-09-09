# native Windows 개발환경

소스 ZIP을 받아 설치·실행할 때는 [Windows 배치 안내](windows-one-click-deployment.md)의 `deploy.bat` → `start.bat`를 사용한다. 이 문서는 기존 환경 보존, 개발 검증 및 PostgreSQL 연결을 위한 상세 절차다.

- 기준일: 2026-09-07
- 상태: 현재 코드와 Windows 호환성 프로필 기준

이 문서는 WSL에서 복사한 작업 트리를 native Windows PowerShell에서 확인하고 개발하는 절차다. Windows는 개발·호환성 검증 프로필이며 Rocky Linux 8.6+와 PostgreSQL 18.x가 canonical 운영 프로필이다. 운영 배포는 [`deploy/rocky8/README.md`](../deploy/rocky8/README.md)를 따른다.

## 먼저 보존할 것

복사 직후 저장소 루트에서 기존 변경을 확인한다.

```powershell
Set-Location E:\simulation_workbench
git status --short
```

미커밋 변경, `.env`, `.postgres-owner.env`, `backend\data\*.duckdb`, transfer bundle과 출력물은 사용자 작업으로 취급한다. `git reset`, `git checkout`, `git clean`, 무조건적인 `git stash`, `.env` 삭제, DB 파일 삭제·초기화 명령을 이 절차에 포함하지 않는다. 설치 전 필요한 경우 별도 백업을 만들고, 기존 WSL 서버를 먼저 중지한다. WSL과 Windows가 같은 DuckDB 파일을 동시에 열지 않는다.

복사 과정에서 생긴 `Zone.Identifier` 메타데이터와 `.test-import-snapshots-*`, `output` 생성물은 코드나 DB 이관 입력이 아니다. 파일을 삭제하지 않고 Git에서만 제외한다.


## 고정 런타임

저장소가 고정한 버전은 Node.js `v22.23.2`, Python `3.12.13`, pnpm `11.15.1`이다. 설치기는 저장소의 `.tools` 런타임과 `.venv-runtime`을 우선 사용하고, bootstrap 단계에서 checksum과 정확한 버전을 확인한다.

```powershell
.\scripts\windows\bootstrap-runtime.ps1
Import-Module .\scripts\windows\Runtime.psm1
Set-ProjectNodePath
.\.tools\node-v22.23.2-win-x64\node.exe --version
.\.tools\python\cpython-3.12.13-windows-x86_64-none\python.exe --version
.\.tools\pnpm.cmd --version
```

`bootstrap-runtime.ps1`이 없는 이전 작업 트리에서는 고정 버전을 별도로 준비한다. `py` 별칭이나 전역 Node/pnpm이 다른 버전을 가리키면 사용하지 않는다.

## DuckDB 로컬 개발

기존 `.env`를 보존한 상태에서 처음 한 번만 의존성을 설치한다. `setup.ps1`은 Python 가상환경, backend 패키지, frontend 패키지를 준비하고 frontend build를 확인하며 기본적으로 DB를 변경하지 않는다.

```powershell
.\setup.ps1
```

`.env`가 없을 때만 `.env.example`을 복사한 뒤 DB 설정을 확인한다. 이미 `.env`가 있으면 통째로 덮어쓰지 말고 현재 DB profile과 경로를 읽어 결정한다. 기존 `.env`가 PostgreSQL을 가리키면 실행 시 `-DatabaseBackend duckdb -DuckdbPath <절대경로>`로 프로세스 범위에서만 DuckDB를 선택할 수 있다.

```powershell
.\start.ps1 -DatabaseBackend duckdb -DuckdbPath 'E:\simulation_workbench\backend\data\windows-development.duckdb'
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-WebRequest http://127.0.0.1:5173 -UseBasicParsing
.\stop.ps1
```

정상 health 응답의 `database_backend`가 `duckdb`인지 확인한다. 로그는 `backend\uvicorn.log`, `backend\uvicorn-error.log`, `frontend\vite.log`, `frontend\vite-error.log`에 남는다. Master Result Refresh는 native Windows에서 현재 POSIX snapshot 계약 때문에 fail-closed하며, 수동 upload와 나머지 compatibility 기능은 계속 검증할 수 있다.

## PostgreSQL을 사용할 때

기존 PostgreSQL을 그대로 사용할 때는 `.env`의 `ANALYSIS_DB_BACKEND=postgresql`과 `DATABASE_URL`을 보존하고, 프로세스 override 없이 실행한다.

```powershell
.\setup.ps1
.\start.ps1 -DatabaseBackend postgresql
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

`start.ps1 -DatabaseBackend postgresql`은 기존 PostgreSQL 연결과 read-only/rollback-only probe만 확인하며 자동 migration을 실행하지 않는다. schema 변경이 승인된 경우에만 별도로 `.\.venv-runtime\Scripts\python.exe backend\scripts\upgrade_postgres_schema.py`를 실행한다. WSL PostgreSQL 또는 다른 PC의 데이터를 실제로 옮길 때는 [`postgresql-pc-transfer-guide.md`](postgresql-pc-transfer-guide.md)의 명시적인 export/import 절차를 사용한다. 오래된 transfer bundle을 그대로 복원하지 말고, 번들 revision과 현재 code head를 먼저 확인한다. DuckDB 파일을 PostgreSQL로 바꾸는 경우에는 [`backend-sql-integration-guide.md`](backend-sql-integration-guide.md)의 migration·backup·행 수 검증을 먼저 적용한다.

현재 native Windows 실행을 위해 PostgreSQL 데이터가 반드시 필요한 것은 아니다. `.env`가 PostgreSQL을 가리키더라도 `-DatabaseBackend duckdb -DuckdbPath <절대경로>`를 주면 기존 DB를 건드리지 않고 별도 DuckDB 파일로 로컬 기능을 검증할 수 있다. WSL DB를 Windows PostgreSQL로 옮기는 경우에만 별도 dump/transfer가 필요하다.

## 검증 명령

```powershell
$previousDbBackend = $env:ANALYSIS_DB_BACKEND
$previousPythonPath = $env:PYTHONPATH
$previousDuckdbPath = $env:ANALYSIS_DUCKDB_PATH
$previousTestPostgres = $env:ANALYSIS_TEST_POSTGRES
$previousTestPostgresDatabase = $env:ANALYSIS_TEST_POSTGRES_DATABASE
$env:ANALYSIS_DB_BACKEND = 'duckdb'
$env:ANALYSIS_DUCKDB_PATH = Join-Path $PWD 'output\windows-smoke.duckdb'
Remove-Item Env:ANALYSIS_TEST_POSTGRES -ErrorAction SilentlyContinue
Remove-Item Env:ANALYSIS_TEST_POSTGRES_DATABASE -ErrorAction SilentlyContinue
Push-Location backend
try {
  $env:PYTHONPATH = (Get-Location).Path
  & ..\.venv-runtime\Scripts\python.exe -m pytest -q tests/test_system_health_slice.py tests/test_database_bootstrap_boundary.py tests/test_deployment_profile.py
} finally {
  Pop-Location
  if ($null -eq $previousPythonPath) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue } else { $env:PYTHONPATH = $previousPythonPath }
  if ($null -eq $previousDbBackend) { Remove-Item Env:ANALYSIS_DB_BACKEND -ErrorAction SilentlyContinue } else { $env:ANALYSIS_DB_BACKEND = $previousDbBackend }
  if ($null -eq $previousDuckdbPath) { Remove-Item Env:ANALYSIS_DUCKDB_PATH -ErrorAction SilentlyContinue } else { $env:ANALYSIS_DUCKDB_PATH = $previousDuckdbPath }
  if ($null -eq $previousTestPostgres) { Remove-Item Env:ANALYSIS_TEST_POSTGRES -ErrorAction SilentlyContinue } else { $env:ANALYSIS_TEST_POSTGRES = $previousTestPostgres }
  if ($null -eq $previousTestPostgresDatabase) { Remove-Item Env:ANALYSIS_TEST_POSTGRES_DATABASE -ErrorAction SilentlyContinue } else { $env:ANALYSIS_TEST_POSTGRES_DATABASE = $previousTestPostgresDatabase }
}
& .\.tools\pnpm.cmd --dir frontend run check:architecture
& .\.tools\pnpm.cmd --dir frontend run test:architecture
& .\.tools\pnpm.cmd --dir frontend run test:preferences
& .\.tools\pnpm.cmd --dir frontend run test:routing
& .\.tools\pnpm.cmd --dir frontend run test:api
& .\.tools\pnpm.cmd --dir frontend run build
```

이 명령은 native DuckDB 개발 검증용이다. PostgreSQL 동시성 테스트는 개발·운영 DB가 아닌 별도 disposable test DB와 `ANALYSIS_TEST_POSTGRES=1`, `ANALYSIS_TEST_POSTGRES_DATABASE`를 명시할 때만 실행한다. 이 문서는 native Windows 전체 suite가 CI/POSIX release gate를 통과했다는 뜻으로 사용하지 않는다.

브라우저 E2E가 필요하면 실행 중인 서버를 먼저 `stop.ps1`로 종료한 뒤 프로젝트 브라우저 경로에 Chromium을 준비한다.

```powershell
$projectRoot = (Get-Location).Path
$previousBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectRoot '.tools\playwright'
try {
& .\.tools\node-v22.23.2-win-x64\node.exe frontend\node_modules\@playwright\test\cli.js install chromium
& .\.tools\pnpm.cmd --dir frontend run test:e2e
} finally {
  if ($null -eq $previousBrowserPath) { Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue } else { $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowserPath }
}
```

테스트가 만든 `frontend\test-results`, `.test-import-snapshots-*`, `output`은 결과 검토 후에도 삭제하지 말고 Git에서 제외한다.

경로·환경변수·DB profile을 바꾼 작업은 [`development-workflow.md`](development-workflow.md)와 [`backend-sql-integration-guide.md`](backend-sql-integration-guide.md)를 함께 갱신한다. WSL 절차는 [`wsl-development-setup.md`](wsl-development-setup.md)에 남겨두며, native Windows와 Rocky 운영 명령을 서로 대체해서 사용하지 않는다.
