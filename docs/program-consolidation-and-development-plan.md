# 프로그램 정리 및 개발 계획

- 기준일: 2026-08-24
- 상태: 실행 계획
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
| IMP-01 | 중앙 format detector/loader와 normalized contract를 사용하고 canonical Master Refresh·typed folder example·일반 manual `SUMMARY_RESULT` JSON/CSV를 공통 UoW로 적재 | Radioss mesh `result_locations` direct-SQL 경로와 legacy persistence는 잔여 | P1 진행 |
| IMP-02 | bundle fingerprint, load-case advisory lock, migration 0016 exact source reservation 구현 | 동일 `run_id` replace·live PostgreSQL concurrency test·TOCTOU snapshot은 잔여 | P1 진행 |
| DEP-01 | Rocky install env·service EnvironmentFile·systemd read-only path에 `SIMDASH_IMPORT_ROOT` wiring과 외부 mount/read preflight를 반영 | 구현 완료, 실제 Rocky host release gate 검증 필요 | P0 진행 |
| DEP-02 | Rocky 8 + nginx + systemd + PostgreSQL을 canonical target으로 ADR 확정하고 Windows를 compatibility profile로 명시 | 문서 결정 완료 | P0 완료 |
| DEP-03 | Windows는 설치/개발 실행과 DB 이관 호환성은 있으나 HTTPS reverse proxy·service·TLS·rollback 운영 자동화 없음 | Windows one-command 운영 배포는 지원 범위에서 제외 | 범위 제외 |
| DEP-04 | 사내 proxy는 Windows setup 중심이고 Rocky DNF/CA/offline RPM 계약이 불완전 | 폐쇄망 설치 재현성 부족 | P1 |
| SEC-01 | nginx forwarded header, trusted host, security header 정책 보강 필요 | audit IP 신뢰와 외부 노출 hardening 부족 | P0/P1 |
| ARC-01 | `main.py`, `database.py`, workbench router/repository, `App.tsx`가 큼 | 기능 추가 시 충돌·회귀 비용 증가 | P1 |
| API-01 | 일부 성공 응답이 익명 OpenAPI schema | frontend runtime adapter 수동 검증 지속 | P1 |

## 3. GitHub Issues 병합 상태

요청된 `https://github.com/wgcha/simdashboard/issues`는 private 저장소로 확인되며 현재 자동화 환경의 비인증 GitHub API와 웹 요청에서 404가 반환되었다. 이슈 내용을 추측해서 번호나 결정을 만들지 않는다.

이슈 export를 확보하면 다음 표에 실제 번호와 링크를 추가한다.

| 추적 키 | 이슈 번호 | 주제 | 현재 로컬 근거 | 반영 문서 |
|---|---:|---|---|---|
| GH-DB-FOLDER | 확인 필요 | DB/결과 폴더 구조 | `master_result_refresh.py`, 기존 import 문서 | `storage-folder-and-file-contract.md` |
| GH-EXTENSIONS | 확인 필요 | 저장 확장자·MIME | `media_policy.py`, `media_storage_service.py` | `storage-folder-and-file-contract.md` |
| GH-DEV-PROXY | 확인 필요 | 개발 적용 proxy | `vite.config.ts`, `setup.ps1` | 이 문서, `development-workflow.md` |
| GH-DEPLOY | 확인 필요 | 사내 일괄 deploy | `deploy/rocky8`, Windows script | 이 문서, 배포 runbook |

private 이슈에만 운영 계약을 남기지 않는다. 확정된 내용은 저장소 문서·환경 예제·자동 테스트에도 복제한다.

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
- GitHub 이슈 export를 확보해 GH 추적 표를 채운다.
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

일반 수동 `SUMMARY_RESULT` JSON/CSV upload는 target-qualified `source_name`과
content checksum을 사용해 공통 UoW로 이관되었다. 재시도는 `SKIPPED`하고 write
transaction 안에서 권한을 재확인하며 `RESULT_IMPORTED` audit event를 함께
기록한다. `Radioss` mesh CSV는 `result_locations` persistence가 canonical UoW에
아직 포함되지 않아 direct-SQL 경로에 남아 있다.

legacy `ResultImportService` persistence는 canonical schema에 없는
`result_import_jobs` 테이블과 `analysis_runs` 확장 컬럼을 참조하므로 비운영
compatibility 경로로 유지한다.

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

상태: 부분 완료. canonical Master Refresh, typed folder-import example, 일반
manual `SUMMARY_RESULT` JSON/CSV의 공통 command/domain port/application
orchestration/SQL UoW 이관은 완료했다. Radioss mesh `result_locations`와 legacy
persistence 이관은 남아 있다.

- P0에서 고정한 중앙 manifest detector/loader를 canonical importer와 legacy
  adapter가 명시적으로 사용한다.
- discovery/preflight/parser와 persistence/status sync를 command/UoW 경계로 분리했다.
- typed folder import와 Master Refresh가 같은 validation·persistence UoW를 사용한다.
- Radioss mesh `result_locations`를 canonical UoW result contract로 이관한다.
- legacy `ResultImportService` persistence는 현행 schema에 맞는 run identity와
  replace 정책을 정한 뒤 같은 UoW로 이관한다.
- parser adapter만 결과 형식별로 교체한다.
- 파일·행·포인트·manifest 개수 제한을 설정한다.

#### P1-02 bundle fingerprint와 Run identity

상태: fingerprint와 PostgreSQL duplicate reservation 기초 구현 완료, identity
정책과 live concurrency 검증은 잔여.

- manifest와 매핑 파일의 checksum/size를 canonical 정렬해 fingerprint를 만든다.
- 동일 fingerprint는 skip하고, 현재 다른 fingerprint는 새 Run으로 처리한다.
- metadata와 job summary에 manifest checksum과 bundle fingerprint를 함께 보존한다.
- PostgreSQL에서는 load case별 namespaced 64-bit transaction advisory lock으로
  `run_no` 할당을 직렬화한다.
- non-null exact `(source_type, source_name, source_checksum)`는 migration `0016`
  `canonical_result_ingestion_sources` PK로 예약하고 실패 transaction에서 rollback한다.
- `run_id`, `run_no`, overwrite policy를 계약에 추가한다.
- 실패/재시도/부분 성공의 감사 이벤트를 표준화한다.
- live PostgreSQL concurrent ingestion test를 추가한다. 현재는 SQL/migration
  contract test만 있다.
- 결과 producer의 임시 폴더 생성 후 atomic rename과 importer snapshot/rehash로
  fingerprint 계산 바이트와 실제 parse·media 저장 바이트의 일치를 보장한다.

#### P1-03 database-only media 마감

- 신규 media는 항상 blob에 연결한다.
- legacy `file_path` fallback의 종료 조건과 보존 기간을 정한다.
- database-only verifier를 현재 migration head와 동기화한다.
- backup/restore 후 blob/chunk checksum과 20개 demo MP4를 확인한다.

완료조건:

- 공통 UoW를 사용하는 import 경로가 같은 malformed input을 같은 코드로 거부
- 동일 bundle 반복 실행이 중복 Run을 만들지 않음
- 신규 `media_assets.blob_id IS NULL` 0건

모든 허용 media extension은 generated minimal `tmp_path` fixture로 정상 수락,
MIME/extension mismatch 거부, corrupt signature 거부를 검증한다. 영구 canonical
folder example은 JSON/CSV/SVG/glTF만 유지한다.

현재 결과 수집 focused 검증은 collection 기준 71개 test case다. manifest 경계,
fingerprint/mapping 변경, 공통 UoW rollback, manual SUMMARY_RESULT JSON/CSV,
media fixture matrix, PostgreSQL reservation SQL/migration contract와 endpoint
wiring을 포함하며 live PostgreSQL concurrent ingestion test는 포함하지 않는다.

### Phase 2 — 기능별 V2 구조 전환(P1)

#### Backend 순서

1. `result_ingestion`: Refresh·parser·media·status sync (공통 UoW 기반)
2. `workbench`: 업무 유형·작업계획·배치 실행·결과 snapshot
3. `access`: permission/project scope/audit transaction
4. `reports`: report context·layout·template
5. 남은 `main.py` endpoint

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

#### P3-02 Rocky one-command deploy

- signed release bundle 또는 조직 artifact repository를 사용한다.
- Python wheel뿐 아니라 OS RPM/internal repo/CA 전제를 preflight한다.
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
- PostgreSQL advisory lock·migration 0016 reservation contract 검증
- 모든 신규 media blob 연결·checksum 검증
- nginx config, TLS, forwarded header, SPA/API/assets/Range smoke
- backup→빈 DB restore rehearsal
- 문서 링크와 환경 예제 정합성

## 10. 다음 실행 순서

1. canonical 예제와 import 검증을 병합한다.
2. GitHub Issues export를 받아 GH 추적 표를 확정한다.
3. migration verifier와 이후 migration head 처리 방식을 고친다. (완료)
4. 운영 target ADR과 `SIMDASH_IMPORT_ROOT` 배포 연결을 구현한다. (구현 완료, Rocky host release validation 남음)
5. Radioss mesh locations와 legacy persistence를 공통 UoW로 이관하고 run identity/replace 정책을 확정한다.
6. live PostgreSQL concurrent ingestion test와 producer snapshot/rehash를 추가한다.
7. result ingestion부터 V2 vertical slice 전환을 시작한다.
