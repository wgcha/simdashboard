# 계약: 깊이 기반 폴더 역할 스키마 (DEPTH_V1)

- 상태: 확정 (2026-10-02)
- 대체 대상: 폴더별 역할 지정 + `match_mode: glob|contains|level` 규칙 (`docs/dashboard/folder-environment-guide.md`의 규칙 편집 부분)
- 기준 커밋: `da5734b` (claude/case-results-redesign)

## 1. 목적

관리자는 폴더 하나하나에 역할을 지정하지 않는다. **Root로부터의 깊이별로 역할을 1회 정의**하고, 같은 깊이의 모든 폴더가 그 역할을 갖는다. 의뢰 폴더 이하는 환경(사용/유통)별 스키마를 따로 쓴다.

## 2. 결정 사항

| # | 결정 |
|---|---|
| D1 | 상위 구간(Root→…→프로젝트→의뢰)의 깊이는 관리자가 지정한다. 전역 1개 |
| D2 | 하위 구간은 표준 깊이를 강제한다. Working 필수, 유통 RunOption 레벨 필수. 이탈하면 "확인 필요" |
| D3 | 스키마는 환경별 전역 1개. 프로젝트별 재정의 없음 |
| D4 | 환경은 의뢰 폴더명 부분일치로 정한다. `사용` → USAGE, `유통` → DISTRIBUTION. 둘 다 있거나 둘 다 없으면 "확인 필요" |
| D5 | 구형 `WR_x_SimType1|2` 규칙은 삭제한다 |
| D6 | 정의된 마지막 깊이보다 깊은 폴더는 역할 없는 내용물(CONTENT)로 허용한다 |
| D7 | 가지가 중간 깊이에서 끝나도 정보 표시만 하고(`BRANCH_INCOMPLETE`), 등록은 허용한다 |
| D8 | 사용환경의 `EVALUATION` 역할을 `SCENE`으로 통합한다. 두 환경 모두 Scene을 쓴다 |
| D9 | 스키마를 저장해도 기존 등록 의뢰는 변경하지 않는다. 의뢰 화면의 **재해석** 버튼으로만 새 스키마를 적용한다 |
| D10 | 하위 L1은 **이름으로** 구분한다. `Working`(필수)과 `Final`(선택, Final 지정 후 생성). `Final`도 스키마 대상이다 |
| D11 | Final 하위 L2는 이름으로 구분한다. `CAE` = 해석 입력·결과 파일, `Reports` = 보고서 기능이 만든 PPTX/PDF. 앱의 SPDM 쓰기는 이 두 폴더로만 한다 |
| D12 | Final/CAE·Reports의 L3 이하는 고정 구조다: `<Case>/<finalization_id>/` 아래에 Working의 Case 이하 구조를 그대로 미러링한다. 관리자 편집 대상이 아니며 UI에 읽기 전용으로 표시한다 |

## 3. 표준 트리

```
Root                                   L0 (역할 없음)
└─ <Project>                           상위 L1 PROJECT      예: 75R9J_PV
   └─ <Request>                        상위 L2 REQUEST      예: [WR-0002]_[유통_환경]
      ├─ Final                         하위 L1 FINAL (이름으로 판정, 선택)
      │  ├─ .finalizations/…            앱 메타(plan.json, complete.json, .request.lock) — 무시
      │  ├─ CAE                         L2 FINAL_CAE      해석 입력·결과 파일
      │  │  └─ <Case>                   L3 SIMULATION_CASE
      │  │     └─ <finalization_id>     L4 FINAL_VERSION  32자리 hex
      │  │        └─ …                  L5~ = Working의 Case 이하 구조 미러 (사용: Scene / 유통: LoadCase/Run/RunOption/Scene)
      │  ├─ Reports                     L2 FINAL_REPORTS  보고서 PPTX/PDF
      │  └─ CAD                         L2 FINAL_CAD      하위는 CONTENT (앱 쓰기 없음) — 미확정, §12
      │     └─ <Case>/<finalization_id>/…  CAE와 같은 구조
      └─ Working                       하위 L1 WORKING (이름으로 판정, 필수)
         사용:
         └─ <Case>                     L2 SIMULATION_CASE   예: Assy_RES_Model_SetCase1_StandCase1_Inner
            └─ <Scene>                 L3 SCENE             예: Settle, Wobble, Slope_Angle …
               └─ (파일·하위폴더)       CONTENT
         유통:
         └─ <Case>                     L2 SIMULATION_CASE   예: Package_Model_SetCase3_CushionCase3_조건표시
            └─ <LoadCase>              L3 LOAD_CASE         예: Drop
               └─ <Run>                L4 EXECUTION_RUN     예: 85qn80h_ref_organized
                  └─ <RunOption>       L5 RUN_OPTION        INDIVIDUAL | CUMULATIVE
                     └─ <Scene>        L6 SCENE             예: 2_Face
                        └─ …           CONTENT
```

상위 구간 기본값은 `L1 PROJECT / L2 REQUEST`이다. 중간 폴더가 있는 구조라면 관리자가 `CONTAINER` 레벨을 끼워 넣는다(예: `L1 CONTAINER / L2 PROJECT / L3 REQUEST`).

## 4. 데이터 계약

### 4.1 저장

- 기존 `folder_environment_profiles` 테이블을 쓴다. 새 테이블은 만들지 않는다.
- 저장 1회 = 같은 `schema_set_id`를 가진 USAGE 행과 DISTRIBUTION 행 **2개를 새로 INSERT**한다(id 신규, `revision=1`). 이전 세트는 `profile_metadata.archived=true`, `superseded_by=<새 schema_set_id>`로 표시한다.
  - 같은 행을 수정하지 않는 이유: 기존 스캔이 `profile_id`와 `profile_revision`을 고정 참조한다. revision을 올리면 기존 의뢰 전체가 `FOLDER_SCHEMA_PROFILE_REVISION_CHANGED`로 막힌다.
- 상위 구간은 두 행에 같은 내용을 복사한다.
- `ROLE_RULES_REVISION`은 올리지 않는다(D9).

### 4.2 `rules_json` (format `DEPTH_V1`)

```json
{
  "format": "DEPTH_V1",
  "schema_set_id": "dss-<uuid>",
  "upper": { "levels": [
    { "level": 1, "role": "PROJECT" },
    { "level": 2, "role": "REQUEST" }
  ]},
  "environment_keyword": "유통",
  "lower": {
    "levels": [
      { "level": 1, "role": "WORKING", "fixed_name": "Working" },
      { "level": 2, "role": "SIMULATION_CASE" },
      { "level": 3, "role": "LOAD_CASE" },
      { "level": 4, "role": "EXECUTION_RUN" },
      { "level": 5, "role": "RUN_OPTION", "allowed_names": ["INDIVIDUAL", "CUMULATIVE"] },
      { "level": 6, "role": "SCENE" }
    ],
    "below_last": "CONTENT"
  },
  "final": {
    "fixed": true,
    "children": [
      { "name": "CAE", "role": "FINAL_CAE" },
      { "name": "Reports", "role": "FINAL_REPORTS" },
      { "name": "CAD", "role": "FINAL_CAD", "below": "CONTENT" }
    ],
    "ignored": [".finalizations"],
    "below_child": ["SIMULATION_CASE", "FINAL_VERSION", "MIRROR_WORKING_FROM_LEVEL_3"]
  },
  "usage_sources": null
}
```

USAGE 행은 `environment_keyword: "사용"`, `lower.levels = [WORKING(fixed), SIMULATION_CASE, SCENE]`이다. `usage_sources`는 기존 USAGE 기본 프로필에서 복사한다.

### 4.3 검증 규칙 (저장 시 422)

| 대상 | 규칙 |
|---|---|
| upper | level은 1부터 연속, 최대 8. `PROJECT`와 `REQUEST`가 정확히 1개씩, PROJECT < REQUEST. REQUEST는 마지막 레벨. 나머지는 `CONTAINER` |
| final | `fixed: true`. 서버 상수이며 PUT으로 변경할 수 없다(요청에 포함되면 무시) |
| lower 공통 | L1 = `WORKING`(fixed_name). L2 = `SIMULATION_CASE`. 마지막은 `SCENE`. `CONTAINER`는 중간 통과 레벨로만 허용 |
| DISTRIBUTION | `RUN_OPTION` 필수, `allowed_names`는 비어 있으면 안 됨 |
| 역할 집합 | USAGE: PROJECT, REQUEST, WORKING, FINAL, FINAL_CAE, FINAL_REPORTS, FINAL_CAD, FINAL_VERSION, SIMULATION_CASE, SCENE, CONTAINER. DISTRIBUTION: 여기에 LOAD_CASE, EXECUTION_RUN, RUN_OPTION 추가. `EVALUATION`, `RESULTS`, `INPUT`은 새 스키마에서 사용 불가 |
| keyword | USAGE는 `사용`, DISTRIBUTION은 `유통`으로 고정(편집 불가) |

### 4.4 마이그레이션 `0034_folder_depth_schema`

1. 기존 활성 프로필을 전부 archive한다. 행은 삭제하지 않는다(기존 스캔 FK와 refresh 유지).
2. 위 기본 세트를 seed한다. USAGE의 `usage_sources`를 복사하되, `metric_paths`와 선택 규칙에 남아 있는 `EVALUATION` 참조는 `SCENE`으로 바꾼다(D8).
3. downgrade는 지원하지 않는다.

## 5. 해석 알고리즘

순수 함수 `resolve_path(parts: list[str], schema) -> NodeRole`을 `environment_folder_profiles.py`에 둔다.

1. **무시 대상**: 이름이 `.`, `$`, `~`로 시작하는 폴더, reparse point/symlink, 파일. 깊이 집계와 샘플에서도 뺀다.
2. **상위 구간**: d ≤ request_level이면 `upper[d].role`이다. project_level보다 얕은 곳에서 끝난 가지는 무시한다.
3. **환경 판정**: request_level 폴더 이름에서 키워드가 하나만 있으면 그 환경이다. 둘 다면 `ENV_KEYWORD_BOTH`, 없으면 `ENV_KEYWORD_NONE`이고, 이때 하위 전체를 UNRESOLVED로 두고 탐색하지 않는다.
4. **하위 구간**: k = d − request_level일 때
   - k=1은 **이름으로** 판정(casefold): `Working` → WORKING, `Final` → FINAL. 그 외 이름은 `UNEXPECTED_REQUEST_CHILD`
   - Final 가지 (D11, D12)
     - k=2: `CAE` → FINAL_CAE, `Reports` → FINAL_REPORTS, `CAD` → FINAL_CAD(이하 CONTENT), `.finalizations` → 무시. 그 외 이름은 `UNEXPECTED_FINAL_CHILD`
     - k=3: SIMULATION_CASE
     - k=4: FINAL_VERSION. 이름이 32자리 hex가 아니면 `FINAL_VERSION_INVALID`
     - k≥5: Working 하위 구간의 L3 이후 역할을 적용(미러). allowed_names 위반은 Working과 같은 코드
     - Final 가지는 **결과 판독 소스가 아니다.** 결과 캡처·Case 결과 화면은 Working만 읽는다. Final 역할은 Final 지정 이력과 대조·표시용이다
   - 의뢰에 Working이 없으면 `WORKING_MISSING`으로 등록을 막는다
   - 2 ≤ k ≤ n: `lower[k].role`. `allowed_names`가 있는데 이름이 없으면 `NAME_NOT_ALLOWED`
   - k > n: CONTENT (D6)
5. **가지가 중간에서 끝남**: `info: BRANCH_INCOMPLETE`로 표시하고 이탈로 보지 않는다(D7).
6. `_interpret`는 프로필 `format == "DEPTH_V1"`이면 `_role`·`resolve_role` 휴리스틱을 호출하지 않는다. legacy 형식은 기존 스냅샷 refresh 전용으로 남긴다.

### 노드 응답 필드 (스캔/프리뷰)

`level`, `segment` (`UPPER` | `LOWER` | `FINAL`), `role_kind`, `role_basis: "DEPTH_SCHEMA"`, `status` (`CONFIRMED` | `UNRESOLVED` | `CONTENT`), `deviation: {code, message} | null`, `info: "BRANCH_INCOMPLETE" | null`

### 이탈 코드

| code | 의미 | 등록 |
|---|---|---|
| `ENV_KEYWORD_BOTH` | 의뢰명에 사용·유통 둘 다 있음 | 차단 |
| `ENV_KEYWORD_NONE` | 의뢰명에 키워드 없음 | 차단 |
| `WORKING_MISSING` | 의뢰 아래 Working 없음 | 차단 |
| `UNEXPECTED_REQUEST_CHILD` | 의뢰 바로 아래에 Working/Final 이외 폴더 | 차단 |
| `UNEXPECTED_FINAL_CHILD` | Final 바로 아래에 CAE/Reports/CAD/.finalizations 이외 폴더 | 경고(등록 허용) |
| `FINAL_VERSION_INVALID` | Final/CAE·Reports/<Case> 아래 폴더명이 finalization id 형식이 아님 | 경고(등록 허용) |
| `NAME_NOT_ALLOWED` | allowed_names 위반(예: L5에 `ALL`) | 차단 |

## 6. API

### 신규 (관리자 전용, `routers/folder_environment_profiles.py`)

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| GET | `/api/folder-discovery/environments/depth-schema` | – | `{schema_set_id, upper, environments:{USAGE:{profile_id, environment_keyword, lower, usage_sources}, DISTRIBUTION:{…}}, created_at, created_by}` |
| PUT | `…/depth-schema` | `{expected_schema_set_id, upper, environments}` | GET과 동일. 경합하면 409 `DEPTH_SCHEMA_CONFLICT` |
| POST | `…/depth-schema/samples` | `{segment: "UPPER"\|"USAGE"\|"DISTRIBUTION", upper?}` | `{levels:[{level, folder_count, samples:[{name, count}] (≤8), truncated}], requests_sampled}` |
| POST | `…/depth-schema/check` | `{upper, environments}` (초안) | `{by_code:{CODE: count}, examples:[{relative_path, code}] (≤20)}` |
| POST | `/api/requests/{request_id}/reinterpret` | – | 현재 스키마로 scan → preview → register. 이탈이 있으면 등록하지 않고 이탈 목록을 반환 |

- `samples`: 하위 구간은 `upper`로 찾은 해당 환경 의뢰를 최대 20개까지 보고, 의뢰 기준 상대 깊이로 집계한다. 집계는 Working 가지만 대상으로 하고(Final은 고정 구조라 편집 대상 아님), 기존 `_Lister` 한도를 재사용한다.
- 모든 엔드포인트는 SPDM에 쓰지 않는다(읽기 전용 스캔).

### 변경

- `scan`: `profile_id`를 무시한다(보내도 오류는 아님). 노드에 §5 필드를 추가한다.
- `preview.assignments`: 허용 값은 `EXCLUDE`와 PROJECT/REQUEST의 `LINK target_id`뿐이다. 그 밖의 role_kind와 `propagate_same_level`은 422 `ASSIGNMENT_NOT_ALLOWED`.

### 폐기 (410 `DEPRECATED_USE_DEPTH_SCHEMA`)

`POST`·`PUT`·`DELETE /profiles`, `/profiles/from-legacy`. `GET /environments`(목록)는 읽기 전용으로 유지한다.

## 7. UI

- `FolderEnvironmentPanels.FolderProfileEditor` → `DepthSchemaEditor`로 교체한다.
  - 탭 3개: 상위 구조 / 사용환경 / 유통환경
  - 탭마다 표 1개: `깊이 | 예시 폴더(이름×개수, +N) | 폴더 수 | 역할 select`
  - Root 행은 표시만 하고, 하위 탭의 L1 Working 행은 잠근다. 하위 탭 아래에 Final 고정 구조(L1 Final / L2 CAE·Reports / L3 Case / L4 버전 / L5~ 미러)를 읽기 전용으로 함께 표시한다. RUN_OPTION 행에만 허용 이름 칩 입력을 둔다.
  - 탭을 열면 `samples`를 자동 호출한다. 행 수는 샘플 깊이에 맞춰 자동이고, 마지막 행 삭제만 가능하다.
  - 버튼은 **확인**(check 결과를 코드별 건수로 표시)과 **저장** 2개뿐이다.
- `FolderEnvironmentWorkspace`
  - 역할 select, "같은 깊이 적용", 프로필 select와 그 localStorage 기억, "구조를 규칙으로 저장"을 삭제한다.
  - 역할은 읽기 전용으로 표시하고 이탈 배지를 붙인다. 남는 편집은 "제외"와 PROJECT/REQUEST "기존 업무 연결"뿐이다.
- 의뢰 화면에 **재해석** 버튼을 둔다(D9).
- 화면 표기: 사용환경의 "평가"는 "Scene"으로 바꾼다(D8).

## 8. 자동 탐색 (`folder_auto_discovery.py`)

- `_project_candidates`: `MAX_CONTAINER_LEVELS`와 `_PROJECT` 정규식 대신 `upper`로 BFS한다. project_level의 비숨김 폴더는 모두 PROJECT, request_level 폴더는 의뢰 후보다.
- `request_identity`: 키워드로 환경을 판정한다(D4). WR 키는 `\[?WR[-_]?(\w+)`로 추출하고, 실패하면 폴더명 casefold를 쓴다. `NAME_NOT_STANDARD`는 `ENV_KEYWORD_*`로 대체한다. `_SIMTYPE` 정규식은 삭제한다(D5).
- 이탈이 하나라도 있으면 `needs_review: DEPTH_DEVIATION`(첫 코드와 건수)으로 보내고 등록하지 않는다.
- `_skip_final_archive`는 제거한다. Final은 §5 규칙으로 역할만 판정하며, 결과 등록 대상에서는 계속 제외한다(D12). 의뢰 판정은 upper의 request_level 기준이다.

## 9. 결과 판독과의 관계 (변경 없음)

역할이 SCENE으로 통합되어도 사용환경 결과 판독 규칙은 그대로다(`dashboard_capture.py`, `usage_source_review.py`).

| Scene 폴더 | 결과 파일 | 최상위 키 |
|---|---|---|
| Settle | `<cond>_settle_result.json` (공통) | `Set Tilt Angle @ Settle (deg)` |
| Wobble | `<cond>_wobble_center_{front,back}_result.json` | `Wobble Disp. (mm)` |
| Horizontal_Force_Angle | `<cond>_horizontal_force_angle_{front,back}_result.json` | `Set Tilt Angle Difference (deg)` |
| Slope_Angle | `<cond>_slope_angle_{front,back}_result.json` | `Slope Angle (deg)`, `OK/NG` |
| Slope_Angle_360 | `<cond>_slope_angle_{front,back}_360_result.json` | `OK/NG` |

미디어는 결과 stem에서 `_result`를 뺀 이름으로 둔다(`.mp4/.webm/.png/.jpg`). 예제 데이터는 `SPDM (Admin)/75R9J_PV/[WR-0001]_[사용_환경]/Working/Assy_RES_Model_SetCase1_StandCase1_Inner/`에 있다(JSON 9개, 영상 9개).

## 10. 작업 분해

| ID | 담당 | 파일 | 선행 |
|---|---|---|---|
| T0 | 메인 | 이 문서, `frontend/src/shared/api/folderEnvironment.ts` 타입 | – |
| T1 | Impl-Backend | `backend/migrations/versions/0034_folder_depth_schema.py` | T0 |
| T2 | Impl-Backend | `environment_folder_profiles.py` (검증, `resolve_path`, 세트 저장) | T0 |
| T3 | Impl-Backend | `folder_discovery_environment.py`, `folder_schema_resolver.py` (`_interpret` 분기, assignment 제한, Final 판정) | T2 |
| T4 | Impl-Backend | `folder_auto_discovery.py` (BFS, 키워드, WR 키, SimType 삭제) | T2 |
| T5 | Impl-Backend | `routers/folder_environment_profiles.py` (GET/PUT/samples/check, 410), reinterpret 엔드포인트 | T2 |
| T6 | Impl-Frontend | `FolderEnvironmentPanels.tsx` → `DepthSchemaEditor` | T0 (mock) |
| T7 | Impl-Frontend | `FolderEnvironmentWorkspace.tsx` 단순화, 재해석 버튼, 평가→Scene 표기 | T0 (mock) |
| V | Verifier | 아래 기준 | 전체 |

T1과 T2는 병렬, T3·T4·T5는 T2 이후 병렬, T6과 T7은 T0 이후 병렬로 진행한다. T3·T4·T5는 파일이 겹치지 않는다.

## 11. Verifier 통과 기준

1. 실제 트리 fixture 2개(유통 `75R9J_PV/[WR-0002]_[유통_환경]`, 사용 `75R9J_PV/[WR-0001]_[사용_환경]`)에서 이탈 0건, 역할이 §3과 일치
2. 유통 L5에 `ALL` 폴더 → `NAME_NOT_ALLOWED`, 등록 차단
3. 의뢰명 키워드 없음/둘 다 → auto-discovery `needs_review`, 등록 없음
4. Working 없는 의뢰 → `WORKING_MISSING`
5. Scene 아래 하위폴더 → CONTENT, 이탈 아님
6. 스키마 저장 후 기존 등록 의뢰의 refresh가 정상(revision 오류 없음)이고 역할 불변. 재해석 후에만 변경
7. Final fixture: `CAE`/`Reports` → FINAL_CAE/FINAL_REPORTS, `<Case>/<id>/Drop/…/INDIVIDUAL` → SIMULATION_CASE/FINAL_VERSION/LOAD_CASE/EXECUTION_RUN/RUN_OPTION, `.finalizations` 무시. Final 하위 파일은 결과 캡처에 포함되지 않음. 모든 신규 엔드포인트에서 SPDM 쓰기 0건
8. 사용환경 예제 Case를 등록하면 대시보드 "다섯 평가 종합" 표가 9개 슬롯 모두 채워짐(PARTIAL 없음)
9. 편집기 버튼은 확인·저장 2개
10. mypy, tsc, pytest, vitest 통과

## 12. 미확정

- `Final/CAD`: 실제 폴더에 존재하며(현재 비어 있음) 앱은 쓰지 않는다. 잠정적으로 FINAL_CAD(하위 CONTENT)로 둔다. 용도와 채우는 주체를 확정해야 한다.
