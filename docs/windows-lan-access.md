# Windows 컴퓨터 이름·IP로 `/home` 접속하기

인증이 설정된 Windows 설치는 기본적으로 사내망에서 접속할 수 있다. `AUTH_MODE=password` 또는 `oidc`이고 수신 주소를 따로 지정하지 않으면 `0.0.0.0`으로 시작한다. 명시적으로 사내 접속을 설정하려면 기존 `.env`에 다음 값을 지정한다. `backend/.env`만 사용하는 설치는 그 파일에 설정한다. `.env.example`을 기존 `.env` 위에 복사하지 않는다.

```dotenv
WINDOWS_WEB_HOST=0.0.0.0
```

지원 값은 `127.0.0.1`과 `0.0.0.0`이며 미지정 시 인증된 설치는 사내망 모드, 인증 없는 개발 환경은 로컬 모드로 시작한다. 프로세스 환경변수, 루트 `.env`, `backend/.env` 순서로 우선한다. 기존 계정·비밀번호·DB 연결 설정은 유지한다.

## 이번 수정 적용

이번 변경은 Windows 웹 기본 포트를 80으로, 앱 기본 경로를 `/home/`로 바꾼다. 실행 PC의 컴퓨터 이름을 자동으로 허용하므로 테스트 PC에서 서버 PC로 옮겨도 이름을 코드에 적을 필요가 없다. 새 패키지나 DB 마이그레이션은 없다. 아래 Git 명령은 변경이 사용하는 원격 브랜치에 게시된 이후 적용한다.

```powershell
git branch --show-current
git pull --ff-only
```

pull이 성공한 뒤 위 `.env` 한 줄을 추가/변경하고, 다음 순서로 실행한다.

```powershell
.\stop.bat
.\start.bat
```

사용자 지정 포트는 `start.bat -FrontendPort 9456 -BackendPort 9123`처럼 유지할 수 있으며 이때 주소는 `http://컴퓨터이름:9456/home`다. 새 업데이트 실행기는 기존 PID 기록의 사용자 지정 포트를 이어받되, 이전 형식의 기본 웹 포트 5173은 중지 후 80으로 전환한다. 새 실행기로 명시적으로 선택한 5173은 이후에도 유지한다. 이미 실행 중인 이전 버전의 `update.bat`을 통해 이번 소스를 받았다면 기존 포트가 유지될 수 있으므로 위의 중지·시작을 한 번 실행한다.

**`git pull`만으로 실행 중인 프로세스의 설정과 코드가 교체되지는 않는다. 이번에는 pull + 재시작으로 적용된다.** 시작 전 미적용 DB migration이 발견되거나 이전 버전에서 여러 변경을 한꺼번에 받는 경우는 `update.bat`으로 의존성·백업·migration·빌드·재시작을 함께 처리한다. 이후 일반 업데이트에도 `update.bat`을 사용한다. Git hook으로 pull 때마다 서비스나 DB를 자동 변경하지 않는다.

## 다른 PC에서 열 주소

`start.bat` 창의 `LAN` 주소 중 직원 PC에서 도달 가능한 사내 네트워크 주소를 사용한다. 여러 네트워크 어댑터나 VPN이 있으면 후보가 여러 개 표시될 수 있다.

```text
http://서버컴퓨터이름/home
http://서버의사내IPv4/home
http://localhost/home
```

`localhost`·`127.0.0.1`은 실행 서버 PC에서만 사용한다. `0.0.0.0`은 수신 설정이며 접속 주소가 아니다. 기본 HTTP 80이므로 포트 입력을 생략한다. `/home` 진입 시 슬래시 또는 `/home/workspace/overview` 같은 내부 화면 경로가 추가될 수 있다. 컴퓨터 이름이 인식되지 않는 사내망에서는 IP 주소를 사용한다. 서버 측 설정은 직원 PC의 이름 확인 기능이나 사내 DNS 정책을 변경하지 않는다. IP가 바뀌면 새 IP를 사용한다.

경로를 바꾸려면 서버 `.env`에 `WINDOWS_WEB_BASE_PATH=/dashboard/`처럼 지정하고 중지·시작한다. 이때 `http://컴퓨터이름/dashboard`가 진입점이다. URL에는 컴퓨터 이름을 하드코딩하지 않으며 화면 이동은 기본 경로를 유지한다. 기본 경로 미지정 시 `/home/`이고, 기존 루트 방식이 필요한 경우 `/`를 지정할 수 있다. 직접 `pnpm dev/build`를 실행하는 프런트엔드와 기존 Rocky/Caddy 배포는 `VITE_APP_BASE_PATH` 미지정 시 기존 루트 `/`를 유지한다.

웹은 선택한 인터페이스에 열리고 `/api`·`/assets` 요청은 웹 서버를 통해 내부 API에 전달된다. 이 변경은 API의 수신 주소 `127.0.0.1`이나 PostgreSQL 수신 설정을 바꾸지 않는다. 직원 PC에 DB 계정이나 5432 포트를 공개할 필요가 없다. 설정 변경 전부터 실행 중인 프로세스는 재사용하지 않고 중지·재시작 안내를 표시한다.

## 연결 실패 점검

먼저 서버 PC에서 위 사내 IP 주소를 연다. 서버에서는 열리지만 다른 PC에서는 안 열리면 클라이언트 PowerShell에서 확인한다.

```powershell
Test-NetConnection -ComputerName 서버의사내IPv4 -Port 80
```

`TcpTestSucceeded=False`이면 서버 Windows 방화벽, 두 PC 사이의 네트워크 정책, 잘못 선택한 IP를 확인한다. 배포 도구는 방화벽을 자동 변경하지 않는다. 서버 관리자 PowerShell에서 사내 연결 프로필과 허용 대역을 확인한 뒤 필요한 경우 다음 규칙을 추가한다.

```powershell
New-NetFirewallRule -DisplayName 'Simulation Workbench HTTP 80' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 80 -Profile Domain,Private -RemoteAddress LocalSubnet
```

기존 허용 규칙이 있으면 중복 생성하지 않는다. 사용자 지정 웹 포트라면 그 포트로 바꾼다. 이 예시는 같은 서브넷만 허용한다. 다른 사내 서브넷은 관리자에게 실제 허용 대역과 네트워크 정책 확인을 요청한다. 공용 프로필까지 무조건 개방하거나 방화벽 전체를 끄지 않는다.

80번 포트를 IIS 등 다른 프로그램이 사용하면 시작기가 충돌을 알리고 중단한다. 다른 서비스를 강제로 종료하지 않는다. 기존 웹서버에서 이 앱으로 프록시하거나 명시적인 다른 포트를 선택한다(다른 포트를 사용하면 주소에 포트를 붙인다).

## 인증과 HTTPS

LAN 모드는 인증이 준비된 경우에만 시작한다. 최초 관리자가 없으면 서버 콘솔에서 계정을 설정한다. 인증 없는 로컬 개발 설정은 LAN 공개에 사용할 수 없다.

위 `http://주소/home` 경로는 기존 Vite 실행을 이용한 사내 접속 시험용이다. HTTP에서는 인증서를 검사하지 않으며 로그인 정보가 암호화되지 않는다. 직원 PC의 로컬 도우미를 안정적으로 사용하려면 신뢰할 수 있는 HTTPS 주소가 필요하다. 상시 운영은 [Windows HTTPS 구성](windows-caddy-intranet.md)의 Caddy 또는 기존 사내 HTTPS 프록시로 `frontend/dist`와 내부 API를 제공한다. 기존 HTTPS 템플릿은 루트 경로용이며 이번 Windows 실행기 설정과 별개다. HTTPS 프록시와 함께 실행할 때는 `WINDOWS_WEB_HOST=127.0.0.1`과 프록시가 점유하지 않는 `-FrontendPort`를 선택한다.

이미 `AUTH_COOKIE_SECURE=true`를 사용하는 환경에서는 HTTP LAN 모드로 낮추지 말고 기존 HTTPS 주소를 사용한다. HTTP 경로에서 Secure 쿠키를 사용할 수 없으므로 해당 조합은 시작 시 안내하고 중단한다. HTTPS 인증서 설치, 회사 방화벽·네트워크 정책 변경은 Git pull에 포함되지 않는다.

설정 유지 정책은 [사내 접속 기본값](lan-access-persistence.md)을 참고한다. 별칭이나 DNS를 등록하지 않으며, 실행 PC의 실제 컴퓨터 이름과 IP를 사용한다. 이름 확인이 안 되는 직원 PC에서는 IP로 접속한다.

아래 기록은 이전 `/workbench` 경로에서 수행한 검증 이력이다. 현재 기본 경로는 `/home/`이다.

## 2026-09-10 초기 LAN 변경과 검증 기록

프런트엔드·백엔드·DB 담당을 분리해 실행 주소, 동일 출처 API 연결, 계정 보존 계약을 함께 검토했다. 웹 수신 주소만 선택 가능하게 변경했고, `.env` 우선순위·Windows UTF-8 BOM 읽기·인증 준비 확인·기존 프로세스의 주소 불일치 감지·LAN 주소 표시를 추가했다. DB 스키마·마이그레이션 파일·의존성 lockfile 변경은 없다. `frontend/package.json` 변경은 자체 검사 명령 등록뿐이다.

| 검사 | 결과 |
|---|---|
| Python LAN 설정·기존 PostgreSQL 시작·배포 프로필 검사 | 103개 통과. 이후 Windows BOM 지원 추가 후 LAN 설정 18개 재통과 |
| Windows PowerShell 5.1 web-access 자체 검사 | 통과. 임시 `.env` 사용, 인증/쿠키 조건, 이전 PID 형식·호스트 변경, IPv4 후보 필터 확인 |
| 실제 LAN 인터페이스 HTTP 검사 | 통과. 같은 검증 PC의 비루프백 IPv4로 Vite 접속, `/api`·`/assets` 프록시와 쿠키 헤더 왕복 확인. 백엔드는 loopback stub 사용 |
| 프런트엔드 빌드 | 성공. 이번 변경 적용에 재빌드가 필요한 것은 아님 |
| 추가 기존 local-http 자체 검사 | 미통과. 변경하지 않은 legacy 요청이 강제 프록시를 우회했다는 기대 조건 불일치로 종료. 새 LAN 함수를 호출하기 전 단계이며 이번 수정의 원인으로 확인된 것은 아님 |

LAN 검사는 `frontend`에서 `npm run test:lan-proxy`, Windows 설정 검사는 `scripts/windows/web-access-self-test.ps1`로 재현한다. 실제 사내 서버·다른 직원 PC·회사 방화벽 통과는 이 로컬 검증에 포함되지 않는다. 기존 운영 `.env`나 DB를 수정하지 않았으며 테스트가 생성한 임시 서버는 종료했다.

## 2026-09-10 `/workbench` 후속 변경 기록

사용자 요청에 따라 HTTP 80의 컴퓨터 이름·IP 접속을 Windows 실행기에 적용했다. `/workbench`는 `/workbench/`로 query를 유지하며 이동하고, React Router basename과 사이드바 Link가 하위 경로·새 탭 주소를 유지한다. Vite 허용 호스트에는 실행 PC 이름을 추가하며 모든 임의 호스트를 허용하지 않는다. API와 legacy asset URL은 기존 동일 출처 `/api`·`/assets`를 유지한다. 설정 계약은 [Vite 기본 경로와 서버 설정](https://vite.dev/config/server-options) 및 React Router basename을 사용한다.

| 검증 | 결과 |
|---|---|
| Python Windows 설정 검사 | 26개 통과: 기본 포트·base, 사용자 지정 경로, 루트 경로, 잘못된 경로, 인증 조건 |
| Windows web-access 자체 검사 | 통과: hostname/IP URL, 80 생략, custom port, PID basePath |
| Windows update-entry 자체 검사 | 통과: legacy 5173→80, custom port 및 새 명시적 5173 유지 |
| LAN HTTP 자체 검사 | 통과: `/workbench` redirect와 query, subpath/root HTML, API·쿠키·asset, 호스트 허용/거부 |
| TypeScript/Vite 빌드 | 통과: 기존 루트 및 `/workbench/` asset base 확인 |
| 역할별 검수 | Astra 통합·설계, Terra Windows 실행기, Luna 프런트엔드 구현, Sol 최종 검수 승인(차단 이슈 없음) |
| 실제 Chromium, 1440×1000 | `http://laptop-tjp4higd/workbench` 및 `http://192.168.45.146/workbench`에서 로그인→개요→도움말→새로고침 통과. 문서 제목·화면 내용·base-aware 링크 확인. framework overlay, 관련 console/runtime 오류, 예상 밖 HTTP 오류 없음 |

브라우저 검증은 설치된 Playwright로 수행했다(Browser plugin not available). 임시 DuckDB와 임시 관리자 계정을 사용했으며 실제 PostgreSQL 계정·데이터를 변경하지 않았다. 실제 로컬 `.env`에는 요청한 접속을 준비하기 위해 `WINDOWS_WEB_HOST=0.0.0.0`만 반영했다. 이 PC의 기존 환경은 `ACCOUNT_SETUP_REQUIRED`이므로 실제 서비스 시작 전에 서버 콘솔에서 `setup-accounts.bat`으로 관리자 설정을 마치고 `start.bat`을 실행해야 한다. 다른 직원 PC에서의 이름 확인·방화벽 통과와 모바일 화면은 이번 검증 범위 밖이다.

## 2026-09-10 현재 PC 실행 준비

사용자의 후속 실행 요청으로 실제 PostgreSQL을 점검했다. 기존 revision `0021_modeling_templates`가 실행을 차단하여 공식 `prepare_account_deployment.py`로 백업을 검증했다(`backups/accounts/20260910T111339Z-905755be/manifest.json`, dual-read DB·파일 검증 완료). 이후 `upgrade_postgres_schema.py`로 추가형 migration 0022·0023을 적용했고, app-role 연결 및 최종 revision `0023_voc_posts` 검사를 통과했다. 기존 자료의 불완전한 데모 카탈로그는 백업 경고와 함께 포함되었으며 별도 수정하지 않았다. 서버 실행은 최초 관리자 계정 입력을 기다리는 대화형 PowerShell 창에서 이어진다. 관리자 자격 증명은 사용자가 직접 입력한다.

### 계정 설정 창 닫힘·인증 설정 저장 오류 수정

후속 점검에서 기존 `admin` 계정은 생성되어 있었지만 `.env` 인증 설정 저장이 Windows error 5로 실패한 것을 확인했다. 원인은 동일한 파일 소유자·그룹에도 `SetFileSecurityW`로 소유권을 다시 설정해 불필요한 `WRITE_OWNER` 권한을 요구한 것이다. 원본·대상 SID가 같으면 소유권 필드를 생략하고, DACL 및 상속 보호 상태를 보존한다. SID가 다르면 기존처럼 복원 권한을 요구하며 실패를 무시하지 않는다. Windows API 타입과 오류 추적도 명시했다.

`setup-accounts.bat`은 성공·실패 메시지 이후 창을 유지한다. 자동 검사는 `setup-accounts.bat -NoPause -NonInteractive`를 사용하며 Python 종료 코드를 유지한다. 검증은 계정 CLI·ACL 관련 13개 테스트, 실제 동일 사용자 임시 파일 ACL 복사, 공식 설정 도구의 `ACCOUNT_SETUP_READY` 및 재확인으로 수행했다. 기존 계정·비밀번호는 변경하지 않고 누락된 인증 설정만 저장했다.

실제 서비스 재시작도 완료했다. `http://127.0.0.1/workbench`, 컴퓨터 이름 및 사내 IPv4의 동일 경로에서 HTTP 200을 확인했다. `/api/auth/status`는 `mode=password`, `setup_required=false`를 반환한다. Chromium에서 최초 설정 안내가 사라지고 회원 로그인 화면이 표시되며 회원가입 화면 이동·로그인 화면 복귀가 정상 동작했다(런타임 오류 없음). 일반 배치 실행은 성공 후 `Press any key`에서 창이 유지되는 것도 직접 확인했다. 현재 실행기는 복원되어 있던 정수형 PID 기록과 호환되도록 포트·기본 경로를 별도 메타데이터로 기록한다.

## 2026-09-15 사내 HTTP 접속의 UUID 오류 수정

사내 IP의 HTTP 주소에서 `crypto.randomUUID is not a function` 오류로 작업대가 열리지 않는 문제를 수정했다. 이 환경에서 제공되지 않는 `randomUUID` 대신 브라우저의 `getRandomValues`로 UUID v4를 생성하는 공통 함수를 사용한다. 작업대 배치 요청, 진행 단계 추가, 로컬 실행 요청, 의미 매핑 위젯 생성에 같은 처리를 적용한다.

소스 실행은 수정된 프런트엔드를 반영하고 브라우저를 강력 새로고침한다. 정적 웹 배포는 새 프런트엔드 빌드를 기존 업데이트 절차로 반영한다. 인증·HTTPS·로컬 도우미의 기존 배포 요구사항은 유지한다.
