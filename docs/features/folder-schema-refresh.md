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

화면의 Refresh 결과는 `LEVEL`, `PATTERN`, `MANUAL`, `EXCLUDED`, `CONFLICT` 등 역할 판정 근거와 구조 차이를 보여준다. 파일 탐색기에서 직접 복사·수정한 내용은 화면이 따라간다(2026-10-02, 자동 반영). Case 결과·소재물성 화면은 열릴 때와 보이는 동안 60초마다(2026-10-07 사용자 결정으로 30초 → 60초, [SCX 드라이브 계획](../plans/scx-drive-integration.md) §9 D3) `POST /api/folder-discovery/environments/sync`를 호출한다. 서버는 폴더 구조와 결과 관련 파일(결과·미디어·덱·보고서 확장자)의 이름·크기·수정 시각만 비교하는 빠른 확인을 먼저 하고, 차이·규칙 개정·새 등록이 있을 때만 위 scoped Refresh를 실행한다. 같은 의뢰 범위의 확인은 20초(수동 "지금 확인"은 5초) 안에서 하나로 합친다. 로그 등 결과와 무관한 파일 변경은 갱신·capture를 만들지 않는다. 같은 폴더 상태의 역할 충돌은 다시 조사하지 않는다. 조회 권한(`project.data.view`)으로 호출할 수 있으며 클라이언트 경로는 받지 않는다. 한계: 크기와 수정 시각이 같은 채 내용만 바뀐 결과 파일은 빠른 확인으로 탐지하지 못한다. 서버 프로세스가 여러 개면 합치기와 기록은 프로세스별이다.

## 새 프로젝트·의뢰 자동 확인

SPDM이 저장소 root에 만든 새 프로젝트·의뢰 폴더는 관리자 조사·등록 없이 선택 목록에 나타난다(2026-10-02). 화면은 시작 시와 보이는 동안 60초마다 `POST /api/folder-discovery/environments/discover`(본문 없음 또는 `{force}`)를 호출한다. 서버(`folder_auto_discovery.py`)는 root → 포장 폴더 최대 2단계(예: `SPDM (Admin)`) → 프로젝트(`[A-Z0-9]{5}_PV`, `project_`/`prj_`, 또는 이미 연결된 프로젝트 경로) → 의뢰의 직속 하위 폴더만 나열한다.

**이름 규칙**: 이름 전체가 `[WR-<숫자>]_[사용_환경]`(USAGE), `[WR-<숫자>]_[유통_환경]`(DISTRIBUTION), 또는 `WR_<id>_SimType1|2`(USAGE|DISTRIBUTION)와 일치할 때만 자동 등록한다. 의뢰처럼 보이지만 정확히 일치하지 않는 이름(`- 복사본`, `_old`, `copy`, 이름 변경)은 등록하지 않는다. 같은 프로젝트에 같은 WR 번호·환경이 이미 다른 폴더로 연결되어 있어도 두 번째 의뢰를 만들지 않는다.

새 의뢰만 한 번 깊게 조사한다. 프로젝트 폴더에서 시작하되 다른 의뢰와 의뢰 직속 `Final`의 하위는 읽지 않고(등록 시 재조사도 같은 경계), 해당 환경의 활성 규칙으로 기존 scan → preview → registration(capture 포함)을 그대로 실행한다. Case가 없는 빈 Working의 의뢰도 등록하며 결과는 비어 있다(이후 60초 자동 반영이 Case를 채운다). 새 프로젝트는 그 첫 의뢰 등록과 같은 트랜잭션에서 폴더 이름으로 만들고 **멤버십을 부여하지 않는다**. 이미 연결된 폴더(환경 registry, SPDM 의뢰 연결, 구 폴더 registry)와 이미 사용한 멱등 키(root+경로+환경)는 건너뛴다.

**확인 필요(`needs_review`, 전역 관리자에게만 반환)**: `NAME_NOT_STANDARD`(이름 형식 불일치), `WR_ALREADY_LINKED`(같은 WR 번호·환경 연결됨), `RENAME_AMBIGUOUS`(연결된 옛 의뢰 폴더가 없어지고 같은 WR 번호·환경의 새 폴더가 여러 개 — 하나뿐이면 기존 의뢰를 그 폴더로 옮겨 연결하고 `renamed_requests`로 알림, [depth-schema §8](../contracts/depth-schema.md)), `PROJECT_RENAME_AMBIGUOUS`·`PROJECT_RENAME_UNSUPPORTED`(연결된 프로젝트 폴더가 없어졌는데 새 프로젝트 폴더가 여럿·다른 프로젝트와도 맞거나, 이전 방식 연결이라 옮길 수 없음 — 하나로 맞으면 같은 프로젝트로 연결하고 `renamed_projects`로 알림), `ADMIN_EXCLUDED`(관리자가 미리보기에서 제외한 폴더, 숨기지 않고 표시), `PROJECT_NAME_EXISTS`(폴더 연결 없는 같은 이름 프로젝트가 있어 새 프로젝트를 만들지 않음), `PROJECT_AMBIGUOUS`, `ROLES_UNRESOLVED`·`SCAN_INCOMPLETE`·`SCAN_FAILED`·`PREVIEW_FAILED`(폴더 상위 수정 시각이 같으면 10분간 재조사 안 함), `REGISTRATION_FAILED` 등 재시도 가능한 실패(폴더별 2·5·10분 backoff, `force`로도 건너뛰지 않음), `LIST_LIMIT`·`PATH_UNAVAILABLE`(목록 한도·접근 오류, 읽을 수 없는 하위 폴더 하나가 형제 폴더를 가리지 않음).

**실행 경계**: 호출자는 실행을 기다리지 않는다. 합치기(프로세스별 60초, `force` 10초) 확인과 실행 잠금(비대기)을 DB 연결 전에 하고, 다른 실행 중이면 마지막 결과를 `status: RUNNING`으로 바로 돌려준다. 한 실행은 최대 45초(단계마다 확인)·새 의뢰 5개까지이며 나머지는 다음 확인에서 처리한다. 다른 프로세스가 같은 폴더를 먼저 등록하면(미리보기 ID 불일치) 이번 실행의 생성으로 보고·감사하지 않는다.

권한: `project.data.view`(모든 활성 계정의 회사 권한, 프로젝트·의뢰 목록 조회와 같은 권한)로 호출하며 클라이언트 경로를 받지 않는다. 무엇인가 생성된 경우에만 `FOLDER_ENVIRONMENT_AUTO_DISCOVERED` 감사 기록을 남긴다. 화면은 새 항목이 있을 때 프로젝트 목록과 현재 선택 프로젝트의 의뢰 목록만 다시 읽고 선택은 바꾸지 않으며, 401/403이면 새로고침 전까지 확인을 멈춘다. 새 프로젝트·의뢰는 기존 규칙대로 모든 활성 계정의 목록에 보이며, 쓰기 작업은 전역 관리자 또는 이후 지정된 프로젝트 멤버만 가능하다. 한계: 서버 프로세스가 여러 개면 합치기·backoff는 프로세스별이고 중복 생성은 등록 멱등 키 UNIQUE가 막는다.

끄기: `SIMDASH_AUTO_DISCOVERY=0`(또는 `false`/`off`/`no`; `folder_auto_discovery.enabled()`)이면 `discover`는 아무것도 등록하지 않는다. 격리된 e2e 실행기가 자기 fixture 폴더가 등록되지 않도록 쓰는 값이며, 배포는 기본값(켜짐)을 둔다(`.env.example` 주석 참고).

**전체 다시 확인 [저장 폴더 새로고침]**(전역 관리자, 2026-10-17 사용자 결정): 상단 버튼은 구 `POST /api/storage/refresh`(고정 root 하중 경우 연결)를 더 이상 부르지 않고 `POST /api/folder-discovery/environments/rescan`(시작 또는 진행 중인 실행 반환, 감사 `STORAGE_RESCAN_STARTED`)·`GET …/rescan`(현재·마지막 상태)을 쓴다(`storage_rescan.py`, 프로세스당 한 번에 하나, 백그라운드 스레드). 순서: 확인 필요 기억·재시도 backoff 지움 → 현재 깊이 스키마로 탐색 회차(`fresh`, 실행 중인 탐색은 기다림, 회차당 300초·새 의뢰 5개)을 `partial`이 아니거나 진전이 없을 때까지(최대 20회) → 등록된 모든 의뢰 `sync_scope(force)`. 결과는 등록하지 않은 폴더마다 경로·코드·사유(회차를 합쳐 경로+코드로 중복 제거, 시간 안에 조사를 못 끝낸 폴더는 `DISCOVERY_TIME_BUDGET`)와 확인 실패·충돌 의뢰(프로젝트·의뢰 이름·메시지)를 돌려준다. 드라이브·저장소는 읽기만 하고 DB만 쓰므로 SCX 드라이브 모드에서도 켜져 있다. 화면은 2초마다 상태를 읽어 버튼에 `저장 폴더 확인 중 · 의뢰 3/12`를 표시하고, 끝나면 목록을 다시 읽고 한 줄 요약(새 프로젝트·새 의뢰·확인한 의뢰·갱신·확인 필요·실패)과 확인 필요·실패 목록 창(닫기 가능)을 보인다. 상태는 메모리에만 있어 서버를 다시 시작하면 사라진다.

**이슈 #55 보완(2026-10-20)**: (1) 순서가 *탐색 → 새 깊이 스키마 적용 → 의뢰별 확인*이 되었다. 최신 등록이 옛 `schema_set_id`인 의뢰는 `reinterpret_request`로 다시 등록하고(단계 `REINTERPRET`, 버튼에 `새 깊이 스키마 적용 3/12`) 결과를 `reinterpreted`·`reinterpret_blocked`(이탈 요약)·`reinterpret_unchanged`·`reinterpret_deferred`로 보인다. (2) 프로젝트 폴더는 의뢰가 등록되지 않아도 프로젝트가 되고(`created_projects`), 공백만 다른 프로젝트 폴더는 한 프로젝트다([depth-schema §8](../contracts/depth-schema.md)). (3) 결과 창의 등록하지 않은 폴더마다 [제외], 위쪽 [모두 제외]로 `ADMIN_EXCLUDED`로 옮기고 "제외한 폴더 n개"(접힘)에서 [다시 포함]한다(`POST …/exclusions`, `…/exclusions/restore`). (4) 깊이 스키마 편집기가 저장 뒤 시작한 새로고침은 `simdash:storage-rescan-started` 창 이벤트로 상단 버튼이 이어받아 진행을 보인다.

**이슈 #55 보완(2026-10-21, 새로고침이 DISCOVER에서 멈춤)**: 운영 서버(SCX 드라이브)에서 새로고침이 14분 넘게 `phase: DISCOVER, rounds: 0, progress 0/0`에 머물렀다. 원인: 첫 탐색 회차의 드라이브 읽기 회차(`drive.reads.run`)와 그 미리 읽기(`_prefetch_tree`: 루트 아래 4단계·최대 600개 목록, `require_listings`: 모든 미연결 의뢰 폴더)에 시간 한도가 없었고, 탐색 시간 한도는 의뢰 등록 단계 사이에서만 확인됐다. 작업자 큐가 밀리면 목록 호출마다 어댑터 대기 시간 초과(`DRIVE_BUSY queue wait timeout`)까지 기다렸고, 실패한 추측 목록은 기록되지 않아 다음 회차에서 다시 읽었다. 회차 수(`rounds`)는 탐색 한 번이 끝나야 갱신되어 진행도 보이지 않았다. 바뀐 점:
- **시간 한도**: 단계마다 전체 한도(`DISCOVER_BUDGET_SECONDS` 900초, `REINTERPRET_BUDGET_SECONDS` 1800초, `SYNC_BUDGET_SECONDS` 1800초, 의뢰 하나 `SCOPE_BUDGET_SECONDS` 300초)가 있고 드라이브 읽기 자체에 걸린다(`drive.reads.bounded`: 한도가 지나면 새 드라이브 호출을 하지 않고, 미리 읽기는 멈추고, 읽기 세션은 다음 회차를 마지막 회차로 돌려 가진 자료로 끝낸다 — 못 읽은 폴더는 `DRIVE_READ_LIMIT`, 남은 폴더는 `DISCOVERY_TIME_BUDGET`/`DISCOVERY_PHASE_TIME_LIMIT`, SYNC에서 남은 의뢰는 `sync_deferred`). 화면·자동 반영의 탐색도 자기 시간 한도 + 30초(`DRIVE_READ_SLACK_SECONDS`) 안에 드라이브 읽기를 끝내므로 새로고침이 그 실행을 기다리는 시간(`ROUND_WAIT_SECONDS` 120초)도 유한하다. 추측 목록은 60초(`LIST_PREFETCH_MAX_SECONDS`)·바쁜 드라이브(재시도 뒤 `DRIVE_BUSY`)에서 멈춘다.
- **화면과 겹치지 않기**: 새로고침이 도는 동안 화면의 60초 탐색(`POST …/discover`)은 드라이브를 읽지 않고 마지막 결과를 `status: RUNNING`으로 돌려주고, 서버 5분 자동 반영은 그 회차를 건너뛴다(새로고침이 같은 일을 한다).
- **`DRIVE_BUSY` 재시도**: 읽기 세션(백그라운드)·한도 안의 드라이브 호출은 `DRIVE_BUSY`(어댑터 `OVERLOADED`·`RATE_LIMITED`)를 2·5·10초 간격으로 3번까지 다시 시도하고(화면 요청은 1초 뒤 1번), 그래도 바쁘면 그 폴더의 확인 필요 항목·의뢰 실패·`drive.last_error`로 보고한다.
- **진행 표시**: `GET …/rescan`이 `progress`(`step`, `current_path`=지금 보는 프로젝트·의뢰, `projects_done`/`projects_total`, 의뢰 `done`/`total`), `drive`(`calls`, `listings`, `errors`, `retries`, `last_error`{code, operation, relative_path}), `elapsed_seconds`, `cancel_requested`를 준다. 버튼 옆에 `프로젝트 3/40 · <경로> · 드라이브 목록 120건 · 재시도 7회 · 최근 드라이브 오류 DRIVE_BUSY (<경로>) · 경과 14분 5초`를 보이고, 서버는 30초마다 `WARNING Storage rescan progress: status=… phase=… round=… elapsed=…s step=… projects=a/b requests=c/d current=… drive_calls=… listings=… drive_errors=… busy_retries=… last_drive_error=CODE:op:path …`를 `uvicorn-error.log`에 남긴다(시작·끝·중지·실패도 `Storage rescan started/done/cancelled/failed`; 로거가 WARNING 이상만 그 파일에 쓰므로 WARNING이다).
- **중지**: [중지] 버튼 = `POST …/rescan/cancel`(전역 관리자, 감사 `STORAGE_RESCAN_CANCELLED`, 실행 중이 아니면 `requested: false`). 회차·의뢰·단계 사이, 모든 드라이브 호출 전, 탐색 잠금 대기 중에 멈추고 상태는 `CANCELLED`(그때까지 찾은 결과는 남음)가 된다.

**드라이브 파일 이름(이슈 #55, 2026-10-21)**: `SPDM_INVALID_PATH "이 서버에서 쓸 수 없는 이름입니다(Windows 금지 문자·대소문자 중복)"`가 의뢰 확인 전체를 실패시켰다. 원인은 `DriveStorageProvider._file_version`의 `local_safe` 검사가 내용 지문(`content_token`, 드라이브 `version_token`만 쓰고 서버에 파일을 두지 않음)에도 걸린 것이다. 드라이브 이름은 Windows 경로가 아니므로 이제 이 검사는 **서버에 내려받을 때만** 적용한다: 대소문자만 다른 쌍둥이 이름은 다운로드마다 새 임시 폴더를 쓰므로 그대로 읽고, Windows가 담을 수 없는 이름(`<>:"\|?*`, 끝 공백·마침표, 예약 이름)은 내려받지 않는다(미리 받기도 건너뜀). 그런 파일·폴더는 Case 수집에서 `UNPROCESSED_FILE:<경로>:NAME_NOT_LOCAL_SAFE` 한 항목이 되고(로그 `Capture skipped a drive file … <경로>`), 대시보드가 경로로 쓸 수 없는 `:`·`\` 이름은 폴더 조사에서 빠지고 `skipped_entries`(`NAME_NOT_SUPPORTED`)와 시간당 한 번의 `Folder scan skipped a drive entry …` 로그로 남는다. 의뢰 확인은 실패하지 않는다. 내려받기에서 거절될 때의 메시지는 파일 경로를 담는다.

**이슈 #55 후속(2026-10-22, 결과 받기 작업)**: 운영 서버에서 Scene이 많은 의뢰가 매번 `DISCOVERY_TIME_BUDGET`, 다른 의뢰의 확인이 `FOLDER_SCHEMA_SCAN_INCOMPLETE …/FINAL (list_dir, DRIVE_READ_NOT_PREPARED)`였다. 등록과 확인이 결과 파일 전부를 조사와 같은 시간 한도(회차·의뢰 300초) 안에서 받았기 때문이다(다운로드 약 6초/건). SCX 드라이브 모드에서는 이제:

- 탐색의 등록과 의뢰 확인은 **목록만** 읽는다. Case는 바로 생기고 결과는 Case별 결과 받기 작업(`folder_environment_capture_jobs`, `PENDING`)이 받는다. 의뢰 확인의 분류는 의뢰 트리 전체 목록(FINAL 포함)을 다운로드보다 먼저 읽고, 내용 비교 다운로드는 한도 안에서만(못 한 것은 다음 확인) 한다. 그래서 다운로드가 느려도 등록·스냅샷은 끝나고 진행이 쌓인다.
- 새로고침 순서 마지막에 **CAPTURE** 단계(`CAPTURE_BUDGET_SECONDS` 900초): 대기 작업을 `capture_jobs.run_pending`으로 처리한다(등록 하나당 읽기 세션 하나, 300초). 끝나지 않은 작업은 실패가 아니라 `captures.pending`으로 남고 서버의 캡처 작업자가 이어서 받는다(이미 받은 파일은 보관소에서 다시 쓰고, 끝난 작업은 다시 하지 않음). 상태 `captures`: `total`·`pending`·`completed`·`failed`·`completed_now`·`interrupted`. 버튼 옆 `저장 폴더 확인 중 · 결과 받는 중 3/12`, 결과 요약 `결과 받는 중 n/N (백그라운드에서 계속 받습니다)`, 진행 로그 `captures_pending=n/N`.
- 화면은 그 Case를 없는 것이 아니라 **결과 받는 중**으로 보인다. 자세한 규칙: [SCX 드라이브 §8.9](scx-drive.md).

## scoped Refresh와 실패

`POST /api/folder-discovery/environments/refresh`는 `project_id`, `request_id`, `environment`를 받아 해당 의뢰 경계만 조사한다. 현재 storage root와 프로필 revision을 확인하고 제한된 scan, 구조·내용 fingerprint, 역할 판정을 마친 뒤 정상 snapshot을 활성화한다. 같은 fingerprint의 재시도는 기존 snapshot을 재사용한다. 내용이 바뀌어 capture가 새로 필요하면 과거 capture를 수정하지 않고 새 버전으로 보존한다. 내용 바이트 해시는 `.csv`, `.json`, `.jpg`, `.jpeg`, `.png`, `.mp4`, `.webm`, `.inc`, `.rad`에 적용한다. 그 외 확장자는 경로·크기·수정 시각으로 변화를 판정하므로 크기와 수정 시각이 모두 같게 바뀐 내용은 탐지하지 못할 수 있다.

한도 초과, 접근 오류, 역할 충돌, 프로필 변경 또는 작성 중 파일이 있으면 새 snapshot을 활성화하지 않는다. 마지막 정상 snapshot과 과거 capture는 유지하며 실패 이유를 표시한다. 결과 등록의 폴더 생성 또는 파일 mirror가 성공하면 같은 scope를 자동 Refresh한다. 자동 Refresh만 실패한 경우에는 폴더 준비나 DB 등록의 성공을 취소하지 않고 화면에 상태를 분리해 표시하며 재시도한다. 초안 업로드와 격리 검수는 자동 Refresh 대상이 아니다.

## 보존·검증 경계

사용자 파일을 이동·삭제하거나 기존 capture를 덮어쓰지 않는다. 새 migration·의존성·배포 진입점 없이 기존 스캔 이력을 사용한다. 재현과 회귀 검증에는 격리 DB와 합성 SPDM root만 사용한다. 실제 사내 DB·저장소 및 Windows Server 2022 폐쇄망 설치·업데이트·재부팅은 별도 현장 검증 대상이다.

## 삭제 뒤 옛 경로가 다시 보이지 않게 (2026-10-23)

- 드라이브 원본 버전(`drive_source_versions`)의 MISSING 행은 그 폴더를 목록에 계속 보이게 한다(등록 버전 유지). 이 오버레이는 이제 **존재하는 의뢰로 표시된 행만** 쓴다. 등록 삭제·프로젝트 정리는 지운 의뢰·프로젝트의 현재 행을 닫고(superseded) 표시를 지운다. 그래서 다시 등록된 폴더는 드라이브에 지금 있는 파일부터 새로 추적한다.
- 공백만 다른 철자로 등록된 행은 드라이브에 없는 행까지 실제 철자로 옮긴다(옛 철자 폴더가 루트 목록에 남지 않음).
- 그래도 예전 버전이 남긴 상태가 얽혀 있으면 관리자 「폴더 등록 전체 초기화」 뒤 [저장 폴더 새로고침]을 누른다. 표 전수 조사와 동작: [깊이 스키마 계약 §16.8](../contracts/depth-schema.md).

