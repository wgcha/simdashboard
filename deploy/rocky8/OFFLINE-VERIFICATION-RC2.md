# Rocky 8.6 rc2 — 인증서 없는 HTTP 개발 모드 검증

- 릴리스 태그: `offline-rocky8.6-20260907-rc2`
- 빌드/게시 소스 커밋: `e5e96497afbeb6addc83d891e61231adce6bec48`
- 번들 식별자: `20260907-rc2-e5e96497afbe`
- 파일: `simdashboard-offline.tar.gz` (215,744,820 bytes)
- 로컬 위치: `dist/offline-rocky86-rc2/`
- SHA-256: `2c2d04745d449843aa65b1ca2b7316da86f3f55a41883f290459e17326e48d39`

## 변경 범위

`install-offline.sh --dev-http` / `install.sh --dev-http`를 명시하면 인증서 없이
HTTP 80으로 설치합니다. `rocky8-dev-http` 프로필 및 비보안 쿠키를 함께 적용합니다.
password 인증만 허용하고 OIDC는 거부합니다. 옵션 없는 기존 설치는 HTTPS/인증서/
Secure 쿠키 필수 조건을 그대로 유지합니다. DB·공유폴더·서비스 계정·무결성 검사는
완화하지 않았습니다. main과 rc1 첨부파일은 변경하지 않았습니다.

## 실제 확인

- 배포/프로필 회귀 테스트 51개 통과, shell 문법 및 템플릿 검사 통과.
- 인증서 경로 없음/빈값/예제 경로를 개발 모드에서 허용하고, HTTPS 기본 모드에서는
  읽을 수 없는 인증서와 비보안 쿠키를 여전히 거부함을 검증.
- 오프라인 wrapper의 `--dev-http` 전달, HTTP CORS/profile/cookie 값, OIDC/disabled
  인증 거부, PostgreSQL/readiness 요구 유지 검증.
- 실제 로그인 handler의 쿠키 생성 검사: 개발에서는 Secure 없음, HTTPS에서는 Secure
  있음, 두 모드 모두 HttpOnly/SameSite=strict 유지. 인증 조회/감사 기록은 가짜로 대체한
  단위 검사이며 실제 DB 로그인 성공을 의미하지 않음.
- 공식 Rocky 8.6 rootfs에서 Python 고정 SHA 및 RPM 서명을 확인하고 번들 생성.
  Python 3.12.13, wheel 38개, RPM 278개 포함. wheel 전체 설치 및 주요 모듈 import 통과.
- 최종 번들을 네트워크가 없는 namespace에서 실제 `--dev-http --check`로 실행:
  인증서/키 파일과 설정이 전혀 없어도 성공. 기존 HTTPS CORS/Secure 쿠키 설정이
  개발 모드로 전환됨을 테스트. `RC2_OFFLINE_HTTP_WITHOUT_CERTIFICATES_PREFLIGHT_OK` 출력.
  `/opt/simdashboard/runtimes`는 생성하지 않음. DB URL은 가짜이며 접속하지 않음.
- Rocky nginx 실행 파일로 HTTP 서버 템플릿 `nginx -t` 성공. 가상 root가 nginx UID로
  chown하지 못하므로 임시 문법검사 환경에만 매핑된 root UID와 시험용 로그/캐시 폴더를
  사용했음. 실제 배포 nginx 사용자 설정을 바꾸거나 서비스를 시작한 검사는 아님.

## 사내에서 남은 확인

전체 root RPM 설치, 실제 DB 연결/마이그레이션, systemd/nginx 서비스 시작, 브라우저
접속·로그인·공유폴더 결과 조회는 사내 서버에서 확인해야 합니다. rc1에서 확인된
노트북 PRoot의 nginx 소유권 변경 제한을 rc2가 해결했다고 주장하지 않습니다.
HTTP 개발 모드는 TLS만 제외하며 다른 사내 설치 제약을 제거하지 않습니다.

개발 전용 계정/데이터와 제한된 개발망에서만 사용하세요. HTTP 비밀번호·세션은
암호화되지 않습니다. 운영 전에는 새 릴리스에서 `--dev-http`를 빼고 실제 인증서와
HTTPS 설정으로 설치하며, 기존 HTTP 방화벽 허용 규칙은 담당자가 별도로 확인합니다.

실행 안내: [OFFLINE-README.ko.md](OFFLINE-README.ko.md).
