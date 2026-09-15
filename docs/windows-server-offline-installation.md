# Windows Server 2022 폐쇄망 설치

- 기준일: 2026-09-14
- 대상: **Windows Server 2022 x64만 설치된 서버**. Python, PostgreSQL, Node.js, Git, VS Code, 인터넷 연결이 없어도 반입 패키지로 설치한다.
- 배포 계약: [Windows 배포 정책](windows-deployment-policy.md). 소스 PC는 [deploy.bat 안내](windows-one-click-deployment.md)를 사용한다.
- 검증 구분: 스크립트·패키지 빌드 검증과 실제 네트워크 차단 Server 2022 VM 검증을 구분한다. 이 문서만으로 VM 실기 검증 완료를 뜻하지 않는다.

## 설치 담당자

1. 빌드 PC에서 받은 `simworkbench-windows-offline-<release>.exe`와 SHA-256 파일을 서버에 반입한다. 전달받은 해시와 비교한다.
2. EXE를 실행하고 UAC 승격을 승인한다. ZIP으로 받은 경우 전체 압축을 풀고 `install.cmd`를 실행한다. 설치 확인 창을 읽고 설치 폴더를 선택한다. 기본값은 `C:\ProgramData\SimulationWorkbench`다.
3. 최초 설치에서는 PostgreSQL 방식을 선택한다. **포함된 서버 설치**는 전용 PostgreSQL과 앱 DB를 준비한다. **기존 서버 연결**은 빈 대상용 관리자 연결 또는 기존 앱 DB의 app/owner 연결을 입력한다. 연결 URL은 숨김 입력이다.
4. 최초 웹 관리자 계정을 입력한다. 기존 계정이 있으면 그 계정을 유지한다.
5. 서비스 상태 확인 후 안내하는 `http://서버이름:8080/home/` 또는 사내 IP 주소로 접속한다. 기본 API는 loopback 18080, 새 PostgreSQL은 loopback 55432를 사용한다. 웹/API는 Windows 서비스이며 재부팅 뒤 자동 실행한다.

운영 프로그램의 `update.bat`은 Git 기반 **소스 업데이트** 도구다. 폐쇄망 설치본은 **새 EXE의 같은 설치 절차**로 업데이트한다. 설치가 끝나면 반입 ZIP/EXE와 임시 압축 해제 폴더를 보관/제거해도 설치 경로의 런타임으로 실행한다.

## DB 상태별 동작

| 상태 | 설치 동작 |
|---|---|
| PostgreSQL도 없음 | 포함 바이너리로 전용 PostgreSQL 서비스 설치, DB/역할/스키마/관리자 준비 |
| PostgreSQL만 있고 앱 DB가 없거나 실제 빈 DB | 기존 서버의 관리자 연결로 비파괴 최초 준비 |
| 기존 앱 DB와 연결 정보 있음 | 설정·DB·관리형 첨부파일 백업 검증 후 대기 중인 migration 적용 |
| 기존 설치본 업데이트 | 저장된 설치 설정·DB 연결·인증 키·계정·자산 경로 보존 |
| 미식별 스키마/잘못된 자격 증명/백업 실패 | 중단. DB 삭제나 새 DB 전환으로 우회하지 않음 |

자동 최초 준비의 DB/역할 이름은 `simulation_dashboard`, `simdashboard_owner`, `simdashboard_app`이다. 기존 서버의 다른 데이터베이스는 변경하지 않는다. 앱 업데이트는 PostgreSQL 메이저 업그레이드와 별개다. 포함 DB 서버의 기존 바이너리와 데이터 디렉터리를 새 앱 릴리스로 교체하지 않는다.

기존 DB가 다른 소스 PC에서 사용되던 경우 app/owner 연결 정보만으로 그 PC의 파일이 옮겨지지는 않는다. 관리형 첨부파일을 포함한 이전은 [DB 이전 안내](postgresql-pc-transfer-guide.md)의 별도 작업이며, 누락된 파일 때문에 백업 검증이 실패하면 해당 파일을 먼저 복구한다. 미디어가 DB에 모두 저장된 경우도 해당 백업 도구가 무결성을 검증한다.

## 설치 설정과 영구 상태

고급 설치는 `config/install.example.json`을 별도 안전한 위치의 JSON으로 복사하고 다음처럼 실행한다.

```powershell
.\install.ps1 -Config C:\deployment\install.json
# 관리자 PowerShell의 자동화: 계정이 이미 준비되어 있어야 성공한다.
.\install.ps1 -Config C:\deployment\install.json -NonInteractive
# 무결성 및 대상 OS 검사만 수행 (설치/DB/서비스 변경 없음)
.\install.ps1 -Check
```

`postgresMode`는 `bundled`/`existing`, `tlsMode`는 `http`/`certificate`다. 기본 HTTP는 컴퓨터 이름·IP 사용을 지원한다. HTTPS는 `serverName`과 조직이 제공한 `tlsCertificate`, `tlsKey`를 지정하고 해당 인증서를 신뢰할 수 있어야 한다. 인터넷 ACME 발급을 시도하지 않는다. 기본 방화벽 규칙은 웹 포트의 `LocalSubnet` 접근이며 다른 사내 서브넷은 조직의 방화벽 정책으로 허용한다. API와 DB 포트는 외부 수신용으로 열지 않는다.

| 설치 경로 아래 | 역할 |
|---|---|
| `releases/<release>-<attempt>/` | 버전별 소스·정적 웹·Python 및 대상에서 만든 venv |
| `postgresql/` | 최초 설치한 bundled PostgreSQL 바이너리 (앱 업데이트에서 교체하지 않음) |
| `state/.env`, `.postgres-owner.env` | 영구 앱/owner 연결과 인증 설정 |
| `state/install-settings.json` | 비밀값을 제외한 재설치/업데이트 설정 |
| `state/assets/` | `SIMDASH_ASSETS_ROOT`로 연결한 관리형 첨부파일·보고서 템플릿 |
| `state/postgres/data/` | bundled DB 데이터 |
| `state/backups/` | DB·설정·관리형 첨부파일의 검증한 업데이트 전 백업 |
| `state/services/`, `state/logs/` | WinSW 서비스 설정과 실행 로그 |
| `state/current.json` | 상태 확인을 통과한 릴리스와 백업 위치 |
| `state/recovery-required.json` | 실패 단계와 복구 안내 |

서비스 이름은 `SimulationWorkbenchApi`, `SimulationWorkbenchProxy`, bundled 모드의 `SimulationWorkbenchPostgreSQL`이다. 웹/API는 내장 LocalService로 실행한다. owner 자격 증명과 백업은 관리자 전용이며 앱 서비스에 읽기 권한을 주지 않는다. 설정은 업데이트할 때 다시 입력하지 않도록 저장한다. JSON에 직접 넣은 DB 비밀번호는 저장된 설치 설정에서 제거되지만 입력용 원본 JSON은 관리자 책임으로 보호한다.

설치 시도마다 새 디렉터리를 사용하므로 현재 실행본을 복사 중 덮어쓰지 않는다. 준비 실패 전까지 기존 서비스는 유지하고, 백업·migration을 위해 중지한 뒤 실패하면 중지 상태와 백업을 유지한다. 이전 앱만 다시 실행하는 것을 DB 복구로 간주하지 않는다. `.setup-recovery-required.json`이 생성된 DB 최초 준비 실패는 복구 검토 전 재실행을 차단한다.

## 빌드 담당자

외부 Windows x64 PC에서 소스를 준비한다. `deploy.bat`로 소스 실행을 검증한 후, 또는 전용 빌드 환경에서 고정 Node/Python과 패키지 캐시를 준비한다. 운영 DB·`.env`·첨부파일을 패키지에 넣지 않는다.

공식 배포처에서 확보한 런타임을 다음 구조로 준비한다. Python은 `.python-version`과 같은 전체 portable 배포판이며 `venv`와 `pip`를 포함해야 한다. Python embed ZIP만으로 대체하지 않는다.

```text
vendor/
  python-runtime/python.exe
  wheelhouse/*.whl
  postgresql/bin/{postgres,pg_ctl,initdb,pg_dump,pg_restore,psql}.exe
  postgresql/lib/ ...
  postgresql/share/ ...
  postgresql/server_license.txt
  caddy/caddy.exe
  caddy/LICENSE
  winsw/WinSW-x64.exe
  winsw/LICENSE.txt
  vc_redist.x64.exe
```

WinSW는 .NET Framework 4.6.1 빌드(`WinSW.NET461.exe`)를 위 이름으로 복사하면 Server 2022의 .NET Framework에서 동작한다. 개발자가 선택한 바이너리의 라이선스와 공식 출처/해시를 함께 관리한다. VC++ 런타임은 Microsoft 서명을 검사한다. [PostgreSQL Windows 바이너리](https://www.postgresql.org/download/windows/), [WinSW 공식 릴리스](https://github.com/winsw/winsw/releases/tag/v2.12.0), [Caddy](https://caddyserver.com/docs/), [Microsoft 런타임](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist), [Inno Setup](https://jrsoftware.org/isdl.php)를 참고한다.

외부 빌드 PC에서 Windows wheel을 수집한다.

```powershell
& C:\vendor\python-runtime\python.exe -m pip download `
  --only-binary=:all: --dest C:\vendor\wheelhouse -r backend\requirements.lock
```

릴리스 제작은 다음 하나의 명령을 사용한다. Inno Setup은 **빌드 PC에만** 필요하다.

```powershell
.\scripts\windows\build-offline-bundle.ps1 `
  -VendorRoot C:\vendor -ReleaseId 2026.09.14.1 `
  -Output E:\release\simworkbench-windows-offline-2026.09.14.1.zip `
  -InnoSetupCompiler 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

빌더는 `pnpm install --offline --frozen-lockfile`과 `/home/` 정적 빌드, 새 venv에서 전체 wheel의 `--no-index` 설치·import, 필수 바이너리 확인, 파일 해시 manifest, ZIP/EXE/SHA-256 생성을 수행한다. 프런트엔드 패키지 캐시는 빌드 PC에서 미리 채운다. `-SkipFrontendBuild`는 검증된 `/home/` 빌드가 이미 있을 때만 사용한다. `.env`·DB·로그·개인 데이터가 아닌 허용된 코드 경로만 복사한다. 기본 릴리스는 깨끗한 Git 작업 트리를 요구하고, `-AllowDirty`는 개발용 패키지에만 사용하며 manifest에 남긴다.

## 릴리스 수락

CI의 Windows 배포 계약, 오프라인 wheel 검사와 설치 스크립트 검사를 통과한 뒤, 네트워크를 끊은 Server 2022 VM에서 신규 설치 → 업무 데이터/첨부파일 생성 → 새 패키지 업데이트 → 데이터·계정 확인 → 재부팅 자동 시작을 수행한다. EXE 빌드 성공을 이 실기 검증으로 대체하지 않는다. 사용 전 검사 결과와 패키지 해시를 [작업 기록](../log/work-log.md)에 남긴다.
