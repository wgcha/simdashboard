# SCX relay — 대시보드 내부 API 계약 (v1, 2026-10-17)

> 원본: `physicsai-platform/docs/contracts/scx_relay.md`. 대시보드 구현 에이전트가 `simdashboard/docs/features/scx-relay.md`로 복사한다(내용 변경은 양쪽 동시에, 원본이 정본).
> PhysicsAI 쪽 사용법은 `physicsai-platform/docs/contracts/phase3.md` §16, 변경 메모 D3-57.
> 기준: 대시보드 `origin/claude/scx-drive`(6b7a70c), 어댑터 `scx_drive_adapter` 계약 v0.2(드라이브) + v0.3.0(JobGateway).

## 0. 결정(사용자, 2026-10-17)

- PhysicsAI는 **대시보드의 SCX 연결(공용 계정 토큰)을 같이 쓴다**. refresh token이 회전하고 대시보드가 `<SIMDASH_SCX_WORK_DIR>/dashboard.lock`으로 어댑터 워커를 단일 프로세스로 잡고 있으므로, PhysicsAI는 **자기 어댑터 워커를 띄우지 않는다**. 어댑터의 유일한 소유자는 대시보드이고, 대시보드가 loopback 전용 내부 HTTP API("SCX relay")를 연다.
- 드라이브 배치: 공용 계정 홈(`/Users/vd.dashboard` = 어댑터 `drive_root="~"`) 아래 `<프로젝트>/<잡 폴더>/`. 잡 폴더는 **항상 새로** 만들고, 드라이브에서 **덮어쓰기·삭제·이동은 없다**(P1).
- 범위: PhysicsAI ① `TD_SOLVE`, ④ `PREDICT_VERIFY`의 Radioss 제출.

## 1. 원칙

1. **어댑터 호출은 대시보드 프로세스 안에서만.** relay는 기존 `gateway.get_drive_gateway()`의 단일 `WorkerDriveGateway`를 쓴다(새 워커·새 토큰 저장소 없음).
2. **드라이브 쓰기는 기존 업로드 대기열(`upload_queue.py`)로만.** relay 모듈은 `upload_new`·`mkdirs`·`copy_within`을 직접 부르지 않는다(기존 정적 시험 "쓰기 메서드를 부르는 모듈은 `upload_queue.py` 하나" 유지). 대기열 규칙(덮어쓰기·삭제·이동 없음, CONFLICT 시 같은 내용이면 DONE, 재시도 가능 코드 백오프, `AUTH_REQUIRED` → `BLOCKED`·대기열 일시정지) 그대로.
3. **읽기는 `list_dir`·`stat`·`download_to`만**, 잡은 `submit_radioss`·`get_job`·`terminate_job`만.
4. **제출은 자동 재시도하지 않는다.** 응답을 잃은 제출(`SUBMISSION_UNKNOWN`)은 사람이 확인한다. 같은 `request_id` 재요청은 어댑터를 다시 부르지 않고 기록된 결과를 돌려준다(§6.1).
5. **SPDM 마스터 루트(`SIMDASH_SCX_DRIVE_ROOT`)에는 relay로 쓸 수 없다**(예외 설정 없음, §4).
6. 기본 꺼짐. 켜도 loopback + 공유 비밀 헤더 두 조건을 모두 만족해야 한다.

## 2. 설정(대시보드 환경변수, 기본 꺼짐)

| 변수 | 기본 | 설명 |
|---|---|---|
| `SIMDASH_SCX_RELAY_ENABLED` | `false` | relay 켜기. `SIMDASH_DRIVE_GATEWAY=scx`일 때만 의미. 끄면 모든 relay 경로 404 `RELAY_DISABLED` |
| `SIMDASH_SCX_RELAY_SECRET_FILE` | (켜면 필수) | 공유 비밀 파일 **절대 경로**. 권장 위치 = 대시보드의 git 추적 밖 런타임 비밀 폴더(예 `<설치 루트>\state\secrets\scx_relay.key`), ACL 현재 사용자·SYSTEM만. 파일 내용 = 한 줄 base64url 43~128자(앞뒤 공백·BOM 무시). 형식 불일치·읽기 실패면 기동 거부(값은 메시지에 넣지 않음) |
| `SIMDASH_SCX_RELAY_DRIVE_BASE` | `""` | relay가 쓰기·읽기를 허용하는 기준 폴더(공용 계정 홈 기준 상대 경로, `validate_drive_rel_path` 규칙, 빈 값 = 홈 `/Users/vd.dashboard`) |
| `SIMDASH_SCX_RELAY_JOB_PATH_FORM` | `rel` | `submit_radioss`에 넘기는 경로 표기: `rel`(`cushion/01_…/a.rad`) · `tilde`(`~/cushion/…`) · `absolute`(`<HOME_ABS>/cushion/…`). 서버 확인 E4-11-3 |
| `SIMDASH_SCX_RELAY_HOME_ABS` | `/Users/vd.dashboard` | 공용 계정 홈의 절대 표기. `absolute` 표기 생성과 `JobInfo.output_path` → 상대 경로 변환에만 쓴다 |
| `SIMDASH_SCX_RELAY_MAX_UPLOAD_BYTES` | `4294967296`(4 GiB) | 파일 1개 업로드 상한(1 MiB~2^40) |
| `SIMDASH_SCX_RELAY_MAX_DOWNLOAD_BYTES` | `21474836480`(20 GiB) | 파일 1개 다운로드 상한(1 MiB~2^40) |

- relay의 쓰기(mkdirs·upload)는 기존 대기열을 쓰므로 **`SIMDASH_DRIVE_WRITES_ENABLED=true`이고 `SIMDASH_SCX_DRIVE_ROOT`가 설정되어 대기열 워커가 돌고 있어야 한다**(`writes.drive_writes_active()`). 아니면 409 `DRIVE_WRITE_DISABLED`. 읽기·잡 조회는 쓰기 설정과 무관.
- 기동 검증: relay가 켜졌는데 scx 모드가 아니거나 비밀 파일이 잘못되면 `DriveConfigError`(변수 이름 표시)로 기동 거부 — 기존 scx 설정 검증과 같은 방식.

## 3. 접근 제어

모든 relay 요청에서 이 순서로 검사한다(앞에서 걸리면 바로 응답, 본문은 §7 오류 형식).

1. relay 꺼짐 → 404 `RELAY_DISABLED`.
2. **loopback 확인**: `request.client.host ∈ {127.0.0.1, ::1}`이고, 요청에 프록시 헤더(`x-forwarded-for`·`x-forwarded-proto`·`x-forwarded-host`·`x-forwarded-port`·`x-forwarded-prefix`·`forwarded`·`x-real-ip`·`x-simdash-proxy-peer`)가 **하나도 없어야** 한다. 아니면 403 `RELAY_FORBIDDEN`. (uvicorn은 127.0.0.1에만 바인드하지만 Vite·Caddy 프록시를 거친 브라우저 요청도 loopback으로 보이므로 헤더 조건이 필요하다.)
3. 경로 접두 `/internal/scx-relay/v1/`은 `/api` 밖이므로 Vite(`/api`·`/assets`·`/physicsai`)와 Caddy(`/api`·`/assets`)가 전달하지 않는다. 이 접두를 프록시 표에 넣지 않는다(정적 시험).
4. **공유 비밀**: 헤더 `X-SCX-Relay-Key`가 비밀 파일 값과 `hmac.compare_digest`(바이트)로 같아야 한다. 없거나 다르면 401 `RELAY_UNAUTHORIZED`(어느 쪽인지 구분하지 않음). 비밀은 기동 때 한 번 읽어 메모리에 둔다(교체 = 두 앱 재시작).
5. 선택 헤더 `X-SCX-Relay-Client: physicsai`(감사·대기열 `requested_by`용, `^[a-z0-9_-]{1,32}$`, 없으면 `relay`).
6. scx 모드 아님 → 503 `DRIVE_MODE_DISABLED`.

비밀 취급: 헤더 값·파일 내용을 로그·오류·응답·감사에 넣지 않는다. 오류 문구는 기존 `safe_error_message`(Bearer·JWT 마스킹, 200자)를 거친다.

## 4. 경로 규칙

- API의 드라이브 경로는 모두 **공용 계정 홈 기준 상대 경로**(어댑터 `drive_root="~"` 관례): NFC, `/` 구분, `/`·`~` 시작 금지, `\`·제어문자 금지, 빈 구간·`.`·`..` 금지(`config.validate_drive_rel_path`), 구간마다 1~255자, 전체 ≤ 1024자(드라이브 한도, 이슈 #51).
- **허용 범위(쓰기)**: `SIMDASH_SCX_RELAY_DRIVE_BASE` 아래. 파일 업로드의 대상 폴더는 기준 아래 **깊이 2 이상**(`<프로젝트>/<잡 폴더>`), mkdirs는 깊이 1 이상.
- **금지(쓰기·읽기 공통)**: SPDM 루트 `S = SIMDASH_SCX_DRIVE_ROOT`에 대해 경로 `p`가 `p == S` 또는 `p`가 `S + "/"`로 시작(대소문자 무시·NFC 비교)이면 403 `PATH_NOT_ALLOWED`. relay 기준이 S 안이거나 S와 같으면 기동 거부. 또 `p`의 첫 구간이 `.`로 시작하면 거부(숨김·시스템 폴더 보호).
- **허용 범위(읽기)**: 쓰기 범위와 같음 + relay가 기록한 잡(§6)의 `output_rel` 아래. 그 밖은 403 `PATH_NOT_ALLOWED`.
- 경로 오류(형식) → 400 `INVALID_PATH`.

## 5. 엔드포인트

공통: 접두 `/internal/scx-relay/v1`, JSON은 UTF-8, 시각은 ISO 8601(UTC, `Z`). 모든 응답 헤더 `Cache-Control: no-store`.

### 5.1 `GET /health`

```jsonc
200 {"relay_version": 1, "drive_mode": "scx", "writes_active": true,
     "job_api": true,                         // 어댑터에 JobGateway(submit_radioss·get_job·terminate_job + RadiossJobRequest·JobResources) 있음
     "adapter_version": "0.3.0",              // 어댑터 __version__ (없으면 null)
     "drive": {"state": "OK", "last_success_at": "…", "last_error_code": null, "last_error_at": null,
               "token_refreshed_at": "…", "queue_depth": 0, "in_flight": 0},   // gateway.health_dict, 게이트웨이 미생성이면 null
     "queue_paused": false,                   // 대기열 BLOCKED(재로그인 필요)
     "relay_base": "",                        // SIMDASH_SCX_RELAY_DRIVE_BASE
     "forbidden_prefixes": ["SPDM"],          // SIMDASH_SCX_DRIVE_ROOT
     "home_display": "/Users/vd.dashboard",
     "job_path_form": "rel",
     "max_upload_bytes": 4294967296, "max_download_bytes": 21474836480}
```
health는 게이트웨이를 새로 만들지 않는다(`existing_gateway()`).

### 5.2 `POST /mkdirs` — 폴더 만들기(대기열 MKDIR, 멱등)

요청 `{"request_id": "pai-<id>:mkdir", "path": "cushion/01_D-003_run__00012_20261017T105700"}` → 202 `Batch`(§5.4). 이미 있는 폴더는 성공(DONE). 같은 `request_id`면 기존 묶음을 그대로 돌려준다(새로 넣지 않음).

### 5.3 `POST /files?dir=<rel>&name=<name>` — 파일 올리기(대기열 MKDIR + FILE)

- 본문: 파일 바이트(`Content-Type: application/octet-stream`), `Content-Length` 필수(없으면 411 `LENGTH_REQUIRED`). 헤더 `X-Content-SHA256`(소문자 hex 64) 필수, `X-Request-Id` 필수(`^[A-Za-z0-9_.:\-]{1,128}$`).
- `name`: 한 구간, 1~255자, `/`·`\`·제어문자·`:` 금지, `.`·`..` 금지.
- 처리: 크기 > `MAX_UPLOAD_BYTES` → 413 `TOO_LARGE`(본문 읽기 전). 서버 임시 공간 여유 < 1 GiB + 크기 → 503 `DRIVE_STAGING_FULL`. 본문을 `<SIMDASH_SCX_STAGING_DIR>/upload-<batch>/<name>`로 받으며 sha256 계산(DB 연결 없이) → 크기·해시 불일치면 임시 파일 지우고 400 `HASH_MISMATCH`. 그 뒤 한 트랜잭션에서 묶음 `[MKDIR dir, FILE dir/name(sha256·size)]`을 `origin="physicsai_relay"`, `request_id=X-Request-Id`로 넣는다 → 202 `Batch`.
- **멱등**: 같은 `request_id`의 묶음이 있으면 (같은 dir·name·sha256일 때) 본문을 버리고 그 묶음을 200으로 돌려준다. 내용이 다르면 409 `REQUEST_ID_REUSED`.
- **충돌**: 드라이브에 같은 이름이 있으면 대기열 규칙 그대로 — 같은 내용(sha1 또는 내려받아 sha256)이면 `DONE`, 다르면 항목 `CONFLICT`(덮어쓰지 않음).
- **동기 아님**: 202 뒤 대기열 워커가 올린다. 이유: (a) 큰 `.rad`(수백 MB~수 GB)를 HTTP 요청 스레드에서 붙잡지 않음, (b) 기존 재시도·`AUTH_REQUIRED` 일시정지·관리 화면(관리 › 업로드 대기열에 출처 "PhysicsAI"로 보임)을 그대로 씀, (c) 쓰기 메서드 호출 모듈 1개 규칙 유지. 단점: 대시보드 자체 업로드(결과 등록·Final)와 같은 줄에서 순서대로 처리된다.

### 5.4 `GET /batches/{batch_id}` — 묶음 상태

`origin="physicsai_relay"` 묶음만(아니면 404 `NOT_FOUND`).

```jsonc
200 {"batch_id": "…", "request_id": "pai-…", "state": "QUEUED|RUNNING|PAUSED|DONE|PARTIAL|CONFLICT|FAILED|CANCELLED",
     "finished": false, "paused": false,        // upload_queue.summarize 그대로
     "items": [{"kind": "MKDIR|FILE", "target": "cushion/…/a.rad", "state": "PENDING|RUNNING|DONE|CONFLICT|FAILED|BLOCKED|CANCELLED",
                "size": 123, "attempts": 1, "next_attempt_at": null, "error_code": null, "error_message": null}]}
```
PhysicsAI 판정: `DONE` = 성공, `PAUSED`(BLOCKED, 재로그인 필요)·`QUEUED`·`RUNNING` = 계속 기다림, `CONFLICT`·`FAILED`·`CANCELLED`·`PARTIAL` = 실패.

### 5.5 `GET /list?path=<rel>&max_entries=<n>` — 폴더 목록

`max_entries` 1~50000(기본 5000). → 200 `{"path": "…", "entries": [{"name","rel_path","kind":"file|dir","size","modified_at","sha1"}]}`. 없음 404 `NOT_FOUND`, 너무 많음 413 `LIMIT`. 우선순위 `BACKGROUND`.

### 5.6 `GET /files?path=<rel>&max_bytes=<n>` — 파일 내려받기(스트리밍)

- `stat` → 파일 아님 404, 크기 > min(`max_bytes`, `MAX_DOWNLOAD_BYTES`) → 413 `TOO_LARGE`. 임시 여유 < 1 GiB + 크기 → 503 `DRIVE_STAGING_FULL`.
- `download_to(path, <STAGING>/relay-dl-<uuid>/, max_bytes, priority=BACKGROUND)`(DB 연결 없이) → 200 스트리밍, 헤더 `Content-Length`, `X-Content-SHA256`(= `DownloadResult.sha256`), `X-Drive-Version`(entry `version_token`). 응답이 끝나거나 끊기면 임시 파일을 지운다(기존 저장소 패널 다운로드와 같은 방식).
- relay는 PhysicsAI 폴더에 쓰지 않는다. 받은 바이트를 PhysicsAI가 자기 허용 루트(AI 루트) 안에 쓴다 — 두 앱 모두 자기 경로 규칙만 지킨다.

### 5.7 `POST /jobs/radioss` — Radioss 제출(멱등 replay)

```jsonc
// 요청
{"request_id": "pai-<hpc_jobs.id>",                         // ^[A-Za-z0-9_.:\-]{1,128}$
 "input_path": "cushion/01_D-003_run__00012_20261017T105700/cushion_0000.rad",
 "engine_paths": ["cushion/01_…/cushion_0001.rad"],          // 상대 경로, 0~64개
 "additional_paths": ["cushion/01_…/part_1.inc", "cushion/01_…/RENUM_INIT.csv"],   // 0~2000개
 "version": "2026.1",
 "resources": {"appliance": "…", "ncpu": 16, "memory_gb": null, "queue_type": "implicit"},
 "run_type": "both - use starter and engine files", "mpi": "SMP (default)", "single_precision": false,
 "client_ref": {"app": "physicsai", "job_id": "…", "hpc_job_id": "…", "run_key": "run__00012"}}   // 기록용, 각 ≤128자
// 201(새 제출) / 200(replay)
{"request_id": "…", "state": "SUBMITTED", "replayed": false,
 "job": {"job_id": "…", "solver": "radioss", "status": "Queued", "output_path": null, "output_rel": null, "error_message": null}}
```

- 검증: 모든 경로 §4(쓰기 범위·SPDM 금지), 같은 잡 폴더 안(= `input_path`의 부모 폴더 아래)이어야 함(아니면 422 `INVALID_REQUEST`). `ncpu` 1~4096, `memory_gb` null 또는 1~65536, 문자열 필드 1~128자·제어문자 금지. `input_path`를 `stat`해 없으면 404 `NOT_FOUND`(제출 전 확인, 어댑터 제출 호출 없음).
- 잡 API 없는 어댑터 → 501 `JOB_API_UNAVAILABLE`(드라이브 엔드포인트는 계속 동작).
- 경로 표기 변환: `SIMDASH_SCX_RELAY_JOB_PATH_FORM`에 따라 `rel` 그대로 / `~/` + rel / `HOME_ABS + "/" + rel`.
- **기록 순서**: (1) `scx_relay_submissions`에 `SUBMITTING` 행 삽입·커밋(request 본문 sha256 포함) → (2) DB 연결 없이 `submit_radioss(RadiossJobRequest(...))` → (3) 결과 기록: 성공 `SUBMITTED`(job_id·status·output_path), 어댑터 `SUBMISSION_UNKNOWN` 또는 **어댑터 오류 코드가 없는 예외**(워커 종료 등) → `UNKNOWN`, 그 밖 어댑터 코드 → `FAILED`(코드·문구).
- **replay(같은 `request_id`)**: 본문 sha256이 다르면 409 `REQUEST_ID_REUSED`. 같으면 어댑터를 부르지 않고: `SUBMITTED` → 200 기록 그대로(`replayed:true`, 잡 상태는 마지막 기록), `SUBMITTING` → 409 `SUBMISSION_IN_PROGRESS`(retryable), `UNKNOWN` → 409 `SUBMISSION_UNKNOWN`, `FAILED` → 기록한 오류 그대로, `ABANDONED` → 409 `SUBMISSION_ABANDONED`. 새로 제출하려면 새 `request_id`.
- 기동 정리: 프로세스 시작 때 `SUBMITTING` 행은 `UNKNOWN`으로 바꾼다(제출 도중 대시보드가 멈춤 — 결과를 모름).

### 5.8 `GET /jobs/{job_id}` — 잡 상태

relay 기록(`SUBMITTED`·`ATTACHED`)에 있는 job_id만(아니면 404 `JOB_NOT_FOUND`, 어댑터 호출 없음). `get_job` → 200 `{"job_id","solver","status","output_path","output_rel","error_message"}` + 기록 갱신(마지막 상태·output). `output_rel` = `output_path`를 상대 경로로 바꾼 값(`~/x` → `x`, `HOME_ABS/x` → `x`, 이미 상대면 그대로, 바꿀 수 없거나 §4 형식 위반이면 null — 그때 PhysicsAI는 회수 불가, E4-11-6). 어댑터가 잡을 모르면 404 `SCX_JOB_NOT_FOUND`. `status` 문자열은 어댑터 값 그대로(Queued·Running·Completed·Failed·Aborted, 해석은 PhysicsAI).

### 5.9 `POST /jobs/{job_id}/terminate`

relay 기록에 있는 job_id만. `terminate_job` 1회(재시도 없음) → 200 `{"job_id","requested":true}`. 어댑터 오류는 §7 매핑. 이미 끝난 잡에 대한 어댑터 응답은 그대로 전달(E4-11-8).

### 5.10 `POST /submissions/{request_id}/resolve` — 결과 모름 정리(사람 확인 후)

`UNKNOWN` 행만(아니면 409 `NOT_UNKNOWN`). 요청 `{"action": "attach", "job_id": "…"}` 또는 `{"action": "abandon"}`.
- `attach`: `get_job(job_id)` 성공해야 함(아니면 404 `SCX_JOB_NOT_FOUND`), 다른 행이 이미 그 job_id를 가지면 409 `JOB_ID_IN_USE`. → 행 `ATTACHED`(job_id·상태 기록) → 200 `{"request_id","state":"ATTACHED","job":{…}}`. 이후 §5.8·§5.9·output 읽기 허용.
- `abandon`: 행 `ABANDONED` → 200. (원격에 잡이 실제로 있었다면 relay로는 더 다루지 않는다 — SCX 화면에서 사용자가 처리.)
- 사람이 SCX 화면에서 잡을 찾은 뒤 PhysicsAI가 이 엔드포인트를 부른다. relay 스스로 attach·abandon하지 않는다.

## 6. 대시보드 DB·대기열 변경

- 새 표 `scx_relay_submissions`(PostgreSQL migration = scx-drive 브랜치의 다음 번호, 현재 `0044_scx_relay_submissions`; DuckDB는 `database.py` 같은 DDL): `request_id` PK, `request_sha256`(64), `client` (≤32), `client_ref` JSON, `input_rel`(≤1024), `job_dir_rel`(≤1024), `state` CHECK `SUBMITTING|SUBMITTED|UNKNOWN|FAILED|ATTACHED|ABANDONED`, `job_id`(유일, NULL 허용), `solver`, `last_status`, `output_path`(≤1024), `output_rel`(≤1024), `error_code`, `error_message`(≤200, 마스킹), `created_at`, `updated_at`, `resolved_at`. 삭제 없음.
- `upload_queue.py`: 새 root 종류. relay 항목의 `root_key = "relay:<server_host>:/<RELAY_DRIVE_BASE>"`, `dst_rel_dir`는 **relay 기준 상대**. `_drive_path`는 항목의 root 종류로 기준을 고른다(SPDM 항목 = `spdm_root`, relay 항목 = relay 기준). 꺼낼 때 root 일치 검사(`DRIVE_ROOT_CHANGED`)는 각 종류의 현재 키와 비교. 끝 훅 없음. 관리 화면 출처 이름 "PhysicsAI".
- relay 항목도 대기열 일시정지(`BLOCKED`)·관리자 재시도·취소 대상이다. 취소는 행 표시만(드라이브 불변).

## 7. 오류 형식과 코드

본문 `{"code": "…", "message": "한국어 문구(마스킹·200자)", "retryable": bool, "retry_after": int|null, "adapter_code": "…"|null}`.

| HTTP | code | 원인 | retryable |
|---|---|---|---|
| 404 | `RELAY_DISABLED` | relay 꺼짐 | – |
| 403 | `RELAY_FORBIDDEN` | loopback 아님·프록시 헤더 | – |
| 401 | `RELAY_UNAUTHORIZED` | 비밀 헤더 없음·불일치 | – |
| 503 | `DRIVE_MODE_DISABLED` | scx 모드 아님 | – |
| 409 | `DRIVE_WRITE_DISABLED` | 쓰기 꺼짐·SPDM 루트 미설정(대기열 미동작) | – |
| 501 | `JOB_API_UNAVAILABLE` | 어댑터 < v0.3.0(JobGateway 없음) | – |
| 400 | `INVALID_PATH` | §4 형식 | – |
| 403 | `PATH_NOT_ALLOWED` | 범위 밖·SPDM 루트 | – |
| 422 | `INVALID_REQUEST` | 본문 모양·값 범위 | – |
| 411 | `LENGTH_REQUIRED` | 업로드 길이 없음 | – |
| 413 | `TOO_LARGE` | 업로드·다운로드 상한 | – |
| 400 | `HASH_MISMATCH` | 받은 바이트 해시·크기 불일치 | 예(다시 보냄) |
| 503 | `DRIVE_STAGING_FULL` | 서버 임시 공간 부족 | 예 |
| 409 | `REQUEST_ID_REUSED` | 같은 request_id, 다른 내용 | – |
| 404 | `NOT_FOUND` | 드라이브 경로·묶음 없음 (어댑터 `NOT_FOUND`) | – |
| 404 | `JOB_NOT_FOUND` | relay 기록에 없는 job_id | – |
| 404 | `SCX_JOB_NOT_FOUND` | 어댑터가 잡을 모름 | – |
| 409 | `SUBMISSION_UNKNOWN` | 제출 결과 모름 (어댑터 `SUBMISSION_UNKNOWN`) | **아니오 — 사람 확인** |
| 409 | `SUBMISSION_IN_PROGRESS` | 같은 request_id 처리 중 | 예 |
| 409 | `SUBMISSION_ABANDONED` · `NOT_UNKNOWN` · `JOB_ID_IN_USE` | §5.7·§5.10 | – |
| 409 | `CONFLICT` | 어댑터 `CONFLICT`(대기열 밖 경로에서 날 일은 없음 — 대기열은 항목 상태로 표시) | – |
| 403 | `FORBIDDEN` | 어댑터 `FORBIDDEN` | – |
| 413 | `LIMIT` | 어댑터 `LIMIT` | – |
| 423 | `LOCKED` | 어댑터 `LOCKED` | 예 |
| 503 | `DRIVE_BUSY` | 어댑터 `BUSY`·`OVERLOADED`·`RATE_LIMITED`(`retry_after` 5) | 예 |
| 503 | `DRIVE_AUTH_REQUIRED` | 어댑터 `AUTH_*` (대시보드 관리 화면에서 재로그인) | 아니오(재로그인 뒤 예) |
| 504 | `DRIVE_TIMEOUT` | 어댑터 `TIMEOUT` | 예 |
| 503 | `DRIVE_UNAVAILABLE` | 어댑터 `UNAVAILABLE`·워커 시작 실패 | 예 |
| 500 | `DRIVE_INTERNAL` | 어댑터 `INTERNAL` | – |
| 502 | `SCX_JOB_ERROR` | 그 밖 어댑터 잡 코드(원 코드는 `adapter_code`) | – |

- 드라이브 코드 매핑은 기존 `DRIVE_ERROR_MAP`을 재사용하고(`storage_code`에서 `SPDM_` 접두는 relay 응답에서 뗀다), 잡 코드만 위 표로 더한다.
- **제출(§5.7)에서는 어떤 코드든 relay가 다시 보내지 않는다.** retryable 표시는 "PhysicsAI가 같은 request_id로 replay해도 안전"이라는 뜻이다(replay는 어댑터를 다시 부르지 않음).

## 8. 시험(대시보드, `backend/tests/test_scx_relay.py`)

`tests/drive_fakes.py`의 `FakeWorkerDriveGateway`에 잡 API(`submit_radioss`·`get_job`·`terminate_job`, `JobResources`·`RadiossJobRequest`·`JobInfo` 가짜 타입, 상태 시퀀스·`SUBMISSION_UNKNOWN`·예외 주입, 호출 기록)를 더하고, 잡 API 없는 변형(501 확인)을 둔다.

1. 접근: 꺼짐 404 · none 모드 503 · 키 없음/틀림 401(같은 응답) · 프록시 헤더 각각 403 · loopback 아닌 client 403 · 비교에 `hmac.compare_digest` 사용(정적) · 비밀이 로그(caplog)·오류 본문에 없음 · 비밀 파일 형식 오류 기동 거부.
2. 경로: `..`·`/`·`~`·`\`·제어문자·빈 구간 400 · SPDM 루트 자신·하위·대소문자 다른 표기 403 · 기준 밖 403 · 업로드 깊이 1 거부 · 1024자 초과 400.
3. 업로드: 대기열 항목(origin·root_key·request_id·MKDIR+FILE) · 해시 불일치 400·임시 파일 없음 · 상한 413 · 임시 공간 부족 503 · 같은 request_id 200 같은 묶음 · 다른 내용 409 · 드라이브에 같은 내용 있음 → DONE · 다른 내용 → CONFLICT(덮어쓰기 없음) · `AUTH_REQUIRED` → 묶음 PAUSED · 쓰기 꺼짐 409 · mkdirs 멱등.
4. 읽기: list·download 범위(기준·기록된 output_rel만) · `max_bytes` 413 · `X-Content-SHA256` · 응답 후·끊김 시 임시 파일 삭제.
5. 잡: 501 · 성공 201 · replay 200(어댑터 호출 1회) · `SUBMISSION_UNKNOWN` 409 + 행 UNKNOWN + replay도 어댑터 미호출 · 코드 없는 예외 → UNKNOWN · 처리 중 409 · 기동 시 SUBMITTING→UNKNOWN · 정의된 오류 매핑 · 경로 표기 3종 · output_rel 변환(`~/`·절대·변환 불가 null) · get_job/terminate 기록 밖 404 · resolve attach(get_job 확인·중복 409)·abandon.
6. 정적: relay 모듈이 `upload_new`·`mkdirs`·`copy_within`과 삭제·이동 계열 이름을 부르지 않음(기존 금지 목록 대상에 추가) · Vite/Caddy 프록시 표에 `/internal` 없음.

## 9. 파일 소유(대시보드 구현 에이전트 — PhysicsAI 저장소 파일은 건드리지 않음)

새 파일: `backend/app/services/drive/relay.py`(접근 제어·경로 범위·제출 기록·오류 매핑), `backend/app/routers/scx_relay.py`, `backend/migrations/versions/0044_scx_relay_submissions.py`(번호는 그때 다음 번호), `backend/tests/test_scx_relay.py`, `docs/features/scx-relay.md`(이 문서 사본).
수정: `backend/app/services/drive/config.py`(relay 설정), `backend/app/services/drive/upload_queue.py`(relay root 종류), `backend/app/main.py`(라우터·기동 정리), `backend/app/database.py`(DuckDB DDL), `backend/tests/drive_fakes.py`(잡 API 가짜), `backend/tests/test_drive_foundation.py`(정적 금지 대상에 relay 모듈), `docs/features/scx-drive.md`(설정 표에 relay 변수 한 줄씩 + 링크), `docs/decisions.md`(대시보드 쪽 한 줄).
