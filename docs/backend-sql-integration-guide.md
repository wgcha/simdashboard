# 백엔드·SQL·대시보드 연동 가이드

## 1. 목적

이 문서는 Analysis Canvas의 변수 카탈로그, 해석 결과, 대시보드 위젯이 DuckDB에서 어떻게 연결되는지 설명하고 향후 PostgreSQL로 이전하는 기준을 정의한다.

핵심 원칙은 다음과 같다.

- 변수의 의미와 표시 규칙은 `variable_definitions`에서 관리한다.
- 실제 해석값은 `scalar_results`와 `time_series_results`에 저장한다.
- 두 영역은 `load_case_id + variable_key`로 연결한다.
- 대시보드 위젯은 `settings.variableId`에 `variable_key`를 저장한다.
- UI는 SQL을 직접 실행하지 않고 FastAPI의 검증된 CRUD API만 호출한다.
- 삭제는 결과 데이터를 지우지 않는 비활성화 방식으로 처리한다.

## 2. 데이터 흐름

```text
변수 카탈로그 UI
  └─ POST/PUT/DELETE /api/load-cases/{load_case_id}/variables
      └─ VariableCatalogRepository
          └─ variable_definitions (DuckDB)

Radioss/요약 CSV 가져오기
  └─ variable_key 검증
      ├─ scalar_results
      └─ time_series_results

대시보드 편집기
  └─ GET /api/load-cases/{load_case_id}/variables
      └─ 허용 위젯과 집계 방식에 맞는 변수 선택
          └─ dashboard.definition_json.widgets[].settings.variableId
              └─ 결과 조회 시 variable_key로 필터
```

## 3. SQL 테이블 역할

### `variable_definitions`

사용자가 편집하는 의미 계층이다.

| 열 | 의미 |
|---|---|
| `id` | 내부 정의 ID |
| `load_case_id` | 변수가 속한 하중 경우 |
| `variable_key` | 결과 데이터 및 위젯과 연결되는 불변 키 |
| `display_name` | 한국어 표시 이름 |
| `data_type` | `NUMBER` 또는 `TIME_SERIES` |
| `unit` | MPa, mm, J 등의 표시 단위 |
| `description` | 변수 정의와 계산 의미 |
| `filterable` | 대시보드 필터 사용 가능 여부 |
| `source` | `scalar_results` 또는 `time_series_results` |
| `threshold_double` | 숫자 변수의 판정 기준 |
| `allowed_widgets_json` | 허용 시각화 목록 |
| `allowed_aggregations_json` | 허용 집계 방식 목록 |
| `analysis_type` | DROP 또는 SIDE_CLAMP |
| `result_group` | OPEN_CELL, CHASSIS_REAR, CUSTOM |
| `is_active` | 카탈로그 노출 여부 |
| `created_at`, `updated_at`, `updated_by` | 변경 감사 정보 |

`(load_case_id, variable_key)`는 유일해야 한다. 변수 키는 생성 후 수정하지 않는다. 키를 변경하면 결과 데이터와 저장된 대시보드 참조까지 모두 마이그레이션해야 하기 때문이다.

### 결과 테이블

- `scalar_results`: 숫자형 단일 결과, 기준값, 판정
- `time_series_results`: 시간별 결과
- `result_locations`: 최대값이 발생한 노드·요소와 위치
- `analysis_runs`: 결과가 속한 해석 실행

결과 테이블에는 변수 표시 규칙을 중복 관리하지 않는다. 가져온 파일의 표시 이름은 원본 추적용이며, 사용자 화면의 최신 의미 정의는 카탈로그를 기준으로 한다.

### 대시보드 테이블

- `dashboards`: 현재 레이아웃 JSON
- `dashboard_versions`: 저장 이력

위젯의 변수 연결 예제:

```json
{
  "id": "energy-kpi",
  "type": "kpi",
  "title": "프레임 흡수 에너지",
  "settings": {
    "variableId": "custom_frame_energy",
    "aggregation": "MAX",
    "showThreshold": true
  }
}
```

## 4. 변수 CRUD API

### 조회

```http
GET /api/load-cases/{load_case_id}/variables
```

활성 변수만 반환한다. `has_data`는 연결된 결과의 존재 여부, `dashboard_usage_count`는 저장된 대시보드 참조 수다.

### 생성

```http
POST /api/load-cases/{load_case_id}/variables
Content-Type: application/json

{
  "variable_key": "custom_frame_energy",
  "display_name": "프레임 흡수 에너지",
  "data_type": "NUMBER",
  "unit": "J",
  "description": "프레임이 흡수한 최대 에너지",
  "threshold": 120,
  "allowed_widgets": ["kpi", "edge_bar", "result_table"],
  "allowed_aggregations": ["MAX", "AVG", "LATEST"],
  "result_group": "CUSTOM",
  "filterable": true,
  "updated_by": "관리자"
}
```

숫자 변수에는 현재 판정 일관성을 위해 기준값이 필수다. 시간 이력 변수는 기준값을 사용하지 않는다.

### 수정

```http
PUT /api/load-cases/{load_case_id}/variables/{variable_key}
```

표시 이름, 단위, 설명, 기준값, 그룹, 필터 여부, 허용 그래프와 집계 방식을 수정한다. `variable_key`와 `data_type`은 참조 안전을 위해 고정한다.

### 삭제

```http
DELETE /api/load-cases/{load_case_id}/variables/{variable_key}
```

물리 삭제 대신 `is_active=false`로 변경한다. 결과 행은 삭제하지 않는다. 저장된 대시보드에서 사용 중이면 HTTP 409를 반환하므로 먼저 위젯 바인딩을 제거하고 레이아웃을 저장해야 한다.

## 5. 결과 데이터 연결

카탈로그에서 만든 변수는 선언만 된 상태이므로 처음에는 `has_data=false`다. 동일한 `variable_key`를 가진 결과를 가져오면 다시 코드를 수정하지 않아도 그래프에 표시된다.

숫자 결과 CSV 예제:

```csv
record_type,variable_key,display_name,value,unit,threshold,time,time_unit,value_unit
scalar,custom_frame_energy,프레임 흡수 에너지,96.4,J,120,,,,
```

시간 이력 예제:

```csv
record_type,variable_key,display_name,value,unit,threshold,time,time_unit,value_unit
time_series,custom_frame_energy_time,프레임 에너지 이력,32.1,,,,12.5,ms,J
```

가져오기 API는 먼저 활성 카탈로그를 읽고 파일의 변수 키와 데이터 유형을 검증한다. 선언되지 않았거나 유형이 다른 변수는 저장하지 않는다.

## 6. 백엔드 코드 경계

- `backend/app/main.py`: HTTP 요청 검증과 응답 상태 코드
- `backend/app/repositories/variable_catalog.py`: 변수 정의 SQL과 참조 검사
- `backend/app/result_import.py`: 결과 파일 형식과 카탈로그 일치 검증
- `backend/app/database.py`: DuckDB 연결, 테이블 생성, 기존 결과의 카탈로그 백필
- `frontend/src/api.ts`: 브라우저의 유일한 CRUD 진입점

새 SQL 기능은 UI 컴포넌트에 넣지 않는다. 저장소 메서드와 API를 먼저 만들고 프런트엔드는 API 계약만 사용한다. 모든 값은 파라미터 바인딩으로 전달하며 변수 이름이나 사용자 문장을 SQL 문자열에 직접 연결하지 않는다.

## 7. PostgreSQL 이전

현재 `connect()`는 DuckDB 연결을 반환하며 PostgreSQL 모드는 의도적으로 차단되어 있다. 이전 시 다음 순서를 권장한다.

1. Alembic 또는 동등한 도구로 현재 DDL을 PostgreSQL 마이그레이션으로 작성한다.
2. JSON 열은 `JSONB`, 시간은 `TIMESTAMPTZ`, 불리언은 `BOOLEAN`을 사용한다.
3. `variable_definitions`에 아래 인덱스를 추가한다.

```sql
CREATE UNIQUE INDEX uq_variable_definition_key
ON variable_definitions(load_case_id, variable_key);

CREATE INDEX ix_variable_definition_active
ON variable_definitions(load_case_id, is_active);

CREATE INDEX ix_scalar_result_variable
ON scalar_results(analysis_run_id, variable_key);

CREATE INDEX ix_time_series_variable_time
ON time_series_results(analysis_run_id, variable_key, time_value);
```

4. `VariableCatalogRepository` 인터페이스는 유지하고 연결/SQL 실행 어댑터만 PostgreSQL 구현으로 교체한다.
5. DuckDB의 `INSERT OR IGNORE`는 PostgreSQL의 `INSERT ... ON CONFLICT DO NOTHING`으로 바꾼다.
6. DuckDB의 `?` 자리표시자는 사용하는 PostgreSQL 드라이버 또는 SQLAlchemy의 바인딩 방식으로 교체한다.
7. 대시보드 참조 검사는 애플리케이션 계층에서 유지하거나 JSONB 경로 인덱스와 조회로 최적화한다.
8. 환경변수로 저장소를 선택한다.

```powershell
$env:ANALYSIS_DB_BACKEND = "postgresql"
$env:DATABASE_URL = "postgresql+psycopg://user:password@host:5432/simdashboard"
```

운영 전환 시 API 응답 형식은 바꾸지 않는다. 그러면 프런트엔드와 저장된 대시보드 JSON을 그대로 유지할 수 있다.

## 8. 운영 안전 기준

- 운영 DB 변경 권한은 관리자 역할에만 부여한다.
- 물리 삭제는 별도 관리 작업으로 제한한다.
- 변수 키 변경이 필요하면 결과와 대시보드 JSON을 하나의 트랜잭션으로 마이그레이션한다.
- 단위 변경은 기존 결과의 수치 변환 없이 표시 문자열만 바꾸면 안 된다.
- 기준값 변경은 과거 판정 재계산 정책을 정한 뒤 수행한다.
- 카탈로그와 결과의 불일치, 미사용 변수, 단위 불일치를 정기 점검한다.

## 9. 검증 명령

```powershell
cd E:\simulation_dashboard\backend
..\.venv-runtime\Scripts\python.exe -m pytest -q

cd E:\simulation_dashboard\frontend
npm.cmd run build
```

CRUD 테스트는 SQL 행 변경, 대시보드 참조 중 삭제 차단, 비활성화 후 카탈로그 미노출을 함께 확인한다.
