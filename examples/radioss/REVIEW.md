# Radioss TV 예제 결과 리뷰

검토 파일: `radioss_tv_result_example.csv`

## 데이터 구성과 품질

- 전체 175행
- NODE 프레임 행 111개, 고유 노드 43개
- ELEMENT 프레임 행 64개, 고유 요소 24개
- 전체 결과 시각 3개: 0, 10, 30 ms
- 중복 `(time, node_id)` 및 `(time, element_id)` 없음
- Open Cell 요소 연결 노드 누락 없음
- 길이 단위 mm, 응력 단위 MPa, 시간 단위 ms로 일관
- Open Cell과 Chassis Rear 파트 모두 포함

## 자동 산출 결과

| 분석 | 위치 | 최대값 | 발생 엔티티/시각 | 판정 |
|---|---|---:|---|---|
| Open Cell 최대주응력 | 상단 엣지 | 74.40 MPa | ELEMENT 5016 / 10 ms | PASS |
| Open Cell 최대주응력 | 하단 엣지 | 84.00 MPa | ELEMENT 5001 / 10 ms | FAIL |
| Open Cell 최대주응력 | 좌측 엣지 | 84.00 MPa | ELEMENT 5001 / 10 ms | FAIL |
| Open Cell 최대주응력 | 우측 엣지 | 82.95 MPa | ELEMENT 5004 / 10 ms | FAIL |
| Chassis Rear 직선 이격 | 상단 엣지 | 6.402 mm | NODE 2014 / 30 ms | FAIL |
| Chassis Rear 직선 이격 | 하단 엣지 | 4.303 mm | NODE 2005 / 30 ms | PASS |
| Chassis Rear 모서리 변위 | 좌상단 | 5.20 mm | NODE 2010 / 30 ms | FAIL |
| Chassis Rear 모서리 변위 | 우상단 | 3.10 mm | NODE 2018 / 30 ms | PASS |
| Chassis Rear 모서리 변위 | 좌하단 | 2.40 mm | NODE 2001 / 30 ms | PASS |
| Chassis Rear 모서리 변위 | 우하단 | 5.40 mm | NODE 2009 / 30 ms | FAIL |

Open Cell 기준은 75 MPa, Chassis Rear 기준은 5 mm이며 값이 기준 이상이면 FAIL입니다. 코너 요소는 인접한 두 엣지 밴드에 동시에 포함될 수 있으므로 ELEMENT 5001이 하단과 좌측 최대값으로 함께 나타나는 것은 의도된 동작입니다.

## 계산 정의

Open Cell은 원래 형상의 X/Y 경계에서 폭 또는 높이의 18% 이내에 중심점이 있는 요소를 엣지 밴드로 분류합니다. 각 프레임에서 엣지별 요소 최대주응력의 최댓값을 구하고, 전체 프레임 중 최대값과 발생 요소/좌표를 저장합니다.

Chassis Rear는 최종 NODE 프레임에서 원래 좌표와 변위를 더해 변형 후 좌표를 만듭니다. 상·하 엣지별 양 끝 노드의 변형 후 위치를 잇는 3차원 직선과 각 중간 노드 사이의 수직거리 중 최댓값을 사용합니다. 네 꼭지점은 최종 변위 벡터의 크기를 사용합니다.

## 중요한 가정

최종 프레임 30 ms를 하중 제거 후 잔류 상태로 가정했습니다. 실제 파일의 마지막 프레임에 하중이 남아 있으면 결과는 `영구변형`이 아니라 `하중 중 변형`이므로 판정에 사용할 수 없습니다. 실제 자동화에서는 하중 제거 시각 또는 분석 단계 식별자를 CSV에 추가하는 것이 좋습니다.

Open Cell은 취성 유리이므로 von Mises가 아닌 최대주응력을 사용했습니다. 적층 유리, 방향별 허용응력 또는 표면별 응력이 필요한 경우 적분점/표면 선택 규칙을 프로젝트 기준에 맞춰 추가해야 합니다.

## 공식 출력 근거

- Radioss H3D에는 노드·요소 정의, 변위와 요소 응력 결과가 포함될 수 있습니다: <https://help.altair.com/hwsolvers/rad/topics/solvers/rad/h3d_output_file_other_r.htm>
- Shell H3D는 적분점, Layer/PLY, MEMB/BEND 위치별 응력을 출력할 수 있습니다: <https://help.altair.com/hwsolvers/rad/topics/solvers/rad/h3d_shell_engine_r.htm>
- HyperView Query는 노드 좌표, 요소 연결·중심, Contour 값을 조회할 수 있습니다: <https://help.altair.com/hwdesktop/hwx/topics/panels/query_panel_querying_entities_t.htm>
- Query 표는 CSV 확장자로 내보낼 수 있습니다: <https://help.altair.com/hwdesktop/hwx/topics/panels/query_panel_exporting_a_query_table_t.htm>
