# 프로그램 정리 및 개발 계획

- 기준일: 2026-09-01
- 상태: 실행 계획 + Run Identity V2·report layout·PPTX template·variable catalog·import-schemas vertical slice 기준선 반영
- 범위: 구조 정리, DB·결과 수집, 확장자, proxy, 사내 배포, 기술 우선순위

## 1. 결론

전체 제로베이스 재개발은 선택하지 않는다. 기존 API·DB migration·권한·결과 import·dashboard·보고서·배포 테스트를 보존하면서 신규 기능부터 독립 V2 경계로 만들고, 변경이 필요한 legacy 기능을 순차 이전하는 Strangler 방식을 사용한다.

```text
현재 동작 계약 고정
  → 신규 기능은 V2 모듈로만 개발
  → 수정이 필요한 legacy 기능을 같은 도메인 V2로 이전
  → API/E2E 호환 검증
  → 이전 완료된 legacy 코드 제거
```

전면 재개발은 실제 사용자·운영 데이터가 없고, 현재 기능의 절반 이상을 폐기하며, API·DB 호환도 필요 없는 경우에만 다시 검토한다.

## 2. 이번 감사에서 확인한 현재 상태

### 유지할 기반

- React/TypeScript/Vite와 FastAPI/Python 3.12
- DuckDB 로컬 adapter와 PostgreSQL 18 운영 profile
- Alembic migration, app/owner 역할 분리, connection pool budget
- OpenAPI snapshot과 생성 TypeScript client
- project scope 권한, password/OIDC, menu policy, audit log
- 업무 유형·작업계획·결과 layout snapshot
- scalar/curve/media import, DB blob chunk와 Range streaming
- dashboard/report version, Playwright E2E
- Rocky nginx/systemd/SELinux 설치·rollback 기반

### 즉시 정리할 불일치

| ID | 현재 증거 | 영향 | 우선순위 |
|---|---|---|---|
| DOC-01 | 현재 문서와 과거 plan이 같은 디렉터리에 혼재 | 개발자가 낡은 경로·명령을 사용 | P0 |
| DB-01 | migration head를 코드 graph에서 동적으로 읽고 PostgreSQL의 누락·stale revision을 fail-closed하도록 verifier와 회귀 테스트를 반영 | 구현 완료, 실제 운영 DB release gate 검증 필요 | P0 완료 |
| IMP-01 | 중앙 format detector/loader와 normalized contract를 사용하고 canonical Master Refresh·typed folder example·수동 `SUMMARY_RESULT` JSON/CSV·Radioss mesh CSV를 공통 UoW로 적재 | schema와 맞지 않던 legacy `result_files` persistence service/repository를 제거하고 parser compatibility만 유지 | P1 진행 |
| IMP-02 | bundle fingerprint, load-case advisory lock, importer snapshot/rehash·workload limits와 Run Identity V2 구현 | `0017_run_identity_v2`, scoped source-version ledger, exact existing identity, `SKIP`/`REJECT`/immutable `REPLACE`, legacy append과 Master `REPLACE` fail-closed를 반영했다. AP-1 atomic publisher/marker와 AP-2 dedicated `0700` snapshot workspace, reserve+min-free app capacity gate, cross-worker single-refresh gate를 코드·focused 계약 검증으로 반영했다. AP-2 PostgreSQL 18.6 disposable live gate도 전용 2-session BUSY/reacquire/close-release로 확인했다. Rocky host/NFS·SMB mount와 filesystem quota 적용은 release gate다. | P1 진행 |
| IMP-03 | load case별 import history/status/retry API와 DataWorkspace UI 구현 | `folder_import_jobs` 이력, V2 revision/run enrichment, resource-scoped 조회, target-fixed 관리자 재시도 및 process-local worker lock을 반영했다. disposable PostgreSQL history gate 기록을 유지한다. | P1 완료 |
| DEP-01 | Rocky install env·service EnvironmentFile·systemd read-only path에 `SIMDASH_IMPORT_ROOT` wiring과 외부 mount/read preflight를 반영 | 구현 완료, 실제 Rocky host release gate 검증 필요 | P0 진행 |
| DEP-02 | Rocky 8 + nginx + systemd + PostgreSQL을 canonical target으로 ADR 확정하고 Windows를 compatibility profile로 명시 | 문서 결정 완료 | P0 완료 |
| DEP-03 | Windows는 설치/개발 실행과 DB 이관 호환성은 있으나 HTTPS reverse proxy·service·TLS·rollback 운영 자동화 없음 | Windows one-command 운영 배포는 지원 범위에서 제외 | 범위 제외 |
| DEP-04 | 사내 proxy는 Windows setup 중심이고 Rocky DNF/CA/offline RPM 계약이 불완전 | 폐쇄망 설치 재현성 부족 | P1 |
| SEC-01 | nginx forwarded header, trusted host, security header 정책 보강 필요 | audit IP 신뢰와 외부 노출 hardening 부족 | P0/P1 |
| ACC-01 | 프로젝트 초대·외부 directory lifecycle, 독립 assignee 후보 query, account status/global-admin command와 menu policy vertical slice를 반영 | access router를 composition-only로 전환하고 코드·focused 계약 검증 완료 | P1 완료 |
| ARC-01 | `main.py`, `database.py`, workbench router/repository, `App.tsx`가 큼 | 기능 추가 시 충돌·회귀 비용 증가 | P1 |
| API-01 | 일부 성공 응답이 익명 OpenAPI schema | frontend runtime adapter 수동 검증 지속 | P1 |

## 3. GitHub Issues 병합 상태

private GitHub Issues는 요구사항의 출처이고, 이 저장소 문서와 자동 테스트는
실행 계약의 정본이다. 아래 이슈의 사실을 반영했지만, upstream 디렉터리·확장자
목록을 곧바로 import 허용 목록이나 배포 구현으로 해석하지 않는다.

| 추적 키 | 이슈 | 주제 | 현재 반영과 경계 | 반영 문서 |
|---|---|---|---|---|
| GH-SPDM-FOLDER | [#13](https://github.com/wgcha/simdashboard/issues/13) | SPDM 폴더 구조 | SPDM은 upstream 의뢰 발견용 폴더 스키마다. 현재 실행 가능한 결과 import root/manifest와 별개이며 직접 import하지 않는다. | `storage-folder-and-file-contract.md`, `work-type-request-results-and-master-refresh.md` |
| GH-EXTENSIONS | [#14](https://github.com/wgcha/simdashboard/issues/14) | source/solver/result/report 확장자·명명 inventory | inventory는 producer/보관 기준이다. 현재 parser/upload 허용 확장자는 코드의 좁은 allowlist만 따른다. | `storage-folder-and-file-contract.md` |
| GH-LINUX-PROXY | [#15](https://github.com/wgcha/simdashboard/issues/15) | Linux 사내 proxy·조직 CA 요구사항 | `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`와 조직 CA를 입력 요구사항으로 정규화했다. Rocky proxy/CA 설치 자동화는 아직 없다. | 이 문서, `development-workflow.md` |

이슈에만 운영 계약을 남기지 않는다. 확정된 내용은 저장소 문서·환경 예제·자동
테스트에도 복제한다. 자격 증명, 프록시 URL의 사용자 정보, 인증서 private key는
이슈 본문·문서·Git에 넣지 않는다.

## 4. 목표 구조

### Backend 기능 단위

```text
backend/app/modules/<feature>/
├─ domain/          model, policy, port
├─ application/     command, query, transaction orchestration
├─ adapters/
│  ├─ http/         router, request/response mapping
│  └─ persistence/  DuckDB/PostgreSQL SQL adapter
└─ tests/            unit, contract, integration
```

현재 공용 `domains/application/adapters` 구조를 한 번에 이동하지 않는다. 먼저 `result_ingestion`, 다음 `workbench`, 이후 `access`, `reports` 순서로 응집된 기능 경계를 만든다.

### Frontend 기능 단위

```text
frontend/src/features/<feature>/
├─ Page/Workspace
├─ components/
├─ model.ts
├─ api.ts
├─ useFeatureController.ts
└─ *.css
```

URL은 route registry, 서버 계약은 `shared/api`, 공용 UI만 `shared/ui`가 소유한다. feature 간 내부 상대 import를 만들지 않는다.

## 5. 단계별 실행 계획

### Phase 0 — 계약과 배포 기준선(P0)

목표: 기능 추가 전에 잘못된 기준으로 개발하지 않도록 현재 계약을 고정한다.

#### P0-01 문서·이슈 기준선

- `docs/README.md`에서 현재 기준/기능 기록/과거 계획을 구분한다.
- 실제 구조, 개발 절차, 저장 계약을 canonical 문서로 지정한다.
- [완료] GitHub Issues #13~#15의 추적 표와 적용/미적용 경계를 기준 문서에 반영한다.
- `GOAL.md`와 과거 roadmap에는 historical banner를 추가한다.
- 오래된 `frontend/src/generated` 경로를 `frontend/src/shared/api/generated`로 수정한다.

완료조건:

- README에서 설치→구조→개발→배포 문서로 4번 이내 이동 가능
- 모든 기준 문서의 상대 링크 검사 통과
- 계획과 현재 구현 상태가 명시적으로 구분됨

#### P0-02 DB·import 기준선

- Alembic 현재 head를 단일 source로 조회하게 verifier를 수정한다.
- `mappings` manifest를 신규 canonical 형식으로 결정한다.
- 중앙 manifest format detector/loader에서 canonical `mappings`와 legacy
  `result_files`를 정확히 분리하고, mixed/unknown/잘못된 importer/root
  escape/symlink를 fail-closed한다.
- 기존 `ManifestParser` 이름은 legacy parser alias로 유지한다.
- ID 폴더 예제와 실제 import 통합 테스트를 추가한다.
- extension/MIME/크기/magic/checksum/path 표를 코드와 일치시킨다.

현재 P0 완료 범위는 manifest format과 경로 경계를 고정하는 데까지다.
canonical Master Refresh와 `/folder-import/example`은
`ResultIngestionCommand`와 `ResultIngestionUnitOfWork`를 통해 normalized payload,
공통 validation, persistence, status sync를 하나의 single-connection transaction으로
처리한다. malformed legacy parser output은 normalized contract에서 fail-closed한다.

일반 수동 `SUMMARY_RESULT` JSON/CSV와 `Radioss` mesh CSV upload는
target-qualified `source_name`과 content checksum을 사용해 공통 UoW로 이관되었다.
재시도는 `SKIPPED`하고 write transaction 안에서 권한을 재확인하며
`RESULT_IMPORTED` audit event를 함께 기록한다. Radioss adapter는 scalar,
time-series/curve, `result_locations`를 동일 transaction에 전달한다.

canonical schema에 없는 `result_import_jobs` 테이블과 `analysis_runs` 확장 컬럼을
참조하던 legacy `ResultImportService` persistence와 repository는 제거했다. legacy
`result_files` manifest schema, `ManifestParser` alias와 normalized parser adapter는
호환 전용으로 유지하고, 결과 쓰기는 canonical UoW로 한정한다.

완료조건:

- checked-in 예제를 실제 parser가 읽음
- scalar/curve/media/blob 저장과 두 번째 Refresh `SKIPPED` 검증
- traversal/symlink/MIME 오류가 부분 DB 행 없이 실패
- DuckDB와 PostgreSQL profile의 결과 계약 동일

#### P0-03 운영 target ADR (완료)

[`adr/0004-canonical-production-deployment-target.md`](adr/0004-canonical-production-deployment-target.md)의 결정에 따라 Rocky Linux 8 + nginx + systemd + PostgreSQL을 canonical 운영 target으로 확정하고 Windows를 개발·DB 이관 compatibility profile로 유지한다. Windows 운영이 필수라면 별도 `deploy/windows/` 프로젝트와 ADR amendment가 필요하다.

완료조건:

- [완료] 하나의 canonical 운영 target을 ADR로 확정
- [완료] README, security/backup guide, deploy runbook의 표현 일치
- [완료] target 밖 profile의 지원 수준과 미지원 기능 명시

#### P0-04 Master Refresh 운영 연결

구현됨:

- Rocky install config와 service EnvironmentFile에 `SIMDASH_IMPORT_ROOT`를 추가했다.
- 기본 local root에 service user 읽기 권한, systemd read-only path, SELinux
  `var_lib_t`와 mount 준비 의존성을 적용했다.
- external mount의 존재·재귀 접근 권한 preflight를 추가하고 NAS/SMB/NFS mount
  정책을 운영 승인 항목으로 정의했다.

남은 항목:

- Rocky VM deploy smoke에서 실제 예제 manifest 하나를 import한다.

완료조건:

- 새 VM 설치 후 설정 파일 하나로 Refresh 동작
- 앱 release 교체와 무관하게 결과 root 유지
- root 밖·symlink·다른 project context 접근 차단

### Phase 1 — 결과 수집·미디어 일원화(P0/P1)

#### P1-01 canonical ingestion service

상태: 부분 완료. canonical Master Refresh, typed folder-import example, 수동
`SUMMARY_RESULT` JSON/CSV와 Radioss mesh CSV의 공통 command/domain
port/application orchestration/SQL UoW 이관은 완료했다. schema와 맞지 않던 legacy
`result_files` persistence service/repository는 제거하고 parser compatibility만
유지한다.

- P0에서 고정한 중앙 manifest detector/loader를 canonical importer와 legacy
  adapter가 명시적으로 사용한다.
- discovery/preflight/parser와 persistence/status sync를 command/UoW 경계로 분리했다.
- typed folder import와 Master Refresh가 같은 validation·persistence UoW를 사용한다.
- Radioss parser adapter는 scalar, time-series/curve, `result_locations`를 canonical UoW result contract로 전달한다.
- parser adapter만 결과 형식별로 교체한다.
- importer의 private snapshot/rehash와 파일·행·포인트·manifest 개수 제한은 구현했다.
- `ijson==3.5.1` bounded manifest/scalar stream의 cap+1·checksum workload limit을 구현했다. manifest 전체 event ceiling은 `256 + 64 * max_mapping_count`, scalar 단일 item ceiling은 64 event이며, 둘 다 `ObjectBuilder` materialization 전에 적용한다. typed scalar/curve mapping snapshot은 structured byte ceiling, media mapping은 file byte ceiling을 copy 전에 적용하고 unsupported kind는 open 전에 거부한다. 최종 normalized scalar 최대 100,000개 list materialization과 Windows/Rocky actual wheel/offline smoke는 별도 release gate다.
- atomic publisher/readiness AP-1과 AP-2 snapshot workspace/gate 코드는 완료했다.
  AP-2는 service-owned `0700` workspace와 import root 비중첩·symlink·ownership
  검증, manifest parse 뒤 reserve+min-free capacity 확인, single refresh gate를
  포함한다. 이는 kernel quota가 아니며 실제 filesystem quota, Rocky host와 NFS/SMB는
  운영 승인·검증 항목이다. PostgreSQL advisory gate는 disposable 18.6 `127.0.0.1:55436`
  전용 session live gate로 BUSY·unlock/close 후 재획득을 확인했다.

#### P1-02 bundle fingerprint와 Run identity

상태: **Run Identity V2 코드·계약·PostgreSQL live gate 완료**. fingerprint,
PostgreSQL advisory lock, importer snapshot/rehash와 workload limits에 더해
`0017_run_identity_v2`와 API/provider 계약을 반영했다. 운영 전환 전 producer
publish/readiness AP-1과 workspace/gate AP-2 코드는 완료했고, host capability와
filesystem quota 적용을 확인하는 AP-2 release gate는 남아 있다.

- manifest와 매핑 파일의 checksum/size를 canonical 정렬해 fingerprint를 만든다.
- 동일 fingerprint 또는 동일 source checksum은 `SKIPPED/NOOP`으로 처리하고 기존
  서버 할당 `analysis_run_id`와 `run_no`를 그대로 반환한다.
- metadata와 job summary에 manifest checksum과 bundle fingerprint를 함께 보존한다.
- PostgreSQL에서는 load case별 namespaced 64-bit transaction advisory lock으로
  `run_no` 할당을 직렬화한다.
- migration `0017_run_identity_v2`의
  `canonical_result_ingestion_source_versions`가
  `(load_case_id, source_type, source_key, source_revision)`을 범위화하고,
  checksum, server-assigned run, supersedes relation을 보존한다. `run_no`는
  `(load_case_id, run_no)` unique로 고정한다. 이전 `0016` 예약은 historical
  backfill 입력이며 runtime identity의 정본이 아니다.
- 명시적 `source_run_id`의 checksum 변경은 `SKIP`(기존 run 반환), `REJECT`(기록 후
  거부), `REPLACE`(기존 run을 삭제·변경하지 않고 새 immutable run을 만들고
  `supersedes_analysis_run_id`로 연결) 정책을 따른다.
- `source_run_id`가 없는 기존 producer는 checksum을 가진 경우에도
  `name:<source_name>` slot의 legacy append revision으로 호환한다. checksum이
  없으면 ledger를 쓰지 않고 append한다.
- 수동 import의 `REJECT`는 job/audit를 transaction 안에서 commit한 뒤 HTTP 409
  `SOURCE_RUN_CONFLICT`를 반환한다. Master manifest는 `SKIP`/`REJECT`만 허용하며
  producer `REPLACE`는 fail-closed한다.
- `backend/tests/test_postgres_result_ingestion_concurrency.py`가 독립 연결 두 개로
  동일 source의 `IMPORTED`/`SKIPPED`, 서로 다른 source의 고유 `run_no`를 검증한다.
  `ANALYSIS_TEST_POSTGRES=1`과 일치하는 전용 test DB가 있어야 실행된다.
  2026-08-25 disposable PostgreSQL 18.6 cluster(127.0.0.1:55433,
  `simulation_dashboard_test_v2`)에서 빈 DB `0001→0017`, 별도 DB의
  `0016→0017` legacy backfill, app-role DDL 거부, pool budget, reference seed와
  동시성 test **2 passed**를 확인했다. 실제 SQL provider로 CREATED/exact
  NOOP/SKIP/REJECT/immutable REPLACE와 revision/supersedes 저장도 검증했다. 종료 후
  cluster·DB·로그를 제거해 residue 0을 확인했으며 기존 5432 DB와 `.env` 연결은
  사용하지 않았다.
- importer snapshot/rehash로 fingerprint 계산 바이트와 실제 parse·media 저장
  바이트의 일치를 보장한다. AP-2는 manifest parse 뒤 `reserve + min-free`를
  확인하고 `ENOSPC`/`EDQUOT`를 `BUNDLE_SNAPSHOT_RESERVE_UNAVAILABLE`로 정규화한다.
  DuckDB/local은 POSIX `flock`, PostgreSQL은 pool 밖 전용 session advisory lock으로
  full refresh와 retry를 함께 직렬화한다. 기본 2 worker PostgreSQL budget은 gate
  session worker당 +1을 포함해 62다.

#### P1-03 database-only media 마감

상태: **코드·disposable 자동검증 완료, 운영 증적 대기**. canonical import와
reference/seed write는 blob-first이며, 현재 Alembic head를 읽는 database-only
verifier와 strict shared inventory를 사용한다. `SIMDASH_MEDIA_STORAGE_MODE`는
`dual-read`(개발 기본)와 `database-only`만 허용하고 Rocky 설치 기본은
`database-only`다. 설치 후와 systemd startup에서 app-role preflight를 실행한다.

- 신규 canonical import media는 `asset_blobs`/`asset_blob_chunks`에 원자적으로
  저장하고 `media_assets.blob_id`를 연결한다.
- verifier는 unbound 참조, blob/chunk checksum·길이·참조 무결성, reference demo load
  case가 있을 때 exact allowlist 20개(없으면 expected/actual 0개), app role·connection
  budget·현재 migration head를 확인하는 계약을 가진다.
- backup은 `pg_dump`와 같은 exported snapshot에서 strict shared inventory를
  생성하고, restore는 app-role exact comparison 뒤 database-only verifier를 실행한다.
  transfer bundle v2는 blob-bound media asset을 ZIP에 중복 포함하지 않고 v1은
  fail-closed한다. migration/cleanup receipt는 O_EXCL 예약형 no-overwrite `PENDING`→
  `COMPLETED`/`FAILED` recoverable journal이고 cleanup은 migration
  receipt·실제 regular non-symlink backup dump·backup manifest·approval ID·confirmation
  receipt와 7-day 조건을 묶는 evidence-gated 명시 실행이다. manifest만으로는 충분하지
  않으며 dump의 filename·bytes·streamed SHA-256이 manifest와 정확히 일치하고
  `pg_restore --list`가 archive를 DB 접근 전에 parse해야 한다.
- `dual-read`에서는 `media_assets.file_path` 및 허용 demo filesystem fallback을
  호환성 경로로 유지하고, `database-only`에서는 blob/DB row가 없으면 fail-closed한다.
  database-only 전환 승인은 이 코드 상태와 별도의 운영 release gate다.

다음은 코드 검증과 별도로 실제 운영 환경에서만 닫을 수 있는 release gate다.

- Rocky 운영 데이터의 전체 preflight·idempotent migration과 누락/손상 source의
  운영자 정정
- reference/demo 데이터를 포함한 backup이면 분리된 빈 PostgreSQL에 restore한 뒤
  media inventory, 전체 blob/chunk checksum, exact 20개 demo MP4를 확인하고, Rocky
  기본 `SEED_MODE=empty` production backup이면 expected/actual demo 0개를 확인하는
  rehearsal
- 검증된 backup과 database-only gate 이후 최소 7일 legacy 원본 보존, migration
  receipt·실제 backup dump·backup manifest·approval ID·confirmation receipt를 포함한
  명시적 evidence-gated 비자동 cleanup (manifest 단독은 불충분)
- Rocky DB volume에서 500 MiB media, 동시 50 stream, 10분 Range/seek 부하의 RSS,
  p95, 5xx, pool-timeout 측정 및 5분 startup timeout 적정성 확인

**2026-08-25 로컬 통합 증적:** disposable PostgreSQL 18.6
(`127.0.0.1:55439`)에서 reference source의 21 blobs/21 chunks,
2,509,278 bytes, 23 refs와 catalog SHA prefix `d89c…`를 확인했다. 동일 exported
snapshot으로 생성한 2,929,899-byte backup dump를 분리된 빈 DB에 restore한 뒤 exact
inventory match, app-role verifier, Alembic `0017`, reference demo 20/20을 통과했고,
app-role audit 권한도 `SELECT`/`INSERT` 허용 및 `UPDATE`/`DELETE` 거부를 확인했다.
별도 schema-only empty DB의 database-only startup preflight도 blob/demo 0/0으로
통과했다. 이 증적은 코드·local integration 검증이며 기존 5432와 `.env` 연결을
사용하지 않았다. 실제 Rocky/NFS/quota, production backup→빈 DB restore rehearsal,
500 MiB/50-stream 부하와 startup timeout 적정성, PowerShell 실실행은 여전히 운영
release gate다.

모든 허용 media extension은 generated minimal `tmp_path` fixture로 정상 수락,
MIME/extension mismatch 거부, corrupt signature 거부를 검증한다. 영구 canonical
folder example은 JSON/CSV/SVG/glTF만 유지한다.

현재 결과 수집 focused 검증은 collection 기준 **256개** test case다. manifest 경계,
fingerprint/mapping 변경, 공통 UoW rollback, manual SUMMARY_RESULT JSON/CSV,
streaming JSON·folder import·bundle snapshot limits, media fixture matrix,
PostgreSQL reservation SQL/migration contract와 endpoint wiring, Radioss canonical
UoW를 포함한다. PostgreSQL 동시성 test는 collection에는 포함되지만 opt-in marker라
전용 test DB가 없는 기본 suite에서는 skip된다. 수집 수는 테스트가 추가되면 함께
변할 수 있으므로 아래 통합 collection 명령을 기준으로 확인한다.

```bash
cd backend
../.venv-wsl/bin/python -m pytest --collect-only -q \
  tests/test_master_result_refresh.py \
  tests/test_master_result_folder_example.py \
  tests/test_result_ingestion_atomicity.py \
  tests/test_result_import_contract.py \
  tests/test_manifest_format.py \
  tests/test_manual_result_ingestion.py \
  tests/test_media_policy_fixtures.py \
  tests/test_result_ingestion_idempotency.py \
  tests/test_api.py::test_typed_folder_example_registers_scalars_curves_media_and_catalog \
  tests/test_postgres_result_ingestion_concurrency.py \
  tests/test_streaming_json.py \
  tests/test_folder_import_limits.py \
  tests/test_import_bundle_limits.py \
  tests/test_bundle_snapshot.py \
  tests/test_result_bundle_readiness.py \
  tests/test_result_bundle_publisher.py \
  tests/test_publish_result_bundle_cli.py \
  tests/test_result_import_history.py \
  tests/test_run_identity_contracts.py \
  tests/test_run_identity_migration.py \
  tests/test_run_identity_v2.py
# PostgreSQL concurrency cases are opt-in at runtime; collection size is expected to change with focused coverage.
```

### Phase 2 — 기능별 V2 구조 전환(P1)

#### Backend 순서

1. `result_ingestion`: Refresh·parser·media·status sync (공통 UoW 기반)
2. `workbench`: 업무 유형·작업계획·배치 실행·결과 snapshot
3. `access`: permission/project scope/audit transaction
4. `reports`: report context·layout·template
5. 남은 `main.py` endpoint

`result_ingestion`은 첫 HTTP 추출, 두 번째 application orchestration, 세 번째 query port 추출을 완료했다. 결과-import
template, 수동 결과 import, 예제 폴더 import endpoint는 독립 router에 두고, parser·canonical command·
ingestion orchestration은 framework-neutral application use case로 이동했다. OpenAPI·권한·atomic
write/audit 계약과 기존 persistence UoW/SQL facade는 유지한다. 결과 미디어 read 전용 `/api/assets/*`
조회·다운로드·Range 경계도 `routers/media.py`로 분리했다. result-media metadata/blob read도 typed domain
read model·query port·application use case·SQL adapter로 옮겨 같은 연결의 metadata → project scope
authorization → blob 순서를 보존한다. DB blob ETag/Range/HEAD/download, audit, 기존 route/OpenAPI 순서는
유지하며 media write/import/storage mode와 persistence UoW 자체의 재배치는 후속 slice로 다룬다. 현재 result-ingestion router는 같은 연결에서 framework-neutral query use case와
SQL read adapter를 조립하며, typed는 target-only read, manual은 context→chassis threshold→open-cell
threshold→catalog 순서를 유지한다.
운영 환경 검증이 필요한 media write/storage-mode 변경은 release gate로 남긴다. `workbench`의 첫
read-only 단위로 업무 유형·의뢰 유형 catalog GET 두 개를 framework-neutral query use case, typed domain
read model/port, SQL adapter로 옮겼다. `all_versions` 선택, active/latest 및 history 정렬, decoded JSON
응답과 기존 route/OpenAPI 순서는 유지한다. 다음으로 단일 request-type resolution GET을 같은 경계로 옮겨
context → assignment → assigned type 또는 active catalog rule 순서, 네 상태 응답, missing-request 404와
무인증 read를 보존했다. 이어서 단일 request work-plan GET은 request 존재 확인 → canonical monitoring summary
순서와 두 not-found 상태, 성공 payload를 framework-neutral outcome/port로 명시했다. monitoring 계산은 기존
service에 남겨 shared projection의 single source of truth를 유지한다. `GET /api/workbench/requests/{request_id}/result-layout`과
`POST /api/workbench/requests/{request_id}/result-layout/materialize`도 typed domain model/error·port,
framework-neutral application query/command, same-connection SQL adapter로 분리했다. GET은 request context →
`PROJECT_DATA_VIEW` → load-case 소유권 → snapshot → bindings 순서와 `UNCONFIGURED`, 의도적으로 이관된
`LEGACY_ASSIGNED` compatibility를 유지한다. POST는 context → `DASHBOARD_EDIT` → ownership → begin → materialize →
commit 순서, `201 DashboardDefinition`, `LookupError`/`ValueError` HTTP mapping과 rollback 계약을 유지한다. canonical
examples와 master-results JSON/CSV/SVG/glTF → DB → bindings bridge test까지 고정했다. 현재 전체 backend 검증은
`897 passed, 10 skipped in 651.42s (0:10:51)`, exit 0이다. 단, request-type assignment `PUT`은 별도 command port/use case/SQL adapter로 옮겼다. router는
principal source·`REQUEST_EDIT`·HTTP 오류 응답을 유지하며, application은 동일 connection의 work-plan immutable
guard 후 기존 repository assignment를 호출한다. persistence adapter는 repository의 admin lock과 target-not-found를
domain error로 번역해 기존 HTTP status/detail을 보존한다. 첫 lifecycle command slice인
`PATCH /api/workbench/work-items/{item_id}/progress`는 typed command/state/error, framework-neutral use case,
command port와 same-connection SQL adapter로 분리했다. router는 principal actor·assigned permission·override audit·HTTP
mapping·transaction boundary를 유지하고, use case는 item read → authorize → audit → running-state/no-op/monotonic
check → UPDATE → canonical status sync 순서를 보존한다. 다음 `POST /api/workbench/work-items/{item_id}/start`는 같은
private lifecycle adapter/UoW·monitoring facade를 재사용해 typed start command/state/error와 use case로 분리했다.
기존 idempotent monitoring, current-item priority, prior-incomplete guard, principal timestamped READY-to-IN_PROGRESS
transition 및 status sync 순서를 유지한다. `POST /api/workbench/work-items/{item_id}/complete`도 typed completion command,
port/use case와 same-connection SQL adapter로 분리했다. completed idempotency → current/started/prerequisite guard → optional
succeeded demo-run validation → completion update → next WAITING READY promotion → canonical sync, 그리고 기존 404/409
detail·audit·rollback 계약을 보존한다. batch는 이 slice에 포함하지 않는다.
`PATCH /api/workbench/work-items/{item_id}/assignee`도 별도 typed reassignment command/state/assignee read, application
use case, same-connection SQL adapter로 분리했다. joined projection read → workflow-edit authorize → completed guard →
active project-member validation/resolution → update → audit → reread 순서를 보존하고, 기존 final 409 및 membership/account
422 detail, single rollback, raw work-item response는 그대로 유지한다. batch는 이 slice에 포함하지 않는다.
최종 work-item execution slice인 `POST /api/workbench/work-items/{item_id}/batch-dispatch`도 command/port/application
boundary를 도입했다. application use case가 동일 idempotency-key 재호출, IN_PROGRESS/profile-version-active 검증,
PREFLIGHT·QUEUED commit, 독립 DEMO_ONLY runner, FAILED 또는 SUCCEEDED attempt/dispatch/progress/status finalization을
단계별 port로 조정한다. rejected attempt의 commit과 runner의 독립 transaction을 일반 rollback으로 합치지 않으며,
adapter는 기존 query/persistence/service를 같은 connection에서 제공하고 router는 권한/audit, HTTP 오류와 민감 run
projection sanitization을 유지한다.
Batch attempt idempotency는 phase-one insert의 DB unique race를 typed conflict로 번역해 rollback 후 같은 connection에서
existing attempt를 재조회한다. SUCCEEDED attempt의 연결된 run만 기존처럼 replay하고, QUEUED linked crash-window attempt는
성공으로 반환하지 않으며 그 외 attempt와 함께 기존 duplicate 409 규칙을 적용한다.
`0018_batch_attempt_run_identity`는 runner commit 뒤 finalization 전 crash를 식별 가능한 상태로만 축소한다. attempt ID 기반
deterministic run ID, `workflow_runs.batch_attempt_id`, `batch_dispatches.attempt_id`를 저장하고 재호출 때
request/mode/owner/attempt identity를 검증한다. SUCCEEDED historical attempt의 기존 run link만 backfill하며 QUEUED legacy
orphan은 매핑하지 않는다. 이는 **execution 구현 경계**이며 lease claim, retry ownership/API, scheduler, 자동 recovery,
status semantics 변경은 포함하지 않는다. 그 기능은 PostgreSQL live migration/privilege 검증과 운영 retry·lease 정책 승인 뒤의
**release gate**다.
`0019_batch_recovery_lease`는 이 release gate의 선행 기반으로 internal ownership만 분리한다. PostgreSQL multi-worker/live
migration 검증 전 DuckDB는 serialized single-connection coverage로만 취급하며, scheduler/retry endpoint/automatic rerun 및
public recovery wiring은 후속 release 작업으로 남긴다. Lease-fenced finalization CAS는 application/domain port와 SQL adapter의
internal-only boundary로 로컬 구현했으며, PostgreSQL multi-connection·app-role·live migration 검증 전에는 release하지 않는다.
DuckDB→PostgreSQL transfer tool은 active·expired·malformed lease metadata와
partial lease schema를 preflight에서 차단하고, five reverse immediate-FK를 NULL staging 뒤 CAS restore하며, source snapshot과
checksum 검증 성공 뒤에만 commit한다. 이 blocker는 read-only dry-run도 non-zero로 종료시켜 PostgreSQL 생성 전 setup gate로
사용하며, 안전한 additive legacy 값만 checksum/INSERT 공통 projection으로 보정한다. target table/column과 필수 recovery
constraint·unique index도 첫 INSERT 전에 검증한다. 실제 PostgreSQL live migration/app-role 검증은 여전히 운영 release gate다.
개인 노트북에서 닫을 수 있는 DuckDB/application 계약과 사내 PostgreSQL separate-connection,
proxy/CA, Rocky/nginx/systemd/TLS, backup/rollback gate 및 인수인계 증적은
[`personal-laptop-to-corporate-release-handoff.md`](personal-laptop-to-corporate-release-handoff.md)에 분리해 기록한다.

`access`의 vertical slice로 프로젝트 멤버십 CRUD
(`GET/POST /api/projects/{project_id}/members`,
`PATCH/DELETE /api/projects/{project_id}/members/{user_id}`)를
`HTTP → application → domain port → SQL adapter` 경계로 분리했다. 기존 project
existence 확인과 same-connection 권한 검사, PostgreSQL table lock, 마지막 admin·전역
관리자·open work·stale timestamp 보호, exact audit detail과 audit 실패 시 mutation
동시 rollback, route/OpenAPI 응답 순서는 유지한다. 이 멤버십 extraction으로 legacy
access router의 `execute` ceiling은 46에서 36으로 낮췄다.

후속 vertical slice로 프로젝트 초대와 외부 directory lifecycle
(`GET /directory/employees`, `GET/POST /invitations`, complete/cancel)을
`HTTP → application → domain port → directory/persistence adapter` 경계로
분리했다. directory 검색, 초대 상태 전이와 완료 시 멤버십 생성, 중복·재실행 차단,
동일 transaction의 mutation·audit 원자성과 기존 API/OpenAPI 계약을 보존한다.
프로젝트 assignee 후보 조회(`GET /api/projects/{project_id}/assignee-candidates`)도
별도 query slice로 분리해 프로젝트 존재·권한 확인 후 active project member만 조회하고,
검색어 정규화·제외 조건과 응답 계약을 유지한다. 이 초대·directory·assignee
extraction으로 legacy access router의 `execute` ceiling은 36에서 20으로 낮아졌다.
두 slice의 focused 검증은
**75 passed in 64.81s, exit 0**다.
이 변경을 포함한 현재 전체 backend suite는 **897 passed, 10 skipped in 651.42s
(0:10:51), exit 0**이다.

2026-09-01 account status 및 global-admin command vertical slice를 완료했다.
`PATCH /api/admin/users/{user_id}/status`와
`PATCH /api/admin/users/{user_id}/global-admin`은
`HTTP → application → domain port → persistence adapter` 경계를 사용한다.
HTTP composition이 PostgreSQL 전용 table lock provider를 status 명령의
`(users, project_invitations)`, global-admin 명령의 `(users,)`로 선택한다.
provider가 transaction을 시작하고 lock을 획득한 뒤 application이 같은
connection의 fresh actor에 대해 `SYSTEM_USER_APPROVE` 권한을 재검사하고
target user를 읽는다. status mutation은 별도 invitation 선행 조회 없이 일치하는
pending invitation을 원자적으로 갱신한다. stale timestamp, 마지막 global admin
보호, `ACTIVE` 계정 승인과 초대의 `READY` 전이를 기존 계약대로 보존한다.
mutation과 exact audit event는 같은 transaction에 기록하며 audit 실패 시 함께
rollback한다. 응답은 안전한 14-field projection으로 제한하고 기존 route,
operationId, 응답 순서와 OpenAPI snapshot을 유지했다.

이 extraction으로 legacy `access_control` router의 직접 SQL 실행 ceiling은
역사적으로 **46 → 36 → 20**을 거쳐 **11**로 낮아졌다. `access_control`은
이제 menu policy를 보유하고, account status/global-admin HTTP adapter는
`backend/app/adapters/http/routers/user_administration.py`가 소유한다.
해당 slice focused 검증은 **14 passed**이며, access focused 검증은
**89 passed in 77.27s, exit 0**이다. architecture/OpenAPI/compile 검증도
통과했다. 이 변경을 포함한 full backend suite는 **911 passed, 10 skipped in
694.22s (0:11:34), exit 0**이다.

2026-09-01 menu policy vertical slice도 완료했다. public navigation policy 조회는
기존처럼 모든 `ACTIVE` 인증 사용자가 사용할 수 있고, admin version list/detail과
update/restore는 요청 시점의 fresh authorization을 유지한다. PostgreSQL mutation은
`(menu_policy_state, role_menu_policies)` fixed table lock을 획득한 뒤 수행한다.
update는 전체 정책 snapshot과 version history를 남기고, restore는 과거의 부분
snapshot을 현재 정책 위에 overlay한다. 과거 snapshot 값은 기존 Pydantic coercion을
거치며 malformed historical payload는 저장하지 않고 transaction을 rollback한다.
정책 상태·version snapshot·audit는 한 transaction에 기록해 audit 실패 시 모두
원자적으로 rollback한다. 이 extraction으로 `backend/app/routers/access_control.py`는
router include만 담당하는 composition-only 경계가 되었고 직접 SQL `execute` ceiling은
**11 → 0**으로 낮아졌다. 완료된 full backend 회귀는 **929 passed, 10 skipped in
691.50s (0:11:31), exit 0**이다. 전체 실행 뒤 production 변경 없이 계약 test 한 개를
보강했으며, 추가 후 menu policy slice 단독 **19 passed**, 확대 focused 검증
**79 passed**도 통과했다.

2026-09-01 report layout vertical slice도 완료했다. 목록·생성·수정·버전
목록·버전 상세·비활성화 6개 API 전체를 reports
`HTTP → application → domain port → SQL adapter` 경계로 이동했다. 읽기는
기존처럼 모든 `ACTIVE` 인증 사용자에게 열려 있고, 생성·수정·삭제는
영속성 provider를 열기 전 fresh `SYSTEM_CATALOG_MANAGE` 권한을 확인한다.
수정은 기존 동작대로 active version/is-system을 `BEGIN` 전에 읽으며 새
PostgreSQL lock이나 CAS를 추가하지 않았다. 생성·수정의 live row, version
snapshot, audit는 같은 transaction에서 commit/rollback된다. 기존 검증의 한국어
422 message 27개를 framework-neutral policy로 옮겼고 legacy 구현 대비 20,001개
차등 검증에서 불일치 0개를 확인했다. report focused 검증은 **56 passed**,
관련 영역을 넓힌 focused 검증은 **66 passed**이며 architecture·OpenAPI·compile
gate도 통과했다. 최종 full backend는 **979 passed, 10 skipped in 717.37s
(0:11:57), exit 0**이다. 이 extraction으로 `backend/app/main.py`의 direct `execute`
ceiling은 **189 → 174**로 낮아졌다.

2026-09-01 PPTX template vertical slice도 완료했다. 목록·업로드·render·비활성화
4개 API를 reports 하위의 독립 HTTP/application/domain/persistence/storage/document
경계로 이동했다. 업로드는 기존처럼 fresh `SYSTEM_CATALOG_MANAGE`를 파일 검증보다
먼저 확인하고, render는 `REPORT_EXPORT`, 목록은 `ACTIVE` middleware 계약을 유지한다.
DB 연결·BEGIN·insert·audit·commit 실패는 생성한 파일만 보상 삭제하며, ID 충돌 시
기존 파일을 덮지 않는다. 저장 경로는 `report-templates/report-template-<12 hex>.pptx`
direct child만 허용하고 child/file symlink와 traversal은 기존 410 응답으로 fail-closed한다.
삭제는 같은 filesystem quarantine 뒤 DB를 비활성화하고 실패 시 파일을 복원한다.
ZIP symlink·외부 relationship·잘못된 XML·비정상 slide 크기도 명시적 422로 차단한다.
기본 runtime 경로는 기존 Rocky 계약인 `backend/assets/report-templates`다.

관련 focused 검증은 **19 passed**, architecture·OpenAPI·compile gate가 통과했고
최종 full backend는 **996 passed, 10 skipped in 686.52s (0:11:26), exit 0**이다.
`backend/app/main.py`는 **2,182줄**, direct `.execute()` 실제값과 architecture ceiling은
**148**이다. 잘못 생성됐던 빈 `backend/app/assets` 테스트 디렉터리는 제거했다.

2026-09-01 variable catalog vertical slice도 완료했다. 변수 목록·생성/재활성화·수정·
soft delete 4개 API를 HTTP/application/domain policy·port/SQL adapter로 이동했다.
GET은 기존처럼 별도 project permission 없이 load-case 존재만 확인하며 mutation은
provider가 연 같은 연결에서 `PROJECT_VARIABLE_MANAGE`를 먼저 확인한다. `has_data`,
dashboard 사용 개수, 사용 중 삭제 409, active duplicate, inactive same-key 재활성화와
클라이언트 `updated_by` 계약을 구조 변경 없이 보존했다. Update는 key/data type을
바꾸지 않지만 inactive row 재활성화는 현재처럼 data type 변경을 허용한다. Result
ingestion의 legacy import는 compatibility facade를 통해 같은 SQL adapter를 사용한다.

Variable focused는 **14 passed**, result-ingestion 호환 회귀는 **23 passed**,
architecture·OpenAPI·compile gate가 통과했다. 최종 full backend는
**1009 passed, 10 skipped in 702.85s (0:11:42), exit 0**이며 `main.py`는
**2,122줄**, direct `.execute()` 실제값과 ceiling은 **147**이다.

2026-09-01 project workspace layout vertical slice도 완료했다. canonical project route
3개와 deprecated alias 2개, 총 5개 API를 HTTP/application/domain policy·port/SQL adapter로
이동했다. Read는 `PROJECT_DATA_VIEW`, write는 `PROJECT_LAYOUT_EDIT`를 provider가 연 같은
open connection에서 확인한다. Write는 `BEGIN → project → auth → live update → version append
→ audit → COMMIT` 순서이며 실패 시 rollback한다. principal actor와 기존 validation/404/422,
alias, operationId를 유지했고 live row가 없을 때 history 조회가 빈 목록을 돌려주는 기존
계약도 바꾸지 않았다.

2026-09-01 import-schemas vertical slice도 완료했다. GET/POST/PUT/DELETE 4 route를
HTTP/application/domain policy·port/SQL adapter로 이동했고, 기존 anonymous OpenAPI response,
path·route order·operationId를 유지했다. GET의 explicit permission 없음과 mappings validation의
provider-open 선행도 보존했다. Create/update는 같은 connection에서 `SYSTEM_CATALOG_MANAGE`를
확인한 뒤 live row·version·audit을 transaction으로 기록하고 실패 시 rollback하며, principal
actor·embedded `schema_id`·version semantics를 유지한다. DELETE는 legacy separate permission
connection, non-transactional usage check, 명시적 domain delete audit 없음 상태를 그대로 둔다.

2026-09-01 result-review vertical slice도 완료했다. `GET/POST
/api/analysis-runs/{run_id}/review-items`와 `PATCH /api/review-items/{annotation_id}` 3 route를
HTTP/application/result-review domain policy·port/SQL adapter로 이동했다. trust와 review는 neutral
persistence `result_keys` helper를 공유해 scalar/time-series/curve/location 및 media metadata의 결과
key SQL/JSON 의미를 하나로 유지한다. GET은 기존 explicit `PROJECT_DATA_VIEW` 부재, run 존재 확인,
bookmark inner join과 `updated_at DESC` 순서를 보존한다. Create는 같은 connection에서 run 존재 확인 전,
update는 current annotation 조회 전 `RESULT_REVIEW` resource authorization을 먼저 확인하고 bookmark·annotation·audit을 atomic
transaction으로 기록한다. HTTP authorization/audit callback을 누락하면 fail-closed하며,
principal display name·Pydantic·anonymous OpenAPI response·route order/operationId 계약은 그대로다.

2026-09-01 analysis-insights read-only vertical slice도 완료했다. `GET
/api/load-cases/{load_case_id}/run-comparison`과 `GET /api/analysis-runs/{run_id}/trust` 2 route를
analysis-insights HTTP/application/domain errors·port·policies/SQL adapter로 이동했다. 두 route는
기존대로 explicit permission, audit, transaction이 없으며 comparison classification·series merge,
trust check·overall status와 raw payload/오류 계약을 유지한다. Trust는 neutral persistence
`result_keys` helper를 같은 connection에서 사용한다.

2026-09-01 load-case-overview read-only vertical slice도 완료했다. `GET
/api/load-cases/{load_case_id}/overview`를 HTTP/application/domain policy+errors+port/SQL adapter로
이동했다. Application은 authorization → existing product query/provider → overview provider의 A/B/C
세 connection 순서를 소유하고, HTTP는 asset/download URL만 매핑해 domain policy를 transport-neutral로
유지한다. 9개 SQL, selected/latest/no-run, 404, JSON/template, threshold/verdict 투영은 그대로다.

2026-09-01 quality-thresholds vertical slice도 완료했다. `GET
/api/projects/{project_id}/quality-thresholds`, canonical
`PUT /api/projects/{project_id}/quality-thresholds/{criterion_key}`, deprecated alias
`PUT /api/quality-thresholds/{criterion_key}`를 domain/application/persistence/HTTP로 분리했다. GET은 기존
explicit project permission 없는 company-wide `ACTIVE` read를 유지한다. PUT은 lookup 404 또는 alias
multi-project 409 뒤 same-connection `PROJECT_THRESHOLD_MANAGE` → principal/시간 → BEGIN → threshold update·
criterion-specific scalar recalc·audit → COMMIT → post-commit fetch 순서를 보존하며, generic OpenAPI와 deprecated
alias도 그대로다.

Focused **22 passed**, architecture·OpenAPI·compile gate와 full backend **1118 passed, 10 skipped**를 확인했다.
직접 측정한 `backend/app/main.py`는 **1,322줄**, direct `.execute()` actual/ceiling은 **71**이다. 개인 노트북
완료 범위는 unit·DuckDB·OpenAPI·architecture·full 검증까지다. GET의 explicit project permission 부재와 alias
global semantics, row lock/CAS, post-commit fetch rollback seam은 보존 기술부채다. provider construction purity,
dict mutation/storage normalization, unordered media/template, company-wide read와 single threshold semantics도
별도다.

PostgreSQL 18 app-role 권한·audit INSERT, correlated update parity, OIDC active-nonmember/cross-project 정책,
동시 update locking/CAS, 현실 데이터 EXPLAIN/index/lock latency와 nginx/proxy/private CA 검증은 office-only
release gate다. 다음 우선순위는 **재감사 후 확정**한다.

2026-09-01 workflow queries read-only vertical slice도 완료했다. `GET
/api/requests/{request_id}/workflow`와 `GET /api/workflows`를 workflow-queries
HTTP/application/domain port/persistence로 분리했고 `main.py`는 router composition만 유지한다. Detail은
analysis request 404를 monitoring 전에 판정하며 detail/list 모두 같은 connection의 monitoring을 사용한다.
List의 exact join·`min`/`COALESCE`·group·`requested_at DESC` SQL/default, status overwrite와 10-field projection을
유지했고 두 GET에는 legacy처럼 explicit permission·audit·transaction이 없다.

Focused **19 passed**, architecture·OpenAPI·compile gate와 full backend **1127 passed, 10 skipped**를 확인했다.
직접 `wc`로 측정한 `backend/app/main.py`는 **1,263줄**, direct `.execute()` actual/ceiling은 **69**다. 이 slice는
개인 노트북 검증으로 완결되며 별도 PostgreSQL 필수 검증은 추가하지 않는다. 기존 office-only release gate와 다음
우선순위는 후속 slice에서 재감사한다.

2026-09-01 request-load-cases read-only vertical slice도 완료했다. `GET
/api/requests/{request_id}/load-cases`를 request_load_cases HTTP/application/domain/persistence로 분리했다. exact
`SELECT * FROM load_cases WHERE request_id = ? ORDER BY created_at`(ASC), 같은 connection, missing request
`200 []`, `parameters_json` pop 뒤 JSON 또는 malformed raw 문자열의 `parameters` 투영을 보존했으며,
permission·audit·transaction·별도 error mapping은 추가하지 않았다.

Focused **11 passed**, workflow/request contract pair **15 passed**, architecture·OpenAPI·compile gate와 final
full backend **1133 passed, 10 skipped**를 확인했다. 직접 `wc`로 측정한 `backend/app/main.py`는 **1,251줄**,
direct `.execute()` actual/ceiling은 **68**이다. 과거 workflow/request 테스트는 monotonic ceiling과 baseline
equality를 검증하도록 보강했다. 이 slice는 개인 노트북 검증으로 완결되며 별도 PostgreSQL 필수 검증은 없다. 기존
office-only release gate는 유지하고 다음은 **feature-examples 재감사 후 확정**한다.
Duplicate rejection 409의 attempt detail은 non-admin에게 profile snapshot과 command preview를 노출하지 않도록 router에서
sanitize하고 admin 원문 계약은 유지한다. idempotency check와 attempt insert 사이의 경쟁, 그리고 QUEUED→DEMO_ONLY runner
→finalization 사이의 복구/재처리 설계는 이번 safe slice에서 transaction semantics를 바꾸지 않고 별도 reliability 계획으로
다룬다.

각 slice는 `HTTP → application → domain port → adapter`를 갖고 router `.execute()`를 0으로 유지한다.

#### Frontend 순서

1. data/result ingestion controller
2. request result snapshot과 dashboard materialization controller
3. workbench admin/editor state
4. report export model/renderer/I/O
5. App bootstrap·selection context와 global styles

완료조건:

- `App.tsx`, `main.py`, `database.py`가 조립/compatibility 역할만 가짐
- feature 간 import gate 신규 예외 0건
- API/E2E 사용자 흐름 유지

### Phase 3 — 사내 proxy와 일괄 배포(P0/P1)

#### P3-01 proxy 구분과 단일 설정 schema

| 종류 | 목적 | 설정 |
|---|---|---|
| Vite dev proxy | 브라우저 same-origin 개발 | `VITE_API_TARGET` |
| outbound corporate proxy | package/IdP/directory 외부 연결 | `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, 조직 CA |
| nginx reverse proxy | 운영 TLS·정적 파일·API 진입 | nginx template |

서로 다른 proxy를 같은 환경변수나 문서 단락으로 섞지 않는다.

[#15](https://github.com/wgcha/simdashboard/issues/15)의 Linux 사내망 요구사항은
다음처럼 정규화한다.

- site proxy 입력: `http://168.219.61.252:8080` (인증정보 없음). runtime/package
  용 `HTTP_PROXY`, `HTTPS_PROXY`에 넣을 후보 값이며 Vite/nginx reverse proxy와는 별개다.
- bypass source 입력: loopback/localhost, `10.*`, `165.213.*`, `168.219.*`,
  `202.20.*`, `112.107.220.*`, `samsung.net`. 이 wildcard 표기는 consumer마다
  `NO_PROXY` 지원 문법이 다르므로 그대로 복사하지 않고 대상 도구별로 정규화·검증한다.
- trust source 입력: `/usr/share/ca-certificates/samsung/DigitalCity.crt`와 Debian의
  `dpkg-reconfigure`/`update-ca-certificates` 절차
- 비밀 입력: proxy 인증정보와 CA private key는 secret store 또는 root-only file로 제공

현재 `.env.example`과 Windows `setup.ps1`은 proxy 입력 형식과 로그 마스킹을
지원한다. #15의 Debian CA 경로와 갱신 절차는 source 환경의 입력이며, 그 이슈에
언급된 `/etc/ssl/cert` 경로로 수동 복제하지 않는다. canonical target인 Rocky 8에는
trust-store 경로와 갱신 절차를 별도로 구현해야 하며, DNF, Python/Node, systemd에
proxy/CA를 설치·갱신하는 자동화는 아직 없다. `NO_PROXY` assignment에는 단일 `=`만
사용한다.

#### P3-02 Rocky one-command deploy

- signed release bundle 또는 조직 artifact repository를 사용한다.
- Python wheel뿐 아니라 OS RPM/internal repo/CA 전제를 preflight한다.
- #15의 proxy/CA 입력을 Rocky installer와 서비스 EnvironmentFile에 안전하게 반영하고, DNF·runtime·health smoke를 실제 사내망에서 검증한다.
- nginx forwarded header를 신뢰 경계에서 덮어쓰고 trusted host·HSTS·CSP를 검토한다.
- upload/download timeout, buffering, 최대 body, media Range를 검증한다.
- migration → service → nginx → health → smoke → rollback 순서를 자동화한다.

#### P3-03 Windows 운영이 필수일 때만

- `deploy/windows/`에 static web server/IIS 또는 승인된 reverse proxy를 결정한다.
- Windows service 등록, TLS binding, firewall, app env ACL, health rollback을 구현한다.
- Vite 개발 서버를 운영에 사용하지 않는다.

완료조건:

- 승인된 VM에서 설정 파일 하나와 release bundle로 설치
- internet 차단 profile에서 승인된 mirror/bundle만 사용
- OIDC/password, PostgreSQL, Refresh, media Range, backup/restore smoke 통과

### Phase 4 — 운영 품질(P2)

- 구조화 log와 request/import correlation ID
- API latency, DB pool, import throughput, media Range metric
- failed manifest quarantine와 재처리 UI
- SQL query/index review와 대용량 curve downsampling
- release retention·disk monitoring·backup restore rehearsal
- project/request/load-case deep-link

## 6. 필요한 개발 기술과 우선순위

| 우선순위 | 기술 | 적용 작업 |
|---|---|---|
| P0 | Python 3.12, FastAPI, Pydantic, pytest | API·parser·권한·통합 테스트 |
| P0 | PostgreSQL 18, SQL, Alembic, transaction, index, pool | 운영 DB·migration·대량 import |
| P0 | TypeScript, React 18, React Router, async race control | feature 분리·context 안정성 |
| P0 | OpenAPI, openapi-typescript, openapi-fetch | 서버/클라이언트 단일 계약 |
| P0 | RBAC, OIDC/OAuth2, cookie/CORS, audit | 사내 SSO·project 격리 |
| P0 | Playwright, API/DB integration test | 전체 사용자 흐름 회귀 |
| P1 | Vertical Slice, application service, port-adapter | legacy 구조 점진 교체 |
| P1 | manifest, checksum, idempotency, streaming I/O | 결과 수집 안정화 |
| P1 | nginx, systemd, Rocky, SELinux, PKI/조직 CA | 사내 일괄 배포 |
| P1 | observability, structured logging, metrics | 운영 장애 추적 |
| P1 | supply-chain signing, offline package mirror | 폐쇄망 배포 신뢰성 |
| P2 | Recharts, react-grid-layout, 접근성 | 대시보드 UX |
| P2 | PPTXGenJS, OOXML | 보고서 고도화 |
| P2 | media Range streaming, GLB/glTF | 대용량 결과 UX |

## 7. 오픈소스·라이브러리 사용 원칙

- 인증: 현재 PyJWT와 표준 OIDC discovery를 유지하고 자체 암호 프로토콜을 만들지 않는다.
- API: FastAPI/Pydantic/OpenAPI 생성 client를 유지한다.
- migration: Alembic을 유일한 PostgreSQL schema migration 도구로 사용한다.
- UI: React Router, Recharts, react-grid-layout, PPTXGenJS의 검증된 기능을 우선한다.
- 파일 감시가 필요하면 OS별 직접 구현보다 watchdog 같은 검증된 library 또는 agent protocol을 평가한다.
- 재시도/worker가 실제 필요해질 때만 Dramatiq/Celery/RQ 등 운영 가능한 queue를 비교한다. 현재 동기 Refresh에 무조건 추가하지 않는다.
- 새 package는 유지보수 상태, license, Rocky/Windows 지원, offline 설치, 보안 update 경로를 검토한다.

## 8. 협업과 검수

- Sol: 목표, 계약, 위험, 작업 분해, architecture/API/DB 검수, 최종 release gate
- Luna: 좁고 명확한 feature 구현, parser·UI·테스트
- Terra: 배포·문서·통합·회귀와 일상적인 구조 개선
- 같은 파일을 여러 agent가 동시에 수정하지 않는다.
- 기능 구현과 검수는 별도 작업으로 나누고, 검수 결과를 구현자가 반영한다.

각 작업 산출물:

1. 요구사항/이슈 링크
2. 변경 계약과 영향 파일
3. migration 또는 호환성 전략
4. 정상·실패·권한·idempotency 테스트
5. 문서와 배포 영향
6. 실행한 검증과 잔여 위험

## 9. Release gate

- 전체 backend test와 architecture check
- OpenAPI snapshot/generation diff 없음
- frontend architecture/self-test/build
- 핵심 Playwright E2E
- 빈 DB→Alembic head와 기존 DB upgrade
- app-role DDL 거부, connection budget
- canonical 결과 예제 import와 재실행 skip
- manual SUMMARY_RESULT JSON/CSV import와 재시도 skip, audit/auth transaction
- 모든 허용 media extension fixture의 정상·MIME mismatch·corrupt signature 검증
- PostgreSQL advisory lock·migration `0017_run_identity_v2` source-version,
  privilege·connection budget·live concurrency 검증
- AP-2 workspace `0700`/비중첩·symlink·ownership, reserve failure와 `ENOSPC`/`EDQUOT`,
  retry/full-refresh gate, PostgreSQL gate session budget 검증
- Rocky host install, NFS/SMB mount probe·승인과 dedicated filesystem quota는 실제
  target에서 별도 확인
- P1-03 blob-first canonical/seed write, media verifier, exported-snapshot
  backup→restore inventory/checksum, app-role exact comparison, transfer v2/v1
  fail-closed와 cleanup receipt 계약의 코드·disposable 자동검증
- nginx config, TLS, forwarded header, SPA/API/assets/Range smoke
- 실제 backup→빈 DB restore rehearsal의 media inventory/blob-chunk checksum·reference
  demo 20/20 또는 empty production 0/0 검증, 7일 보존 뒤 승인 cleanup, Rocky media
  streaming 부하 측정, PowerShell 실실행 (운영 release gate)
- 문서 링크와 환경 예제 정합성

## 10. 다음 실행 순서

1. canonical 예제와 import 검증을 유지하고 #13 SPDM discovery layout을 결과 import와 분리한다.
2. GitHub Issues #13~#15 추적 표와 실행/계획 경계를 유지한다. (완료)
3. migration verifier와 이후 migration head 처리 방식을 고친다. (완료)
4. 운영 target ADR과 `SIMDASH_IMPORT_ROOT` 배포 연결을 구현한다. (구현 완료, Rocky host release validation 남음)
5. legacy `result_files` parser compatibility를 유지하면서 Radioss mesh locations 공통 UoW 이관을 검증한다. (완료)
6. import history/status/retry UI를 구현했다. 선택 load case의 상태별 이력과
   `operation`, reason, 기존/교체 run, source revision을 조회하며 조건을 만족하는
   Master 실패/거부 job만 재시도한다. 원 job target drift는 거부하고 실패 재시도도 새
   attempt로 남긴다. 별도 disposable PostgreSQL history gate 기록을 유지한다. (완료)
7. **완료(코드·focused 계약 검증):** AP-1 producer atomic publish/readiness와 local
   `legacy`/Rocky `required` policy, AP-2 dedicated `0700` snapshot workspace,
   manifest parse 뒤 capacity gate, retry/full-refresh 공용 cross-worker gate를
   정합화했다. 현재 구현은 `SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT=1`만 허용한다.
   **2026-08-25 AP-2 검증 기록:** focused 통합 `126 passed in 87.32s`, full backend
   `573 passed, 5 skipped in 382.12s`; backend architecture/OpenAPI/compileall,
   Rocky validator, frontend architecture/API self-test/build도 통과했다.
8. **현재 완료·다음 우선순위:** menu policy, report layout 6 API,
   PPTX template 4 API, variable catalog 4 API, project workspace layout 5 route, import-schemas
   4 route, result-review bookmark+annotation GET/POST/PATCH 3 route, analysis-insights comparison/trust
   GET 2 route, load-case-overview GET 1 route, quality-thresholds GET + canonical PUT + deprecated alias PUT
   3 route, workflow detail/list GET 2 route와 request load-cases GET 1 route는 완료했다. 다음 vertical-slice
   우선순위는 **feature-examples 재감사 후 확정**한다. GET의 explicit project permission
   부재와 alias global semantics, row lock/CAS, post-commit fetch rollback seam, import-schemas DELETE의 별도
   permission connection·non-transactional usage check·명시적 domain delete audit 부재, review-list GET의
   explicit `PROJECT_DATA_VIEW` 부재, provider construction purity, dict mutation/storage normalization,
   unordered media/template, company-wide read와 single threshold semantics는 보존 debt로 유지하며 구조 refactor에
   섞지 않는다. **office-only release gate 우선순위:** PostgreSQL 18 app-role 권한·audit INSERT, correlated
   update parity, OIDC active-nonmember/cross-project 정책, 동시 update locking/CAS, 현실 데이터
   EXPLAIN/index/lock latency, (1) 실제 Rocky host install과 app-role
   startup preflight, NFS/SMB mount probe·승인 및 filesystem quota/capacity 확인,
   (2) production backup을 분리된 빈 DB에 복구하고 exported-snapshot inventory와
   app-role verifier를 대조, (3) 500 MiB/50 stream 부하와 5분 startup timeout 적정성,
   (4) PowerShell restore/backup 실실행과 7-day evidence-gated cleanup을 검증한다.
   native Windows Refresh는 계속 fail-closed다.
