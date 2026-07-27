# Run 비교·데이터 신뢰도·결과 검토 확장 사양

문서 버전: 1.0

## 1. 목적과 호환성

해석 담당자가 최신 결과가 기준 Run보다 나빠졌는지, 표시 결과를 신뢰할 수 있는지, 어떤 위치를 후속 검토해야 하는지를 한 화면에서 판단하도록 한다. 기존 대시보드 정의, 레이아웃 버전, 결과 판정과 보고서 내보내기는 수정하지 않는다.

## 2. Run 비교

- 하중 경우에 속한 완료·진행 Run을 최신 순으로 조회한다.
- 기준 Run과 대상 Run은 서로 다른 Run을 선택한다.
- 정량 결과는 `variable_key`로 결합하고 단위가 같을 때만 `대상값 - 기준값`과 변화율을 계산한다.
- 원본 `verdict`를 사용해 `REGRESSION`, `IMPROVED`, `UNCHANGED`를 계산한다.
- 한쪽에만 존재하는 변수는 `ADDED` 또는 `REMOVED`, 단위가 다른 변수는 `NOT_COMPARABLE`로 표시한다.
- 두 Run에 공통으로 있는 시계열 변수는 선택한 변수 하나씩 겹쳐 표시한다.

## 3. 데이터 신뢰도

신뢰도는 새로운 판정값이 아니라 데이터 사용 전 확인 정보다.

- Run 상태와 완료 시각
- 최신 Run 여부와 완료 후 경과 일수
- 원본 종류·파일명 또는 폴더, SHA-256 체크섬
- 폴더 스키마 ID·버전과 파서 버전
- 정량·시계열·커브·미디어·위치 결과 수
- 결과 변수의 카탈로그 연결 여부와 선언 변수 누락 여부
- 결과 단위와 변수 카탈로그 단위의 불일치
- 연결된 Validation의 실패 여부

치명 실패가 있으면 `FAIL`, 확인할 경고가 있으면 `WARN`, 모두 통과하면 `TRUSTED`로 요약한다.

## 4. 결과 북마크와 검토 의견

- 북마크 문맥: `analysis_run_id`, `variable_key`, 선택 시점, `NODE|ELEMENT`와 엔티티 ID
- 검토 의견: 제목, 본문, 작성자, `OPEN|IN_REVIEW|RESOLVED`
- 의견 생성 후 상태 변경은 허용하고 초기 버전에서는 삭제하지 않는다.
- 변수 문맥을 지정하면 해당 Run에 실제 결과가 존재하는지 API에서 검증한다.

## 5. API

- `GET /api/load-cases/{load_case_id}/runs`
- `GET /api/load-cases/{load_case_id}/run-comparison`
- `GET /api/analysis-runs/{run_id}/trust`
- `GET /api/analysis-runs/{run_id}/review-items`
- `POST /api/analysis-runs/{run_id}/review-items`
- `PATCH /api/review-items/{annotation_id}`

## 6. 수용 기준

1. 기존 해석 상세 화면과 저장 레이아웃의 API 응답이 바뀌지 않는다.
2. 예제 하중 경우에서 과거 기준 Run과 최신 Run을 비교할 수 있다.
3. PASS에서 FAIL로 바뀐 변수가 회귀로 표시된다.
4. 단위가 다른 값은 수치 차이를 계산하지 않는다.
5. 신뢰도 패널은 출처·최신성·커버리지·단위·검증 점검을 표시한다.
6. 변수 문맥을 가진 의견을 생성하고 검토 상태를 변경할 수 있다.
