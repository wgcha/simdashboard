# Rocky 8.6 오프라인 설치 파일 — 2026-09-07 검증 기록

## 전달 파일

- 위치: `dist/offline-rocky86/simdashboard-offline.tar.gz`
- 전송 검증: 같은 폴더의 `simdashboard-offline.tar.gz.sha256`
- 크기: 215,754,869 bytes (약 216 MB)
- 릴리스: `20260907T104903Z-0b9d13d6376e`
- 소스 커밋: `0b9d13d6376e22be69026c4c7f97647531a63f2f`
- SHA-256: `c1fed79afc202e3675fe1425d7769cd34febf055fb286542e6a10a9add72e27a`
- 대상: Rocky Linux 8.6 x86_64, 기존 PostgreSQL 18 사용. DB 자체는 포함하지 않음.
- 포함: 빌드된 프런트엔드, 백엔드, Python 3.12.13, wheel 38개, 공식 서명 RPM 278개.

파일은 로컬에서 생성했습니다. GitHub Actions workflow를 추가할 현재 토큰의
권한이 없어 새 브랜치를 원격에 게시하지 못했습니다. 이 설치 파일은 `git pull`로
받는 파일이 아니며 승인된 파일 전송 방식으로 반입해야 합니다. 기존 작업 중인
프런트엔드 변경은 포함하지 않고 위 커밋의 깨끗한 소스로 빌드했습니다.

## 확인한 결과

- 배포 관련 자동 테스트: 24 passed.
- 배포 shell 문법 검사 및 `git diff --check`: 통과.
- 공식 Rocky 8.6 amd64 이미지의 rootfs에서 패키지 제작. 이미지 digest:
  `sha256:b820932e70edf6559d190a1f2e7b19f080ff45a61fc485bb4aad2d1b38b64e5d`.
- Python 고정 SHA-256 및 모든 RPM 서명을 확인한 후 번들 생성.
- 같은 Rocky ABI에서 wheel 전체 설치 및 ssl/sqlite3/cryptography/psycopg/fastapi/duckdb import 성공.
- 최종 파일을 별도 network namespace(외부 네트워크 없음)에서 실제
  `install-offline.sh --check`로 검증: 무결성, Python 실행, wheel 전체 오프라인
  설치, 앱 설치 설정 검사 통과. 임시 런타임만 사용하고 `/opt` 런타임을 만들지 않음.
- 테스트 설정은 가짜 DB URL과 인증서 경로를 사용했습니다. DB 연결/TLS 통신은
  이 검사에서 시도하거나 검증하지 않았습니다. 실 비밀번호는 패키지에 없습니다.

## 아직 확인되지 않은 범위

**전체 OS 설치 및 서비스 시작 성공을 확인한 파일은 아닙니다.** 노트북에는 실제
root/Docker가 없어 PRoot를 사용했습니다. 새 Rocky rootfs에서 네트워크를 끈 DNF
의존성 해결·트랜잭션 검사·서명 확인은 성공했고 대부분 RPM 설치도 진행됐지만,
nginx의 `/var/lib/nginx` 파일 소유권 설정에서 PRoot 환경의 `chown` 오류로 전체
트랜잭션이 실패했습니다. 권한 검사를 끄거나 nginx를 제외해 성공으로 처리하지
않았습니다. 최종 `--check` 성공은 이 전체 설치 실패와 구분해야 합니다.

사내 실제 root 서버에서 확인할 항목:

1. 기존 DB 백업 및 PostgreSQL 버전/접속/마이그레이션 계정 확인.
2. 기존 설정 파일을 압축 해제 폴더 밖에 두고 root 소유, `chmod 600` 적용.
3. `bash install-offline.sh --config /실제/설정/파일 --check` 실행.
4. 위 명령에서 `--check`를 빼 실제 설치: 로컬 RPM 설치, DB 마이그레이션,
   nginx/systemd 시작 및 HTTPS/로그인 확인. 오류 시 강제 삭제 옵션을 붙이지 않음.
5. 공유 폴더 마운트와 서비스 계정 읽기 권한, 프로젝트→의뢰→버전별 결과 표시 확인.

실행 안내: [OFFLINE-README.ko.md](OFFLINE-README.ko.md).
시스템 Python 3.6은 교체하지 않습니다. 앱 릴리스와 전용 런타임만 서비스에서 사용합니다.
