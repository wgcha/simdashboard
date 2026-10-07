# SCX 드라이브 연동 — 설치·설정·토큰·점검·읽기 경로 (D0–D2)

- 기준일: 2026-10-07
- 상태: **D0(기반)·D1(공급자 주입)·D2(읽기 경로) 구현(2026-10-07, 브랜치 `claude/scx-drive`).** scx 모드에서 SPDM 폴더 기능은 드라이브를 **읽기 전용**으로 쓴다. 드라이브 쓰기(D3: 업로드 대기열·Final 재설계)는 아직 없다. 계획은 [SCX 드라이브 연동 계획](../plans/scx-drive-integration.md).
- 근거: GitHub 이슈 #45의 어댑터 계약 v0.2(contract §2–4, §10)와 연동 문서 01·02·04·06
- 배포 영향: [ADR 0006](../adr/0006-scx-drive-adapter-external-wheel.md) — 어댑터 wheel은 대시보드 패키지에 넣지 않고 따로 설치한다.

## 1. 무엇이 바뀌나

| 모드 | 동작 |
|---|---|
| `SIMDASH_DRIVE_GATEWAY=none` (기본, 미설정 포함) | **지금과 같다.** 어댑터 패키지를 불러오지 않고, 관리자 드라이브 API는 409 `DRIVE_MODE_DISABLED`, 상태 API는 `mode: "none"`, 상단 배너 없음 |
| `SIMDASH_DRIVE_GATEWAY=scx` | 기동 시 설정·어댑터 설치·단일 프로세스를 확인하고 하나라도 어긋나면 **기동을 거부**한다. 관리자 화면에서 공용 계정 토큰 등록·연결 시험·드라이브 점검을 할 수 있다. **D2부터 SPDM 루트는 드라이브의 `SIMDASH_SCX_DRIVE_ROOT`**(§8): 폴더 탐색·자동 탐색·자동 반영(60초)·Case 결과 캡처·진척·소재·의미 매핑은 드라이브에서 읽는다. 드라이브에 쓰는 기능(결과 등록 업로드·폴더 만들기·Final 지정·저장소 패널 업로드)은 409 `DRIVE_WRITE_DISABLED`(읽기 전용, D3 이후) |

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
| `SIMDASH_DRIVE_WRITES_ENABLED` | — | `false` | **드라이브 쓰기 허용**(사용자 결정 2026-10-07 20:25). D2에서는 상태 API·관리 화면에 표시만 하고, 쓰기 기능은 값과 관계없이 꺼져 있다. D3 업로드 대기열이 이 값을 따른다. `true/false/1/0/on/off` 외 값은 기동 거부 |
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
| GET | `/api/drive/status` | 로그인 사용자 | `{mode, state, writes_enabled, writes_available}`(none 모드 `writes_available=true`) |
| GET | `/api/drive/source-changes?project_id&request_id` | 의뢰 조회 권한 | 의뢰의 원본 변경·원본 없음 목록과 개수(§8.4). 드라이브 호출 없음 |
| POST | `/api/drive/source-changes/accept` | 의뢰 결과 등록 권한(`result.import`) | `{project_id, request_id, ids?}` → 새 버전 등록(전체 또는 `ids`). 감사 `DRIVE_SOURCE_CHANGES_ACCEPTED` |
| POST | `/api/drive/source-changes/dismiss` | 의뢰 결과 등록 권한 | 무시(같은 드라이브 버전은 다시 묻지 않음). 감사 `DRIVE_SOURCE_CHANGES_DISMISSED` |

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
- 세션 밖에서 연결을 쥔 채 드라이브를 읽으려 하면 드라이브를 부르지 않고 `DRIVE_READ_NOT_PREPARED`를 낸다(시험이 모든 가짜 드라이브 호출 시점에 연결이 없음을 확인).
- 감싼 API: 폴더 탐색(browse·scan·preview·apply), 환경 조사·미리보기·사용 검수·등록·캡처 재시도·새로고침, 자동 탐색(60초), 자동 반영(`/sync`, 60초), 의뢰 진척, Case 캡처·조사, 깊이 스키마 예시·점검, 소재 카탈로그·덱, 의미 매핑 폴더·연결·새로고침·검수, 결과 등록의 조회 API, Final 상태, 저장소 패널 조회. 그 밖의 SPDM 읽기는 scx 모드에서 `DRIVE_READ_NOT_PREPARED`로 실패한다(§8.7).

### 8.3 다운로드 → 등록 → 삭제, 서버 보관소 (05 §2–3)

- 세션마다 `<SIMDASH_SCX_STAGING_DIR>/read-<id>/`를 만들고 파일마다 하위 폴더에 받는다. 받은 뒤 `stat` 기준 버전과 비교해 다르면 `SPDM_FILE_BUSY`(다음 확인에서 다시). 세션이 끝나면(성공·실패 무관) 폴더를 지운다. 하루 지난 `read-*` 폴더는 한 시간에 한 번 정리한다.
- 32 MiB 초과 파일은 지우지 않고 **서버 보관소**(`SIMDASH_DRIVE_BLOB_DIR/<sha256 앞 2자>/<sha256>`)로 옮긴다. 같은 버전을 다시 읽을 때는 보관소 파일의 sha256을 확인해 그대로 쓴다. 용량 상한을 넘으면 오래 안 쓴 것부터 지운다.
- 세션이 성공하면 받은 파일의 sha256·보관 경로를 `drive_source_versions`에 기록한다(처음 읽은 파일은 버전 1 생성).
- 내용 지문(`scan_fingerprints`)은 scx 모드에서 파일을 읽지 않고 `version_token`을 쓴다. **변경 없는 자동 반영은 파일을 내려받지 않는다**(S2-5, 시험으로 확인). 전체 새로고침(새 파일·새 Scene·새 버전 등록 뒤)은 지금처럼 그 의뢰 Case의 캡처 파일을 다시 읽으므로 그때 내려받는다.

### 8.4 원본 비교·확인 (05 §4, 사용자 결정 §9 D3)

`drive_source_versions`(migration `0037`, DuckDB 동일): 결과 관련 파일(`.csv .json .jpg .jpeg .png .mp4 .webm .inc .rad .pdf .ppt .pptx .xlsx`)의 등록 버전. 의뢰 폴더(Final 보관 폴더·점 폴더 제외)를 자동 반영 때마다 드라이브 목록으로 비교한다.

| 드라이브 상태 | 처리 |
|---|---|
| 새 파일(행 없음) — 새 Scene 포함 | 버전 1로 **자동 등록**, 같은 확인에서 반영 |
| 같은 버전 | 그대로(상태가 바뀐 행만 `last_checked_at` 갱신) |
| 다른 `version_token` | sha1이 양쪽에 있으면 sha1로, 없으면 크기·시각이 다르고 등록 sha256이 있을 때 내려받아 sha256으로 비교. 같으면 행 정보만 갱신. 다르면 `review_state=PENDING`, `source_state=CHANGED`, 드라이브 값은 `pending_*` — **자동 반영하지 않는다** |
| 목록에 없음 | `source_state=MISSING`. DB 자료는 지우지 않는다 |

- 확인 전까지 화면과 새로고침은 **등록된 버전**을 본다: 공급자가 그 파일의 크기·시각·token을 등록값으로 보여 주고(지문 불변 → 자동 반영 없음), 내용이 필요하면 보관소 또는 DB(`dashboard_assets`/`result_registration_files`의 같은 sha256)에서 꺼낸다. 없으면 `DRIVE_SOURCE_CHANGED`. MISSING 파일·폴더도 등록값으로 계속 보인다. 그래서 변경 대기 중에도 새 파일·새 Scene은 자동 반영된다.
- **[새 버전 등록]**: 버전 `n+1` 행 추가, 이전 행은 `superseded_at`만 채워 보존. 다음 자동 반영이 그 파일만 내려받아 캡처한다. MISSING 행은 "삭제 반영"(행 종료, 다음 새로고침에서 스냅샷에서 빠짐 — 사용자가 확인한 경우에만).
- **[무시]**: `IGNORED` — 등록 버전을 계속 쓰고 같은 드라이브 버전은 다시 묻지 않는다. 다시 바뀌면 다시 묻는다.
- 확인 단위는 의뢰(전체) 또는 파일(`ids`). 권한은 의뢰 결과 등록 권한(`result.import`, 전역 관리자 포함). 결정하면 그 의뢰의 자동 반영 결과 캐시를 비워 다음 확인(1분 안, 또는 "지금 확인")에 반영된다.
- `version_token`은 sha1이 있으면 `sha1:<hex>:<size>`, 없으면 `t:<size>:<µs>`. 실제 드라이브 목록에 sha1이 있는지(C1·C9)는 아직 모르므로 둘 다 지원하고, 관리 상태 `version_tokens`와 자동 반영 결과 `drive.token_kind`·로그(`SCX drive listing version_token: …`)에 실제로 쓴 형태를 남긴다.

### 8.5 자동 반영(60초) 흐름

`POST /api/folder-discovery/environments/sync`(scx): 권한 확인(짧은 연결) → 합치기 간격 확인 → 의뢰별 드라이브 잠금(연결 없이) → 읽기 세션: ① 분류(§8.4, 드라이브 목록은 연결 없이, DB는 짧은 연결) ② 기존 빠른 확인·새로고침(연결 → 의뢰 범위 잠금, local 모드·등록 삭제와 같은 잠금 순서) → 결과에 `drive: {pending_changes, missing, ignored, token_kind, new_files}`. 드라이브 오류(`BUSY`·`TIMEOUT`·`AUTH_REQUIRED` 등)는 `FAILED`+코드로 끝나고 등록 버전은 바뀌지 않으며 다음 주기에 다시 확인한다.

### 8.6 화면 (데스크톱)

- Case 결과·소재 탭의 폴더 자동 확인 줄: `원본 변경 n · 확인 필요 · 원본 없음 m` 배지 → **드라이브 원본 변경** 대화상자(파일·상태·등록 버전·드라이브 값, 파일별/전체 [새 버전 등록]·[무시], 권한이 없으면 목록만).
- 드라이브 읽기 전용(`writes_available=false`): 결과 등록 화면은 안내 문구와 함께 새 폴더 만들기·파일/폴더 선택·끌어 놓기를 끈다. Final 지정·재시도·요약 갱신 버튼을 끄고 "드라이브 읽기 전용" 표시. 저장소 패널은 업로드 카드 대신 안내, "저장 폴더 새로고침"(폴더 생성 동반) 끔.
- 관리 › 드라이브 관리: "드라이브 쓰기"(읽기 전용 · 쓰기 허용 설정 켜짐/꺼짐), "원본 지문"(관찰한 token 형태와 파일 수).

### 8.7 D2에서 남긴 것

- 쓰기 경로 전부(D3): 결과 등록 업로드·폴더 준비, 승인 결과 복사, Final 지정·보고서·요약, 저장소 패널 연결·업로드는 409 `DRIVE_WRITE_DISABLED`.
- 읽기 세션으로 감싸지 않은 SPDM 읽기: 저장소 패널 파일 다운로드(스트리밍 응답), 결과 등록의 초안 생성·검사·위치 연결 등 쓰기와 섞인 API — scx에서 `DRIVE_READ_NOT_PREPARED` 또는 `DRIVE_WRITE_DISABLED`(D3에서 결과 등록과 함께 정리). 저장소 패널 연결 파일 목록의 sha1 체크섬(03 8-3)도 D3·D4.
- 32 MiB 초과 영상의 "드라이브에서 불러오는 중" 표시, 화면 폴더 선택기(D4). 같은 파일 `BUSY` 3회 연속 "사용 중" 배지 미구현(`FAILED`+`SPDM_FILE_BUSY`로 표시).
- 서버 임시·보관소 정리는 읽기 세션 단위와 한 시간 주기 고아 정리만 있다(업로드 대기 파일 정리는 D3).

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
| `backend/app/services/drive/writes.py` | D2 쓰기 가능 여부·`DRIVE_WRITE_DISABLED` 의존성 |
| `backend/app/services/storage/drive.py` | `DriveRoot`·`DriveStorageProvider`(읽기 전용 공급자) |
| `backend/app/services/storage/server_local.py` | 서버 임시 저장소·보관소 파일 조작(드라이브 패키지에는 삭제·교체 호출 없음) |
| `backend/migrations/versions/0036_drive_credentials.py`, `0037_drive_source_versions.py` | PostgreSQL 테이블(DuckDB는 `database.py`) |
| `backend/tests/drive_fakes.py` | **시험 전용** 가짜 어댑터(메모리 드라이브). `app/`에서 import하지 않으며 배포물에 쓰이지 않는다 |
| `frontend/src/features/storage/DriveAdminPanel.tsx`, `shared/components/DriveStatusBanner.tsx`, `shared/api/drive.ts`, `shared/hooks/useDriveWriteStatus.ts`, `features/results/DriveSourceChangesDialog.tsx` | 화면 |

정적 시험이 `app/services/drive`와 `routers/drive.py`에서 삭제·이동·덮어쓰기 계열 호출(`rm`, `mv`, `move`, `rename`, `replace`, `remove`, `delete`, `unlink`, `force=` 등)을 금지한다. D2 시험(`test_drive_reads.py`)은 읽기 모듈이 게이트웨이의 `list_dir`·`stat`·`download_to`만 부르는지 정적으로 확인한다.
