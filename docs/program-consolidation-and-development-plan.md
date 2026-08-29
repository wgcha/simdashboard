# 프로그램 정리 및 개발 계획

- 기준일: 2026-08-25
- 상태: 실행 계획 + Run Identity V2 기준선 반영
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
운영 환경 검증이 필요한 media write/storage-mode 변경은 release gate로 남기고, 다음 안전
구조 단위는 `workbench`의 read-only 업무 유형·작업 계획 조회 경계부터 시작한다.

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
8. **다음 권장 release gate 우선순위:** (1) 실제 Rocky host install과 app-role
   startup preflight, NFS/SMB mount probe·승인 및 filesystem quota/capacity 확인,
   (2) production backup을 분리된 빈 DB에 복구하고 exported-snapshot inventory와
   app-role verifier를 대조, (3) 500 MiB/50 stream 부하와 5분 startup timeout 적정성,
   (4) PowerShell restore/backup 실실행과 7-day evidence-gated cleanup을 검증한다.
   native Windows Refresh는 계속 fail-closed다.
