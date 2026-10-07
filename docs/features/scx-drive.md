# SCX 드라이브 연동 — 설치·설정·토큰·점검 (D0 기반)

- 기준일: 2026-10-07
- 상태: **D0(기반) 구현.** 드라이브를 실제로 읽고 쓰는 기능(D2 읽기 경로, D3 쓰기 경로)은 아직 없다. 계획은 [SCX 드라이브 연동 계획](../plans/scx-drive-integration.md).
- 근거: GitHub 이슈 #45의 어댑터 계약 v0.2(contract §2–4, §10)와 연동 문서 01·02·04·06
- 배포 영향: [ADR 0006](../adr/0006-scx-drive-adapter-external-wheel.md) — 어댑터 wheel은 대시보드 패키지에 넣지 않고 따로 설치한다.

## 1. 무엇이 바뀌나

| 모드 | 동작 |
|---|---|
| `SIMDASH_DRIVE_GATEWAY=none` (기본, 미설정 포함) | **지금과 같다.** 어댑터 패키지를 불러오지 않고, 관리자 드라이브 API는 409 `DRIVE_MODE_DISABLED`, 상태 API는 `mode: "none"`, 상단 배너 없음 |
| `SIMDASH_DRIVE_GATEWAY=scx` | 기동 시 설정·어댑터 설치·단일 프로세스를 확인하고 하나라도 어긋나면 **기동을 거부**한다. 관리자 화면에서 공용 계정 토큰 등록·연결 시험·드라이브 점검을 할 수 있다. SPDM 폴더 기능(탐색·동기화·등록·Final)은 D2·D3 전까지 **여전히 로컬 루트**를 쓴다 |

## 2. PC·서버 준비 (사용자가 직접 하는 일)

어댑터 저장소(`VD_scx_drive_adapter`)의 릴리스 zip(`VD_scx_drive_adapter-<버전>-win_amd64-cp311.zip`)을 받아 둔다. 이 저장소에는 어댑터 wheel·SDK wheel이 없다(결정 D6).

1. **워커 런타임 설치** (SDK가 도는 별도 가상환경, Python 3.11 x64)
   ```powershell
   # 릴리스 zip을 푼 폴더에서
   powershell -ExecutionPolicy Bypass -File .\install_runtime.ps1 -Python C:\Python311\python.exe -InstallRoot C:\SimDashboard\scx-runtime
   ```
   끝에 출력되는 `SIMDASH_SCX_WORKER_PYTHON=...\venv\Scripts\python.exe` 값을 적어 둔다.
2. **대시보드 가상환경에 어댑터 wheel만 설치** (의존성 없이; 소스 PC는 `.venv-runtime`, 폐쇄망 서버는 설치 루트의 런타임 venv python)
   ```powershell
   .\.venv-runtime\Scripts\python.exe -m pip install --no-deps <압축 푼 폴더>\wheelhouse\vd_scx_drive_adapter-<버전>-py3-none-any.whl
   ```
   `siemens_scx`는 대시보드 가상환경에 **설치하지 않는다**(pydantic·PyJWT 버전 충돌, contract §10.1).
   **주의:** 소스 설치·업데이트(`setup.ps1`, `update.bat`)는 `uv pip sync backend/requirements.lock`으로 가상환경을 lock과 정확히 맞추므로 lock에 없는 어댑터 wheel을 **지운다.** D5에서 설치기가 외부 wheel 경로를 받도록 바꾸기 전까지는 `update.bat` 뒤마다 이 단계를 다시 하고 대시보드를 재시작한다(scx 모드에서 wheel이 없으면 기동 거부 메시지가 이를 알려 준다). 폐쇄망 설치기도 같다.
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
| `SIMDASH_SCX_DRIVE_ROOT` | 점검 시 | — | SPDM 루트, 공용 계정 홈(`~`) 기준 상대 경로(예 `SPDM/Projects`). 설정하면 화면에서 바꿀 수 없음(D2의 폴더 선택기 예정). D0에서는 드라이브 점검에 필요 |
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
| 어댑터 패키지 없음 | `SCX 드라이브 어댑터 패키지가 설치되지 않았습니다 … python -m pip install --no-deps <…whl>` (영문 병기) |
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

- 테이블 `drive_credentials`(행 1개, `id='scx'`), TokenBundle JSON 전체를 `Fernet(SIMDASH_SECRET_ENC_KEY)`로 암호화. 평문 열은 `account_hint`, `obtained_at`, `updated_by`, `updated_at`, `key_id`(키 sha256 앞 8자)뿐.
- 키가 바뀌거나 복호화가 안 되면 `load()`는 없음으로 처리 → 상태 "재로그인 필요", 화면에 "암호화 키가 바뀌었습니다".
- SDK는 세션을 만들 때마다 refresh 토큰을 회전시킨다. 워커가 알리는 새 토큰은 `DbTokenStore.save()`가 **자기 DB 연결로 즉시 커밋**한다(`updated_by='worker'`). 저장 실패는 ERROR 로그와 관리 화면 경고로 표시한다.
- API 응답·로그·감사 기록에 토큰 값을 넣지 않는다. 감사: `DRIVE_CREDENTIALS_REGISTERED`(계정 표시명·발급 시각·서버 호스트·key_id), `DRIVE_CREDENTIALS_DELETED`, `DRIVE_CHECK_RUN`.
- 새 토큰을 등록하면 워커는 최대 약 10초 안에 받아 간다(`WorkerConfig.token_poll_interval_s`). 등록 직후 연결 시험이 "인증 필요"면 잠시 뒤 다시 시험한다.

## 5. 드라이브 점검 (결정 D7)

**드라이브 관리 → 드라이브 점검**에 SPDM 루트 기준 시험 폴더(예 `_simdash_test`)를 넣고 **점검 실행**. 한 번에 하나만 실행된다(409 `DRIVE_CHECK_RUNNING`).

| 순서 | 단계 | 확인 내용 |
|---|---|---|
| 1 | `stat` 시험 폴더 | 폴더 존재·종류. 없으면 여기서 멈춤 |
| 2 | `list_dir` | 항목 수, 지연, 목록 항목에 `sha1`·`modified_at`이 있는지(contract C1·C9) |
| 3 | `stat` 첫 작은 파일 | 단건 조회의 `sha1`·`modified_at` (목록과 비교) |
| 4 | `download_to` (8 MiB 이하 첫 파일) | 서버 임시 폴더로 받은 뒤 **서버 사본만** 삭제 |
| 5 | `upload_new` `simdash-check-<시각>.txt` | 새 파일 업로드 |
| 6 | 같은 이름 다시 `upload_new` | **CONFLICT여야 성공**(덮어쓰기 금지, contract C2) |
| 7 | `mkdirs` `simdash-check-<시각>/` | 폴더 생성, 같은 폴더 재생성은 성공(기존 반환) |
| 8 | `copy_within` 업로드 파일 → 새 폴더, 한 번 더 | 첫 복사 성공, 두 번째는 CONFLICT(contract C7) |
| 9 | `health` | 어댑터 상태(SDK 호출 없음) |

드라이브에서는 **아무것도 지우거나 옮기거나 덮어쓰지 않는다.** 점검이 만든 `simdash-check-*` 파일·폴더는 시험 폴더에 남으며, 필요하면 사람이 드라이브에서 직접 정리한다. 결과 표는 단계·결과·코드·지연(ms)·관찰을 보여 준다. 이 표가 이슈 #45 수용 시험의 C1·C2·C7·C9 기록 근거가 된다.

## 6. 상태와 화면

| 상태 | 관리 화면 배지 | 일반 화면 상단 배너 |
|---|---|---|
| `OK` | 연결됨 | 없음 |
| `DEGRADED` | 불안정 | 없음 |
| `UNAVAILABLE` | 연결 안 됨 | "드라이브 연결이 원활하지 않습니다…" |
| `AUTH_REQUIRED` (토큰 없음·키 변경·갱신 실패) | 재로그인 필요 | "드라이브 인증이 필요합니다. 관리자에게 문의하세요." |
| `UNKNOWN` (토큰은 있으나 워커가 아직 한 번도 호출되지 않음) | 확인 전 | 없음 |

- 배너는 `GET /api/drive/status`를 60초마다 조회한다(로그인 사용자, 상태 문자열만). none 모드면 첫 응답 후 조회를 멈춘다. 상태 조회는 드라이브를 호출하지 않는다(`health()`만).
- 오류 코드 한글 문구는 `frontend/src/shared/api/drive.ts`의 `DRIVE_ERROR_MESSAGES`(integration 06 §2).

## 7. API

| 메서드 | 경로 | 권한 | 내용 |
|---|---|---|---|
| GET | `/api/admin/drive/status` | 전역 관리자 | 모드·서버·루트·토큰 메타·health·워커 버전·토큰 저장 실패 시각 |
| PUT | `/api/admin/drive/credentials` | 전역 관리자, scx | TokenBundle JSON → `{account_hint, obtained_at, test}` |
| DELETE | `/api/admin/drive/credentials` | 전역 관리자, scx | 204 |
| POST | `/api/admin/drive/test` | 전역 관리자, scx | `stat("")` + `root_identity()` → `{ok, latency_ms, root_identity, error}` |
| POST | `/api/admin/drive/check` | 전역 관리자, scx | `{test_folder}` → 단계별 결과 표 |
| GET | `/api/drive/status` | 로그인 사용자 | `{mode, state}` |

비관리자는 403 `GLOBAL_ADMIN_REQUIRED`. none 모드의 쓰기·시험·점검은 409 `DRIVE_MODE_DISABLED`.

## 8. 코드 위치와 경계

| 위치 | 역할 |
|---|---|
| `backend/app/services/drive/config.py` | 환경변수 읽기·검증, 경로 규칙(contract §1) |
| `backend/app/services/drive/gateway.py` | 모드별 기동 검사, 어댑터 지연 import, `dashboard.lock`, `WorkerDriveGateway` 싱글턴, 계약 Protocol 사본, `DriveError` → `SpdmStorageError`/HTTP 대응표 |
| `backend/app/services/drive/token_store.py` | `DbTokenStore` |
| `backend/app/services/drive/check.py` | 드라이브 점검 |
| `backend/app/routers/drive.py` | API |
| `backend/migrations/versions/0036_drive_credentials.py` | PostgreSQL 테이블(DuckDB는 `database.py`) |
| `backend/tests/drive_fakes.py` | **시험 전용** 가짜 어댑터(메모리 드라이브). `app/`에서 import하지 않으며 배포물에 쓰이지 않는다 |
| `frontend/src/features/storage/DriveAdminPanel.tsx`, `shared/components/DriveStatusBanner.tsx`, `shared/api/drive.ts` | 화면 |

정적 시험이 `app/services/drive`와 `routers/drive.py`에서 삭제·이동·덮어쓰기 계열 호출(`rm`, `mv`, `move`, `rename`, `replace`, `remove`, `delete`, `unlink`, `force=` 등)을 금지한다.
