# 업무 유형 관리·의뢰 접수·SPDM 연계 구현 계획

- 작성일: 2026-08-12
- 대상 시스템: Simulation Dashboard
- 상태: 구현 계획 확정

## 1. 목표와 결정 사항

작업 유형 관리에서 부서 업무에 사용할 세부 작업과 업무 유형을 정의하고, 의뢰 접수에서 새로 만든 활성 업무 유형을 선택할 수 있게 한다. SPDM 공유폴더에서 새 의뢰 폴더와 기존 폴더의 변화를 감지하고, 부서장 수기 지시와 같은 공통 수명주기로 관리한다.

공통 수명주기는 다음과 같다.

```text
의뢰 발생 → 접수 대기 → 수행 가능 → 수행 중 → 완료
```

확정된 제품 결정은 다음과 같다.

- 현재 프로젝트를 부서 범위로 사용한다. 프로젝트 관리자가 부서장 역할로 프로젝트별 업무 유형과 세부 작업을 관리한다.
- 기존 시스템 공통 유형은 유지하고, 프로젝트에서 만든 Custom 유형은 해당 프로젝트와 전역 관리자에게만 노출한다.
- Custom 업무는 재사용 가능한 세부 작업 카탈로그로 정의하며, 각 세부 작업은 이름과 수행 설명을 가진다. 별도 체크리스트는 이번 범위에 포함하지 않는다.
- 업무 유형은 세부 작업의 순서 또는 병렬 조합이며 불변 버전으로 저장한다. 이미 발생한 의뢰는 당시 버전과 작업계획 스냅샷을 유지한다.
- 부서장 수기 의뢰와 SPDM 의뢰 모두 담당자가 `의뢰 수령`을 눌러야 수행 가능한 상태가 된다.
- SPDM 의뢰는 관리자가 업무 유형과 담당자를 확정한 뒤 담당자 수령 대기로 전환한다.
- SPDM 폴더 변화는 새 의뢰를 만들지 않고 동일 의뢰의 변경 이력으로 기록한다. 진행 상태는 유지하며 앱 내 배지와 확인 이력을 제공한다.
- SPDM은 폴더 스키마로 프로젝트/의뢰 폴더 레벨을 설정하고, 자동 주기 감지와 관리자 수동 재감지를 모두 지원한다.

## 2. 용어와 업무 흐름

### 2.1 용어

| 화면 용어 | 내부 개념 | 설명 |
|---|---|---|
| 세부 작업 | Task Type | 수행자가 실제로 시작·진행·완료하는 재사용 가능 작업. Custom 작업은 이름과 수행 설명을 가진다. |
| 업무 유형 | Request Type | 하나 이상의 세부 작업과 순서/의존관계를 묶은 의뢰 템플릿. |
| 작업계획 | Request Work Plan | 의뢰 발생 시 선택한 업무 유형 버전을 복제한 변경 불가 스냅샷. |
| 의뢰 발생 | Occurred | SPDM 폴더 감지 또는 부서장 등록으로 의뢰 레코드가 만들어진 상태. |
| 접수 | Acceptance | 업무 유형·담당자가 확정된 의뢰를 담당자가 수령하는 행위. |
| 수행 | Execution | 작업계획의 세부 작업을 시작·진행·완료하는 과정. |

### 2.2 부서장 수기 지시 흐름

1. 프로젝트 관리자가 `작업 유형 관리`에서 Custom 세부 작업을 만들거나 기존 세부 작업을 선택한다.
2. 세부 작업을 순차 또는 병렬로 조합해 프로젝트 업무 유형의 새 불변 버전을 저장한다.
3. `의뢰 접수`의 `신규 업무 배정`에서 프로젝트, 업무 유형, 제목, 담당자, 기한, 지시 내용을 입력한다.
4. 저장 시 의뢰, 업무 유형 배정, 작업계획 스냅샷, 작업 항목을 한 트랜잭션으로 생성하고 `PENDING_ACCEPTANCE`로 둔다.
5. 담당자가 `내 접수 대기`에서 내용을 확인하고 `의뢰 수령`을 누르면 `READY`가 되며 첫 실행 가능 작업이 열린다.
6. 담당자가 세부 작업을 수행하고 모두 완료하면 의뢰가 `COMPLETED`가 된다.

### 2.3 SPDM 흐름

1. 활성 SPDM 감시 설정이 주기적으로 폴더를 스캔한다.
2. 폴더 스키마의 의뢰 레벨에 새 폴더가 나타나면 `SPDM` 출처 의뢰를 `OCCURRED`로 생성한다.
3. 프로젝트 관리자가 감지 목록에서 신규 의뢰를 열어 업무 유형과 담당자, 기한을 확정한다.
4. 작업계획을 생성하고 의뢰를 `PENDING_ACCEPTANCE`로 전환한다.
5. 담당자가 수령한 뒤 수기 지시와 동일한 수행 흐름을 따른다.
6. 같은 폴더의 구조 또는 파일 메타데이터가 바뀌면 동일 의뢰에 변경 이벤트를 추가하고 미확인 배지를 표시한다. 접수·수행·완료 상태는 자동으로 되돌리지 않는다.

### 2.4 상태 규칙

| 의뢰 상태 | 진입 조건 | 허용 동작 |
|---|---|---|
| `OCCURRED` | SPDM이 새 의뢰 폴더를 감지 | 관리자의 유형·담당자 확정, 중복/오탐 제외 |
| `PENDING_ACCEPTANCE` | 수기 배정 완료 또는 SPDM 배정 완료 | 지정 담당자의 의뢰 수령, 관리자의 담당자 재배정 |
| `READY` | 담당자 수령 완료 | 실행 가능한 첫 세부 작업 시작 |
| `IN_PROGRESS` | 하나 이상의 세부 작업 시작 또는 완료 | 진행률 저장, 현재 작업 완료, 다음 작업 시작 |
| `COMPLETED` | 필수 세부 작업 모두 완료 | 결과 열람, SPDM 후속 변경 확인 |

- `request_work_items`의 상태는 기존 `WAITING`, `READY`, `IN_PROGRESS`, `COMPLETED`를 유지한다.
- 작업계획 생성 시 모든 작업을 `WAITING`으로 저장한다. `의뢰 수령` 트랜잭션에서 선행 작업이 없는 첫 작업들을 `READY`로 바꾼다.
- 병렬 업무 유형은 모든 선행 조건이 완료된 작업을 동시에 `READY`로 전환한다. 기존 sequence 번호만으로 선행 조건을 판단하는 로직은 작업계획 snapshot의 `depends_on` 기준으로 교체한다.
- SPDM 변경 이벤트는 의뢰 수명주기와 독립적이다. 완료 후 변경도 기록하되 의뢰를 재개하지 않는다.

## 3. 데이터 및 서버 변경

### 3.1 업무 카탈로그 범위

기존 `task_type_versions`와 `request_type_versions`에 다음 범위 메타데이터를 추가한다.

- `scope_kind`: `SYSTEM` 또는 `PROJECT`
- `project_id`: 프로젝트 범위이면 필수, 시스템 범위이면 `NULL`
- `created_by`: 생성 사용자 ID 또는 canonical 표시명
- Custom 세부 작업은 `kind='CUSTOM'`으로 저장하고 기존 수동 시작·진행·완료 API를 사용한다.
- 기존 데이터는 `scope_kind='SYSTEM'`으로 backfill하여 모든 프로젝트에서 계속 선택할 수 있게 한다.
- ID는 기존 `(id, version)` 키를 유지하므로 전역에서 유일해야 한다. 프로젝트 Custom ID는 서버가 `custom-{slug}-{short-id}` 형식으로 생성하고, 같은 ID 저장은 새 버전 생성으로만 처리한다.
- 조회 시 현재 프로젝트의 활성 최신 버전과 활성 시스템 최신 버전을 합쳐 반환한다. 프로젝트 관리자는 자기 프로젝트 범위만 생성·버전업할 수 있다.

### 3.2 의뢰 출처와 접수 이력

의뢰 발생 시점부터 출처를 저장할 수 있도록 `request_origins`를 추가한다.

```text
request_origins
- request_id PK/FK
- source_type: SPDM | DEPARTMENT_HEAD | LEGACY_EXTERNAL
- source_reference
- external_key nullable, source_type 내 고유
- source_snapshot_json
- occurred_at
```

`analysis_requests`에는 다음 필드를 추가한다.

- `accepted_by_user_id`, `accepted_by`, `accepted_at`
- 상태 제약에 `OCCURRED`, `PENDING_ACCEPTANCE`, `READY`, `IN_PROGRESS`, `COMPLETED` 추가

기존 `request_work_plans.source_*` 필드는 실행계획 생성 당시의 출처 snapshot으로 유지한다. 기존 데이터는 work plan 출처를 바탕으로 `request_origins`를 backfill하고 현재 상태와 실행 이력에 따라 `accepted_at`을 채운다.

### 3.3 SPDM 감시 모델

다음 테이블을 추가한다.

```text
spdm_watch_configs
- id, project_id, display_name
- root_path
- schema_json: project_level, request_level, optional include/exclude patterns
- scan_interval_seconds
- is_active
- last_scan_started_at, last_scan_completed_at, last_error
- created_by, updated_by, created_at, updated_at

spdm_request_bindings
- request_id PK/FK
- watch_config_id FK
- normalized_relative_path
- external_key UNIQUE per watch config
- last_fingerprint
- first_detected_at, last_seen_at

spdm_change_events
- id, request_id, detected_at
- change_kind: CREATED | STRUCTURE_CHANGED
- previous_fingerprint nullable, current_fingerprint
- summary_json: added_paths, removed_paths, changed_paths, truncated
- acknowledged_by_user_id nullable, acknowledged_by nullable, acknowledged_at nullable
```

감지 규칙은 다음으로 고정한다.

- 폴더 스키마의 `request_level` 디렉터리 하나를 의뢰 하나로 본다.
- fingerprint는 의뢰 폴더 아래의 정규화된 상대 경로, 항목 종류, 파일 크기, 수정 시각을 정렬해 계산한다. 파일 본문은 읽거나 저장하지 않는다.
- 심볼릭 링크와 감시 루트 밖으로 해석되는 경로는 따라가지 않는다.
- 임시 파일 패턴(`~*`, `*.tmp`, `.DS_Store`)은 기본 제외하며 설정에서 추가 제외 패턴을 허용한다.
- 같은 `external_key`는 반복 스캔해도 의뢰를 중복 생성하지 않는다.
- 스캔 도중 접근 실패한 폴더는 기존 항목 삭제로 판정하지 않고 scan 오류로 기록한다.
- 변경 경로 목록은 상한을 두고 초과 시 `truncated=true`로 기록한다.

자동 감지는 애플리케이션 lifespan의 단일 주기 작업으로 실행하고, 현재 단일 Uvicorn 프로세스 배포를 기준으로 한다. PostgreSQL 다중 프로세스 배포 시에는 advisory lock으로 동일 감시 설정의 중복 스캔을 막는다. `SPDM_ALLOWED_ROOTS` 환경변수에 등록된 canonical 루트 아래 경로만 감시 설정으로 저장할 수 있게 한다.

### 3.4 API 계약

기존 API 호환성을 유지하면서 프로젝트 범위 API를 추가한다.

```text
GET  /api/workbench/task-types?project_id={project_id}
GET  /api/workbench/request-types?project_id={project_id}
POST /api/projects/{project_id}/workbench/task-types
POST /api/projects/{project_id}/workbench/request-types

POST /api/projects/{project_id}/requests
POST /api/requests/{request_id}/accept
GET  /api/projects/{project_id}/requests/pending-acceptance

GET  /api/projects/{project_id}/spdm-watch-configs
POST /api/projects/{project_id}/spdm-watch-configs
PUT  /api/projects/{project_id}/spdm-watch-configs/{config_id}
POST /api/projects/{project_id}/spdm-watch-configs/{config_id}/scan
GET  /api/projects/{project_id}/spdm-detections
POST /api/requests/{request_id}/assign-work-plan
GET  /api/requests/{request_id}/spdm-changes
POST /api/requests/{request_id}/spdm-changes/{change_id}/acknowledge
```

주요 요청/응답 규칙은 다음과 같다.

- `POST /projects/{project_id}/requests`의 `request_type_id`는 하드코딩된 두 Literal이 아니라 문자열 ID와 version을 받는다.
- 수기 의뢰 생성은 `source_type='DEPARTMENT_HEAD'`만 허용하고 현재 사용자를 지시자로 기록한다. 클라이언트가 보낸 `requested_by`/`assigned_by` 문자열은 신뢰하지 않는다.
- SPDM 감지 의뢰의 `assign-work-plan`은 활성 업무 유형, 프로젝트 범위, 활성 담당자 멤버십을 검증한 뒤 배정·작업계획을 원자적으로 생성한다.
- `accept`는 지정된 `owner_user_id` 또는 `work.execute_any` 권한만 호출할 수 있으며 멱등적으로 동작한다.
- 작업 시작 API는 의뢰가 `READY` 또는 `IN_PROGRESS`이고 수령 이력이 있을 때만 허용한다.
- 목록과 상세 응답에 `lifecycle_status`, `accepted_at`, `source`, `unacknowledged_spdm_change_count`를 포함한다.
- OpenAPI와 생성된 프런트엔드 타입/클라이언트를 함께 갱신한다.

### 3.5 권한

- `project.work_type.manage` 권한을 추가하고 프로젝트 `admin`과 전역 관리자에게 부여한다.
- `작업 유형 관리` 메뉴를 system context에서 project context로 변경한다. 프로젝트 관리자는 선택 프로젝트의 Custom 유형만 관리한다.
- 시스템 공통 유형의 생성·비활성화는 기존 `system.catalog.manage`를 가진 전역 관리자만 가능하다.
- SPDM 감시 설정·수동 재감지·감지 의뢰 배정은 `project.work_type.manage`가 필요하다.
- 의뢰 발생/배정은 `request.create`, 담당자 수령은 배정자 본인 또는 `work.execute_any`, 변경 확인은 프로젝트 멤버 중 의뢰 열람 권한 보유자에게 허용한다.
- 모든 유형 생성, SPDM 설정 변경, 수동 스캔, 의뢰 배정·수령, 변경 확인을 감사로그에 남긴다.

## 4. 화면 변경

### 4.1 작업 유형 관리

`작업 유형 관리`를 선택 프로젝트 기준의 세 탭으로 구성한다.

1. `세부 작업`
   - 프로젝트 Custom 세부 작업 목록과 활성 버전을 표시한다.
   - 이름, 수행 설명, 활성 여부를 입력해 새 세부 작업 또는 새 불변 버전을 저장한다.
   - 시스템 공통 세부 작업은 읽기 전용 배지로 구분한다.
2. `업무 유형`
   - 시스템 공통 및 프로젝트 Custom 세부 작업을 선택해 순차/병렬로 구성한다.
   - 이름, 설명, 기본 실행 방식과 작업 순서를 미리 보고 불변 버전으로 저장한다.
   - 기존 의뢰에 사용된 버전은 수정하지 않고 새 버전을 만든다는 안내를 표시한다.
3. `SPDM 감시`
   - 허용 루트 내 감시 경로, 폴더 스키마 레벨, 제외 패턴, 주기, 활성 여부를 관리한다.
   - 최근 성공/실패 시각과 오류를 표시하고 `지금 재감지` 버튼을 제공한다.

기존 `배치 경로 정의`는 별도 네 번째 탭으로 유지한다.

### 4.2 의뢰 접수

기존 두 개 고정 시나리오 필터와 TypeScript Literal을 제거하고 다음 세 영역으로 개편한다.

1. `신규 업무 배정`
   - 프로젝트의 활성 시스템/Custom 업무 유형 전체를 표시한다.
   - 업무 유형을 선택하면 버전, 설명, 세부 작업 순서/병렬 관계를 미리 보여 준다.
   - 부서장 지시, 담당자, 기한, 제목, 지시 내용을 입력해 `PENDING_ACCEPTANCE` 의뢰를 만든다.
2. `SPDM 감지 대기`
   - `OCCURRED` 의뢰의 폴더명, 상대 경로, 감지 시각, 변경 요약을 표시한다.
   - 업무 유형과 담당자를 지정해 접수 대기로 넘긴다.
   - 중복/오탐은 사유와 함께 제외 처리하며 원본 폴더를 삭제하지 않는다.
3. `내 접수 대기`
   - 현재 사용자가 담당자인 `PENDING_ACCEPTANCE` 의뢰를 표시한다.
   - 출처, 지시 내용, 업무 유형, 작업 미리보기를 확인한 뒤 `의뢰 수령`을 실행한다.

저장 성공 문구는 기존의 즉시 `READY` 표시 대신 `담당자 수령 대기`를 표시한다.

### 4.3 실행·현황 화면

- `해석 작업 실행`은 `PENDING_ACCEPTANCE` 의뢰의 작업 시작 버튼을 비활성화하고 수령 안내를 표시한다.
- Custom 세부 작업 상세에 관리자가 정의한 수행 설명을 표시한다.
- 의뢰 목록, 의뢰 진행 상태, 운영 대시보드는 공통 monitoring projection의 동일 상태와 진행률을 사용한다.
- SPDM 변경이 있으면 의뢰 카드와 상세에 미확인 개수 배지를 표시하고, 변경 경로 요약과 감지 시각을 본 뒤 `변경 확인`할 수 있게 한다.
- 변경 확인은 알림만 해제하며 업무 상태와 진행률을 바꾸지 않는다.

## 5. 마이그레이션과 구현 순서

1. 새 Alembic migration에 카탈로그 scope, 접수 이력, SPDM 감시/변경 테이블과 인덱스·제약을 추가하고 `schema.sql` 및 로컬 초기화 DDL을 동기화한다.
2. 기존 업무/세부 작업을 `SYSTEM` 범위로 backfill하고, 기존 의뢰의 출처·수령 상태를 역산한다. 기존 URL과 시스템 관리 API는 유지한다.
3. Repository와 schema에서 프로젝트 범위 조회·생성, Custom kind, 동적 업무 유형 ID를 지원한다.
4. 의뢰 생성·SPDM 배정·수령 상태 전이와 DAG 기반 작업 활성화 로직을 구현하고 monitoring projection을 확장한다.
5. SPDM 스캔 서비스, 시작/종료 lifecycle, 수동 스캔, fingerprint 및 변경 확인 API를 구현한다.
6. 작업 유형 관리와 의뢰 접수 화면을 개편하고 실행·현황 화면에 수령 및 변경 배지를 연결한다.
7. OpenAPI를 내보내고 프런트엔드 생성 타입을 갱신한 뒤 기존 테스트와 신규 통합/E2E 테스트를 통과시킨다.

현재 작업 트리에 다른 미커밋 변경이 많으므로 구현 시 관련 파일의 사용자 변경을 기준선으로 삼고, migration revision 충돌과 접근제어 변경을 먼저 재확인한다.

## 6. 테스트 및 인수 기준

### 6.1 서버/데이터 테스트

- 프로젝트 관리자는 자기 프로젝트의 Custom 세부 작업과 업무 유형 버전을 생성할 수 있고 다른 프로젝트에는 생성할 수 없다.
- 일반/파워 사용자는 관리 API가 403이며, 현재 프로젝트의 활성 유형만 의뢰 접수에서 조회한다.
- 기존 시스템 업무 유형 두 개는 마이그레이션 후에도 모든 프로젝트에서 선택 가능하다.
- 새 Custom 업무 유형으로 수기 의뢰를 만들면 한 트랜잭션에 의뢰·배정·snapshot·work item이 생성되고 상태는 `PENDING_ACCEPTANCE`다.
- 담당자 외 사용자는 수령할 수 없고, 담당자가 수령하면 선행조건이 없는 작업만 `READY`가 된다.
- 순차/병렬 dependency에 따라 다음 작업이 정확히 활성화되며 모든 필수 작업 완료 시 의뢰가 `COMPLETED`가 된다.
- 이미 생성된 의뢰는 업무 유형의 새 버전을 저장해도 원래 snapshot과 작업명이 바뀌지 않는다.
- 동일 SPDM 폴더 반복 스캔은 의뢰를 중복 생성하지 않는다.
- 신규 의뢰 폴더는 `OCCURRED`가 되고 관리자가 유형·담당자를 배정하면 `PENDING_ACCEPTANCE`가 된다.
- 폴더 구조 변경은 동일 의뢰의 change event만 생성하며 의뢰 상태를 바꾸지 않는다.
- 일시적 접근 오류는 삭제 변경으로 오판하지 않고 `last_error`에 남는다.
- 변경 확인은 사용자·시각을 기록하며 반복 호출은 멱등적이다.
- 감시 허용 루트 밖 경로, symlink 탈출, 다른 프로젝트 ID 변조는 거부된다.

### 6.2 프런트엔드/E2E 테스트

- 프로젝트 관리자가 Custom 세부 작업을 만든 뒤 이를 조합한 업무 유형을 저장한다.
- 새 업무 유형이 페이지 재접속 후에도 같은 프로젝트의 의뢰 접수 선택지에 나타나고 다른 프로젝트에는 나타나지 않는다.
- 수기 의뢰 등록 성공 메시지가 `담당자 수령 대기`를 표시하고, 담당자의 수령 후에만 작업 시작이 가능하다.
- SPDM 감지 대기 항목에 유형과 담당자를 배정하고 담당자가 수령해 수행 화면으로 진입한다.
- 폴더 변경 배지가 의뢰 현황과 상세에 동일하게 표시되고 확인 후 두 화면에서 사라진다.
- 기존 DOE/신뢰성 시나리오 접수, 작업 시작·진행·완료, 운영 대시보드 동기화 회귀 테스트가 계속 통과한다.

### 6.3 완료 기준

- 프로젝트 관리자가 코드 수정 없이 Custom 세부 작업과 업무 유형을 정의할 수 있다.
- 같은 프로젝트의 의뢰 접수에서 방금 저장한 활성 업무 유형을 선택할 수 있다.
- SPDM 신규 폴더가 자동 또는 수동 스캔으로 한 번만 의뢰 발생으로 잡힌다.
- 수기/SPDM 의뢰 모두 담당자 수령 전에는 수행을 시작할 수 없다.
- 수행자는 각 Custom 세부 작업의 설명을 보고 시작·진행·완료할 수 있다.
- SPDM 변경은 동일 의뢰에 추적되고 확인 이력이 남으며 기존 진행 상태를 훼손하지 않는다.
- API 권한, 감사로그, OpenAPI, migration, 백엔드 테스트와 핵심 E2E가 모두 검증된다.

## 7. 범위 제외와 기본 가정

- 별도의 회사 조직/부서 엔터티와 HR 부서장 직책 연동은 만들지 않고 현재 프로젝트와 프로젝트 관리자 역할을 부서/부서장으로 사용한다.
- 이메일, Teams, Slack 등 외부 알림은 제외하고 앱 내 배지와 확인 이력만 제공한다.
- SPDM API 연동이나 파일 업로드는 제외하며 서버에서 접근 가능한 공유폴더의 메타데이터만 스캔한다.
- 파일 내용 hash, 파일 본문 저장, 결과 자동 import는 제외한다.
- Custom 세부 작업 체크리스트, 승인 단계, 반려/취소/재오픈 상태는 후속 범위로 둔다.
- SPDM 변경은 자동 재접수 또는 신규 의뢰 생성을 유발하지 않는다.
- 자동 감지 기본 주기는 60초로 하고 설정별로 30초 이상에서 변경 가능하게 한다.
