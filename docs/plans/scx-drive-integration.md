# SCX 드라이브 연동 계획 (어댑터 · UI · 브랜치)

- 기준일: 2026-10-07
- 상태: **결정 반영(2026-10-07 15:40). D0·D1·D2·D3 구현(2026-10-07~08, 브랜치, 검수 대기)** — 사용 안내는 [SCX 드라이브 기능 안내](../features/scx-drive.md)(D2 읽기 경로 §8, D3 쓰기 경로 §10), 배포 영향은 [ADR 0006](../adr/0006-scx-drive-adapter-external-wheel.md). D4(UI 나머지)·D5(배포·이관)·D6(수용 시험) 남음.
- 근거: GitHub 이슈 #45 본문과 코멘트(사내 `VD_scx_drive_adapter` 계약 v0.2, integration 00–07), [저장소 계층 계약](../contracts/storage-provider.md), [개선 전체 계획](improvement-roadmap.md)
- 작업 브랜치: `claude/scx-drive` (main `8db9f66`에서 분기)

## 1. 요약

SPDM 원본이 로컬·공유 폴더에서 **SCX 드라이브(AltairOne Drive)**로 옮겨 간다. 사내 어댑터(`scx_drive_adapter`)가 SDK를 별도 워커 프로세스에서 돌리고, 대시보드는 의존성 없는 클라이언트 `WorkerDriveGateway`만 쓴다. 대시보드는 이미 모든 폴더 접근을 `StorageProvider` 한 곳으로 모았으므로, **`DriveStorageProvider`를 추가하고 모드 설정(`SIMDASH_DRIVE_GATEWAY=none|scx`)으로 바꿔 끼운다.** 기본값은 `none`(지금과 동일)이라 main 운영에는 영향이 없다.

드라이브는 **삭제·이동·덮어쓰기가 없다**(어댑터 계약 P1, §2.5). 그래서 "임시로 쓰고 이름 바꾸기·교체하기"에 기대는 대시보드 기능은 드라이브 모드에서 다른 방식이 필요하다(§3).

## 2. 이슈 #45 문서 대비 현재 코드 차이 (2026-10-07 이후 변경분)

이슈 문서는 W2·W3·W4·W8 이전 코드를 기준으로 작성되었다. 다음은 재조정이 필요하다.

| 영역 | 이슈 문서 가정 | 현재 코드 | 드라이브 모드 대응 |
|---|---|---|---|
| Final 복사 | 원본 → Final 복사 후 `complete.json` | W2: `.finalizations/<op>/staging`에 조각 복사 → 폴더째 **이름 바꾸기**로 공개, 백그라운드 작업 | 드라이브 안 복사 `copy_within`(데이터가 서버를 거치지 않음) → 보고서 `upload_new` → 마지막 `complete.json`. 임시 폴더·이름 바꾸기 없음. 진행률·재시작 이어하기는 업로드 대기열로 |
| 현재 Final 요약 | 없음 | W3: `Final/current.json`을 **교체**(replace), 포인터 `designations.json` | 드라이브는 교체 불가 → §5 결정 D2 |
| 결과 등록 | 초안 → 승인 → 복사 | W8: 브라우저 조각 업로드 → `Working/.simdash-upload` 임시 → 이름 바꾸기 공개 | 서버 임시 저장소로 조각 업로드 → 업로드 대기열 `upload_new`. "경로 복사"(탐색기용)는 드라이브 모드에서 숨기고 드라이브 경로 표시로 대체 |
| 프로젝트 정리 | 없음 | W4: DB만 정리, SPDM 파일 불변 | 변경 없음(드라이브 쓰기 없음) |
| 직접 생성 지점 | `LocalFsProvider(` 69곳 | **102곳**(Final 52, 결과 등록 경로 12, 끌어놓기 10 등) | S1 주입 리팩터링 범위 확대 |
| migration 번호 | 다음 `0036` | 현재 최신 `0035` | 그대로 `0036`부터 |
| 단일 프로세스 | 워커 1개 강제 | W8 끌어놓기도 단일 프로세스 필요 | 같은 잠금(`dashboard.lock`)으로 함께 강제 |

## 3. 드라이브 모드에서 바뀌는 동작

| 기능 | local 모드(현재) | scx 모드 |
|---|---|---|
| 자동 탐색(60초)·자동 반영(60초, 2026-10-07 30초에서 변경) | 목록 + 크기·시각 지문, 변경 시 내용 해시 | 목록 + `version_token`(sha1). **주기 작업에서 파일 다운로드 없음** |
| Case 결과 수집 | 파일 직접 읽기 | 변경된 파일만 서버 임시 저장소로 받아 DB 등록 후 삭제. 32 MiB 초과는 서버 보관소(blob) |
| 원본 변경 | 새 결과로 자동 반영 | 이슈 Q3: **사용자 확인 후 새 버전 등록**("원본 변경됨 — 확인 필요"). §5 결정 D3 |
| 원본 삭제 | 결과에서 사라짐 | `MISSING` 표시, DB 기준본 유지 |
| 소재·물성 | 덱 직접 읽기 | 덱 다운로드 후 파싱 |
| 결과 등록(끌어놓기) | 공유폴더에 바로 공개 | 업로드 대기열, "드라이브 반영 대기/완료/충돌" 표시 |
| Final 지정 | 조각 복사 + 이름 바꾸기 | `copy_within` + `upload_new` 대기열. 잠금·계획·보고서 메타는 DB |
| 저장소 패널 업로드 | 사용 | 끔(`DRIVE_WRITE_DISABLED`) |

## 4. 구현 단계

이슈 문서의 S0–S4를 현재 코드에 맞춰 조정했다. 각 단계는 **별도 PR**로 `claude/scx-drive`에 쌓고, 단계마다 local 모드 회귀 시험을 통과해야 다음으로 간다.

| 단계 | 내용 | 대시보드 동작 변화 | 예상 |
|---|---|---|---|
| D0 | **기반**: 어댑터 클라이언트 연결부(`drive_gateway.py`: 모드 설정, `WorkerDriveGateway` 싱글턴, 단일 프로세스 잠금, 기동 검사), 가짜 게이트웨이(테스트용, 메모리 드라이브), migration `0036`(`drive_credentials`, `drive_source_versions`, `drive_upload_queue`, `drive_locks`, `finalization_operations`), 토큰 저장(`DbTokenStore`, Fernet 암호화, 동기 저장), 관리자 API(`/api/admin/drive/*`), 상태 API(`/api/drive/status`) | 없음(`none` 기본) | 2일 |
| D1 | **주입 리팩터링(local 모드)**: `LocalFsProvider(` 직접 생성 102곳 → 주입. 경계 시험을 "storage 패키지 밖 직접 생성 금지"로 강화. `materials_catalog`의 루트 우회 수정 | 없음 | 3–4일 |
| D2 | **읽기 경로**: `DriveStorageProvider` 읽기(목록·stat·안정 읽기·다운로드), `version_token` 지문, 다운로드→등록→삭제, 서버 보관소(blob), `drive_source_versions`, 원본 변경 확인 흐름, `MISSING` 처리 | scx 모드 조회 동작 | 4–5일 |
| D3 | **쓰기 경로**: 업로드 대기열 작업자, 결과 등록(끌어놓기) 대기열화, Final 재설계(`copy_within`, DB 메타·잠금, 보고서 업로드, `complete.json` 마지막), 현재 Final 요약(D2 결정안) | scx 모드 전체 동작 | 4–5일 |
| D4 | **UI**: §6 전체 | — | 2–3일(D0–D3과 병행) |
| D5 | **배포·이관**: 대시보드 오프라인 wheelhouse에 어댑터 wheel(`--no-deps`), 워커 런타임 설치 단계(선택), 설정 안내, 로컬→드라이브 재연결 이관 명령(드라이 런 → 실행, 이슈 Q1), 경로 비교 점검 명령 | — | 2일 |
| D6 | **수용 시험**: 이슈 07의 S0–S3를 테스트용 드라이브 공간에서 수행, 측정 기록 | — | 현장 |

합계 약 17–21일(현장 시험 제외).

### D0 구현 결과 (2026-10-07)

| 항목 | 결과 |
|---|---|
| 모드·기동 검사 | `services/drive/config.py`·`gateway.py`: `none` 기본(동작 불변, 어댑터 import 안 함). `scx`에서 필수 설정 누락·형식 오류, 어댑터 미설치, `dashboard.lock` 선점 시 기동 거부. `WorkerDriveGateway` 싱글턴 지연 생성(`drive_root="~"`), lifespan 종료 시 `close()` |
| 토큰 | `DbTokenStore`(Fernet, `key_id`=키 sha256 앞 8자, 키 변경·복호화 실패 → 없음, 워커 스레드 `save()` 자체 연결 즉시 커밋, 마이크로초 보존) |
| DB | migration `0036_drive_credentials`는 **`drive_credentials`만** 만든다. 위 표의 `drive_source_versions`·`drive_upload_queue`·`drive_locks`·`finalization_operations`는 쓰는 단계(D2·D3)에서 각자 migration으로 추가한다(쓰지 않는 빈 테이블을 미리 배포하지 않음) |
| API | `/api/admin/drive/{status,credentials,test,check}`(전역 관리자), `/api/drive/status`(로그인 사용자) |
| 드라이브 점검(D7) | **조회 전용**(2026-10-07 20:12 결정): 시험 폴더에서 stat → list_dir → stat → download_to(8 MiB 이하, 서버 사본 삭제) → health. 드라이브 쓰기 없음. C2·C7은 D3 쓰기 수용 시험으로 이관 |
| UI | 저장소 설정 › SCX 드라이브 › 드라이브 관리 대화상자, 전체 상단 배너(60초 조회), `DRIVE_*` 한글 문구 |
| 시험 | 가짜 어댑터(`backend/tests/drive_fakes.py`, 시험 전용) |
| 남은 배포 과제(D5) | 외부 wheel 보존 폴더(`external-wheels`, 서버 `state\external-wheels`)로 sync·새 venv 뒤 어댑터 재설치 — 2026-10-07 반영(ADR 0006). 워커 런타임 설치 단계·서비스 환경변수 안내는 남음 |

### D1 구현 결과 (2026-10-07)

| 항목 | 결과 |
|---|---|
| 주입점 | `services/storage/factory.py`의 `provider_for_root(root)` 하나. `get_storage_provider(conn)`도 이것을 거친다. 지금은 `LocalFsProvider(root)`를 그대로 돌려준다(동작 불변). D2에서 scx 모드면 드라이브 공급자를 돌려주도록 이곳만 바꾼다 |
| 시험용 교체 | `set_provider_factory(f)`(이전 값 반환, `None`은 기본 복원)·`override_provider_factory(f)` 컨텍스트 매니저 |
| 바꾼 곳 | storage 패키지 밖 `LocalFsProvider(` 직접 생성 105회(12개 모듈) → `provider_for_root(`. 공개 함수 시그니처·쓰기 구역·LEGACY 호출 모듈 검사·고정(pinned) 쓰기는 그대로. 형 표기(`fs: LocalFsProvider`)는 생성이 아니므로 남김 |
| `materials_catalog` | 덱 파일을 `LocalFsProvider(path.parent)`(파일의 부모 폴더를 루트로)로 열던 우회를 없애고 설정 루트 공급자의 상대 경로로 읽는다(`_open_deck(fs, relative)`, `_file_roles(fs, relative, …)`, `_scan_include_references(fs, relative, …, source=)`). 공급자는 함수마다 한 번만 만든다 |
| `folder_schema_resolver` | 파일마다 만들던 공급자를 `scan_fingerprints` 한 번으로 |
| 경계 시험 | `test_storage_provider_boundary.py`: storage 패키지 밖 공급자 클래스 직접 생성 시 실패, 팩터리 교체가 `provider_for_root`·서비스 호출에 닿는지 확인 |
| 자동 반영 주기 | §9 D3에 따라 local 모드도 60초: 화면 `FOLDER_AUTO_SYNC_INTERVAL_MS`·`FOLDER_PROGRESS_POLL_MS` 60 000, 서버 진척 메모 `MEMO_SECONDS` 60, 안내 문구 "1분 안에". 서버 합치기 간격(20초/5초)은 그대로 |

### D2 구현 결과 (2026-10-07)

상세는 [기능 안내 §8](../features/scx-drive.md#8-읽기-경로-d2).

| 항목 | 결과 |
|---|---|
| 공급자 | `storage/drive.py` `DriveRoot`·`DriveStorageProvider`. scx 모드의 `spdm_storage.storage_root()`는 `SIMDASH_SCX_DRIVE_ROOT`의 `DriveRoot`를 돌려주고 `provider_for_root`가 드라이브 공급자를 만든다(local 모드 불변). 읽기는 `list_dir`·`stat`·`download_to`만, 쓰기 메서드는 모두 `DRIVE_WRITE_DISABLED`. `root_identity`는 경로 기준 `scx:<host>:/<루트>`(integration 03 §5의 항목 id 대신 — 루트가 환경변수로 고정되고, 연결을 쥔 호출부에서 드라이브 호출 없이 계산해야 함) |
| 연결 규칙 | `database_connection.connection_held()` 추적. 읽기 세션(`drive/reads.py` `run`, 라우트 데코레이터 `read_session`): 연결을 쥔 채 없는 자료를 찾으면 `DriveReadMiss`로 연결을 닫고 연결 없이 받은 뒤 본문 재실행(최대 8회). 쓰기 뒤 읽는 흐름(등록 캡처·재시도·새로고침 캡처)은 쓰기 전에 `require_content`. 세션 밖 연결 보유 중 읽기는 `DRIVE_READ_NOT_PREPARED`. 32개 화면 API·자동 탐색·자동 반영을 감쌈 |
| 파일 흐름 | 세션별 `staging/read-<id>/`에 받고 끝나면 삭제(05 §2), 32 MiB 초과는 서버 보관소(`SIMDASH_DRIVE_BLOB_DIR`, sha256 이름, 100 GiB 상한 LRU, 05 §3). 내용 지문은 `version_token`으로 대체해 변경 없는 자동 반영은 다운로드 0회(S2-5 시험) |
| version_token | sha1 있으면 `sha1:<hex>:<size>`, 없으면 `t:<size>:<µs>`. 비교는 양쪽 sha1이면 sha1, 아니면 크기·시각 → 등록 sha256과 내용 비교. C1·C9 미확인이라 둘 다 지원하고 관찰 형태를 관리 상태 `version_tokens`·자동 반영 `drive.token_kind`·로그에 기록 |
| DB | migration `0037_drive_source_versions`(추가 전용, 앱 역할 GRANT, 현재 행 부분 유일 색인), DuckDB DDL 동일(부분 색인 없음 — 작성 코드와 버전 유일 키로 유지), PG 기동 필수 테이블·열 검사 |
| 자동 반영(§9 D3) | 60초 `/sync`(scx): 의뢰별 드라이브 잠금 → 분류(새 파일·새 Scene 자동 버전 1, 기존 파일 변경은 `PENDING`, 삭제는 `MISSING`) → 기존 빠른 확인·새로고침(등록 버전 기준 보기) → `drive` 개수. 변경 대기·무시·원본 없음 파일은 등록 버전으로 계속 보이고 내용은 보관소·DB에서 꺼냄. local 모드 동작 불변 |
| 원본 변경 API | `GET /api/drive/source-changes`(의뢰 조회 권한), `POST …/accept`·`…/dismiss`(의뢰 `result.import`, 전역 관리자 포함, 감사 기록). 새 버전 등록은 `version_no+1` 추가·이전 행 보존, 다음 자동 반영이 그 파일만 받아 캡처 |
| 쓰기 허용 설정 | `SIMDASH_DRIVE_WRITES_ENABLED`(기본 false) — 상태 API·관리 화면 표시만, D2 쓰기는 항상 불가. 쓰기 API(결과 등록 폴더·업로드·복사, Final 지정·보고서·요약, 저장소 패널 연결·업로드)는 scx에서 409 `DRIVE_WRITE_DISABLED` |
| UI | 폴더 자동 확인 줄의 `원본 변경 n · 확인 필요 · 원본 없음 m` 배지 → 확인 대화상자(파일별·전체 새 버전 등록/무시). 읽기 전용 안내와 결과 등록 폴더 만들기·올리기, Final 지정·재시도·요약 갱신, 저장소 패널 업로드·새로고침 비활성. 관리 화면에 쓰기 허용 설정·원본 지문 형태 |
| 시험 | 가짜 게이트웨이 확장(sha1 목록/조회별 유무, `modify_file`·`touch`·`delete`, `fail_always`, 호출 시 연결 보유 감시). `test_drive_reads.py` 18건(DuckDB; 임시 PostgreSQL에서는 DuckDB 전용 1건 건너뜀), e2e `drive-read-path` 2건 |
| 남은 일 | D3 쓰기 전부, 저장소 패널 다운로드·결과 등록 초안 API의 읽기 세션화, 03 8-3 sha1 체크섬, "사용 중"(BUSY 3회) 배지, 화면 폴더 선택기·대용량 영상 로딩 표시(D4), 실제 드라이브 C1·C9 확인(D6) |

### D3 구현 결과 (2026-10-08)

상세는 [기능 안내 §10](../features/scx-drive.md#10-쓰기-경로-d3).

| 항목 | 결과 |
|---|---|
| 쓰기 허용 | `SIMDASH_DRIVE_WRITES_ENABLED`(기본 false): 꺼짐이면 D2와 같이 409 `DRIVE_WRITE_DISABLED`. 켜짐 + SPDM 루트면 결과 등록(끌어놓기·새 폴더)·Final 지정이 드라이브에 쓴다. 결과 등록 초안(폴더 준비·게시·복사 재시도)·저장소 패널(새로고침·연결·업로드)은 scx에서 항상 409(`require_local_writes`) |
| DB | migration `0038_drive_write_path`(추가 전용, 앱 역할 GRANT): `drive_upload_queue`(04 §2.3 + `root_key`·`src_version_token`·`halt_on_error`·`transfer_method`·`result_*`·범위 열·`CANCELLED` 상태), `drive_locks`(04 §2.4), `finalization_operations`(04 §2.5 + 범위·`report_formats`·`complete_sha256`·`confirmed_at`·`designation_seq`·`designation_batch_id`·오류). DuckDB DDL 동일, PG 기동 필수 테이블·열 |
| 대기열 | `services/drive/upload_queue.py`: 유일한 드라이브 쓰기 모듈(`mkdirs`·`copy_within`·`upload_new`, 확인 `stat`, 대체 `download_to`). 단일 작업자 스레드(앱 수명), 기동 시 `RUNNING`→`PENDING`. CONFLICT는 `stat` 비교(sha1, 이전 시도 있으면 크기)로 같은 내용만 `DONE`, 아니면 `CONFLICT`. 재시도 전 `stat`, 재시도 가능 오류 30 s→2 m→10 m→30 m→1 h(`SIMDASH_DRIVE_UPLOAD_MAX_ATTEMPTS` 8), `AUTH_REQUIRED`는 `BLOCKED`로 대기열 정지·토큰 등록 시 재개. `halt_on_error` 항목 정지 시 묶음 정지. 드라이브 호출 중 DB 연결 없음. 임시 파일은 `DONE` 뒤 삭제, 하루 지난 고아 정리 |
| C7 대체 | `copy_within`이 `INTERNAL`(또는 메서드 없음)이면 프로세스 안에 "미지원"을 기록하고 `download_to`→`upload_new`로 대체(`INVALID_PATH`·`FORBIDDEN`은 그 항목만 대체 시도). 항목·완료 기록의 `transfer_method`와 경고 로그 |
| 결과 등록 | 조각은 서버 `upload-<세션>/`에, 완료는 묶음(MKDIR… → FILE…) 등록 후 `QUEUED` 응답. 충돌 규칙은 local과 같음(이름 바꾸기·덮어쓰기 없음). 새 폴더는 `MKDIR` 묶음 + 20초 대기. 묶음 완료 시 자동 반영 캐시 비움 → 다음 60초 동기화(D2)가 반영 |
| Final | `services/case_finalization_drive.py`: 계획·보고서·완료 DB, `drive_locks` 의뢰 잠금(묶음 끝까지), 묶음 MKDIR → COPY → 보고서 FILE → `plan.json` → `complete.json`(마지막, 앞 항목 완료 뒤 생성). 파일별 `version_token` 고정(확정·복사 직전 재확인), 원본 변경 확인 대기 파일 거부. 같은 Final ID 재시도는 멈춘 항목만. 상태·진척은 DB, `verification: DRIVE_UPLOAD` |
| 현재 Final 요약 | 결정 D2 ①: `Final/.finalizations/designations/<8자리 순번>-<Final ID>.json` 추가 전용(완료·요약 갱신마다 새 순번, 가장 큰 순번 = 현재). SPDM 안내는 기능 안내 §10.5 |
| 수용 보조 | 관리자 점검은 조회 전용 유지. C2·C7은 전용 시험 폴더에서 일반 기능으로 확인하는 수동 절차(기능 안내 §10.6) — 새 자동 쓰기 점검 없음 |
| UI | 결과 등록 대기열 진행(`DriveBatchProgress`), Final 배지·창 "드라이브 반영 중/일시 정지", "드라이브 반영 기록"·"요약 파일 드라이브 반영 중", 관리 › 업로드 대기열(목록·다시 시도·취소·재개), 저장소 패널은 scx에서 항상 업로드 안내 |
| D1 검수 낮음 | `result_drop_upload.py` 모듈 설명·주석과 `folder_request_progress.folder_progress` 설명의 30초 → 60초, `tests/conftest.py` 공급자 팩터리 원복 autouse 고정구 |
| 남은 일 | 실제 드라이브 C1·C2·C7·C9(D6, §10.6 절차), Final 바이트 단위 진척, 결과 등록 초안·저장소 패널의 드라이브 쓰기(계획 없음), SPDM 협의(순번 파일·`sha256` null) |

## 5. 결정 필요 사항

| 번호 | 내용 | 제안 |
|---|---|---|
| D1 | 브랜치 운영 | `claude/scx-drive`에 단계별로 쌓고, **D1(리팩터링)까지는 main에 먼저 합칠 수 있다**(동작 변화 없음). D2 이후는 수용 시험(D6) 통과 후 main에 합친다. 기본 모드는 계속 `none` |
| D2 | 현재 Final 요약 파일(`current.json`) | 드라이브는 교체가 안 되므로 ① 요약을 **새 파일로 추가**(`Final/.finalizations/designations/<순번>-<Final ID>.json`, SPDM은 가장 큰 순번을 읽음) ② 현재 Final은 **DB만**에 두고 드라이브에는 각 Final의 `complete.json`만 ③ SPDM이 대시보드 API로 조회. **①을 제안**(폴더만 보고 판단 가능, 덮어쓰기 없음). SPDM 협의 필요 |
| D3 | 원본 변경 시 동작 | 이슈 Q3는 "항상 사용자 확인". 지금 local 모드는 30초 자동 반영이다. 제안: **새 파일·새 Scene은 자동 반영, 기존 파일 내용 변경만 확인 필요**. local 모드는 지금처럼 자동 유지 |
| D4 | 워커 런타임 설치를 대시보드 `install.ps1`/`update.bat`에 넣을지 | 넣는다(선택 단계, 패키지 있을 때만). 배포 정책·ADR 갱신 동반 |
| D5 | local 모드 유지 | 개발·시험·폐쇄망 대체용으로 **유지** |
| D6 | 어댑터 wheel 공급 | 사내 저장소에서 빌드한 어댑터 wheel(표준 라이브러리만)을 대시보드 저장소에 넣을지, 배포 시 별도 경로로 받을지. SDK wheel(`siemens_scx`)은 공개 저장소에 넣지 않는다 |
| D7 | 개발 시험 환경 | 이 개발 환경에서는 SDK·드라이브에 접근할 수 없다. **가짜 게이트웨이**로 개발·자동 시험하고, 실제 드라이브 확인은 사용자 PC(워커 런타임 설치)에서 D6로 한다 |

## 6. UI 개선

| 위치 | 내용 | 단계 |
|---|---|---|
| 관리 › 저장소 설정 | **드라이브 카드**: 모드·서버·루트, 연결 상태(OK/불안정/연결 안 됨/재로그인 필요), 토큰 등록(붙여넣기)·삭제, 연결 시험, 워커 버전 | D0·D4 |
| 관리 › 저장소 설정 | **드라이브 폴더 선택기**로 SPDM 루트 지정(환경변수 고정 시 읽기 전용) | D2·D4 |
| 전체 상단 | 드라이브 상태 배너(연결 안 됨 / 인증 필요), 60초 조회 | D0·D4 |
| Case 결과 | 원본 상태 배지(`변경됨 — 확인 필요`, `원본 없음`, `사용 중`), 의뢰 단위 **[새 버전 등록] / [무시]**, "마지막 동기화 n분 전" | D2·D4 |
| Case 결과 | 32 MiB 초과 영상 열 때 "드라이브에서 불러오는 중" | D2·D4 |
| 결과 등록 | 드라이브 모드: "경로 복사" 대신 드라이브 경로 표시, 끌어놓기 후 **대기열 상태**(대기·진행·완료·충돌·실패) | D3·D4 |
| Final 지정 | 진행률을 대기열 묶음 상태로 표시, "드라이브 반영 중", 실패 시 관리자 재시도 | D3·D4 |
| 저장소 패널 | 업로드 버튼 숨김 | D2·D4 |
| 오류 문구 | `DRIVE_*` 코드 한글 문구 사전 | D0·D4 |

## 7. 브랜치·배포 절차

1. 작업 브랜치 `claude/scx-drive`(main에서 분기). 단계별 커밋·PR, 단계마다 독립 검수(인증·비밀정보·쓰기 경계·DB migration은 고위험).
2. main은 수용 시험 전까지 건드리지 않는다(사용자 결정 2026-10-07 15:57). main 쪽 수정이 생기면 이 브랜치로 가져와 맞춘다.
3. D2–D3는 브랜치에서만. 사용자 PC 시험은 PC 저장소를 이 브랜치로 바꾼 뒤 `update.bat` 실행(업데이트는 현재 브랜치를 따른다). 전환 방법은 D5에서 안내 문서로 제공한다.
4. 수용 시험(D6) 통과 후 main에 합치고 `SIMDASH_DRIVE_GATEWAY=scx`로 전환. 전환 전 로컬 데이터는 재연결 이관(이슈 Q1).
5. 모든 단계에서 기본 모드 `none` 유지, migration은 빈 설치·기존 DB 업데이트 모두 검증(배포 계약 3·5항).

## 8. 위험

| 위험 | 대응 |
|---|---|
| `ls`에 sha1이 없을 수 있음(이슈 C9) | 크기·시각 지문으로 후퇴, 변경 의심 파일만 다운로드 |
| 드라이브 응답 지연으로 60초 동기화·탐색 상한 초과 | 어댑터 캐시·우선순위(INTERACTIVE/BACKGROUND), 측정 후 주기 조정 |
| refresh 토큰 회전·저장 실패 | `save()` 동기 커밋, 실패 시 관리자 알림, 재시작 3회 시험 |
| 덮어쓰기 금지 업로드의 원자성 미보장(이슈 §2.4) | 재시도 전 `stat` 확인, 같은 내용이면 완료, 다르면 충돌로 정지 |
| 대규모 리팩터링(102곳) 회귀 | D1을 동작 변화 없는 독립 단계로, 전체 회귀 시험 |
| W2·W3·W8 로컬 전용 설계와 드라이브 설계의 이중 경로 | provider 능력 플래그(`supports_rename`, `supports_replace`)로 분기, 공통 상위 로직 유지 |

## 9. 사용자 결정 (2026-10-07 15:40)

| 번호 | 결정 |
|---|---|
| D1 브랜치 | **전 단계(D0–D6)를 `claude/scx-drive`에서만 개발하고 main은 건드리지 않는다**(2026-10-07 15:57 변경: 현재 잘 동작하는 main 보존). 리팩터링(D1)도 main에 먼저 합치지 않는다. 수용 시험 통과 후 사용자 확인을 받아 한 번에 main에 합친다 |
| D2 현재 Final 요약 | 제안 ①(순번 파일 추가 방식)으로 먼저 구현하고, 이후 SPDM 협의 결과에 맞춘다 |
| D3 자동 반영 | **자동 반영 주기 60초**(30초 → 60초, local·scx 공통으로 적용; local 반영은 D1에서 완료), 새 파일·새 Scene은 자동, **기존 파일 내용 변경만 사용자 확인** |
| D6 어댑터 wheel | 저장소에 넣지 않는다. 사용자가 서버·PC에 따로 복사해 설치하고, 대시보드는 설치된 패키지를 찾는다(없으면 scx 모드 기동 거부, 안내 메시지) |
| D7 시험 방식 | 실제 어댑터로 사용자가 반복 시험한다. 가짜 드라이브는 자동 회귀 시험용으로만 두고 배포물에는 포함하지 않는다. 실제 환경 점검을 쉽게 하도록 **관리자 "드라이브 점검" 기능**(지정한 시험 폴더에서 목록·조회·다운로드를 차례로 실행하고 결과·지연을 표로 보여 줌)을 D0에 추가한다. **2026-10-07 20:12 변경: 점검은 조회만 하고 드라이브에 쓰지 않는다**(업로드·폴더 생성·복사 단계 삭제, 옵션 없음). C2·C7은 D3에서 전용 시험 공간을 쓰는 명시적 쓰기 수용 시험으로 확인한다 |
| 진행·시험 버전 | 2026-10-07 20:25 사용자 결정: D1–D3를 이어서 구현하고 시험 버전은 한 번에 제공. 드라이브 쓰기 허용 설정(기본 꺼짐)으로 D2(읽기) 확인 후 D3(쓰기)를 전용 시험 폴더에서 확인 |
