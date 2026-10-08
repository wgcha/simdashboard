# SCX 드라이브 연동 — 설치·설정·토큰·점검·읽기·쓰기 경로 (D0–D3)

- 기준일: 2026-10-07
- 상태: **D0(기반)·D1(공급자 주입)·D2(읽기 경로)·D3(쓰기 경로) 구현(2026-10-07~08, 브랜치 `claude/scx-drive`).** scx 모드에서 SPDM 폴더 기능은 드라이브를 읽고(§8), **드라이브 쓰기 허용**(`SIMDASH_DRIVE_WRITES_ENABLED`, 기본 꺼짐)을 켜면 결과 등록(끌어놓기)과 Final 지정이 업로드 대기열로 드라이브에 쓴다(§10). 계획은 [SCX 드라이브 연동 계획](../plans/scx-drive-integration.md).
- 근거: GitHub 이슈 #45의 어댑터 계약 v0.2(contract §2–4, §10)와 연동 문서 01·02·04·06
- 배포 영향: [ADR 0006](../adr/0006-scx-drive-adapter-external-wheel.md) — 어댑터 wheel은 대시보드 패키지에 넣지 않고 따로 설치한다.

## 1. 무엇이 바뀌나

| 모드 | 동작 |
|---|---|
| `SIMDASH_DRIVE_GATEWAY=none` (기본, 미설정 포함) | **지금과 같다.** 어댑터 패키지를 불러오지 않고, 관리자 드라이브 API는 409 `DRIVE_MODE_DISABLED`, 상태 API는 `mode: "none"`, 상단 배너 없음 |
| `SIMDASH_DRIVE_GATEWAY=scx` | 기동 시 설정·어댑터 설치·단일 프로세스를 확인하고 하나라도 어긋나면 **기동을 거부**한다. 관리자 화면에서 공용 계정 토큰 등록·연결 시험·드라이브 점검을 할 수 있다. **D2부터 SPDM 루트는 드라이브의 `SIMDASH_SCX_DRIVE_ROOT`**(§8): 폴더 탐색·자동 탐색·자동 반영(60초)·Case 결과 캡처·진척·소재·의미 매핑은 드라이브에서 읽는다. 드라이브에 쓰는 기능은 **쓰기 허용 설정이 꺼져 있으면** 409 `DRIVE_WRITE_DISABLED`(읽기 전용). 켜면 결과 등록(끌어놓기·새 폴더)과 Final 지정은 업로드 대기열로 드라이브에 쓰고(§10), 설계가 없는 기능(결과 등록 초안 게시·복사 재시도·폴더 준비, 저장소 패널 연결·업로드·새로고침)은 계속 409 |

## 2. PC·서버 준비 (사용자가 직접 하는 일)

어댑터 저장소(`VD_scx_drive_adapter`)의 릴리스 zip(`VD_scx_drive_adapter-<버전>-win_amd64-cp311.zip`)을 받아 둔다. 이 저장소에는 어댑터 wheel·SDK wheel이 없다(결정 D6).

1. **워커 런타임 설치** (SDK가 도는 별도 가상환경, Python 3.11 x64)
   ```powershell
   # 릴리스 zip을 푼 폴더에서
   powershell -ExecutionPolicy Bypass -File .\install_runtime.ps1 -Python C:\Python311\python.exe -InstallRoot C:\SimDashboard\scx-runtime
   ```
   끝에 출력되는 `SIMDASH_SCX_WORKER_PYTHON=...\venv\Scripts\python.exe` 값을 적어 둔다.
2. **어댑터 wheel을 `external-wheels` 폴더에 한 번 복사** (대시보드 가상환경에는 설치 스크립트가 의존성 없이 넣는다)
   - 소스 PC: 저장소 루트의 `external-wheels\` (`.gitignore` 대상, 업데이트가 건드리지 않는 보호 경로)
   - 폐쇄망 서버: 설치 루트의 `state\external-wheels\` (예: `C:\ProgramData\SimulationWorkbench\state\external-wheels\`, 릴리스 폴더 밖)
   ```powershell
   New-Item -ItemType Directory -Force .\external-wheels | Out-Null
   Copy-Item <압축 푼 폴더>\wheelhouse\vd_scx_drive_adapter-<버전>-py3-none-any.whl .\external-wheels\
   ```
   그다음 소스 PC는 `update.bat`(또는 `deploy.bat`·`setup.ps1`), 서버는 오프라인 설치기를 다시 실행한다. 이 스크립트들은 lock 설치(`uv pip sync`·새 릴리스 venv) **직후마다** 폴더의 `*.whl`을 `--no-deps --no-index`로 설치하고(네트워크 사용 안 함) `import scx_drive_adapter`를 확인해 결과를 출력한다. 그래서 업데이트 뒤 다시 설치할 필요가 없다. 폴더에는 어댑터 버전 하나만 둔다(새 버전으로 바꿀 때 이전 wheel을 지운다).
   - 폴더가 없거나 비어 있고 `SIMDASH_DRIVE_GATEWAY`가 `none`이면 아무것도 하지 않는다(기본 동작 불변).
   - `none` 모드에서 wheel 설치·import가 실패하면 경고만 하고 업데이트를 계속한다.
   - `scx` 모드(`.env` 값 기준)에서 wheel이 없거나 설치·import가 실패하면 설치·업데이트가 그 단계에서 **명확한 오류로 중단**된다. wheel을 넣고 다시 실행한다.
   `siemens_scx`는 대시보드 가상환경에 **설치하지 않는다**(pydantic·PyJWT 버전 충돌, contract §10.1). SDK wheel을 `external-wheels`에 넣지 않는다.
3. **암호화 키 만들기** (`AUTH_SECRET_KEY`와 다른 값)
   ```powershell
   .\.venv-runtime\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
4. **`.env`에 설정** (§3) → 대시보드 재시작. 상태가 "재로그인 필요"로 나오면 정상이다.
5. **토큰 등록** (§4) → **연결 시험** → **드라이브 점검** (§5).

## 3. 설정값

모두 환경변수(`.env` 또는 서비스 환경). scx 모드에서 필수 값이 없거나 형식이 틀리면 **기동을 거부**하고 오류 메시지에 변수 이름을 표시한다. 토큰 암호화 키 값은 메시지에 넣지 않는다.

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `SIMDASH_DRIVE_GATEWAY` | — | `none` | `none` 또는 `scx`. 그 밖의 값은 기동 거부 |
| `SIMDASH_SCX_WORKER_PYTHON` | scx | — | 워커 런타임 `python.exe` 절대 경로(파일이 있어야 함) |
| `SIMDASH_SCX_SERVER_URL` | scx | — | `https://<호스트>/`. 토큰 등록 시 `server_url` 호스트와 같아야 함 |
| `SIMDASH_SCX_CLIENT_NAME` | scx | — | SSO 클라이언트 ID(HPC_SDK와 같은 값) |
| `SIMDASH_SECRET_ENC_KEY` | scx | — | 토큰 암호화 Fernet 키(32바이트 urlsafe base64). `AUTH_SECRET_KEY`와 같으면 거부 |
| `SIMDASH_SCX_DRIVE_ROOT` | SPDM 기능·점검 | — | SPDM 루트, 공용 계정 홈(`~`) 기준 상대 경로(예 `SPDM/Projects`). D2부터 scx 모드의 SPDM 루트는 **이 값뿐**이다(화면의 로컬 루트 설정은 `SPDM_ROOT_LOCKED`로 막고 저장된 로컬 루트 값은 건드리지 않음). 없으면 SPDM 폴더 기능은 "루트 미설정". 화면 폴더 선택기는 D4 |
| `SIMDASH_DRIVE_WRITES_ENABLED` | — | `false` | **드라이브 쓰기 허용**(사용자 결정 2026-10-07 20:25). 꺼져 있으면 scx 모드는 읽기 전용(D2와 같음). 켜면 업로드 대기열 작업자가 뜨고 결과 등록·Final 지정이 드라이브에 쓴다(§10). 먼저 전용 시험 폴더에서 §10.6 수용 절차로 확인한다. `true/false/1/0/on/off` 외 값은 기동 거부 |
| `SIMDASH_DRIVE_UPLOAD_MAX_ATTEMPTS` | — | `8` | 대기열 항목 하나의 재시도 가능 오류(BUSY·LOCKED·TIMEOUT·OVERLOADED·UNAVAILABLE) 최대 시도 수(1–100). 넘으면 `FAILED` |
| `SIMDASH_DRIVE_VERIFY_MAX_BYTES` | — | `2147483648`(2 GiB) | 같은 이름 파일이 드라이브에 있고 sha1로 비교할 수 없을 때 서버로 내려받아 내용(sha256)을 비교하는 크기 상한(0–2^50). 넘으면 비교하지 않고 `CONFLICT`(`SPDM_CONFLICT_UNVERIFIED`, 관리자 판단). 0이면 내려받아 비교하지 않음 |
| `SIMDASH_DRIVE_BLOB_DIR` | — | `<WORK_DIR>\blobs` | 32 MiB 초과 다운로드 파일의 서버 보관소(내용 sha256 이름, 05 §3). 백업 대상 아님(드라이브에서 다시 받을 수 있음) |
| `SIMDASH_DRIVE_BLOB_MAX_BYTES` | — | `107374182400`(100 GiB) | 보관소 용량 상한. 넘으면 가장 오래 쓰지 않은 파일부터 지운다(64 MiB 이상) |
| `SIMDASH_SCX_CA_BUNDLE` | 권장 | — | 사내 CA 번들 pem 절대 경로 |
| `SIMDASH_SCX_WORK_DIR` | — | `backend\data\scx-worker` | 워커 TEMP·adcopy 로그·`dashboard.lock`. 서버 설치는 릴리스 폴더 밖 경로 권장 |
| `SIMDASH_SCX_STAGING_DIR` | — | `<WORK_DIR>\staging` | 다운로드·업로드 임시 저장소. 대시보드 서비스 계정만 접근 |
| `SIMDASH_SCX_MAX_CONCURRENCY` | — | `1` | 워커 동시 SDK 호출 수(1–8) |
| `SIMDASH_SCX_CACHE_TTL_SECONDS` | — | `30` | 어댑터 목록·조회 캐시(0–3600초) |

어댑터 `AdapterConfig.drive_root`는 항상 `~`로 넘긴다. SPDM 루트는 대시보드가 그 아래 상대 경로로 관리한다(integration 03 §5).

### 기동 거부 조건 (scx 모드)

| 조건 | 메시지 요지 |
|---|---|
| 필수 설정 누락·형식 오류 | `SCX 드라이브 설정 오류: <변수> — …` |
| 어댑터 패키지 없음 | `SCX 드라이브 어댑터 패키지(scx_drive_adapter)가 설치되지 않았습니다 … wheel을 external-wheels 폴더에 복사한 뒤 update.bat(폐쇄망 서버는 오프라인 설치기)을 다시 실행` (영문 병기, §2의 2단계) |
| 다른 대시보드 프로세스가 `<WORK_DIR>\dashboard.lock`을 잡고 있음 | `다른 대시보드 프로세스가 SCX 워커를 사용 중입니다 … 단일 프로세스` — uvicorn `--workers 2` 이상도 여기서 거부된다 |

워커 프로세스는 **첫 드라이브 호출 때** 뜬다(지연 시작). 앱 종료 시 `close()`로 워커를 끝낸다.

## 4. 공용 계정 토큰

1. 브라우저가 있는 관리자 PC에서 어댑터 CLI로 로그인한다.
   ```powershell
   <워커 런타임>\python.exe -m scx_drive_adapter login --server https://<호스트>/ --client-name <클라이언트 ID> [--ca-bundle <pem>]
   ```
2. 출력된 TokenBundle JSON 전체를 **저장소 설정 → SCX 드라이브 → 드라이브 관리 → 공용 계정 토큰**에 붙여 넣고 **토큰 등록**.
3. 서버는 `server_url` 호스트가 `SIMDASH_SCX_SERVER_URL`과 같은지, 두 토큰이 비어 있지 않은지 확인한다(아니면 400 `DRIVE_CREDENTIALS_INVALID`). 저장 직후 `stat("")`로 연결을 시험해 결과를 보여 준다.

저장 방식(integration 02 §3):

- 테이블 `drive_credentials`(행 1개, `id='scx'`), TokenBundle JSON 전체를 `Fernet(SIMDASH_SECRET_ENC_KEY)`로 암호화. 평문 열은 `account_hint`, `obtained_at`, `updated_by`, `updated_at`, `key_id`(키 sha256 앞 8자), `generation`(등록 세대 번호)뿐.
- 키가 바뀌거나 복호화가 안 되면 `load()`는 없음으로 처리 → 상태 "재로그인 필요", 화면에 "암호화 키가 바뀌었습니다".
- SDK는 세션을 만들 때마다 refresh 토큰을 회전시킨다. 워커가 알리는 새 토큰은 `DbTokenStore.save()`가 **자기 DB 연결로 즉시 커밋**한다(`updated_by='worker'`). 저장 실패는 ERROR 로그와 관리 화면 경고로 표시한다.
- **세대 번호로 낡은 저장 차단**: 관리자 등록(`PUT`)마다 행의 `generation`을 이전 값보다 큰 새 값(이전+1과 현재 시각 µs 중 큰 값 — 삭제 뒤 재등록에도 재사용되지 않음)으로 쓴다. 게이트웨이마다 따로 만드는 토큰 저장소(`DbTokenStore.for_gateway`)는 마지막으로 읽은 세대를 기억하고, 워커의 `save()`는 `UPDATE … WHERE id='scx' AND generation=<읽은 세대>`만 한다(INSERT 없음). 0행이면 토큰 내용 없이 경고 로그만 남기고 버린다. 그래서 삭제 뒤 늦게 도착한 회전 토큰이 행을 되살리거나, 이전 계정의 회전 토큰이 새로 등록한 계정을 덮어쓰지 않는다.
- **삭제·재등록 시 게이트웨이 초기화**: `DELETE`와 `PUT`은 저장 직후 `WorkerDriveGateway`를 닫고 버린다(워커가 메모리의 이전 토큰을 계속 쓰지 않게). 다음 드라이브 호출이 새 게이트웨이를 만들어 현재 행을 읽는다. 삭제 후 첫 호출은 `AUTH_REQUIRED`.
- 어댑터에는 경계에서만 어댑터의 `TokenBundle`(repr에 토큰이 보일 수 있음)로 바꿔 넘기고, 대시보드 안에서는 repr이 토큰을 가리는 `LocalTokenBundle`만 쓴다. 오류 문구는 `safe_error_message`가 `Bearer <값>`과 `eyJ…` 형태(JWT)를 한 번 더 가린다.
- API 응답·로그·감사 기록에 토큰 값을 넣지 않는다. 감사: `DRIVE_CREDENTIALS_REGISTERED`(계정 표시명·발급 시각·서버 호스트·key_id), `DRIVE_CREDENTIALS_DELETED`, `DRIVE_CHECK_RUN`.
- 새 토큰을 등록하면 게이트웨이를 새로 만들므로 등록 직후 연결 시험부터 새 토큰을 쓴다. 요청 본문은 64 KiB까지만 읽는다(`Content-Length`로 먼저 거부, 스트림도 상한). 너무 깊게 중첩된 JSON도 400 `DRIVE_CREDENTIALS_INVALID`.

## 5. 드라이브 점검 (결정 D7)

**드라이브 관리 → 드라이브 점검**에 SPDM 루트 기준 시험 폴더(예 `_simdash_test`)를 넣고 **점검 실행**. 한 번에 하나만 실행된다(409 `DRIVE_CHECK_RUNNING`).

| 순서 | 단계 | 확인 내용 |
|---|---|---|
| 1 | `stat` 시험 폴더 | 폴더 존재·종류. 없으면 여기서 멈춤 |
| 2 | `list_dir` | 항목 수, 지연, 목록 항목에 `sha1`·`modified_at`이 있는지(contract C1·C9) |
| 3 | `stat` 첫 작은 파일 | 단건 조회의 `sha1`·`modified_at` (목록과 비교) |
| 4 | `download_to` (8 MiB 이하 첫 파일) | 서버 임시 폴더로 받은 뒤 **서버 사본만** 삭제 |
| 5 | `health` | 어댑터 상태(SDK 호출 없음) |

**조회 전용**(사용자 결정 2026-10-07 20:12 "점검시 조회만 하고 뭘 쓰지는마"): 점검은 드라이브에 **아무것도 만들거나 바꾸거나 지우지 않는다.** 쓰기 메서드(`upload_new`·`mkdirs`·`copy_within` 등)는 호출하지 않으며 켜는 옵션도 없다(정적 시험 `test_drive_check_calls_no_drive_write_methods`가 강제). 서버 임시 폴더에 받은 다운로드 사본만 지운다. 결과 표는 단계·결과·코드·지연(ms)·관찰을 보여 주며, 이슈 #45 수용 시험의 C1·C9 기록 근거가 된다.

**C2(덮어쓰기 금지 업로드 CONFLICT)·C7(드라이브 안 복사 CONFLICT)은 이 점검으로 더 이상 확인하지 않는다.** 두 항목은 미확인으로 남으며, D3에서 전용 시험 공간을 쓰는 별도의 명시적 쓰기 수용 시험으로 확인한다.

## 6. 상태와 화면

| 상태 | 관리 화면 배지 | 일반 화면 상단 배너 |
|---|---|---|
| `OK` | 연결됨 | 없음 |
| `DEGRADED` | 불안정 | 없음 |
| `UNAVAILABLE` | 연결 안 됨 | "드라이브 연결이 원활하지 않습니다…" |
| `AUTH_REQUIRED` (토큰 없음·키 변경·갱신 실패) | 재로그인 필요 | "드라이브 인증이 필요합니다. 관리자에게 문의하세요." |
| `UNKNOWN` (토큰은 있으나 워커가 아직 한 번도 호출되지 않음) | 확인 전 | 없음 |

- 배너는 `GET /api/drive/status`를 60초마다 조회한다(로그인 사용자, 상태 문자열과 `writes_available`·`writes_enabled`). 화면은 이 값으로 읽기 전용 안내를 띄우고 업로드·폴더 만들기·Final 지정 버튼을 끈다(§8.6). none 모드면 첫 응답 후 조회를 멈춘다. 상태 조회는 드라이브를 호출하지 않는다(`health()`만).
- 상태 조회(`/api/drive/status`, `/api/admin/drive/status`)는 **게이트웨이를 만들지 않는다**. 관리자 동작(토큰 등록·연결 시험·점검)이나 이후 드라이브 기능이 이미 만든 게이트웨이의 `health()`만 읽는다. 그래서 재시작 뒤 첫 드라이브 호출 전에는 토큰이 있으면 `UNKNOWN`(배너 없음)이다. 조회 중 어떤 오류(어댑터·설정·DB)도 500이 아니라 `UNAVAILABLE`로 응답한다(백오프 재생성보다 단순하고, 조회마다 워커를 띄울 수 없음).
- 오류 코드 한글 문구는 `frontend/src/shared/api/drive.ts`의 `DRIVE_ERROR_MESSAGES`(integration 06 §2).

## 7. API

| 메서드 | 경로 | 권한 | 내용 |
|---|---|---|---|
| GET | `/api/admin/drive/status` | 전역 관리자 | 모드·서버·루트·토큰 메타·health·워커 버전·토큰 저장 실패 시각 |
| PUT | `/api/admin/drive/credentials` | 전역 관리자, scx | TokenBundle JSON(64 KiB 이하) → `{account_hint, obtained_at, test}`. 게이트웨이 초기화 |
| DELETE | `/api/admin/drive/credentials` | 전역 관리자, scx | 204. 게이트웨이 초기화 |
| POST | `/api/admin/drive/test` | 전역 관리자, scx | `stat("")` + `root_identity()` → `{ok, latency_ms, root_identity, error}` |
| POST | `/api/admin/drive/check` | 전역 관리자, scx | `{test_folder}` → 단계별 결과 표 |
| GET | `/api/drive/status` | 로그인 사용자 | `{mode, state, writes_enabled, writes_available, queue_paused}`(none 모드 `writes_available=true`; scx는 쓰기 허용 설정과 SPDM 루트가 있을 때 true) |
| GET | `/api/drive/source-changes?project_id&request_id` | 의뢰 조회 권한 | 의뢰의 원본 변경·원본 없음 목록과 개수(§8.4). 드라이브 호출 없음 |
| POST | `/api/drive/source-changes/accept` | 의뢰 결과 등록 권한(`result.import`) | `{project_id, request_id, ids?}` → 새 버전 등록(전체 또는 `ids`). 감사 `DRIVE_SOURCE_CHANGES_ACCEPTED` |
| POST | `/api/drive/source-changes/dismiss` | 의뢰 결과 등록 권한 | 무시(같은 드라이브 버전은 다시 묻지 않음). 감사 `DRIVE_SOURCE_CHANGES_DISMISSED` |
| GET | `/api/drive/upload-batches/{batch_id}` | 묶음 의뢰의 조회 권한 | 업로드 묶음 진행(§10.2: 상태·파일 n/m·바이트·현재 항목·오류·`transfer_methods`) |
| GET | `/api/admin/drive/queue?state&limit` | 전역 관리자, scx | 대기열 항목(열린 것과 하루 안에 끝난 것, 또는 상태별)·상태별 개수·일시 정지·작업자 실행 여부 |
| POST | `/api/admin/drive/queue/{item_id}/retry` | 전역 관리자, scx | `FAILED`·`CONFLICT`·`BLOCKED`·`CANCELLED` 항목을 다시 대기로(충돌은 드라이브를 다시 확인, 덮어쓰지 않음). Final 묶음이면 Final을 다시 "반영 중"으로. 감사 `DRIVE_QUEUE_ITEM_RETRIED` |
| POST | `/api/admin/drive/queue/{item_id}/cancel` | 전역 관리자, scx | 끝나지 않은 항목을 `CANCELLED`로 표시만(드라이브 내용은 지우지 않음). 감사 `DRIVE_QUEUE_ITEM_CANCELLED` |
| POST | `/api/admin/drive/queue/resume` | 전역 관리자, scx | 인증 대기(`BLOCKED`) 항목 재개(토큰 등록 시 자동). 감사 `DRIVE_QUEUE_RESUMED` |

비관리자는 403 `GLOBAL_ADMIN_REQUIRED`(관리자 API). none 모드의 쓰기·시험·점검·원본 변경 API는 409 `DRIVE_MODE_DISABLED`. 관리자 상태에는 `writes_enabled`·`writes_available`와 목록의 `version_token` 형태 관찰값(`version_tokens`: sha1 / 크기·시각, C1·C9 기록)이 추가되었다.

## 8. 읽기 경로 (D2)

### 8.1 루트와 공급자

- scx 모드의 SPDM 루트는 `DriveRoot`(`SIMDASH_SCX_DRIVE_ROOT`)이며 `storage.provider_for_root()`가 `DriveStorageProvider`를 돌려준다. 호출부(D1 주입점)는 바뀌지 않는다. 서버 쪽 영역(임시 저장소, 보관소, `.finalizations` 작업 폴더, 가져오기 루트)은 그대로 로컬이다.
- `root_identity`는 `scx:<서버 호스트>:/<SIMDASH_SCX_DRIVE_ROOT>`(경로 기준)다. 루트가 환경변수로 고정되므로 경로가 곧 식별자이고, 계산에 드라이브 호출이 필요 없다(호출부가 DB 연결을 쥔 채 루트를 해석하기 때문). integration 03 §5의 항목 id 기준과 다르다 — 루트 폴더를 같은 이름으로 다시 만들어도 같은 루트로 본다. local 모드와 키가 달라 기존 로컬 등록과 섞이지 않는다(이관은 D5).
- 공급자 동작: 목록·stat은 `list_dir`·`stat`, 내용 읽기는 `download_to`로 서버 임시 저장소에 받아 읽는다. 링크 없음(`is_link=False`), `pin`은 무동작, `item_id`=드라이브 항목 id, `etag`=유효 `version_token`. **쓰기 메서드는 모두 `DRIVE_WRITE_DISABLED`**.
- 드라이브 오류 → `SpdmStorageError`(integration 06 표). 목록 `NOT_FOUND`는 로컬과 같이 `FileNotFoundError`.

### 8.2 DB 연결을 쥔 채 드라이브를 기다리지 않는다

드라이브 클라이언트는 토큰 회전 알림을 받는 스레드에서 `TokenStore.save()`(자기 DB 연결)를 부른다. DuckDB는 프로세스 잠금 하나이므로 요청 스레드가 `with connect()` 안에서 드라이브를 기다리면 교착이 생기고, PostgreSQL에서도 풀 연결과 잠금을 드라이브 호출 시간만큼 붙잡는다. 그래서:

- 현재 스레드가 연결을 쥐고 있는지(`database_connection.connection_held()`)를 추적한다.
- 드라이브를 읽는 화면 API는 **읽기 세션**(`drive.reads.run` / 라우트 데코레이터 `read_session`)으로 감싼다. 본문은 지금처럼 자기 연결을 연다. 본문이 연결을 쥔 채 세션에 없는 자료(목록·stat·내용)를 찾으면 제어 신호(`DriveReadMiss`)로 연결을 닫고, **연결 없이** 그 자료(목록은 형제 폴더 포함 4단계, 내용은 같은 범위의 읽을 파일 묶음)를 받은 뒤 본문을 다시 실행한다(최대 8회, 마지막 회차의 부족분은 `DRIVE_READ_NOT_PREPARED`). 다시 실행해도 안전한 본문(읽기 후 쓰기, 쓰기는 트랜잭션)에만 쓴다.
- 쓰기 뒤에 파일을 읽는 흐름(등록 후 캡처, 캡처 재시도, 새로고침 캡처)은 쓰기 **전에** `require_content(Case 경로들)`로 Case의 캡처 파일을 미리 받는다. 커밋 뒤 캡처가 드라이브를 기다리는 일이 없다.
- 커밋 표시(검수 L1): 등록 커밋 뒤 캡처 단계와 캡처 재시도의 작업별 커밋 구간은 `committed_phase()`, 새로고침 스냅샷 커밋 뒤는 `after_commit()`로 표시한다. 그 뒤 세션에 없는 자료는 본문을 다시 실행하지 않고 `DRIVE_READ_NOT_PREPARED`로 실패한다(캡처 작업은 `FAILED`, 재시도 가능). 여러 의뢰를 등록하는 자동 탐색은 등록마다 표시가 풀린다.
- 세션 밖에서 연결을 쥔 채 드라이브를 읽으려 하면 드라이브를 부르지 않고 `DRIVE_READ_NOT_PREPARED`를 낸다(시험이 모든 가짜 드라이브 호출 시점에 연결이 없음을 확인).
- 감싼 API: 폴더 탐색(browse·scan·preview·apply), 환경 조사·미리보기·사용 검수·등록·캡처 재시도·새로고침, 자동 탐색(60초), 자동 반영(`/sync`, 60초), 의뢰 진척, Case 캡처·조사, 깊이 스키마 예시·점검, 소재 카탈로그·덱, 의미 매핑 폴더·연결·새로고침·검수, 결과 등록의 조회 API, Final 상태, 저장소 패널 조회. 그 밖의 SPDM 읽기는 scx 모드에서 `DRIVE_READ_NOT_PREPARED`로 실패한다(§8.7).

### 8.3 다운로드 → 등록 → 삭제, 서버 보관소 (05 §2–3)

- 세션마다 `<SIMDASH_SCX_STAGING_DIR>/read-<id>/`를 만들고 파일마다 하위 폴더에 받는다. 세션 하나의 임시 바이트 한도는 2 GiB이고 임시 디스크 여유가 1 GiB 아래로 내려가면 받지 않는다(`DRIVE_STAGING_FULL`; 미리 받기는 한도에서 멈춘다. 검수 L3). 받은 뒤 `stat` 기준 버전과 비교해 다르면 `SPDM_FILE_BUSY`(다음 확인에서 다시). 세션이 끝나면(성공·실패 무관) 폴더를 지운다. 하루 지난 `read-*` 폴더는 한 시간에 한 번 정리한다.
- 32 MiB 초과 파일은 지우지 않고 **서버 보관소**(`SIMDASH_DRIVE_BLOB_DIR/<sha256 앞 2자>/<sha256>`)로 옮긴다. 같은 버전을 다시 읽을 때는 보관소 파일의 sha256을 확인해 그대로 쓴다. 용량 상한을 넘으면 오래 안 쓴 것부터 지운다 — 지우는 대상은 `<64자리 hex>` 이름이 `<앞 2자>` 폴더 안에 있는 파일뿐이고 다른 파일은 건드리지 않는다. 보관소 폴더가 작업·임시 폴더와 같거나 그 상위이거나 임시 폴더 안이면 기동을 거부한다(`SIMDASH_DRIVE_BLOB_DIR`, 검수 L2).
- 세션이 성공하면 받은 파일의 sha256·보관 경로를 `drive_source_versions`에 기록한다(처음 읽은 파일은 새 버전 행 생성).
- **사용환경 소재(OptiStruct `.fem`, 500–1000 MB)**: 일반 읽기 한도 512 MiB와 별개로 `SIMDASH_OPTISTRUCT_MAX_BYTES`(기본 2 GiB)까지 받는다. 요청 스레드는 내려받지 않고, 백그라운드 분석 작업이 DB 연결 없이 자기 읽기 세션(`drive_reads.run`, 우선순위 BACKGROUND)으로 **한 번** 받아 보관소로 옮긴다(세션 임시 한도 2 GiB·여유 1 GiB 규칙 그대로). 분석 결과는 `materials_deck_cache`(migration 0039)에 `drive:<version_token>` 지문과 보관소 sha256으로 남아, 버전이 같으면 다시 받지도 분석하지도 않고, 분석기 버전만 바뀌면 보관소 파일을 다시 쓴다. `.fem`은 `drive_source_versions` 대상 확장자가 아니어서 원본 변경 확인(§8.4) 없이 현재 드라이브 버전을 읽는다. 자세한 규칙은 [사용환경 소재](materials-optistruct.md).
- 내용 지문(`scan_fingerprints`)은 scx 모드에서 파일을 읽지 않고 `version_token`을 쓴다. **변경 없는 자동 반영은 파일을 내려받지 않는다**(S2-5, 시험으로 확인). 전체 새로고침(새 파일·새 Scene·새 버전 등록 뒤)은 지금처럼 그 의뢰 Case의 캡처 파일을 다시 읽으므로 그때 내려받는다.

### 8.4 원본 비교·확인 (05 §4, 사용자 결정 §9 D3)

`drive_source_versions`(migration `0037`, DuckDB 동일): 결과 관련 파일(`.csv .json .jpg .jpeg .png .mp4 .webm .inc .rad .pdf .ppt .pptx .xlsx`)의 등록 버전. 의뢰 폴더(Final 보관 폴더·점 폴더 제외)를 자동 반영 때마다 드라이브 목록으로 비교한다.

| 드라이브 상태 | 처리 |
|---|---|
| 새 파일(현재 행 없음) — 새 Scene 포함 | 다음 번호(`MAX(version_no)+1`: 처음이면 1, 삭제 반영 뒤 다시 생긴 파일이면 n+1)로 **자동 등록**, 같은 확인에서 반영. 행을 저장하지 못하면 조용히 넘어가지 않고 오류(`DRIVE_SOURCE_VERSION_CONFLICT`) — 검수 M1 |
| 같은 버전 | 그대로(상태가 바뀐 행만 `last_checked_at` 갱신) |
| 다른 `version_token` | sha1이 양쪽에 있으면 sha1로, 없으면 크기·시각이 다르고 등록 sha256이 있을 때 내려받아 sha256으로 비교. 같으면 행 정보만 갱신. 다르면 `review_state=PENDING`, `source_state=CHANGED`, 드라이브 값은 `pending_*` — **자동 반영하지 않는다** |
| 목록에 없음 | `source_state=MISSING`. DB 자료는 지우지 않는다 |

- 확인 전까지 화면과 새로고침은 **등록된 버전**을 본다. 아직 어느 동기화도 분류하지 않은 드라이브 변경(등록 행 `review_state=NONE`인데 드라이브 token이 다름)도 같다(검수 M2, 실패 시 닫힘): 읽기 세션은 목록에 나온 결과 파일의 현재 등록 행을 짧은 연결로 함께 읽고, 의뢰 범위로 분류된 행(`project_id`·`request_id` 있음)과 드라이브 값이 다르면 등록 버전으로 읽는다. 그래서 변경과 다음 동기화 사이의 수동 캡처(`POST /api/dashboard/captures`)·캡처 재시도·새로고침도 확인 안 된 내용을 읽지 않는다(등록 내용이 서버에 없으면 `DRIVE_SOURCE_CHANGED`로 실패, 다음 동기화가 `PENDING`으로 기록). Final 계획도 이런 파일을 거부한다. 의뢰 범위 밖 행(분류하지 않는 파일)은 예전처럼 드라이브 값으로 읽는다.
- 수동 새로고침(`/refresh`)은 먼저 권한을 확인하고 분류한다. 분류가 실패하면(드라이브 오류·범위 오류) 새로고침을 그 코드로 거부하고 `FOLDER_ENVIRONMENT_REFRESH_FAILED`를 감사 기록한다(예전에는 오류를 무시하고 분류 없이 새로고침했다).
- 화면과 새로고침의 등록 버전 표시: 공급자가 그 파일의 크기·시각·token을 등록값으로 보여 주고(지문 불변 → 자동 반영 없음), 내용이 필요하면 보관소 또는 DB(`dashboard_assets`/`result_registration_files`의 같은 sha256)에서 꺼낸다. 없으면 `DRIVE_SOURCE_CHANGED`. DB 내용을 세션 임시 파일로 꺼낼 때 경로에 `\`·`:`·`..`가 있으면 같은 오류로 거부한다(파일 이름이 DB 값에서 오므로). MISSING 파일·폴더도 등록값으로 계속 보인다. 그래서 변경 대기 중에도 새 파일·새 Scene은 자동 반영된다.
- **[새 버전 등록]**: 먼저 DB 연결 없이 대기 중인 드라이브 버전을 한 번 내려받아 그 버전인지 확인하고 sha256을 기록한다(05 §4, 검수 L4; 32 MiB 초과는 보관소에 남고, 읽기 한도 512 MiB 초과 파일은 받지 않음). 그사이 드라이브가 다시 바뀌었으면 409 `DRIVE_SOURCE_CHANGE_STALE`(다음 동기화가 새 값으로 다시 묻는다). 확인한 행만 다음 번호(`MAX+1`) 행으로 추가하고 이전 행은 `superseded_at`만 채워 보존. 다음 자동 반영이 그 파일을 캡처한다. MISSING 행은 "삭제 반영"(행 종료, 다음 새로고침에서 스냅샷에서 빠짐 — 사용자가 확인한 경우에만).
- **[무시]**: `IGNORED` — 등록 버전을 계속 쓰고 같은 드라이브 버전은 다시 묻지 않는다. 다시 바뀌면 다시 묻는다.
- 확인 단위는 의뢰(전체) 또는 파일(`ids`). 권한은 의뢰 결과 등록 권한(`result.import`, 전역 관리자 포함). 결정하면 그 의뢰의 자동 반영 결과 캐시를 비워 다음 확인(1분 안, 또는 "지금 확인")에 반영된다.
- `version_token`은 sha1이 있으면 `sha1:<hex>:<size>`, 없으면 `t:<size>:<µs>`. sha1은 소문자 40자리 hex만 쓰고 그 밖의 값은 없음으로 본다(PostgreSQL `VARCHAR(40)`와 DuckDB 동일, 검수 L5). 실제 드라이브 목록에 sha1이 있는지(C1·C9)는 아직 모르므로 둘 다 지원하고, 관리 상태 `version_tokens`와 자동 반영 결과 `drive.token_kind`·로그(`SCX drive listing version_token: …`)에 실제로 쓴 형태를 남긴다.

### 8.5 자동 반영(60초) 흐름

`POST /api/folder-discovery/environments/sync`(scx): 권한 확인(짧은 연결) → 합치기 간격 확인 → 의뢰별 드라이브 잠금(연결 없이) → 읽기 세션: ① 분류(§8.4, 드라이브 목록은 연결 없이, DB는 짧은 연결) ② 기존 빠른 확인·새로고침(연결 → 의뢰 범위 잠금, local 모드·등록 삭제와 같은 잠금 순서) → 결과에 `drive: {pending_changes, missing, ignored, token_kind, new_files}`. 드라이브 오류(`BUSY`·`TIMEOUT`·`AUTH_REQUIRED` 등)는 `FAILED`+코드로 끝나고 등록 버전은 바뀌지 않으며 다음 주기에 다시 확인한다.

### 8.6 화면 (데스크톱)

- Case 결과·소재 탭의 폴더 자동 확인 줄: `원본 변경 n · 확인 필요 · 원본 없음 m` 배지 → **드라이브 원본 변경** 대화상자(파일·상태·등록 버전·드라이브 값, 파일별/전체 [새 버전 등록]·[무시], 권한이 없으면 목록만).
- 드라이브 읽기 전용(`writes_available=false`): 결과 등록 화면은 안내 문구와 함께 새 폴더 만들기·파일/폴더 선택·끌어 놓기를 끈다. Final 지정·재시도·요약 갱신 버튼을 끄고 "드라이브 읽기 전용" 표시. 저장소 패널은 업로드 카드 대신 안내, "저장 폴더 새로고침"(폴더 생성 동반) 끔.
- 관리 › 드라이브 관리: "드라이브 쓰기"(읽기 전용 · 쓰기 허용 설정 켜짐/꺼짐), "원본 지문"(관찰한 token 형태와 파일 수).

### 8.7 D2에서 남긴 것

- 쓰기 경로(D3에서 구현, §10). 결과 등록 초안 게시·복사 재시도·폴더 준비와 저장소 패널 연결·업로드는 D3 이후에도 scx에서 409 `DRIVE_WRITE_DISABLED`.
- 읽기 세션으로 감싸지 않은 SPDM 읽기: 저장소 패널 파일 다운로드(스트리밍 응답), 결과 등록의 초안 생성·검사·위치 연결 등 쓰기와 섞인 API — scx에서 `DRIVE_READ_NOT_PREPARED` 또는 `DRIVE_WRITE_DISABLED`(D3에서 결과 등록과 함께 정리). 저장소 패널 연결 파일 목록의 sha1 체크섬(03 8-3)도 D3·D4.
- 32 MiB 초과 영상의 "드라이브에서 불러오는 중" 표시, 화면 폴더 선택기(D4). 같은 파일 `BUSY` 3회 연속 "사용 중" 배지 미구현(`FAILED`+`SPDM_FILE_BUSY`로 표시).
- 서버 임시·보관소 정리는 읽기 세션 단위와 한 시간 주기 고아 정리만 있다(업로드 대기 파일 정리는 D3).
- 등록 직후 첫 동기화 전(행이 아직 의뢰 범위로 분류되기 전)의 드라이브 변경은 등록 버전으로 가리지 않는다(분류 없는 행은 확인 대상이 될 수 없어 영구 차단을 피함). 보통 1분 안의 자동 반영이 분류한다.

## 9. 코드 위치와 경계

| 위치 | 역할 |
|---|---|
| `backend/app/services/drive/config.py` | 환경변수 읽기·검증, 경로 규칙(contract §1) |
| `backend/app/services/drive/gateway.py` | 모드별 기동 검사, 어댑터 지연 import, `dashboard.lock`, `WorkerDriveGateway` 싱글턴, 계약 Protocol 사본, `DriveError` → `SpdmStorageError`/HTTP 대응표 |
| `backend/app/services/drive/token_store.py` | `DbTokenStore`(관리자용 1개 + 게이트웨이마다 `for_gateway()`), 세대 번호 차단 |
| `backend/app/services/drive/check.py` | 드라이브 점검 |
| `backend/app/routers/drive.py` | API(관리·상태·원본 변경) |
| `backend/app/services/drive/reads.py` | D2 읽기 세션·연결 없는 드라이브 호출·다운로드·보관소·`version_token` |
| `backend/app/services/drive/sources.py` | D2 `drive_source_versions` 분류·확인(새 버전 등록·무시)·MISSING |
| `backend/app/services/drive/writes.py` | 쓰기 가능 여부(`SIMDASH_DRIVE_WRITES_ENABLED`)·`require_drive_writes`(D3 대상)·`require_local_writes`(scx에서 항상 409) |
| `backend/app/services/drive/upload_queue.py` | D3 업로드 대기열(유일한 드라이브 쓰기 모듈: `mkdirs`·`copy_within`·`upload_new`, 확인용 `stat`·대체 경로 `download_to`)·작업자 스레드·관리 기능 |
| `backend/app/services/case_finalization_drive.py` | D3 scx Final(계획·보고서·완료 기록 DB, 대기열 묶음, 현재 Final 순번 파일) |
| `backend/app/services/storage/drive.py` | `DriveRoot`·`DriveStorageProvider`(읽기 전용 공급자) |
| `backend/app/services/storage/server_local.py` | 서버 임시 저장소·보관소 파일 조작(드라이브 패키지에는 삭제·교체 호출 없음) |
| `backend/migrations/versions/0036_drive_credentials.py`, `0037_drive_source_versions.py`, `0038_drive_write_path.py` | PostgreSQL 테이블(DuckDB는 `database.py`) |
| `backend/tests/drive_fakes.py` | **시험 전용** 가짜 어댑터(메모리 드라이브). `app/`에서 import하지 않으며 배포물에 쓰이지 않는다 |
| `frontend/src/features/storage/DriveAdminPanel.tsx`(대기열 포함), `shared/components/DriveStatusBanner.tsx`, `shared/components/DriveBatchProgress.tsx`, `shared/api/drive.ts`, `shared/hooks/useDriveWriteStatus.ts`, `features/results/DriveSourceChangesDialog.tsx` | 화면 |

정적 시험이 `app/services/drive`와 `routers/drive.py`에서 삭제·이동·덮어쓰기 계열 호출(`rm`, `mv`, `move`, `rename`, `replace`, `remove`, `delete`, `unlink`, `force=` 등)을 금지한다. D2 시험(`test_drive_reads.py`)은 읽기 모듈이 게이트웨이의 `list_dir`·`stat`·`download_to`만 부르는지, D3 시험(`test_drive_writes.py`)은 드라이브 쓰기 메서드(`upload_new`·`mkdirs`·`copy_within`)를 부르는 모듈이 `upload_queue.py` 하나뿐인지 정적으로 확인한다.

## 10. 쓰기 경로 (D3)

### 10.1 쓰기 허용과 범위

- `SIMDASH_DRIVE_WRITES_ENABLED=true` + SPDM 루트(`SIMDASH_SCX_DRIVE_ROOT`)가 있을 때만 `writes_available=true`. 그 밖에는 D2와 같이 읽기 전용(409 `DRIVE_WRITE_DISABLED`). 설정 변경은 재시작 후 반영.
- 드라이브에 쓰는 코드는 **업로드 대기열 작업자 하나**(`services/drive/upload_queue.py`)뿐이다. `DriveStorageProvider`의 쓰기 메서드는 계속 `DRIVE_WRITE_DISABLED`.
- 대상 기능: 결과 등록(끌어놓기) 올리기·새 폴더 만들기(§10.3), Final 지정·보고서 업로드·확정·재시도·요약 갱신(§10.4–10.5).
- scx에서 계속 끈 기능(`require_local_writes`, 설정과 무관하게 409): 결과 등록 초안의 폴더 준비·게시·복사 재시도(이전 흐름), 저장소 패널의 새로고침·연결·업로드(integration 03 8-6: 업로드는 결과 등록으로 일원화). 화면은 저장소 패널에 "드라이브 모드에서는 이 화면에서 업로드할 수 없습니다" 안내.

### 10.2 업로드 대기열 (integration 05 §5, 04 §2.3)

`drive_upload_queue`(migration `0038`, DuckDB 동일): 묶음(`batch_id`) 안 순서(`seq`)대로 실행하는 항목.

| 종류 | 드라이브 호출 | 비고 |
|---|---|---|
| `MKDIR` | `mkdirs` | 이미 있는 폴더는 성공 |
| `COPY` | `copy_within`(드라이브 안 복사) | 복사 전 원본 `stat`으로 계획 때 `version_token`과 비교(다르면 `FINALIZATION_SOURCE_STALE`, 실패). `INTERNAL`·`INVALID_PATH`·`FORBIDDEN`은 그 항목만 `download_to`(서버 `xfer-*` 임시 폴더) → `upload_new`로 대체한다. `INTERNAL`이 **연속 3번**(서로 1시간 안) 나면 복사 미지원(C7 미확인)으로 보고 1시간 동안 모든 복사를 대체 경로로 보낸 뒤 다시 `copy_within`을 시도한다(어댑터에 메서드가 없으면 프로세스 동안 대체). 대체 경로는 내려받기 전에 서버 임시 공간 여유(1 GiB + 파일 크기)를 확인하고 모자라면 기다린다(`DRIVE_STAGING_FULL`). 항목의 `transfer_method`(`COPY_WITHIN`/`DOWNLOAD_UPLOAD`)와 경고 로그에 남는다 |
| `FILE` | `upload_new` | 서버 임시 파일(`<SIMDASH_SCX_STAGING_DIR>/upload-<묶음>/…`)을 새 이름으로만 올린다. 올리기 전 임시 파일 sha256을 기록과 비교 |
| `COMPLETE_MARKER` | `upload_new` | 묶음의 마지막 항목. 내용은 앞 항목이 모두 `DONE`일 때 만든다(Final `complete.json`) |

규칙:

- **덮어쓰기·삭제·이동 없음.** `CONFLICT`(같은 이름 존재)이면 대상을 `stat`해 같은 내용이면 `DONE`, 아니면 `CONFLICT`로 멈춘다(화면·관리자에게 표시). 같은 내용 판정: 양쪽 sha1이 있으면 sha1. 한쪽이라도 없으면 **크기만으로는 판정하지 않고** 대상 파일을 서버 `xfer-*` 임시 폴더로 내려받아(DB 연결 없이, 항목 크기까지만) sha256을 서버 임시 파일·계획 sha256과 비교한다(COPY에 계획 sha256이 없으면 원본도 같은 방식으로 내려받아 비교). `SIMDASH_DRIVE_VERIFY_MAX_BYTES`(기본 2 GiB)보다 크면 내려받지 않고 `CONFLICT`(`SPDM_CONFLICT_UNVERIFIED`) — 관리자가 드라이브에서 확인한 뒤 다시 시도·취소한다. 서버 임시 공간이 1 GiB 여유를 남기지 못하면 기다린다(`DRIVE_STAGING_FULL`, 재시도 간격).
- **재시도 전 확인:** 두 번째 시도부터는 먼저 대상을 `stat`한다(응답을 잃은 업로드가 이미 올라갔을 수 있음 — 같으면 `DONE`, 중복 업로드 없음).
- **재시도 가능 오류** `BUSY`·`LOCKED`·`TIMEOUT`·`OVERLOADED`·`UNAVAILABLE`: 30초 → 2분 → 10분 → 30분 → 1시간 간격, `SIMDASH_DRIVE_UPLOAD_MAX_ATTEMPTS`(기본 8)회를 넘으면 `FAILED`. 대기 중인 항목 뒤의 같은 묶음 항목은 기다린다(순서 유지).
- **`AUTH_REQUIRED`**: 항목을 `BLOCKED`로 두고 **대기열 전체를 멈춘다**(`queue_paused`). 관리자가 토큰을 다시 등록하면 자동 재개(또는 관리 화면 "대기열 재개").
- **묶음 정지:** `halt_on_error` 항목(Final 전부, 결과 등록의 `MKDIR`)이 `FAILED`·`CONFLICT`·`CANCELLED`로 끝나면 그 묶음의 뒤 항목은 실행하지 않는다 — Final은 `complete.json`이 없어 미완료로 판정된다. 이미 드라이브에 쓴 것은 지우지 않는다. 결과 등록의 `FILE`은 서로 독립이라 하나가 충돌해도 나머지는 올린다(묶음 상태 `PARTIAL`).
- **재시작:** 앱 기동 시 `RUNNING`으로 남은 항목은 `PENDING`으로 돌아가고 재시도 전 확인 규칙으로 이어서 실행된다.
- **결과 기록 실패·정리(sweep):** 드라이브 호출 뒤 결과를 DB에 기록하지 못하면 짧은 연결로 몇 번 다시 기록한다. 그래도 실패하면 행은 `RUNNING`으로 남고, 작업자가 1분마다 돌리는 정리가 이 프로세스에서 실행 중이 아닌 `RUNNING` 항목을 `PENDING`으로 되돌린다(다음 시도는 재시도 전 확인 규칙으로 이미 올라간 파일을 알아본다). 같은 정리가 묶음이 이미 끝났는데 `PUBLISHING`으로 남은 Final의 마무리(완료·실패 판정, 잠금 해제)를 다시 실행한다(마무리 훅 실패, 관리자 취소 뒤 깨우기 유실).
- **DB 연결:** 작업자는 짧은 연결로 항목을 잡고(`RUNNING`), **연결 없이** 드라이브를 부른 뒤, 짧은 연결로 결과를 기록한다(§8.2와 같은 이유; 시험이 모든 가짜 드라이브 호출 시점에 연결이 없음을 확인).
- **임시 파일:** 항목이 `DONE`이 되기 전에는 지우지 않는다. Final 묶음은 보고서·`plan.json` 임시 파일을 **묶음 전체가 `DONE`일 때까지** 남긴다(같은 Final ID 재시도). 묶음이 모두 `DONE`이면 `upload-<묶음>` 폴더를 지운다. 기동 시와 그 뒤 1시간마다 하루 지난 `upload-*`·`xfer-*`를 지우되, 아직 진행할 수 있는 묶음(묶음 요약 `QUEUED`·`RUNNING`·`PAUSED`)이나 `PUBLISHING` Final이 쓰는 것은 남기고, 관리자 판단을 기다리는 묶음(`CONFLICT`·`FAILED` 항목, 또는 `halt_on_error` 항목이 멈춰 뒤 항목이 `PENDING`으로 남은 정지 묶음)과 미완료 Final(`PLANNED`·`STAGED`·`FAILED`)의 것은 마지막 활동 후 **7일**까지 남긴다(서버 임시 저장소만, 드라이브는 건드리지 않음). 7일 뒤 재시도하면 보고서·파일을 다시 올려야 한다(Final 재시도는 지워진 `plan.json` 임시 파일을 DB `plan_json`에서 다시 만들고, 대기열에 기록된 sha256과 다르면 422 `FINALIZATION_PLAN_STAGE_INVALID`로 멈춘다).
- **단일 작업자:** 프로세스당 스레드 1개(`dashboard.lock`으로 프로세스도 하나). 묶음은 만든 순서대로, 묶음 안은 `seq` 순서.
- 묶음 상태: `QUEUED`·`RUNNING`·`PAUSED`(인증 대기)·`DONE`·`PARTIAL`·`CONFLICT`·`FAILED`·`CANCELLED`. 항목 상태: `PENDING`·`RUNNING`·`DONE`·`CONFLICT`·`FAILED`·`BLOCKED`·`CANCELLED`.
- 관리자 취소는 항목을 `CANCELLED`로 표시만 한다(드라이브 내용 불변). 묶음 마무리(Final 실패 판정·잠금 해제)는 취소가 커밋된 뒤에 실행한다. 다시 시도는 같은 항목을 처음부터(충돌은 드라이브 재확인). Final 묶음 항목의 다시 시도는 의뢰 잠금을 먼저 잡는다(다른 Final이 반영 중이면 409 `FINALIZATION_LOCKED`).
- **항목 잡기:** 조건부 `UPDATE … WHERE state='PENDING' RETURNING`으로 자기 잡기를 확인한다.

### 10.3 결과 등록(끌어놓기) — integration 05 §5.3, W8

- 계획(`plan`)·시작은 D2 읽기 세션에서 드라이브 목록으로 검사한다(Working 범위·깊이·이름·기존 파일 충돌은 local과 같은 규칙, 같은 이름이 있으면 거부 — 이름 바꾸기·덮어쓰기 없음). 남은 공간은 **서버 임시 저장소**의 여유 공간으로 본다(드라이브는 여유 공간을 알려 주지 않음).
- 조각은 서버 `upload-<세션 ID>/<n>.part`에 받는다(드라이브에 임시 폴더를 만들지 않음 — 드라이브에서는 이름 바꾸기·삭제를 할 수 없으므로).
- **올리기 완료(`complete`)**: 의뢰·대상 폴더·충돌을 다시 확인한 뒤 묶음 하나(묶음 ID = 세션 ID)를 대기열에 넣고 즉시 `state: QUEUED`로 응답한다. `MKDIR`(새 폴더, 얕은 것부터) → 파일마다 `FILE`. 감사 `RESULT_DROP_UPLOAD_QUEUED`(실제 사용자, 묶음 ID).
- 화면은 묶음 진행을 2초마다 조회해 "드라이브 반영 대기/중/완료/충돌/일시 정지"를 보여 준다. 묶음이 끝나면 다음 자동 반영(60초, D2 읽기 경로)이 새 파일·새 Scene을 Case 결과에 넣는다(묶음 완료 시 의뢰 자동 반영 캐시를 비움).
- **새 폴더 만들기**: 검사 후 `MKDIR` 묶음을 넣고 최대 20초 기다린다(연결 없이). 끝나지 않으면 "드라이브에 만드는 중" 안내.
- scx 모드에서는 "경로 복사"(탐색기용)를 숨기고 드라이브 경로(`scx://<호스트>/<루트>/…`)만 표시한다.

### 10.4 Final 지정 — integration 05 §6

| 항목 | local 모드 | scx 모드 |
|---|---|---|
| 의뢰 잠금 `.request.lock` | 파일 | `drive_locks` 행 `final:<프로젝트>/<의뢰>`(확정부터 묶음이 끝날 때까지, 30분 만료·작업자가 연장). 잠금을 가진 Final이 `PUBLISHING`이면 만료 시각이 지나도(인증 대기로 대기열이 멈춘 경우) 잠금으로 본다. 넘겨받기는 읽은 행이 그대로일 때만(조건부 `UPDATE … RETURNING`, PostgreSQL은 `FOR UPDATE`). 같은 의뢰의 다른 Final 확정·관리자 대기열 다시 시도는 409 `FINALIZATION_LOCKED`. 관리자 대기열 다시 시도는 잠금을 잡는 같은 트랜잭션에서 `FAILED` Final을 `PUBLISHING`으로 되돌린다(다시 멈추면 마무리 훅이 `FAILED`·잠금 해제, 다시 시도가 거부되면 둘 다 되돌림) |
| `plan.json` | `.finalizations/<op>/` | `finalization_operations.plan_json`(서명 동일, 파일마다 `version_token` 추가). 확정 때 드라이브 `.finalizations/<op>/plan.json`에 사본 1회(불변) |
| 보고서 업로드·`reports.json` | `.finalizations/<op>/reports/` | 서버 `upload-<op>/reports/<이름>` + `reports_json`(DB). 확정 전에는 다시 올려 교체 가능, 대기열에 들어간 뒤에는 같은 바이트만 허용 |
| 원본 → `Final/CAE/<Case>/<op>/…` | 임시 폴더 복사 → 이름 바꾸기 | `COPY` 항목(`copy_within`, 지원하지 않으면 다운로드→업로드) |
| 보고서 → `Final/Report/<Case>/<op>/` | 이름 바꾸기 | `FILE` 항목(`upload_new`) |
| `complete.json` | 마지막에 쓰기 | `COMPLETE_MARKER` — **묶음의 마지막**. 형식은 local과 같은 서명 기록(`schema_version` 3)에 `storage: "scx"`, 파일마다 `sha1`(드라이브 결과)·`transfer_method`. 수집 결과 파일 외에는 `sha256`이 `null`(서버가 내용을 읽지 않음). DB에도 사본(`complete_json`, `complete_sha256`) |
| 상태·진척 | 드라이브 스캔·해시 | DB(`finalization_operations` + 대기열). 완료 기록의 `verification`은 `DRIVE_UPLOAD`(드라이브 파일 내용은 다시 읽지 않음) |
| 정리 삭제 | 앱이 만든 임시만 | 없음(드라이브는 지우지 않음, 서버 임시 파일만) |

**재시도**에서 이미 드라이브에 올라간(`DONE`) 보고서는 서버 임시 파일을 다시 확인하지 않는다(화면의 [재시도]는 보고서를 다시 올리지 않음).

확정 순서: 권한 → 서명 계획·범위 → 드라이브 원본 재확인(읽기 세션: 계획 이후 `version_token`이 바뀐 파일이 있으면 409 `FINALIZATION_SOURCE_STALE`, 원본 변경 확인 대기·무시·원본 없음 파일이 있으면 미리보기부터 거부) → 보고서 해시 확인 → 의뢰 잠금 → 묶음 등록(`MKDIR`… → `COPY`… → 보고서 `FILE` → `plan.json` → `COMPLETE_MARKER`) → 응답(`state: QUEUED`, `drive` 묶음 요약). 묶음이 `DONE`이면 `COMPLETE`, 현재 Final 순번 부여(§10.5), 잠금 해제. 실패·충돌·취소면 `FAILED`(첫 오류 코드), 잠금 해제, `complete.json` 없음(미완료). **재시도**는 같은 Final ID로 멈춘 항목만 다시 실행한다(이미 복사한 CAE는 다시 복사하지 않음).

### 10.5 현재 Final 요약 — 순번 파일 (결정 D2 ①, SPDM 협의 대상)

드라이브는 교체가 안 되므로 `Final/current.json`(local W3)을 쓰지 않는다. Final이 완료될 때마다 **새 파일을 추가**한다.

- 경로: `<의뢰>/Final/.finalizations/designations/<순번 8자리>-<Final ID>.json` (예 `00000003-4f…e1.json`). 순번은 의뢰마다 1부터 증가(DB `designation_seq`, 의뢰 안 유일). 순번 배정(Final 완료·요약 갱신)은 의뢰마다 직렬화한다(PostgreSQL 트랜잭션 advisory lock, DuckDB는 프로세스 안 연결 직렬화) — 같은 Final의 요약 갱신이 동시에 두 번 와도 순번 파일은 하나만 대기열에 들어간다.
- **SPDM은 순번이 가장 큰 파일을 현재 요약으로 읽는다.** 같은 순번은 없다. 이전 파일은 그대로 남는다(이력).
- 내용(형식 `simdashboard-final-summary`, `schema_version` 1, local `current.json`과 같은 환경 항목): `designation_seq`, `final_id`, `environment`(이번 지정의 환경), `environments`(그 시점 환경별 현재 Final 항목: `final_id`·`case_label`·`case_relative_path`·`designated_by`·`designated_at`·`cae_path`·`report_path`·`reports`·`files`(또는 `files_in`)·`complete_record`·`complete_sha256`·`previous_final_id`), `storage: "scx"`. 경로는 `Final` 폴더 기준.
- 재지정(같은 Case·다른 Case 모두) = 새 순번 파일. **요약 갱신**(`POST /summary/repair`)은 현재 Final로 새 순번 파일을 하나 더 올린다(앞 파일을 고치지 않음). 상태의 `summary.state`: 순번 파일 업로드 완료 `OK`, 대기열 진행 중 `PENDING`, 실패·충돌 `MISSING`(화면 "요약 파일 갱신" 버튼), 완료된 Final 없음 `NONE`.
- 현재 Final(화면·상태 API)은 DB의 가장 큰 순번이다.

### 10.6 쓰기 수용 시험 절차 (C2·C7, 수동)

관리자 드라이브 점검은 조회 전용이므로(결정 2026-10-07 20:12) 덮어쓰기 금지 업로드(C2)와 드라이브 안 복사(C7)는 **일반 기능으로 전용 시험 폴더에서** 확인한다. 실데이터 폴더에서 하지 않는다. 새 자동 쓰기 점검은 없다.

준비: 드라이브에 시험 전용 의뢰 트리(예 `SPDM_ROOT/_simdash_write_test/<프로젝트>/[WR-TEST]_[유통_환경]/Working/<Case>/…/<Scene>/결과.csv`)를 만들어 둔다 → `SIMDASH_DRIVE_WRITES_ENABLED=true`로 재시작 → 폴더 탐색으로 그 의뢰만 등록·수집.

| 번호 | 절차 | 기대 결과 | 기록 |
|---|---|---|---|
| C2-1 | 결과 등록에서 Scene 폴더에 새 파일 `c2.csv` 올리기 | 묶음 `DONE`, 드라이브에 `c2.csv` 생성, 1분 안에 결과 반영 | 관리 › 업로드 대기열 항목(`upload_new`) |
| C2-2 | 같은 Scene에 큰 파일 여러 개(예 50 MB × 10, 이름 `c2_01.bin`…`c2_10.bin`)를 한 번에 올린다. 대기열이 앞 파일을 올리는 동안(관리 › 업로드 대기열에서 `진행` 확인) 드라이브 웹·다른 PC로 같은 폴더에 **다른 내용**의 `c2_10.bin`을 만든다 | 마지막 항목 `CONFLICT`(`SPDM_CONFLICT`), 나머지 9개 `DONE`, 묶음 `PARTIAL`, **드라이브의 `c2_10.bin` 내용·수정 시각 불변** | 어댑터 `upload_new`(`force=False`)가 거부했는지 — C2 통과 |
| C2-3 | C2-2와 같되 드라이브에 만드는 `c2_10.bin`이 **같은 내용** | 항목 `DONE`(같은 내용 판정). 드라이브 sha1이 없으면 `CONFLICT`(보수적) — 어느 쪽인지 기록 | 항목 `result_sha1` 유무(C1·C9) |
| C2-4 | 결과 등록 계획 단계에서 이미 있는 이름을 올리려 한다 | 올리기 전 차단(`CONFLICT` 문제), 드라이브 쓰기 없음 | — |
| C7-1 | 그 의뢰 Case를 Final 지정(HTML 보고서) | 묶음 `DONE`, `Final/CAE/<Case>/<op>/…`·`Final/Report/<Case>/<op>/`·`.finalizations/<op>/plan.json`·`complete.json`(마지막)·`designations/00000001-<op>.json` 생성, Working 불변 | 관리 › 대기열의 `transfer_method`: `copy_within`이면 C7 지원, `다운로드→업로드`면 미지원(로그 `copy_within unsupported`) |
| C7-2 | 같은 Case를 다시 Final 지정 | 새 `<op2>` 폴더·`00000002-<op2>.json`, 이전 Final·순번 파일 불변 | 상태 화면 현재/이전 Final |
| C7-3 | Final 확정 직후(대기열 진행 중) 원본 Scene 파일 하나를 드라이브에서 수정 | 해당 `COPY` `FAILED`(`FINALIZATION_SOURCE_STALE`), `complete.json` 없음, 이미 복사된 파일은 남음(삭제 없음) | 화면 "Final 복사 실패" |
| S3-9 | 전체 시험 뒤 드라이브 감사 로그 | 삭제·이동·덮어쓰기 0건 | 드라이브 감사 |

결과(C2·C7, `transfer_method`, 소요 시간)는 이슈 #45 측정 기록 표에 남긴다. 시험 폴더는 사용자가 드라이브에서 직접 정리한다(앱은 지우지 않음).

### 10.7 화면 (데스크톱)

- 결과 등록: 올리기 후 "n개 파일을 서버에 받았습니다 · 드라이브 업로드 대기열" + 묶음 진행(막대, 파일 n/m, 현재 항목, 인증 대기 안내, 충돌 목록 — "덮어쓰거나 지우지 않음"). 쓰기 허용이 꺼져 있으면 D2와 같은 읽기 전용 안내.
- Final 지정: 배지·창의 진행 문구가 "Final 드라이브 반영 대기/중 n%", 인증 대기 시 "Final 일시 정지(드라이브 인증 필요)". 완료 기록에 "드라이브 반영 기록" 표시, 요약 파일 대기 중 "요약 파일 드라이브 반영 중".
- 관리 › 드라이브 관리 › **업로드 대기열**: 상태별 개수·필터, 항목(기능·작업·대상·시도·오류·전송 방식), 다시 시도·취소(확인 창: 드라이브 내용은 지우지 않음)·대기열 재개, 쓰기 설정 꺼짐·작업자 미실행·일시 정지 경고.
- 상단 상태 API의 `queue_paused`로 일시 정지 여부를 알 수 있다.

### 10.8 D3에서 남긴 것

- 실제 드라이브 확인(C1·C2·C7·C9, 대용량 Final의 `copy_within` 시간·대체 경로 대역폭) — §10.6 절차로 D6.
- 결과 등록 초안(이전 흐름)의 드라이브 게시, 저장소 패널 연결·업로드(계속 409). 저장소 패널 다운로드 읽기 세션화(D2 남은 일).
- 큰 Final의 진척은 파일 단위(묶음 항목)로만 보인다(`copy_within` 한 건 안의 바이트 진척 없음).
- `complete.json`의 비수집 파일 `sha256`은 `null`(드라이브 sha1로 대신). SPDM이 sha256을 요구하면 협의 필요.
- 순번 파일 방식은 SPDM 협의 전 임시안(결정 D2 ①).
- **사용자 결정 대기(D3 검수 #11):** 프로젝트 삭제(정리)는 대기열 행·Final 메타를 남기며(`project_cleanup.KEEP_COLUMNS`), **열린 대기열 항목(`PENDING`·`BLOCKED`)을 멈추지 않는다** — 삭제 뒤에도 작업자가 그 프로젝트의 남은 파일·Final을 드라이브에 올리고, Final 마무리 훅은 지워진 의뢰 기준으로 실행된다. 삭제 전에 남은 항목을 취소할지, 삭제가 항목을 `CANCELLED`로 표시할지, 그대로 둘지는 결정 후 반영한다(현재 동작 유지, 관리자는 업로드 대기열에서 해당 항목을 직접 취소할 수 있다).
- 결과 등록 완료(`complete`)는 라우터 커밋 뒤에 세션을 등록 목록에서 뺀다. 커밋하지 못했으면(묶음 행 없음) 같은 세션으로 다시 완료할 수 있다.
