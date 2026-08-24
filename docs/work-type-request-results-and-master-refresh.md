# 작업 유형 요청 결과와 마스터 폴더 Refresh 설계

- 작성일: 2026-08-20
- 상태: 구현 기준
- 대상: 작업 유형 관리, 의뢰 결과 계약, 결과 폴더 수집, 상세 분석 갱신
- 관련 문서:
  - `docs/work-definition-result-widget-ui-design.md`
  - `docs/result-import-pipeline-spec.md`
  - `docs/work-type-request-intake-spdm-plan.md`
  - `docs/storage-folder-and-file-contract.md`

## 1. 결정

작업 유형 작성자는 하나의 화면에서 다음 세 가지를 함께 정의한다.

1. **유형 작성**: 이름, 설명, 추천 조건, 라벨
2. **수행 작업**: 수행할 작업과 순서·의존 관계
3. **요청 결과**: 수행 후 생성되어야 할 결과와 이를 표시할 상세 분석 위젯

시스템 작업 유형 작성 과정에서 사용자가 분석 템플릿을 별도로 만들거나 게시하지 않는다. 요청 결과 영역에서 위젯을 태그처럼 추가하면 서버가 내부 `DashboardDefinition`과 게시 버전을 자동 생성하고 작업 유형 버전에 연결한다.

프로젝트별 결과 화면 override와 기존 API 기반 분석 템플릿은 호환 기능으로 유지한다. 다만 시스템 작업 유형의 기본 작성 흐름에서는 노출하지 않는다.

```text
작업 유형 정의
  ├─ 유형 작성
  ├─ 수행 작업
  └─ 요청 결과 위젯 태그
        ↓ 작업 유형 버전 저장
내부 DashboardDefinition + 결과 프로필 자동 생성
        ↓ 의뢰 생성
작업계획 + 결과 레이아웃 snapshot 고정
        ↓ 수행자가 의뢰 폴더에 결과 생성
마스터 폴더 Refresh
        ↓
탐색 → 검증 → bundle fingerprint 중복 판정
→ normalized command → 공통 single-connection UoW 적재
→ 상태 동기화
        ↓
상세 분석 위젯 WAITING/PARTIAL/READY 갱신
```

## 2. 요청 결과의 의미

요청 결과 위젯은 단순한 화면 장식이 아니다. 하나의 위젯 선언은 다음 계약을 함께 가진다.

- 상세 분석에 표시할 시각화 유형
- 결과 데이터의 `variable_key`
- 필요한 데이터 계약
- 수행 완료 판정에 사용할 필수 여부
- 결과가 없는 동안 표시할 대기 상태
- 의뢰 생성 시 고정할 레이아웃 정보

예시:

```json
{
  "id": "max-stress",
  "type": "kpi",
  "title": "최대 응력",
  "variable_key": "max_stress",
  "data_contracts": ["SCALAR_RESULT"],
  "required": true
}
```

작업 유형 편집 화면은 KPI, 판정, 요약, 결과표, 시계열, Contour, 이미지·영상 등의 카탈로그를 태그 버튼으로 제공한다. 선택된 항목은 제목, 변수 키, 필수 여부를 편집하고 제거할 수 있어야 한다.

## 3. 외부 계약과 내부 저장 분리

### 3.1 작업 유형 입력 계약

작업 유형 생성·새 버전 API는 사용자 중심의 `result_definition`을 입력받는다.

```json
{
  "display_name": "낙하 신뢰성 검증",
  "allowed_task_types": [],
  "default_workflow": { "nodes": [] },
  "result_definition": {
    "page_name": "낙하 결과 요약",
    "page_description": "작업 유형에서 선언한 기대 결과",
    "widgets": []
  }
}
```

`result_definition`은 기존 `result_profile`과 동시에 사용할 수 없다. 기존 클라이언트를 위해 `result_profile` 입력은 유지한다.

### 3.2 내부 컴파일

독립적인 결과 정의 컴파일러가 다음 작업만 담당한다.

1. 위젯 ID와 데이터 계약 정규화
2. 위젯 타입별 기본 크기 적용
3. 12열 레이아웃에 결정적으로 배치
4. `variable_key`, `data_contracts`, `required`를 `settings`로 변환
5. 유효한 단일 페이지 `DashboardDefinition` 생성

라우터나 React 컴포넌트에서 레이아웃 좌표를 직접 계산하지 않는다.

### 3.3 원자적 버전 저장

작업 유형 저장 트랜잭션은 다음을 함께 처리한다.

- 작업 유형 불변 버전 생성
- 내부 `PUBLISHED` 시스템 분석 템플릿 버전 생성
- 모든 선택 위젯을 포함한 시스템 기본 결과 프로필 생성

검증 또는 저장이 실패하면 세 항목 모두 롤백한다. 작업 유형 새 버전을 만들면 내부 템플릿과 결과 프로필도 새 불변 버전으로 만든다. 기존 의뢰 snapshot에는 영향을 주지 않는다.

## 4. 데이터 계약과 대기 상태

요청 결과 위젯은 앞으로 필요한 결과 화면을 선언한다. 선택한 수행 작업의 현재 산출물과 위젯 요구 데이터가 일치하지 않아도 작업 유형 저장을 막지 않는다. 결과는 마스터 폴더 Refresh 등 후속 적재 경로에서 연결될 수 있기 때문이다.

| 요청 결과 계약 | 업로드 후 충족 기준 |
|---|---|
| `LOAD_CASE` | 의뢰에 하중 경우가 생성되거나 매핑됨 |
| `RESULT_RUN` | 최신 분석 Run이 적재됨 |
| `SCALAR_RESULT` | 최신 Run에 수치 결과가 적재됨 |
| `TIME_SERIES` | 최신 Run에 시계열 결과가 적재됨 |
| `CURVE` | 최신 Run에 곡선 결과가 적재됨 |
| `MEDIA_ASSET` | 최신 Run에 이미지·영상 등 미디어가 적재됨 |

지원하지 않는 계약명, 중복 위젯 ID, 필수 위젯 누락 같은 구조 오류만 저장을 막는다. 아직 적재되지 않은 계약은 해당 위젯에 `결과 대기`로 표시하고, 결과가 업로드되면 같은 snapshot 레이아웃에서 자동 갱신한다.

## 5. 의뢰와 결과 snapshot

의뢰 생성 시 다음을 snapshot으로 저장한다.

- 작업 유형 ID와 버전
- 수행 작업과 의존 관계
- 내부 결과 템플릿 ID와 버전
- 페이지와 위젯 정의
- 위젯 변수 키, 데이터 계약, 필수 여부

이후 작업 유형이나 내부 템플릿이 바뀌어도 기존 의뢰의 화면은 바뀌지 않는다. 결과 데이터만 동일 snapshot의 위젯에 연결되며 상태가 갱신된다.

### 5.1 사용자 결과 화면 확장

작업 유형의 결과 위젯은 모든 의뢰에 제공되는 최소 초기 레이아웃이다. 상세 분석에는 결과 레이아웃 종류와 관계없이 `보고서 내보내기`, `자연어로 개선`, `대시보드 편집` 진입점을 제공한다.

- 보고서는 현재 snapshot 레이아웃과 업로드된 Run을 직접 사용한다.
- 자연어 개선 또는 편집을 처음 시작하면 `POST /api/workbench/requests/{request_id}/result-layout/materialize`가 snapshot 페이지를 사용자용 `custom/published` 대시보드 v1으로 한 번만 복제한다. 따라서 저장 후 새로고침해도 상세 분석 페이지 목록에 유지된다.
- snapshot이 없는 레거시 `UNCONFIGURED` 의뢰도 자연어 개선 또는 편집에 처음 진입하면 빈 `custom/published` 사용자 대시보드를 생성하므로, 이후 위젯을 추가해 확장할 수 있다.
- 재호출은 동일 대시보드를 반환하며 snapshot 원본은 변경하지 않는다.
- 이후 위젯 추가, 이동, 크기 변경, 설정, 자연어 변경안과 버전 저장은 기존 대시보드 편집 기능을 사용한다.
- 편집 기능은 기존 `DASHBOARD_EDIT` 권한을 따르고, 결과 Run이 없을 때 보고서 버튼은 보이되 내보내기는 대기한다.

## 6. 마스터 폴더 Refresh

### 6.1 신뢰 경계

마스터 결과 루트는 서버 설정 `SIMDASH_IMPORT_ROOT`로만 지정한다. 클라이언트는 절대경로나 임의 서버 경로를 전달할 수 없다.

```text
{SIMDASH_IMPORT_ROOT}/
└─ {project_id}/
   └─ {request_id}/
      └─ {load_case_id}/
         └─ {run_id}/
            ├─ manifest.json
            ├─ scalar_results.csv
            ├─ time_history.csv
            └─ media/
```

폴더명은 탐색 보조 정보이며 실제 DB 연결은 검증된 `manifest.json`의 ID를 사용한다. root 탈출, 심볼릭 링크 탈출, 존재하지 않는 프로젝트·의뢰·하중 경우 조합은 거부한다.

실행 가능한 기준 예제는 `examples/master-results/`에 있다. 현재 구현은 root 아래의 manifest를 재귀적으로 찾고 context ID를 정본으로 사용하므로, 폴더명의 ID 일치 강제는 별도 개선 항목이다. 확장자·MIME·DB blob 저장의 현재 계약은 `docs/storage-folder-and-file-contract.md`를 따른다.

### 6.2 Refresh 동작

`POST /api/result-imports/refresh`는 다음을 수행한다.

1. root 아래 `manifest.json` 탐색
2. manifest별 독립 검증과 수집 작업 생성
3. manifest와 mapping 파일의 크기·SHA-256으로 계산한 bundle fingerprint와 완료 이력으로 중복 판정
4. normalized command를 공통 single-connection UoW에 전달해 신규 결과를 Run, scalar, time-series, curve, media 저장소에 원자적으로 적재
5. Run 및 하중 경우 상태 갱신
6. 가능한 경우 의뢰·수행 작업 상태 동기화
7. manifest별 `IMPORTED`, `SKIPPED`, `FAILED` 결과 반환

하나의 manifest 실패가 전체 Refresh를 중단시키지 않는다. 동일한 완료 bundle을
다시 Refresh하면 DB 중복 없이 `SKIPPED`되어야 한다. 동일 manifest라도 참조
mapping 파일이 변경되면 fingerprint가 달라지므로 새 Run을 생성한다. manifest
checksum은 fingerprint와 별도로 metadata/job summary에 보존한다.

PostgreSQL에서는 load case별 namespaced 64-bit transaction advisory lock으로
`run_no` 할당을 직렬화하고, non-null exact
`(source_type, source_name, source_checksum)`를 migration `0016`의
`canonical_result_ingestion_sources` PK로 예약한다. 실패 transaction은 claim도
rollback한다. 현재는 SQL/migration contract 검증까지이며 live PostgreSQL
concurrent ingestion test는 없다.

### 6.3 결과 화면 갱신

상세 분석 화면은 저장된 snapshot을 유지하고 result-layout binding만 다시 조회한다.

- 필요한 데이터 없음: `WAITING` 또는 `EMPTY`
- 일부 필수 결과만 존재: `PARTIAL`
- 모든 필수 계약 충족: `READY`
- 수집 실패: `ERROR` 또는 `FAILED`

화면은 폴더명이나 `analysis_type`을 보고 위젯을 추론하지 않는다.

## 7. 모듈 경계

유지보수성을 위해 다음 책임을 분리한다.

### Backend

```text
schemas/workbench.py
  └─ 작업 유형·요청 결과 입력 검증

services/request_result_definition.py
  └─ 위젯 태그를 DashboardDefinition으로 컴파일

repositories/workbench.py
  └─ 작업 유형·내부 템플릿·프로필 원자적 저장

schemas/result_folder_refresh.py
  └─ Refresh 응답 계약

services/master_result_refresh.py
  └─ root 탐색, fingerprint·command 생성, manifest별 실패 격리

application/results/commands.py
  └─ ResultIngestionCommand와 공통 ingestion orchestration

domains/results/{models,ports}.py
  └─ DB 독립 command/outcome와 ResultIngestionUnitOfWork port

adapters/persistence/result_ingestion.py
  └─ DuckDB/PostgreSQL single-connection SQL UoW

routers/result_folder_refresh.py
  └─ 권한, 감사, HTTP 변환
```

파서, 저장소, 상태 동기화 코드는 Refresh 서비스에 복제하지 않고 공통 UoW와
normalized contract를 사용한다. 일반 수동 `SUMMARY_RESULT` JSON/CSV upload도
target-qualified source와 content checksum으로 같은 UoW를 사용하며, 재시도는
`SKIPPED`, 권한 재확인과 `RESULT_IMPORTED` audit는 write transaction 안에서
원자적으로 처리한다. `Radioss` mesh CSV는 `result_locations` 저장이 canonical
UoW에 포함될 때까지 direct-SQL compatibility 경로다.

구형 `result_files` 기반 `ResultImportService` persistence는 현재 schema에 없는
`result_import_jobs`와 `analysis_runs` 확장 컬럼을 참조하므로 parser/manifest
호환을 위한 비운영 compatibility 경로다.

### Frontend

```text
features/workbench/requestResultWidgetCatalog.ts
  └─ 위젯 태그 카탈로그와 기본 계약

features/workbench/RequestResultWidgetConfiguration.tsx
  └─ 태그 추가·제거·설정 UI

features/workbench/SimulationWorkbench.tsx
  └─ 작업 유형 작성 상태와 저장 오케스트레이션
```

프로젝트 override용 `ResultProfileConfiguration`은 별도 기능으로 유지한다.

## 8. 권한과 감사

- 시스템 작업 유형 작성: `system.catalog.manage`
- 프로젝트 override: `dashboard.edit`
- 전체 마스터 root Refresh: 전역 관리자
- typed folder example 적재: `result.import`를 요청 경계와 write UoW transaction 안에서 재확인
- manual SUMMARY_RESULT JSON/CSV: 동일 `result.import` 권한을 write transaction 안에서 재확인
- 개별 결과 데이터 조회: 기존 `project.data.view`

최소 감사 이벤트:

- 작업 유형 결과 정의 버전 생성
- 마스터 폴더 Refresh 실행과 집계
- manifest별 수집 성공·실패

Refresh 감사 이벤트에는 파일 내용·서버 경로를 넣지 않고 전체
scanned/imported/skipped/failed 집계만 기록한다. manifest별 상대 경로·상태와
manifest checksum·bundle fingerprint는 `folder_import_jobs` 요약과
`analysis_run_metadata`에 보존한다. manual SUMMARY_RESULT 성공은
`RESULT_IMPORTED` audit event를 결과 write와 같은 transaction에 기록하며,
재시도 `SKIPPED`에는 새 Run audit를 만들지 않는다.

## 9. 호환성과 마이그레이션

- 기존 `result_profile` 기반 작업 유형과 의뢰 snapshot은 그대로 읽는다.
- 기존 프로젝트별 템플릿 override는 유지한다.
- 결과 구성이 없는 레거시 의뢰는 중립 `UNCONFIGURED` 화면을 사용한다.
- 기존 템플릿 관리 API는 관리·호환 경로로 남기되 시스템 작업 유형 기본 UI에서는 요구하지 않는다.
- 저장된 결과 템플릿을 작업 유형 편집 화면에서 열면 위젯 `settings`를 태그 초안으로 역변환한다.

## 10. 완료 조건

### 작업 유형

- 별도 템플릿 없이 요청 결과 위젯을 추가하고 작업 유형을 저장할 수 있다.
- 새 작업 유형과 새 버전 모두 내부 템플릿·결과 프로필을 원자적으로 만든다.
- Workflow 산출물과 무관하게 필요한 결과 위젯 구성을 저장할 수 있다.
- 아직 업로드되지 않은 결과는 해당 위젯만 `결과 대기`로 표시한다.
- 기존 `result_profile` API 사용은 회귀하지 않는다.

### Refresh

- 설정된 root에서 신규 manifest를 찾아 결과를 적재한다.
- 두 번째 동일 Refresh는 중복 없이 skip한다.
- 정상 manifest와 오류 manifest가 함께 있을 때 정상 건은 성공한다.
- root 밖 파일과 임의 절대경로를 처리하지 않는다.
- 적재 후 기존 상세 분석 API에서 새 Run과 결과를 조회할 수 있다.

### Frontend

- 작업 유형 관리의 세 번째 영역이 템플릿 선택이 아닌 위젯 태그 UI다.
- 태그 추가·제거·필수 설정과 변수 키 편집이 가능하다.
- 저장 후 다시 편집해도 같은 태그 구성이 복원된다.
- 의뢰 접수 미리보기와 상세 분석 snapshot 렌더링은 유지된다.
- 모든 상세 분석에서 보고서·자연어 개선·대시보드 편집 진입점을 제공한다.
- 사용자 편집본은 snapshot과 분리된 custom dashboard 버전으로 저장한다.
