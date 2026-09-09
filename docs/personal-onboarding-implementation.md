# 개인 회원가입과 PC 도우미 설치

- 기준일: 2026-09-09
- 상태: 계정·도우미 구성 요소와 서버 최초 관리자 설정·인증 전환·백업 복원 검증을 구현했다. 실제 사내 서버 반영은 별도다.
- 현재 설치·업데이트 기준: [개인 계정 설치·업데이트와 검증 기록](personal-account-rollout-runbook.md).
- 후속 검토: [개인 계정 첫 화면과 사내 전환 개발 계획](personal-account-entry-rollout-plan.md). 기존 disabled 설정에서 로그인·가입 첫 화면을 건너뛰는 문제와 배포 절차 누락을 다룬다.
- 근거: 사용자가 회원가입 기능과 개인용 로컬 도우미 개발을 요청했다.

## 사용자 흐름과 권한

비밀번호 로그인 화면에서 회원가입 → 관리자 승인 대기 → 승인 후 로그인 → 내 PC 설정에서 도우미 설치 → 최초 PC 연결 승인 → 개인 프로그램 등록·실행으로 이어진다. 기존 프로젝트별 실행 권한은 유지한다. 회원가입으로 관리자나 프로젝트 실행 권한을 자동 부여하지 않는다.

프로젝트가 아직 배정되지 않은 활성 일반 계정은 첫 로그인과 새로고침에서 `내 PC 설정`으로 진입한다. 프로젝트 bootstrap 조회를 시작하지 않아 미배정 프로젝트의 권한 오류에 막히지 않는다. 프로젝트 멤버십을 부여하면 기존 업무 화면을 사용할 수 있다.

비밀번호 인증 모드에서는 사용자가 아이디·표시 이름·비밀번호로 가입하고 PENDING 계정을 받는다. 기존 관리자 승인 기능으로 ACTIVE로 전환한다. disabled/OIDC 모드에서는 비밀번호 회원가입을 제공하지 않는다. 비밀번호 변경은 현재 비밀번호 확인 후 본인 계정에만 적용한다. 최초 관리자는 기존 서버용 create_user.py로 준비한다.

## 구현 계약

- POST /api/auth/register: username, display_name, password → user_id, username, account_status, message. 중복 아이디는 충돌 응답, 권한 필드 입력은 거절한다.
- POST /api/auth/password: current_password, new_password → ok. 로그인한 비밀번호 계정에 한한다.
- GET /api/auth/status: registration_enabled를 추가한다.
- 가입 입력 검증, 암호 해시, 비밀정보 없는 감사 기록, 가입 시도 제한을 서버에서 처리한다.
- 아이디는 영문·숫자·마침표(.)·밑줄(_)·대시(-)를 사용한 3~80자다. 한글과 공백은 허용하지 않는다. 가입과 로그인은 같은 길이 상수를 사용한다. 비밀번호는 12~256자로 앞뒤 공백을 보존한다. 잘못된 가입/비밀번호 변경 입력을 응답에 그대로 반환하지 않는다.
- 도우미는 Windows에서 실행 파일 및 필요한 런타임을 묶고 중앙 서버에서 배포한다. 사용자 PC에는 소스·Git·별도 Python 설치를 요구하지 않는다.
- 설치는 사용자 로컬 경로에 수행한다. 웹 주소·Origin·자동 시작 선택만 설정 파일에 담으며 계정/PC 토큰을 포함하지 않는다. 중앙 원격 주소는 HTTPS, 로컬 개발 주소만 loopback HTTP를 허용한다.
- 배포 파일 준비 상태와 무결성을 검증하고, 미준비 상태는 사용자에게 설치 실패와 구별해서 안내한다.
- PC 연결과 실행 권한은 기존 관리형 API를 유지한다. 계정 전환·연결 해제 시 다른 계정의 정보와 토큰이 남지 않도록 한다.

## 책임 분리

계정은 전용 schema/service/router와 frontend auth feature, 설치기는 전용 빌드·배포 모듈, 개인 PC 화면은 연결 및 설치 안내 조합을 담당한다. 생성 API는 코드에서 재생성한다. 기존 personal-pc-settings-plan.md의 사용자 변경을 보존하면서 이번 구현으로 대체되는 계획을 갱신한다.

## 검증과 운영 인계

가입 성공·중복·입력 오류·권한 주입·승인 전 차단·승인 후 접근·비밀번호 변경과 잘못된 비밀번호를 검증한다. 도우미 패키지 무결성·설치 스크립트·독립 실행·기존 연결 회귀를 검증한다. 브라우저에서 가입→대기와 개인 설정의 설치 흐름을 확인한다. 실제 사용자 서버 설정이나 운영 DB를 이번 작업에서 임의 변경하지 않는다.

운영 인증/프로필 변경, 배포 파일 생성·배치, 최초 관리자 준비 절차를 함께 안내한다. 이후 OIDC 이관은 기존 users.id를 유지하는 별도 계정 연결 절차로 진행한다.

## 서버 관리자의 최초 준비

1. 기존 DB 설정은 유지하고 서버에 `AUTH_MODE=password`, 32자 이상의 충분히 무작위인 `AUTH_SECRET_KEY`를 설정한다. 클라이언트가 접속하는 주소는 신뢰되는 인증서의 HTTPS로 제공하고 `AUTH_COOKIE_SECURE=true`를 사용한다. HTTP loopback은 같은 PC 개발에만 허용한다.
2. 기존 전역 관리자가 없을 때만 서버의 `backend/scripts/create_user.py --username admin --display-name 관리자 --role admin --global-admin`을 해당 배포 Python으로 실행해 최초 관리자를 만든다. 비밀번호는 명령 인수 대신 프롬프트에서 입력한다. 일반 사용자에게는 CLI 계정 생성이 필요 없다.
3. Windows 빌드 PC에서 아래 명령으로 사용자용 배포본을 만든다. 빌드 PC에만 PyInstaller가 필요하다. 결과는 `dist/local-helper/windows-x64/` 아래 ZIP과 `distribution-manifest.json`이다.

```powershell
.\scripts\windows\build-local-helper-distribution.ps1 -Version 0.1.0 -InstallBuildDependency
.\scripts\windows\local-helper-distribution-self-test.ps1 -DistributionDirectory .\dist\local-helper\windows-x64
.\scripts\windows\local-helper-installer-self-test.ps1 -DistributionDirectory .\dist\local-helper\windows-x64
```

4. 생성한 ZIP과 manifest를 서버에 함께 배치한다. Linux 서버에서는 Windows에서 생성한 파일을 그대로 사용한다. 기본 탐색 위치 외에는 `LOCAL_HELPER_DISTRIBUTION_DIR`로 폴더를 지정한다. 새 버전은 버전별 ZIP을 먼저 완성한 후 manifest를 마지막에 원자 교체한다. 사용 중인 ZIP을 덮어쓰지 않는다.
5. 서버와 프런트엔드를 업데이트·재시작한 후 `/api/auth/status`에서 `registration_enabled=true`, `/api/local-helper/distribution`에서 `status=ready`인지 확인한다. 관리자 화면의 `사용자·프로젝트 권한`에서 신규 계정을 승인한다. 계정 승인과 프로젝트별 실행 권한 부여는 별도다.

Rocky 운영 프로필은 password/OIDC를 지원한다. 기존 `windows-vm-intranet`은 OIDC 전용 계약을 유지하며 비밀번호용 `windows-password-intranet`을 별도로 추가했다. 실제 회사 서버의 인증서·서비스 배치는 자동 수행하지 않는다. 최초 관리자는 `setup-accounts.bat` 또는 서버 Python 설정 도구로 준비하고 일반 가입과 별도로 처리한다.

## 운영 범위와 후속 작업

- 사용자는 도우미 설치 BAT를 실행하면 현재 Windows 사용자 경로에 설치한다. 기본 시작 스크립트의 계정-PC 데이터는 `%LOCALAPPDATA%\SimulationWorkbench\local-runner`에 유지한다. Python 모듈 직접 실행의 별도 기본 경로는 사용자 홈의 `.simulation-workbench/local-runner`다.
- 실행 중인 도우미를 업데이트할 때는 먼저 실행 중인 작업을 마치고 도우미를 종료한다. 다른 계정/서버의 실행 중인 프로세스를 설치기가 임의 종료하거나 인수하지 않는다.
- 가입 시도 제한은 서버 프로세스 단위이므로 여러 worker 배포에서는 reverse proxy에도 공통 제한을 둔다.
- 이메일 인증/비밀번호 분실 메일, OIDC 계정 이관, 코드 서명 인증서와 자동 업데이트는 이번 구현 범위에 포함하지 않는다. 관리자에 의한 비밀번호 재설정은 기존 서버 도구를 사용한다.

## 검증 기록

| 검증 | 결과와 범위 |
|---|---|
| 계정·기존 보안·OIDC·관리형 연결 | 핵심 백엔드 18개 통과. 공백 비밀번호, 80자 아이디, 81자 거절, 승인 전 차단, 승인 후 비밀번호 변경·구/신 비밀번호 로그인, 입력 오류의 비밀번호 비노출을 추가 확인 |
| 배포 API | 5개 통과. 무인증 정적 배포, 경로 거절, 손상 거절, fingerprint 캐시 무효화, UTF-8 BOM, no-store 검증 |
| 로컬 도우미 | 23개 통과. frozen picker 명령 및 기존 실행·관리 연결 회귀 포함 |
| 시작 파일 생성 | Node self-test 5개 통과. URL/해시 입력, UTF-16LE payload, Windows PowerShell 구문 검증 |
| 실제 브라우저 | Chromium E2E 5개 통과. 회원가입→승인→로그인→개인 설정→새로고침→비밀번호 변경→구/신 비밀번호 로그인, 미설정 안내, 개인 프로그램 CRUD·자동 복원·해제 |
| 실제 설치 payload | PowerShell 7 및 Windows PowerShell 5.1에서 새 설치·실행 중 업데이트 거절·실패 복구·재설치 통과. production payload의 설치 경로·포트·데이터 경로만 테스트용으로 치환 |
| 배포 빌드 | TypeScript와 Vite production build 통과. 기존 큰 chunk 경고는 유지 |
| 구조 | architecture check와 git diff --check 통과. 기존 사용자 문서 변경 보존 |

브라우저 환경은 `http://127.0.0.1:15173` / 테스트 API `127.0.0.1:18000`, Chromium 1440×960 및 390×844다. Browser plugin not available: 해당 browser skill이 없어 저장소 Playwright를 사용했다. 최초 Vite 임시 파일 접근 오류는 동일 테스트의 권한 있는 실행으로 해결했다. 페이지 식별·비어 있지 않은 본문·오류 overlay 없음·상호작용·가로 넘침·스크린샷을 확인했다. 미로그인 401과 도우미 미실행 연결 실패는 기대한 상태이며 미처리 page error는 없었다.

화면 증거는 검증 PC의 `%TEMP%\workbench-personal-pc-evidence`에 저장한다: `personal-signup-desktop.png`, `personal-signup-mobile.png`, `personal-pc-first-setup.png`, `personal-pc-connected-desktop.png`, `personal-pc-mobile.png`, `personal-password-changed.png`. `personal-pc` E2E의 설치 파일 metadata는 고정 fixture이며 실제 ZIP·설치 실행은 별도 native harness로 검증한다.

Windows 독립 배포본은 `dist/local-helper/windows-x64/SimulationWorkbenchLocalHelper-0.1.0-windows-x64.zip`에 생성했다(약 20.3 MiB). 운영 서버, 사내 인증서, 새 물리 PC의 기업 보안 정책/코드 서명 신뢰는 이번 로컬 검증으로 확인하지 않았다. 후속 작업에서 실제 PostgreSQL 17.11의 신규 마이그레이션·가입·승인·로그인·백업 복원을 검증했다. 최신 결과는 [설치·업데이트와 검증 기록](personal-account-rollout-runbook.md)을 따른다.

최종 배포본 SHA-256: `2c5c0c5e0bec1ccb635ed0dcf1fe7b2b0f1d06c53a5d43f749abb5e872808979` (21,289,514 bytes). 빌드 smoke 프로세스와 설치 harness의 테스트 프로세스는 종료했고 사용자 기본 도우미 경로에는 테스트 연결을 남기지 않았다.
