# 사내 PC 원클릭 업데이트

평소에는 **`start.bat`**, 새 버전 적용은 **`update.bat`**를 더블클릭한다.

## 지금처럼 ZIP 배포 폴더를 쓰는 경우

1. 최신 **`update.bat` 한 파일**을 기존 배포 폴더의 `stop.ps1` 옆에 넣는다.
2. **`update.bat`를 더블클릭**한다. 최초 연결 안내가 나오면 폴더·브랜치를 확인하고 Enter를 누른다.

`main` / `No commits yet` 상태도 이 흐름으로 처리한다. 새 폴더로 이사하거나 런타임·DB를 다시 설치할 필요가 없다. 같은 경로의 기존 소스 파일은 `backups/git-update-*`에 보관하고, `.env`·DB·런타임·결과 파일은 유지한다. 이어지는 배포 단계에서 기존 DB와 설정을 `backups/accounts/` 아래 별도로 백업하고 검증한다.

Git이 설치되어 있고 해당 저장소에 접근할 수 있어야 한다. 기본 저장소는 `https://github.com/wgcha/simdashboard.git`, 최초 연결 브랜치는 `codex/windows-one-click-deploy`다. Git의 기존 로그인·프록시 설정을 사용한다. 최초 Git 인증이 필요한 PC에서는 로그인 창이 추가로 나올 수 있다.

2026-09-09 업데이트에는 PC 도우미·기본 `내 PC 설정` 메뉴·원클릭 업데이트기가 포함된다. 이 변경의 게시 대상은 위 브랜치이며, `main`에서 pull하는 것만으로 이 브랜치의 새 변경을 받지는 않는다. 원격에 `update.ps1`과 Git 모듈이 없으면 다운로드 후 안내하고 종료한다. 최초 실행용 driver는 `backups/updater-driver-*`에 보관한다.

이미 커밋이 있는 정상 Git 폴더에서 이번 브랜치를 받으려면 다음을 실행한다. 직접 수정한 소스가 있으면 먼저 보존하고, 전환이 거부될 때 강제로 덮어쓰지 않는다. `No commits yet` 폴더는 위 BAT 최초 연결 절차를 사용한다.

```powershell
git fetch origin
git switch codex/windows-one-click-deploy
git pull --ff-only origin codex/windows-one-click-deploy
.\update.bat
```

PostgreSQL 기본 실행은 [Windows PostgreSQL 빠른 시작](windows-postgresql-quickstart.md)을 따른다. 이미 PostgreSQL로 이관한 PC에서는 최초 구축 도구를 다시 실행할 필요가 없다.

## 다음 업데이트부터

`update.bat`만 더블클릭한다. 아래 작업을 순서대로 처리한다.

1. Git 상태 확인과 다운로드
2. 실행 중인 웹/API 종료
3. 소스 업데이트
4. 의존성 설치와 프런트엔드 빌드
5. 기존 DB와 설정 자동 백업·검증, 백업 경로 표시
6. 필요한 PostgreSQL DB migration과 계정 준비 확인 — 최초 관리자가 없으면 같은 창에서 설정
7. 웹/API 시작과 브라우저 열기

기존 Git 폴더는 현재 브랜치의 upstream을 따른다. upstream이 없으면 같은 이름의 `origin` 브랜치를 사용한다. 대상 브랜치가 없거나 직접 수정한 소스·미추적 파일이 있으면 앱을 종료하기 전에 중단한다. 다른 브랜치로 임의 전환하거나 변경을 강제로 덮어쓰지 않는다.

이전 실행의 사용자 지정 웹/API 포트도 유지한다. 기본 웹 주소는 `http://127.0.0.1:5173`이다. 설치된 런타임과 사내 네트워크 설정은 기존 [Windows 배치 안내](windows-one-click-deployment.md)를 따른다.

## 실패했을 때

화면의 실패 단계와 `log/update-*.log`를 확인한다. Git 다운로드 실패는 기존 앱을 종료하지 않는다. 백업 실패는 DB migration을 차단하고, 배포·DB migration·계정 준비 실패는 이후 단계와 재시작을 중단한다. 원인을 해결한 다음 같은 `update.bat`를 다시 실행한다. DB migration을 자동으로 되돌리지는 않는다.

PostgreSQL은 기존 `.postgres-owner.env`와 DB 연결 설정으로 `upgrade_postgres_schema.py`를 실행한다. owner 설정이 없다면 [PostgreSQL 운영 절차](backend-sql-integration-guide.md)를 따른다. DuckDB를 쓰는 설치에서는 PostgreSQL migration을 건너뛴다. 현재 관리형 로컬 실행의 migration은 `0022_managed_local_execution`이며 실제 적용 대상은 Alembic의 최신 head다.

## 관리자 옵션

업데이트기가 이미 설치된 폴더에서는 다음처럼 선택 옵션을 줄 수 있다.

```powershell
.\update.ps1 -NoBrowser
.\update.ps1 -NetworkMode direct
```

`update.ps1`을 직접 실행해 다른 저장소에 **최초 연결**할 때는 `-RepositoryUrl`, `-Branch`를 지정한다. 기존 Git checkout에서는 현재 upstream을 유지한다. BAT 한 파일로 시작하는 최초 다운로드는 옵션 없이 실행하며, 환경변수 `SIMDASH_UPDATE_REPOSITORY`, `SIMDASH_UPDATE_BRANCH`로 대상을 변경할 수 있다. 이 최초 다운로드 단계에 CLI 옵션을 주면 사용 방법을 표시하고 종료한다.

이 업데이트기는 Windows 로컬 배포용이다. Rocky 운영 서버는 기존 systemd/nginx 배포 절차를 사용한다.

## 적용 확인

1. 기존 프로젝트·의뢰가 남아 있는지 확인한다.
2. 저장 폴더 설정의 사내 SPDM 경로가 유지되는지 확인한다.
3. 기본 메뉴 `내 PC 설정`에서 내 PC 연결과 프로그램 목록을 확인하고 작업 실행 화면에서도 같은 목록을 확인한다.
