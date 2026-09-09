# DB·결과 폴더·파일 확장자 계약

- 기준일: 2026-09-08
- 상태: 현재 구현 기준 + 잔여 개선 항목
- 관련 코드: `backend/app/config.py`, `database_connection.py`, `folder_import.py`, `parsers/manifest_format.py`, `parsers/manifest_parser.py`, `media_policy.py`, `services/bundle_fingerprint.py`, `services/canonical_result_bundle.py`, `services/bundle_snapshot.py`, `services/result_bundle_publisher.py`, `scripts/publish_result_bundle.py`, `services/master_result_refresh.py`, `application/results/commands.py`, `adapters/persistence/result_ingestion.py`, `services/media_storage_service.py`

이 문서는 DB 실행 위치, 결과 수집 폴더, manifest mapping, 허용 확장자와 실제 저장 방식을 하나의 기준으로 정리한다. GitHub [#13](https://github.com/wgcha/simdashboard/issues/13), [#14](https://github.com/wgcha/simdashboard/issues/14)의 upstream 요구사항은 아래 실행 계약과 구분해 기록한다.

## 1. DB 실행 계약

| profile | 설정 | 실제 저장 위치와 책임 |
|---|---|---|
| 로컬 개발 | `ANALYSIS_DB_BACKEND=duckdb` | 기본 `backend/data/analysis_dashboard.duckdb`; `ANALYSIS_DUCKDB_PATH`로 변경 가능 |
| 운영 | `ANALYSIS_DB_BACKEND=postgresql` | `DATABASE_URL`의 PostgreSQL 18 DB; 로컬 DB 폴더 개념이 없음 |

PostgreSQL schema의 원본은 `backend/migrations/versions/`의 Alembic revision이다. `backend/migrations/schema.sql`은 초기 schema와 이관 도구의 기준으로 함께 유지한다. PostgreSQL 실행 중에는 애플리케이션이 DDL을 수행하지 않는다.

다음 파일은 생성물이며 Git에 저장하지 않는다.

```text
backend/data/*.duckdb
backend/data/*.wal
backend/data/e2e-playwright.duckdb
backend/backups/
transfer-bundles/
```

## 2. 폴더 구조의 적용 범위

| 구분 | 출처·용도 | 현재 처리 |
|---|---|---|
| canonical 결과 import layout | 이 문서와 `examples/master-results/` | 실행 가능. `SIMDASH_IMPORT_ROOT` 아래 canonical `manifest.json`과 `mappings`를 실제 parser/UoW가 처리한다. |
| SPDM discovery layout | [#13](https://github.com/wgcha/simdashboard/issues/13)의 상위 SPDM 폴더 구조 | 별도 `SIMDASH_SPDM_ROOT` adapter의 대상이다. 의뢰 탐색·파일 업로드·직접 저장 파일 연결은 [SPDM 저장 폴더 연계](spdm-storage-workflow.md)를 따른다. |
| DB 저장 | DuckDB 파일 또는 PostgreSQL schema | canonical 수집은 검증한 manifest context ID로 연결한다. SPDM adapter는 검증한 구조와 명시적 폴더 binding으로 별도 연결한다. |

SPDM adapter는 별도 허용 root, 프로젝트·의뢰 mapping, 완료 파일 판정과 명시적
결과 등록 command를 제공한다. 아래 canonical manifest 수집의 read-only root와
publisher 계약은 SPDM 업로드 경로에 적용하지 않는다. SPDM 지원 상태와 검증 기록은
[SPDM 저장 폴더 연계](spdm-storage-workflow.md)에 기록한다.

[#13](https://github.com/wgcha/simdashboard/issues/13)의 대표 discovery 구조는 다음과
같다. 이 tree는 SPDM adapter의 의뢰 분류 기준이다. 다음 절의 `SIMDASH_IMPORT_ROOT`
canonical manifest 수집과는 서로 다른 진입점이다.

```text
Project_.../
├─ INPUT/
│  └─ OriginalCAD/{Set,Cushion,Stand}/
├─ WR_0001_SimType1/  (사용 환경)
│  ├─ SimCAD_Type1/
│  ├─ 보고서/
│  ├─ TEST/
│  └─ CAE/
│     ├─ Assy_Model.../{CMS,Modal,2kgfPush}/
│     ├─ Set_Model.../{Deflection,CMS,Modal,2kgfPush,Stiffness}/
│     ├─ Assy_RES.../{Settle,Wobble,Horizontal_Force_Angle,Slope_Angle}/
│     └─ ChRear/Stiffness/
└─ WR_0001_SimType2/  (유통 환경)
   ├─ SimCAD_Type1/
   ├─ 보고서/
   ├─ TEST/
   └─ CAE/Assy_Set.../{Drop,Clamping}/.../{INDIVIDUAL,CUMULATIVE}/
```

## 3. 마스터 결과 폴더

서버가 읽을 수 있는 최상위 폴더는 `SIMDASH_IMPORT_ROOT` 하나다. 클라이언트는 절대 경로나 서버 경로를 API로 전달하지 않는다.

권장 구조:

```text
{SIMDASH_IMPORT_ROOT}/
└─ {project_id}/
   └─ {request_id}/
      └─ {load_case_id}/
         └─ {run_id}/
            ├─ manifest.json
            ├─ .simdashboard-ready.json
            ├─ results/
            │  └─ scalar-results.json
            ├─ curves/
            │  └─ stress-time.csv
            ├─ media/
            │  └─ contour.svg
            └─ models/
               └─ result.gltf
```

실제 DB 연결은 `manifest.context`의 ID로 결정한다. readiness `required`에서는 marker가
있는 bundle만 full scan 후보가 되며, marker 검증은 physical 네 segment path와
`manifest.context`/publication ID가 일치하는지도 확인한다. `legacy`에서는 marker 없는
기존 manifest도 호환 discovery 대상이지만, marker가 관찰된 bundle은 항상 strict
검증으로 승격되어 손상 marker를 legacy로 우회하지 않는다.

## 4. 현재 canonical manifest

중앙 `backend/app/parsers/manifest_format.py`가 manifest format을 먼저 식별한다.
`mappings`와 `result_files` 중 정확히 하나만 선택되며, 둘을 함께 가진 mixed
manifest, discriminator가 없는 unknown manifest, JSON object가 아닌 root는
fail-closed로 거부한다.

마스터 Refresh와 versioned folder importer는
`load_manifest(..., expected_format=ManifestFormat.CANONICAL_MAPPINGS)`를 사용하고,
legacy `result_files`는
`load_manifest(..., expected_format=ManifestFormat.LEGACY_RESULT_FILES)`를 사용하는
parser compatibility 경로다. 이 형식의 persistence service/repository는 제거되어
runtime 결과 쓰기를 수행하지 않는다.

마스터 Refresh가 읽는 canonical 형식은 `mappings` 기반 manifest다.

```json
{
  "schema_id": "master-results-v1",
  "version": 1,
  "source_run_id": "producer-stable-run-id",
  "conflict_policy": "SKIP",
  "solver": "Radioss",
  "context": {
    "project_id": "project-tv-001",
    "request_id": "request-drop-001",
    "load_case_id": "loadcase-drop-bottom-001"
  },
  "mappings": [
    {"kind": "typed_scalars", "path": "results/scalar-results.json"},
    {
      "kind": "curve_csv",
      "path": "curves/stress-time.csv",
      "variable_key": "top_edge_stress_time",
      "x_column": "time_ms",
      "y_column": "stress_mpa"
    },
    {
      "kind": "media",
      "path": "media/contour.svg",
      "variable_key": "stress_contour",
      "asset_type": "IMAGE",
      "mime_type": "image/svg+xml"
    }
  ]
}
```

### mapping 종류

| kind | 파일 | 필수 계약 | DB 저장 |
|---|---|---|---|
| `typed_scalars` | JSON 배열 | `variable_key`, `data_type`, `value` | `scalar_results` |
| `curve_csv` | CSV | `variable_key`, `x_column`, `y_column` | `curve_results`, `curve_points`, `time_series_results` |
| `media` | 허용 미디어 | `variable_key`, `asset_type`, `mime_type` | `media_assets`, `asset_blobs`, `asset_blob_chunks` |

scalar `data_type`은 `FLOAT`, `INTEGER`, `TEXT`, `VERDICT`, `STATUS`, `BOOLEAN`을 지원한다. 숫자는 NaN과 무한대를 허용하지 않는다.

구형 `result_files` 기반 manifest와 `OPEN_CELL_STRESS` 같은 parser 계약은
현재 compatibility 경로로 격리되어 있다. 기존 외부 호출자의
`ManifestParser` 이름은 `LegacyResultFilesManifestParser` alias로 유지되며,
canonical importer가 legacy manifest를 읽거나 legacy importer가 canonical
manifest를 읽는 경우 모두 fail-closed한다.

이 중앙 detector/loader는 format과 manifest 경계를 통일한다. canonical
Master Refresh와 versioned folder-import example은 공통
`ResultIngestionCommand`/`ResultIngestionUnitOfWork`를 통해 같은 single-connection
transaction으로 결과를 저장한다. parser별 차이는 adapter와 normalized contract
안에서만 허용한다.

일반 수동 `SUMMARY_RESULT` JSON/CSV와 `Radioss` mesh CSV upload는 공통 UoW를
사용한다. `source_name`은 target-qualified이고 content checksum으로 동일 재시도를
`SKIPPED`한다. write transaction 안에서 권한을 재확인하고 `RESULT_IMPORTED` audit
event를 함께 기록한다. Radioss adapter는 scalar, time-series/curve,
`result_locations`를 같은 transaction으로 전달하므로 부분 위치 행을 남기지 않는다.

구형 `result_files` 기반 persistence service/repository는 현재 canonical schema에
없는 `result_import_jobs` 테이블과 `analysis_runs` 확장 컬럼을 참조해 동작하지
않았으므로 제거했다. legacy manifest schema, `ManifestParser` alias와 normalized
parser adapter는 parser/manifest 형식 호환 전용으로 남는다. runtime 결과 쓰기는
공통 `ResultIngestionUnitOfWork`만 사용한다.

## 5. 허용 확장자와 MIME

[#14](https://github.com/wgcha/simdashboard/issues/14)의 source/solver/result/report
확장자와 명명 inventory는 producer와 보관·연계 범위를 식별하는 목록이다.
SPDM adapter는 아래 원본을 저장·다운로드할 수 있지만, 원본 보관과 수치 parser
지원은 다르다. canonical manifest mapping과 수동 수치 등록은 기존 JSON/CSV·미디어
정책을 유지한다. 새 수치 parser나 inline media 형식은 MIME·signature 정책,
size limit, 예제와 정상/실패 테스트를 함께 추가한 뒤에만 허용한다.

| #14 inventory 범주 | 대표 확장자·명명 | 현재 import 의미 |
|---|---|---|
| CAD source | `.prt`, `.x_t` | SPDM `inputs` 원본 보관; 수치 parser 없음 |
| model | `.hm`, `.mdl` | SPDM `inputs` 원본 보관; 모델 실행·변환 없음 |
| solver input | `.fem`, `.rad`, `.xml` | SPDM `inputs` 원본 보관; solver 실행 없음 |
| solver result | `.h3d` | SPDM `solver` 원본 보관; H3D 수치 decoding 없음 |
| document output | `.csv`, `.ppt`, `.pdf`, `.json` | CSV/JSON은 지원 수치 형식을 검증; PPT/PPTX/PDF는 SPDM 의뢰 보고서 폴더에 원본 보관 |
| vibration output | `.txt`, `.pkl`, `.png` | TXT/PKL은 SPDM `solver` 원본 보관(PKL 역직렬화 없음); PNG 원본 보관과 기존 canonical media 정책은 별도 |
| naming token | `Deflection`, `CMS`, `StandFailure`, `Stiffness_ChRear`, `1st`, `2nd`, `result` | 파일 발견/분류 후보일 뿐 parser 선택·권한·DB mapping을 우회하지 않음 |

### 구조화 결과

| 용도 | 확장자 | 형식 |
|---|---|---|
| manifest·typed scalar | `.json` | UTF-8 JSON |
| curve·time series·Radioss mesh 결과 | `.csv` | UTF-8 또는 UTF-8 BOM CSV |

### 미디어

| asset type | 확장자 | MIME | 최대 크기 |
|---|---|---|---:|
| `IMAGE`/`CONTOUR_IMAGE` | `.png` | `image/png` | 25 MiB |
|  | `.jpg`, `.jpeg` | `image/jpeg` | 25 MiB |
|  | `.webp` | `image/webp` | 25 MiB |
|  | `.svg` | `image/svg+xml` | 25 MiB |
| `VIDEO` | `.mp4` | `video/mp4` | 500 MiB |
|  | `.webm` | `video/webm` | 500 MiB |
| `MODEL_3D` | `.glb` | `model/gltf-binary` | 100 MiB |
|  | `.gltf` | `model/gltf+json` | 100 MiB |

확장자와 manifest의 MIME이 일치해야 한다. 추가로 PNG/JPEG/WebP signature, MP4 `ftyp`, WebM EBML, GLB `glTF`, glTF JSON 구조를 검사한다. SVG는 script, event handler, iframe/object/embed, 외부 URL과 active data URL을 거부한다.

허용된 모든 미디어 확장자(`.png`, `.jpg`, `.jpeg`, `.webp`, `.svg`, `.mp4`,
`.webm`, `.glb`, `.gltf`)는 [`backend/tests/test_media_policy_fixtures.py`](../backend/tests/test_media_policy_fixtures.py)의
generated minimal `tmp_path` fixture matrix로 검증한다. 각 확장자는 정상
signature/hash 수락, 확장자·MIME 불일치 거부, corrupt signature 거부를 각각
확인한다. 이 fixture는 임시 파일이며, 영구 canonical folder example은
JSON/CSV/SVG/glTF만 유지한다.

## 6. 경로와 보안

- mapping 경로는 bundle 내부 상대 경로만 허용한다.
- 절대 경로와 `..` traversal을 거부한다.
- 중앙 manifest path resolver는 root 밖 경로와 manifest symlink를 거부한다.
- Master Refresh도 bundle·mapping 결과 파일의 root escape와 symlink를 별도로 거부한다.
- 오류 응답에 서버 절대 경로를 노출하지 않는다.
- 파일 하나의 실패가 다른 manifest import를 중단시키지 않는다.
- 신규 결과는 한 manifest 단위 transaction으로 저장한다.

## 7. 미디어 저장 방식

현재 신규 canonical import와 reference/seed write의 정본은 DB blob이다. 파일 바이트는
최대 1 MiB 청크로 `asset_blob_chunks`에 먼저 저장하고 `asset_blobs`가 전체 SHA-256,
크기, 청크 수를 가지며, 같은 `(sha256, file_size)`는 deduplicate한다. media row는
같은 transaction에서 `blob_id`, `original_filename`, `mime_type`과 실제 size/hash를
연결한다.

`blob_id`, `original_filename`, `mime_type`이 신규 canonical write의 실제 계약이다.
PostgreSQL과 DuckDB 개발 DB 모두 같은 repository/service 계약을 사용한다.

현재 서비스 mode는 `SIMDASH_MEDIA_STORAGE_MODE=dual-read`(기본) 또는
`database-only`로 명시한다. dual-read에서만 `media_assets.file_path`와 허용 demo의
검증된 내부 filesystem fallback을 사용하며, database-only에서는 blob/DB row가 없을
때 404 또는 빈 DB catalog로 fail-closed한다. Rocky 설치 기본은 database-only이고
설치 후 및 systemd startup app-role preflight를 실행한다. HTTP 응답은 언제나
`asset_id` 또는 `video_id`로 접근하고 서버 파일 경로를 노출하지 않는다.

P1-03의 구현·검증 범위는 blob-first canonical/seed write, 현재 Alembic head를
따르는 database-only verifier, migration dry-run/preflight/idempotent 실행 도구,
그리고 `scripts/media_transfer_manifest.py`의 strict shared inventory다. verifier는
unbound `media_assets`, blob/chunk checksum·길이·참조, reference demo load case가
있을 때 exact allowlist 20개(없으면 expected/actual 0개), PostgreSQL app role·connection
budget을 확인한다. backup은 pg_dump와 같은 exported
snapshot에서 inventory를 만들고 restore는 app-role exact comparison 뒤 verifier를
실행한다. transfer bundle v2는 blob-bound media asset을 ZIP에 중복 포함하지 않고
v1은 fail-closed한다. migration/cleanup receipt는 O_EXCL 예약형 no-overwrite
`PENDING`→
`COMPLETED`/`FAILED` recoverable journal이며 cleanup은
receipt·실제 regular non-symlink backup dump·backup manifest·approval/confirmation과
7-day 조건을 묶는 evidence-gated 명시 실행이다. manifest만으로는 충분하지 않고 dump의
filename·bytes·streamed SHA-256 및 `pg_restore --list`가 DB 접근 전에 정확히 일치해야
한다. 삭제는 same-parent private quarantine으로 원자 rename한 뒤 inode/hash 재검증을
통과한 파일만 수행하고 불일치는 quarantine에 보존한다. 이는 **코드·disposable 자동검증 완료, 운영 증적 대기**이며 실제 Rocky
data migration·빈 DB restore rehearsal·부하 검증을 대신하지 않는다.

database-only cutover는 다음 운영 release gate가 모두 충족된 뒤에만 별도 승인한다.

1. Rocky 운영 데이터의 사전검사와 idempotent migration이 완료되고 모든 참조가 blob에 연결된다.
2. 분리된 빈 PostgreSQL restore에서 backup media inventory와 전체 blob/chunk checksum,
   reference demo backup이면 exact 20개 demo MP4, fresh `SEED_MODE=empty` production
   backup이면 expected/actual 0개 및 verifier 결과가 일치한다.
3. 검증된 backup과 gate 통과 뒤 legacy 원본을 최소 7일 보존하고, migration receipt,
   실제 `--backup BACKUP.dump`, backup manifest, approval ID와 `--execute --confirm
   --migration-id ID --cleanup-receipt PATH` confirmation을 포함한 비자동 cleanup을
   실행한다. manifest만으로는 충분하지 않고 dump의 filename·bytes·SHA-256을 stream
   검증한다.
4. 운영 DB volume에서 500 MiB/동시 50 stream/10분 Range·seek 부하와 recovery 절차를
   기록한다. 실제 Rocky/NFS·quota, production backup→빈 DB restore rehearsal,
   PowerShell 실실행과 5분 startup timeout 적정성은 외부 release gate다.

backup 및 restore의 운영 절차와 필요한 증적은
[`deployment-security-backup-guide.md`](deployment-security-backup-guide.md)를 따른다.

- `GET`/`HEAD`
- 단일 byte Range와 `206`/`416`
- ETag와 `304`
- 원본 filename download
- `nosniff`, SVG CSP

## 8. 중복과 변경 감지

현재 canonical Master Refresh는 manifest와 모든 canonical mapping 파일을 포함한
bundle fingerprint로 이미 완료된 import를 `SKIPPED`한다. fingerprint 항목은
bundle 내부 상대 경로, 파일 크기, 파일 내용의 SHA-256이며 상대 경로 순으로
정렬한다. `analysis_run_metadata.source_checksum`에는 fingerprint를 저장하고,
`metadata_json` 및 job summary에는 원래 manifest checksum과 fingerprint를 모두
보존한다.

현재 계약:

1. manifest와 각 mapping 파일의 SHA-256과 크기를 canonical 순서로 계산한다.
2. 파일 항목을 직렬화한 bundle fingerprint를 저장하고 manifest checksum은 별도 metadata로 보존한다.
3. 동일 fingerprint는 `SKIPPED`한다.
4. 다른 fingerprint는 새 Run으로 처리한다.

현재 PostgreSQL 보호와 검증:

5. canonical ingestion은 load case별 namespaced 64-bit transaction advisory lock으로 `run_no` 할당을 직렬화한다.
6. migration `0017_run_identity_v2`의 `canonical_result_ingestion_source_versions`가
   `(load_case_id, source_type, source_key, source_revision)`별 checksum, server-assigned
   run, immutable `supersedes_analysis_run_id`를 정본으로 보존한다. 이전 `0016`
   `canonical_result_ingestion_sources`는 historical backfill 입력이며 runtime identity
   reservation 정본이 아니다.
7. 명시적 `source_run_id`의 변경 checksum은 `SKIP`, `REJECT`, immutable `REPLACE`로
   처리한다. Master manifest의 `REPLACE`는 fail-closed이며 실패 transaction은 source
   version reservation과 결과 write를 함께 rollback한다.
8. `backend/tests/test_postgres_result_ingestion_concurrency.py`는 전용 PostgreSQL
   test DB의 독립 연결로 exact NOOP, `SKIP`/`REJECT`/immutable `REPLACE`와 고유 `run_no`를
   검증한다. 2026-08-25 disposable PostgreSQL 18.6 live gate에서 blank `0001→0017`,
   `0016→0017` backfill, app-role DDL 거부, pool budget, reference seed 및 concurrency
   cases를 통과했고 cluster/DB/port를 정리했다.
9. importer private snapshot/rehash와 parser workload limits는 구현되어 fingerprint
   이후 파일 교체가 parse·media 저장 provenance를 바꾸지 못하게 한다. producer atomic
   publish/readiness와 local `legacy`/Rocky `required` marker policy는 AP-1 코드·WSL
   검증을 완료했다. integrated focused `122 passed in 77.07s`, full backend
   `533 passed, 5 skipped in 366.02s`, strict checked-in example marker import,
   architecture/OpenAPI/Rocky template/compileall도 통과했다. 실제 Rocky host deploy와
   NFS/SMB mount capability 검증은 아직 운영 release gate다. 다음 단계는 dedicated
   snapshot workspace quota와 cross-worker refresh lock/budget(AP-2)이다.

### 8.1 Producer snapshot과 import limits

Refresh는 live manifest를 먼저 private snapshot으로 복사하고, 복사된 manifest를
검증한 뒤 그 manifest가 선언한 파일을 private snapshot으로 이어서 캡처·parse한다.
fingerprint와 parser는 동일한 captured bytes를 사용해야 한다. 원본 파일이 캡처
이후 교체·삭제되더라도 해당 import의 provenance와 결과는 캡처 시점의 bytes에
고정된다. 캡처 대상은 regular file만 허용하며 manifest와 mapping 경로는
absolute/traversal/backslash/dot/duplicate path 및 중간·최종 symlink를 거부한다.

Master Refresh의 secure snapshot traversal은 POSIX `dir_fd` 상대 open과
`O_NOFOLLOW`를 사용하며 canonical WSL 개발 환경과 Rocky Linux 운영 경로를
지원한다. native Windows compatibility profile에서는 Windows handle 기반의
동등한 safe traversal adapter가 제공되기 전까지 이 endpoint를 fail-closed한다.
이 제한은 수동 upload나 다른 compatibility 기능에는 적용되지 않는다.

다음 환경변수는 snapshot capture와 parser workload를 함께 제한한다. 기본값은
`.env.example`에 있으며 운영 환경에서는 저장소 용량과 producer 계약에 맞게
명시적으로 조정한다.

```text
SIMDASH_IMPORT_MAX_MANIFEST_BYTES    # manifest.json 최대 bytes
SIMDASH_IMPORT_MAX_MAPPING_COUNT     # bundle mapping 최대 개수
SIMDASH_IMPORT_MAX_FILE_BYTES        # mapping 파일 1개 최대 bytes
SIMDASH_IMPORT_MAX_TOTAL_BYTES       # manifest + mapping 전체 최대 bytes
SIMDASH_IMPORT_MAX_STRUCTURED_BYTES  # typed_scalars/curve_csv 1개 최대 bytes (기본 8MiB, hard max 64MiB; snapshot copy 전 적용)
SIMDASH_IMPORT_MAX_SCALAR_RECORDS    # bundle 전체 scalar record 최대 개수
SIMDASH_IMPORT_MAX_CURVES            # bundle 전체 curve 최대 개수
SIMDASH_IMPORT_MAX_CURVE_POINTS      # curve 1개 및 bundle 전체 point 최대 개수
```

standalone `scan_folder()` 경로도 Master Refresh와 동일하게 media 파일에
`SIMDASH_IMPORT_MAX_FILE_BYTES`를 적용한다.

Master Refresh private snapshot은 mapping `kind`를 먼저 확인한다. `typed_scalars`와
`curve_csv`는 `SIMDASH_IMPORT_MAX_STRUCTURED_BYTES`, `media`는
`SIMDASH_IMPORT_MAX_FILE_BYTES`를 각각 snapshot copy 전에 적용한다. 지원하지 않는
kind는 파일을 열기 전에 거부한다. manifest 자체와 bundle 전체에는 별도의 manifest·total
byte ceiling도 적용한다.

canonical manifest와 typed scalar JSON은 `backend/requirements.txt`와
`backend/requirements.lock`에 `ijson==3.5.1`로 고정한 public bounded event
parser를 사용한다. manifest는 mapping cap+1에서 중단하며, 전체 parser event도
`256 + 64 * SIMDASH_IMPORT_MAX_MAPPING_COUNT` ceiling을 넘기기 전에 거부한다.
scalar stream은 record cap+1에서 중단하고 단일 scalar item은 64 event ceiling을
넘기기 전에 `ObjectBuilder`로 전달하지 않는다. scalar checksum도 같은 bounded
stream이 읽은 bytes에서 계산한다. raw bounded stream은 unpaired Unicode surrogate
escape를 backend-independent하게 거부하고 valid surrogate pair와 direct UTF-8은
유지하며, ijson backend별 예외 진단은 공개 error taxonomy를 거쳐 importer의 고정
오류 코드로 정규화한다.

다만 최종 normalized scalar 최대 100,000개 list는 메모리에 materialize된다. 기본
`SIMDASH_IMPORT_MAX_STRUCTURED_BYTES`는 8MiB이고 설정 가능한 hard max는 64MiB이므로,
운영자는 worker 수와 동시 refresh를 포함한 메모리 예산 안에서 override해야 하며 이
한도를 절대적인 메모리 고갈 방지로 해석해서는 안 된다. requirements lock은 버전을
고정할 뿐 Windows compatibility profile과 Rocky 운영 target의 wheel 설치 가능성을
증명하지 않으므로, 해당 target의 actual wheel/offline 설치 smoke는 별도 release gate로
남긴다.

fingerprint는 live path를 다시 읽어 계산하지 않고 captured metadata/bytes에서
생성해야 한다. 캡처 또는 fingerprint 단계에서 실패한 bundle은 결과 Run을 만들지
않는다. 안전하게 검증된 target이 있는 경우에만 실패 job에 제한 위반·경로·snapshot
error code를 남기며, target을 검증할 수 없는 경우에는 DB job을 만들지 않고
sanitized refresh item 오류로 반환한다. snapshot은 하나의
import가 정확히 어떤 bytes를 읽었는지는 고정하지만, producer가 여러 파일을
서로 다른 시점에 쓰는 상황에서 논리적으로 일관된 generation을 보장하지는 않는다.
producer는 결과 파일을 in-place로 쓰지 않는다. Linux 지원 filesystem에서는 final
directory와 같은 부모 아래 private sibling staging directory에 payload를 완성하고 각
payload file 및 디렉터리를 `fsync`한다. 그 뒤 실제 bytes에서 marker v1을 만들고
`.simdashboard-ready.json`을 **마지막**으로 write+`fsync`, staging directory를
`fsync`한 뒤 `renameat2(..., RENAME_NOREPLACE)`로 final publication path에 commit하고
마지막으로 parent directory를 `fsync`한다. marker는 `schema_id`, `version`, `state`,
`bundle_path`, `manifest_checksum`, `bundle_fingerprint`, `entry_count`, `published_at`
필드를 정확히 가진다. marker는 idempotency fingerprint entry에 포함하지 않는다.
`RESULT_BUNDLE_PARENT_FSYNC_FAILED`는 final directory가 이미 visible해진 뒤 parent
directory durability 확인이 실패한 경우다. publisher는 이 final을 삭제·교체하지 않으므로
운영자는 blind retry 대신 marker/final 내용과 filesystem durability를 확인해야 한다.

`SIMDASH_IMPORT_READINESS_POLICY=legacy`는 local/default compatibility이며 marker
없는 기존 manifest도 full scan한다. `required`는 marker 없는 manifest를 full scan에서
완전히 무시한다. DB에 이미 기록된 failed/REJECTED Master job의 retry는 stored relative
manifest path만 재사용하고 `required` capture를 적용하므로 missing/invalid marker도
원 target에 새 FAILED attempt로 남는다. Rocky installer와 deployment preflight는
반드시 `required`만 허용한다.

지원 1차 대상은 WSL과 Rocky Linux의 local ext4/XFS다. native Windows, cross-device
`EXDEV`, `renameat2`/`RENAME_NOREPLACE` 미지원은 copy/replace fallback 없이
fail-closed한다. NFS/SMB는 mount capability probe와 조직 승인 전에는 production
publication target으로 승인되지 않는다.

## 9. 실제 예제와 검증

canonical 예제:

```text
examples/master-results/
└─ project-tv-001/request-drop-001/loadcase-drop-bottom-001/run-example-001/
```

기존 typed-result 예제는 `examples/typed-results/tv-drop-chassis/`에 유지한다.

검증은 예제 폴더를 임시 `SIMDASH_IMPORT_ROOT`로 복사한 뒤 실제
`scan_folder()` 또는 `MasterResultRefreshService`가 다음을 만족하는지
확인한다. 중앙 manifest detector/loader와 legacy alias의 경계 테스트도
`backend/tests/test_manifest_format.py`에 있다.

- JSON scalar와 CSV curve parsing
- 미디어 MIME·magic·크기 검증
- 모든 허용 미디어 확장자의 generated `tmp_path` fixture 수락·MIME 불일치·corrupt signature 거부
- `scalar_results`, `curve_results`, `curve_points`, `result_locations`, `media_assets` 저장
- media `blob_id`와 blob chunk 생성
- 두 번째 Refresh의 idempotent `SKIPPED`
- manifest는 같고 mapping 파일만 변경된 Refresh의 새 Run 생성
- metadata의 manifest checksum과 bundle fingerprint 보존
- traversal, symlink, 잘못된 MIME/확장자 거부
- mixed/unknown manifest와 잘못된 importer 선택 거부
- 공통 UoW의 transaction failure injection 시 부분 Run/job/result 행 미생성
- manual `SUMMARY_RESULT` JSON/CSV와 Radioss mesh CSV의 공통 UoW 저장·target-qualified source·재시도 `SKIPPED`·audit/auth 재확인·rollback

## 10. 운영 배치

`SIMDASH_IMPORT_ROOT`는 애플리케이션 release 디렉터리 밖의 지속 저장소다. 권장 예시는 `/var/lib/simdashboard/import` 또는 승인된 사내 공유 경로다.

운영 배포에서 다음을 함께 결정해야 한다.

- systemd service user의 읽기 권한
- NAS/NFS/SMB mount와 재연결 정책
- SELinux label 또는 read-only bind 정책
- root 용량·파일 수·manifest 수 제한
- backup 대상인지, 재생성 가능한 source인지
- 실패 파일 격리와 재처리 운영 절차

Rocky installer는 `SIMDASH_IMPORT_ROOT`와 `SIMDASH_IMPORT_READINESS_POLICY=required`를 root 전용 service EnvironmentFile에
전달하고 systemd `ReadOnlyPaths`와 `RequiresMountsFor`로 보호한다. 기본 local root는
`/var/lib/simdashboard/import`이며 `root:simdashboard`, mode `0750`, SELinux
`var_lib_t`를 사용한다. 외부 NAS/NFS/SMB root는 installer가 ownership/mode를
변경하지 않으므로, mount 완료·서비스 계정의 재귀 읽기/실행 권한·조직 SELinux
정책을 배포 전 승인해야 한다.

publisher는 application service 계정이 아닌 별도 producer/root 계정에서 실행한다.
application service는 `SIMDASH_IMPORT_ROOT`를 systemd `ReadOnlyPaths`로 열고 import만
수행한다. #13 SPDM directory tree와 #14 source/result extension inventory는 publisher
input을 자동 허용하지 않는다. producer가 canonical `manifest.json`, 허용 mapping,
안정 `source_run_id`, `conflict_policy`를 생성·검증한 경우에만 publication한다.
