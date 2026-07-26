# TV 낙하·Chassis Rear 형식별 결과 예제

`manifest.json`이 폴더 파일을 변수 카탈로그와 연결한다.

`context`에는 프로젝트(제품), 의뢰, 하중 경우 정보를 넣는다. 자동 등록 모드에서는 이 메타데이터로 계층을 먼저 만들고, 그 하위 Analysis Run에 결과를 적재한다. 수동 등록 모드에서는 화면에서 선택한 기존 하중 경우에만 결과를 적재한다.

- `results/summary.json`: Open Cell 4개 엣지 응력, Chassis Rear 6개 위치 영구변형, 정수, 텍스트, 판정
- `curves/*.csv`: 배열/커브 데이터
- `media/*.svg`: 컨투어 이미지

영상은 대용량 파일이므로 Git 예제에는 넣지 않았다. 실제 운영 폴더에서는 `media/drop_animation.mp4`를 추가하고 manifest에 `kind: media`, `asset_type: VIDEO`, `mime_type: video/mp4` 매핑을 선언한다.
