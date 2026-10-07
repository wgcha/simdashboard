# 결과 등록 (W8: 폴더 안내 + 끌어서 올리기)

- 기준일: 2026-10-06
- 상태: 현재 구현 계약. 격리 임시 root·합성 데이터로 검증. 독립·보안 검수와 실제 Windows Server 2022·SMB 검증(W9)은 남음.
- 근거: [개선 로드맵](../plans/improvement-roadmap.md) §4 W8, [Case 결과 흐름 재설계](../plans/case-results-workflow-redesign.md) §8.6, [깊이 스키마](../contracts/depth-schema.md) §3·§5, [저장소 공급자](../contracts/storage-provider.md) S3 ③.

## 1. 결정 (2026-10-06 사용자)

해석자는 대부분 탐색기로 SPDM 공유 폴더에 결과를 직접 복사한다. 대시보드는 1분 안에(`folder_auto_sync`, 60초 주기 — 2026-10-07 30초에서 변경) 바뀐 폴더를 읽고 깊이로 역할을 정한다. 검수는 Final 지정으로 옮겼다. 그래서 결과 등록 화면은 **폴더 안내(A안) + 파일·폴더 끌어서 올리기**만 둔다. 초안·자동 검사·승인은 없다. 나중에 드라이브 탐색 프로그램과 Simcenter SDK가 연결될 예정이라 최소로 유지한다.

## 2. 화면 (`ResultDropWorkspace`)

1. **위치**: 공용 경로 표시줄(유통 Case › 하중경우 › Run Case › Run Option › Scene, 사용 Case › Scene). Working부터 아무 깊이나 고른다(선택하지 않은 깊이는 "여기까지"). 선택한 폴더의 탐색기 경로(UNC/드라이브)를 보여 주고 `경로 복사`(클립보드, HTTP LAN에서는 textarea 복사로 대체)로 탐색기 주소창에 붙여 넣게 한다. 브라우저는 탐색기를 열 수 없다. `새 폴더 만들기`는 선택한 폴더 바로 아래, Scene 깊이까지만 만든다.
2. **안내**: 깊이 사다리(Working → … → Scene → 결과 파일)에 선택 위치와 "여기 넣을 폴더"를 표시하고, "Run Option 폴더를 통째로 복사하려면 Run Case 폴더 아래에" 같은 규칙과 "1분 안에 자동 반영됩니다"를 보여 준다.
3. **올리기**: 파일이나 폴더를 끌어 놓거나 `파일 선택`/`폴더 선택`(`webkitdirectory`). 폴더는 `webkitGetAsEntry`로 따라가며 상대 구조와 빈 폴더를 유지한다. 서버 계획(올라갈 상대 경로, 개수, 총 크기, 새로 만들 폴더와 그 깊이 역할, 깊이 경고·차단, 충돌, 제외 파일)을 먼저 보여 주고 `올리기`. 진행률(파일 수·바이트·현재 파일)과 `중지`. 완료 후 폴더 확인(“방금 확인”과 같은 동기화)을 바로 실행하고 Case 결과 링크를 보여 준다.
4. **이전 등록 초안(읽기 전용)**: §6.

## 3. 검사 (쓰기 전에 모두)

| 항목 | 규칙 | 결과 |
|---|---|---|
| 대상 | 이 의뢰 폴더의 `Working` 아래(Working 자신 포함), 존재하는 일반 폴더, 경로 사슬에 링크·reparse 없음, Final·숨김 폴더 아님 | 422 `RESULT_DROP_TARGET_OUTSIDE_REQUEST`·`…_OUTSIDE_WORKING`·`…_TARGET_INVALID`, 409/422 `SPDM_PATH_UNSAFE` 등 |
| 소유권 | `result_registration_paths._owner_conflict`(다른 의뢰·환경의 Case·경로·연결·등록과 겹치면 거부) | 409 `RESULT_PATH_OWNERSHIP_CONFLICT` |
| 이름 | 상대 경로만, `/` 구분, `..`·`.`·빈 칸·절대·드라이브·`\`·`<>:"|?*`·제어 문자·끝 마침표/공백·CON 등 예약 이름(COM¹–³·LPT¹–³ 포함)·255자 초과·32단계 초과 금지(요청 전체 거부) | 422 `RESULT_DROP_PATH_INVALID` |
| 제외(건너뜀) | 실행·스크립트·셸 실행·디스크 이미지 형식(`DROP_BLOCKED_EXTENSIONS` = 등록 차단 목록 + `.scf .url .lnk .library-ms .searchConnector-ms .msc .iso .img .vhd .vhdx .appref-ms .settingcontent-ms .application .wsc .sct .chm .inf .xll .ps1xml .hta .cpl .reg .msix .appx .ms-appinstaller .diagcab .website .mht .cab .psd1`). 확장자는 끝 마침표·공백을 떼고 마지막 `.` 뒤로 판정(`.bat`처럼 점으로 시작하는 이름 포함, 대소문자 무시). `Thumbs.db`·`desktop.ini`·`.DS_Store`·`~$*`·`.simdash-upload`, 숨김 폴더(`.`·`$`·`~` 시작) 안 | 계획 `skipped`(사유 표시), 남는 것이 없으면 422 `RESULT_DROP_EMPTY` |
| 깊이(DEPTH_V1) | 새 폴더의 역할은 깊이로 정해진다. 부모와 같은 이름(INDIVIDUAL/INDIVIDUAL), 이 의뢰에서 다른 깊이의 이름(예: Scene 이름이 Run Option 자리에, 기본 RUN_OPTION 이름 INDIVIDUAL·CUMULATIVE 포함), Working·Final 이름은 **차단**(Scene 안 내용 폴더는 이름 제한 없음 — `final`·`Working` 하위 폴더도 허용). 숫자_로 시작하는 새 이름이 Scene 위 자리에 오면, Scene 위 깊이에 놓이는 파일, W6 비슷한 Scene 이름, 대소문자만 다른 기존 폴더는 **경고** | 계획 `issues[].severity` error/warning |
| 충돌 | 같은 이름(대소문자 무시) 파일이 이미 있거나 폴더 자리에 파일이 있으면 차단. 덮어쓰지 않는다 | 계획 `conflicts`, 시작 409 `RESULT_DROP_PLAN_BLOCKED` |
| 경로 길이 | 서버 경로와 표시 경로 모두 259자 이하 | `PATH_TOO_LONG` 차단 |
| 공간 | 남은 공간 − 같은 root의 열린 업로드가 아직 공개하지 않은 바이트(`reserved_bytes`) ≥ 합계 + max(5 %, 1 GiB). 세션 등록 직전 잠금 안에서 다시 확인 | `FREE_SPACE` 차단, 등록 시 507 `RESULT_DROP_FREE_SPACE` |
| 동시 업로드 | 열린(업로드 중·일부 공개) 업로드는 사용자당 의뢰마다 2개, 의뢰 전체 6개 | 429 `RESULT_DROP_BUSY` |

파일당·전체 크기 상한은 없다(남은 공간만). 항목 수는 요청당 파일·폴더 각 20,000개.

## 4. API (`/api/result-registration`, 모두 `result.import` 권한·의뢰 단위)

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| GET | `/drop-target` | `project_id, request_id, environment` | `working_relative_path, working_display_path, display_root, levels[{level, role, label}], nodes[{relative_path, name, parent_path, level, role, display_path}], truncated, chunk_bytes, blocked_extensions, active_uploads` |
| POST | `/drop-target/folders` | `{project_id, request_id, environment, parent_relative_path, name, confirm}` | 201 `{relative_path, display_path, level, role, role_label, warnings, sync}`. 422 `RESULT_DROP_FOLDER_NAME_INVALID`·`…_LEVEL_INVALID`(Scene 안)·`…_DEPTH_INVALID`, 409 `…_EXISTS`·`…_NAME_DUPLICATE`(대소문자·구분 기호만 다름)·`…_NAME_WARNING`(`warnings`; `confirm:true`로 다시) |
| POST | `/drop-uploads/plan` | `{project_id, request_id, environment, target_relative_path, files[{relative_path, size, sha256?}], folders[]}` | 계획(쓰기 없음): `target_*`, `files[{client_index, relative_path, destination_relative_path, size, role}]`, `folders_to_create[{relative_path, client_path, level, role, role_label}]`, `skipped`, `conflicts`, `issues[{code, severity, message, paths, count}]`, `file_count, folder_count, total_bytes, free_bytes, required_bytes, can_upload` |
| POST | `/drop-uploads` | plan과 같음 | 201 세션 `{session_id, state, chunk_bytes, files[{index, client_index, size, received, complete, published}], …, plan}`. 계획에 오류가 있으면 409 `RESULT_DROP_PLAN_BLOCKED`(`plan` 포함) |
| GET | `/drop-uploads` | `project_id, request_id` | 열린 업로드 `sessions[{session_id, state, user_id, own, target_relative_path, file_count, total_bytes, published_files, idle_seconds}]`(자기 것, 전역 관리자는 전부) |
| GET | `/drop-uploads/{id}` | – | 세션 상태(이어 올릴 `received`) |
| PUT | `/drop-uploads/{id}/files/{index}?offset=` | 본문 = 조각(≤ 8 MiB, `application/octet-stream`), 선택 헤더 `X-Chunk-SHA256` | `{index, received, size, complete, sha256}`. 409 `RESULT_DROP_OFFSET_MISMATCH`(`expected_offset`), 413 `…_CHUNK_TOO_LARGE`, 422 `…_CHUNK_HASH_MISMATCH`·`…_SIZE_EXCEEDED`·`…_HASH_MISMATCH`(선언한 파일 SHA-256과 다르면 임시 파일을 지우고 0부터) |
| POST | `/drop-uploads/{id}/complete` | – | `{state: PUBLISHED|PARTIAL, published_files, published_bytes, created_folders, conflicts, busy, skipped, sync{status,…}, cases[{case_relative_path, case_name, case_id}]}`. 409 `RESULT_DROP_INCOMPLETE`·`…_CONFLICT`(그 사이 생긴 파일, 아무것도 공개 안 함)·`…_SCOPE_CHANGED`·`…_STAGING_CHANGED` |
| DELETE | `/drop-uploads/{id}` | – | 이 세션의 임시 파일과 이 세션이 만든 빈 폴더만 지움. 만든 사용자 또는 전역 관리자(감사 `by_admin`) |
| GET | `/drafts` | `project_id, request_id, environment` | 이전 초안 목록(읽기 전용) |

세션은 만든 사용자만 쓸 수 있다(다른 사용자는 404, 전역 관리자는 중지만). 감사: `RESULT_DROP_UPLOAD_REFUSED`(시작 거부: 코드·계획 문제 코드), `RESULT_DROP_UPLOAD_STARTED`(대상·파일 수·바이트·폴더 수·제외 수), `RESULT_DROP_UPLOAD_PUBLISHED`/`…_PARTIAL`/`…_FAILED`, `RESULT_DROP_UPLOAD_ABORTED`, `RESULT_DROP_FOLDER_CREATED`.

## 5. 쓰기 경계와 공개

- 저장소 공급자의 새 쓰기 구역 `WORKING`(S3 ③): `<프로젝트>/<의뢰>/Working/**` — 첫 `Working` 위에 폴더 2단계 이상, 그 앞에 `Final` 없음, `.`·`..` 없음(Working 안쪽의 `final`·`Working` 이름은 내용으로 허용). 계획·대상 확인·새 폴더 만들기가 같은 규칙을 모든 대상·임시 경로에 미리 적용해 위반을 계획 단계에서 `PATH_NOT_ALLOWED`로 알린다. 호출 모듈 `app.services.result_drop_upload`만, 쓰기 함수는 `mkdir_pinned`·`write_chunk`·`rename_no_replace`·`remove`·`set_hidden`만(다른 쓰기는 `NOT_ALLOWED_WRITE`). 서비스는 다시 등록된 의뢰의 Working으로 한정한다.
- 임시 위치: `<의뢰>/Working/.simdash-upload/<세션 32hex>/<n>.part`(같은 볼륨, 공개 = 이름 바꾸기). Windows에서는 숨김 속성. 폴더 조사(`folder_discovery_scan.scan`·`browse`)는 이 폴더를 통째로 건너뛰어 노드·파일·지문·자동 동기화에 나타나지 않는다. 이름이 `.`로 시작해 깊이 스키마도 무시한다.
- 조각 쓰기 `LocalFsProvider.write_chunk`: 상위 사슬 고정·재확인, 링크 없이 연 핸들이 경로의 `lstat`과 같은 단일 연결 일반 파일, 현재 크기 = offset일 때만 쓰고 실패하면 offset으로 되자름. SHA-256은 서버가 받으면서 계산한다(브라우저는 WebCrypto가 있으면 조각 해시를 보냄; HTTP LAN에서는 생략).
- 같은 이름 확인은 부모 폴더마다 한 번 목록을 읽은 casefold 지도로 한다(계획·완료 각각 새로; 3,000개를 3,000개 있는 폴더에 넣는 계획 약 0.4초).
- 완료: 받은 크기 재확인 → 의뢰·Working·소유권 재확인 → 모든 대상의 충돌 재확인(있으면 아무것도 쓰지 않음) → 폴더를 얕은 순서로 하나씩 `mkdir_pinned`(고정된 부모 아래, 기존 일반 폴더는 그대로) → 파일마다 `rename_no_replace`(대상이 있으면 공개하지 않고 `conflicts`, 공유 위반은 재시도 후 `busy`). 의뢰별 공개 잠금. 모두 공개되면 세션 폴더를 지운다. 일부만 공개(PARTIAL)되면 폴더를 그대로 둔다(요청한 빈 폴더 포함). 실패·중지·만료 때만 이 세션이 만든 폴더 중 비어 있는 것을 지운다. 완료 중 대상 폴더를 읽지 못하면 503 `RESULT_DROP_TARGET_UNAVAILABLE`. 화면은 PARTIAL에서 `나머지 다시 옮기기`·`나머지 중지`를 보여 준다. 그다음 자동 동기화의 기억을 지우고 `sync(force=True)`.
- 중지·실패: 세션의 `<n>.part`와 빈 세션 폴더만 지운다. 기존 SPDM 파일은 덮어쓰거나 지우지 않는다.
- 세션은 서버 프로세스 메모리에 있다(새 DB 테이블·migration 없음). 60분 동안 움직임이 없는(업로드 중·일부 공개) 세션은 만료되어 임시 파일과 빈 새 폴더를 지운다. 재시작하면 업로드를 처음부터 다시 하고, 남은 임시 폴더는 같은 의뢰의 다음 업로드가 60분 지난 것만(`<n>.part`만) 정리한다. 화면은 업로드 중이거나 옮기는 중 페이지를 닫으려 하면 `beforeunload`로 확인을 묻고, 실제로 페이지를 떠날 때(`pagehide`) 조각 업로드 중이면 `keepalive` DELETE로 중지한다(옮기는 중이면 중지하지 않음). 다른 화면으로 가면 업로드를 멈추고 중지한다.
- **백엔드 작업자 1개 필요**: 세션·동시 업로드 제한·공개 잠금이 프로세스별이다. Windows 서비스(WinSW)는 uvicorn 작업자 1개라 맞다. Rocky 8 배포 템플릿은 `UVICORN_WORKERS=2`가 기본(`deploy/rocky8/install.env.example`)이므로 그 환경에서 끌어서 올리기를 쓰려면 `UVICORN_WORKERS=1`로 두거나 세션 저장을 공유 저장소로 옮겨야 한다(미결정, W9). 작업자가 2개면 조각 요청이 다른 작업자로 가서 404가 날 수 있다.
- 표시 경로: 환경 변수 `SIMDASH_SPDM_DISPLAY_ROOT`(예: `\\fileserver\SPDM`)가 있으면 그 뒤에, 없으면 서버의 SPDM root 경로 뒤에 상대 경로를 붙인다. 서버 경로와 사용자 PC의 공유 경로가 다르면 운영에서 이 값을 `.env`에 둔다.

## 5a. SCX 드라이브 모드 (D3, 2026-10-08)

SPDM 루트가 SCX 드라이브이면(`SIMDASH_DRIVE_GATEWAY=scx`) 드라이브 쓰기 허용(`SIMDASH_DRIVE_WRITES_ENABLED`)이 켜져 있을 때만 올릴 수 있다(꺼져 있으면 읽기 전용 안내). 조각은 드라이브가 아니라 **서버 임시 저장소**(`<SIMDASH_SCX_STAGING_DIR>/upload-<세션>/`)에 받고, 완료하면 드라이브 업로드 대기열에 묶음 하나(새 폴더 `mkdirs` → 파일마다 `upload_new`, 새 이름만)를 넣고 `state: QUEUED`로 응답한다. 검사(깊이·이름·충돌 거부)는 위와 같고 남은 공간은 서버 임시 저장소 기준이다. 화면은 대기열 진행(대기·진행·완료·충돌·일시 정지)을 보여 주며, 드라이브에 같은 이름이 생기면 덮어쓰지 않고 그 파일만 충돌로 남긴다. 반영은 묶음 완료 뒤 1분 자동 반영(드라이브 읽기 경로)이 한다. `경로 복사`는 숨긴다(드라이브 경로 표시). `새 폴더 만들기`도 대기열(`mkdirs`)로 만든다. 상세: [SCX 드라이브 기능 안내 §10.3](scx-drive.md#103-결과-등록끌어놓기--integration-05-53-w8).

## 6. 이전 등록 초안 (기존 흐름)

W8 이전 화면(대상 선택 → 저장 위치 → 초안 업로드(파일 32 MiB·초안 256 MiB) → 자동 검사 → 검수·승인 → capture 게시·폴더 mirror)은 화면에서 뺐다. 기존 초안·파일·검사·게시 기록과 `result_registration_*` 표는 그대로 보존하고 바꾸지 않는다. 결과 등록 탭 아래 `이전 등록 초안 (읽기 전용)`에서 목록(만든 때·상태·결과 위치·파일 수·Case 결과 링크)만 보여 준다(`GET /drafts`). 기존 초안 API(`/targets`, `/folders`, `/locations`, `/folders/prepare`, `/drafts…`)는 호환을 위해 남아 있으나 화면은 호출하지 않는다. 이 API의 계약·검증 기록은 [보관 계획](../archive/2026/result-registration-review-plan.md)에 있다. 마이그레이션 `0031_result_registration`, `0033_result_registration_location_links`는 그대로다.

## 7. 검증과 남은 위험

- 백엔드 `tests/test_result_drop_upload.py`(26): 폴더 구조 보존 → 동기화 후 Scene, 잘못된 깊이 차단, 기존 파일 충돌은 쓰기 전 거부, 업로드 중 생긴 충돌은 아무것도 공개 안 함, 경로 탈출·예약·절대 이름, Working 밖·다른 의뢰 소유 거부, 조각 이어 올리기·조각/파일 해시 불일치, 임시 폴더가 조사·자동 동기화에 안 보임, 링크 거부, 제외 파일, 권한 403, 공간 부족, 동시 업로드 제한·자기 임시 파일만 정리, 새 폴더 깊이·이름 규칙.
- 프런트 e2e `result-drop-upload.spec.ts`(API 모의), `spdm-storage-workflow.spec.ts`(실제 API로 새 화면·이전 흐름 미호출 확인).
- 독립 수동 보안 검수(2026-10-06, HIGH 없음)의 M1–M3·L1–L5를 반영했다(Codex Security는 미가용, 수동 검수로 대체). 반영 재검수와 Windows Server 2022·SMB(이름 바꾸기·고정 핸들·숨김 속성·백신 잠금, 수 GB 업로드)는 미수행.
- 알려진 한계: 작업자 여러 개(위 Rocky 8 기본값)에서는 동작하지 않는다. POSIX에서는 고정 핸들이 없어 확인과 쓰기 사이의 바꿔치기를 완전히 막지 못한다(W2와 같은 한계, Windows가 대상).
