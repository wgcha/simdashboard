# 알림 — 상단 알림 아이콘·알림 페이지

- 기준일: 2026-10-08 (사용자 요청 "상단에 알림 아이콘 및 알림 페이지 생성 그리고 글자크기조절 숨김")
- 상태: 구현(브랜치 `claude/scx-drive`)
- 코드: `backend/app/services/notifications.py`(기록·수신자·조회), `backend/app/routers/notifications.py`(API), migration `0040_notifications`(DuckDB `database.ensure_notifications_schema`), `frontend/src/features/notifications/`(아이콘·드롭다운·페이지), `frontend/src/app/workspace/NotificationsRoute.tsx`(경로), `frontend/src/shared/api/notifications.ts`
- 화면 기준: 데스크톱 전용(AGENTS.md 2026-09-20)

## 1. 화면

| 위치 | 동작 |
|---|---|
| 상단 막대 오른쪽(모든 작업 화면) | 종 아이콘 + 읽지 않은 수 배지(99 초과는 `99+`). 60초마다(자동 반영·드라이브 상태와 같은 간격)와 창이 다시 활성화될 때 `GET /api/notifications/unread-count`. 401·403이면 아이콘을 숨긴다 |
| 아이콘 클릭 | 최근 8건 드롭다운: 심각도 아이콘·제목·본문 2줄·종류·상대 시각. 항목 클릭 = 읽음 처리 후 관련 화면으로 이동(링크가 있을 때). [모두 읽음], [모두 보기] → `/workspace/notifications`. Esc·바깥 클릭으로 닫음 |
| `/workspace/notifications` | 목록(최신순, 50건씩 [더 보기]), 필터 `전체`/`읽지 않음`·종류, 항목별 [관련 화면]·[읽음], [모두 읽음], [새로고침]. 좌측 메뉴에는 넣지 않는다(`SIDEBAR_HIDDEN_MENUS`, 메뉴 정책 항목 아님). ACTIVE 계정이면 메뉴 정책과 무관하게 열린다(`MENU_POLICY_FREE_PAGES`) |

관련 화면 링크는 의뢰의 Case 결과 화면(`/workspace/requests?project=…&request=…&view=case_results`), 대기열 일시 정지는 결과 대시보드(`/workspace/overview`, 전역 관리자의 저장소 설정)다.

## 2. 이벤트와 수신자

수신자는 항상 **ACTIVE 계정**(`users.account_status='ACTIVE'`, `is_active`)이다. "의뢰 구성원" = 의뢰가 속한 프로젝트의 `project_memberships` 구성원 중 그 권한을 가진 사용자(`access_policy.permissions_for`: 역할 general/power/admin, 전역 관리자는 admin). 전역 관리자는 직접 한 작업이거나 관리 이벤트일 때만 받는다(모든 의뢰 알림을 받지 않음). 보는 권한(`project.data.view`)은 회사 권한으로 모든 ACTIVE 계정에 있으므로, 결과 등록 권한(`result.import`)이 필요한 이벤트는 power·admin 역할(또는 직접 시작한 전역 관리자)만 받는다.

| 종류(`type`) | 언제 | 수신자 | 심각도 | 중복 키 |
|---|---|---|---|---|
| `DRIVE_QUEUE_PAUSED` | 드라이브 대기열 항목이 `AUTH_REQUIRED`로 `BLOCKED` — 멈춰 있지 않던 대기열이 멈춘 순간 | 전역 관리자 | WARNING | `drive-queue-paused` |
| `DRIVE_UPLOAD` | 결과 등록(끌어놓기) 업로드 묶음이 끝남: `DONE`(파일 없는 새 폴더 묶음 제외)·`PARTIAL`·`CONFLICT`·`FAILED`·`CANCELLED` | 묶음을 시작한 사용자 | SUCCESS / WARNING / ERROR | `drive-upload:<묶음>:<상태>` |
| `FINAL` | 드라이브 Final이 `COMPLETE` 또는 `FAILED` | 지정한 사용자 + 의뢰 구성원(보기) | SUCCESS / ERROR | `final:<Final ID>:<상태>` |
| `FINAL_SUMMARY` | 현재 Final 요약(순번) 파일 묶음이 `FAILED`·`CONFLICT`·`CANCELLED`·`PARTIAL` — 화면의 "요약 파일 갱신" 필요 | 지정한 사용자 + 의뢰 구성원(`result.import`) | WARNING | `final-summary:<의뢰>`(합침) |
| `DRIVE_SOURCE_CHANGED` | 동기화가 새 원본 변경 확인 대기(`PENDING`)를 기록 | 의뢰 구성원(`result.import`) | WARNING | `drive-source-changed:<의뢰>`(합침) |
| `DRIVE_SOURCE_MISSING` | 동기화가 새 `MISSING` 원본을 기록 | 의뢰 구성원(`result.import`) | WARNING | `drive-source-missing:<의뢰>`(합침) |
| `NEW_RESULTS` | 자동 반영(60초 sync)의 전체 새로고침이 이전 활성 스냅숏에 없던 확정 Case·Scene(사용환경 평가 폴더 포함, Final 사본 제외)을 반영 | 의뢰 구성원(보기) | INFO | `new-results:<스냅숏>` — 동기화 1회·의뢰당 1건 |
| `CAPTURE_FAILED` | 폴더 등록(수동·자동 탐색)의 결과 캡처 작업 `FAILED`(등록 직후·다시 캡처) | 등록한 사용자·이번 실행자 + 의뢰 구성원(`result.import`) | ERROR | `capture-failed:<등록>`(합침) |

- 첫 등록(이전 활성 스냅숏 없음)·수동 새로고침·규칙 개정만의 재해석은 `NEW_RESULTS`를 만들지 않는다.
- local 모드 Final(파일 기반 복사 작업)은 DB 트랜잭션이 없어 이번 범위에서 알림을 만들지 않는다(scx 드라이브 Final만).

## 3. 기록 규칙

- **같은 트랜잭션**: 이벤트를 기록하는 코드가 그 연결로, 커밋 전에 `notifications.emit`(종류별 도우미)을 부른다 — 대기열 결과 기록(`upload_queue._apply`, 관리자 취소, 루트 변경 실패), Final 마무리(`case_finalization_drive._finalization_finished`)·요약 묶음 마무리 훅, 원본 분류 트랜잭션(`drive.sources.classify_request`), 새로고침 스냅숏 트랜잭션(`folder_discovery_environment.refresh_scope(notify_new_results=True)`, 자동 반영만), 캡처 작업 `FAILED` 기록. 알림 쓰기는 **DB만** 쓴다(드라이브·파일 접근 없음, 정적 시험).
- **실패해도 이벤트는 유지**: `emit`은 예외를 내지 않는다. PostgreSQL은 `SAVEPOINT`로 알림 쓰기만 되돌리고 경고 로그를 남긴다.
- **중복**: 같은 `(user_id, dedupe_key)`의 읽지 않은 행이 있으면 새로 만들지 않는다(키는 300자로 자른 값으로 찾는다; PostgreSQL은 읽지 않은 행의 고유 부분 색인 `notifications_unread_dedupe`와 `ON CONFLICT DO NOTHING`으로 동시 트랜잭션 경합도 막는다, migration 0041). "합침" 종류는 그 행의 제목·본문·시각을 갱신해 최신 상태 하나만 보인다. 읽은 뒤 같은 일이 다시 생기면 새 알림이 생긴다.
- **보관**: 90일(`RETENTION_DAYS`) 지난 행은 조회에서 숨기고 그 사용자의 다음 알림 기록 때 지운다(알림 한 번 기록에 한 번, 새로 쓴 사용자 전체를 묶어서; 넣기도 여러 행 한 문장). 사용자당 최신 1,000건(`MAX_PER_USER`)만 남긴다. 한 이벤트의 수신자는 최대 500명(넘으면 잘라 내고 경고 로그).
- **이벤트 보호**: 수신자·의뢰 제목·개수 조회도 기록과 같은 savepoint·`try` 안에서 실행한다(`_guarded`). 그 조회가 실패해도 알림만 빠지고 이벤트 트랜잭션은 계속된다(PostgreSQL). DuckDB 개발 DB에는 savepoint가 없다.
- **프로젝트 정리**: 알림은 파생된 사용자 표시 자료이므로 프로젝트 정리(`project_cleanup`)가 삭제하는 프로젝트·의뢰의 행(`project_id` 또는 `request_id` 일치)을 함께 지운다(`_STAGE_LEAVES`). 계정 삭제 정리는 이번 범위 밖(기존 계정 정리 정책을 따름).

## 4. API

| 메서드 | 경로 | 권한 | 내용 |
|---|---|---|---|
| GET | `/api/notifications?unread_only&type&limit(1–200, 기본 50)&offset` | ACTIVE 계정(자기 행만) | `{items, total, unread_count, limit, offset, types[{type,label}]}`. 알 수 없는 `type`은 422 `NOTIFICATION_TYPE_INVALID` |
| GET | `/api/notifications/unread-count` | ACTIVE 계정 | `{unread_count}` |
| POST | `/api/notifications/read` | ACTIVE 계정 | `{ids?: string[≤500], all?: bool}` → `{updated, unread_count}`. 다른 사용자의 id는 무시. 둘 다 없으면 422 `NOTIFICATION_READ_TARGET_REQUIRED` |

항목: `id, type, type_label, severity(INFO|SUCCESS|WARNING|ERROR), title, body, link, project_id, request_id, created_at, read_at, read`.

## 5. DB (migration `0040_notifications`)

`notifications(id PK, user_id, type, severity CHECK, title, body, link, project_id, request_id, dedupe_key, created_at, read_at)`, 색인 `(user_id, created_at)`·`(user_id, dedupe_key)`·`(project_id, request_id)`. 추가만 하는 migration(기존 표·행 변경 없음), 앱 역할 `SELECT/INSERT/UPDATE/DELETE` 권한(0036–0039와 같은 방식), PostgreSQL 기동 시 필수 표·열 검사에 포함. DuckDB 개발 DB는 기동 때 같은 표와 같은 색인 3개를 만든다(부분 색인은 없음). Migration `0041_folder_link_reservations`는 읽지 않은 행 고유 부분 색인 `notifications_unread_dedupe (user_id, dedupe_key) WHERE read_at IS NULL AND dedupe_key IS NOT NULL`을 추가한다 — 기존 행에 읽지 않은 중복이 있으면 행을 바꾸지 않고 색인만 건너뛴다(NOTICE; 앱은 조회 기반 중복 처리로 계속 동작). `project_id`·`request_id`는 외래 키 없는 표시용 값이다.

## 6. 전체 글자 크기 조절 숨김

좌측 메뉴 하단의 `전체 글자 크기 늘리기/줄이기` 버튼을 없앴다. 저장된 값(`localStorage` `simdashboard.workspace.font-size-pt.v1`, 11–18pt, 기본 14pt)은 그대로 읽어 적용하므로 이미 고른 크기는 바뀌지 않는다. 다른 탭에서 값이 바뀌면(`storage` 이벤트) 따라간다 — e2e 도우미 `setWorkspaceFontSize`가 이 경로로 크기를 바꿔 기존 배치 검사를 유지한다. 다시 보이려면 `AppSidebar.tsx`의 주석 위치에 버튼을 되돌린다.

## 7. 검증

- 백엔드 `tests/test_notifications.py`: 수신자(ACTIVE·구성원·역할 권한·전역 관리자), 중복·합침, 보관(90일·사용자당 상한), API(자기 행만·필터·읽음), 가짜 드라이브 어댑터로 업로드 완료/일부 완료·대기열 일시 정지·Final 완료/실패·요약 파일 충돌·원본 변경/없음·자동 반영 새 Scene·캡처 실패, 프로젝트 정리 삭제, migration·DuckDB 부트스트랩, 검수 수정(도우미 조회 실패의 savepoint 격리 L6, emit당 한 번 보관 정리 L7, 잘린 중복 키 조회·수신자 잘림 로그 L8, DuckDB 색인).
- e2e `frontend/e2e/notifications.spec.ts`: 아이콘·배지·드롭다운·관련 화면 이동·페이지 필터·읽음, 실제 API 빈 목록, 글자 크기 버튼 숨김과 저장값 적용.
