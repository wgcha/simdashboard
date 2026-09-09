# Windows 원클릭 업데이트

2026-09-09 사용자 요청: 이미 배포한 폴더에서 여러 명령을 입력하지 않고 한두 번의 파일 실행으로 업데이트한다. `.env`, DB, 런타임, 결과 폴더를 유지한다.

## 사용자 흐름

- 평소 시작은 `start.bat`, 새 버전 적용은 `update.bat` 하나를 실행한다.
- 이전 배포본에 업데이트기가 없으면 새 `update.bat` 한 파일만 기존 `stop.ps1` 옆에 복사한다. BAT는 원격 브랜치의 업데이트기를 `backups/updater-driver-*`에 내려받고 `update.ps1 -ProjectRoot <기존 폴더>`로 실행한다. Git 모듈은 내려받은 driver에서 로드하며 배포·Runtime·DB·시작 스크립트는 갱신된 대상 폴더에서 실행한다. 원격 브랜치에 업데이트기가 아직 없으면 기존 서비스를 건드리기 전에 중단한다.
- 업데이트는 Git 상태/원격 확인 → 다운로드 → 앱 종료 → 소스 적용 → `deploy.ps1` → `upgrade_postgres_schema.py` → `start.ps1` 순서다. 각 단계의 성공 후에만 다음으로 진행한다.
- 기존 Git checkout은 현재 브랜치의 upstream을 사용한다. upstream이 없으면 같은 이름의 origin 브랜치에 연결하며, 원격에 해당 브랜치가 없으면 임의로 다른 브랜치로 바꾸지 않는다.
- ZIP/`No commits yet` 폴더는 기본 저장소 `https://github.com/wgcha/simdashboard.git`, 브랜치 `codex/windows-one-click-deploy`에 최초 연결한다. 원격/브랜치는 명시적 실행 옵션으로 변경할 수 있다. 최초에는 대상과 원본 소스의 백업 위치를 보여주고 Enter로 시작할 수 있게 한다. 이후에는 추가 입력이 없다. Git 자격 증명 요구는 Git의 기존 인증 방식을 사용한다.
- 최초 연결은 기존 폴더 안에서 수행한다. 내려받을 Git 추적 경로와 겹치는 기존 소스 파일만 백업한 후 checkout한다. 개인 설정·DB·런타임·결과 파일은 이동하거나 삭제하지 않는다. 로컬 수정 이력이 있는 정상 checkout은 자동으로 덮어쓰거나 stash하지 않고 중단한다.
- 실패한 단계와 재실행/확인 방법을 표시한다. 빌드/마이그레이션 실패 후에는 새 서버를 시작하지 않는다. DB 마이그레이션을 자동으로 되돌리지는 않는다.

## 구현 경계

`scripts/windows/GitUpdate.psm1`은 Git 사전 점검·원격 fetch·소스 적용을 담당한다. `update.ps1`은 사용자 진행 표시와 종료/배포/DB/재시작 순서를 담당한다. 기존 배포/DB 도구를 재사용한다. 임시 Git 저장소와 가짜 서비스 실행 파일로 실제 사용자 환경을 건드리지 않는 검증을 수행한다.

모듈 공개 계약:

- `Get-WorkbenchGitUpdatePlan -Root <absolute> [-RepositoryUrl <url>] [-Branch <name>]`: Git 설치/프로젝트 루트/작업트리 상태 확인, remote와 branch 결정, fetch 및 fast-forward 검사. Bootstrap은 실제 checkout 전 파일 충돌/보호 경로를 검사한다. 반환은 PSCustomObject `{Root,Mode,Branch,Remote,TargetRef,TargetCommit,OriginalCommit,BackupDirectory,Changed}`. Mode=`Existing|Bootstrap`. 무변경이어도 배포/마이그레이션 재시도는 가능하다.
- `Invoke-WorkbenchGitUpdate -Plan <plan>`: 사전 점검 후 변경이 없는지 재확인하고 소스 적용. Bootstrap 백업/보호 검사를 모두 끝낸 뒤 겹치는 소스 파일을 이동하고 checkout한다. 기존 checkout은 검증한 커밋으로 fast-forward만 한다.

## 제약과 검증

- 모든 Git/PowerShell 호출은 인자 배열로 전달하고 모든 exit code를 확인한다. PS5.1, 공백/한글 경로를 지원한다.
- 최초 연결 및 일반 업데이트의 보호 경로: `.env*`, `.postgres-owner.env*`, `.git`, `.tools`, `.venv*`, `node_modules`, `frontend/node_modules`, `frontend/dist`, `backend/data`의 사용자 DB/파일, `backups`, `output`, `dist`, `transfer-bundles`, 로컬 인증서/설정/실행 기록. 원격 소스가 이 경로를 추적하면 적용 전에 거부한다. 소스 예제 `.env.example` 등 명확한 템플릿과 기존 추적 안내 문서 `deploy/windows/certs/README.md`, `log/work-log.md`는 허용한다.
- 재귀 이동/삭제 전에 절대 경로가 허용된 프로젝트 또는 생성한 테스트/백업 경계 내부인지 검증한다. symlink/reparse point를 따라 개인 파일에 쓰지 않도록 겹치는 경로 및 부모를 점검한다.
- 동일 폴더 중복 업데이트는 파일 잠금으로 막는다. 백업·로그·잠금은 Git에서 제외하며 자격 증명/비밀 환경 내용을 로그에 출력하지 않는다.
- 정상 FF, 변경 없음, 수정/충돌/분기/원격 실패, unborn/no-.git 최초 연결 및 파일 보존, 보호 경로·symlink 차단, Git 실패 시 서비스 미종료, 단계 실패 시 후속 단계 중단, 포트 보존을 검증한다.
- 이미 실행 중인 웹/API의 사용자 지정 포트는 기존 PID 메타데이터에서 보존한다. 설치된 업데이트기 및 `update.ps1` 직접 실행은 `-NoBrowser` 옵션을 제공한다. BAT 한 파일의 최초 다운로드는 인자 없는 더블클릭 전용이며, 다른 원격/브랜치는 문서에 명시한 환경변수로 선택한다. 이 단계에 CLI 옵션을 주면 무시하지 않고 사용 방법을 표시하며 종료한다.
- Git으로 받은 소스는 신뢰하는 프로젝트 코드를 실행하는 경계다. 이 업데이트기는 Windows 로컬 배포용이며 Rocky 운영 서버의 systemd/nginx 배포를 대신하지 않는다.
