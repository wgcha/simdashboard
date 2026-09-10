# Windows 서버에 다른 사내 PC로 접속하기

기본 `start.bat`은 서버 자신만 접속하는 `127.0.0.1` 모드다. 사내 IP로 시험 접속하려면 기존 `.env`에 다음 값을 한 번 설정한다. `backend/.env`만 사용하는 설치는 그 파일에 설정한다. `.env.example`을 기존 `.env` 위에 복사하지 않는다.

```dotenv
WINDOWS_WEB_HOST=0.0.0.0
```

지원 값은 `127.0.0.1`과 `0.0.0.0`이며 미지정 시 로컬 모드를 유지한다. 프로세스 환경변수, 루트 `.env`, `backend/.env` 순서로 우선한다. 기존 계정·비밀번호·DB 연결 설정은 유지한다.

## 이번 수정 적용

직전 관리자 생성/8자 비밀번호 수정본까지 설치한 환경에서는 이번 변경에 새 패키지·웹 빌드·DB 마이그레이션이 없다. 프로젝트 폴더의 PowerShell에서 현재 브랜치를 확인하고 소스를 받는다. 게시 브랜치는 `codex/windows-one-click-deploy`다.

```powershell
git branch --show-current
git pull --ff-only
```

pull이 성공한 뒤 위 `.env` 한 줄을 추가/변경하고, 다음 순서로 실행한다.

```powershell
.\stop.bat
.\start.bat
```

사용자 지정 포트를 사용했다면 같은 포트로 시작한다. 예를 들어 웹 9456/API 9123 환경은 `start.bat -FrontendPort 9456 -BackendPort 9123`이다. `update.bat`은 기존 PID 기록의 사용자 지정 포트를 자동으로 이어받는다.

**`git pull`만으로 실행 중인 프로세스의 설정과 코드가 교체되지는 않는다. 이번에는 pull + 재시작으로 적용된다.** 시작 전 미적용 DB migration이 발견되거나 이전 버전에서 여러 변경을 한꺼번에 받는 경우는 `update.bat`으로 의존성·백업·migration·빌드·재시작을 함께 처리한다. 이후 일반 업데이트에도 `update.bat`을 사용한다. Git hook으로 pull 때마다 서비스나 DB를 자동 변경하지 않는다.

## 다른 PC에서 열 주소

`start.bat` 창의 `LAN` 주소 중 직원 PC에서 도달 가능한 사내 네트워크 주소를 사용한다. 여러 네트워크 어댑터나 VPN이 있으면 후보가 여러 개 표시될 수 있다.

```text
http://서버의사내IPv4:5173/workspace/overview
```

`0.0.0.0`은 서버의 수신 설정이며 브라우저에 입력하는 주소가 아니다. `127.0.0.1`은 각 PC 자신을 가리킨다. 포트를 생략하면 기본 HTTP 80으로 접속하므로 기본 웹 포트 `5173`을 포함한다. 사내 IP가 바뀌면 새 IP를 사용한다.

웹은 선택한 인터페이스에 열리고 `/api`·`/assets` 요청은 웹 서버를 통해 내부 API에 전달된다. 이 변경은 API의 수신 주소 `127.0.0.1`이나 PostgreSQL 수신 설정을 바꾸지 않는다. 직원 PC에 DB 계정이나 5432 포트를 공개할 필요가 없다. 설정 변경 전부터 실행 중인 프로세스는 재사용하지 않고 중지·재시작 안내를 표시한다.

## 연결 실패 점검

먼저 서버 PC에서 위 사내 IP 주소를 연다. 서버에서는 열리지만 다른 PC에서는 안 열리면 클라이언트 PowerShell에서 확인한다.

```powershell
Test-NetConnection -ComputerName 서버의사내IPv4 -Port 5173
```

`TcpTestSucceeded=False`이면 서버 Windows 방화벽, 두 PC 사이의 네트워크 정책, 잘못 선택한 IP를 확인한다. 배포 도구는 방화벽을 자동 변경하지 않는다. 서버 관리자 PowerShell에서 사내 연결 프로필과 허용 대역을 확인한 뒤 필요한 경우 다음 규칙을 추가한다.

```powershell
New-NetFirewallRule -DisplayName 'Simulation Dashboard LAN 5173' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5173 -Profile Domain,Private -RemoteAddress LocalSubnet
```

기존 허용 규칙이 있으면 중복 생성하지 않는다. 사용자 지정 웹 포트라면 그 포트로 바꾼다. 이 예시는 같은 서브넷만 허용한다. 다른 사내 서브넷은 관리자에게 실제 허용 대역과 네트워크 정책 확인을 요청한다. 공용 프로필까지 무조건 개방하거나 방화벽 전체를 끄지 않는다.

## 인증과 HTTPS

LAN 모드는 인증이 준비된 경우에만 시작한다. 최초 관리자가 없으면 서버 콘솔에서 계정을 설정한다. 인증 없는 로컬 개발 설정은 LAN 공개에 사용할 수 없다.

위 `http://IP:5173` 경로는 기존 Vite 실행을 이용한 사내 접속 시험용이다. HTTP에서는 로그인 정보가 암호화되지 않으며, 직원 PC의 로컬 도우미를 안정적으로 사용하려면 신뢰할 수 있는 HTTPS 주소가 필요하다. 상시 운영은 [Windows HTTPS 구성](windows-caddy-intranet.md)의 Caddy 또는 기존 사내 HTTPS 프록시로 `frontend/dist`와 내부 API를 제공한다. 이 경우 `WINDOWS_WEB_HOST=127.0.0.1`을 유지하고 HTTPS 공개 포트를 사용한다.

이미 `AUTH_COOKIE_SECURE=true`를 사용하는 환경에서는 HTTP LAN 모드로 낮추지 말고 기존 HTTPS 주소를 사용한다. HTTP 경로에서 Secure 쿠키를 사용할 수 없으므로 해당 조합은 시작 시 안내하고 중단한다. HTTPS 인증서 설치, 회사 방화벽·네트워크 정책 변경은 Git pull에 포함되지 않는다.

## 2026-09-10 변경과 검증 기록

프런트엔드·백엔드·DB 담당을 분리해 실행 주소, 동일 출처 API 연결, 계정 보존 계약을 함께 검토했다. 웹 수신 주소만 선택 가능하게 변경했고, `.env` 우선순위·Windows UTF-8 BOM 읽기·인증 준비 확인·기존 프로세스의 주소 불일치 감지·LAN 주소 표시를 추가했다. DB 스키마·마이그레이션 파일·의존성 lockfile 변경은 없다. `frontend/package.json` 변경은 자체 검사 명령 등록뿐이다.

| 검사 | 결과 |
|---|---|
| Python LAN 설정·기존 PostgreSQL 시작·배포 프로필 검사 | 103개 통과. 이후 Windows BOM 지원 추가 후 LAN 설정 18개 재통과 |
| Windows PowerShell 5.1 web-access 자체 검사 | 통과. 임시 `.env` 사용, 인증/쿠키 조건, 이전 PID 형식·호스트 변경, IPv4 후보 필터 확인 |
| 실제 LAN 인터페이스 HTTP 검사 | 통과. 같은 검증 PC의 비루프백 IPv4로 Vite 접속, `/api`·`/assets` 프록시와 쿠키 헤더 왕복 확인. 백엔드는 loopback stub 사용 |
| 프런트엔드 빌드 | 성공. 이번 변경 적용에 재빌드가 필요한 것은 아님 |
| 추가 기존 local-http 자체 검사 | 미통과. 변경하지 않은 legacy 요청이 강제 프록시를 우회했다는 기대 조건 불일치로 종료. 새 LAN 함수를 호출하기 전 단계이며 이번 수정의 원인으로 확인된 것은 아님 |

LAN 검사는 `frontend`에서 `npm run test:lan-proxy`, Windows 설정 검사는 `scripts/windows/web-access-self-test.ps1`로 재현한다. 실제 사내 서버·다른 직원 PC·회사 방화벽 통과는 이 로컬 검증에 포함되지 않는다. 기존 운영 `.env`나 DB를 수정하지 않았으며 테스트가 생성한 임시 서버는 종료했다.
