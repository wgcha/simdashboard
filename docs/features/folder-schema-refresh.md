# Folder Schema 공용 위치·Refresh 계약

- 요구사항: GitHub [#43](https://github.com/wgcha/simdashboard/issues/43)
- 적용 범위: 의뢰별 사용환경·유통환경 Folder Schema, Case 결과, 유통환경 소재·물성, 결과 등록
- 배포 기준: [Windows 배포 정책](../windows-deployment-policy.md)

## 위치 정본

`folder_schema_locations.resolve_request_locations()`는 선택한 프로젝트·의뢰·환경의 확인된 Folder Schema snapshot에서 위치를 투영한다. 유통환경의 기준 항목은 `SCENE`이다. Scene ID는 확인된 `target_id`를 우선하고, 없는 과거 행에서만 경로 기반 stable ID를 사용한다. Case 결과와 소재 목록은 이 ID·상대 경로·계층을 공유한다. 덱이 없는 확인 Scene도 소재 목록에 남고 `has_deck=false`로 표시한다.

위치에는 `SIMULATION_CASE → LOAD_CASE → EXECUTION_RUN → RUN_OPTION → SCENE` 문맥을 보존한다. 생략 가능한 역할은 허용하지만 다른 Run 또는 Scene의 자료를 합치지 않는다. 확인된 `INPUT`과 `RESULTS` 경로만 같은 계층의 Scene에 연결하고, 각 경로의 근거와 우선순위를 노출한다. 제외·미확정·다른 의뢰/환경 소유 경로와 root 이탈·reparse 경로는 후보가 아니다.

## 새 폴더 판정

Refresh는 활성 환경 규칙과 기존 수동 확인·제외 상태를 적용한다. 같은 `(환경, 의뢰 root, 부모 역할, 상대 깊이)`에 가능한 역할이 정확히 하나면 새 자식 폴더에 `LEVEL` 근거로 상속한다. 역할이 여러 개면 이름 규칙 또는 수동 확인이 필요하며 임의로 하나를 고르지 않는다. 결과용·입력용 폴더, 제외 규칙 및 수동 확정은 레벨 상속보다 우선한다. 기존 확정 ID와 제외 상태는 유지하고 새 폴더에만 새 stable ID를 부여한다.

화면의 Refresh 결과는 `LEVEL`, `PATTERN`, `MANUAL`, `EXCLUDED`, `CONFLICT` 등 역할 판정 근거와 구조 차이를 보여준다. 파일 탐색기에서 직접 수정한 내용은 자동 감시하지 않으며 사용자가 명시적으로 Refresh를 실행한다.

## scoped Refresh와 실패

`POST /api/folder-discovery/environments/refresh`는 `project_id`, `request_id`, `environment`를 받아 해당 의뢰 경계만 조사한다. 현재 storage root와 프로필 revision을 확인하고 제한된 scan, 구조·내용 fingerprint, 역할 판정을 마친 뒤 정상 snapshot을 활성화한다. 같은 fingerprint의 재시도는 기존 snapshot을 재사용한다. 내용이 바뀌어 capture가 새로 필요하면 과거 capture를 수정하지 않고 새 버전으로 보존한다. 내용 바이트 해시는 `.csv`, `.json`, `.jpg`, `.jpeg`, `.png`, `.mp4`, `.webm`, `.inc`, `.rad`에 적용한다. 그 외 확장자는 경로·크기·수정 시각으로 변화를 판정하므로 크기와 수정 시각이 모두 같게 바뀐 내용은 탐지하지 못할 수 있다.

한도 초과, 접근 오류, 역할 충돌, 프로필 변경 또는 작성 중 파일이 있으면 새 snapshot을 활성화하지 않는다. 마지막 정상 snapshot과 과거 capture는 유지하며 실패 이유를 표시한다. 결과 등록의 폴더 생성 또는 파일 mirror가 성공하면 같은 scope를 자동 Refresh한다. 자동 Refresh만 실패한 경우에는 폴더 준비나 DB 등록의 성공을 취소하지 않고 화면에 상태를 분리해 표시하며 재시도한다. 초안 업로드와 격리 검수는 자동 Refresh 대상이 아니다.

## 보존·검증 경계

사용자 파일을 이동·삭제하거나 기존 capture를 덮어쓰지 않는다. 새 migration·의존성·배포 진입점 없이 기존 스캔 이력을 사용한다. 재현과 회귀 검증에는 격리 DB와 합성 SPDM root만 사용한다. 실제 사내 DB·저장소 및 Windows Server 2022 폐쇄망 설치·업데이트·재부팅은 별도 현장 검증 대상이다.
