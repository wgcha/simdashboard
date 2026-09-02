# 해석 후처리 결과 수집·DB 적재·가시화 상세 사양서

> 상태: **historical/실행 금지** 초기 파이프라인 사양 및 배경 기록이다. 이 문서의 SQL과 legacy repository 예시는 현재 migration에 적용하거나 운영 경로로 호출하지 않는다. schema와 맞지 않던 legacy `result_files` persistence service/repository는 제거됐다. legacy manifest schema, `ManifestParser` alias와 normalized parser adapter만 compatibility 전용으로 남고, runtime 결과 쓰기는 canonical `ResultIngestionUnitOfWork`를 사용한다. 아래 Run Identity V2 단락과 구현 현황은 현재 계약을 보충한다. 실제 폴더·확장자·blob 계약은 `storage-folder-and-file-contract.md`를 따른다.

## 1. 문서 목적

본 문서는 해석 솔버의 원본 결과 파일을 직접 처리하지 않고, 별도의 후처리 자동화 프로그램이 표준 형식으로 생성한 CSV/JSON 결과를 수집하여 SQL 기반 데이터베이스에 적재하고, 판정 로직과 대시보드 가시화로 연결하는 기능의 상세 구현 사양을 정의한다.

현재 개발은 DuckDB, canonical 운영은 PostgreSQL 18을 사용한다. 공통
`ResultIngestionCommand`/`ResultIngestionUnitOfWork`가 Master Refresh와 typed
folder-import example의 검증된 payload를 하나의 connection transaction으로
저장한다. 일반 수동 `SUMMARY_RESULT` JSON/CSV도 target-qualified source name,
content checksum, retry `SKIPPED`, write-transaction auth recheck와 atomic audit를
사용해 이 경계로 이관되었다. `Radioss` mesh CSV도 `result_locations`를 포함해
canonical UoW로 저장한다. schema와 맞지 않던 구형 `ResultImportService`
persistence service/repository는 제거됐다.

이 문서의 범위는 다음 기능을 포함한다.

- 표준 후처리 결과 폴더 구조
- `manifest.json` 명세
- 결과 유형별 CSV/JSON 스키마
- 결과 수집 서비스
- DB 스키마 확장
- API 사양
- 판정 로직
- 프런트엔드 기능
- 중복 방지 및 이력 관리
- 오류 처리와 보안
- 테스트 및 완료 기준
- PostgreSQL 전환 고려사항

---

## 2. 시스템 목표

### 2.1 최종 데이터 흐름

```text
해석 솔버 실행
    ↓
후처리 자동화 프로그램
    ↓
표준 폴더 및 CSV/JSON 생성
    ↓
결과 수집 서비스
    ↓
스키마·경로·식별자 검증
    ↓
Project / AnalysisRequest / LoadCase / AnalysisRun 연결
    ↓
DuckDB 또는 PostgreSQL 적재
    ↓
품질 기준 조회 및 PASS / FAIL / NO_DATA 판정
    ↓
기존 대시보드 위젯과 결과 화면 가시화
```

### 2.2 핵심 원칙

1. 솔버 원본 파일을 직접 파싱하지 않는다.
2. 후처리 자동화 프로그램이 만든 표준 CSV/JSON만 수집한다.
3. 모든 결과는 반드시 `AnalysisRun`에 귀속한다.
4. 파일명만으로 결과 유형을 판단하지 않는다.
5. `manifest.json`의 결과 유형 선언과 실제 데이터 컬럼을 함께 검증한다.
6. 구조화 JSON/CSV 원본 전체는 DB BLOB으로 저장하지 않는다. 단, 검증된 canonical media asset은 `asset_blobs`/`asset_blob_chunks`에 저장한다.
7. 원본 파일 경로, 체크섬, 스키마 버전, 수집 이력은 보존한다.
8. 수집·변환·판정·저장·API 계층을 분리한다.
9. DuckDB 개발과 PostgreSQL 운영이 같은 application/domain/port 계약을 사용해야 한다.
10. 결과가 없는 신규 하중 경우는 오류가 아니라 `NO_DATA`로 처리한다.

---

## 3. 현재 시스템과의 연결 구조

현재 데이터 소유 관계는 다음과 같다.

```text
Project
└─ AnalysisRequest
   └─ LoadCase
      ├─ TemplateExecution
      └─ AnalysisRun
         ├─ ScalarResult
         ├─ TimeSeriesResult
         ├─ MediaAsset
         ├─ QualitativeNote
         └─ folder_import_jobs + analysis_run_metadata
```

### 3.1 데이터 귀속 원칙

- 프로젝트 설명, 제품 정보, 신뢰성 기준: `Project`
- 업무 의뢰 및 진행 단계: `AnalysisRequest`
- DROP, SIDE_CLAMP 등 해석 조건: `LoadCase`
- 실제 한 번의 실행 결과: `AnalysisRun`
- 최대값, 영구변형, 판정값: `scalar_results`
- 시간 이력: `time_series_results`
- 이미지, 영상, 3D 결과 메타데이터: `media_assets`
- canonical 결과 수집 기록: `folder_import_jobs`, fingerprint/run provenance: `analysis_run_metadata`

이 문서의 `result_import_jobs` 및 `analysis_runs` 확장 제안은 historical 설계다.
현재 schema에는 해당 table과 `source_program`, `source_program_version`,
`result_import_status`, `overall_verdict`, `last_imported_at` 컬럼이 없다. 이를
참조하던 legacy persistence service/repository는 제거됐다. legacy manifest schema,
`ManifestParser` alias와 normalized parser adapter만 parser 호환 전용으로 남으며,
현행 runtime 결과 쓰기는 공통 UoW import API로만 수행한다. 일반
`SUMMARY_RESULT` upload는 이 legacy 설명에 포함되지 않는다.

### 3.2 현재 import 이력 조회와 제한 재시도 계약

현재 runtime의 수집 이력은 `folder_import_jobs`에 저장한다. 이력 item은 상태와
source 정보뿐 아니라 conflict policy, outcome reason, 결과/교체 run, V2 ledger의
source revision, 생성·완료 시각과 재시도 가능 여부를 보존·표시한다. 검증된 manifest를
읽은 뒤 실패한 경우에도 source checksum, producer run ID, policy, reason과 완료
시각을 가능한 범위에서 기록한다.

- `GET /api/load-cases/{load_case_id}/result-imports?status=&limit=25&offset=0`은
  `RESULT_IMPORT` resource scope로 제한한다. `{items,total,counts}`를 반환하며
  `status`는 `RUNNING`, `COMPLETED`, `SKIPPED`, `REJECTED`, `FAILED` 중 하나다.
- `POST /api/result-imports/{job_id}/retry`는 `SYSTEM_CATALOG_MANAGE`와 해당 job의
  load case `RESULT_IMPORT`를 모두 요구한다. client path/body를 받지 않고 DB에
  저장된 Master manifest 상대경로만 사용한다. 유효 대상은 `FAILED`/`REJECTED`
  Master job뿐이며, 그 외에는 HTTP 409 `RESULT_IMPORT_NOT_RETRYABLE`이다.
- 재시도는 원 job의 project/request/load case target을 사용한다. 변경된 manifest가
  다른 target을 가리키면 `RESULT_IMPORT_RETRY_TARGET_MISMATCH`로 거부한다. missing
  manifest나 snapshot 초기 실패도 원 load case의 새 `FAILED` attempt로 남기며,
  과거 job은 불변이다.
- 재시도와 전체 Master Refresh는 process-local lock 뒤 동일한 cross-worker execution
  gate를 공유한다. `SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT=1`만 허용한다. DuckDB/local은
  POSIX nonblocking `flock`, PostgreSQL은 request/media pool 밖 전용 session의
  import-root keyed advisory lock을 사용한다. busy는 `RESULT_IMPORT_REFRESH_BUSY`,
  gate 불능은 `RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE`이다.

동일한 하중 조건을 여러 번 재실행하면 `AnalysisRun`을 분리한다.

```text
LoadCase: DROP_BOTTOM_450MM
├─ AnalysisRun #1
├─ AnalysisRun #2
└─ AnalysisRun #3
```

기존 실행을 덮어쓰지 않고 재실행 이력과 결과 차이를 추적 가능하게 한다.

---

## 4. 구현 범위

### 4.1 포함 범위

- 지정된 import root 폴더 스캔
- `manifest.json` 읽기 및 검증
- CSV/JSON 결과 파싱
- 결과 유형별 표준 스키마 검증
- Project / Request / LoadCase 식별자 검증
- AnalysisRun 생성 또는 기존 실행 연결
- Scalar 및 Time Series 결과 적재
- 품질 기준 기반 자동 판정
- 수집 이력 기록
- 중복 수집 차단
- 결과 조회 API
- 프런트엔드 수집 상태 및 결과 표시
- 기존 대시보드 위젯과 연결

### 4.2 제외 범위

- Radioss, LS-DYNA, Abaqus 등 솔버 원본 파일 파싱
- H3D, ODB, D3PLOT, BINOUT 직접 처리
- 브라우저에서 사용자가 임의 절대경로를 전달하는 기능
- 임의 SQL 실행
- 구조화 결과 원본 대용량 파일을 DB BLOB으로 저장
- canonical media의 검증된 바이트 저장은 `asset_blobs` 계약으로 처리
- 외부 AI가 검증 없이 결과 컬럼을 추론하는 기능

---

## 5. 표준 결과 폴더 구조

### 5.1 기본 import root

기본 경로:

```text
backend/data/import/
```

운영 환경에서는 환경변수로 변경 가능하게 한다. local/default는 migration compatibility를
위해 `SIMDASH_IMPORT_READINESS_POLICY=legacy`이며 Rocky production installer/profile은
`SIMDASH_IMPORT_READINESS_POLICY=required`만 허용한다.

```text
SIMDASH_IMPORT_ROOT=D:\simulation_results\import
```

### 5.2 표준 폴더 구조

```text
backend/data/import/
└─ {project_id}/
   └─ {request_id}/
      └─ {load_case_id}/
         └─ {run_id}/
            ├─ manifest.json
            ├─ .simdashboard-ready.json
            ├─ open_cell_stress.csv
            ├─ chassis_rear_deformation.csv
            ├─ time_history.csv
            └─ media/
               ├─ contour_01.png
               └─ result_01.glb
```

### 5.3 폴더 구조 사용 원칙

- 폴더 경로는 보조 식별 수단이다.
- 최종 식별자는 `manifest.json` 안의 ID를 기준으로 한다.
- 폴더 ID와 manifest ID가 다르면 수집 실패 처리한다.
- import root 밖의 경로 참조는 금지한다.
- 심볼릭 링크를 통한 root 탈출도 차단한다.

### 5.4 Atomic publication과 readiness marker

producer는 source bundle을 application service가 읽는 final folder에 in-place로 쓰지
않는다. final path의 parent 아래 sibling staging directory에서 canonical payload를
완성하고 file/directory `fsync` 후, captured payload bytes에서 marker v1을 만든다.
marker `.simdashboard-ready.json`은 payload 뒤 마지막으로 write+`fsync`하고 staging
directory `fsync` 뒤 Linux `renameat2(RENAME_NOREPLACE)`로 final path를 한 번에
공개한다. commit 뒤 parent directory도 `fsync`한다. 이미 존재하는 final path는
replace하지 않는다.

marker v1의 정확한 fields는 `schema_id`, `version`, `state=READY`, `bundle_path`,
`manifest_checksum`, `bundle_fingerprint`, `entry_count`, `published_at`이다. importer는
동일 descriptor-relative capture bytes와 marker 값을 비교하고 marker 자신은
fingerprint에서 제외한다. WSL/Rocky ext4/XFS가 1차 지원이며 native Windows,
`EXDEV`, `renameat2`/NOREPLACE 미지원은 fallback 없이 fail-closed한다. NFS/SMB는
mount probe와 운영 승인 전에는 미지원이다.

full refresh에서 `required`는 marker sibling이 있는 bundle만 scan하고 unmarked
manifest는 보이지 않는다. `legacy`는 unmarked legacy manifest를 scan하되, marker가
존재하면 strict marker capture만 사용한다. retry는 DB-stored relative manifest path로
한 건을 다시 capture하며, strict profile의 missing/invalid marker는 원 target에 새
FAILED attempt로 남긴다.

---

## 6. `manifest.json` 명세

### 6.1 필수 필드

| 필드 | 형식 | 필수 | 설명 |
|---|---|---:|---|
| `schema_version` | string | Y | manifest 스키마 버전 |
| `project_id` | string | Y | 프로젝트 ID |
| `request_id` | string | Y | 해석 의뢰 ID |
| `load_case_id` | string | Y | 하중 경우 ID |
| `run_id` | string | Y | 실행 ID |
| `run_no` | integer | Y | 동일 LoadCase 내 실행 번호 |
| `solver` | string | N | Radioss, LS-DYNA, Abaqus 등 |
| `started_at` | ISO 8601 | N | 해석 시작 시각 |
| `completed_at` | ISO 8601 | N | 해석 완료 시각 |
| `source_program` | string | Y | 후처리 프로그램 이름 |
| `source_program_version` | string | N | 후처리 프로그램 버전 |
| `result_files` | array | Y | 결과 파일 목록 |
| `overwrite_policy` | enum | Y | **historical** legacy manifest 필드. 현재 canonical 경로는 `conflict_policy`를 사용한다. |
| `source_run_id` | string | N | producer가 부여한 안정적 실행 식별자(1~120 printable). 없으면 legacy append 호환 |
| `conflict_policy` | enum | N | source identity가 있을 때 `SKIP`(기본), `REJECT`, `REPLACE`; Master manifest는 `REPLACE` 금지 |
| `metadata` | object | N | 추가 메타데이터 |

### 6.2 결과 파일 객체

| 필드 | 형식 | 필수 | 설명 |
|---|---|---:|---|
| `type` | enum | Y | 결과 유형 |
| `path` | string | Y | manifest 기준 상대경로 |
| `format` | enum | N | `CSV`, `JSON` |
| `time_unit` | string | N | 시간 단위 |
| `value_unit` | string | N | 결과 단위 |
| `required` | boolean | N | 필수 결과 여부 |
| `metadata` | object | N | 파일별 추가 정보 |

### 6.3 지원 결과 유형

```text
OPEN_CELL_STRESS
CHASSIS_REAR_DEFORMATION
GENERIC_TIME_HISTORY
SCALAR_RESULTS
MEDIA_ASSET
```

### 6.4 manifest 예시

```json
{
  "schema_version": "1.0",
  "project_id": "project-tv-001",
  "request_id": "request-drop-001",
  "load_case_id": "loadcase-drop-bottom-001",
  "run_id": "run-drop-002",
  "run_no": 2,
  "solver": "Radioss",
  "started_at": "2026-07-20T12:40:00Z",
  "completed_at": "2026-07-20T13:00:00Z",
  "source_program": "simulation-postprocessor",
  "source_program_version": "1.2.0",
  "overwrite_policy": "REJECT",
  "result_files": [
    {
      "type": "OPEN_CELL_STRESS",
      "path": "open_cell_stress.csv",
      "format": "CSV",
      "time_unit": "s",
      "value_unit": "MPa",
      "required": true
    },
    {
      "type": "CHASSIS_REAR_DEFORMATION",
      "path": "chassis_rear_deformation.csv",
      "format": "CSV",
      "value_unit": "mm",
      "required": false
    }
  ],
  "metadata": {
    "model_revision": "rev.C",
    "template_version": "2.3.1"
  }
}
```

---

## 7. 결과 데이터 표준 스키마

### 7.1 Open Cell 응력 시간 이력

```csv
time,top,bottom,left,right
0.0000,0.0,0.0,0.0,0.0
0.0001,12.4,10.2,8.3,9.1
0.0002,61.8,82.4,73.1,69.5
```

필수 컬럼:

- `time`
- `top`
- `bottom`
- `left`
- `right`

검증 규칙:

- 모든 값은 유한한 숫자여야 한다.
- `time`은 오름차순이어야 한다.
- 중복 시간값은 기본적으로 허용하지 않는다.
- 필수 컬럼 빈 값은 오류 처리한다.
- 단위는 manifest의 `time_unit`, `value_unit`을 사용한다.
- 저장 전에 표준 단위로 정규화한다.

Scalar 저장 키:

| variable_key | display_name | unit |
|---|---|---|
| `top_edge_max_stress` | 상단 엣지 최대 응력 | MPa |
| `bottom_edge_max_stress` | 하단 엣지 최대 응력 | MPa |
| `left_edge_max_stress` | 좌측 엣지 최대 응력 | MPa |
| `right_edge_max_stress` | 우측 엣지 최대 응력 | MPa |

Time series 저장 키:

| variable_key | display_name |
|---|---|
| `top_edge_stress_time` | 상단 엣지 |
| `bottom_edge_stress_time` | 하단 엣지 |
| `left_edge_stress_time` | 좌측 엣지 |
| `right_edge_stress_time` | 우측 엣지 |

### 7.2 Chassis Rear 영구변형

```csv
location,permanent_deformation
top_edge_gap,5.8
bottom_edge_gap,4.2
corner_top_left,6.3
corner_top_right,3.7
corner_bottom_left,4.9
corner_bottom_right,5.4
```

지원 location:

```text
top_edge_gap
bottom_edge_gap
corner_top_left
corner_top_right
corner_bottom_left
corner_bottom_right
```

저장 키:

```text
chassis_rear_{location}_permanent_deformation
```

### 7.3 일반 시간 이력

```csv
time,variable_key,value,display_name,time_unit,value_unit
0.0,center_acceleration,0.0,중앙 가속도,s,m/s2
0.1,center_acceleration,12.4,중앙 가속도,s,m/s2
```

필수 컬럼:

- `time`
- `variable_key`
- `value`

### 7.4 일반 Scalar 결과

```csv
variable_key,display_name,value,unit,threshold,criterion_key
panel_max_displacement,Panel 최대 변위,12.2,mm,15.0,panel_max_displacement_mm
```

판정 우선순위:

1. `criterion_key`로 `quality_thresholds` 조회
2. 파일의 `threshold`
3. 둘 다 없으면 `NO_CRITERION`

---

## 8. DB 스키마 확장 (historical proposal — 실행 금지)

아래 SQL은 과거 설계 초안이며 현재 `backend/migrations/` 또는 DuckDB bootstrap과
일치하지 않는다. 현재 canonical table은 `folder_import_jobs`와
`analysis_run_metadata`다. schema 변경은 반드시 Alembic revision과 현재 storage
contract를 함께 갱신한다.

### 8.1 제안된 `result_import_jobs` (현재 미존재)

```sql
CREATE TABLE IF NOT EXISTS result_import_jobs (
    id VARCHAR PRIMARY KEY,
    analysis_run_id VARCHAR,
    project_id VARCHAR NOT NULL,
    request_id VARCHAR NOT NULL,
    load_case_id VARCHAR NOT NULL,
    manifest_path VARCHAR NOT NULL,
    source_directory VARCHAR NOT NULL,
    schema_version VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    overwrite_policy VARCHAR NOT NULL,
    file_count INTEGER NOT NULL DEFAULT 0,
    row_count BIGINT NOT NULL DEFAULT 0,
    manifest_checksum VARCHAR NOT NULL,
    source_checksum VARCHAR,
    error_code VARCHAR,
    error_message VARCHAR,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    imported_at TIMESTAMP NOT NULL
);
```

상태:

```text
PENDING
IMPORTING
COMPLETED
FAILED
SKIPPED
```

### 8.2 제안된 `analysis_runs` 확장 (현재 미적용)

```sql
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS overall_verdict VARCHAR;
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS source_program VARCHAR;
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS source_program_version VARCHAR;
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS result_import_status VARCHAR;
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS last_imported_at TIMESTAMP;
```

---

## 9. 과거 제안 백엔드 모듈 구조 (현재 제거됨)

아래 tree는 초기 설계 기록이며 현재 파일 구조가 아니다. 표시된 legacy
repository/service는 제거됐고, 현재 구조는 `current-architecture.md`를 따른다.

```text
backend/app/
├─ main.py
├─ database.py
├─ repositories/
│  ├─ analysis_repository.py
│  ├─ result_repository.py
│  └─ import_job_repository.py
├─ services/
│  ├─ result_import_service.py
│  ├─ result_validation_service.py
│  └─ verdict_service.py
├─ parsers/
│  ├─ manifest_parser.py
│  ├─ open_cell_parser.py
│  ├─ chassis_rear_parser.py
│  ├─ generic_time_history_parser.py
│  └─ scalar_result_parser.py
├─ schemas/
│  ├─ result_import.py
│  └─ result_response.py
└─ config.py
```

다음 책임은 반드시 분리한다.

- 경로 탐색
- manifest 파싱
- 데이터 검증
- 판정
- DB 저장
- FastAPI 라우팅

---

## 10. 결과 수집 서비스

### 10.1 폴더 스캔

1. process-local lock과 global execution gate 획득
2. gate 안에서 strict stale private snapshot만 정리
3. import root 확인과 root 아래 `manifest.json` 검색
4. 완료 checksum 조회와 미처리 manifest 선별
5. 수정 시간 또는 경로 기준 정렬

### 10.2 단일 manifest 수집

```text
1. 상대경로 정규화
2. import root 내부 경로 검증
3. manifest 파싱
4. 전용 service-owned `0700` snapshot root의 reserve+min-free capacity 확인
5. schema_version 검증
6. 필수 필드 검증
7. 폴더 ID와 manifest ID 비교
8. Project 존재 확인
9. Request 소유관계 확인
10. LoadCase 소유관계 확인
11. 결과 파일 존재 확인
12. checksum 계산
13. 결과 유형별 스키마 검증
14. AnalysisRun 생성 또는 확인
15. 트랜잭션 시작
16. overwrite 정책 적용
17. scalar_results 적재
18. time_series_results 적재
19. media_assets 적재
20. 전체 판정 계산
21. AnalysisRun 갱신
22. import job 완료 기록
23. 커밋
```

오류 발생 시 전체 롤백한다.

### 10.3 overwrite 정책 — historical

- `REJECT`: 기존 결과가 있으면 실패
- `REPLACE`: 기존 결과 삭제 후 교체
- `APPEND`: 충돌하지 않는 신규 결과만 추가

이 절은 초기 설계의 historical 정책이며 현재 runtime에 적용하지 않는다. 특히
`REPLACE`의 “기존 결과 삭제” 동작은 구현 계약이 아니다.

### 10.4 현재 계약 — Run Identity V2

현재 runtime은 Alembic `0017_run_identity_v2`의
`canonical_result_ingestion_source_versions`를 source identity 정본으로 사용한다.
ledger는 load case별 `source_type`·`source_key`·`source_revision`으로 범위화하고,
source checksum, server-assigned `analysis_run_id`, `run_no`, conflict policy와
`supersedes_analysis_run_id`를 보존한다. `(load_case_id, run_no)`는 unique이며 기존
run의 결과·검토·미디어 행을 수정하거나 삭제하지 않는다.

- 동일 checksum은 `SKIPPED/NOOP`이며 기존 `analysis_run_id`와 `run_no`를 반환한다.
- 명시적 `source_run_id`의 변경 checksum은 `SKIP`이면 기존 run 반환,
  `REJECT`이면 terminal import job을 남기고 거부, `REPLACE`이면 새 run을 추가하고
  이전 run을 `supersedes_analysis_run_id`로 연결한다.
- `source_run_id`가 없는 경로는 `name:<source_name>`의 `LEGACY_APPEND` revision으로
  기존 호환 동작을 유지한다. checksum이 없으면 ledger를 기록하지 않는다.
- 수동 upload의 `REJECT`는 job과 audit를 commit한 뒤 HTTP 409
  `SOURCE_RUN_CONFLICT`를 반환한다. Master Refresh는 manifest의 `SKIP`/`REJECT`만
  허용하고 producer `REPLACE`는 fail-closed한다. Master의 reject는 형제 manifest를
  중단하지 않고 `FAILED/SOURCE_RUN_CONFLICT` item으로 반환한다.

2026-08-25 disposable PostgreSQL 18.6 live gate에서는 loopback 55433의 별도
test DB로 빈 DB `0001→0017`, 기존 `0016→0017` backfill, app-role DDL 거부,
pool budget, reference seed와 동시성 test **2 passed**를 확인했다. 실제 SQL
provider의 CREATED/exact NOOP/SKIP/REJECT/immutable REPLACE와 revision/supersedes도
검증했다. 종료 후 cluster·DB·로그를 제거해 잔여 파일 0과 port 종료를 확인했으며
기존 5432 `simulation_dashboard`와 `.env`는 건드리지 않았다.

---

## 11. 판정 로직

아래 threshold 식은 legacy 설계 기준이다. 현재 canonical Master Refresh는
`value >= threshold`를 `FAIL`로 처리하고 legacy `VerdictService`는
`value <= threshold`를 `PASS`로 처리하므로 경계값에서 계약이 다르다. 공통
ingestion으로 legacy를 승격하기 전에 하나의 판정 정책으로 통일하고
`value == threshold` 회귀 테스트를 추가해야 한다. 이 절의 식을 현재 운영
계약으로 사용하지 않는다.

### 11.1 Open Cell 응력

```text
value <= threshold → PASS
value > threshold  → FAIL
데이터 없음        → NO_DATA
기준 없음          → NO_CRITERION
```

### 11.2 Chassis Rear 영구변형

```text
value <= threshold → PASS
value > threshold  → FAIL
데이터 없음        → NO_DATA
```

### 11.3 전체 판정

```text
하나 이상 FAIL          → FAIL
필수 결과 일부 누락     → PARTIAL
모든 필수 결과 PASS     → PASS
결과 없음               → NO_DATA
기준 없음               → NO_CRITERION
```

---

## 12. API 사양

### `POST /api/result-imports/scan`

지정 import root를 스캔한다.

### `POST /api/result-imports/import`

```json
{
  "manifest_path": "project-tv-001/request-drop-001/loadcase-drop-bottom-001/run-drop-002/manifest.json"
}
```

절대경로는 허용하지 않는다.

### `GET /api/result-imports`

수집 이력을 조회한다.

### `GET /api/result-imports/{job_id}`

수집 상세와 오류를 조회한다.

### `GET /api/analysis-runs/{run_id}/results`

Scalar, time series 요약, media, 판정을 조회한다.

### `GET /api/load-cases/{load_case_id}/analysis-runs`

LoadCase에 속한 실행 목록을 조회한다.

---

## 13. 프런트엔드 사양

LoadCase 상세 화면에 `해석 결과` 섹션을 추가한다.

표시 항목:

- Run 번호
- Solver
- 실행 상태
- 완료 시각
- 수집 상태
- Scalar 결과 개수
- Time Series 결과 개수
- PASS / FAIL / PARTIAL / NO_DATA
- 최근 오류

버튼:

- 결과 폴더 스캔
- 선택 결과 수집
- 결과 다시 불러오기
- 결과 대시보드 열기
- 수집 이력 보기

브라우저 로컬 파일 선택 UI는 우선 구현하지 않는다.

---

## 14. 기존 대시보드 연결

DB 적재 결과를 다음 위젯에 연결한다.

- Open Cell 엣지 맵
- 상·하·좌·우 최대 응력
- 응력-시간 그래프
- PASS/FAIL 판정
- Chassis Rear 영구변형 결과표
- 컨투어 이미지
- 3D 결과 링크

표시 대상 우선순위:

1. 사용자가 선택한 AnalysisRun
2. 완료된 최신 run
3. 최신 run
4. 결과 없음이면 `NO_DATA`

---

## 15. 오류 처리와 보안

오류 코드 예시:

```text
INVALID_MANIFEST
UNSUPPORTED_SCHEMA_VERSION
PATH_OUTSIDE_IMPORT_ROOT
MISSING_RESULT_FILE
INVALID_COLUMNS
INVALID_NUMERIC_VALUE
ENTITY_NOT_FOUND
ENTITY_RELATION_MISMATCH
DUPLICATE_IMPORT
OVERWRITE_REJECTED
DATABASE_ERROR
BUNDLE_SNAPSHOT_RESERVE_UNAVAILABLE
RESULT_IMPORT_REFRESH_BUSY
RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE
```

보안 요구사항:

1. import root 기준 상대경로만 허용
2. 절대경로와 `..` 차단
3. 확장자 허용 목록 적용
4. 파일 크기 제한
5. JSON 깊이와 행 수 제한
6. 서비스 계정 최소 권한
7. 비밀정보 로그 금지
8. 인증 전 외부 인터넷 공개 금지
9. private snapshot root는 import root와 중첩·symlink일 수 없고 service-owned `0700`이어야 한다.

---

## 16. 성능 요구사항

MVP 권고값:

- 단일 manifest 최대 파일 수: 20
- 단일 CSV 최대 크기: 100 MB
- 단일 time series 최대 행 수: 2,000,000
- 스캔 1회 최대 manifest 수: 1,000
- 목록 API 기본 반환: 100건

대량 적재 시 batch insert를 사용하고 time series 전체를 일반 API 응답으로 반환하지 않는다.

---

## 17. 환경 설정

```text
SIMDASH_IMPORT_ROOT=backend/data/import
SIMDASH_MAX_IMPORT_FILE_MB=100
SIMDASH_MAX_IMPORT_ROWS=2000000
SIMDASH_ALLOWED_SCHEMA_VERSIONS=1.0
SIMDASH_DEFAULT_OVERWRITE_POLICY=REJECT
SIMDASH_AUTO_IMPORT_ENABLED=false
SIMDASH_IMPORT_SNAPSHOT_ROOT=/absolute/private/snapshot-root
SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES=1073741824
SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES=536870912
SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS=86400
SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT=1
```

snapshot reserve/min-free는 app capacity gate이며 kernel/filesystem quota가 아니다.
`ENOSPC`/`EDQUOT` 복사 실패도 `BUNDLE_SNAPSHOT_RESERVE_UNAVAILABLE`로 정규화한다.
Rocky installer는 core와 같은 범위로 reserve `1073741824..17179869184` bytes,
min-free `0..17179869184` bytes, stale `60..7776000` seconds를 fail-fast 검증한다.
초기 MVP는 API 수동 스캔을 우선하며 watcher는 후속 단계로 둔다.

---

## 18. 테스트 사양

백엔드 필수 테스트:

1. 정상 manifest 파싱
2. Open Cell CSV 수집
3. 최대 응력 계산
4. threshold 판정
5. Chassis Rear 적재 및 판정
6. 일반 time series 적재
7. 일반 scalar 적재
8. 존재하지 않는 Project 거부
9. 엔터티 소유관계 불일치 거부
10. 잘못된 컬럼 거부
11. 숫자 오류 거부
12. import root 밖 경로 차단
13. 누락 파일 거부
14. bundle fingerprint 중복 차단
15. REJECT 정책
16. Run Identity V2 `SKIP`/`REJECT`/immutable `REPLACE` 정책
17. 트랜잭션 롤백
18. 결과 없는 run의 NO_DATA
19. 필수 결과 일부 누락의 PARTIAL
20. API 조회

프런트엔드 검증:

- TypeScript 검사 통과
- `pnpm run build` 통과
- run 목록 표시
- import 상태 표시
- 오류 표시
- 대시보드 연결
- 기존 화면 유지
- 콘솔 오류 없음

이 문서의 원래 71개 collection은 historical 검증 범위다. 현재 focused 검증은
canonical/legacy manifest, bundle fingerprint, common UoW, manual SUMMARY_RESULT
JSON/CSV, media fixture matrix, `0017` migration/Run Identity contract, typed folder
API, PostgreSQL source-version과 endpoint wiring을 포함한다. PostgreSQL
concurrency test는 opt-in이며 disposable 0017 V2 live gate에서 통과했다.

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
  tests/test_api.py::test_typed_folder_example_registers_scalars_curves_media_and_catalog
# historical collection reference; current collection also includes Run Identity V2 tests
```

이 historical collection 이후 Radioss mesh locations는 canonical UoW로 이관됐고
schema와 맞지 않던 legacy persistence는 제거됐다. AP-1 producer atomic publication과
local `legacy`/Rocky `required` marker policy, AP-2 private workspace/capacity/gate는
코드와 focused 계약 검증으로 반영됐다. AP-2 PostgreSQL gate는 disposable 18.6
`127.0.0.1:55436`에서 두 전용 session BUSY, unlock/close 뒤 재획득, explicit unlock
없이 session close 뒤 재획득까지 확인하고 cluster 및 `/tmp` data/log를 정리했다.
기존 5432/`.env` DB는 사용하지 않았다. 실제 Rocky host, NFS/SMB mount capability와
filesystem quota는 release gate다.

**2026-08-25 AP-2 검증 기록:** focused 통합 `126 passed in 87.32s`, full backend
`573 passed, 5 skipped in 382.12s`; backend architecture/OpenAPI/compileall, Rocky
validator, frontend architecture/API self-test/build도 통과했다.

import history/status/retry slice의 별도 PostgreSQL 18.6 disposable
`127.0.0.1:55434` `simulation_dashboard_test_history` 기록은 blank
Alembic head `0017`, app privilege/DDL denial, history list/filter/pagination/counts,
V2 revision join, GET, missing retry `FAILED` append, target drift mismatch/no foreign
run도 확인한 뒤 cluster/port를 정리했다.

---

## 19. 구현 단계

### 단계 1. Parser와 Schema

- manifest Pydantic 모델
- 결과 유형 enum
- CSV/JSON parser
- 데이터 검증
- checksum

### 단계 2. DB와 Repository

- (historical) `result_import_jobs`
- (historical) `analysis_runs` 확장
- batch insert
- bundle fingerprint 중복 제어
- 공통 UoW transaction

### 단계 3. Service와 API

- 폴더 스캔
- 단일 import
- Run Identity V2 결과(`operation`, `reason_code`, 기존/교체 run, revision) 반환
- 수동 conflict의 commit 후 HTTP 409/audit
- import history/status/retry API와 UI 구현, target-fixed retry와 새 실패 attempt 기록

### 단계 4. 프런트엔드

- run 목록
- 스캔 및 수집
- 상태·오류·판정
- 대시보드 연결
- 선택 load case의 최근 25건 이력, 상태 필터·새로고침과 조건부 재시도 UI

### 단계 5. 운영 안정화

- 대량 적재 최적화
- 감사 로그
- PostgreSQL repository
- 자동 watcher
- **완료(WSL 검증):** producer atomic publish/readiness AP-1
- **완료(코드·focused 계약 검증):** AP-2 snapshot workspace, capacity gate와
  cross-worker refresh lock/budget. 실제 target host capability 검증은 release gate.

---

## 20. PostgreSQL 전환

유지할 계층:

- parser
- validation service
- verdict service
- API schema
- 프런트엔드 계약

교체할 계층:

- DB connection
- SQL dialect
- batch insert
- migration
- JSON 저장

PostgreSQL 단계에서는 Alembic, JSONB, 외래키, unique constraint, `COPY`, 인덱스 및 필요 시 파티셔닝을 적용한다.

---

## 21. 최종 인수 조건

다음 조건을 모두 만족해야 한다.

- 표준 CSV/JSON을 지정 폴더에서 수집
- Project / Request / LoadCase / AnalysisRun 관계 검증
- Open Cell 최대값과 시간 이력 적재
- Chassis Rear 영구변형 적재
- PASS/FAIL/NO_DATA 자동 판정
- 중복 및 부분 적재 방지
- 수집 이력과 오류 조회
- 기존 대시보드 연결
- 결과 없는 LoadCase의 NO_DATA 처리
- 기존 프로젝트·의뢰·하중 경우 기능 유지
- 백엔드 테스트와 프런트엔드 빌드 통과
- PostgreSQL 전환 가능한 계층 구조 유지
