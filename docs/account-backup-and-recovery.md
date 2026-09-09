# 계정 보존·백업·복구

회원 계정은 소스나 설치 폴더가 아니라 애플리케이션 DB에서 관리한다. `users`에 비밀번호 해시·승인 상태·관리자 권한을 저장하고, `project_memberships`에 프로젝트 권한을 저장한다. 소스 업데이트와 프로그램 재설치 시 기존 PostgreSQL DB 및 `.env`의 연결 설정을 유지해야 한다. 시작·업데이트 절차는 기존 계정을 초기화하지 않는다. 새 서버로 이관할 때는 전체 DB 백업을 복원한 뒤 복원된 DB에 연결한다.

## PostgreSQL 백업

마이그레이션·재설치·DB 이관 전에 백업한다. 전체 custom-format `pg_dump`에 계정 테이블과 비밀번호 해시를 포함하므로 복원 후 기존 비밀번호를 사용할 수 있다. `.dump`와 함께 생성된 `.manifest.json`을 한 쌍으로 보호된 백업 저장소에 보관한다. DB 백업에는 민감한 데이터가 있으므로 공개 배포 ZIP이나 소스 저장소에 넣지 않는다.

운영자의 보호된 환경에 `DATABASE_URL`과 `POSTGRES_BIN`을 설정한 후 실행한다. 비밀번호가 포함된 연결 문자열은 명령 예시·로그·manifest에 기록하지 않는다.

```powershell
.\scripts\postgres\backup-postgres.ps1 -OutputDir 'D:\simulation-backups'
```

manifest의 `account_inventory`에는 계정 수·상태별 수·관리자 수와 사용자 ID/권한의 SHA-256 요약값만 저장한다. 아이디·비밀번호 해시·토큰은 manifest에 넣지 않는다. 계정 검증 정보와 DB dump는 동일한 PostgreSQL 스냅샷을 기준으로 생성한다.

`deploy.bat`과 `update.bat`은 기존 DB를 확인하면 마이그레이션·계정 변경 전에 자동 백업을 진행한다. 백업 위치는 배포 창에 표시한다. DB를 조회할 수 없거나 백업 검증에 실패하면 신규 설치로 간주하지 않고 배포를 중단한다. 계정 테이블에 회원이 없더라도 다른 기존 데이터가 있으면 백업한다.

자동 백업은 `backups/accounts/<UTC 시각>-<고유값>/`에 저장된다. 바깥 `manifest.json`은 배포 판정·DB 백업·설정 파일 해시를 기록한다. 현재 PostgreSQL 스키마의 백업은 아래 복원 도구와 호환되는 `database-*.dump`와 같은 이름의 `.manifest.json`을 포함한다. DuckDB는 `database.duckdb`로 저장한다. `.env`, `backend/.env`, `.postgres-owner.env`가 있으면 함께 보호된 설정 백업으로 보관한다. Windows는 소유자 접근 권한을 적용한다.

계정 테이블이 아직 없는 레거시 PostgreSQL도 전체 dump를 남기되 `legacy_inventory_status=unavailable_legacy_schema`로 표시한다. 이 백업은 현재 미디어·계정 검증 계약을 충족한 것으로 취급하지 않는다. 레거시 복구는 별도 빈 DB에서 해당 버전에 맞는 `pg_restore`와 스키마 점검으로 진행해야 한다.

배포와 무관한 정기 백업 스케줄과 보관 기간은 아직 자동 등록하지 않는다. 운영자가 사내 정책에 맞게 별도 설정하고, 별도 저장 장치로 복제하며 주기적으로 복구 연습을 해야 한다. 같은 디스크의 자동 백업만으로 디스크 고장까지 대비할 수는 없다.

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
