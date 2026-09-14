# Windows 컴퓨터 이름·사내 IP로 접속하기

인증된 Windows 설치는 기본적으로 사내 접속을 허용한다. `AUTH_MODE=password` 또는 `oidc`이고 `WINDOWS_WEB_HOST`가 없으면 웹을 `0.0.0.0`으로 실행한다. 인증 없는 개발 환경은 로컬 수신을 유지한다.

## 접속 주소

- `http://서버컴퓨터이름/home/`
- `http://서버사내IP/home/`

`start.bat` 실행 창에 실제 컴퓨터 이름과 사용 가능한 IP를 표시한다. 이름이 인식되지 않는 직원 PC는 IP를 사용한다. 별칭, DNS 또는 hosts 파일은 변경하지 않는다. `localhost`는 서버 자신의 접속 주소다.

## 기존 설치 업데이트

```powershell
git pull --ff-only
.\stop.bat
.\start.bat
```

기존 `.env`의 계정·DB 설정은 유지한다. `WINDOWS_WEB_HOST=127.0.0.1`을 명시했던 설치를 사내 접속으로 바꾸려면 해당 값만 `0.0.0.0`으로 변경한다. 명시한 `WINDOWS_WEB_BASE_PATH`는 그대로 유지하며, 생략 시 `/home/`을 사용한다.

명시적으로 설정하는 예:

```dotenv
WINDOWS_WEB_HOST=0.0.0.0
WINDOWS_WEB_BASE_PATH=/home/
```

웹 기본 포트는 80이다. `update.bat`은 실행 기록의 포트를 유지하므로 이전 기본 포트 5173도 그대로 이어받는다. 새 기본 포트 80으로 전환하려면 위와 같이 한 번 중지한 뒤 포트 인자 없이 `start.bat`을 실행한다. 사용자 지정 포트는 `start.bat -FrontendPort 9456 -BackendPort 9123`처럼 지정하며 접속 주소는 `http://서버컴퓨터이름:9456/home/`이다. API·DB는 외부로 직접 공개할 필요 없이 웹 프록시를 사용한다.

회사 방화벽과 네트워크에서 웹 포트 접속이 허용되어야 한다. 상시 HTTPS 운영은 기존 프록시 구성을 사용하고 `WINDOWS_WEB_HOST=127.0.0.1`을 명시한다. `AUTH_COOKIE_SECURE=true` 설정을 직접 HTTP LAN 접속을 위해 낮추지 않는다.

자세한 설정 우선순위와 검증은 [사내 접속 기본값](lan-access-persistence.md), [영상·와이드 화면 보완](lan-video-usability-followup.md)을 참고한다.

## 이전 검증 기록

아래는 기본 포트와 경로를 변경하기 전의 기록이며, 현재 실행 방법은 위 안내를 따른다.

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
