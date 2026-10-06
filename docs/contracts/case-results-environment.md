# 계약: 결과 화면의 환경 자동 판정과 "선택" 처리

- 상태: 확정 (2026-10-03)
- 관련: `docs/contracts/depth-schema.md` (D4 의뢰명 키워드로 환경 판정)

## 1. 결정

| # | 결정 |
|---|---|
| E1 | 작업 규칙상 한 의뢰에는 사용·유통이 섞이지 않는다. 의뢰의 환경은 그 의뢰에 등록된 Case(`dashboard_cases.environment`)로 판정한다 |
| E2 | Case 결과 화면의 사용환경/유통환경 버튼을 없애고, 판정된 환경을 경로바에 라벨로 표시한다. 환경 전환은 자동이다 |
| E3 | Case가 없는 의뢰는 "결과 없음" 안내 페이지를 보여준다. 사용자가 폴더에 결과를 추가하면 자동 탐색이 등록한다(별도 버튼 없음) |
| E4 | 예외로 한 의뢰에 두 환경 Case가 모두 있으면(구 데이터) 기존 토글을 그 경우에만 보여준다 |
| E5 | 결과 등록 화면(`ResultDropWorkspace`, W8)도 의뢰를 고르면 환경을 같은 규칙으로 자동 지정한다. 폴더 연결·규칙 화면의 환경 선택은 유지한다(등록 전, 스키마가 환경별) |
| E6 | 헤더 프로젝트 드롭다운에서 "선택"(빈 값)을 고르면 **내 작업**(`/workspace/requests`, page `dashboard`)으로 이동한다. 빈 ID로 API를 호출하지 않는다 |
| E7 | 의뢰 드롭다운에서 "선택"을 고르면 의뢰 선택만 해제하고 프로젝트 화면에 머문다. 빈 ID로 API를 호출하지 않는다 |

## 2. API

| 메서드 | 경로 | 응답 |
|---|---|---|
| GET | `/api/projects/{project_id}/requests/{request_id}/result-environments` | `{environments: ("USAGE"\|"DISTRIBUTION")[], case_counts: {USAGE: n, DISTRIBUTION: n}}` |

- 출처: `SELECT environment, count(*) FROM dashboard_cases WHERE project_id=? AND request_id=? GROUP BY environment`. 순서는 USAGE, DISTRIBUTION.
- 권한: 해당 의뢰의 Case 결과를 볼 수 있는 사용자(기존 catalog 엔드포인트와 같은 권한 검사).
- 의뢰가 없으면 404, Case가 없으면 `environments: []`.

## 3. 프론트 동작 (Case 결과 화면)

1. 의뢰가 바뀌면 `result-environments`를 호출한다.
2. 환경이 1개면 URL 파라미터 `result_environment`를 그 값으로 **replace**(히스토리 추가 없음)하고, 토글 없이 경로바에 "사용환경" 또는 "유통환경" 라벨을 표시한다. URL에 다른 값이 있어도 판정값이 우선한다.
3. 0개면 "결과 없음" 안내를 보여준다. 안내 문구: "이 의뢰에는 아직 등록된 결과가 없습니다. 의뢰 폴더의 Working에 결과를 추가하면 자동 탐색이 등록합니다." Catalog는 호출하지 않는다.
4. 2개면 기존 토글을 보여준다(E4).
5. 판정 중에는 catalog를 호출하지 않는다(잘못된 환경으로 한 번 불러오는 깜빡임 방지).

`result_environment`를 소비하는 곳(자동 동기화, 보고서, Final 지정 범위, 폴더 링크)은 판정된 값을 그대로 쓴다.

## 4. Verifier 기준

1. 사용 의뢰를 열면 사용 결과, 유통 의뢰를 열면 유통 결과가 표시되고, 토글이 없다(라벨만)
2. URL에 `result_environment=USAGE`를 넣고 유통 의뢰를 열면 유통으로 교정된다
3. Case가 없는 의뢰 → "결과 없음" 안내, catalog 호출 없음
4. 두 환경 Case가 섞인 의뢰(합성 데이터) → 토글 표시
5. 헤더 프로젝트 "선택" → 내 작업으로 이동, 404 호출 없음. 의뢰 "선택" → 선택 해제, 404 호출 없음
6. 결과 등록 화면에서 의뢰 선택 시 환경 자동 지정
7. 백엔드 엔드포인트 권한(비멤버 403/404 기존 규칙과 동일), 환경 집계 정확성
