# 사내 PC Git 업데이트

대상 브랜치는 `codex/windows-one-click-deploy`다. 사내 프록시·인증서는 기존 [Windows 배치 안내](windows-one-click-deployment.md)의 설정을 유지한다.

## 이미 Git으로 받은 경우

실행 중인 앱을 `stop.bat`으로 종료하고 소스 폴더에서 PowerShell을 연다. 업데이트 전 `.env`와 현재 DB를 별도 보관한다. DuckDB 파일은 앱이 종료된 상태에서 복사한다. 외부 SPDM 저장 폴더는 소스 폴더와 구분해 유지한다.

```powershell
git status --short
git branch --show-current
git pull --ff-only origin codex/windows-one-click-deploy
.\deploy.bat
.\start.bat
```

위 명령은 현재 브랜치가 `codex/windows-one-click-deploy`일 때 사용한다. 다른 브랜치라면 수정 파일을 먼저 보존하고 다음 명령으로 전환한다.

```powershell
git fetch origin
git switch codex/windows-one-click-deploy
git pull --ff-only
```

`git status`에 직접 수정한 파일이 있거나 `pull --ff-only`가 중단되면 강제 덮어쓰기·reset·clean을 하지 않는다. 변경 파일과 오류 내용을 확인한 후 업데이트한다. `deploy.bat`은 필요한 런타임·의존성을 준비하고, 이후 평상시 실행은 `start.bat`만 사용한다.

기본 DuckDB는 시작 시 추가 테이블을 준비한다. PostgreSQL을 사용하는 설치는 앱을 다시 시작하기 전에 새 `0020_spdm_storage` migration을 적용해야 한다. `deploy.bat`은 PostgreSQL migration을 대신 실행하지 않는다. 기존 owner 설정을 갖춘 소스 폴더에서 아래 명시 업데이트를 실행한 후 `start.bat`을 실행한다. owner 설정이 없는 경우 [PostgreSQL 운영 절차](backend-sql-integration-guide.md)의 자격 증명·대상 확인 절차를 먼저 따른다.

```powershell
& .\.venv-runtime\Scripts\python.exe backend\scripts\upgrade_postgres_schema.py
```

웹 주소는 기본 설정에서 `http://127.0.0.1:5173`이다. 실행기가 다른 포트를 안내하면 실행기에 표시된 `127.0.0.1` 주소를 사용한다.

## ZIP으로 다운로드했던 경우

ZIP에는 Git 이력이 없어 `git pull`을 바로 사용할 수 없다. 기존 폴더는 보존하고 다른 이름의 폴더로 최초 한 번 clone한다. 비공개 저장소이므로 접근 권한이 있는 GitHub 계정으로 인증해야 한다.

```powershell
git clone --branch codex/windows-one-click-deploy https://github.com/wgcha/simdashboard.git simdashboard-git
cd simdashboard-git
```

기존 앱을 종료한 뒤 `.env`를 새 소스 폴더에 복사한다. 기존 DuckDB 기본 파일을 썼다면 파일을 새 폴더의 `backend/data/`로 복사하거나 `.env`의 `ANALYSIS_DUCKDB_PATH`를 기존 DB의 절대 경로로 지정한다. 두 앱을 같은 DB에 동시에 실행하지 않는다. 별도의 PostgreSQL이나 SPDM 저장소는 기존 연결 설정을 유지한다.

```powershell
.\deploy.bat
.\start.bat
```

다음 업데이트부터는 이 새 clone 폴더에서 위 `git pull` 절차를 사용한다. 프록시 주소·인증 정보를 Git에 커밋하지 않는다.

## 적용 확인

1. 기존 프로젝트·의뢰가 남아 있는지 확인한다.
2. 저장 폴더 설정에서 사내 SPDM 최상위 경로를 지정한다.
3. 기존 의뢰를 재사용할 경우 해당 하중 경우에 SPDM 상대 폴더를 먼저 연결한다. 폴더에서 새 의뢰를 만드는 경우에는 저장 폴더 새로고침을 바로 실행한다.
4. 결과 등록에서 하중 경우의 저장 경로를 확인하고 시험 파일을 등록한다.
5. 탐색기에서 실제 파일 저장 위치를 확인한 뒤 다시 새로고침하여 중복 Run이 생기지 않는지 확인한다.
