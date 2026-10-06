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
| D2a | Working 하위의 폴더 이름은 제한하지 않는다. RunOption도 INDIVIDUAL·CUMULATIVE 외 이름을 허용한다. 역할은 깊이로만 정한다(이름 규칙 `allowed_names` 없음) |
| D2b | RunOption 레벨은 생략하지 않는다(운영 규칙). 자동 누락 감지는 두지 않고, 대신 L5(RunOption)에서 발견된 **폴더 이름 전체 목록**을 보여준다. 관리자는 이 목록에서 Scene 이름이 섞였는지 등을 눈으로 확인한다 |
| D3 | 스키마는 환경별 전역 1개. 프로젝트별 재정의 없음 |
| D4 | 환경은 의뢰 폴더명 부분일치로 정한다. `사용` → USAGE, `유통` → DISTRIBUTION. 둘 다 있거나 둘 다 없으면 "확인 필요" |
| D5 | 구형 `WR_x_SimType1|2` 규칙은 삭제한다 |
| D6 | 정의된 마지막 깊이보다 깊은 폴더는 역할 없는 내용물(CONTENT)로 허용한다 |
| D7 | 가지가 중간 깊이에서 끝나도 정보 표시만 하고(`BRANCH_INCOMPLETE`), 등록은 허용한다 |
| D8 | 사용환경의 `EVALUATION` 역할을 `SCENE`으로 통합한다. 두 환경 모두 Scene을 쓴다 |
| D9 | 스키마를 저장해도 기존 등록 의뢰는 변경하지 않는다. 의뢰 화면의 **재해석** 버튼으로만 새 스키마를 적용한다 |
| D10 | 하위 L1은 **이름으로** 구분한다. `Working`(필수)과 `Final`(선택, Final 지정 후 생성). `Final`도 스키마 대상이다 |
| D11 | Final 하위 L2는 이름으로 구분한다. `CAE` = 해석 입력·결과 파일, `Report` = 보고서 기능이 만든 PPTX·HTML(사용자가 Final 지정 때 하나 이상 선택, PDF는 만들지 않음). 앱의 SPDM 쓰기는 이 두 폴더로만 한다 |
| D12 | Final/CAE·Report의 L3 이하는 고정 구조다: `<Case>/<finalization_id>/` 아래에 Working의 Case 이하 구조를 그대로 미러링한다. 관리자 편집 대상이 아니며 UI에 읽기 전용으로 표시한다 |

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
      │  ├─ Report                      L2 FINAL_REPORTS  보고서 PPTX·HTML
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
                  └─ <RunOption>       L5 RUN_OPTION        예: INDIVIDUAL, CUMULATIVE (이름 제한 없음)
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
      { "level": 5, "role": "RUN_OPTION" },
      { "level": 6, "role": "SCENE" }
    ],
    "below_last": "CONTENT"
  },
  "final": {
    "fixed": true,
    "children": [
      { "name": "CAE", "role": "FINAL_CAE" },
      { "name": "Report", "role": "FINAL_REPORTS" },
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
| DISTRIBUTION | `RUN_OPTION` 레벨 필수 |
| 이름 제한 | 레벨 항목에 `allowed_names` 등 이름 조건을 둘 수 없다(있으면 422). 이름으로 판정하는 곳은 하위 L1(Working/Final)과 Final L2(CAE/Report/CAD)뿐이다 |
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
     - k=2: `CAE` → FINAL_CAE, `Report` → FINAL_REPORTS, `CAD` → FINAL_CAD(이하 CONTENT), `.finalizations` → 무시. 그 외 이름은 `UNEXPECTED_FINAL_CHILD`
     - k=3: SIMULATION_CASE
     - k=4: FINAL_VERSION. 이름이 32자리 hex가 아니면 `FINAL_VERSION_INVALID`
     - k≥5: Working 하위 구간의 L3 이후 역할을 적용(미러)
     - Final 가지는 **결과 판독 소스가 아니다.** 결과 캡처·Case 결과 화면은 Working만 읽는다. Final 역할은 Final 지정 이력과 대조·표시용이다
   - 의뢰에 Working이 없으면 `WORKING_MISSING`으로 등록을 막는다
   - 2 ≤ k ≤ n: `lower[k].role` (이름 무관)
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
| `UNEXPECTED_FINAL_CHILD` | Final 바로 아래에 CAE/Report/CAD/.finalizations 이외 폴더 | 경고(등록 허용) |
| `FINAL_VERSION_INVALID` | Final/CAE·Report/<Case> 아래 폴더명이 finalization id 형식이 아님 | 경고(등록 허용) |

## 6. API

### 신규 (관리자 전용, `routers/folder_environment_profiles.py`)

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| GET | `/api/folder-discovery/environments/depth-schema` | – | `{schema_set_id, upper, environments:{USAGE:{profile_id, environment_keyword, lower, usage_sources}, DISTRIBUTION:{…}}, created_at, created_by}` |
| PUT | `…/depth-schema` | `{expected_schema_set_id, upper, environments}` | GET과 동일. 경합하면 409 `DEPTH_SCHEMA_CONFLICT` |
| POST | `…/depth-schema/samples` | `{segment: "UPPER"\|"USAGE"\|"DISTRIBUTION", upper?}` | `{levels:[{level, folder_count, samples:[{name, count}] (≤8), truncated}], requests_sampled, run_option_names?:[{name, count, request_count}]}` |
| POST | `…/depth-schema/check` | `{upper, environments}` (초안) | `{by_code:{CODE: count}, examples:[{relative_path, code}] (≤20)}` |
| POST | `/api/requests/{request_id}/reinterpret` | – | 현재 스키마로 scan → preview → register. 이탈이 있으면 등록하지 않고 이탈 목록을 반환 |

- `run_option_names` (DISTRIBUTION만): L5 위치에서 발견된 폴더 이름의 **전체** 목록(중복 제거, casefold 기준 병합, 개수와 의뢰 수 포함). 샘플 20개 제한을 받지 않고 해당 환경의 모든 의뢰를 대상으로 한다(목록 한도 500, 초과 시 `truncated`).
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
  - Root 행은 표시만 하고, 하위 탭의 L1 Working 행은 잠근다. 하위 탭 아래에 Final 고정 구조(L1 Final / L2 CAE·Report / L3 Case / L4 버전 / L5~ 미러)를 읽기 전용으로 함께 표시한다.
  - 유통 탭의 RUN_OPTION 행은 예시 대신 **RunOption 이름 전체 목록**(이름 · 폴더 수 · 의뢰 수)을 펼쳐 보여준다. 읽기 전용이다.
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
2. 유통 L5에 `INDIVIDUAL`·`CUMULATIVE` 외 이름(예: `ALL`) → RUN_OPTION으로 정상 판정, 이탈 0건. `samples`의 `run_option_names`에 모든 의뢰의 L5 이름이 빠짐없이 나옴
3. 의뢰명 키워드 없음/둘 다 → auto-discovery `needs_review`, 등록 없음
4. Working 없는 의뢰 → `WORKING_MISSING`
5. Scene 아래 하위폴더 → CONTENT, 이탈 아님
6. 스키마 저장 후 기존 등록 의뢰의 refresh가 정상(revision 오류 없음)이고 역할 불변. 재해석 후에만 변경
7. Final fixture: `CAE`/`Report` → FINAL_CAE/FINAL_REPORTS, `<Case>/<id>/Drop/…/INDIVIDUAL` → SIMULATION_CASE/FINAL_VERSION/LOAD_CASE/EXECUTION_RUN/RUN_OPTION, `.finalizations` 무시. Final 하위 파일은 결과 캡처에 포함되지 않음. 모든 신규 엔드포인트에서 SPDM 쓰기 0건
8. 사용환경 예제 Case를 등록하면 대시보드 "다섯 평가 종합" 표가 9개 슬롯 모두 채워짐(PARTIAL 없음)
9. 편집기 버튼은 확인·저장 2개
10. mypy, tsc, pytest, vitest 통과

## 13. 등록 삭제 (관리자)

### 13.1 결정

| # | 결정 |
|---|---|
| D13 | 관리자는 "등록 이력"의 등록을 삭제할 수 있다. 삭제 범위는 **등록 기록 + 폴더 역할 매핑 + 그 등록이 생성한 업무 데이터**(프로젝트·의뢰·Case·캡처·자산·Final 지정 DB 기록 등)다 |
| D14 | SPDM 폴더·파일은 어떤 경우에도 지우거나 옮기지 않는다(`.finalizations`, Final/CAE·Report 포함) |
| D15 | 다른 살아 있는 등록, 수동 연결(SPDM 저장소 연결, 레거시 매핑), 등록 이전부터 존재한 엔터티가 참조하는 데이터는 지우지 않는다. 이런 참조가 삭제 대상 행에 걸려 있으면 **전체를 거부(409)**하고 부분 삭제하지 않는다. **예외(2026-10-06, §16):** 관리자가 프로젝트 정리에서 명시적으로 선택한 프로젝트는 예전 연결 기록(`folder_discovery_registry`, `spdm_storage_*`, `semantic_folder_bindings`)과 등록 이전 데이터까지 함께 지운다 |
| D16 | 등록 행은 물리 삭제하지 않고 `status=DELETED`로 남긴다(묘비). 감사·이력 추적용이다. 이력 화면은 기본으로 DELETED를 숨긴다 |
| D17 | 삭제된 등록은 "연결됨"으로 치지 않는다. 같은 의뢰 폴더는 이후 자동 탐색이 **새 깊이 스키마로 다시 등록**할 수 있다(목적: 구 스키마 등록 정리 후 재등록) |

### 13.2 소유 판정 (무엇이 "이 등록이 생성한" 데이터인가)

1. 신규 등록부터는 `register` 시점에 실제로 INSERT한 엔터티 id를 `folder_environment_registrations.created_targets`(JSON: `{project_ids, request_ids, case_ids}`)에 기록한다. 이 값이 있으면 그대로 쓴다.
2. 값이 없는 기존 등록(구 스키마)은 추론한다. 엔터티가 이 등록의 registry `target_id`이고, **그 엔터티의 `created_at`이 등록 `created_at` − 5초 이후**이며, 다른 살아 있는(DELETED가 아닌) 등록의 registry가 같은 `target_id`를 가리키지 않으면 소유로 본다.
3. Case(`dashboard_cases`)는 이 등록의 registry `relative_path`에 해당하고 다른 살아 있는 등록이 참조하지 않으면 소유다. 캡처·자산은 소유 Case의 것이면 함께 삭제한다.
4. 소유가 아닌 엔터티(LINK된 기존 프로젝트 등)는 남긴다. 그 아래에서 이 등록이 만든 의뢰·Case만 지운다.
5. (2026-10-06 보완) 지우는 등록이 그 프로젝트·의뢰를 쓰는 **마지막 살아 있는 등록**이면, 이미 DELETED된 같은 프로젝트·의뢰의 등록이 만든 프로젝트·의뢰도 이 등록의 소유로 본다(DELETED 등록의 `created_targets`, 없으면 1·2의 시각 추론). 등록을 하나씩 지워도 빈 프로젝트·의뢰가 남지 않는다.

### 13.3 삭제 순서 (한 트랜잭션, 자식 먼저, cascade 없음)

`folder_discovery.WRITE_LOCK`과 해당 scope의 자동 동기화 잠금을 잡고, Postgres에서는 대상 등록 행을 `SELECT … FOR UPDATE`한다.

1. `folder_environment_capture_jobs` (해당 등록)
2. 소유 Case의 Final 지정 DB 기록, `result_registration_*`(case_id/capture_id 참조), `dashboard_assets` → `dashboard_captures` → `dashboard_cases`
3. `folder_environment_registry` (해당 등록)
4. 소유 의뢰의 하위 행(`spdm_storage_bindings`, `request_work_plans`, `analysis_request_type_assignments`, `request_result_layout_snapshots`, `semantic_folder_bindings`, load_cases 하위 등 구현 시 FK 전수 조사 결과 전부) → `folder_environment_scans`/`previews`의 request_id·project_id는 NULL 처리(이력 조인 보존) → `analysis_requests`
5. 소유 프로젝트의 하위 행(`product_information`, `project_memberships`, `project_invitations`, `quality_thresholds`, `project_workspace_layouts(+versions)` 등) → `projects`
6. 등록 행: `status=DELETED`, `deleted_at`, `deleted_by` 기록
7. 감사 이벤트 `FOLDER_ENVIRONMENT_REGISTRATION_DELETED`(삭제 건수 요약 포함). `audit_events`는 지우지 않는다

차단 사유가 하나라도 있으면 1~7을 하나도 실행하지 않고 409 `REGISTRATION_DELETE_BLOCKED`와 차단 목록을 반환한다.

### 13.4 마이그레이션 `0035_folder_registration_delete`

- `folder_environment_registrations.status` CHECK에 `DELETED` 추가
- 컬럼 추가: `deleted_at TIMESTAMP NULL`, `deleted_by TEXT NULL`, `created_targets TEXT NULL`(JSON)
- `registration()`의 상태 재계산(작업 상태로 덮어쓰기)은 DELETED를 건드리지 않는다
- Postgres와 DuckDB 둘 다 지원한다

### 13.5 자동 탐색·동기화 영향

- `_linked_state`, 자동 탐색 멱등 키 검사, ADMIN_EXCLUDED 계산, auto_sync 대상 선정에서 DELETED 등록을 제외한다.
- 자동 탐색 멱등 키는 `auto-discovery-<sha(경로, schema_set_id)>`로 바꾼다. 구 키가 남아 있어도 새 스키마 등록을 막지 않는다.
- DELETED 등록의 capture job은 재시도·실행 대상이 아니다.

### 13.6 API (전역 관리자 전용)

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| POST | `/api/folder-discovery/environments/registrations/delete-preview` | `{registration_ids: string[]}` (1~200) | `{items:[{registration_id, deletable, counts:{projects, requests, cases, captures, assets, finalizations, other}, blockers:[{table, id, reason}]}], confirm_token}` |
| POST | `/api/folder-discovery/environments/registrations/delete` | `{registration_ids, confirm_token}` | `{deleted:[registration_id], counts}`. 차단 시 409 `REGISTRATION_DELETE_BLOCKED`(+preview와 같은 items). 토큰이 현재 상태와 다르면 409 `DELETE_PREVIEW_STALE` |
| GET | `…/history?include_deleted=false` | 기본 false | 기존 형식 + 항목별 `status`, `deleted_at` |

- `confirm_token` = 대상 id 집합과 삭제 예정 행 id 집합의 해시. preview 이후 상태가 바뀌면 삭제를 거부한다.
- 요청한 등록 중 하나라도 차단되면 전체 거부한다(원자적).
- 이미 DELETED인 등록은 건너뛰고 `deleted`에 포함하지 않는다(멱등).

### 13.7 UI (등록 이력 탭)

- 각 행 왼쪽에 체크박스, 목록 위에 **삭제** 버튼 1개(선택이 없으면 비활성)와 "전체 선택" 체크박스를 둔다.
- 삭제를 누르면 preview를 호출하고, 확인 대화상자에 합계(프로젝트 n · 의뢰 n · Case n · 캡처 n · Final 기록 n)와 "SPDM 폴더·파일은 삭제되지 않습니다"를 표시한다. 차단이 있으면 사유 목록만 보여주고 삭제 버튼을 비활성화한다.
- 삭제가 끝나면 목록, 프로젝트 목록, 선택 상태를 다시 읽는다. 삭제된 프로젝트·의뢰를 보고 있었다면 선택을 해제한다.

### 13.8 Verifier 기준 (추가)

11. 구 스키마 등록 1건 삭제 → 그 등록이 만든 프로젝트·의뢰·Case·캡처·자산 0건, 등록 행은 DELETED. SPDM 트리의 파일 수·mtime 변화 0
12. LINK된 기존 프로젝트 아래 등록 삭제 → 프로젝트는 남고 이 등록의 의뢰·Case만 삭제
13. 다른 살아 있는 등록이 같은 Case를 참조 → 409, 어떤 행도 삭제되지 않음
14. preview 후 다른 등록 추가 → delete가 `DELETE_PREVIEW_STALE`
15. 삭제 후 자동 탐색 1회 → 같은 의뢰가 새 깊이 스키마로 재등록됨(DELETED 등록에 막히지 않음)
16. 비관리자 호출 → 403
17. Postgres(가능하면)와 DuckDB 양쪽에서 삭제 테스트 통과

## 14. 수동 등록은 의뢰 1개 단위 (2026-10-03)

### 14.1 결정

| # | 결정 |
|---|---|
| D18 | 등록 1건 = 의뢰 1개. 수동 등록("폴더 연결")의 미리보기 계획에 REQUEST가 2개 이상이면 `can_apply=false`로 등록을 막는다. 일괄 등록은 자동 탐색이 담당한다 |
| D19 | 의뢰 폴더를 직접 골라 조사하면 상위 스키마의 PROJECT 깊이 폴더(조사 경로의 조상)를 프로젝트로 자동 도출한다. 기존 업무 연결을 고르지 않았으면 그 폴더로 프로젝트를 만들거나 연결한다 |
| D20 | 깊이 스키마의 역할 정의는 "저장된 규칙"에서만 한다. 수동 등록 화면은 역할을 바꾸지 않는다(§7, 읽기 전용) |

### 14.2 동작

- `_preview_depth`: 활성 계획(EXCLUDE 제외)의 REQUEST 수를 센다.
  - 0개: 기존대로(`can_apply=false`, 의뢰 없음).
  - 2개 이상: `can_apply=false`, 차단 사유 `MULTIPLE_REQUESTS`("의뢰 폴더별로 조사하세요. 여러 의뢰는 자동 탐색이 의뢰별로 등록합니다."), 미리보기 응답에 `request_paths:[...]` 포함.
- `register`: 방어 검사. 계획의 REQUEST가 1개가 아니면 409 `MULTIPLE_REQUESTS`로 거부하고 아무것도 쓰지 않는다(registry·프로젝트·의뢰 생성 없음).
- 의뢰 폴더 조사(D19): 조사 경로 깊이가 request_level이면 그 조상 중 project_level 폴더를 계획에 PROJECT 행으로 넣는다(`parent_context` 채움). 조상 깊이가 상위 스키마와 맞지 않으면 `can_apply=false`, 사유 `DEPTH_SCHEMA_REQUEST_LEVEL_MISMATCH`. `analysis_requests.project_id`가 NULL로 들어가는 경로가 없어야 한다(500 제거).
- Case를 의뢰 밖에서 계획에 넣는 경로가 없으므로 `CAPTURE_CONTEXT_MISMATCH`는 정상 흐름에서 발생하지 않는다.

### 14.3 시각 표기

- 폴더 환경 API(스캔, 미리보기, 등록, 이력, 삭제 결과)의 시각 필드는 UTC 오프셋을 포함한 ISO 8601(`...Z` 또는 `+00:00`)로 반환한다. 저장 형식(naive UTC)은 바꾸지 않는다.
- 프론트는 오프셋이 없는 시각을 UTC로 해석하는 공용 함수로 표시한다(구 응답·캐시 대비).

### 14.4 Verifier 기준 (추가)

18. Root·프로젝트 수준 조사(의뢰 2개 이상, 사용·유통 각각) → 미리보기 `can_apply=false`, `MULTIPLE_REQUESTS`. 등록 API 직접 호출 시 409, DB 변화 0
19. 의뢰 폴더 수준 조사(기존 업무 미선택) → 프로젝트 자동 도출, 등록 성공, 모든 Case 결과 읽기 완료(사용·유통 각각). 500 없음
20. 등록·이력 시각이 KST 화면에서 실제 시각으로 표시(UTC 23:02 → 10.3 오전 8:02)

## 15. 폴더명 정정과 프로젝트 공통 폴더 (2026-10-06)

| # | 결정 |
|---|---|
| D21 | Final 하위 보고서 폴더 이름은 `Report`(단수)다. 앱은 `Final/Report`만 쓰고 읽는다. 기존에 만들어진 `Final/Reports`는 인식하지 않는다(그 Final 기록은 보고서 없음으로 보임, 필요하면 사용자가 폴더 이름을 바꾼다). 역할 키 `FINAL_REPORTS`는 내부 식별자로 유지한다 |
| D22 | 프로젝트 폴더 바로 아래의 `CAD`, `Report` 폴더(이름 대소문자 무시)는 사용자 수동 작업 영역이다. 의뢰 후보가 아니며, 자동 탐색·수동 조사·확인 필요·진척 계산에서 모두 제외(무시)한다. 앱은 여기에 쓰지 않는다 |

- 구현: 상위 스키마의 request_level 폴더 중 이름이 `CAD`/`Report`이면 무시 목록(`.`, `~$` 시작 이름과 같은 취급). `ENV_KEYWORD_NONE` 확인 필요를 만들지 않는다.
- Verifier: (1) Final 지정·보고서 업로드가 `Final/Report/<Case>/<id>/`에 저장되고 진척 REPORT가 그것으로 완료, (2) 기존 `Final/Reports`만 있는 Final 기록은 REPORT 대기, 오류 없음, (3) `75R9J_PV/CAD`, `75R9J_PV/Report`가 있어도 자동 탐색 needs_review 0건, 등록·스캔 영향 없음, (4) 쓰기 구역(StorageProvider FINAL)이 `Final/Report`로 바뀌고 `Final/Reports` 쓰기는 거부

## 16. 프로젝트 정리 (관리자, 2026-10-06)

계획: [운영 정리 계획](../plans/workspace-cleanup.md) §1. 구현: `backend/app/services/project_cleanup.py`, 화면 `FolderProjectCleanup.tsx`(관리 › 폴더 스키마 › **프로젝트 정리** 탭, 전역 관리자만 보임).

### 16.1 대상과 구분

| 구분 | 판정 | 선택 |
|---|---|---|
| 데모 | 코드 상수 `DEMO_PROJECT_IDS`(`project-tv-001`, `project-feature-showcase`, `project-d2f8298b56ce`). 이름으로 판정하지 않는다 | 가능 |
| 등록 없음 | 살아 있는(DELETED가 아닌) 등록이 프로젝트·그 의뢰·그 Case를 참조하지 않음 | 가능 |
| 등록됨 | 살아 있는 등록이 참조 | 불가. §13 등록 삭제로 안내 |

목록은 의뢰 수, Case·해석 이력 수, **직접 만든 데이터**(시스템 분석 페이지·예제 시드 행을 뺀 분석 페이지, 실행 기록, 배치·PC 실행, 검증, 검토 메모, 결과 등록 초안)를 보여 준다. 직접 만든 데이터가 있는 프로젝트는 경고하고 기본 선택에서 뺀다.

### 16.2 삭제 범위 (선택한 프로젝트 것만, 자식 먼저, cascade 없음)

- 지움: 프로젝트, 의뢰, 하중경우, 해석 이력과 결과(스칼라·시계열·곡선·위치·북마크·검토·메모·메타데이터·가져오기 작업), 작업 단계·항목·계획·유형 지정·결과 레이아웃 스냅샷, 실행 기록(workflow·task·배치·PC 실행과 승인), 검증, 분석 페이지와 버전, Case·캡처·결과 자산, 결과 등록 초안·파일·이벤트·경로·위치 연결, 프로젝트 설정(제품 정보, 멤버십·초대, 품질 기준, 워크스페이스 레이아웃, 결과 프로필, 프로젝트 템플릿, 프로젝트 어휘), 변수 정의, 예전 연결 기록(`folder_discovery_registry` 하위 포함, `spdm_storage_*`, `semantic_folder_bindings`·검토 항목, D15 예외), DELETED 등록의 남은 registry·capture job, 이 프로젝트들만 참조하는 미디어(`media_assets`·`drop_video_assets`와 `asset_blobs`·청크).
- NULL 처리(행 유지): `folder_environment_scans.project_id/request_id`, DELETED 등록 묘비의 `project_id/request_id`.
- 남김: SPDM 폴더·파일(D14, 이 모듈은 파일 시스템에 접근하지 않는다), 계정·권한, 감사 기록, 공용 설정(보고서 레이아웃, 의뢰 유형·작업 카탈로그, 메뉴 정책, 가져오기 스키마), 다른 프로젝트도 참조하는 미디어. 예전 `media_assets.file_path`의 파일은 지우지 않는다.
- 표 전수 조사: `KEEP_COLUMNS`(보존 이유 포함)와 삭제 단계가 모든 프로젝트·의뢰·하중경우·실행·대상 id 컬럼을 분류해야 한다(`tests/test_project_cleanup.py::test_every_reference_column_is_handled_or_kept`).
- 참고: 시스템 분석 페이지(`dashboard-*-default`)는 Orion 프로젝트에 묶여 있어 Orion을 지우면 함께 사라진다.

### 16.3 차단 (하나라도 있으면 전체 409 `PROJECT_CLEANUP_BLOCKED`, 아무것도 지우지 않음)

`LIVE_REGISTRATION`(등록됨, 살아 있는 등록의 capture job·registry가 대상 Case를 가리킴), `RUNNING_EXECUTION`(대기·실행 중인 배치 시도·디스패치·workflow·task·PC 실행), `PROJECT_TEMPLATE_IN_USE`(지우지 않는 결과 프로필이 프로젝트 템플릿을 사용).

### 16.4 API (전역 관리자 전용, 그 외 403)

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| GET | `/api/folder-discovery/environments/project-cleanup` | — | `{items:[{project_id, name, category: DEMO\|EMPTY\|REGISTERED, selectable, requests, cases, runs, user_data, user_data_total}], demo_project_ids}` |
| POST | `…/project-cleanup/preview` | `{project_ids}` (1~200) | `{items:[{project_id, name, category, deletable, counts:{표: 건수}, blockers:[{table,id,reason}], user_data}], totals, confirm_token}`. 없는 id는 404 |
| POST | `…/project-cleanup/delete` | `{project_ids, confirm_token}` | `{deleted, counts}`. 토큰 불일치 409 `DELETE_PREVIEW_STALE`, 차단 409 `PROJECT_CLEANUP_BLOCKED`(+items) |

- `confirm_token` = 선택 id, 구분, 삭제 대상 id 집합, 표별 건수, 차단 목록의 해시.
- 실행: `folder_discovery.WRITE_LOCK` 안에서 계획을 다시 계산해 토큰을 비교한다. PostgreSQL은 한 트랜잭션(`projects`·`analysis_requests`·`load_cases`·`folder_environment_registrations` SHARE ROW EXCLUSIVE 잠금). 개발용 DuckDB는 §13과 같이 Case FK 단계마다 커밋한다(재시도 시 수렴).
- 감사 이벤트 `PROJECT_CLEANUP_DELETED`(`project_ids`, 구분, 표별 건수).

### 16.5 데모 재생성 방지

- 데모 프로젝트를 지우면 기존 키-값 표 `spdm_storage_settings`의 `demo_projects_removed`(JSON 목록)에 기록한다(새 표·migration 없음).
- `ensure_default_content`(개발용 DuckDB 시작, 명시적 `seed_database.py --mode reference`)는 기록된 데모를 다시 만들지 않는다: Orion 시드·요청 그래프·예제 진화·데모 미디어·Chassis 분석 페이지, 기능 예제 모음. 새 DB(기록 없음)는 지금처럼 데모를 만든다.
- PostgreSQL 앱 시작과 `update.bat`/`update.ps1`은 예제를 만들지 않는다(테스트로 고정).

### 16.6 화면

목록(체크박스, 등록됨은 비활성) → **미리보기** → 확인 창(합계, 프로젝트별 건수, 표별 건수 펼침, 직접 만든 데이터 경고, "SPDM 폴더·파일은 삭제되지 않습니다", 차단 사유) → **삭제** → 목록·프로젝트 목록 다시 읽기, 삭제된 프로젝트를 보고 있었으면 선택 해제(§13.7과 같은 콜백).

### 16.7 Verifier 기준

1. 데모가 든 DB에서 Orion 삭제 → 모든 표의 텍스트 컬럼에서 Orion 프로젝트·의뢰·하중경우·실행 id 참조 0건(감사 기록 제외), 기능 예제 모음의 참조 수 불변, 공유 미디어 blob 유지, Orion 전용 영상 blob 삭제, SPDM 트리 파일 수·mtime 불변
2. 등록된 프로젝트 409, 실행 중 작업 409(함께 고른 다른 프로젝트도 그대로), 미리보기 후 변경 시 `DELETE_PREVIEW_STALE`, 비관리자 403
3. 예전 연결만 있는 프로젝트 정리 → 연결 기록 0건, 조사 이력 행은 NULL로 유지
4. DuckDB 재시작 후 지운 데모 미재생성, PostgreSQL 시작 시 시드 없음
5. §13.2-5: 같은 프로젝트의 등록 둘을 하나씩 지우면 프로젝트가 남지 않음

## 12. 미확정

- `Final/CAD`: 실제 폴더에 존재하며(현재 비어 있음) 앱은 쓰지 않는다. 잠정적으로 FINAL_CAD(하위 CONTENT)로 둔다. 용도와 채우는 주체를 확정해야 한다.
