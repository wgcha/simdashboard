# Windows 파일 실행으로 설치·구동하기

- 기준일: 2026-09-14
- 대상: PostgreSQL이 준비된 Windows x64 소스 배포 PC. Python·Node.js·Git·VS Code의 사전 설치는 필요하지 않음
- 진입점: `deploy.bat`, `start.bat`, `stop.bat`, `update.bat`

모든 소스 변경에 적용하는 [배포 정책](windows-deployment-policy.md)과 [신규·업데이트 시나리오](windows-deployment-scenarios.md)를 따른다. **Windows Server 2022만 있는 폐쇄망**은 [완결 설치 패키지 안내](windows-server-offline-installation.md)를 사용한다.

## 처음 사용할 때

`deploy.bat`은 실행 환경 전용 준비 스크립트와 안전한 DB 준비 단계를 호출한다. 기존 `setup.ps1`의 Fresh/Transfer 메뉴는 일반 설치·업데이트에서 호출하지 않는다.

1. GitHub에서 소스 ZIP을 받고 **전체 압축을 푼다**. ZIP 안에서 배치 파일만 실행하지 않는다.
2. 압축을 푼 폴더의 **`deploy.bat`**를 더블클릭한다. 런타임·의존성·웹 빌드 후 DB 준비, 기존 DB 백업, 마이그레이션, 계정 준비를 진행한다. PostgreSQL 연결이 없으면 관리자 호스트·포트·사용자·비밀번호를 입력한다. 비밀번호 입력은 숨겨진다. DB 없음/실제 빈 DB만 최초 준비하며, 데이터가 있는 미설정 DB를 덮어쓰지 않는다.
3. 최초 웹 관리자 아이디와 비밀번호를 입력하면 **앱 시작까지 이어서 실행**한다. 웹과 API가 준비되면 기본 브라우저에 회원 로그인·가입 화면이 열린다. 일반 직원은 가입 후 관리자 승인을 받는다. 이후 다시 실행할 때는 **`start.bat`**를 사용한다.

기본 주소는 **http://127.0.0.1/home**다(HTTP 80). 인증된 설치는 별도 수신 주소 설정이 없어도 **http://컴퓨터이름/home**와 **http://사내IP/home**로 접속한다. [Windows 사내 접속 안내](windows-lan-access.md)를 참고한다. 종료할 때는 **`stop.bat`**를 더블클릭한다. 브라우저 창만 닫아도 서버는 계속 실행된다.

`stop.bat`은 중지 확인 중임을 표시하고, 작업 후 완료 또는 실패 결과를 안내한다. 결과를 확인한 뒤 아무 키나 누르면 배치 실행이 끝나고 더블클릭으로 연 창이 닫힌다. 업데이트·배포 자동화는 입력 대기가 없는 `stop.ps1`을 사용한다.

Python·Node.js·Git을 별도로 설치하지 않아도 된다. 기본 Windows PowerShell 5.1을 사용하며 런타임은 소스 폴더 안의 `.tools`와 `.venv-runtime`에 둔다. 최초 설치에는 패키지 다운로드가 가능한 연결이 필요하다. 이후 `start.bat`는 설치나 패키지 다운로드를 수행하지 않는다.

## 사내 프록시와 인증서

[개발환경 이슈 #15](https://github.com/wgcha/simdashboard/issues/15)의 프록시·인증서 입력을 `deploy/windows/network-settings.json`에 반영했다.

- 기본 모드 `auto`: 기존 `HTTP_PROXY`/`HTTPS_PROXY`를 우선 사용한다. 별도 환경변수가 없으면 설정된 사내 프록시의 연결 가능 여부에 따라 프록시 또는 직접 연결을 선택한다.
- 프록시: 이슈의 `http://168.219.61.252:8080`.
- 우회: localhost·loopback 및 이슈의 사내 IP 대역·`samsung.net`을 도구별 형식으로 정규화한다. 웹→로컬 API 연결은 사내 프록시로 보내지 않는다.
- 신뢰: 기본 Windows 인증서 저장소와 선택한 조직 CA를 사용한다. TLS 검증을 끄는 설정은 기본으로 저장하지 않는다.

회사 PC에 조직 CA가 이미 등록되어 있으면 그대로 실행한다. 별도 파일이 필요한 PC는 사내에서 받은 **`DigitalCity.crt`**를 아래 위치 중 하나에 놓고 `deploy.bat`를 실행한다.

1. `deploy/windows/certs/DigitalCity.crt`
2. 사용자의 바탕화면 `DigitalCity.crt`

다른 위치의 인증서는 `network-settings.json`의 `caCertificatePath`에 경로를 지정한다. JSON에서는 Windows 경로를 `C:/certs/DigitalCity.crt`처럼 슬래시로 쓰면 간편하다. 인증서 파일과 자격 증명은 Git에 올리지 않는다. 인증이 필요한 프록시는 회사에서 제공하는 프로세스 환경변수로 지정하고 설정 파일에 계정·비밀번호를 적지 않는다.

자동 판별 대신 모드를 정하려면 PowerShell에서 실행한다.

```powershell
.\deploy.ps1 -NetworkMode proxy
# 사외 또는 직접 연결이 승인된 환경
.\deploy.ps1 -NetworkMode direct
```

실제 사내 방화벽·프록시·조직 인증서의 접속 가능 여부는 사내 PC에서 확인한다. Node.js, GitHub의 uv/Python 릴리스, PyPI 및 npm 패키지 다운로드가 허용되어야 한다. 완전 폐쇄망에서는 소스 ZIP만으로 최초 설치할 수 없다.

## 재설치·업데이트·기존 데이터

- 다시 사용할 때는 `start.bat`만 실행한다. 이미 이 폴더에서 정상 실행 중이면 기존 서버를 사용하고 브라우저를 연다.
- 새 버전 적용은 **`update.bat` 하나를 더블클릭**한다. Git 다운로드·앱 종료·소스 적용·의존성/빌드·기존 DB 및 설정 백업·DB migration·계정 확인·재시작을 순서대로 처리한다. 백업 실패 시 DB 변경과 재시작을 중단한다. Git은 업데이트 기능에 필요하다. ZIP/`No commits yet` 폴더의 최초 연결은 [사내 PC 업데이트 안내](windows-git-update.md)를 따른다.
- `.env`가 없을 때만 기본 예제를 복사한다. 기존 `backend/.env`가 있으면 그 설정을 유지한다. 새 환경과 DB 종류 미지정 실행의 기본값은 PostgreSQL이다.
- PostgreSQL 서버가 준비되어 있으면 새 소스 설치가 전용 앱 DB·owner/app 역할을 준비한다. 기존 설치의 `.env`/`backend/.env`와 `.postgres-owner.env`를 유지하면 그 연결로 백업 후 업데이트한다. 별도 서버에 기존 DB만 있다면 해당 app/owner 연결을 먼저 설정한다. 관리자 연결을 입력했다고 기존 데이터에서 앱 비밀번호를 추측하거나 재발급하지 않는다.
- 기존 `ANALYSIS_DB_BACKEND=duckdb` 설정은 유지한다. 해당 DB 파일을 백업하고 검증된 이관을 완료한 뒤 PostgreSQL로 전환한다. 로컬 시험용 DuckDB도 명시적으로 선택해야 한다.
- DB 종류 설정이 없는데 기존 DuckDB 파일이 있으면 배포·시작을 중단하고 이관 안내를 표시한다. `.env` 또는 `backend/.env` 파일이 존재하더라도 DB 종류가 빠져 있으면 같은 보호 규칙을 적용한다. 기존 데이터를 계속 사용할 때는 `ANALYSIS_DB_BACKEND=duckdb`, 이관 검증을 마친 경우에는 `ANALYSIS_DB_BACKEND=postgresql`을 명시한다.
- 기존 `.env`, PostgreSQL 연결 정보, 의뢰·결과 DB는 설치 과정에서 덮어쓰거나 초기화하지 않는다.
- 이전 DB 이관 실패의 `.setup-recovery-required.json`이 있으면 먼저 해당 복구 절차를 완료해야 한다.
- 다른 PC로 기존 데이터를 옮길 때는 소스 업데이트와 별개로 [DB 이전 안내](postgresql-pc-transfer-guide.md)를 따른다.

인증된 Windows 설치는 기본적으로 사내 IP와 컴퓨터 이름으로 접속할 수 있다. 기존 `.env`에 `WINDOWS_WEB_HOST=127.0.0.1`을 명시한 설치는 그 설정을 유지하므로, 사내 접속으로 바꾸려면 `0.0.0.0`으로 변경 후 재시작한다. [사내 IP 접속 안내](windows-lan-access.md)를 참고한다. 시작 시 스키마 변경이 필요하면 배포/업데이트를 안내하며 `start` 자체는 migration을 실행하지 않는다. Windows 상시 서버 설치는 [폐쇄망 서버 안내](windows-server-offline-installation.md), Rocky 서버는 [기존 운영 안내](../deploy/rocky8/README.md)를 따른다.

## 실행이 안 될 때

오류가 나면 배치 창이 바로 닫히지 않아 메시지를 읽을 수 있다.

| 증상 | 확인할 것 |
|---|---|
| 설치 중 연결 실패 | 프록시 접속, 프록시 모드, 패키지 사이트 접근 권한 |
| 인증서 검증 실패 | 회사에서 제공한 올바른 `DigitalCity.crt` 또는 Windows 신뢰 저장소 |
| `.tools` 또는 `.venv-runtime` 없음 | 전체 소스를 압축 해제했는지 확인하고 `deploy.bat` 실행 |
| 포트 사용 중 | 다른 프로그램인지 확인하고 포트를 변경하거나 해당 프로그램을 직접 종료 |
| 기존 실행 상태를 확인할 수 없음 | 로그와 `.server-pids.json`의 이 폴더 실행 상태 확인; 다른 프로그램을 자동 종료하지 않음 |
| PostgreSQL 연결 실패 | 보존된 `.env`의 대상 DB·권한·스키마 확인 |
| `Backend readiness timed out` | `backend/uvicorn-error.log` 마지막 줄을 확인한다. `Waiting for application startup`이면 초기화 중이고, `Application startup complete` 이후라면 로컬 상태 확인 경로를 점검한다. |

실행 로그는 `backend/uvicorn.log`, `backend/uvicorn-error.log`, `frontend/vite.log`, `frontend/vite-error.log`에 남는다.

포트가 겹치면 다음처럼 시작할 수 있다. 중지는 같은 폴더의 `stop.bat`를 사용한다.

```powershell
.\start.ps1 -BackendPort 18080 -FrontendPort 18173
```

백엔드·웹 준비 확인과 중복 실행 확인은 Windows 시스템 프록시/PAC와 무관하게 loopback에 직접 요청한다. 다운로드용 사내 프록시는 유지한다. 기본 준비 대기는 서비스별 120초이며, 초기 DB 준비가 느린 PC는 다음처럼 늘릴 수 있다.

```powershell
.\start.ps1 -StartupTimeoutSeconds 300
```

제한시간이 지나도 준비되지 않으면 서버 로그에서 원인을 확인한다. 이 옵션은 DB 오류를 복구하거나 기존 데이터를 초기화하지 않는다.

## 검증과 구현 경계

- 설치: `deploy.ps1` → `scripts/windows/prepare-source-environment.ps1` → 필요 시 `bootstrap-runtime.ps1` → `initialize-source-postgres.ps1` → 백업·migration·계정 준비.
- `deploy.ps1`은 업데이트 자동화와 호환되도록 기본값에서 앱을 시작하지 않는다. `deploy.bat`은 `-StartAfterDeploy`를 전달한다. 비대화형 최초 설치는 `POSTGRES_ADMIN_URL`과 계정 준비 조건을 사전에 충족해야 하며, 필요한 입력이 없으면 기다리지 않고 실패한다.
- 프록시·CA: `scripts/windows/Network.psm1` 및 `deploy/windows/network-settings.json`.
- 실행·중지: `start.ps1`, `stop.ps1`. 저장된 PID와 시작 시각·실행 경로·포트를 확인한다.
- CI: `.github/workflows/windows-deploy-smoke.yml`에서 Windows PowerShell 5.1의 설치·실행·재실행·중지를 확인한다.
- 로컬 검증(2026-09-08): Git·런타임·가상환경·설정 파일이 없는 별도 소스 폴더에서 공식 Node/uv/Python 다운로드, 의존성 설치, TypeScript/Vite 빌드를 통과했다. 한글·공백 경로와 Windows PowerShell 5.1을 사용했다. npm 패키지 일부는 PC의 기존 패키지 캐시를 재사용했다.
- 실제 `start.bat` 재실행 시 PID·시작 시각이 유지되며, API·웹·사용자 지정 포트의 웹 API 프록시가 정상 응답했다. IAB에서 결과 대시보드와 콘솔 오류 0건을 확인했다.
- `stop.bat`의 포트 종료, 다른 폴더의 실행본 종료 거부, 잘못된 PID JSON 보존, 복구 표시 파일의 설치 차단을 통과했다. 재설치 후 `.env`와 DB의 SHA-256이 동일했다.
- 검사 과정에서 발견한 PowerShell 5.1의 Python 문자열·포트 인자 전달 문제와 기본 DB 상대 경로 문제는 수정 후 재검증했다. 기존 500 kB 초과 프런트 번들 경고는 남아 있다.
- 네트워크 선택 자체 검사와 시작 스크립트 계약 pytest 2개를 통과했다. 로컬 실행 기록은 Git에서 제외한 `backups/windows-smoke-20260908/`에 보관한다. 실제 사내 프록시와 조직 CA의 외부 다운로드는 현장 확인 대상이다.

시스템 CA 지원은 [Node.js 22.19 릴리스](https://nodejs.org/en/blog/release/v22.19.0)와 [uv 인증서 설정](https://docs.astral.sh/uv/concepts/authentication/certificates/)을 기준으로 적용한다. 사용자 PC의 전역 npm/pip 설정이나 Windows 인증서 저장소를 설치기가 일괄 변경하지 않는다.
