# 계정 보존·백업·복구

회원 계정은 소스나 설치 폴더가 아니라 애플리케이션 DB에서 관리한다. `users`에 비밀번호 해시·승인 상태·관리자 권한을 저장하고, `project_memberships`에 프로젝트 권한을 저장한다. 소스 업데이트와 프로그램 재설치 시 기존 PostgreSQL DB 및 `.env`의 연결 설정을 유지해야 한다. 시작·업데이트 절차는 기존 계정을 초기화하지 않는다. 새 서버로 이관할 때는 전체 DB 백업을 복원한 뒤 복원된 DB에 연결한다.

## PostgreSQL 백업

마이그레이션·재설치·DB 이관 전에 백업한다. 전체 custom-format `pg_dump`에 계정 테이블과 비밀번호 해시를 포함하므로 복원 후 기존 비밀번호를 사용할 수 있다. `.dump`와 함께 생성된 `.manifest.json`을 한 쌍으로 보호된 백업 저장소에 보관한다. DB 백업에는 민감한 데이터가 있으므로 공개 배포 ZIP이나 소스 저장소에 넣지 않는다.

운영자의 보호된 환경에 `DATABASE_URL`을 설정한 후 실행한다. PostgreSQL 도구는 `POSTGRES_BIN`, `PATH`, Windows 표준 설치 폴더 순서로 찾는다. 표준 설치 폴더는 `%ProgramFiles%/PostgreSQL/<버전>/bin` 및 `C:`부터 `Z:`까지의 `PostgreSQL/<버전>/bin`이며, 자동 탐색 후보는 버전 숫자가 큰 순서로 확인한다. 다른 경로에 설치했거나 `PATH`에 오래된 도구가 있으면 서버 버전과 호환되는 실제 bin 폴더를 `POSTGRES_BIN`으로 지정한다. 비밀번호가 포함된 연결 문자열은 명령 예시·로그·manifest에 기록하지 않는다.

```powershell
.\scripts\postgres\backup-postgres.ps1 -OutputDir 'D:\simulation-backups'
```

manifest의 `account_inventory`에는 계정 수·상태별 수·관리자 수와 사용자 ID/권한의 SHA-256 요약값만 저장한다. 아이디·비밀번호 해시·토큰은 manifest에 넣지 않는다. 계정 검증 정보와 DB dump는 동일한 PostgreSQL 스냅샷을 기준으로 생성한다.

`deploy.bat`과 `update.bat`은 기존 DB를 확인하면 마이그레이션·계정 변경 전에 자동 백업을 진행한다. 백업 위치는 배포 창에 표시한다. DB를 조회할 수 없거나 백업 검증에 실패하면 신규 설치로 간주하지 않고 배포를 중단한다. 계정 테이블에 회원이 없더라도 다른 기존 데이터가 있으면 백업한다.

자동 백업은 `backups/accounts/<UTC 시각>-<고유값>/`에 저장된다. 바깥 `manifest.json`은 배포 판정·DB 백업·설정 파일 해시를 기록한다. `SIMDASH_MEDIA_STORAGE_MODE=database-only`의 현재 스키마 백업은 아래 엄격한 복원 도구와 호환되는 `database-*.dump`와 같은 이름의 `.manifest.json`을 포함한다. 기본 `dual-read`의 현재 스키마 백업은 DB 덤프에 `.assets.zip`을 추가하는 별도 배포 백업 형식이며, 아래 dual-read 복구 절차를 따른다. DuckDB는 `database.duckdb`로 저장한다. `.env`, `backend/.env`, `.postgres-owner.env`가 있으면 함께 보호된 설정 백업으로 보관한다. Windows는 소유자 접근 권한을 적용한다.

계정 테이블이 아직 없는 레거시 PostgreSQL도 전체 dump를 남기되 `legacy_inventory_status=unavailable_legacy_schema`로 표시한다. 계정 테이블이 있더라도 저장소에 정의된 Alembic 0001~0007 revision은 미디어 테이블 도입 전이므로 같은 전체 dump 경로를 사용하고 `pg_restore --list`로 검증한다. 이 백업은 현재 미디어·계정 검증 계약을 충족한 것으로 취급하지 않는다. 레거시 복구는 별도 빈 DB에서 해당 버전에 맞는 `pg_restore`와 스키마 점검으로 진행해야 한다. 현재 스키마의 백업 실패를 레거시 방식으로 우회하지 않는다.

배포와 무관한 정기 백업 스케줄과 보관 기간은 아직 자동 등록하지 않는다. 운영자가 사내 정책에 맞게 별도 설정하고, 별도 저장 장치로 복제하며 주기적으로 복구 연습을 해야 한다. 같은 디스크의 자동 백업만으로 디스크 고장까지 대비할 수는 없다.

## 업데이트 중 계정 백업 실패

`Account backup preparation failed`는 마이그레이션 전 백업 단계가 실패해 배포를 중단했다는 뜻이다. 2026-09-10 수정본부터 바로 위에 `ACCOUNT_BACKUP_FAILED code=... stage=...`와 `ACCOUNT_BACKUP_ACTION` 조치 안내를 출력한다. 보호된 백업 폴더가 준비된 경우 같은 내용을 `backups/accounts/<시각>-<고유값>/failure.json`에도 남긴다. 폴더 생성·권한 설정 자체가 실패하면 보고서가 없을 수 있다. DB 연결 문자열, 비밀번호, 자식 프로세스의 원문 오류는 진단 출력에 포함하지 않는다.

아래 코드에는 공통 접두사 `ACCOUNT_BACKUP_FAILED_`가 붙는다.

| 코드 | 확인할 사항 |
|---|---|
| `MISSING_POSTGRES_TOOLS` | `pg_dump`, `pg_restore` 설치 및 `POSTGRES_BIN`의 실제 bin 경로 |
| `POSTGRES_VERSION_MISMATCH` | 서버와 호환되는 클라이언트 도구를 `POSTGRES_BIN`으로 지정. 오래된 `PATH` 도구가 우선될 수 있음 |
| `DATABASE_CONNECTION_OR_AUTHENTICATION` | PostgreSQL 서비스, 연결 대상, 보호된 owner 연결 설정 |
| `DATABASE_PRIVILEGE` | 백업용 owner 역할과 기존 테이블 조회 권한 |
| `DATABASE_SCHEMA_OBJECT_MISSING` | 현재 DB revision과 누락된 스키마 객체. 검증된 백업 없이 마이그레이션을 먼저 진행하지 않음 |
| `FILESYSTEM_ACCESS_OR_DISK` | 백업 폴더 접근 권한, 디스크 여유 공간, Windows 폴더 보호 정책 |
| `MEDIA_INTEGRITY` | 추가 `ACCOUNT_BACKUP_MEDIA`의 숫자·reason으로 파일 누락, blob 손상, 참조 문제 등을 구분. DB 종류나 미디어 모드를 임의로 바꿔 우회하지 않음 |
| `UNKNOWN` | 출력된 stage와 진단 코드를 기록하고 해당 단계의 설정·전제 조건 확인 |

수정본을 반영한 뒤 기존 설치 폴더에서 `update.bat`을 다시 실행한다. 계속 실패하면 비밀정보 대신 `code`, `stage`만 공유해 원인을 좁힌다. DB 삭제, 백업 검사 해제, 최초 설치 강제 전환으로 해결하지 않는다.

미디어 실패는 `failure.json`의 `media_diagnostics`와 콘솔 `ACCOUNT_BACKUP_MEDIA`에 허용된 숫자·상태·고정 reason만 추가한다. 사용자 ID, blob ID, 원본 파일 경로는 출력하지 않는다. `UNBOUND_MEDIA_FILE_MISSING`은 파일 방식 미디어의 원본이 없다는 뜻이고, `MEDIA_BLOB_CORRUPT`는 DB 미디어 바이트의 해시 불일치, `MEDIA_DATABASE_REFERENCES_INVALID`는 누락된 DB 참조·고립 chunk·예상 밖 데모 식별자를 의미한다. `ASSETS_CHANGED`는 백업 중 파일 변경이며 앱·파일 작성 프로세스를 종료한 후 다시 확인한다.

## dual-read 업데이트 백업과 복구

2026-09-10 추가 수정: 기본 `dual-read`는 파일 방식 미디어를 정상 지원하지만 이전 자동 백업은 DB 전용 릴리스 검사를 먼저 요구했다. `blob_id`가 없는 정상 파일, 아직 DB에 생성되지 않은 데모, 내용이 정상인 정리 대기 blob 때문에 계정 업데이트까지 중단될 수 있었다.

현재는 배포 전에 저장 모드를 읽어 백업 계약을 선택한다. `dual-read` 백업 형식은 `analysis-canvas-deployment-postgresql`이며 `recovery_contract=database-and-assets-before-migration`를 기록한다. 계정·미디어 목록과 전체 덤프는 같은 읽기 전용 PostgreSQL 스냅샷을 사용한다. `backend/assets`와 프로젝트 루트의 `video_example`에 존재하는 전체 파일을 ZIP 안의 `assets/`, `video_example/`에 각각 저장한다. DB 파일 참조의 원본 존재 여부, 경로 범위, 링크·Windows junction, 파일별 SHA-256, ZIP 재읽기, 백업 전후 파일 목록과 내용을 검증한다. 파일 작성 프로세스가 중지되어 있어야 한다.

DB 손상 blob, 누락 DB 참조, 고립 chunk, 예상 밖 데모 ID, 누락된 일반 미디어 원본, 파일 변경·접근 오류는 계속 배포를 중단한다. 내용이 정상인 고립 blob은 덤프에 그대로 보존하고 `ORPHAN_BLOBS_INCLUDED`를 표시한다. 선택적인 합성 데모 목록이 덜 생성됐거나 데모 폴더가 없으면 `INCOMPLETE_DEMO_CATALOG_INCLUDED` / `DEMO_SOURCE_MISSING`를 기록한다. 이는 기존 상태를 보존했다는 뜻이며, 없던 데모를 생성하거나 DB 전용 미디어 준비 완료로 판정하지 않는다. 원본 미디어를 삭제하거나 자동 이전하지 않는다.

복구는 기존 운영 DB와 폴더를 덮어쓰지 않는 별도 환경에서 진행한다.

1. 바깥 `manifest.json`과 DB `.manifest.json`의 형식을 확인하고, DB dump 및 `.assets.zip`의 크기·SHA-256이 기록과 일치하는지 확인한다. `BACKUP_FAILED` 또는 성공 manifest가 없는 묶음은 사용하지 않는다.
2. 빈 복구 DB와 owner/app 역할을 준비한다. 보호된 접속 환경에서 `pg_restore --single-transaction --exit-on-error --no-owner --no-privileges`로 지정한 빈 DB에 dump를 복원한다. 운영 DB를 대상으로 `--clean`을 실행하지 않는다.
3. ZIP 경로에 절대 경로·상위 이동·링크가 없는지 확인하고 빈 임시 폴더에 해제한다. `assets/` 내용은 새 설치의 `backend/assets`, `video_example/` 내용은 새 설치 루트의 `video_example`에 배치한다. 빈 폴더를 대상으로 하며 운영 파일과 합치지 않는다.
4. 보호된 설정 백업을 참고해 새 DB 연결과 기존 인증 서명 키를 설정하고, 복구 환경의 `SIMDASH_MEDIA_STORAGE_MODE=dual-read`를 유지한다. 원래 버전에서 계정 목록·비밀번호 해시·권한 및 `media_inventory`가 백업 시점과 같은지 확인한다. 앱 역할 권한은 기존 `harden_postgres_privileges.py` 절차로 적용한다.
5. 원래 관리자·회원의 로그인과 파일 기반 미디어 조회를 확인한 뒤 업데이트를 진행한다. DB 전용 전환은 별도 미디어 이전·검증 작업이다.

`restore_postgres.py`는 이 형식을 DB 전용 백업으로 복원하지 않고 위 절차를 안내한다. 기존 `postgresql-custom`의 엄격한 복원 및 `database-only` 시작 검사는 유지한다.

## 새 DB로 복구·이관

별도로 만든 빈 DB에 복구를 연습한다. 소유자 역할의 `DATABASE_URL`과 제한된 앱 역할의 `SIMDASH_APP_DATABASE_URL`이 같은 복구 대상 DB를 가리키도록 보호된 환경에서 설정한다. 해당 DB와 역할을 먼저 준비하고 `POSTGRES_BIN`을 지정한다.

```powershell
.\scripts\postgres\restore-postgres.ps1 -Backup 'D:\simulation-backups\analysis-canvas-YYYYMMDDTHHMMSSZ.dump' -ConfirmDatabase simdashboard_restored
```

복구 도구는 dump SHA-256, 미디어 목록, 계정·권한 요약값과 제한된 앱 역할의 조회 결과를 확인한다. 예전 manifest도 지원하지만 `account_inventory`가 없으면 추가 계정 비교는 수행하지 않는다. 새 버전으로 백업하고 복원 후 실제 로그인을 확인한다.

`-Clean`은 대상 데이터를 삭제하는 명시적 복원 옵션이다. 일반 업데이트·시작·재설치에는 사용하지 않는다. 기존 DB를 삭제하거나 설치 폴더와 함께 제거하지 않는다.

`.env`의 연결 정보, `AUTH_SECRET_KEY`, OIDC 비밀정보는 별도의 보호된 설정 백업으로 보관한다. DB 복원으로 비밀번호 해시는 유지된다. 서명 키를 바꾸면 기존 웹 세션은 무효화되지만 사용자는 원래 비밀번호로 다시 로그인할 수 있다. 서버 주소나 도우미 인증 설정이 바뀌면 PC 연결을 다시 진행해야 할 수 있다.

## 로컬 개발용 DuckDB

프로그램을 정상 종료한 뒤 설정된 `ANALYSIS_DUCKDB_PATH` 파일을 보호된 위치로 복사하고 SHA-256과 소스 버전을 기록한다. WAL이 남아 있으면 먼저 정상 복구·종료한 뒤 백업한다. 실행 중인 DB 파일만 복사하지 않는다. DuckDB 백업은 PostgreSQL 복원용 dump와 서로 바꿔 쓸 수 없다.

## 검증 기록

실제 PostgreSQL 17.11에서 전체 백업을 별도 빈 DB 두 개에 복원했다. 사용자 ID·비밀번호 해시·승인 상태·관리자 권한·프로젝트 power 멤버십이 유지됐고, 제한된 앱 역할로 기존 관리자와 회원의 원래 비밀번호 로그인이 성공했다. 상세 범위는 [설치·업데이트와 검증 기록](personal-account-rollout-runbook.md)을 참고한다.
