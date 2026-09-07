# Rocky 8.6 오프라인 설치

대상: Rocky Linux **8.6, x86_64** 서버. root로 설치하며 앱은 비로그인 서비스
계정으로 실행합니다. 이 패키지는 빌드된 화면, 앱, Python 3.12.13, Python
의존성 전체 및 nginx 등 필요한 OS RPM을 포함합니다. 대상 서버에서 Git,
Node.js, uv, 외부 인터넷 다운로드가 필요하지 않습니다.

## 인증서 없이 개발용으로 설치 (rc2 이상)

이미 받은 rc1에는 이 옵션이 없습니다. rc2 이상 파일을 **새 폴더에** 풀고 사용하세요.
기존 압축 파일이나 `SHA256SUMS`를 수동으로 수정하지 않습니다.

설정 파일은 설치 폴더 밖에 둡니다. 이전 안내대로 바로 위 폴더에
`simdashboard-install.env`를 두었다면 아래 명령을 그대로 사용할 수 있습니다.
파일에서 `SERVER_NAME`은 실제 서버 IP 또는 접속 도메인으로 지정하고,
`CORS_ALLOWED_ORIGINS=`는 비워 두면 해당 HTTP 주소로 자동 설정합니다.
기존 DB 설정은 유지하고 개발 전용 계정을 사용하세요.

```bash
bash install-offline.sh --config ../simdashboard-install.env --dev-http --check
bash install-offline.sh --config ../simdashboard-install.env --dev-http
```

첫 검사가 성공한 다음 두 번째 명령을 실행합니다. 접속은 `http://서버IP/`입니다.
`--dev-http`는 인증서/키 설정을 읽지 않으며, 기존 예제 인증서 경로가 남아 있어도
이를 사용하지 않습니다. HTTP 80 포트와 password 로그인, 개발용 쿠키 설정을
함께 적용합니다. OIDC는 이 모드에서 허용하지 않습니다. DB·공유폴더·권한·무결성
검사는 그대로 유지됩니다. 인증서만 불필요한 것이며 DB 설치/연결 문제를 없애는 옵션은 아닙니다.

**HTTP는 비밀번호와 세션이 암호화되지 않습니다.** 제한된 개발망에서 시험 계정과
시험 데이터로만 사용하고 인터넷에 공개하지 마세요. 사내 정책이 허용하는 접근 범위를
확인하세요. 운영 전환 때는 새 릴리스에 `--dev-http`를 빼고 인증서와 HTTPS 설정으로
설치합니다. 기존 방화벽 HTTP 허용 규칙은 자동 삭제하지 않으므로 담당자가 별도 점검합니다.

## 사내 설치

Windows에서 받은 설치 압축 파일과 `.sha256` 파일을 승인된 경로로 서버에 복사합니다.
기존에 작성한 `install.local.env` 또는 `install.env`를 계속 사용할 수 있습니다.
아래 명령의 설정 경로는 실제 사용 중인 파일의 절대 경로로 바꿉니다. 설정 파일은 압축을
푼 설치 폴더 밖에 두고 root 소유, 권한 600으로 유지합니다.

```bash
sha256sum -c simdashboard-offline.tar.gz.sha256
tar -xzf simdashboard-offline.tar.gz
cd simdashboard-rocky8-offline-*
bash install-offline.sh --config /기존/프로젝트/deploy/rocky8/install.local.env
```

각 명령이 성공한 다음 다음 명령을 실행합니다. 마지막 명령이 로컬 OS 패키지
설치 → 전용 Python 준비 → 앱 설치 → DB 스키마 갱신 → 서비스 시작까지 진행합니다.
기존 시스템 Python 3.6은 변경하지 않습니다. 배포 전에는 기존 PostgreSQL
백업을 준비하세요. DB 마이그레이션은 앱 코드 롤백으로 되돌아가지 않습니다.

시스템 변경 전 확인만 하려면 마지막 명령에 `--check`를 붙입니다. 이 검사는
압축 무결성, Python 실행, 모든 Python 패키지의 오프라인 설치, 설정을 확인하며
임시 파일만 사용합니다. 대상 OS의 실제 RPM 충돌과 DB/TLS 서비스 동작까지
보장하는 검사는 아닙니다.

## 서버에 있어야 하는 것

- Rocky 8.6 x86_64와 systemd, root 권한. 다른 OS/CPU에는 이 파일을 사용하지 않습니다.
- 접속 가능한 PostgreSQL 18 및 기존 앱/마이그레이션 계정. DB 서버 자체는 포함하지 않습니다.
- 서비스 주소와 기존 환경설정. 기본 HTTPS 모드에는 TLS 인증서/키가 필요하며
  `--dev-http` 개발 모드에서는 불필요합니다. 운영 로그인 정책에 따라 사내 인증 서버가 필요합니다.
- 실제 결과 공유 폴더의 마운트와 서비스 계정의 읽기 권한.
- 서비스용 비로그인 계정을 생성할 수 있어야 하며, 생성 금지 환경은 기존 승인된
  서비스 계정과 기본 그룹을 `SERVICE_USER`/`SERVICE_GROUP`으로 지정합니다.

파일을 모두 반입하므로 외부 다운로드 제약은 제거하지만, 서버의 패키지 충돌,
권한·방화벽 정책, DB 연결과 같은 사내 환경 조건까지 제거하는 것은 아닙니다.
RPM은 Rocky 8.6 공식 보관 저장소 기준이며 서버의 사내 보안 패치와 충돌하면
강제로 덮어쓰지 말고 해당 서버 기준으로 패키지를 다시 만들어야 합니다.

## 패키지 제작과 검증

GitHub Actions의 `Rocky 8.6 offline installation package` 작업으로 생성할 수 있습니다.
고정된 Rocky 8.6 컨테이너에서 Python과 wheel을 검증하고, 공식 서명이 있는
OS RPM 의존성을 모두 모읍니다. 새 컨테이너의 **네트워크를 끈 상태**에서 RPM
설치와 Python/wheel/앱 설정 사전 검사를 통과한 파일만 artifact로 제공합니다.
실제 사내 DB·TLS·systemd·마운트와 함께 실행한 검증은 별도로 필요합니다.

Docker가 없는 제작 PC에서는 동일한 공식 Rocky 8.6 이미지의 rootfs를 격리 실행해
`ci-build-offline.sh`를 사용할 수도 있습니다. 검증에는 반드시 별도의 새 rootfs와
네트워크가 차단된 namespace를 사용합니다. 호스트 OS 패키지는 수정하지 않습니다.
이 방식은 systemd 서비스가 실제로 시작되었다는 검증을 대신하지 않습니다.
