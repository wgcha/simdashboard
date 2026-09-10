# 개인 계정 설치·업데이트와 검증 기록

기준일: 2026-09-09. 로그인 첫 화면, 서버 최초 관리자 설정, 계정 보존·백업 검증을 구현했다. 실제 사내 서버의 설정·인증서·서비스 배치는 아직 적용하지 않았다.

신규 설치와 `ANALYSIS_DB_BACKEND` 미지정 실행의 기본 DB는 PostgreSQL이다. PostgreSQL 서비스와 app/owner 연결 설정을 먼저 준비한다. 기존 설치의 명시적인 DuckDB 설정은 보존하며, 백업·이관 검증 없이 DB 종류만 바꾸지 않는다. 개발·테스트용 DuckDB는 명시적으로 선택할 수 있다.

## 사용 흐름

- 최초 서버 관리자: `deploy.bat` 실행 → 배포 창에서 관리자 아이디·표시 이름·비밀번호 두 번 입력 → `start.bat` → 웹 로그인. `setup-accounts.bat`을 따로 선택할 필요가 없으며, 일반 회원가입이나 승인 대기를 거치지 않는다.
- 직원: 웹 첫 화면의 `회원 로그인` / `개인 회원가입` → 가입 → 관리자 승인 → 로그인 → 내 PC 설정 → 도우미 설치·연결.
- 기존 설치: 로그인 가능한 ACTIVE 비밀번호 관리자가 이미 있으면 새 계정 생성·비밀번호 변경 없이 재사용한다. 서버 PC에서 접속했다는 이유로 관리자 권한을 부여하지 않는다.
- 아이디: 영문·숫자·`.`·`-`·`_`, 3~80자. 대문자는 소문자로 정규화한다. 한글·공백은 거절한다. 비밀번호는 12~256자이며 앞뒤 공백도 비밀번호의 일부다.

## 서버 최초 준비와 기존 설치 업데이트

1. 신규 설치·재설치는 `deploy.bat`, 소스 업데이트는 `update.bat`으로 진입한다. DB 연결 대상을 기준으로 기존 데이터 유무를 확인하므로 소스 폴더가 새것이라는 이유로 신규 계정 DB로 취급하지 않는다.
2. 배포는 실행 환경 준비 후 앱을 중지하고, 기존 DB와 설정을 자동 백업한 뒤 마이그레이션·계정 준비를 진행한다. 백업 경로를 창에 표시하며 실패하면 DB 변경과 새 서버 시작을 중단한다. DB 초기화·교체·`--clean` 복원은 사용하지 않는다. [계정 백업과 복구](account-backup-and-recovery.md)를 따른다.
3. 최초 관리자 설정이 필요하면 같은 배포 창에서 입력을 받는다. 새 비밀키가 필요한 경우 도구가 생성하고 기존 `.env`의 다른 설정을 보존한다. 현재 쉘/서비스 환경이 인증 값을 덮어쓰고 있으면 해당 관리 설정을 먼저 정리한다. 기존 OIDC 설정은 자동 전환하지 않는다. `setup-accounts.bat`은 수동 점검·복구용 보조 진입점으로 남긴다.
4. `start.bat`으로 실행한다. 이 시작 경로도 계정 준비를 확인하고, 필요한 경우 터미널에서 최초 관리자 입력을 받는다. 비대화형 실행에서 준비가 안 됐으면 시작을 중단하며 성공으로 표시하지 않는다.
5. 기존 서버에서 설정을 바꿨다면 서버를 재시작한다. 실행 중인 Python 프로세스는 `.env` 변경을 자동으로 다시 읽지 않는다.
6. `/api/auth/status`의 `setup_required=false`, 비밀번호 모드의 `registration_enabled=true`를 확인한다. 관리자가 없는 상태에서는 일반 가입과 보호 API를 차단한다.

상태 확인만 할 때는 `setup-accounts.ps1 -NonInteractive` 또는 배포 Python으로 `backend/scripts/setup_accounts.py --check`를 실행한다. 준비 완료는 종료 코드 0, 준비 필요는 2, 설정 오류는 1이다. 상태 확인은 계정·비밀번호·DB 스키마·인증 설정을 변경하지 않는다. 동시 실행 방지를 위한 잠금 파일은 남아 있어도 다음 실행을 막지 않으며, 운영체제가 실제 잠금을 관리한다.

자동화 환경의 `deploy.ps1 -NonInteractive` / `update.ps1 -NonInteractive`는 관리자 입력이 필요하면 완료로 표시하지 않고 중단한다. 관리자 비밀번호를 임의 기본값으로 생성하거나 공개 가입 첫 사용자를 자동 관리자로 승격하지 않는다. PostgreSQL 최초 설치는 DB와 owner/app 역할·기본 권한을 먼저 준비해야 한다. 배포는 준비된 빈 DB의 스키마를 자동 생성하지만 PostgreSQL 서비스·DB·역할 자체를 새로 설치하지 않는다.

신규 관리자 생성은 DB에 저장한다. `.env`는 연결 정보·서명 비밀키 등 서버 설정만 보관한다. 기존 사용자 ID, 비밀번호 해시, 승인 상태와 권한을 설정 도구가 덮어쓰거나 초기화하지 않는다. `.env` 변경 전 백업을 만들며 Windows의 기존 접근 권한을 백업·교체 파일에 유지한다. 신규 파일은 소유자 접근으로 제한한다.

## Windows 사내 비밀번호 프로필과 HTTPS

`windows-password-intranet` 프로필을 추가했다. PostgreSQL, password 인증, secure cookie 조건을 검사한다. 기존 `windows-vm-intranet`의 OIDC 계약은 유지한다. 인증 없는 로컬 개발은 `DEPLOYMENT_PROFILE=local`과 `AUTH_ALLOW_INSECURE_LOCAL=true`를 명시한 경우에만 가능하다. 기본 disabled 설정은 업무 화면을 열지 않고 서버 설정 필요 상태가 된다.

다른 PC의 도우미 연결에는 직원 PC가 신뢰하는 HTTPS 주소가 필요하다. 기존 사내 프록시를 사용할 수 있고, Windows용 [Caddy 수동 인증서 템플릿](windows-caddy-intranet.md)도 제공한다. 템플릿은 정적 frontend 빌드와 API를 구분하고 backend의 보호된 `/assets`를 직접 공개하지 않는다. 인증서 발급·신뢰 배포·Windows 서비스 등록은 서버 운영 환경에서 별도로 수행해야 한다.

## 도우미 배포 파일 업데이트

도우미 ZIP과 manifest는 소스 Git에 포함하지 않는다. 계정 DB, 인증 설정과도 별개다. 빌드된 배포 폴더를 준비해 다음 중 한 경로로 가져온다.

```powershell
# 소스 업데이트와 함께 폐쇄망 반입 폴더에서 도우미 배포본 적용
.\update.ps1 -LocalHelperDistributionSource 'D:\releases\local-helper\windows-x64'

# 소스 업데이트와 함께 지정 HTTPS 릴리스에서 적용
.\update.ps1 -LocalHelperManifestUrl 'https://release.example/internal/distribution-manifest.json'

# 이미 소스를 업데이트한 서버에 도우미 배포본만 적용
.\scripts\windows\import-local-helper-distribution.ps1 -LocalHelperDistributionSource 'D:\releases\local-helper\windows-x64'
```

HTTPS URL은 운영자가 실제 게시한 릴리스 주소를 사용한다. 위 예시 URL에 파일이 게시되어 있다는 뜻이 아니다. 온라인·오프라인 옵션을 함께 지정할 수 없다. 경로, 용량, SHA-256을 확인하고 ZIP을 배치한 뒤 manifest를 마지막에 교체한다. 실패하면 기존 배포 파일을 유지하고 업데이트의 새 서버 시작을 중단한다. 배포 옵션 없는 기존 `update.bat`은 기존 배포 폴더를 보존하며 새 도우미를 가져왔다고 보고하지 않는다.

기본 배포 경로는 서버의 `dist/local-helper/windows-x64`, 변경하려면 `LOCAL_HELPER_DISTRIBUTION_DIR`를 설정한다. Python 서버와 가져오기 도구가 같은 설정을 읽는다. 배포 후 `/api/local-helper/distribution`의 `status=ready`를 확인한다.

## 2026-09-10 계정 백업 실패 수정

사내 업데이트에서 `Account backup preparation failed`가 보고되어 백업 경로를 점검했다. 기존 도구는 세부 원인을 숨겼으므로 사내에서 발생한 단일 원인은 아직 확정하지 않았다. 확인된 코드 문제와 진단을 다음과 같이 수정했다.

- 설치·이관·백업·복원의 PostgreSQL 도구 탐색을 공유한다. Windows 표준 설치 폴더도 자동 탐색하며, 명시한 `POSTGRES_BIN`은 `PATH`보다 우선한다. 오래된 `PATH` 도구로 인한 버전 불일치는 호환되는 실제 bin 경로를 지정해 해결한다.
- 미디어 테이블 도입 전인 정확한 0001~0007 revision은 마이그레이션 전에 검증된 전체 dump를 남긴다. 0007에 계정 테이블이 있다는 이유로 아직 없는 미디어 테이블을 조회하던 문제를 수정했다.
- 백업 자식 프로세스의 UTF-8 입출력을 명시하고, 원문 대신 고정 진단 코드·실패 단계·조치 안내를 출력한다. 보호된 `failure.json`은 연결 문자열과 비밀번호를 기록하지 않는다.
- 백업 실패 시 마이그레이션과 새 서버 시작을 중단하는 기존 계약을 유지한다.

관련 Python 회귀 테스트는 **69개 통과, 2개 건너뜀**이다. 별도 실제 PostgreSQL에서 현재 스키마 자동 백업과 0007 형태의 DB 전체 백업·새 DB 복원을 확인했다. 이전 스키마의 사용자 ID·비밀번호 해시·프로젝트 권한·revision이 일치했고 원본에는 마이그레이션을 실행하지 않았다. 테스트용 DB만 사용했으며, 실제 사내 서버에서의 재실행 확인은 별도다. 진단별 조치는 [계정 백업 실패 안내](account-backup-and-recovery.md#업데이트-중-계정-백업-실패)를 따른다.

## 2026-09-10 미디어 무결성 오류 후속 수정

사내 오류 코드가 `ACCOUNT_BACKUP_FAILED_MEDIA_INTEGRITY`로 확인됐다. 기존 파일을 지원하는 기본 `dual-read`와 자동 백업의 DB 전용 릴리스 검사 사이에 정책 충돌이 있었다. 사내 개별 미디어의 상태는 직접 조회하지 않았으나, 파일 방식 미디어·미생성 데모·정리 대기 정상 blob을 가진 실제 PostgreSQL 테스트 DB에서 동일 오류를 재현했다.

현재 스키마의 `dual-read` 배포는 별도 `analysis-canvas-deployment-postgresql` 형식으로 계정·권한·전체 DB와 기존 `backend/assets`, `video_example` 파일을 보존한다. DB 덤프와 목록의 동일 스냅샷, 파일별 해시와 ZIP 재읽기, 파일 변경·누락·링크·Windows junction 거부를 검증한다. 선택적 데모 미완성과 정리 대기 정상 blob은 상태를 명시해 그대로 보존한다. 실제 DB 내용 손상·누락 참조·고립 chunk·누락 일반 미디어 파일은 계속 중단한다. DB 전용 백업·복원·시작 검사는 유지하며 저장 모드를 자동 전환하지 않는다.

검증 결과는 **80개 통과, 3개 건너뜀**이다. 건너뜀은 이 Windows 환경에서 만들 수 없는 symlink fixture이며 Windows junction 검사는 통과했다. 실제 PostgreSQL 별도 DB에서 strict 실패를 재현한 뒤 dual-read DB 덤프·파일·데모 ZIP을 새 DB와 새 폴더로 복원했다. 계정 목록·비밀번호 해시·권한과 전체 미디어 목록이 같았고, 일반 미디어 원본 누락과 blob 해시 변조를 각각 주입했을 때 백업 실패로 중단됐다. 복구용 테스트 데이터만 사용했고 원본 계정 초기화·자동 미디어 이전·삭제는 하지 않았다.

계속 실패하면 `ACCOUNT_BACKUP_MEDIA`와 `failure.json.media_diagnostics`의 고정 reason·숫자로 구분한다. 새 배포 백업은 DB와 파일을 함께 복원해야 하므로 [dual-read 업데이트 백업과 복구](account-backup-and-recovery.md#dual-read-업데이트-백업과-복구) 절차를 따른다. 엄격한 `restore_postgres.py`에 넣으면 복구 전 해당 절차를 안내하고 중단한다.

## 이번 검증 기록

| 검증 | 결과와 범위 |
|---|---|
| 실제 PostgreSQL 17.11 최초 설치 | 임시 localhost:55439의 빈 DB에 0001부터 0022까지 적용 성공. 0022의 기존 baseline 테이블 중복 생성 오류를 발견하고 수정 |
| 최초 관리자 | 실제 터미널에서 아이디·표시 이름·비표시 비밀번호 입력, 즉시 ACTIVE 관리자 로그인 성공, 설정 확인 재실행 시 `.env` 바이트와 기존 설정 유지 |
| 실제 PostgreSQL 계정 흐름 | 미로그인 차단 → 가입 → 승인 전 차단 → 관리자 승인 → 개인 계정 로그인 → 비밀번호 변경·구 비밀번호 거절·새 비밀번호 성공 |
| 전체 DB 백업·새 DB 복원 | 별도 빈 DB 두 개에 복구 연습. 사용자 ID·비밀번호 해시·승인 상태·관리자 권한 및 실제 power 프로젝트 멤버십 일치, 제한된 app 역할에서 원래 비밀번호 로그인 성공 |
| 설정 파일·잠금 | Windows 신규 파일 보호, 기존 `.env`와 백업의 NTFS 보안 설명자 일치, 설정 프로세스 강제 종료 후 잠금 재획득 성공 |
| 브라우저 | Chromium 7개 통과. 실제 가입·승인·로그인·비밀번호 변경 2개와 최초 설정·오류 재시도·깊은 URL·아이디 검증 5개 |
| 백업·배포 회귀 | `test_local_helper_distribution.py`와 `test_media_backup_restore.py` 재검증: 23개 통과, 2개 건너뜀. 실제 PostgreSQL 복원은 위 별도 실행으로 검증 |
| 프런트엔드 빌드 | TypeScript·Vite production build와 architecture 검사 통과. Windows 검증 환경의 `.vite-temp` 쓰기 제한으로 `npm run build -- --configLoader runner` 사용. 큰 번들 경고는 남음 |
| HTTPS | Caddy 2.11.4와 임시 인증서로 localhost:54543 검증. 인증서 검증을 유지한 요청, SPA 새로고침/no-cache, JS/CSS, 보호 assets, Secure/HttpOnly/SameSite=Strict 로그인 쿠키, 실제 도우미 다운로드 성공 |
| 업데이트 | 임시 Git 저장소에서 기존 DB·설정 보존, helper 성공 순서, helper 실패 시 시작 금지, 잘못된 이중 소스는 정지 전 차단 |
| 도우미 가져오기 | PowerShell 5.1 오프라인 가져오기·재실행·변조·경로 탈출·정션·디렉터리 충돌 거절과 기존 배포본 보존 통과 |
| 배포 자동 계정 준비 | 배포 lifecycle 자체 검사 통과: 신규 환경 파일 보호, 기존 설정 유지, `backend/.env`만 있는 설치 보존, 백업 실패·관리자 입력 미완료 시 완료 표시 금지. Python 일반 출력이 종료 코드 판정에 섞이지 않도록 수정 |
| 자동 백업 회귀 | 신규 DB 미생성, 기존 DuckDB 계정 해시 보존, backend 상대 경로, 비정상 경로·DB 대상 불일치 거부, non-public 데이터 보존, 현재 백업 실패 시 레거시 경로로 우회하지 않음 등 9개 통과 |
| 실제 배포 자동 백업 | PostgreSQL 자동 백업을 새 DB에 복원하여 계정·비밀번호 해시·승인·프로젝트 권한 일치 확인. 기존 복원 도구의 manifest 검증 통과. Windows 디렉터리와 모든 백업 파일의 소유자 전용 ACL 확인 |
| 빈 PostgreSQL 자동 준비 | 준비된 빈 DB를 fresh로 판정해 불필요한 dump 없이 head까지 마이그레이션 성공. 제한된 app 역할 권한과 재실행 무변경 확인. startup migration 회귀 72개 통과 |
| 배포 안내 화면 | `deploy.bat` 안내로 변경 후 Chromium 5개 재통과, 390px 모바일 안내 확인 |
| PostgreSQL 기본값 | 설정·백업·시작 사전 점검 28개, 시작·마이그레이션 관련 78개 통과(일부 중복). DB 종류 미지정 시 PostgreSQL, 명시적 DuckDB 유지, 연결 문자열 누락·공백 차단, DB 종류 없는 기존 DuckDB 보호 확인 |
| 기본값 변경 후 배포 배치 | Windows PowerShell 5.1 lifecycle 실제 실행 통과. PostgreSQL 설정 실패 시 앱 중지 전 차단, 백업 실패 시 migration 차단, 관리자 설정 미완료 시 완료 표시 금지 확인. 테스트용 CMD의 실패 분기를 수정하고 재검증 |

브라우저 환경은 `http://127.0.0.1:15173`, API `127.0.0.1:18000`, 별도 테스트 DB다. Browser plugin not available: browser skill이 제공되지 않아 저장소 Playwright를 사용했다. desktop 및 390px mobile 화면에서 본문·상태·버튼·오류 안내를 확인했다. 정상 미로그인 401과 테스트에서 의도한 500 외에 페이지 오류나 Vite overlay가 없었다. 실제 응답의 `setup_reason=null`을 잘못 거절하던 오류는 통합 테스트에서 발견해 수정했다.

화면 증거는 `%TEMP%/simulation-workbench-auth-setup-desktop.png`, `simulation-workbench-auth-setup-mobile.png`, `workbench-personal-pc-evidence/personal-signup-desktop.png`, `personal-signup-mobile.png`에 저장했다. 테스트용 인증서는 시스템 신뢰 저장소에 설치하지 않았다. 운영 DB·실제 회원 데이터는 사용하지 않았다.

비밀번호 변경 시 기존 세션의 즉시 폐기, 메일 기반 계정 복구, 도우미 자동 업데이트와 코드 서명은 후속 범위다. 새 물리 직원 PC의 기업 보안 정책·사내 인증서 배포와 실제 서버 적용은 이번 로컬 검증에 포함되지 않는다.
