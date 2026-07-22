# 해석 결과 데이터 형식·미디어 저장 및 대시보드 연동 사양서

## 1. 목적과 범위

이 문서는 해석 결과 폴더에 포함되는 실수, 정수, 텍스트, 배열(커브), 이미지, 영상 및 경량 3D 결과를 파일 기반 DuckDB와 향후 PostgreSQL에 일관되게 저장하고, 사용자 구성형 대시보드에서 가시화하기 위한 기능 사양이다.

적용 대상은 다음 계층이다.

```text
프로젝트 → 의뢰작업 → 하중경우 → 해석 실행(analysis run) → 결과 변수/미디어
```

각 결과는 반드시 하나의 `analysis_run_id`와 하나의 변수 정의 `variable_key`에 연결한다. 원본 파일명, 원본 경로, 체크섬, 가져오기 시각을 남겨 재현성과 중복 등록 방지를 보장한다.

## 2. 핵심 원칙

1. 폴더마다 물리 SQL 테이블을 새로 만들지 않는다. 도메인 테이블은 안정적으로 유지하고, 폴더 구조·파일 규칙은 버전이 있는 JSON 가져오기 스키마로 관리한다.
2. 대시보드는 파일 경로가 아니라 `analysis_run_id`, `variable_key`, `asset_id`로 데이터를 요청한다. 따라서 DuckDB에서 PostgreSQL로 이전하거나 저장소를 바꾸어도 프론트엔드 API는 유지된다.
3. 변수의 데이터 유형은 생성 뒤 변경하지 않는다. 표시 이름, 단위, 설명, 기준값, 허용 위젯은 변경할 수 있다.
4. 원본 파일은 보존하고, 파싱·검증한 결과를 별도 SQL 레코드로 적재한다.
5. 대용량 영상과 3D 파일은 DB가 파일 위치와 무결성을 관리한다. 작은 이미지까지 반드시 DB 바이너리로 보관해야 하는 환경에서는 선택적으로 바이너리 저장소를 사용한다.

## 3. 지원 데이터 유형

| 카탈로그 유형 | 해석 예 | 권장 SQL 타입/테이블 | 대표 위젯 |
|---|---|---|---|
| `FLOAT` | 최대 응력, 최대 변형량, 에너지 | `DOUBLE PRECISION`, `scalar_results` | KPI, 게이지, 막대, 산점도, 표 |
| `INTEGER` | 요소 수, 반복 횟수, 불량 개수 | `BIGINT`, `scalar_results` | KPI, 막대, 표 |
| `TEXT` | 판정 사유, 재질명, 해석 메모 | `TEXT`, `scalar_results` 또는 `qualitative_notes` | 판정 카드, 메모, 표 |
| `CURVE` | 시간-응력, 시간-변위, 주파수 응답 | `curve_results`, `curve_points` | 선 그래프, 산점도, 표 |
| `IMAGE` | 컨투어, 엣지 변형 이미지 | `media_assets` + 선택적 `asset_blobs` | 이미지/컨투어 뷰어 |
| `VIDEO` | 낙하 해석 애니메이션 | `media_assets` + 파일/객체 저장소 | 영상 플레이어 |
| `MODEL_3D` | 경량 GLB/GLTF 결과 | `media_assets` + 파일/객체 저장소 | 3D 뷰어 |

`VERDICT`, `STATUS`, `BOOLEAN`은 후속 확장 유형으로 사용한다. 현재 PASS/FAIL 값은 `scalar_results.verdict` 또는 검증 테이블에서 관리한다.

## 4. 데이터 모델

### 4.1 변수 카탈로그: `variable_definitions`

변수 카탈로그는 결과 파일의 열 또는 자산을 의미적으로 정의한다.

```sql
-- PostgreSQL 목표 예시
CREATE TABLE variable_definitions (
    id UUID PRIMARY KEY,
    load_case_id UUID NOT NULL REFERENCES load_cases(id),
    variable_key VARCHAR(80) NOT NULL,
    display_name TEXT NOT NULL,
    data_type VARCHAR(20) NOT NULL,
    unit TEXT,
    description TEXT,
    threshold_double DOUBLE PRECISION,
    filterable BOOLEAN NOT NULL DEFAULT TRUE,
    allowed_widgets_json JSONB NOT NULL,
    allowed_aggregations_json JSONB NOT NULL,
    result_group VARCHAR(30) NOT NULL DEFAULT 'CUSTOM',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    updated_by TEXT NOT NULL,
    UNIQUE(load_case_id, variable_key),
    CHECK (data_type IN ('FLOAT','INTEGER','TEXT','CURVE','IMAGE','VIDEO','MODEL_3D','VERDICT','STATUS','BOOLEAN'))
);
```

`variable_key`는 CSV/JSON/폴더 가져오기 스키마가 참조하는 영문 식별자다. 예: `chassis_rear_top_edge_permanent_deformation_mm`.

### 4.2 단일 값: `scalar_results`

실수, 정수, 텍스트, PASS/FAIL 결과를 저장한다. 하나의 행에는 실제 값 열 하나만 채운다.

```sql
CREATE TABLE scalar_results (
    id UUID PRIMARY KEY,
    analysis_run_id UUID NOT NULL REFERENCES analysis_runs(id),
    variable_key VARCHAR(80) NOT NULL,
    value_double DOUBLE PRECISION,
    value_integer BIGINT,
    value_text TEXT,
    unit TEXT,
    threshold_double DOUBLE PRECISION,
    verdict VARCHAR(10),
    source_file TEXT,
    source_checksum CHAR(64),
    created_at TIMESTAMPTZ NOT NULL,
    CHECK (num_nonnulls(value_double, value_integer, value_text) = 1),
    CHECK (verdict IS NULL OR verdict IN ('PASS','FAIL','WARN','INFO'))
);
CREATE INDEX ix_scalar_results_run_variable ON scalar_results(analysis_run_id, variable_key);
```

예: Chassis Rear 상단 엣지 최대 영구변형은 `FLOAT`, 값 `4.2`, 단위 `mm`, 기준값 `5.0`, 판정 `PASS`로 기록한다.

### 4.3 배열·커브: `curve_results`, `curve_points`

커브 데이터를 JSON 배열 한 칸에만 넣으면 범위 검색, 최대값 계산, 차트 처리와 PostgreSQL 이전이 불편해진다. 메타데이터와 포인트를 분리한다.

```sql
CREATE TABLE curve_results (
    id UUID PRIMARY KEY,
    analysis_run_id UUID NOT NULL REFERENCES analysis_runs(id),
    variable_key VARCHAR(80) NOT NULL,
    display_name TEXT NOT NULL,
    series_key VARCHAR(120) NOT NULL DEFAULT 'default',
    x_label TEXT NOT NULL DEFAULT '시간',
    x_unit TEXT NOT NULL,
    y_label TEXT NOT NULL,
    y_unit TEXT NOT NULL,
    point_count INTEGER NOT NULL,
    source_file TEXT,
    source_checksum CHAR(64),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(analysis_run_id, variable_key, series_key)
);

CREATE TABLE curve_points (
    curve_id UUID NOT NULL REFERENCES curve_results(id) ON DELETE CASCADE,
    point_index INTEGER NOT NULL,
    x_value DOUBLE PRECISION NOT NULL,
    y_value DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(curve_id, point_index)
);
CREATE INDEX ix_curve_points_curve_x ON curve_points(curve_id, x_value);
```

- 시간-응력: `x_label=시간`, `x_unit=ms`, `y_label=응력`, `y_unit=MPa`
- 시간-변형: `x_label=시간`, `x_unit=ms`, `y_label=영구변형`, `y_unit=mm`
- 여러 엣지 비교: 같은 `variable_key` 아래 `series_key=top|bottom|left|right`로 구분한다.

원본 JSON 배열은 감사 또는 빠른 재적재 용도로 `raw_payload_json JSONB`에 선택 저장할 수 있으나, 가시화와 분석의 기준 데이터는 `curve_points`다.

### 4.4 이미지·영상·3D: `media_assets`, `asset_blobs`

```sql
CREATE TABLE media_assets (
    id UUID PRIMARY KEY,
    analysis_run_id UUID NOT NULL REFERENCES analysis_runs(id),
    variable_key VARCHAR(80),
    asset_type VARCHAR(20) NOT NULL,
    title TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    storage_mode VARCHAR(20) NOT NULL,
    storage_key TEXT,
    original_filename TEXT NOT NULL,
    file_size BIGINT NOT NULL,
    checksum CHAR(64) NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    CHECK (asset_type IN ('IMAGE','VIDEO','MODEL_3D')),
    CHECK (storage_mode IN ('DATABASE','FILESYSTEM','OBJECT_STORAGE')),
    UNIQUE(analysis_run_id, checksum)
);

CREATE TABLE asset_blobs (
    asset_id UUID PRIMARY KEY REFERENCES media_assets(id) ON DELETE CASCADE,
    content BYTEA NOT NULL
);
```

저장 방식은 다음을 기본으로 한다.

| 자산 | 기본 저장 방식 | 이유 |
|---|---|---|
| PNG/JPEG/WebP/SVG 컨투어 | `DATABASE` 또는 `FILESYSTEM` | 비교적 작고 대시보드에서 자주 표시 |
| MP4/WebM 영상 | `FILESYSTEM` 또는 `OBJECT_STORAGE` | 대용량 스트리밍과 DB 백업 부담 감소 |
| GLB/GLTF | `FILESYSTEM` 또는 `OBJECT_STORAGE` | 큰 바이너리와 브라우저 전송량 관리 |

`FILESYSTEM`이어도 클라이언트에 절대 경로를 노출하지 않는다. `GET /api/assets/{asset_id}`가 인증·권한 확인 뒤 파일 또는 DB 바이너리를 스트리밍한다.

## 5. 대시보드 연동 규칙

1. 위젯 설정은 `variable_key`와 필요한 경우 `asset_id`만 저장한다.
2. 위젯 카탈로그는 변수의 `data_type`, `allowed_widgets`를 보고 선택 가능한 그래프만 표시한다.
3. 변수는 선언되었지만 결과가 아직 없을 수 있다. 이 경우 대시보드는 오류 대신 “결과 데이터 대기” 상태를 표시한다.
4. 커브 위젯은 `curve_results`와 `curve_points`를 조회해 선 그래프를 그린다. 포인트가 매우 많으면 서버가 범위별 다운샘플링을 적용한다.
5. 이미지·영상·3D 위젯은 `asset_id`를 통해 API에서 콘텐츠를 받아 표시한다.
6. 실수와 정수의 기준값·판정은 KPI, 게이지, PASS/FAIL 카드에 함께 표시한다.

## 6. 폴더 스키마(JSON)와 적재 규칙

폴더 구조는 데이터베이스 테이블 구조와 동일하지 않다. 사용자 GUI에서 정의한 폴더 가져오기 스키마(JSON)가 실제 파일을 도메인 레코드와 변수에 연결한다.

```json
{
  "schema_id": "tv-drop-v1",
  "version": 1,
  "mappings": [
    {
      "path_pattern": "results/open_cell_stress.csv",
      "parser": "csv_curve",
      "variable_key": "open_cell_top_edge_stress",
      "data_type": "CURVE",
      "series_key": "top",
      "x_column": "time_ms",
      "y_column": "stress_mpa"
    },
    {
      "path_pattern": "results/chassis_summary.json",
      "parser": "json_scalar",
      "variable_key": "chassis_rear_top_edge_permanent_deformation_mm",
      "data_type": "FLOAT",
      "json_path": "top_edge.max_deformation_mm"
    },
    {
      "path_pattern": "contour/*.png",
      "parser": "binary_asset",
      "variable_key": "open_cell_stress_contour",
      "data_type": "IMAGE",
      "asset_type": "IMAGE"
    },
    {
      "path_pattern": "animation/*.mp4",
      "parser": "binary_asset",
      "variable_key": "drop_animation",
      "data_type": "VIDEO",
      "asset_type": "VIDEO"
    }
  ]
}
```

가져오기 처리 순서:

```text
폴더/ZIP 선택 → 스키마 버전 선택 → 파일 탐색 → 파싱 → 유형·단위 검증
→ 미리보기 → 체크섬 중복 확인 → 트랜잭션 저장 → 대시보드 재조회
```

원본 폴더에서 자동 가져오기를 하려면 웹 브라우저가 임의의 로컬 폴더를 지속 감시할 수 없으므로, 다음 중 하나를 사용한다.

- MVP: 폴더 또는 ZIP을 웹 화면에서 업로드한다.
- 사내 서버: 허용된 서버 경로만 선택하여 스캔한다.
- 운영 자동화: 해석 PC의 로컬 수집 에이전트가 `manifest.json`과 결과를 API로 올린다.

## 7. 유효성 검증과 보안

- 변수 카탈로그에 정의된 `data_type`과 가져온 값의 실제 유형이 일치해야 한다.
- 단위가 정의된 변수는 단위 누락을 허용하지 않는다.
- `FLOAT`/`INTEGER`에는 유한 수만 허용하고, 커브 포인트의 `point_index` 중복을 금지한다.
- 미디어는 MIME, 확장자, 최대 크기, SHA-256 체크섬을 검증한다.
- 영상은 MP4/WebM, 3D는 GLB/GLTF 등 허용 목록만 사용한다.
- 업로드 파일명은 정규화하고 `..`, 절대 경로, 외부 URL을 거부한다.
- 한 번의 가져오기는 하나의 DB 트랜잭션으로 완료한다. 오류가 나면 부분 결과를 남기지 않는다.

## 8. 현재 구현과 개발 범위

현재 MVP는 `scalar_results`의 실수/정수/텍스트 열과 `time_series_results`, `media_assets` 메타데이터 테이블을 보유한다. 대시보드와 변수 카탈로그가 실제로 연결된 데이터 유형은 숫자와 시간 이력 중심이다.

다음 구현 단계에서 아래를 추가한다.

1. 변수 카탈로그의 `FLOAT`, `INTEGER`, `TEXT`, `CURVE`, `IMAGE`, `VIDEO`, `MODEL_3D` 확장
2. `curve_results`, `curve_points`, 선택적 `asset_blobs` 마이그레이션
3. 폴더 스키마 편집·버전 관리 및 가져오기 미리보기
4. 이미지·영상 업로드와 `/api/assets/{asset_id}` 스트리밍 API
5. 데이터 유형별 위젯 필터링, 빈 데이터 상태, 대용량 커브 다운샘플링
6. DuckDB와 PostgreSQL 양쪽에서 동일한 Repository/API 계약을 유지하는 데이터 접근 계층

## 9. PostgreSQL 전환 메모

DuckDB의 `DOUBLE`, `BIGINT`, `VARCHAR`, `JSON`, `BLOB`은 PostgreSQL에서 각각 `DOUBLE PRECISION`, `BIGINT`, `TEXT`, `JSONB`, `BYTEA`로 옮긴다. UUID, 외래 키, CHECK 제약, 인덱스, 트랜잭션 규칙은 PostgreSQL 마이그레이션으로 명시한다.

상세 전환 절차와 환경별 실행 명령은 `docs/backend-sql-integration-guide.md`를 따른다. 이 문서는 그 전환 과정에서 추가해야 할 결과 형식·미디어·폴더 적재 사양이다.
