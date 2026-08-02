# 배치 실행·판정 기준·보고서 Run 선택 사양

- 문서 상태: 구현 기준안
- 문서 버전: 1.0
- 작성일: 2026-08-02
- 담당: Sol(계획·검토), Luna(구현)
- 대상: Analysis Canvas (`React + TypeScript + FastAPI + DuckDB/PostgreSQL`)
- 상위 계획: `docs/integrated-simulation-workbench-plan.md` 0.8

## 1. 결론

이번 변경은 세 가지를 하나의 일관된 계약으로 구현한다.

1. 보고서 내보내기의 Run은 사용자가 ID를 입력하는 값이 아니라, 현재 하중 경우에 존재하는 `AnalysisRun` 중 하나를 선택하는 드롭다운으로 제공한다. 선택된 Run을 보고서 데이터 snapshot의 원천으로 고정한다.
2. Chassis 관리 기준은 Chassis Rear 결과 중 `영구변형`이며 단위가 `mm`인 모든 정량 값에 적용한다. Open Cell에는 별도 `Open Cell 판정 요약` 위젯과 `MPa` 응력 관리 기준을 추가하며, 이 기준은 Open Cell 결과 중 단위가 `MPa`인 값에만 적용한다.
3. `해석 작업 실행`의 각 작업 행을 선택 가능한 master-detail 구조로 바꾸고, 상세 영역에서 진행률 갱신과 배치 실행을 제공한다. `작업 유형 관리`에는 `배치 경로 정의` 내부 창을 추가한다. 실행은 관리자가 버전으로 고정한 경로·인자·허용 루트만 사용하며 사용자가 임의 명령을 입력할 수 없다.

기존 계획의 Control Plane/Runner 분리, 수행자/관리자 화면 분리, 불변 버전, 허용 루트, 임의 명령 금지 원칙은 그대로 유지한다. 다만 상위 계획 21.3의 완료 작업 수만으로 계산하는 진행률은 이번 사양의 작업별 진행률 합산 방식으로 대체한다.

## 2. 현재 구현 검토 결과

### 2.1 재사용할 기반

- 보고서 내보내기는 현재 분석 페이지와 레이아웃을 드롭다운으로 선택하고 `ReportSource`와 콘텐츠 snapshot을 만든다.
- 같은 하중 경우의 Run 목록과 Run 비교 UI에는 이미 `GET /api/load-cases/{load_case_id}/runs` 및 Run 드롭다운이 있다.
- `quality_thresholds`와 `PUT /api/quality-thresholds/{criterion_key}`가 있으며 Chassis 기준 저장 시 기존 결과를 재판정한다.
- Chassis 시스템 페이지에는 `chassis_summary` 위젯과 관리 기준 입력 UI가 있다.
- Open Cell 결과는 정량·시계열·맵 위젯에 기준선을 표시하지만, Chassis와 같은 독립 판정 요약/기준 저장 위젯은 없다.
- `해석 작업 실행`은 `request_work_items`의 순차 상태를 표시하고 현재 작업에만 시작/완료 버튼을 노출한다.
- `작업 유형 관리`는 관리자 전용이며 Request Type의 불변 버전을 저장한다.
- `task_type_versions`, `request_type_versions`, `workflow_runs`, `task_runs`, `task_run_events`, `request_work_plans`, `request_work_items`를 재사용할 수 있다.

### 2.2 반드시 보완할 문제

- 일반 보고서의 데이터 원천에는 선택된 `run_id`가 고정되어 있지 않다. 현재 overview가 최신 Run 중심이어서, 화면에서 다른 Run을 고르더라도 보고서 snapshot을 재현할 계약이 없다.
- `quality_thresholds.criterion_key`만으로 갱신하는 현재 API는 프로젝트 문맥이 URL에 드러나지 않는다. 다중 프로젝트에서 동일 criterion을 운영하려면 프로젝트 식별자를 포함해야 한다.
- Chassis 재판정 쿼리는 변수 키 패턴만 확인한다. 요구사항은 의미와 단위를 함께 제한하므로 `CHASSIS_REAR + permanent deformation + mm` 교집합으로 좁혀야 한다.
- Open Cell의 기준은 결과 행에 포함된 첫 threshold를 사실상 사용한다. 프로젝트 관리 기준의 정본과 적용 범위가 명시적이지 않다.
- `request_work_items`에는 작업 내부 진행률과 실행 시도 정보가 없다. 완료 작업 수 비율만으로는 배치 실행 중 진행도를 표현할 수 없다.
- 현재 `workflow_runs.execution_mode`은 DB 제약으로 `DEMO_ONLY`만 허용한다. 실제 Batch Runner 정보 없이 이 제약을 무조건 해제하면 안전 계약과 충돌한다.

### 2.3 이번 변경의 실행 경계

이번 UI/API는 `BATCH` 실행 계약과 프로파일 정의를 추가한다. 실제 프로세스를 시작하는 기능은 다음 정보가 등록되고 preflight를 통과한 프로파일에서만 활성화한다.

- Runner 또는 실행 호스트 식별자와 상태
- 허용 작업 루트 alias와 실제 root mapping
- 승인된 실행 파일 alias 및 버전
- 인자 token template과 입력 schema
- 로그 상대 경로와 진행률 parser
- 결과 상대 경로와 수집 규칙

정보가 없는 개발/데모 환경에서는 동일 버튼으로 `DEMO_ONLY` dispatch를 생성해 profile snapshot, 실행 시도와 이벤트를 DB에 기록하고 UI·상태 계약까지만 검증한다. 이 dispatch는 `subprocess`, Scheduler, 외부 네트워크를 호출하지 않는다. `DEMO_ONLY`는 화면, API 응답, DB에서 항상 명확히 표시하며 실제 실행 성공으로 해석하지 않는다.

## 3. 공통 도메인 규칙

### 3.1 판정 범위

| 기준 키 | 분석 범위 | 적용 대상 | 단위 | 비교 | 기본값 |
|---|---|---|---|---|---:|
| `chassis_rear_permanent_deformation_mm` | `CHASSIS_REAR` | 모든 영구변형 정량 값 | `mm` | 값 `>=` 기준이면 FAIL | 5.0 |
| `open_cell_stress_mpa` | `OPEN_CELL` | 모든 응력 정량 값 | `MPa` | 값 `>=` 기준이면 FAIL | 75.0 |

적용 대상은 문자열 하나만으로 판단하지 않는다. 서버가 다음 교집합을 검증한다.

- Chassis: 결과가 현재 프로젝트에 속함 + 결과 그룹/분석이 Chassis Rear + `unit == "mm"` + 변수 정의 또는 canonical key가 영구변형 의미를 가짐.
- Open Cell: 결과가 현재 프로젝트에 속함 + 결과 그룹/분석이 Open Cell + `unit == "MPa"` + 숫자 결과임.

단위 비교는 저장 시 canonical unit으로 정규화한 뒤 수행한다. `Pa`, `kPa`, `N/mm²` 등 다른 표기는 이번 범위에서 자동 변환하지 않으며 기준 적용 대상에서 제외하고 데이터 품질 경고를 남긴다. 텍스트, 시계열 포인트, Chassis `mm` 값에 Open Cell 기준을 적용하지 않는다.

판정 경계는 기존 가져오기 경로와 일치하도록 `값 >= 기준 → FAIL`, `값 < 기준 → PASS`를 사용한다. 기준이 없거나 단위/범위가 맞지 않으면 임의 PASS로 만들지 않고 `NO_CRITERION` 또는 기존 판정을 유지하며 적용 제외 사유를 표시한다.

### 3.2 진행률과 상태

`request_work_items.progress`를 0~100 정수로 추가하고 다음 불변식을 적용한다.

| 작업 상태 | 허용 진행률 | 의미 |
|---|---:|---|
| `WAITING`, `READY` | 0 | 아직 실행하지 않음 |
| `IN_PROGRESS` | 1~99 | 수동 또는 Runner 이벤트로 진행 중 |
| `COMPLETED` | 100 | 완료 |
| `FAILED`, `BLOCKED`, `CANCELLED` | 마지막 값 유지 | 실패/차단/취소 시점의 진행률 |

의뢰 전체 진행률은 모든 화면에서 아래 식 하나를 사용한다.

```text
request_progress = round(sum(work_item.progress) / total_work_item_count)
```

마이그레이션 시 기존 데이터는 `COMPLETED=100`, 그 외 `0`으로 backfill하여 현재 표시를 보존한다. 배치 실행이 시작되면 상태를 `IN_PROGRESS`로 만들고 최소 1%를 기록한다. Runner가 보고한 진행률은 감소할 수 없다. 완료 API는 100%로 원자 전이한다.

`해석 작업 실행`, `해석 의뢰 현황`, `운영 대시보드`는 각각 계산하지 않고 `RequestMonitoringSummary` projection을 공통 사용한다. 진행률 이벤트와 작업 상태 전이는 같은 트랜잭션에서 저장한다.

### 3.3 배치 실행 상태

실행 시도 상태는 다음과 같다.

```text
PENDING → PREFLIGHT → QUEUED → RUNNING → SUCCEEDED
                      │          ├──────→ FAILED
                      │          └──────→ CANCELLED
                      └─────────────────→ REJECTED
```

- 한 작업에는 동시에 하나의 활성 시도(`PREFLIGHT`, `QUEUED`, `RUNNING`)만 허용한다.
- `SUCCEEDED`만 작업 완료의 근거가 될 수 있다. 로그 문구나 100% 이벤트만으로 성공 처리하지 않는다.
- `FAILED`/`CANCELLED` 후 재실행은 기존 row를 덮어쓰지 않고 새 attempt를 만든다.
- 제출 API는 idempotency key를 받아 중복 클릭으로 시도를 두 개 만들지 않는다.
- 순차 작업은 선행 작업이 모두 완료되어야 실행할 수 있다. 독립/병렬 정의는 snapshot의 dependency로 판정한다.

## 4. 데이터 모델

### 4.1 기존 테이블 변경

#### `quality_thresholds`

권고 PK는 `(project_id, criterion_key)`다. 기존 단일 `criterion_key` PK는 마이그레이션으로 복합 PK 또는 동일 효력을 갖는 unique constraint로 바꾼다.

추가 컬럼:

- `comparison_operator VARCHAR NOT NULL DEFAULT 'GTE_FAIL'`
- `scope_json JSON/JSONB NOT NULL`

예시:

```json
{
  "result_group": "OPEN_CELL",
  "quantity": "STRESS",
  "unit": "MPa",
  "data_types": ["NUMBER", "FLOAT", "INTEGER"]
}
```

Chassis scope는 `result_group=CHASSIS_REAR`, `quantity=PERMANENT_DEFORMATION`, `unit=mm`로 저장한다. 과거 행은 이 값으로 backfill한다.

#### `request_work_items`

추가 컬럼:

- `progress INTEGER NOT NULL DEFAULT 0`, check `0 <= progress <= 100`
- `progress_source VARCHAR NOT NULL DEFAULT 'SYSTEM'`, `SYSTEM|USER|RUNNER`
- `progress_message VARCHAR`
- `progress_updated_at TIMESTAMP`
- `latest_attempt_id VARCHAR NULL`

상태 check에는 `FAILED`, `BLOCKED`, `CANCELLED`를 추가한다.

#### `workflow_runs` / `task_runs`

- 실제 Runner가 준비된 경우에만 `execution_mode`에 `BATCH`를 허용한다.
- 프로파일 snapshot과 실행 attempt가 정본이므로 기존 데모 task event 구조와 혼동하지 않는다.
- 이번 구현의 DEMO 환경은 기존 `DEMO_ONLY` 제약을 유지하고 dispatch 기록만 추가한다. `BATCH` 허용 migration과 실제 Runner 호출은 운영 정보가 갖춰진 후 별도 승인 범위로 둔다.

### 4.2 신규 테이블

#### `batch_execution_profile_versions`

- PK: `(id, version)`
- `display_name`, `description`
- `task_type_refs_json`: 사용할 수 있는 Task Type 불변 버전 목록
- `runner_pool`, `execution_mode` (`LOCAL|SLURM|PBS|DEMO_ONLY`)
- `work_root_alias`, `workdir_template`
- `application_alias`, `application_version`
- `argv_template_json`: 셸 문자열이 아닌 인자 token 배열
- `parameter_schema_json`
- `input_patterns_json`, `output_patterns_json`
- `log_sources_json`
- `timeout_seconds`, `resource_defaults_json`
- `is_active`, `created_by`, `created_at`

동일 `id` 저장은 새 version을 생성한다. 실행 시 선택한 version 전체를 attempt에 snapshot으로 고정한다.

#### `task_execution_attempts`

- `id` PK
- `request_id`, `work_item_id`
- `profile_id`, `profile_version`
- `profile_snapshot_json`
- `execution_mode`, `status`
- `runner_id`, `scheduler_job_id`
- `idempotency_key`
- `progress`, `last_message`, `exit_code`
- `created_by`, `created_at`, `started_at`, `completed_at`
- `failure_code`, `failure_detail`

인덱스/제약:

- `(work_item_id, created_at DESC)`
- `(status, created_at)`
- `(work_item_id, idempotency_key)` unique
- 활성 attempt 중복은 partial unique index 또는 서비스 트랜잭션 잠금으로 차단

#### `task_execution_events`

- `id` PK, `attempt_id`, `event_index`
- `event_type`, `level`, `message`, `progress`
- `source` (`CONTROL_PLANE|RUNNER|USER`), `occurred_at`
- unique `(attempt_id, event_index)`

### 4.3 API/프런트 타입 변경

- `ReportSource.analysis_page`에 `runId`를 필수 추가한다.
- `Overview` 조회 결과의 `run`은 선택한 Run ID와 일치해야 한다.
- `QualityThreshold`에 `project_id`, `comparison_operator`, `scope`를 추가한다.
- `DashboardWidget.type`과 분석 페이지 허용 목록에 `open_cell_summary`를 추가한다.
- `RequestWorkItem`에 `progress`, `progress_source`, `progress_message`, `latest_attempt`를 추가한다.
- `BatchExecutionProfileVersion`, `TaskExecutionAttempt`, `TaskExecutionEvent`, `WorkItemDetail` 타입을 추가한다.

## 5. API 계약

### 5.1 보고서 Run 선택

```text
GET /api/load-cases/{load_case_id}/runs
GET /api/load-cases/{load_case_id}/overview?run_id={run_id}
```

- `run_id` 생략 시 기존과 같이 최신 Run을 반환한다.
- 지정 Run이 해당 하중 경우에 속하지 않으면 404로 가장하지 않고 `422 RUN_LOAD_CASE_MISMATCH`를 반환한다.
- 선택 Run의 scalar/time-series/curve/media/note/location을 같은 snapshot에서 반환한다.
- 보고서 생성 시 `ReportSource`에 `loadCaseId + dashboardId + runId`를 고정한다.

### 5.2 판정 기준

```text
GET /api/projects/{project_id}/quality-thresholds
PUT /api/projects/{project_id}/quality-thresholds/{criterion_key}
```

요청:

```json
{
  "threshold_double": 75.0,
  "updated_by": "관리자"
}
```

응답에는 기준과 함께 `affected_result_count`, `excluded_unit_count`, `recalculated_run_count`를 포함한다. 서버는 URL project와 기준 row의 project가 다르면 404/403으로 차단하고, `unit` 또는 `scope`를 클라이언트 요청으로 바꾸게 하지 않는다.

기존 `PUT /api/quality-thresholds/{criterion_key}`는 프런트 전환 뒤 제거하거나 한 릴리스 동안 deprecated wrapper로 유지하되 project를 서버에서 유일하게 판별할 수 없으면 호출을 거부한다.

### 5.3 작업 상세·진행률

```text
GET   /api/workbench/work-items/{item_id}
PATCH /api/workbench/work-items/{item_id}/progress
```

진행률 요청:

```json
{
  "progress": 40,
  "message": "입력 deck 검증 완료",
  "updated_by": "해석 담당자",
  "idempotency_key": "uuid"
}
```

- editor/admin만 갱신할 수 있다.
- 현재 작업이 아니거나 상태가 `IN_PROGRESS`가 아니면 409를 반환한다.
- 수동 감소, 100 직접 입력, 범위 밖 값은 422를 반환한다. 완료는 기존 완료 API를 사용한다.
- 응답은 갱신된 work item과 `RequestMonitoringSummary`를 함께 반환한다.

### 5.4 배치 경로 정의

```text
GET  /api/admin/workbench/batch-profiles?all_versions=false
GET  /api/admin/workbench/batch-profiles/{id}/versions
POST /api/admin/workbench/batch-profiles
POST /api/admin/workbench/batch-profiles/{id}/{version}/preflight
```

POST는 새 불변 버전을 생성한다. 일반 사용자는 관리자 endpoint를 볼 수 없다. preflight는 저장된 profile version만 검사하며 요청 body에서 실행 파일이나 셸 문자열을 받지 않는다.

### 5.5 배치 실행

```text
POST /api/workbench/work-items/{item_id}/batch-attempts
GET  /api/workbench/work-items/{item_id}/batch-attempts
GET  /api/workbench/batch-attempts/{attempt_id}
POST /api/workbench/batch-attempts/{attempt_id}/cancel
POST /api/workbench/batch-attempts/{attempt_id}/retry
GET  /api/workbench/batch-attempts/{attempt_id}/events
```

실행 요청은 `profile_id`, `profile_version`, 검증된 `parameters`, `idempotency_key`만 받는다. `command`, `shell`, `cwd`, 절대 `path`, 임의 `environment` 필드는 strict schema로 422 처리한다.

실행 전 서버 검증:

1. 사용자 역할과 작업 배정 확인
2. 작업이 현재 실행 가능 상태인지 확인
3. profile이 active이고 해당 Task Type Version을 허용하는지 확인
4. dependency 완료 확인
5. 동일 작업 활성 attempt 부재 확인
6. parameter JSON Schema 검증
7. Runner heartbeat, 라이선스/도구, 허용 루트, 공간, 입력 존재 preflight
8. profile snapshot 저장 후 queue 등록

충돌은 `409 WORK_ITEM_NOT_RUNNABLE` 또는 `409 ACTIVE_ATTEMPT_EXISTS`, 환경 문제는 `503 RUNNER_UNAVAILABLE`, 입력 문제는 `422 PREFLIGHT_FAILED`로 구분한다.

## 6. UI 흐름

### 6.1 보고서 내보내기

일반 분석 페이지 보고서 toolbar 순서는 다음과 같다.

```text
분석 페이지 [dropdown] · Run [dropdown] · 출력 레이아웃 [dropdown] · 출력 버전 [dropdown]
```

- Run 목록은 현재 하중 경우의 Run만 표시한다.
- 기본값은 현재 화면의 Run이며 없으면 최신 Run이다.
- 라벨은 `Run {run_no} · {overall_verdict} · {완료일 또는 상태}`를 사용하고 내부 UUID를 사용자에게 주 라벨로 노출하지 않는다.
- Run 변경 시 overview와 보고서 콘텐츠 snapshot을 다시 읽고, 레이아웃의 `contentId` 바인딩을 새 snapshot에 맞게 준비한다.
- 로딩 중 생성 버튼을 비활성화한다. 실패하면 이전 선택과 snapshot을 유지하고 오류를 표시한다.
- Run이 없으면 비활성 드롭다운에 `등록된 Run 없음`을 표시하고 PPTX 생성을 막는다.
- Run 비교 보고서는 기준/대상 Run 두 개가 원천이다. raw ID 텍스트 입력을 두지 않고 비교 화면과 같은 두 드롭다운을 사용하거나, 이미 선택된 비교 문맥을 읽기 전용 라벨로 표시한다.

### 6.2 Chassis 판정 요약

- 제목: `Chassis 판정 요약`
- 표시: 전체 판정, 최대 영구변형, 관리 기준, 적용 대상 개수
- 안내: `Chassis Rear의 모든 영구변형(mm)에 동일 기준을 적용합니다. 값이 기준 이상이면 FAIL입니다.`
- 기준 저장 권한은 admin으로 제한한다. editor/viewer에게는 값과 적용 범위만 읽기 전용으로 표시한다.
- 저장 성공 시 영향을 받은 Chassis 값과 전체/분석 판정을 서버에서 재계산하고 현재 overview를 다시 읽는다.
- Chassis 이외 값, `mm`가 아닌 값, 영구변형이 아닌 값은 변경되지 않는다.

### 6.3 Open Cell 판정 요약 위젯

새 위젯 타입은 `open_cell_summary`, 기본 크기는 12×2다. Open Cell 시스템 페이지의 기본 정의에 추가하고, 기존 대시보드는 시작 시 멱등 보강한다. 사용자가 기존 버전을 편집 중인 경우 강제로 live 정의를 덮어쓰지 않는다.

표시:

- 전체 판정: 적용 대상 중 하나라도 FAIL이면 FAIL, 데이터가 없으면 NO_DATA
- 최대 응력: 적용 대상 `MPa` 값의 최대값
- 관리 기준: `open_cell_stress_mpa`
- 적용 대상: `MPa 결과 N개`
- 관리자 기준 입력과 저장 버튼
- 안내: `Open Cell의 MPa 정량 결과에만 적용합니다. 다른 단위 결과는 판정에서 제외합니다.`

위젯 카탈로그, 분석 페이지 schema allowlist, Widget 설정 목록, 보고서 콘텐츠 변환, CSS, OpenAPI/TypeScript 타입을 모두 함께 갱신한다.

### 6.4 해석 작업 실행 master-detail

작업 목록의 모든 행은 마우스와 키보드로 선택할 수 있다. 선택은 실행이나 상태 전이를 일으키지 않는다.

```text
┌ 배정 작업 순서 ─────────────────────────────┐
│ 작업 1  완료 100%                           │
│ 작업 2  진행 중 40%  ← 선택                 │
│ 작업 3  선행 작업 대기 0%                   │
└──────────────────────────────────────────────┘
┌ 선택 작업 상세 ─────────────────────────────┐
│ 목적 / 필요 입력 / 받을 결과 / dependency   │
│ 실행 프로파일 [dropdown] / preflight 상태    │
│ 진행률 40% [진행률 갱신]                     │
│ [배치 실행] [취소] [재실행] [작업 완료]      │
│ 최근 이벤트 / 로그 / 결과                   │
└──────────────────────────────────────────────┘
```

- 초기 선택은 현재 `IN_PROGRESS`, 없으면 첫 `READY`, 모두 완료면 마지막 작업이다.
- 행에는 상태, 진행률, 담당자, 최근 갱신 시각을 표시한다.
- 상세의 `하는 일`, `필요 입력`, `받을 결과`는 기존 `TASK_GUIDANCE`와 Task Type 계약을 재사용한다.
- 실행 프로파일 드롭다운에는 선택 Task Type Version과 호환되는 active profile만 표시한다.
- `배치 실행`은 READY/IN_PROGRESS이면서 dependency와 preflight를 만족할 때만 활성화한다.
- 비활성 버튼에는 `선행 작업 대기`, `프로파일 미정`, `Runner 오프라인`, `이미 실행 중`, `권한 없음` 같은 한 가지 주 사유를 표시한다.
- `진행률 갱신`은 사용자 지정 1~99 숫자와 메모를 저장한다. 활성 Batch attempt가 RUNNING이면 Runner가 정본이므로 수동 입력을 잠그거나 관리자 override로 별도 감사 기록을 남긴다.
- 완료 행도 상세·이벤트·결과를 볼 수 있으나 실행 버튼은 `재실행` 정책이 허용될 때만 보인다.

### 6.5 작업 유형 관리 > 배치 경로 정의

관리 화면 안에 상단 내부 탭 또는 우측 drawer를 추가한다.

```text
[의뢰 유형 정의] [배치 경로 정의]
```

`배치 경로 정의`는 HyperStudy의 resource/application 등록 개념을 참고하되 셸 편집기가 아니다.

- 기본 정보: 프로파일 ID, 표시명, 설명, active 여부
- 적용 작업: Task Type + Version 다중 선택
- 실행 위치: Runner pool, Local/Slurm/PBS/DEMO_ONLY
- 작업 경로: work root alias, 상대 workdir template
- 응용 프로그램: 승인된 application alias/version
- 인자: schema로 허용된 token과 placeholder 순서
- 입출력: 상대 glob, 필수 여부, 충돌 정책
- 로그: 상대 glob, encoding, parser profile
- 리소스: CPU, memory, walltime 기본값과 허용 범위
- 검증: `저장 전 검사`, 검사 결과, 마지막 검증 시각
- 저장: `새 불변 버전 저장`

물리 절대경로는 Runner 관리자 설정에만 보관한다. 웹의 작업 유형 관리자도 임의 UNC host, `..`, 장치 경로, shell operator, 환경변수 이름을 입력할 수 없다.

## 7. 권한·보안·감사

| 기능 | viewer | editor | admin |
|---|---:|---:|---:|
| 보고서 Run 선택/내보내기 | 허용 | 허용 | 허용 |
| 판정 기준 조회 | 허용 | 허용 | 허용 |
| 판정 기준 변경 | 금지 | 금지 | 허용 |
| 작업 행/상세 조회 | 허용 범위 내 | 허용 범위 내 | 허용 |
| 진행률 수동 갱신 | 금지 | 본인 배정 작업 | 허용 |
| 배치 실행/취소 | 금지 | 본인 배정 작업 | 허용 |
| 배치 프로파일 작성/버전 저장 | 금지 | 금지 | 허용 |

모든 기준 변경, 진행률 갱신, preflight, 제출, 취소, 재시도, 프로파일 버전 저장은 audit event를 남긴다. 비밀값은 profile JSON이나 로그에 저장하지 않고 secret reference만 저장한다. 오류 응답과 UI에는 물리 root, token, license server 주소를 노출하지 않는다.

## 8. 수용 기준

### AC-1 보고서 Run 드롭다운

1. 보고서 내보내기에서 Run은 `<input>`이 아니라 `<select>` 또는 동등한 선택 컴포넌트다.
2. 같은 하중 경우의 Run만 보이며 기본 선택은 현재/최신 Run이다.
3. Run 변경 후 생성한 PPTX의 정량·시계열·미디어와 Run 표기가 모두 선택 Run과 일치한다.
4. 다른 하중 경우의 Run ID를 API에 보내면 거부한다.
5. Run 비교 보고서도 UUID 직접 입력을 요구하지 않는다.

### AC-2 Chassis 기준 범위

1. 관리 기준 변경 시 해당 프로젝트의 모든 Chassis Rear 영구변형 `mm` 값이 같은 기준으로 재판정된다.
2. Chassis의 다른 단위/다른 물리량 및 Open Cell 값은 변경되지 않는다.
3. 위젯은 적용 대상 개수와 `mm` 범위를 명시한다.
4. 기준 이상은 FAIL, 기준 미만은 PASS다.

### AC-3 Open Cell 판정 요약

1. Open Cell 페이지에 `Open Cell 판정 요약` 위젯이 표시되고 카탈로그에서도 추가할 수 있다.
2. admin은 양수 MPa 기준을 저장할 수 있고 editor/viewer는 읽기 전용이다.
3. 기준 변경은 Open Cell의 `MPa` 정량 값만 재판정한다.
4. Chassis `mm`, 텍스트, 다른 단위 값은 변경되지 않는다.
5. 위젯과 보고서에는 새 판정과 기준이 일치하게 반영된다.

### AC-4 작업 행 상세

1. 어느 작업 행이든 클릭/Enter/Space로 선택하고 상세를 볼 수 있다.
2. 선택만으로 작업 상태, 진행률, 실행 attempt가 바뀌지 않는다.
3. 상세에는 관련 업무 설명, 입출력, 상태, 진행률, 호환 프로파일, 실행 버튼과 최근 이벤트가 보인다.
4. 실행 불가 작업은 버튼 비활성 사유가 보인다.

### AC-5 진행률 동기화

1. IN_PROGRESS 작업의 1~99 진행률을 저장할 수 있다.
2. 감소, 100 직접 설정, 현재 작업이 아닌 항목 갱신은 거부한다.
3. 의뢰 진행률은 작업별 progress 평균으로 계산되고 세 화면에서 동일하다.
4. 작업 완료 시 100%, 다음 READY 작업은 0%로 반영된다.
5. 중복 요청은 이벤트나 시각을 중복 생성하지 않는다.

### AC-6 배치 프로파일과 실행

1. admin만 `배치 경로 정의`에서 프로파일을 새 불변 버전으로 저장할 수 있다.
2. 수행자는 선택 작업과 호환되는 active profile만 선택할 수 있다.
3. 배치 실행 요청은 임의 command/shell/cwd/path/environment 필드를 거부한다.
4. 현재 범위의 배치 버튼은 profile snapshot을 포함한 DEMO_ONLY dispatch/attempt/event를 저장하지만 `subprocess`, Scheduler, 외부 네트워크를 호출하지 않는다.
5. preflight 실패 시 실행 attempt가 RUNNING으로 전이하지 않고 이유를 표시한다.
6. 중복 클릭은 하나의 attempt만 만든다.
7. 실행 이벤트가 상세와 공통 진행률 projection에 반영된다.
8. 실제 실행 정보가 없는 환경은 DEMO_ONLY임을 명확히 표시하고 외부 프로세스를 실행하지 않는다.

## 9. 테스트 계획

### 9.1 백엔드 단위/통합

- overview `run_id` 지정/기본값/하중 경우 불일치/존재하지 않는 Run
- report source snapshot이 선택 Run의 모든 결과 테이블을 일관되게 필터링하는지 검증
- Chassis 기준 저장: 모든 `CHASSIS_REAR + permanent deformation + mm` 갱신, 경계값, 타 단위 제외
- Open Cell 기준 저장: 모든 `OPEN_CELL + MPa` 갱신, Chassis/타 단위 제외, NO_CRITERION
- project-scoped threshold 권한과 다른 프로젝트 오염 방지
- work item progress 상태 전이, 단조 증가, 100 차단, 멱등성, 동시 갱신
- 공통 monitoring projection 계산과 세 API 응답 일치
- profile immutable version, task 호환성, strict schema, path traversal/UNC/device/shell token 거부
- attempt 단일 활성 제약, preflight 실패, submit/cancel/retry, event 중복 방지
- DEMO_ONLY 환경에서 subprocess/scheduler/network 실행 코드가 호출되지 않는지 검증
- PostgreSQL migration과 DuckDB 개발 schema의 동일 계약 검증

### 9.2 프런트엔드/E2E

- 보고서 dialog의 Run selector 접근성 role이 combobox이며 text input이 아님
- Run 변경 뒤 콘텐츠 제목/정량/그래프와 PPT snapshot 일치
- Chassis 기준 저장 전후 모든 mm 영구변형 row와 판정 갱신
- Open Cell summary 위젯 노출, admin 저장, editor/viewer 읽기 전용
- 작업 행 클릭/키보드 선택, selected 스타일, 상세 업무 안내
- READY/WAITING/IN_PROGRESS/COMPLETED별 실행 버튼과 비활성 이유
- 진행률 갱신 후 작업 실행·의뢰 현황·운영 대시보드의 동일 값
- 배치 경로 내부 탭 admin 전용, 새 버전 저장 및 재조회
- 중복 배치 클릭 방지, preflight 오류, 실행/취소/재시도 이벤트 표시
- 기존 14개 E2E, 보고서 레이아웃/버전, Run 비교, 데이터 가져오기 회귀

### 9.3 수동 브라우저 검증

- 첨부 화면의 `배정 작업 순서`에서 현재/대기/완료 행을 각각 선택해 상세가 바뀌는지 확인
- 좁은 화면에서 목록과 상세가 세로로 배치되고 버튼/문구가 잘리지 않는지 확인
- 관리자와 editor/viewer 계정으로 메뉴·버튼·API 권한이 일치하는지 확인
- console error, 실패 요청의 원문 `Not Found`, stale response가 없는지 확인

## 10. 구현 순서와 파일 경계

1. DB migration과 seed: 두 criterion scope, progress, profile/attempt/event 테이블
2. 백엔드 판정 서비스와 project-scoped threshold API
3. overview Run filter와 보고서 source 타입
4. Open Cell summary widget/schema/default dashboard 멱등 보강
5. work item detail/progress/profile/attempt API와 monitoring projection
6. `SimulationWorkbench` master-detail 및 배치 버튼
7. `WorkbenchTypeAdmin` 내부 `배치 경로 정의` 창
8. OpenAPI JSON/생성 TypeScript 타입 갱신
9. 백엔드·빌드·E2E·실제 PostgreSQL 회귀 검증

권고 코드 경계:

- `backend/app/services/verdict_service.py`: criterion scope와 재판정 규칙
- `backend/app/repositories/workbench.py`: profile/attempt/work item 영속화
- `backend/app/services/request_monitoring.py`: 공통 progress projection
- `backend/app/routers/workbench.py`: 작업 상세·진행률·배치 API
- 새 `backend/app/services/batch_execution.py`: preflight와 attempt 상태 머신
- `frontend/src/features/workbench/SimulationWorkbench.tsx`: master-detail
- `frontend/src/features/workbench/api.ts`, `types.ts`: API 계약
- `frontend/src/App.tsx`: 보고서 Run selector, 판정 위젯 연결
- `frontend/src/reportExport.ts`: 선택 Run snapshot과 `open_cell_summary` 보고서 콘텐츠

대형 `App.tsx`/`main.py`에 상태 머신과 SQL을 더 넣지 말고, 새 배치 로직은 위 모듈 경계를 우선한다.

## 11. 구현 검토 체크리스트

- [ ] 보고서 Run 선택값이 실제 데이터 조회와 `ReportSource.runId`에 연결되어 있는가
- [ ] Run 라벨에 UUID 대신 run_no/판정/일자가 보이는가
- [ ] Chassis 기준 대상이 이름 일부가 아니라 의미 + `mm` 교집합인가
- [ ] Open Cell 기준 대상이 Open Cell + `MPa`로 제한되는가
- [ ] threshold 변경이 트랜잭션으로 기준·결과·상위 판정을 함께 갱신하는가
- [ ] Open Cell 위젯이 schema/catalog/default/report 경로에 모두 등록됐는가
- [ ] 선택 행과 현재 실행 행을 혼동하지 않는가
- [ ] progress 감소·100 직접 설정·중복 요청을 서버가 차단하는가
- [ ] 세 화면이 동일 monitoring projection을 사용하는가
- [ ] profile은 불변 version이며 attempt에 snapshot되는가
- [ ] 실제 실행 API가 raw command/path/environment를 받지 않는가
- [ ] 허용 루트 최종 검증을 Runner에서도 반복하는가
- [ ] DEMO_ONLY와 BATCH가 화면·DB·이벤트에서 구분되는가
- [ ] 기존 더티 변경과 기존 보고서/분석 페이지 버전을 보존했는가

## 12. 남은 외부 입력과 재개 지점

실제 Batch 프로세스까지 활성화하려면 다음 운영 정보가 필요하다. 이 정보가 없더라도 profile CRUD, DEMO_ONLY attempt, 상세/진행률 UI와 판정/보고서 기능은 구현·검증할 수 있다.

- 첫 대상 Task와 응용 프로그램/버전
- 실행 호스트 OS, Runner 설치 위치/계정
- 승인 executable 경로 또는 alias mapping
- work root와 입력/출력/로그 실제 예시
- 인자 목록과 필수/선택 parameter
- 성공 exit code, scheduler 상태, 로그 parser, 결과 수집 조건
- 취소/timeout/retry 정책

토큰 한도에 도달해 중단해야 할 때는 `log/work-log.md` 최신 항목에 다음을 남긴다.

- 완료한 migration/API/UI/test 파일
- 마지막 성공한 검증 명령과 결과
- 실패 또는 미실행 검증
- 다음 첫 수정 파일과 미완료 수용 기준 번호
- 실제 실행을 활성화했는지 여부와 DEMO_ONLY 안전 상태

## 13. 2026-08-02 구현 결과와 계획 대비 범위

이번 구현은 사용자에게 바로 필요한 Run 선택, 판정 기준, master-detail, 진행률, 배치 경로 등록과 안전한 실행 기록을 완료했다. 실제 Runner 정보가 없는 환경이므로 4~8장의 운영용 완전 상태 머신은 활성화하지 않았고 다음의 축소 계약을 사용한다.

- `batch_path_profiles`: 현재 프로필을 upsert하고 호환 `task_type_ids`를 필수 저장한다. 수행자 UI와 dispatch API가 현재 작업의 Task Type 포함 여부를 모두 검사한다.
- `batch_dispatches`: 실행 시점 profile snapshot, command preview, 작업/워크플로 Run 참조와 `RECORDED_DEMO` 상태를 불변 기록한다.
- `workflow_runs.execution_mode`: 기존 `DEMO_ONLY`만 유지한다. 외부 process, scheduler, network 호출 코드는 추가하지 않았다.
- 진행률: `progress`, `progress_updated_by`, `progress_updated_at`을 저장하고 시작=1, 완료=100, 수동 1~99 단조 증가 규칙과 공통 평균 projection을 적용한다.
- 판정 기준: 기존 프로젝트별 `quality_thresholds` 계약을 유지하면서 Chassis `mm + permanent_deformation`, Open Cell `MPa + stress` 교집합으로 갱신 범위를 제한한다.
- Run 보고서: 일반 분석과 Run 비교 모두 같은 하중 경우의 AnalysisRun 드롭다운을 사용하며, 선택 결과로 overview·콘텐츠·source snapshot을 재생성한다.

운영 Batch를 후속 활성화할 때는 프로필 불변 version, 승인 application alias, 허용 root, tokenized argv, idempotency, preflight, cancel/retry, Runner 이중 검증을 4~8장 원안대로 추가해야 한다. 현재 UI의 command preview는 기록용이며 임의 명령 실행 권한을 부여하지 않는다.
