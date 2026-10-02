# Folder Schema 공용 위치·Refresh 계약

- 요구사항: GitHub [#43](https://github.com/wgcha/simdashboard/issues/43)
- 적용 범위: 의뢰별 사용환경·유통환경 Folder Schema, Case 결과, 유통환경 소재·물성, 결과 등록
- 배포 기준: [Windows 배포 정책](../windows-deployment-policy.md)
- 2026-10-02 승인된 Working/Final·계층 규칙 보완은 [구현 계획](../plans/folder-schema-working-final-implementation.md)에서 단계별 검증 상태를 관리한다.

## 위치 정본

`folder_schema_locations.resolve_request_locations()`는 선택한 프로젝트·의뢰·환경의 확인된 Folder Schema snapshot에서 위치를 투영한다. 유통환경의 기준 항목은 `SCENE`이다. Scene ID는 확인된 `target_id`를 우선하고, 없는 과거 행에서만 경로 기반 stable ID를 사용한다. Case 결과와 소재 목록은 이 ID·상대 경로·계층을 공유한다. 덱이 없는 확인 Scene도 소재 목록에 남고 `has_deck=false`로 표시한다.

위치에는 `SIMULATION_CASE → LOAD_CASE → EXECUTION_RUN → RUN_OPTION → SCENE` 문맥을 보존한다. 생략 가능한 역할은 허용하지만 다른 Run 또는 Scene의 자료를 합치지 않는다. **확정 Scene 폴더 자체가 입력과 결과의 공통 위치**다. `.rad`·`.inc`와 CSV·미디어 등 결과 파일이 함께 존재하며 별도 INPUT/RESULTS 하위 폴더를 요구하지 않는다. 기존 자료에 명시적인 `INPUT`과 `RESULTS`가 있으면 같은 계층의 Scene에 연결하고 각 경로의 근거와 우선순위를 노출한다. 제외·미확정·다른 의뢰/환경 소유 경로와 root 이탈·reparse 경로는 후보가 아니다.

결과 등록·Case 결과·소재 물성의 구조 목록은 이 공용 위치와 확정 역할 트리를 사용한다. 값·그래프·미디어는 선택된 immutable capture에서 조회한다. capture가 없거나 결과 파일 수집이 실패했다는 이유로 확정된 하중경우·Run·Scene 자체를 목록에서 제거하지 않는다. 현재 구조와 과거 수집 버전의 불일치는 별도 상태로 표시한다.

등록 이력에서 의뢰 경로를 복원할 때는 현재 환경과 프로젝트·의뢰 target ID가 일치하는 역할만 사용한다. 저장소 전체 조사에 함께 포함된 다른 의뢰 폴더는 현재 의뢰의 연결 후보가 아니다. 실제로 같은 의뢰가 여러 경로에 연결되면 임의 선택하지 않는다. 수집 준비 실패는 등록 이력을 보존한 채 Case별 실패 코드로 표시하고, 연결을 수정한 뒤 같은 등록에서 미완료 결과를 다시 읽는다.

저장소 기준 경로나 상위 포장 폴더가 바뀌어 조사 역할의 생성 ID가 기존 업무 ID와 달라진 경우에는, 선택 업무에 명시적으로 연결해 적용한 미리보기와 과거 동일 업무의 확정 registry를 대조한다. 프로젝트·의뢰 이름과 프로젝트 아래 의뢰의 상대 경로가 정확히 일치하는 단일 경로만 복원한다. 다른 의뢰 및 미적용 미리보기는 복원 근거가 아니다. 근거가 없으면 연결 필요, 일치 경로가 여러 개면 연결 중복으로 구분하며 기존 수집본·업무 ID는 변경하지 않는다.

결과 수집은 현재 등록의 의뢰 범위로 제한한다. 여러 의뢰의 Case가 함께 미리보기에 있더라도 현재 의뢰 밖의 Case를 빈 수집본이나 다른 의뢰 소유 Case로 저장하지 않는다. 범위 밖 Case는 `CAPTURE_CONTEXT_MISMATCH`로 실패 처리하며 해당 의뢰 폴더에서 별도로 등록하도록 안내한다.

## Working과 Final

구조 확인에서 수동으로 역할을 확정하면 조사 당시의 역할 불일치 경고를 해소하고 미리보기에서 상위 역할을 다시 검증한다. Scene은 Run Case 또는 Run Option 아래여야 하며, 실제 계층 오류는 확인 필요 항목으로 남겨 등록을 차단한다. 등록 미리보기의 긴 경로·폴더명은 각 열 안에서 줄바꿈하고 상태와 진단 문구를 구분해 표시한다.

의뢰 직속 `Working`은 작업 Case의 기준 영역이고 `Final`은 사용자가 최종확정한 자료의 보존 영역이다. Working을 0으로 삼는 깊이별 역할은 같은 깊이의 이름이 다른 폴더에도 적용한다. 의뢰 직속 Working/Final의 역할은 깊이만으로 합치지 않으며 Final 및 그 하위는 일반 Case 조사·등록·수집 후보가 아니다. 개별 수동 예외·제외와 과거 ID는 보존한다. 구체적인 저장 규칙 편집·삭제·재적용과 최종확정 파일 복사 범위는 구현 계획을 따른다.

## 새 폴더 판정

Refresh는 활성 환경 규칙과 기존 수동 확인·제외 상태를 적용한다. 같은 `(환경, 의뢰 root, 부모 역할, 상대 깊이)`에 가능한 역할이 정확히 하나면 새 자식 폴더에 `LEVEL` 근거로 상속한다. 역할이 여러 개면 이름 규칙 또는 수동 확인이 필요하며 임의로 하나를 고르지 않는다. 결과용·입력용 폴더, 제외 규칙 및 수동 확정은 레벨 상속보다 우선한다. 기존 확정 ID와 제외 상태는 유지하고 새 폴더에만 새 stable ID를 부여한다.

이름 규칙과 맞지 않아 역할 없이 남은 폴더(`UNRESOLVED`, 근거 `DEFAULT`, 저장된 수동 결정 없음)는 미결정으로 본다. 미결정 폴더는 같은 `(부모 역할, 상대 깊이)`의 확정 역할이 **정확히 하나이고 그것이 `SCENE`일 때만** `LEVEL` 근거로 Scene 역할을 상속한다. 이전 Refresh에서 이미 조사된 폴더도 같다. 그 밖의 역할(예: Run 아래 알 수 없는 폴더)이나 여러 역할이 섞인 깊이에서는 미결정 상태를 유지하며, 이것만으로 Refresh를 `CONFLICT`로 막지 않는다. 결과 등록 화면에서 만든 빈 Scene이나 사용자가 직접 복사한 Scene 폴더가 이 규칙으로 목록에 들어간다(2026-10-02, 4_Edge 사례).

각 Refresh snapshot은 역할 해석 규칙 버전 `role_rules_revision`을 기록한다. 이전 버전 snapshot은 폴더 fingerprint가 같아도 다음 Refresh에서 한 번 다시 해석한다. 다시 해석해도 노드 역할이 바뀌지 않으면 새 capture를 만들지 않고 `changed=false`로 응답한다.

화면의 Refresh 결과는 `LEVEL`, `PATTERN`, `MANUAL`, `EXCLUDED`, `CONFLICT` 등 역할 판정 근거와 구조 차이를 보여준다. 파일 탐색기에서 직접 수정한 내용은 자동 감시하지 않으며 사용자가 명시적으로 Refresh를 실행한다.

## scoped Refresh와 실패

`POST /api/folder-discovery/environments/refresh`는 `project_id`, `request_id`, `environment`를 받아 해당 의뢰 경계만 조사한다. 현재 storage root와 프로필 revision을 확인하고 제한된 scan, 구조·내용 fingerprint, 역할 판정을 마친 뒤 정상 snapshot을 활성화한다. 같은 fingerprint의 재시도는 기존 snapshot을 재사용한다. 내용이 바뀌어 capture가 새로 필요하면 과거 capture를 수정하지 않고 새 버전으로 보존한다. 내용 바이트 해시는 `.csv`, `.json`, `.jpg`, `.jpeg`, `.png`, `.mp4`, `.webm`, `.inc`, `.rad`에 적용한다. 그 외 확장자는 경로·크기·수정 시각으로 변화를 판정하므로 크기와 수정 시각이 모두 같게 바뀐 내용은 탐지하지 못할 수 있다.

한도 초과, 접근 오류, 역할 충돌, 프로필 변경 또는 작성 중 파일이 있으면 새 snapshot을 활성화하지 않는다. 마지막 정상 snapshot과 과거 capture는 유지하며 실패 이유를 표시한다. 결과 등록의 폴더 생성 또는 파일 mirror가 성공하면 같은 scope를 자동 Refresh한다. 자동 Refresh만 실패한 경우에는 폴더 준비나 DB 등록의 성공을 취소하지 않고 화면에 상태를 분리해 표시하며 재시도한다. 초안 업로드와 격리 검수는 자동 Refresh 대상이 아니다.

## 보존·검증 경계

사용자 파일을 이동·삭제하거나 기존 capture를 덮어쓰지 않는다. 새 migration·의존성·배포 진입점 없이 기존 스캔 이력을 사용한다. 재현과 회귀 검증에는 격리 DB와 합성 SPDM root만 사용한다. 실제 사내 DB·저장소 및 Windows Server 2022 폐쇄망 설치·업데이트·재부팅은 별도 현장 검증 대상이다.
