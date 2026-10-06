# 폴더 이름 경고 (W6)

사용자는 SPDM 공유 폴더에 Scene 폴더를 직접 복사한다. 이름을 잘못 쓰면(`4_Edge` 옆에 `4_edge2`, Case A의 `6_Corner`와 Case B의 `6_corner`) 결과가 다른 Scene으로 갈라지거나 Case 비교가 맞지 않는데, 화면에는 오류로 나타나지 않는다. Case 결과 화면은 이런 이름을 **경고만** 하며 폴더는 바꾸지 않는다. 근거: [개선 로드맵 W6](../plans/improvement-roadmap.md), [Case 결과 흐름 재설계 §8.4](../plans/case-results-workflow-redesign.md).

## 데이터

- 계산: `backend/app/services/folder_name_warnings.py`. Case 결과 catalog(`GET /api/dashboard/catalog`)가 이미 읽은 현재 Folder Schema snapshot(`resolve_request_locations`)만 사용한다. 추가 폴더 스캔·DB 쓰기 없음. 오류가 나면 경고만 빈 목록이 되고 catalog는 그대로 응답한다.
- 응답 필드(추가형): `name_warnings: [{kind, severity: "warning"|"info", message, paths, case_id, run_option_id}]`. `message`는 한국어이며 내부 코드를 넣지 않는다. `paths`는 SPDM 루트 기준 상대 경로(경고당 최대 20개), 전체 최대 50건, 경고(`warning`)가 먼저 온다. Folder Schema를 읽지 못하면 빈 목록이다.

| kind | 기준 | severity |
|---|---|---|
| `SCENE_NAME_CASE` | 같은 Run Option(유통)·Case(사용) 아래 Scene 이름이 casefold 후 `_ - 공백`을 빼면 같음 (`2_Face`/`2_face`) | warning |
| `SCENE_NAME_SUFFIX` | 한 이름 뒤에 숫자만 붙은 이름 (`4_Edge`/`4_edge2`). 대소문자·구분 기호까지 같으면(`4_Edge`/`4_Edge2`) 의도일 수 있어 info | warning·info |
| `SCENE_NAME_SPELLING` | 정규화 후 글자 하나 추가·삭제·변경 또는 인접 두 글자 바꿈(5자 이상, 숫자 차이는 제외: `1_Face`/`2_Face`는 정상) | warning |
| `CASE_SCENE_MISMATCH` | 같은 의뢰의 여러 Case에서 같은 위치(Case 아래 하위 경로를 정규화해 비교)의 Scene 이름이 위 규칙으로 비슷하지만 Case마다 다르게 적힘 | warning |
| `UNEXPECTED_FOLDER` | snapshot 노드의 깊이 스키마 이탈 코드([depth-schema §5](../contracts/depth-schema.md))를 한국어 문구로 표시, 상태 UNRESOLVED·CONFLICT 노드(EXCLUDED는 사용자가 뺀 것이라 제외) | warning |
| `UNEXPECTED_FOLDER` | 같은 Run Option·Case 아래 Scene 대부분이 번호로 시작하는데 번호로 시작하지 않는 폴더(`backup` 등). 깊이 스키마에서는 그 깊이 폴더가 모두 Scene이 되므로 이름으로만 구분 | info |

Final 가지는 위치 투영에서 이미 빠지므로 Final 하위 이탈(`UNEXPECTED_FINAL_CHILD` 등)은 이 목록에 나오지 않는다.

## 화면

- `frontend/src/features/results/FolderNameWarnings.tsx`. Case 결과 머리줄(환경 토글 옆)에 경고가 있을 때만 "폴더 이름 확인 n건" 배지를 보인다. 누르면 문구와 관련 경로 목록이 펼쳐진다. 경로는 `Working/…`부터 보이고 전체 경로는 title 툴팁에 있다. 소재·물성 탭에서는 숨긴다.
- Scene 비교 탭(엣지별 수준 Scene 설명, 컨투어 행 머리, 거동 제목)에서 경고 경로의 마지막 폴더 이름과 같은 Scene 옆에 작은 경고 아이콘을 둔다. `run_option_id`가 있는 경고는 선택한 Run Option에서만 표시한다.
- 데스크톱 전용 기준으로 2560×1440·배율 1.5·18pt에서 확인했다(`e2e/simulation-dashboard.spec.ts`의 `folder name warnings at 4K 150%`).

## 검증

- `backend/tests/test_folder_name_warnings.py`: 합성 트리 `4_Edge`/`4_edge2`, `2_Face`/`2_face`, Case 간 `6_Corner`/`6_corner`, Run Option 아래 `backup`, 정상 트리(경고 없음), 이탈 코드 한국어 문구, 유사도 규칙.
