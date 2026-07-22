# Radioss TV 결과 CSV 예제

`radioss_tv_result_example.csv`는 대시보드의 `해석 데이터 → 해석 결과 가져오기`에서 바로 검증할 수 있는 예제입니다.

## 행의 의미

- `NODE`: 원래 좌표 `x,y,z`와 해당 프레임 변위 `dx,dy,dz`
- `ELEMENT`: 연결 노드 `n1~n4`와 요소 응력
- `part_name`: `OPEN_CELL` 또는 `CHASSIS_REAR`
- `time`: Radioss 결과 프레임 시각
- 단위: 길이 `mm`, 응력 `MPa`, 시간 `ms`

Open Cell에는 `stress_max_principal`이 필수입니다. 유리 파손 평가에서 최대주응력을 사용하기 때문입니다. `stress_von_mises`도 원본 추적용으로 함께 보관할 수 있습니다.

Chassis Rear는 가장 늦은 NODE 프레임을 하중 제거 후 상태로 간주합니다. 상·하 엣지마다 변형 후 양 끝 노드를 잇는 3차원 직선을 만들고, 중간 노드 중 이 직선에서 가장 멀리 떨어진 거리와 위치를 계산합니다. 네 모서리는 최종 변위 벡터 크기를 사용합니다.

## HyperView에서 준비할 데이터

1. Radioss H3D 또는 A-file을 HyperView에 로드합니다.
2. 최종 하중 제거 프레임의 노드 ID, 원래 좌표와 Displacement X/Y/Z를 Query로 CSV 내보냅니다.
3. 전체 해석 프레임의 Open Cell 요소 ID, Connectivity, Component/Part, 최대주응력 Contour 값을 CSV로 내보냅니다.
4. 열 이름을 이 예제의 스키마로 매핑하고 NODE/ELEMENT 행을 하나의 CSV로 합칩니다.

HyperView Query는 노드 좌표, 요소 연결, 요소 중심 및 현재 Contour 값을 CSV로 내보낼 수 있습니다. 실제 HyperView 내보내기 헤더는 버전과 결과 설정에 따라 달라질 수 있으므로, 첫 실데이터 파일을 받으면 별도 변환 없이 인식하도록 헤더 매핑을 추가하는 것이 다음 작업입니다.

## 예제 재생성

```powershell
python .\examples\radioss\generate_example.py
```
