# DB·결과 폴더·파일 확장자 계약

- 기준일: 2026-08-24
- 상태: 현재 구현 기준 + 명시된 개선 항목
- 관련 코드: `backend/app/config.py`, `database_connection.py`, `folder_import.py`, `parsers/manifest_format.py`, `parsers/manifest_parser.py`, `media_policy.py`, `services/master_result_refresh.py`, `services/media_storage_service.py`

이 문서는 DB 실행 위치, 결과 수집 폴더, manifest mapping, 허용 확장자와 실제 저장 방식을 하나의 기준으로 정리한다.

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

## 2. 마스터 결과 폴더

서버가 읽을 수 있는 최상위 폴더는 `SIMDASH_IMPORT_ROOT` 하나다. 클라이언트는 절대 경로나 서버 경로를 API로 전달하지 않는다.

권장 구조:

```text
{SIMDASH_IMPORT_ROOT}/
└─ {project_id}/
   └─ {request_id}/
      └─ {load_case_id}/
         └─ {run_id}/
            ├─ manifest.json
            ├─ results/
            │  └─ scalar-results.json
            ├─ curves/
            │  └─ stress-time.csv
            ├─ media/
            │  ├─ contour.svg
            │  └─ animation.mp4
            └─ models/
               └─ result.gltf
```

현재 구현은 root 아래의 모든 `manifest.json`을 탐색하고 실제 DB 연결은 `manifest.context`의 ID로 결정한다. 위 폴더명은 운영 convention이며, 현재 코드는 네 단계 폴더명의 ID가 manifest ID와 같은지 강제하지 않는다. 폴더명 일치가 필수 요구라면 별도 validation으로 추가해야 한다.

## 3. 현재 canonical manifest

중앙 `backend/app/parsers/manifest_format.py`가 manifest format을 먼저 식별한다.
`mappings`와 `result_files` 중 정확히 하나만 선택되며, 둘을 함께 가진 mixed
manifest, discriminator가 없는 unknown manifest, JSON object가 아닌 root는
fail-closed로 거부한다.

마스터 Refresh와 versioned folder importer는
`load_manifest(..., expected_format=ManifestFormat.CANONICAL_MAPPINGS)`를 사용하고,
legacy `ResultImportService`는
`load_manifest(..., expected_format=ManifestFormat.LEGACY_RESULT_FILES)`를 사용하는
명시적 compatibility 경로다.

마스터 Refresh가 읽는 canonical 형식은 `mappings` 기반 manifest다.

```json
{
  "schema_id": "master-results-v1",
  "version": 1,
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

이 중앙 detector/loader는 format과 manifest 경계만 통일한다. canonical
`scan_folder`/Master Refresh와 legacy `ResultImportService`의 결과 validation,
persistence, transaction, overwrite/idempotency 정책은 아직 분리되어 있다.
두 경로를 하나의 ingestion service와 repository 계약으로 통합하는 작업은
P1 canonical ingestion 단계의 잔여 범위다.

## 4. 허용 확장자와 MIME

### 구조화 결과

| 용도 | 확장자 | 형식 |
|---|---|---|
| manifest·typed scalar | `.json` | UTF-8 JSON |
| curve·time series·Radioss 중간 결과 | `.csv` | UTF-8 또는 UTF-8 BOM CSV |

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

## 5. 경로와 보안

- mapping 경로는 bundle 내부 상대 경로만 허용한다.
- 절대 경로와 `..` traversal을 거부한다.
- 중앙 manifest path resolver는 root 밖 경로와 manifest symlink를 거부한다.
- Master Refresh도 bundle·mapping 결과 파일의 root escape와 symlink를 별도로 거부한다.
- 오류 응답에 서버 절대 경로를 노출하지 않는다.
- 파일 하나의 실패가 다른 manifest import를 중단시키지 않는다.
- 신규 결과는 한 manifest 단위 transaction으로 저장한다.

## 6. 미디어 저장 방식

현재 신규 import 미디어의 정본은 DB blob이다. 파일 바이트는 최대 1 MiB 청크로 `asset_blob_chunks`에 저장하고 `asset_blobs`가 전체 SHA-256, 크기, 청크 수를 가진다. 같은 `(sha256, file_size)`는 deduplicate한다.

`media_assets.file_path`는 논리 경로와 legacy fallback을 위해 남아 있다. `blob_id`, `original_filename`, `mime_type`이 신규 경로의 실제 계약이다. PostgreSQL과 DuckDB 개발 DB 모두 같은 repository/service 계약을 사용한다.

HTTP는 `asset_id`로 접근하며 서버 파일 경로를 노출하지 않는다.

- `GET`/`HEAD`
- 단일 byte Range와 `206`/`416`
- ETag와 `304`
- 원본 filename download
- `nosniff`, SVG CSP

## 7. 중복과 변경 감지

현재 마스터 Refresh는 `manifest.json`의 SHA-256과 상대 경로를 기준으로 이미 완료된 import를 `SKIPPED`한다. manifest가 같고 참조 결과 파일만 바뀌면 변경을 놓칠 수 있다.

개선 계약:

1. 각 mapping 파일의 SHA-256과 크기를 canonical 순서로 계산한다.
2. manifest checksum과 파일 checksum을 합친 bundle fingerprint를 저장한다.
3. 동일 fingerprint는 `SKIPPED`한다.
4. 다른 fingerprint는 새 Run 또는 명시된 replace 정책으로 처리한다.
5. 동일 `run_id` 충돌 정책을 manifest version에 포함한다.

## 8. 실제 예제와 검증

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
- `scalar_results`, `curve_results`, `curve_points`, `media_assets` 저장
- media `blob_id`와 blob chunk 생성
- 두 번째 Refresh의 idempotent `SKIPPED`
- traversal, symlink, 잘못된 MIME/확장자 거부
- mixed/unknown manifest와 잘못된 importer 선택 거부

## 9. 운영 배치

`SIMDASH_IMPORT_ROOT`는 애플리케이션 release 디렉터리 밖의 지속 저장소다. 권장 예시는 `/var/lib/simdashboard/import` 또는 승인된 사내 공유 경로다.

운영 배포에서 다음을 함께 결정해야 한다.

- systemd service user의 읽기 권한
- NAS/NFS/SMB mount와 재연결 정책
- SELinux label 또는 read-only bind 정책
- root 용량·파일 수·manifest 수 제한
- backup 대상인지, 재생성 가능한 source인지
- 실패 파일 격리와 재처리 운영 절차

Rocky installer는 `SIMDASH_IMPORT_ROOT`를 root 전용 service EnvironmentFile에
전달하고 systemd `ReadOnlyPaths`와 `RequiresMountsFor`로 보호한다. 기본 local root는
`/var/lib/simdashboard/import`이며 `root:simdashboard`, mode `0750`, SELinux
`var_lib_t`를 사용한다. 외부 NAS/NFS/SMB root는 installer가 ownership/mode를
변경하지 않으므로, mount 완료·서비스 계정의 재귀 읽기/실행 권한·조직 SELinux
정책을 배포 전 승인해야 한다.
