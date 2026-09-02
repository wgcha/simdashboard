# 개인 노트북 작업과 사내 릴리스 인수인계

- 기준일: 2026-08-31
- 목적: 개인 노트북에서 안전하게 끝낼 수 있는 코드·계약 검증과, 사내 환경에서만 가능한 PostgreSQL·권한·네트워크·배포 검증을 분리한다.
- canonical 운영 대상: [Rocky Linux 8 + nginx + systemd + PostgreSQL 18 ADR](adr/0004-canonical-production-deployment-target.md)

이 문서는 운영 DB나 사내 네트워크에 직접 접근하지 않는 개발자를 위한 handoff 문서다. 기존 절차의 정본은 [개발 작업 절차](development-workflow.md), [Rocky 배포 runbook](rocky8-deployment-runbook.md), [외부 배포 보안·백업 가이드](deployment-security-backup-guide.md), [SQL 통합 가이드](backend-sql-integration-guide.md)다. 이 문서는 그 문서를 복제하지 않고, 작업 경계와 증적 전달 형식만 정의한다.

## 1. 이번 노트북 작업에서 닫는 범위

노트북에서는 실제 운영 연결을 사용하지 않고, 기본 DuckDB와 disposable 테스트 자원만 사용한다.

| 범위 | 노트북에서 확인할 계약 | 사내 릴리스와의 관계 |
|---|---|---|
| 복구 lease | claim·renew·release의 owner/token/generation/expiry CAS, malformed·partial metadata 차단 | PostgreSQL 동시성 검증의 사전 조건 |
| finalization CAS | 활성 lease가 정확히 일치할 때만 `QUEUED → SUCCEEDED`와 lease clear를 같은 transaction에서 수행 | 실제 multi-worker recovery 권한을 부여하지 않음 |
| 실패 복구 | downstream event/dispatch/progress 실패 시 attempt·lease·관련 row 전체 rollback | PostgreSQL transaction 재현 테스트로 이어짐 |
| 이관 안전성 | active/expired/malformed lease, cyclic FK, schema manifest preflight가 write 전 차단되는지 확인 | 운영 DB 이관 승인 전 필수 evidence |
| 구조 | application/domain port와 SQL adapter 경계를 유지하고 router·scheduler에 자동 연결하지 않음 | 운영 기능 enablement 전 review 대상 |
| PPTX template | managed direct-child path, upload compensation, render containment, delete quarantine/rollback, ZIP/XML 제한 | Rocky runtime root와 backup/restore 실검증 전제 |

finalization은 runner를 다시 실행하지 않는다. 동일한 transaction에서 lease fencing, attempt 상태, 성공 event, dispatch, work-item progress, request status 동기화가 모두 성공해야 commit한다. stale owner/token/generation, 만료 lease, identity 불일치, 중간 SQL 오류는 fail-closed여야 한다.

다음은 이 노트북 작업의 범위가 아니다.

- retry endpoint, scheduler, 자동 rerun, worker daemon, 운영용 recovery API 공개
- 실제 회사 PostgreSQL, SSO/IdP, directory, proxy, CA, NAS/NFS/SMB 접근
- 운영 데이터에 대한 migration·backup·restore·legacy 파일 삭제
- Rocky host에서의 SELinux, firewalld, TLS 인증서, systemd·nginx 전환

## 2. 노트북 재현 명령과 전달할 증거

작업 시작 전 사용자 변경을 확인하고, 프론트엔드나 개인 진단 파일을 포함하지 않은 별도 증적 디렉터리를 사용한다.

```bash
git status --short

# Backend focused contract/integration
cd backend
../.venv-wsl/bin/python -m pytest -q \
  tests/test_batch_recovery_lease.py \
  tests/test_batch_recovery_finalize.py

# Migration/transfer safety contracts
../.venv-wsl/bin/python -m pytest -q \
  tests/test_postgres_transfer.py \
  tests/test_batch_recovery_lease.py \
  tests/test_batch_recovery_finalize.py

# PPTX template local contract/integration
../.venv-wsl/bin/python -m pytest -q \
  tests/test_report_templates_slice.py \
  tests/test_api.py::test_pptx_template_upload_placeholder_inspection_and_render

# Full backend regression (시간이 허용될 때)
../.venv-wsl/bin/python -m pytest -q

# Rocky bundle/template static validation
cd ..
bash deploy/rocky8/validate-templates.sh
```

focused 결과는 아래 표를 채워 change ticket이나 handoff 묶음에 함께 보관한다. `<...>`는 실행 후 실제 값으로 바꾸며, `skipped`는 운영 인증으로 간주하지 않는다.

| 검사 | 명령/증거 | 결과 |
|---|---|---|
| lease contract | `tests/test_batch_recovery_lease.py` | `<YYYY-MM-DD> / <passed> passed, <skipped> skipped` |
| internal finalization CAS | `tests/test_batch_recovery_finalize.py` | `<YYYY-MM-DD> / <passed> passed, <skipped> skipped` |
| transfer safety | `tests/test_postgres_transfer.py` | `<YYYY-MM-DD> / <passed> passed, <skipped> skipped` |
| source commit | `git rev-parse HEAD` | `<commit SHA>` |
| template/static check | `deploy/rocky8/validate-templates.sh` | `<exit 0 / failure detail>` |
| PPTX template slice | `tests/test_report_templates_slice.py` + legacy happy path + ownership contract | `2026-09-01 / 19 passed` |
| 1차 full backend | `../.venv-wsl/bin/python -m pytest -q` | `820 passed, 5 skipped, 16 capacity failures` (WSL `/tmp` reserve 진단) |
| 동일 실패 묶음 safe workspace 재실행 | absolute `TMPDIR` + `SIMDASH_IMPORT_SNAPSHOT_ROOT` 지정 | `50 passed` |
| 최종 full backend 재실행 | 위 safe workspace 명령 | `836 passed, 10 skipped` (새 PG 전용 gate 5 skips 포함) |
| PPTX slice 반영 후 full backend | `../.venv-wsl/bin/python -m pytest -q` | `2026-09-01 / 996 passed, 10 skipped in 686.52s` |

최종 `10 skipped` 중 새 PostgreSQL separate-connection gate의 `5 skipped`는 사내 전용 실행 가드에 따른 정상 결과이며 운영 합격 증거가 아니다. 사내 전용 DB에서 실제 5개 test가 통과한 별도 증적이 필요하다.

WSL의 `/tmp` tmpfs 여유가 기본 snapshot reserve(1 GiB)와 min-free(64 MiB)의 합보다 작으면 `BUNDLE_SNAPSHOT_RESERVE_UNAVAILABLE`가 정상적으로 발생할 수 있다. reserve를 낮추거나 기존 임시 파일을 삭제하는 대신, 결과·진단 디렉터리를 Git에 넣지 않는 전용 workspace로 분리해 전체 pytest를 실행한다.

```bash
cd backend
install -d -m 0700 "$PWD/.pytest-tmp/runtime" "$PWD/.pytest-tmp/import-snapshots"

# SIMDASH_IMPORT_ROOT가 설정되어 있다면 import-snapshots와 겹치지 않는
# 별도 absolute import root를 사용한다. 두 경로 모두 결과 디렉터리이므로 commit하지 않는다.
TMPDIR="$PWD/.pytest-tmp/runtime" \
SIMDASH_IMPORT_SNAPSHOT_ROOT="$PWD/.pytest-tmp/import-snapshots" \
../.venv-wsl/bin/python -m pytest -q
```

`.gitignore`의 `backend/.pytest-tmp/` 규칙을 유지하고, `SIMDASH_IMPORT_ROOT`와 snapshot root는 서로 같은 경로이거나 부모·자식 관계가 되지 않게 한다. `backend/.pytest-tmp/` 아래의 결과·WAL·실패 진단 파일은 commit하지 않는다.

전용 PostgreSQL을 노트북에서 일시적으로 띄울 수 있더라도 그것은 disposable 개발 증거일 뿐 사내 운영 인증이 아니다. PostgreSQL test를 실행할 때는 기존 `.env` DB나 5432 운영 유사 DB를 사용하지 말고 전용 DB 이름과 전용 app role을 명시한다.

```bash
cd backend
ANALYSIS_DB_BACKEND=postgresql \
ANALYSIS_TEST_POSTGRES=1 \
ANALYSIS_TEST_POSTGRES_DATABASE=simdashboard_recovery_test \
DATABASE_URL='postgresql+psycopg://<test_app_role>:<test_password>@<test_host>:<test_port>/simdashboard_recovery_test' \
../.venv-wsl/bin/python -m pytest -q -m postgres_integration
```

비밀번호는 명령행·문서·로그에 실제 값을 넣지 않는다. 가능한 경우 환경변수 대신 secret store 또는 일시적인 보호 파일로 주입한다.

사내 담당자에게 전달할 노트북 증거는 다음으로 제한한다.

- 테스트 실행 명령, 날짜, commit SHA, 결과 요약(`passed/skipped/failed`)
- migration head와 변경된 파일 목록
- lease/finalization focused 테스트 결과 및 실패 시 재현 SQL/오류 유형
- `deploy/rocky8/validate-templates.sh`와 backend architecture/OpenAPI 검사 결과
- 실제 비밀번호, token, cookie, OIDC 응답, proxy 인증정보, CA private key, 운영 데이터 dump는 제외

`git status --short` 결과에 기존 사용자 변경이 있으면 그 파일은 commit·증적 archive에서 제외한다. `.test-import-snapshots-transfer-safety/` 같은 진단 산출물은 삭제하거나 전달하지 말고, 생성 경로와 보존 정책을 담당자와 별도로 확인한다.

## 3. 사내 입력값과 책임자

아래 값은 문서나 저장소에 실제 값을 기록하지 않고 사내 secret/config 관리 체계에서 주입한다.

| 입력/승인 | 주 책임 | 필요한 값 또는 결정 | 완료 증적 |
|---|---|---|---|
| 운영 대상 | 인프라 | Rocky 8.10 host, DNS, 시간 동기화, CPU/RAM/disk, firewalld·SELinux 정책 | host preflight 출력과 승인 ID |
| DB 구축 | DBA | PostgreSQL 18 endpoint, database, admin/owner/app role, pool/connection budget | role grant/revoke와 `alembic_version` 확인 |
| 인증 | 보안/IdP | OIDC issuer/client/redirect, cookie domain, CORS, 계정 승인 절차 | 로그인·권한·세션 폐기 smoke |
| proxy/CA | 네트워크/보안 | outbound proxy URL, `NO_PROXY` 정규화, 조직 CA chain, 내부 DNF/PyPI/npm mirror | DNF·Python·Node·서비스 trust 검사 |
| 결과 입력 | 데이터/인프라 | `SIMDASH_IMPORT_ROOT`, mount 방식, service user read/traverse, quota/capacity | non-symlink·권한·mount probe |
| 보고서 템플릿 | 인프라/운영 | `/var/lib/simdashboard/report-templates`, service owner·0750, release symlink, backup 보관·복원 정책 | upload→render→delete smoke와 파일 checksum |
| 백업 | DBA/운영 | 보관 기간, 별도 저장소, backup encryption, restore 담당자 | dump/manifest·restore rehearsal |
| 복구 정책 | 애플리케이션 owner | lease TTL, owner naming, 수동 recovery 승인, retry/자동화 허용 시점 | 정책 승인 문서와 feature flag 계획 |
| 변경관리 | 릴리스 담당 | release ID, maintenance window, rollback 책임자·연락망 | change ticket와 승인 ID |

proxy URL, 계정 비밀번호, OIDC secret, directory token, TLS private key는 secret store 또는 root-only 권한 파일에서 runtime에 주입한다. `.env`, GitHub issue comment, shell history, systemd journal, `SHA256SUMS`에 넣지 않는다. [Issue #15](https://github.com/wgcha/simdashboard/issues/15)의 네트워크 요구사항은 입력 후보일 뿐이며 실제 주소·bypass·CA 경로는 사내 네트워크 담당자가 승인한 값을 사용한다.

## 4. 사내 PostgreSQL 검증 순서

모든 검증은 운영 DB가 아닌 이름이 분리된 disposable/staging DB에서 먼저 수행한다. 애플리케이션 app role은 migration을 실행하지 않고, owner/admin role은 서비스 환경파일에 남기지 않는다.

### 4.1 schema·migration·이관 preflight

1. release bundle의 checksum과 commit을 확인한다.
2. 빈 PostgreSQL 18 DB에 Alembic을 최신 단일 head까지 적용하고, 기존 revision DB에도 upgrade한다.
3. app role로 DB와 `public` schema의 `CREATE` 권한이 모두 `false`이고 migration 실행도 거부되는지 확인한다. `SIM_DASH_OWNER_ROLE`(기본 `simdashboard_owner`)은 app connection role로 거부하며, owner/admin credential은 서비스 환경에 넣지 않는다.
4. DuckDB→PostgreSQL transfer를 먼저 `--execute` 없이 기본 dry-run으로 실행한다. source manifest, target table/column, 0019 lease 컬럼·constraint·index, required checksum을 첫 write 전에 검사한다.
5. active·expired·malformed lease 또는 partial five-column lease schema가 있으면 dry-run과 execute 모두 non-zero로 종료하는지 확인한다. 원본과 target에 write가 없어야 한다.
6. `workflow_runs.batch_attempt_id ↔ batch_execution_attempts.workflow_run_id`와 `request_work_items.demo_run_id` cycle은 nullable staging → 양쪽 row 적재 → exact ID restore를 한 transaction에서 확인한다. orphan·mismatch는 hard blocker다.
7. full checksum·row count·relationship audit가 일치할 때만 execute commit을 허용한다.

상세 명령과 receipt 규칙은 [SQL 통합 가이드](backend-sql-integration-guide.md)와 [보안·백업 가이드](deployment-security-backup-guide.md)를 따른다. 대상 DB 이름과 backup 경로는 명령에 placeholder를 사용해 review 후 치환한다.

### 4.2 separate-connection lease/finalization concurrency

다음 cases는 반드시 서로 다른 PostgreSQL connection/session에서 실행한다. 단일 connection 또는 DuckDB 통과만으로는 release 승인으로 보지 않는다.

| case | 기대 결과 |
|---|---|
| 같은 attempt에 두 owner가 동시에 claim | 정확히 한 명만 lease 획득, 다른 쪽은 unavailable |
| 같은 owner/token의 repeat claim | idempotent, generation·expiry를 부적절하게 연장하지 않음 |
| 만료 lease takeover | 새 token과 다음 generation만 획득 |
| 이전 owner의 renew/release/finalize | 전부 lost, successor lease와 row를 변경하지 않음 |
| 유효 lease finalization | 정확한 owner/token/generation일 때만 `QUEUED → SUCCEEDED`, lease clear와 결과 row commit |
| 만료 직전/동시 finalization | 한 transaction만 성공하고 나머지는 lost; duplicate success event/dispatch 없음 |
| finalization 후 downstream 오류 | attempt 상태, lease, event, dispatch, progress, request status가 모두 원상 rollback |
| identity/status/partial metadata 오류 | write 전 fail-closed, orphan·민감 projection 없음 |

실행 test는 전용 marker와 전용 DB 이름을 사용한다. PostgreSQL separate-connection gate 모듈은 [`tests/test_postgres_batch_recovery_concurrency.py`](../backend/tests/test_postgres_batch_recovery_concurrency.py)이며, 개인 노트북에서는 모듈 수집과 `5 skipped`가 정상이다. 실제 PostgreSQL 실행 결과가 없으면 release 합격으로 기록하지 않는다.

```bash
cd backend
ANALYSIS_DB_BACKEND=postgresql \
ANALYSIS_TEST_POSTGRES=1 \
ANALYSIS_TEST_POSTGRES_DATABASE=simdashboard_recovery_test_change1234 \
DATABASE_URL='postgresql+psycopg://<test_app_role>:<test_password>@<pg18_host>:<pg18_port>/simdashboard_recovery_test_change1234' \
../.venv-wsl/bin/python -m pytest -q \
  tests/test_postgres_batch_recovery_concurrency.py \
  -m postgres_integration
```

위 명령은 아래 실행 가드를 모두 만족하는 전용 PostgreSQL에서만 실제 test를 수행한다: `ANALYSIS_TEST_POSTGRES=1`, `ANALYSIS_TEST_POSTGRES_DATABASE`와 `current_database()`의 exact 일치, DB 이름이 정확히 `simdashboard_recovery_test` 또는 `simdashboard_recovery_test_<ticket>` 패턴, `ANALYSIS_DB_BACKEND=postgresql`, 최소 2개 독립 pool connection, 기본 `SIM_DASH_APP_ROLE`(또는 승인된 설정 role), `SIM_DASH_OWNER_ROLE`(기본 `simdashboard_owner`)과 다른 non-superuser·no-createdb·no-createrole app role, database와 `public` schema `CREATE` 권한 모두 `false`, 코드 Alembic head, recovery fixture 전체 table의 `SELECT,INSERT,UPDATE,DELETE` 권한. 서비스 DB 이름은 거부된다.

```bash
# 개인 노트북: 수집은 5개, 실제 실행은 PG 가드 때문에 5 skipped가 정상
install -d -m 0700 "$PWD/.pytest-tmp/runtime" "$PWD/.pytest-tmp/import-snapshots"
TMPDIR="$PWD/.pytest-tmp/runtime" \
SIMDASH_IMPORT_SNAPSHOT_ROOT="$PWD/.pytest-tmp/import-snapshots" \
../.venv-wsl/bin/python -m pytest --collect-only -q tests/test_postgres_batch_recovery_concurrency.py
TMPDIR="$PWD/.pytest-tmp/runtime" \
SIMDASH_IMPORT_SNAPSHOT_ROOT="$PWD/.pytest-tmp/import-snapshots" \
../.venv-wsl/bin/python -m pytest -q tests/test_postgres_batch_recovery_concurrency.py
```

사내 PG에서 통과해야 하는 5개 case는 claim race 단일 승자, 동일 owner/token claim idempotency race, fenced finalization race와 private projection, 만료 predecessor의 successor 변경 차단, finalization integrity 오류의 전체 rollback이다.

합격에는 test 결과뿐 아니라 `pg_stat_activity`/connection 수, transaction rollback 여부, 성공 commit 수, stale owner의 변경 행 수(0), duplicate event·dispatch 수(0)를 함께 기록한다. 운영 DB를 대상으로 경쟁 테스트를 하지 않는다.

## 5. 사내 proxy·CA·Rocky/nginx/systemd/TLS 검증

Vite dev proxy(`VITE_API_TARGET`), outbound corporate proxy(`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`), nginx reverse proxy는 서로 다른 계층이다. 설정을 한 환경변수나 한 인증서로 재사용하지 않는다.

사내 네트워크에서 다음 순서로 검증한다.

1. 승인된 internal DNF/PyPI/npm mirror와 조직 CA를 사용해 인터넷 차단 상태에서 release bundle을 설치한다.
2. DNF, Python dependency retrieval, Node build artifact retrieval, systemd service runtime이 모두 CA chain을 신뢰하는지 확인한다.
3. `NO_PROXY`는 loopback·DB·내부 IdP·directory 등 실제 대상에 맞게 consumer별 문법으로 정규화하고, proxy 인증정보가 journal·process list에 노출되지 않는지 확인한다.
4. [deploy/rocky8](../deploy/rocky8)의 `install.sh --check`와 template validator를 실행한 뒤에만 설치한다.
5. nginx는 TLS 종료, SPA/static serving, `/api` loopback forwarding, forwarded header overwrite, body/timeout/buffering, media Range를 검증한다.
6. systemd는 root-only EnvironmentFile, app role만 포함된 runtime env, `RequiresMountsFor=SIMDASH_IMPORT_ROOT`, read-only import path, restart/health timeout을 검증한다.
7. TLS 인증서 chain, hostname, 만료 경고, HSTS/CSP, HTTP→HTTPS redirect와 방화벽을 확인한다.
8. `/api/health`, 로그인, 역할별 권한, 결과 조회/등록, audit, media Range smoke를 수행한다.
9. `/var/lib/simdashboard/report-templates`가 service-owned 0750이고 release의
   `backend/assets/report-templates`가 그 runtime root를 가리키는지 확인한 뒤,
   실제 service user로 PPTX upload→list→render→delete와 재시작 후 render를 검증한다.

현재 transfer/backup collector는 assets 아래 symlink를 일반적으로 거부하는 반면 Rocky의
report-template root는 의도적인 top-level symlink다. 사내 cutover 전에는 이 root를
별도 inventory/checksum/restore 대상으로 승인하거나 collector가 승인된 root symlink만
명시적으로 처리하도록 보강해야 한다. 이를 확인하기 전에는 템플릿 파일을 immutable
release 안으로 복사해 우회하지 말고 report-template 포함 backup/cutover를 fail-closed한다.

구체적인 install·상태 확인·rollback 명령은 [Rocky 배포 runbook](rocky8-deployment-runbook.md)과 [deploy/rocky8 README](../deploy/rocky8/README.md)를 실행 기준으로 삼는다. 이 문서에는 인증서·비밀번호·사내 host를 복사하지 않는다.

## 6. backup·rollback·중단 조건

배포 직전 custom-format PostgreSQL backup과 manifest를 만들고, 별도 빈 DB에 restore한다. restore 후 `alembic_version`, row count, relationship audit, media/blob inventory, app-role exact comparison을 기록한다. backup·restore·legacy cleanup 상세는 [보안·백업 가이드](deployment-security-backup-guide.md)의 receipt·7-day 보존 조건을 따른다.

애플리케이션 rollback은 이전 `current` release symlink로 되돌리고 health를 확인한다. DB migration은 자동 downgrade하지 않는다. schema가 이전 release와 호환되지 않으면 서비스 중지 후 수정 release를 배포하며, DB downgrade/restore는 DBA 승인과 검증된 backup으로만 수행한다.

다음 중 하나라도 발생하면 rollout을 중지하고 증적을 보존한다.

- migration head, target schema, lease constraint/index, checksum 불일치
- app role이 DDL을 수행하거나 owner/admin credential이 서비스 로그·환경에 노출
- active/partial/malformed lease가 transfer preflight를 통과함
- separate-connection 경쟁에서 두 owner가 성공하거나 stale owner가 row를 변경함
- finalization 중 오류 후 partial event/dispatch/progress가 남음
- backup restore inventory 또는 relationship audit 불일치
- proxy/CA trust 실패, TLS/forwarded header/secure cookie 실패
- nginx·systemd·health smoke 실패, import root 권한·mount·quota 불충족

lease scheduler, retry endpoint, automatic rerun, multi-worker recovery는 위 gate의 모든 합격 증적과 제품 owner의 별도 승인 전까지 enable하지 않는다. 노트북에서 테스트가 통과했다는 사실은 운영 recovery 권한이나 배포 승인을 의미하지 않는다.

## 7. 인수인계 완료 기준

사내 릴리스 담당자는 다음 묶음을 change ticket에 첨부한다.

- 테스트 commit SHA와 변경 파일 목록
- 노트북 focused/full test 결과와 known limitation
- PostgreSQL 18 migration·app-role·separate-connection concurrency 결과
- transfer dry-run/execute manifest, checksum, relationship audit, receipt
- backup dump/manifest와 빈 DB restore 결과
- proxy/CA/mirror preflight 결과(민감값 제거본)
- Rocky install `--check`, nginx/systemd/TLS/health smoke 결과
- 승인자, 실행자, 실행 시각, 중단·rollback 판단과 다음 담당자

이 묶음이 완성되기 전에는 상태를 “운영 가능”으로 기록하지 않고, “노트북 검증 완료 / 사내 release gate 대기”로 기록한다.
