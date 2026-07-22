# 등록용 예제 파일

`radioss_tv_result_example.csv`는 실제 Radioss/HyperView 결과를 웹 대시보드에 등록하기 위한 표준화 중간 CSV 예제다.

- `NODE`: 노드 ID, 원 좌표, 프레임별 변위
- `ELEMENT`: 요소 ID, 연결 노드, 프레임별 최대 주응력/등가응력
- `OPEN_CELL`: 외곽 18% 밴드의 요소별 최대 주응력으로 상·하·좌·우 엣지를 평가
- `CHASSIS_REAR`: 최종 프레임의 변형 좌표로 엣지 chord 최대 이격과 네 모서리 영구변형을 평가

재생성:

```powershell
python examples/radioss/generate_example.py example/radioss_tv_result_example.csv
```

실행 중인 로컬 앱에 API로 등록:

```powershell
python example/register_example.py
```

이 파일은 합성 검증 데이터이며 실제 제품 승인 자료가 아니다.
