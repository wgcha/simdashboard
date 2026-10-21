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
   - 회사 PC에서 `uv`가 `Failed to update Windows PE resources … os error -2147024891`(접근 거부)로 실패하면(백신 등이 실행 파일 런처 생성을 막는 경우), 스크립트가 자동으로 wheel의 라이브러리 파일만 오프라인으로 풀어 설치한다(`scripts/windows/install_pure_wheels.py`, 순수 Python wheel만, 콘솔 명령 런처는 만들지 않음). 대시보드는 `import scx_drive_adapter`만 쓰므로 기능 차이는 없다.
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
| `SIMDASH_SCX_WORK_DIR` | — | `backend\data\scx-worker` | 워커 TEMP·adcopy 로그·`dashboard.lock`·작업자 stderr `worker.log`(§8.8). 서버 설치는 릴리스 폴더 밖 경로 권장 |
| `SIMDASH_SCX_STAGING_DIR` | — | `<WORK_DIR>\staging` | 다운로드·업로드 임시 저장소. 대시보드 서비스 계정만 접근 |
| `SIMDASH_SCX_MAX_CONCURRENCY` | — | `1` | 워커 동시 SDK 호출 수(1–8) |
| `SIMDASH_SCX_CACHE_TTL_SECONDS` | — | `30` | 어댑터 목록·조회 캐시(0–3600초) |
| `SIMDASH_DRIVE_SCAN_MAX_SECONDS` | — | `180` | 드라이브 폴더 조사 시간 상한(15–3600초). 의뢰 폴더 미리 목록 읽기·조사·내용 지문에 적용. local 모드는 15초 그대로. 범위 밖·숫자 아님은 경고 로그 후 기본값 |
| `SIMDASH_DRIVE_SCAN_MAX_ENTRIES` | — | `200000` | 드라이브 폴더 조사 전체 항목 수 상한(1000–2000000). local 50000 |
| `SIMDASH_DRIVE_SCAN_MAX_FOLDERS` | — | `20000` | 드라이브 폴더 조사 폴더 수·미리 목록 읽기 수 상한(100–200000). local 5000 |
| `SIMDASH_SCX_RELAY_ENABLED` | — | `false` | PhysicsAI용 SCX relay 켜기(§11, [relay 계약](scx-relay.md)). 꺼져 있으면 relay 경로는 모두 404 |
| `SIMDASH_SCX_RELAY_SECRET_FILE` | relay | — | 공유 비밀 파일 절대 경로(한 줄 base64url 43~128자). 형식·읽기 오류면 기동 거부(값은 메시지에 없음) |
| `SIMDASH_SCX_RELAY_DRIVE_BASE` | — | `""`(홈) | relay가 쓰고 읽는 기준 폴더(공용 계정 홈 기준 상대). SPDM 루트와 같거나 그 안이면 기동 거부 |
| `SIMDASH_SCX_RELAY_JOB_PATH_FORM` | — | `rel` | `submit_radioss` 경로 표기 `rel`·`tilde`·`absolute`(서버 확인 E4-11-3) |
| `SIMDASH_SCX_RELAY_HOME_ABS` | — | `/Users/vd.dashboard` | 공용 계정 홈 절대 표기(`absolute` 표기·`output_path` 변환에만) |
| `SIMDASH_SCX_RELAY_MAX_UPLOAD_BYTES` | — | `4294967296` | relay 파일 1개 업로드 상한(1 MiB~2^40) |
| `SIMDASH_SCX_RELAY_MAX_DOWNLOAD_BYTES` | — | `21474836480` | relay 파일 1개 다운로드 상한(1 MiB~2^40) |

어댑터 `AdapterConfig.drive_root`는 항상 `~`로 넘긴다. SPDM 루트는 대시보드가 그 아래 상대 경로로 관리한다(integration 03 §5).

### 기동 거부 조건 (scx 모드)

| 조건 | 메시지 요지 |
|---|---|
| 필수 설정 누락·형식 오류 | `SCX 드라이브 설정 오류: <변수> — …` |
| 어댑터 패키지 없음 | `SCX 드라이브 어댑터 패키지(scx_drive_adapter)가 설치되지 않았습니다 … wheel을 external-wheels 폴더에 복사한 뒤 update.bat(폐쇄망 서버는 오프라인 설치기)을 다시 실행` (영문 병기, §2의 2단계) |
| 다른 대시보드 프로세스가 `<WORK_DIR>\dashboard.lock`을 잡고 있음 | `다른 대시보드 프로세스가 SCX 워커를 사용 중입니다 … 단일 프로세스` — uvicorn `--workers 2` 이상도 여기서 거부된다 |

워커 프로세스는 **첫 드라이브 호출 때** 뜬다(지연 시작). 앱 종료 시 `close()`로 워커를 끝낸다.

## 4. 공용 계정 토큰

**연결·재연결의 기본 방법(사용자 결정 2026-10-16)**: 대시보드에 아이디·비밀번호나 코드를 넣지 않고, 어댑터 CLI로 받은 토큰 파일을 등록한다. HPC SSO는 drive-desktop 클라이언트의 비밀번호 로그인을 막고, CLI `login`은 SDK의 코드 승인(device grant)으로 정상 동작한다(이슈 #52).

1. 어댑터가 설치된 PC(서버 PC 등)에서 CLI로 로그인한다. `--device-code`는 브라우저를 열지 않고 활성화 코드와 주소만 출력한다. 그 주소를 **휴대폰 등 다른 기기**에서 열어 **공용 계정**으로 로그인하고 코드를 승인한다(개인 계정이 로그인된 브라우저에서 승인하면 개인 계정 토큰이 만들어진다). `--device-code` 없이 실행하면 그 PC의 브라우저가 열린다.
   ```powershell
   <워커 런타임>\python.exe -m scx_drive_adapter login --device-code --server https://<호스트>/ --out token.json [--client-name <클라이언트 ID>] [--ca-bundle <pem>]
   ```
2. **드라이브 관리 → 공용 계정 토큰**에서 **[파일에서 불러오기]**로 `token.json`을 고르거나(64 KB 이하) 출력된 JSON 전체를 붙여 넣고 **토큰 등록**. 연결 계정과 다른 계정이면 저장 전에 변경 확인을 묻는다(공용 계정이 아니면 취소). 등록 뒤 `token.json`은 지운다.
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

### 4.1 대시보드에서 다시 연결 (이슈 #50)

사양: [재연결 사양](../plans/scx-drive-relogin-spec.md). 어댑터에 `auth_capabilities`·`refresh_now`·`login_password`(워커 op 같은 이름)가 있어야 한다. 없는 어댑터는 화면에 "어댑터 업데이트 필요"를 보여 주고 토큰 붙여넣기만 쓴다.

- 진입: **드라이브 관리 → 다시 연결** 구역, 또는 `AUTH_REQUIRED` 배너의 **[다시 연결]**(전역 관리자에게만 보임, 일반 사용자는 지금처럼 "관리자에게 문의").
- **[저장된 토큰으로 다시 연결]**: 입력 없이 `refresh_now`. `AUTH_REFRESH_EXPIRED`면 "저장된 토큰이 만료되었습니다"와 함께 계정 로그인 양식을 연다.
- **계정으로 로그인**: HPC 아이디·비밀번호 → 어댑터가 HPC SSO에서 새 토큰을 받아 자동 등록(브라우저 불필요). 아이디 칸은 연결 계정으로 미리 채운다. 비밀번호 칸은 `type=password`·`autocomplete=off`이며 보내는 즉시 비운다.
- **연결 계정**(결정 4, migration `0042`): 처음 등록·로그인에 성공한 계정을 `drive_connected_account`에 기억한다. 다른 계정의 토큰(로그인·붙여넣기·갱신 결과)은 저장 전에 "연결 계정이 ○○에서 △△로 바뀝니다. 계속할까요?"를 묻고, 확인 없이는 409 `DRIVE_ACCOUNT_CHANGE_CONFIRM`으로 저장하지 않는다(워커 세션도 버려 다음 호출은 기존 계정 토큰으로 돌아간다).
- 성공하면 새 세대로 암호화 저장하고, 인증 대기로 멈춘 업로드 대기열을 재개하며 관리자 "대기열 일시 정지" 알림을 읽음 처리한다.
- **자동 갱신**(사양 §3.5): 드라이브 호출·대기열 항목이 `AUTH_REQUIRED`를 받으면 `AUTH_REQUIRED` 한 번당 `refresh_now`를 한 번만 시도하고 같은 호출을 한 번 더 한다. 실패하면 지금처럼 대기열이 멈추고 배너가 뜬다.

| 설정 | 기본 | 내용 |
|---|---|---|
| `SIMDASH_DRIVE_PASSWORD_LOGIN` | `https_only` | `off`/`https_only`/`on`. `https_only`는 HTTPS 접속(같은 서버 Caddy가 붙인 `X-Forwarded-Proto`를 uvicorn이 신뢰한 경우만) 또는 **서버 PC에서 직접 접속**(`127.0.0.1`)만 허용한다. 클라이언트가 보낸 `X-Forwarded-Proto`는 믿지 않는다. 사내망 HTTP(예 `http://서버:8080`)로 다른 PC에서 접속하면 거부되므로 서버 PC 브라우저에서 로그인하거나, 위험을 이해한 뒤 `on`으로 바꾼다(비밀번호가 사내망에 평문으로 흐름) |
| `SIMDASH_DRIVE_LOGIN_MAX_ATTEMPTS` | `3` | 15분당 계정 로그인 시도 한도(1–20, 대시보드 전체). 넘으면 15분 잠금(429 `DRIVE_LOGIN_LOCKED`). 잘못된 비밀번호 뒤 자동 재시도 없음. 어댑터가 SSO에 보내지 못한 시도(어댑터 미지원·드라이브 미가동)는 세지 않는다. 프로세스 메모리에 두므로 재시작하면 초기화된다(scx 모드는 단일 프로세스) |

같은 PC 판정: 소스 배포는 Vite 서버가 모든 요청을 127.0.0.1에서 API로 넘기므로, Vite 프록시가 브라우저가 보낸 `X-Forwarded-*`·`Forwarded`를 지우고 실제 접속지를 `X-Simdash-Proxy-Peer`로 덮어써 전달한다. API는 이 헤더를 127.0.0.1에서 온 요청에서만 믿는다(폐쇄망 설치의 Caddy는 이 헤더를 지운다). 다시 연결·로그인이 계정을 확인하는 동안(최대 45초) 드라이브 읽기는 기다리고 업로드 대기열은 다음 차례로 미룬다.

보안: 비밀번호는 요청 처리 함수의 지역 변수로만 있고 워커에 stdin JSON으로 한 번 전달된다. DB·파일·로그·감사·알림·오류 응답에 넣지 않는다(본문은 pydantic 검증 없이 직접 읽어 422 입력 반향이 없다). 감사: `DRIVE_RECONNECT_SUCCEEDED/FAILED`, `DRIVE_LOGIN_SUCCEEDED/FAILED`(입력 아이디·받은 계정·결과 코드), 붙여넣기 거부 `DRIVE_CREDENTIALS_REJECTED`.

| 메서드 | 경로 | 내용 |
|---|---|---|
| GET | `/api/admin/drive/auth` | 어댑터 지원 여부·capabilities·연결 계정·마지막 갱신/오류·계정 로그인 허용 여부(`password_login_blocked`: `OFF`/`HTTP`)·남은 시도·잠금 시각 |
| POST | `/api/admin/drive/reconnect` | `{confirm_account_change?}` → `{account_hint, obtained_at, account_changed, queue_resumed}` |
| POST | `/api/admin/drive/login` | `{username(≤128), password(≤256), confirm_account_change?}` → 위와 같음. 오류: 401 `AUTH_INVALID_CREDENTIALS`, 409 `AUTH_ACCOUNT_LOCKED`·`AUTH_MFA_REQUIRED`·`AUTH_METHOD_UNSUPPORTED`·`DRIVE_ACCOUNT_CHANGE_CONFIRM`·`DRIVE_ADAPTER_UPDATE_REQUIRED`, 429 `RATE_LIMITED`·`DRIVE_LOGIN_LOCKED`, 403 `DRIVE_PASSWORD_LOGIN_DISABLED` |
| PUT | `/api/admin/drive/credentials?confirm_account_change=true` | 다른 계정 토큰 붙여넣기 확인 |

### 4.2 코드로 로그인 (2026-10-14, 이슈 #52) — 기본 꺼짐

**2026-10-16부터 기본 꺼짐**(`SIMDASH_DRIVE_DEVICE_LOGIN=false`). 어댑터의 `device_login_*` 구현이 서버 SSO 주소를 찾지 못해 실패했고(OIDC discovery가 HTML 응답), 사용자 결정으로 §4의 CLI 토큰 등록을 기본 방법으로 쓴다. 어댑터가 사양서 §7대로 동작하게 되면 `.env`에 `SIMDASH_DRIVE_DEVICE_LOGIN=true`로 켠다(꺼져 있으면 버튼이 보이지 않고 시작 API는 409 `DRIVE_DEVICE_LOGIN_DISABLED`).

HPC SSO가 drive-desktop 클라이언트의 아이디·비밀번호 로그인을 막아(`AUTH_METHOD_UNSUPPORTED`) **활성화 코드(device grant)**로 공용 계정을 연결한다. 사양·어댑터 계약: [재연결 사양 §7](../plans/scx-drive-relogin-spec.md#7-코드-로그인device-grant-추가-2026-10-14-이슈-52). 어댑터가 `auth_capabilities.device=true`와 `device_login_start/poll/cancel`을 제공해야 보인다(없으면 기존 화면 그대로).

1. **드라이브 관리 → 다시 연결 → [코드로 로그인]** (배너 [다시 연결]로도 열림). 어댑터가 비밀번호를 지원하지 않으면 비밀번호 칸 대신 이 구역만 보인다.
2. 화면의 **[인증 페이지 열기]**를 아무 브라우저에서 열고(다른 PC·휴대폰 가능) **공용 계정**으로 HPC SSO에 로그인한 뒤 화면의 코드를 입력한다.
3. 화면이 `interval`초(보통 5초)마다 확인하고, 승인되면 "○○ 계정으로 연결됨"과 대기열 재개를 보여 준다. 남은 시간이 지나면 만료 — [코드 다시 받기].

- **공용 계정 고정**(사용자 결정 2026-10-14): 연결 계정과 다른(또는 이름 없는) 계정으로 승인되면 409 `DRIVE_DEVICE_ACCOUNT_MISMATCH`로 저장하지 않고 워커 세션을 버린다(변경 확인 없음). 이 PC 브라우저가 개인 계정으로 SSO에 로그인되어 있으면 이렇게 되므로, 그때는 휴대폰 등 다른 기기에서 주소를 연다. 연결 계정이 아직 없으면 처음 승인한 계정이 연결 계정이 된다. 공용 계정 자체를 바꾸려면 토큰 붙여넣기의 변경 확인을 쓴다.
- HTTP 접속에서도 쓸 수 있다(대시보드에 비밀번호가 지나가지 않음). 코드는 전역 관리자 화면에만 보이고 로그·감사에 남기지 않는다. 어댑터 내부 `login_id`는 브라우저로 보내지 않는다.
- 한 번에 1건(대시보드 전체). 진행 중에 다시 시작하면 같은 코드를 보여 주고, 다른 관리자도 같은 진행을 이어 본다. 대시보드 재시작·드라이브 중지 때 진행 중 코드는 버려진다.
- 감사: `DRIVE_DEVICE_LOGIN_STARTED`·`SUCCEEDED`·`FAILED`(결과 코드·받은 계정)·`CANCELLED`.

| 메서드 | 경로 | 내용 |
|---|---|---|
| POST | `/api/admin/drive/auth/device/start` | 코드 발급(진행 중이면 그것을 반환) → `{device_id, status, user_code, verification_uri(_complete), expires_at, interval, …}` |
| POST | `/api/admin/drive/auth/device/{device_id}/poll` | `interval`이 지났을 때만 어댑터에 1회 확인. `status`: `PENDING`/`SUCCEEDED`/`FAILED`/`EXPIRED`/`CANCELLED`, 실패 시 `code`·`message`(불일치면 `current`·`received`) |
| POST | `/api/admin/drive/auth/device/{device_id}/cancel` | 취소(어댑터에도 취소 전달) |

`GET /api/admin/drive/auth`에 `capabilities.device`, `device_login_allowed`, 진행 중·최근(10분) `device_login`이 추가된다.

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

- 알림(2026-10-08): 대기열 일시 정지(전역 관리자)·업로드 묶음 결과(시작한 사용자)·Final 완료/실패·요약 파일 갱신 필요·원본 변경 확인/원본 없음은 상단 알림 아이콘으로도 알린다 — 수신자·중복 규칙은 [알림 기능 안내](notifications.md)(migration `0040`).

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
- 자동 탐색·전체 새로고침(이슈 #51, 2026-10-17): 탐색 한 번은 읽기 세션 하나에서 돌고 회차 한도는 `DISCOVERY_READ_ROUNDS`(8 + 3 × `MAX_NEW_REQUESTS_PER_RUN`)다. 미연결 의뢰 폴더 목록은 모든 프로젝트를 모아 **한 회차**에(`require_listings`), 새 의뢰를 조사하기 전에는 그 프로젝트 폴더 트리(형제 의뢰 폴더 아래 제외)를 **한 회차**에 받는다(`require_subtree` → `prefetch_subtree`, 드라이브 조사 한도 적용). 이전에는 조사가 트리를 4단계씩 드러내며 회차를 다 써서 깊은 `FINAL/CAE/...`·`Working/...` 폴더가 `PATH_UNAVAILABLE`(`SCAN_INCOMPLETE`)이 되고 `_Lister.mtime`의 `DRIVE_READ_NOT_PREPARED`가 500이 되었다. 앞 회차에 등록한 의뢰는 다시 실행된 회차에도 결과에 남고(`carry`), 실행 한도·시간 예산은 실행 전체 기준이다. 마지막 회차에 아직 못 받은 의뢰는 `deferred`(다음 확인), 못 읽은 폴더는 `DRIVE_READ_LIMIT`이다. 폴더 하나의 오류는 `DISCOVERY_FAILED`/`DRIVE_READ_FAILED` 항목이 되고 응답은 항상 200이다.
- 의뢰 동기화·새로고침·재해석(이슈 #55, 2026-10-20): 의뢰 하나의 확인(`folder_auto_sync.sync_drive`/`sync_scope`, 수동 새로고침, 재해석, 저장 폴더 새로고침의 의뢰 확인)도 같은 읽기 세션 안에서 돌았고, 본문의 조사가 트리를 4단계씩 드러내서 Final 버전·Case·Drop이 많은 의뢰(`FINAL/CAE/<Case>/<id>/Drop/<run>/INDIVIDUAL/<scene>`)는 `MAX_ROUNDS=8` 회차를 다 쓰고 `FOLDER_SCHEMA_SCAN_INCOMPLETE`("현재 의뢰 폴더를 안전하게 모두 조사할 수 없습니다.")로 끝났다(Final 폴더 하나당 2회차). 이제 `refresh_scope`·`resolve_request_schema`·`reinterpret_request`는 조사 전에 의뢰 폴더 트리를 `require_subtree`로 **한 회차**에 받고(드라이브 조사 한도 적용, 이미 읽은 폴더는 다시 읽지 않음, 변경 없는 확인은 Final을 읽지 않는 빠른 확인으로 끝나 읽기 추가 없음), 재해석 경로 `/api/requests/{id}/reinterpret`는 드라이브 읽기 세션 안에서 돈다. 조사가 끝내 불완전하면 메시지가 `… 읽지 못한 곳: <경로> (list_dir, <오류 코드>) 외 N건`으로 어느 폴더에서 어떤 드라이브 작업이 실패했는지 보여 준다. Final 버전 ID 층은 CAE에서 선택 사항이다([depth-schema](../contracts/depth-schema.md) D12a): hex가 아닌 폴더는 이탈 경고 없이 버전 없는 Final로 읽는다.
- 이름 형식: 대시보드 경로는 NFC다. 드라이브 목록 이름은 목록 경계(`_live_list`/`_live_stat`)에서 NFC로 바꾸고, 드라이브가 준 철자(NFD 등)는 경로별로 기억해 그 경로의 `list_dir`·`stat`·`download_to`에 쓴다. 공백·이중 공백·괄호·한글 이름은 그대로 등록된다. 끝 공백·마침표·Windows 금지 문자 이름은 `FOLDER_NAME_INVALID`로 보고하고 등록하지 않는다.
- 어댑터 오류: 조사 중 드라이브 호출이 실패하면 `DRIVE_READ_FAILED`(`drive_operation`: `list_dir`/`stat`, `drive_error_code`, `issue_path`), 상위 목록 실패는 `DRIVE_LIST_FAILED`로 보고한다. 이 경우는 대시보드가 고칠 수 없는 드라이브·어댑터 문제다.
- 읽기 한도·바쁜 드라이브(이슈 #55, 2026-10-21): `drive.reads.bounded(deadline, cancel, observer)` 안의 모든 드라이브 읽기(중첩된 `run` 세션 포함)는 한도가 지나거나 중지되면 드라이브를 부르지 않고(`DRIVE_READ_DEADLINE`/`DRIVE_READ_CANCELLED`), 미리 읽기 반복을 멈추며, `run`은 다음 회차를 마지막 회차로 만든다. 자동 탐색 한 번은 자기 시간 한도 + `DRIVE_READ_SLACK_SECONDS`(30초), 저장 폴더 새로고침은 단계별 한도로 묶인다. 목록 미스 하나의 추측 목록(`_prefetch_tree`)은 60초(`LIST_PREFETCH_MAX_SECONDS`)에서 끝나고, 추측 목록이 재시도 뒤에도 `DRIVE_BUSY`면 그 회차의 추측 목록을 그만둔다. `DRIVE_BUSY`는 백그라운드 세션·한도 안에서 2·5·10초 간격 3회, 화면 요청은 1초 뒤 1회 다시 시도한다. 작업자 큐는 `SIMDASH_SCX_MAX_CONCURRENCY`(기본 1)개 호출만 동시에 처리하므로, 화면 동기화·자동 반영·새로고침이 겹치면 큐 대기 시간 초과가 난다.
- 이름(이슈 #55, 2026-10-21): 어댑터의 `local_safe=False`(Windows에 쓸 수 없는 이름·대소문자 쌍둥이)는 서버에 **내려받을 때만** 따진다. 내용 지문(`content_token`)은 이름과 무관하다. 대소문자 쌍둥이는 다운로드마다 새 임시 폴더라 그대로 받고, Windows 금지 이름은 받지 않으며(`SPDM_INVALID_PATH`, 메시지에 경로) Case 수집은 그 파일을 `NAME_NOT_LOCAL_SAFE` 미처리 항목으로 남긴다. `:`·`\` 이름은 폴더 조사에서 `skipped_entries`(`NAME_NOT_SUPPORTED`)로 빠진다.
- 세션 밖에서 연결을 쥔 채 드라이브를 읽으려 하면 드라이브를 부르지 않고 `DRIVE_READ_NOT_PREPARED`를 낸다(시험이 모든 가짜 드라이브 호출 시점에 연결이 없음을 확인).
- 감싼 API: 폴더 탐색(browse·scan·preview·apply), 환경 조사·미리보기·사용 검수·등록·캡처 재시도·새로고침, 자동 탐색(60초), 자동 반영(`/sync`, 60초), 의뢰 진척, Case 캡처·조사, 깊이 스키마 예시·점검, 소재 카탈로그·덱, 의미 매핑 폴더·연결·새로고침·검수, 결과 등록의 조회 API, Final 상태, 저장소 패널 조회·파일 다운로드. 그 밖의 SPDM 읽기는 scx 모드에서 `DRIVE_READ_NOT_PREPARED`로 실패한다(§8.7).

### 8.3 다운로드 → 등록 → 삭제, 서버 보관소 (05 §2–3)

- 세션마다 `<SIMDASH_SCX_STAGING_DIR>/read-<id>/`를 만들고 파일마다 하위 폴더에 받는다. 세션 하나의 임시 바이트 한도는 2 GiB이고 임시 디스크 여유가 1 GiB 아래로 내려가면 받지 않는다(`DRIVE_STAGING_FULL`; 미리 받기는 한도에서 멈춘다. 검수 L3). 받은 뒤 `stat` 기준 버전과 비교해 다르면 `SPDM_FILE_BUSY`(다음 확인에서 다시). 세션이 끝나면(성공·실패 무관) 폴더를 지운다. 세션을 다른 실행 문맥에서 닫게 되어도(프레임워크가 응답 본문을 복사된 문맥에서 실행) 오류 없이 폴더를 지운다. 하루 지난 `read-*` 폴더와 `download-*` 파일은 한 시간에 한 번 정리한다.
- 저장소 패널 파일 다운로드(`GET /api/storage/files/{id}/download`): scx 모드에서는 권한·파일 정보 조회의 DB 연결을 닫은 뒤, 요청 처리 중에 기록하지 않는 임시 읽기 세션(원본 변경 확인 대기 상태 반영, `drive_source_versions`에 행을 만들거나 바꾸지 않음)으로 드라이브 파일을 서버 임시 파일(`<SIMDASH_SCX_STAGING_DIR>/download-*`, DB 연결 없이)로 받은 뒤 그 파일을 응답으로 보내고 응답이 끝나면 지운다(클라이언트가 끊기거나 전송 중 오류가 나도 지움; 파일 이름·헤더는 로컬 모드와 같음). 사본을 만들기 전에 임시 디스크 여유가 1 GiB + 파일 크기 이상인지 확인하고, 모자라거나 디스크가 가득 차면(`ENOSPC` 등) 503 `DRIVE_STAGING_FULL`로 답하고 사본을 남기지 않는다. 로컬 모드는 그대로 스트리밍한다.
- 32 MiB 초과 파일은 지우지 않고 **서버 보관소**(`SIMDASH_DRIVE_BLOB_DIR/<sha256 앞 2자>/<sha256>`)로 옮긴다. 같은 버전을 다시 읽을 때는 보관소 파일의 sha256을 확인해 그대로 쓴다(프로세스마다 파일별로 처음 한 번 전체 해시를 확인하고, 그 뒤에는 크기·수정 시각(ns)이 확인 때와 같으면 다시 해시하지 않는다. 다르면 다시 해시하고 어긋나면 지운다). 용량 상한을 넘으면 오래 안 쓴 것부터 지운다 — 지우는 대상은 `<64자리 hex>` 이름이 `<앞 2자>` 폴더 안에 있는 파일뿐이고 다른 파일은 건드리지 않는다. 보관소 폴더가 작업·임시 폴더와 같거나 그 상위이거나 임시 폴더 안이면 기동을 거부한다(`SIMDASH_DRIVE_BLOB_DIR`, 검수 L2).
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

- 폴더 이름의 공백(2026-10-19): 등록 경로와 드라이브 목록은 `reads.name_key`(NFC·대소문자·공백 무시)로 비교한다. 분류는 먼저 등록 의뢰 폴더 경로를 드라이브 목록과 **세그먼트별로** 맞춰 실제 철자를 찾고(`sources.resolve_request_folder`: 세그먼트마다 후보가 정확히 하나일 때만; 공백만 다른 폴더가 둘이면 모호 → 등록 경로 그대로(옛 동작)이고 로그에 남김), 등록 행 경로가 실제 철자와 다르면 같은 파일로 보아 `rel_path`만 실제 철자로 바꾼다(행 id·버전 이력·내용 유지, 새 파일·`원본 없음` 아님; 두 철자가 모두 등록돼 있으면 목록에 있는 쪽이 이기고 다른 행은 대체됨). 의뢰·프로젝트 등록 자체는 자동 탐색이 실제 철자로 따라간다(depth-schema §8). 같은 부모 아래 공백만 다른 **프로젝트** 폴더가 함께 있으면 한 프로젝트로 묶어 모든 철자를 같은 프로젝트에 연결하고 의뢰를 모두 찾는다(이슈 #55). 새로고침의 새 깊이 스키마 적용은 의뢰마다 읽기 세션에서 실행한다(DB 연결 없이 의뢰 폴더 트리를 한 회차에 읽음, [폴더 스키마 새로고침](folder-schema-refresh.md)).
- 확인 전까지 화면과 새로고침은 **등록된 버전**을 본다. 아직 어느 동기화도 분류하지 않은 드라이브 변경(등록 행 `review_state=NONE`인데 드라이브 token이 다름)도 같다(검수 M2, 실패 시 닫힘): 읽기 세션은 목록에 나온 결과 파일의 현재 등록 행을 짧은 연결로 함께 읽고, 의뢰 범위로 분류된 행(`project_id`·`request_id` 있음)과 드라이브 값이 다르면 등록 버전으로 읽는다. 그래서 변경과 다음 동기화 사이의 수동 캡처(`POST /api/dashboard/captures`)·캡처 재시도·새로고침도 확인 안 된 내용을 읽지 않는다(등록 내용이 서버에 없으면 `DRIVE_SOURCE_CHANGED`로 실패, 다음 동기화가 `PENDING`으로 기록). Final 계획도 이런 파일을 거부한다. 의뢰 범위 밖 행(분류하지 않는 파일)은 예전처럼 드라이브 값으로 읽는다.
- 수동 새로고침(`/refresh`)은 먼저 권한을 확인하고 분류한다. 분류가 실패하면(드라이브 오류·범위 오류) 새로고침을 그 코드로 거부하고 `FOLDER_ENVIRONMENT_REFRESH_FAILED`를 감사 기록한다(예전에는 오류를 무시하고 분류 없이 새로고침했다).
- 화면과 새로고침의 등록 버전 표시: 공급자가 그 파일의 크기·시각·token을 등록값으로 보여 주고(지문 불변 → 자동 반영 없음), 내용이 필요하면 보관소 또는 DB(`dashboard_assets`/`result_registration_files`의 같은 sha256)에서 꺼낸다. 없으면 `DRIVE_SOURCE_CHANGED`. DB 내용을 세션 임시 파일로 꺼낼 때 경로에 `\`·`:`·`..`가 있으면 같은 오류로 거부한다(파일 이름이 DB 값에서 오므로). MISSING 파일·폴더도 등록값으로 계속 보인다. 그래서 변경 대기 중에도 새 파일·새 Scene은 자동 반영된다.
- **[새 버전 등록]**: 먼저 DB 연결 없이 대기 중인 드라이브 버전을 한 번 내려받아 그 버전인지 확인하고 sha256을 기록한다(05 §4, 검수 L4; 32 MiB 초과는 보관소에 남고, 읽기 한도 512 MiB 초과 파일은 받지 않음). 그사이 드라이브가 다시 바뀌었으면 409 `DRIVE_SOURCE_CHANGE_STALE`(다음 동기화가 새 값으로 다시 묻는다). 확인한 행만 다음 번호(`MAX+1`) 행으로 추가하고 이전 행은 `superseded_at`만 채워 보존. 다음 자동 반영이 그 파일을 캡처한다. MISSING 행은 "삭제 반영"(행 종료, 다음 새로고침에서 스냅샷에서 빠짐 — 사용자가 확인한 경우에만).
- **[무시]**: `IGNORED` — 등록 버전을 계속 쓰고 같은 드라이브 버전은 다시 묻지 않는다. 다시 바뀌면 다시 묻는다.
- 확인 단위는 의뢰(전체) 또는 파일(`ids`). 권한은 의뢰 결과 등록 권한(`result.import`, 전역 관리자 포함). 결정하면 그 의뢰의 자동 반영 결과 캐시를 비워 다음 확인(1분 안, 또는 "지금 확인")에 반영된다.
- `version_token`은 sha1이 있으면 `sha1:<hex>:<size>`, 없으면 `t:<size>:<µs>`. sha1은 소문자 40자리 hex만 쓰고 그 밖의 값은 없음으로 본다(PostgreSQL `VARCHAR(40)`와 DuckDB 동일, 검수 L5). 실제 드라이브 목록에 sha1이 있는지(C1·C9)는 아직 모르므로 둘 다 지원하고, 관리 상태 `version_tokens`와 자동 반영 결과 `drive.token_kind`·로그(`SCX drive listing version_token: …`)에 실제로 쓴 형태를 남긴다.

### 8.5 자동 반영(60초) 흐름

`POST /api/folder-discovery/environments/sync`(scx): 권한 확인(짧은 연결) → 합치기 간격 확인 → 의뢰별 드라이브 잠금(연결 없이) → 읽기 세션: ① 분류(§8.4, 드라이브 목록은 연결 없이, DB는 짧은 연결) ② 기존 빠른 확인·새로고침(연결 → 의뢰 범위 잠금, local 모드·등록 삭제와 같은 잠금 순서) → 결과에 `drive: {pending_changes, missing, ignored, token_kind, new_files}`. 드라이브 오류(`BUSY`·`TIMEOUT`·`AUTH_REQUIRED` 등)는 `FAILED`+코드로 끝나고 등록 버전은 바뀌지 않으며 다음 주기에 다시 확인한다.

이슈 #51(2026-10-12, 실제 드라이브 현장 보고)에서 고친 규칙:

- **내용 지문의 크기 한도:** 드라이브의 내용 지문은 `version_token`이라 파일을 읽지 않는다. 그래서 로컬 읽기용 한도(결과·미디어 32 MiB, 덱 64 MiB)를 적용하지 않는다. 전에는 32 MiB를 넘는 결과 영상(사용환경 동작 영상 등) 하나가 의뢰 전체의 동기화를 매번 `FAILED`(`FOLDER_SCHEMA_CONTENT_FILE_LIMIT`, HTTP 200)로 만들었다. local 모드는 그대로다(내용을 읽으므로 한도 유지).
- **등록 뒤 생긴 Case:** Case 없이(빈 `Working`) 등록된 의뢰에 사용자가 드라이브에서 직접 만든 Case 폴더는, 결과 파일(값·미디어)이 생기면 그 새로고침에서 바로 수집한다(전에는 Case·Scene만 보이고 "결과 없음"으로 남았다). 새 Case의 수집 실패는 스냅샷을 막지 않고 Case를 결과 없이 보여 준다(경고 로그, 다음 폴더 변경 때 다시 시도). 다른 의뢰에 연결된 Case 경로는 건너뛴다. local 모드는 등록된 Case만 다시 수집한다(기존 동작).
- **큰 자산:** 드라이브 모드의 수집은 32 MiB를 넘는 영상·이미지를 내려받지 않고 `UNPROCESSED_FILE:…:ASSET_TOO_LARGE` 품질 이슈로 남긴 채 나머지 결과를 수집한다(전에는 Case 수집 전체가 `DASHBOARD_ASSET_TOO_LARGE`로 실패). local 모드와 파일·값 검수 경로는 그대로 거부한다.
- 일부 단계만 있는 구조(빈 Case, 빈 평가 폴더, Case 바로 아래 파일)도 있는 Case·Scene을 그대로 보여 준다(회귀 시험 `tests/test_drive_usage_sync.py`).

이슈 #51 후속(2026-10-12, `FOLDER_SCHEMA_SCAN_INCOMPLETE` "최근 폴더 조사가 완료되지 않았습니다"가 60초마다 반복):

- **원인:** 읽기 세션 안의 폴더 조사는 연결을 쥔 채 드라이브를 기다리지 않으려고 빠진 목록을 회차마다(한 회차에 4단계·600개) 받아 다시 실행한다(최대 8회). 큰 의뢰 폴더의 수동 "다시 조사"가 회차를 다 써 `DRIVE_READ_NOT_PREPARED` → 조사 항목 `PATH_UNAVAILABLE` → `INCOMPLETE`로 저장됐고, 이 저장 조사가 의뢰에 연결된 최신 조사가 되어 활성 스냅샷이 아직 없는 의뢰의 모든 새로고침을 영구히 막았다. 첫 새로고침은 또 등록 조사 범위(프로젝트 전체, 형제 의뢰 포함)를 다시 조사해 같은 한도에 걸릴 수 있었다.
- **미리 목록 읽기:** `POST …/environments/scan`(수동 조사)은 조사 전에 대상 폴더 트리 전체를 연결 없이 목록으로 받아 둔다(`drive.reads.prefetch_subtree`, 위 `SIMDASH_DRIVE_SCAN_MAX_*` 상한). 상한에 걸려 남은 폴더는 기존 회차 방식으로 받는다. 60초 동기화는 분류 단계가 이미 의뢰 트리(Final 보관 내용 제외)를 연결 없이 모두 읽으므로 추가로 읽지 않는다(매분 Final 전체를 다시 읽는 부하를 피함).
- **저장된 INCOMPLETE 조사:** 등록·역할 근거는 COMPLETE 조사만 쓰므로 INCOMPLETE 저장 조사는 환경 규칙(프로필)만 제공하고 새로고침을 막지 않는다. 판단은 **현재 의뢰 범위 조사**로 하며, 그것이 COMPLETE이면 진행해 활성 스냅샷을 만든다(이후 새로고침은 스냅샷 기준). 저장 조사 행은 고치지 않는다. 현재 조사가 INCOMPLETE일 때만 실패하고 로그에 첫 조사 항목(`issues=PATH_UNAVAILABLE:<경로>, …`)을 남긴다.
- **의뢰 범위 재조사:** 저장 조사가 의뢰보다 위(프로젝트)에서 시작했으면 현재 조사는 의뢰 폴더만 한다(역할은 경로 깊이로 정해져 결과가 같다). local 모드도 같다(형제 의뢰의 접근 불가 폴더가 이 의뢰를 막던 문제).
- **부분 구조는 반영하지 않음(결정):** 한 하위 트리만 조사하지 못한 INCOMPLETE 조사로 나머지 Scene을 반영하는 것은 하지 않는다. 조사하지 못한 트리가 "없어진 것"으로 읽혀 등록 자료를 지우거나 덮을 위험이 있고, 드라이브에는 연결 폴더(`REPARSE`)가 없으며 깊이 64 한도(`DEPTH_LIMIT`)는 실제로 생기지 않아 이득이 작다. 대신 실패 로그에 원인 항목과 경로를 남긴다.

### 8.6 화면 (데스크톱)

- Case 결과·소재 탭의 폴더 자동 확인 줄: `원본 변경 n · 확인 필요 · 원본 없음 m` 배지 → **드라이브 원본 변경** 대화상자(파일·상태·등록 버전·드라이브 값, 파일별/전체 [새 버전 등록]·[무시], 권한이 없으면 목록만).
- 드라이브 읽기 전용(`writes_available=false`): 결과 등록 화면은 안내 문구와 함께 새 폴더 만들기·파일/폴더 선택·끌어 놓기를 끈다. Final 지정·재시도·요약 갱신 버튼을 끄고 "드라이브 읽기 전용" 표시. 저장소 패널은 업로드 카드 대신 안내. 상단 "저장 폴더 새로고침"은 2026-10-17부터 드라이브를 읽기만 하는 전체 다시 확인(`…/environments/rescan`, [폴더 스키마 새로고침](folder-schema-refresh.md))이라 드라이브 모드에서도 켜져 있다.
- 관리 › 드라이브 관리: "드라이브 쓰기"(읽기 전용 · 쓰기 허용 설정 켜짐/꺼짐), "원본 지문"(관찰한 token 형태와 파일 수).

### 8.7 D2에서 남긴 것

- 쓰기 경로(D3에서 구현, §10). 결과 등록 초안 게시·복사 재시도·폴더 준비와 저장소 패널 연결·업로드는 D3 이후에도 scx에서 409 `DRIVE_WRITE_DISABLED`.
- 읽기 세션으로 감싸지 않은 SPDM 읽기: 결과 등록의 초안 생성·검사·위치 연결 등 쓰기와 섞인 API — scx에서 `DRIVE_READ_NOT_PREPARED` 또는 `DRIVE_WRITE_DISABLED`(D3에서 결과 등록과 함께 정리). 저장소 패널 연결 파일 목록의 sha1 체크섬(03 8-3)도 D3·D4.
- 32 MiB 초과 영상의 "드라이브에서 불러오는 중" 표시, 화면 폴더 선택기(D4). 같은 파일 `BUSY` 3회 연속 "사용 중" 배지 미구현(`FAILED`+`SPDM_FILE_BUSY`로 표시).
- 서버 임시·보관소 정리는 읽기 세션 단위와 한 시간 주기 고아 정리만 있다(업로드 대기 파일 정리는 D3).
- 등록 직후 첫 동기화 전(행이 아직 의뢰 범위로 분류되기 전)의 드라이브 변경은 등록 버전으로 가리지 않는다(분류 없는 행은 확인 대상이 될 수 없어 영구 차단을 피함). 보통 1분 안의 자동 반영이 분류한다.

### 8.8 문제 확인: 로그 위치

드라이브 화면이 "폴더 확인 필요"·"폴더를 확인하지 못했습니다"만 보일 때(동기화 응답은 HTTP 200 + `status: FAILED`) 아래 순서로 본다. 로그에 토큰·비밀번호 값은 남지 않는다(`Bearer`·JWT 형태·`*token=`/`"password":` 값 가림).

| 무엇 | 어디 | 형태 |
|---|---|---|
| 동기화·새로고침 실패·충돌 | 서버 로그(`uvicorn-error.log`, 로거 `app.services.folder_auto_sync`) | `WARNING Folder sync FAILED: project=… request=… environment=USAGE code=… message=… cause=<예외 형식>: <정리된 메시지> <- <원인 예외>` — 같은 의뢰·환경·코드는 **10분에 한 번**(60초 자동 확인이 같은 줄을 반복하지 않음). 수동 새로고침은 `Folder refresh …` |
| 읽기 세션의 드라이브 호출 실패 | 같은 서버 로그(로거 `app.services.drive.reads`) | `WARNING SCX drive read <list_dir|stat|download_to> failed: rel=<루트 기준 경로> code=<DRIVE_*·SPDM_*> message=…`(같은 호출·경로·코드는 5분에 한 번, `SPDM_NOT_FOUND`는 INFO). `DRIVE_BUSY`(어댑터 큐 대기 시간 초과 `queue wait timeout` 등)는 재시도를 다 쓴 뒤에만 `… (after 3 retries)`로 남는다(이슈 #55, 2026-10-21) |
| 저장 폴더 새로고침 진행 | 같은 서버 로그(로거 `app.services.storage_rescan`) | `WARNING Storage rescan progress: status=RUNNING phase=DISCOVER round=0 elapsed=120s step=PROJECT projects=3/40 requests=0/0 current=<경로> drive_calls=… listings=… drive_errors=… busy_retries=… last_drive_error=DRIVE_BUSY:list_dir:<경로> …` 30초마다, 시작·끝·중지·실패는 `Storage rescan started/done/cancelled/failed` ([folder-schema-refresh](folder-schema-refresh.md) "이슈 #55 보완 2026-10-21") |
| 업로드 대기열의 드라이브 호출 실패 | 같은 서버 로그(로거 `app.services.drive.upload_queue`) | `WARNING SCX drive queue <mkdirs|upload_new|copy_within> failed: rel=… code=… message=…`, 관리 화면 업로드 대기열의 오류 열 |
| 어댑터 작업자(별도 Python 프로세스)의 stderr | `<SIMDASH_SCX_WORK_DIR>/worker.log`(기본 `backend\data\scx-worker\worker.log`, 5 MB × 3개 회전: `worker.log.1`~`.3`) | `<시각> pid=<작업자 PID> <줄>` — 어댑터 내부 예외·SDK 오류 메시지. 줄 단위로 토큰을 가린다 |

- `worker.log`는 어댑터가 작업자를 `subprocess.Popen`으로 시작하면서 stderr를 버리거나(`DEVNULL`) 물려받게(`None`) 둘 때 대시보드가 그 stderr를 파이프로 받아 기록한다(`drive/worker_log.py`; 어댑터 모듈의 `subprocess`만 바꾸며 다른 프로세스에는 영향 없음). 어댑터가 stderr를 직접 읽으면 손대지 않는다. 어댑터가 다른 방식으로 작업자를 띄우면 이 파일은 생기지 않는다 — 그때는 어댑터 쪽 로그를 본다.
- 응답 코드 읽기: `FOLDER_SCHEMA_*`는 폴더 구조·규칙 문제(메시지가 원인), `DRIVE_*`/`SPDM_*`는 드라이브 호출 문제(위 읽기 로그의 같은 시각 줄), `DRIVE_READ_NOT_PREPARED`는 읽기 세션 회차(8회)를 넘긴 경우다.
- 조사 미완료(`FOLDER_SCHEMA_SCAN_INCOMPLETE`)는 같은 줄 끝에 `issues=<코드>:<경로>, …`(처음 3개와 나머지 수)를 붙인다. 수동 조사가 미완료로 저장되면 `WARNING Folder scan INCOMPLETE: path=… issues=…`(로거 `app.services.folder_discovery_environment`). `SCAN_LIMIT`·`ENTRY_LIMIT`는 위 `SIMDASH_DRIVE_SCAN_MAX_*`, `PATH_UNAVAILABLE`은 같은 시각의 드라이브 읽기 로그를 본다.
- **로그 인코딩:** 서버 로그(`uvicorn.log`·`uvicorn-error.log`, 오프라인 설치 서비스 로그)는 UTF-8이다(`start.ps1`과 오프라인 설치의 API 서비스가 `PYTHONUTF8=1`·`PYTHONIOENCODING=utf-8`로 백엔드를 띄움). 한국어 메시지는 `Get-Content -Encoding UTF8 backend\uvicorn-error.log -Tail 200`(실시간은 `-Wait` 추가)으로 읽는다. 인코딩 없이 `Get-Content`(Windows PowerShell 5.1 기본 cp949)로 읽거나 이 변경 전에 쓴 줄은 `�ֱ� ���� …`처럼 깨져 보인다.
- 관리 › 드라이브 관리 › **드라이브 점검**은 조회 전용(stat·list·작은 파일 download)이라 통과해도 동기화 실패 원인(구조·크기 한도·수집)은 위 로그로 확인한다.

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
- 대상 기능: 결과 등록(끌어놓기) 올리기·새 폴더 만들기·폴더 구조 만들기(§10.3), Final 지정·보고서 업로드·확정·재시도·요약 갱신(§10.4–10.5).
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

- **올린 뒤 크기 불일치:** `upload_new`·`copy_within`이 돌려준 크기가 보낸(원본) 크기와 다르면 크기만으로 실패 처리하지 않고 대상을 `stat`한다. 그 `stat`이 없거나 여전히 다른 크기면 드라이브 반영 지연으로 보고 `CONFLICT`가 아니라 재시도(`RETRY`, `DRIVE_UPLOAD_MISMATCH`)로 기록한다. `stat`이 맞는 크기를 보이거나 재시도 때(`DRIVE_UPLOAD_MISMATCH` 다음 시도)는 크기가 아니라 아래 같은 내용 판정(sha1, 없으면 내려받아 sha256)으로 `DONE` 또는 `CONFLICT`를 정한다. 재시도 횟수를 다 쓰면 `FAILED`(`DRIVE_UPLOAD_MISMATCH`).
- **덮어쓰기·삭제·이동 없음.** `CONFLICT`(같은 이름 존재)이면 대상을 `stat`해 같은 내용이면 `DONE`, 아니면 `CONFLICT`로 멈춘다(화면·관리자에게 표시). 같은 내용 판정: 양쪽 sha1이 있으면 sha1. 한쪽이라도 없으면 **크기만으로는 판정하지 않고** 대상 파일을 서버 `xfer-*` 임시 폴더로 내려받아(DB 연결 없이, 항목 크기까지만) sha256을 서버 임시 파일·계획 sha256과 비교한다(COPY에 계획 sha256이 없으면 원본도 같은 방식으로 내려받아 비교). `SIMDASH_DRIVE_VERIFY_MAX_BYTES`(기본 2 GiB)보다 크면 내려받지 않고 `CONFLICT`(`SPDM_CONFLICT_UNVERIFIED`) — 관리자가 드라이브에서 확인한 뒤 다시 시도·취소한다. 서버 임시 공간이 1 GiB 여유를 남기지 못하면 기다린다(`DRIVE_STAGING_FULL`, 재시도 간격).
- **재시도 전 확인:** 두 번째 시도부터는 먼저 대상을 `stat`한다(응답을 잃은 업로드가 이미 올라갔을 수 있음 — 같으면 `DONE`, 중복 업로드 없음).
- **재시도 가능 오류** `BUSY`·`LOCKED`·`TIMEOUT`·`OVERLOADED`·`UNAVAILABLE`: 30초 → 2분 → 10분 → 30분 → 1시간 간격, `SIMDASH_DRIVE_UPLOAD_MAX_ATTEMPTS`(기본 8)회를 넘으면 `FAILED`. 대기 중인 항목 뒤의 같은 묶음 항목은 기다린다(순서 유지).
- **`AUTH_REQUIRED`**: 항목을 `BLOCKED`로 두고 **대기열 전체를 멈춘다**(`queue_paused`). 관리자가 토큰을 다시 등록하면 자동 재개(또는 관리 화면 "대기열 재개").
- **묶음 정지:** `halt_on_error` 항목(Final 전부, 결과 등록의 `MKDIR`)이 `FAILED`·`CONFLICT`·`CANCELLED`로 끝나면 그 묶음의 뒤 항목은 실행하지 않는다 — Final은 `complete.json`이 없어 미완료로 판정된다. 이미 드라이브에 쓴 것은 지우지 않는다. 결과 등록의 `FILE`은 서로 독립이라 하나가 충돌해도 나머지는 올린다(묶음 상태 `PARTIAL`).
- **재시작:** 앱 기동 시 `RUNNING`으로 남은 항목은 `PENDING`으로 돌아가고 재시도 전 확인 규칙으로 이어서 실행된다.
- **정상 종료:** 앱 종료 때 작업자가 드라이브 호출 중이면 그 항목이 끝나기를 최대 60초 기다린 뒤 드라이브 게이트웨이를 닫는다. 종료 중에 실패·재시도로 끝난 호출은 기록하지 않고 행을 `RUNNING`으로 남긴다(종료 자체가 원인일 수 있음). 다음 기동이 `PENDING`으로 되돌린다.
- **결과 기록 실패·정리(sweep):** 드라이브 호출 뒤 결과를 DB에 기록하지 못하면 짧은 연결로 몇 번 다시 기록한다. 그래도 실패하면 행은 `RUNNING`으로 남고, 작업자가 1분마다 돌리는 정리가 이 프로세스에서 실행 중이 아닌 `RUNNING` 항목을 `PENDING`으로 되돌린다(다음 시도는 재시도 전 확인 규칙으로 이미 올라간 파일을 알아본다). 같은 정리가 묶음이 이미 끝났는데 `PUBLISHING`으로 남은 Final의 마무리(완료·실패 판정, 잠금 해제)를 다시 실행한다(마무리 훅 실패, 관리자 취소 뒤 깨우기 유실).
- **DB 연결:** 작업자는 짧은 연결로 항목을 잡고(`RUNNING`), **연결 없이** 드라이브를 부른 뒤, 짧은 연결로 결과를 기록한다(§8.2와 같은 이유; 시험이 모든 가짜 드라이브 호출 시점에 연결이 없음을 확인).
- **임시 파일:** 항목이 `DONE`이 되기 전에는 지우지 않는다. Final 묶음은 보고서·`plan.json` 임시 파일을 **묶음 전체가 `DONE`일 때까지** 남긴다(같은 Final ID 재시도). 묶음이 모두 `DONE`이면 `upload-<묶음>` 폴더를 지운다. 기동 시와 그 뒤 1시간마다 하루 지난 `upload-*`·`xfer-*`를 지우되(이 프로세스에서 아직 내려받는 중인 항목의 `xfer-*`는 제외), 아직 진행할 수 있는 묶음(묶음 요약 `QUEUED`·`RUNNING`·`PAUSED`)이나 `PUBLISHING` Final이 쓰는 것은 남기고, 관리자 판단을 기다리는 묶음(`CONFLICT`·`FAILED` 항목, 또는 `halt_on_error` 항목이 멈춰 뒤 항목이 `PENDING`으로 남은 정지 묶음)과 미완료 Final(`PLANNED`·`STAGED`·`FAILED`)의 것은 마지막 활동 후 **7일**까지 남긴다(서버 임시 저장소만, 드라이브는 건드리지 않음). 7일 뒤 재시도하면 보고서·파일을 다시 올려야 한다(Final 재시도는 지워진 `plan.json` 임시 파일을 DB `plan_json`에서 다시 만들고, 대기열에 기록된 sha256과 다르면 422 `FINALIZATION_PLAN_STAGE_INVALID`로 멈춘다).
- **단일 작업자:** 프로세스당 스레드 1개(`dashboard.lock`으로 프로세스도 하나). 묶음은 만든 순서대로, 묶음 안은 `seq` 순서.
- 묶음 상태: `QUEUED`·`RUNNING`·`PAUSED`(인증 대기)·`DONE`·`PARTIAL`·`CONFLICT`·`FAILED`·`CANCELLED`. 항목 상태: `PENDING`·`RUNNING`·`DONE`·`CONFLICT`·`FAILED`·`BLOCKED`·`CANCELLED`.
- **드라이브 경로:** 대기열 항목의 드라이브 경로는 읽기 경로와 같은 함수로 만든다(`reads.drive_path(SIMDASH_SCX_DRIVE_ROOT, reads.check_rel(rel))` — NFC 정규화, 계약 §1 경로 규칙). 루트는 설정에서 앞뒤 `/`를 떼고 `~`로 시작하면 기동을 거부한다. 그래서 `mkdirs`가 만든 폴더는 다음 동기화가 목록에서 보는 그 폴더다(이슈 #51에서 확인).
- **실패 로그:** 드라이브 호출이 실패하면 `WARNING SCX drive queue <mkdirs|upload_new|copy_within> failed: rel=<루트 기준 경로> code=<코드> message=<정리된 메시지>`를 남긴다.
- 관리자 취소는 항목을 `CANCELLED`로 표시만 한다(드라이브 내용 불변). 묶음 마무리(Final 실패 판정·잠금 해제)는 취소가 커밋된 뒤에 실행한다. 다시 시도는 같은 항목을 처음부터(충돌은 드라이브 재확인). Final 묶음 항목의 다시 시도는 의뢰 잠금을 먼저 잡는다(다른 Final이 반영 중이면 409 `FINALIZATION_LOCKED`).
- **항목 잡기:** 조건부 `UPDATE … WHERE state='PENDING' RETURNING`으로 자기 잡기를 확인한다.
- **목록 편집(2026-10-12, 전체 관리자):** `POST /api/admin/drive/queue/bulk-cancel {ids | all_filter: {state}}`는 고른 항목(또는 그 목록 조건의 취소 가능한 항목 전부, 최대 5000)을 단건 취소와 똑같이 `CANCELLED`로 표시하고, 커밋 뒤 묶음마다 마무리 훅(Final 실패 판정·잠금 해제)을 실행한다. 취소할 수 없는 상태는 `skipped`로 돌려준다. `POST /api/admin/drive/queue/purge {ids | states}`는 끝난 항목(`DONE`·`CANCELLED`·`FAILED`·`CONFLICT`)의 **대기열 행과 서버 임시 파일만** 지운다(드라이브는 건드리지 않음). 같은 묶음에 진행 중인 항목(`PENDING`·`RUNNING`·`BLOCKED`)이 있으면 그 묶음은 `DRIVE_QUEUE_BATCH_OPEN`으로, Final 지정 묶음은 Final이 `COMPLETE`가 아니면(`PUBLISHING`은 잠금 보유, 멈춘 Final은 이 행으로 다시 시도) `DRIVE_QUEUE_FINAL_ACTIVE`로 건너뛴다. 묶음의 행이 모두 지워지면 `upload-<묶음>` 폴더를, 일부만이면 그 항목의 임시 파일을 지운다. 감사 기록 `DRIVE_QUEUE_BULK_CANCELLED`·`DRIVE_QUEUE_PURGED`(개수, 항목·묶음 id 최대 200개).

### 10.3 결과 등록(끌어놓기) — integration 05 §5.3, W8

- 계획(`plan`)·시작은 D2 읽기 세션에서 드라이브 목록으로 검사한다(Working 범위·깊이·이름·기존 파일 충돌은 local과 같은 규칙, 같은 이름이 있으면 거부 — 이름 바꾸기·덮어쓰기 없음). 남은 공간은 **서버 임시 저장소**의 여유 공간으로 본다(드라이브는 여유 공간을 알려 주지 않음).
- 조각은 서버 `upload-<세션 ID>/<n>.part`에 받는다(드라이브에 임시 폴더를 만들지 않음 — 드라이브에서는 이름 바꾸기·삭제를 할 수 없으므로).
- **올리기 완료(`complete`)**: 의뢰·대상 폴더·충돌을 다시 확인한 뒤 묶음 하나(묶음 ID = 세션 ID)를 대기열에 넣고 즉시 `state: QUEUED`로 응답한다. `MKDIR`(새 폴더, 얕은 것부터) → 파일마다 `FILE`. 감사 `RESULT_DROP_UPLOAD_QUEUED`(실제 사용자, 묶음 ID).
- 화면은 묶음 진행을 2초마다(인증 대기로 멈췄거나 예약된 재시도 시각 전이면 15초 이내로 그 시각까지) 조회하고 묶음이 끝나면 멈추며, "드라이브 반영 대기/중/완료/충돌/일시 정지"를 보여 준다. 묶음이 끝나면 다음 자동 반영(60초, D2 읽기 경로)이 새 파일·새 Scene을 Case 결과에 넣는다(묶음 완료 시 의뢰 자동 반영 캐시를 비움).
- **새 폴더 만들기**: 검사 후 `MKDIR` 묶음을 넣고 최대 20초 기다린다(연결 없이). 끝나지 않으면 "드라이브에 만드는 중" 안내.
- **폴더 구조 만들기**([결과 등록 §2a](result-registration.md#2a-폴더-구조-만들기-2026-10-08-사용자)): 검사 후 묶음 하나(출처 `result_structure`)에 새 의뢰 폴더·`Working`·Case를 얕은 순서의 `MKDIR`로 넣고(의뢰·Working은 `halt_on_error`, 이미 있는 폴더는 넣지 않음 — 다시 실행하면 묶음 없음) 최대 20초 기다린다. 묶음이 끝나면 의뢰 자동 반영 캐시를 비우고 폴더 자동 탐색을 한 번 강제로 돌려(백그라운드 스레드) 예약된 의뢰 폴더를 선택한 의뢰의 LINK로 등록한다.
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
- 관리 › 드라이브 관리 › **업로드 대기열**: 상태별 개수·필터, 항목(기능·작업·대상·시도·오류·전송 방식), 다시 시도·취소(확인 창: 드라이브 내용은 지우지 않음)·대기열 재개, 쓰기 설정 꺼짐·작업자 미실행·일시 정지 경고. 행마다 선택 칸과 머리글의 **전체 선택(현재 목록 조건)**, **선택 취소**·**목록에서 지우기**·**완료·취소 항목 모두 지우기**(모두 확인 창: 드라이브에 이미 올라간 파일·폴더는 지우지 않음, 결과에 처리·건너뜀 개수). 전체 선택 상태의 선택 취소는 화면에 보이는 행이 아니라 그 목록 조건 전체에 적용된다.
- 상단 상태 API의 `queue_paused`로 일시 정지 여부를 알 수 있다.

### 10.8 D3에서 남긴 것

- 실제 드라이브 확인(C1·C2·C7·C9, 대용량 Final의 `copy_within` 시간·대체 경로 대역폭) — §10.6 절차로 D6.
- 결과 등록 초안(이전 흐름)의 드라이브 게시, 저장소 패널 연결·업로드(계속 409). (저장소 패널 다운로드는 읽기 세션 + 서버 임시 파일로 처리, 2026-10-11.)
- 큰 Final의 진척은 파일 단위(묶음 항목)로만 보인다(`copy_within` 한 건 안의 바이트 진척 없음).
- `complete.json`의 비수집 파일 `sha256`은 `null`(드라이브 sha1로 대신). SPDM이 sha256을 요구하면 협의 필요.
- 순번 파일 방식은 SPDM 협의 전 임시안(결정 D2 ①).
- **사용자 결정 대기(D3 검수 #11):** 프로젝트 삭제(정리)는 대기열 행·Final 메타를 남기며(`project_cleanup.KEEP_COLUMNS`), **열린 대기열 항목(`PENDING`·`BLOCKED`)을 멈추지 않는다** — 삭제 뒤에도 작업자가 그 프로젝트의 남은 파일·Final을 드라이브에 올리고, Final 마무리 훅은 지워진 의뢰 기준으로 실행된다. 삭제 전에 남은 항목을 취소할지, 삭제가 항목을 `CANCELLED`로 표시할지, 그대로 둘지는 결정 후 반영한다(현재 동작 유지, 관리자는 업로드 대기열에서 해당 항목을 직접 취소할 수 있다).
- 결과 등록 완료(`complete`)는 라우터 커밋 뒤에 세션을 등록 목록에서 뺀다. 커밋하지 못했으면(묶음 행 없음) 같은 세션으로 다시 완료할 수 있다.

## 11. SCX relay (PhysicsAI, 기본 꺼짐)

PhysicsAI가 대시보드의 SCX 연결(공용 계정 토큰·단일 워커)을 같이 쓰도록 대시보드가 여는 loopback 전용 내부 API다. 계약 전문(정본: `physicsai-platform/docs/contracts/scx_relay.md`)의 사본은 [scx-relay.md](scx-relay.md).

- 경로 `/internal/scx-relay/v1/*`(`/api` 밖, OpenAPI에 없음, Vite·Caddy·nginx 프록시 표에 없음). 검사 순서: 꺼짐 404 `RELAY_DISABLED` → loopback(`127.0.0.1`·`::1`) 아님·프록시 헤더 있음 403 `RELAY_FORBIDDEN` → `X-SCX-Relay-Key` ≠ 비밀 파일(`hmac.compare_digest`) 401 `RELAY_UNAUTHORIZED` → scx 모드 아님 503 `DRIVE_MODE_DISABLED`.
- 코드: `backend/app/services/drive/relay.py`(접근 제어·경로 범위·제출 기록·오류 매핑), `backend/app/routers/scx_relay.py`. 쓰기는 업로드 대기열의 새 root 종류(출처 `physicsai_relay`, `root_key = sha256("relay:<서버 호스트>:/<RELAY_DRIVE_BASE>")`, `dst_rel_dir`는 relay 기준 상대)로만 하고, 대기열이 relay 항목마다 SPDM 루트를 다시 거부한다. relay 모듈은 쓰기·삭제·이동 메서드를 부르지 않는다(정적 시험).
- DB: migration `0044_scx_relay_submissions`(추가 전용, DuckDB는 `database.py` 같은 DDL). 제출은 `SUBMITTING` 기록 → 어댑터 1회 → 결과 기록. 같은 `request_id`는 어댑터를 다시 부르지 않고 기록을 돌려준다. 기동 때 남은 `SUBMITTING`은 `UNKNOWN`.
- 켜는 법(관리자): ① 같은 비밀 값을 대시보드와 PhysicsAI가 읽을 파일 하나로 만든다 — 예 PowerShell `$b = New-Object byte[] 48; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b).TrimEnd('=').Replace('+','-').Replace('/','_') | Set-Content -NoNewline -Encoding ascii <설치 루트>\state\secrets\scx_relay.key`, ACL은 대시보드·PhysicsAI 서비스 계정과 SYSTEM만. 실제 값은 어떤 문서·저장소·채팅에도 두지 않는다. ② `.env`에 `SIMDASH_SCX_RELAY_ENABLED=true`, `SIMDASH_SCX_RELAY_SECRET_FILE=<그 파일 절대 경로>`(필요하면 `SIMDASH_SCX_RELAY_DRIVE_BASE` 등). relay 업로드에는 `SIMDASH_DRIVE_WRITES_ENABLED=true`와 `SIMDASH_SCX_DRIVE_ROOT`도 필요(없으면 409 `DRIVE_WRITE_DISABLED`, 읽기·잡 조회는 동작). ③ 대시보드 재시작(비밀 교체도 두 앱 재시작).
- 구현 메모(계약과 다르거나 구체화한 곳): (a) none 모드에서 relay를 켜면 기동은 거부하지 않고 경고 로그만 남기며 relay 응답은 모두 503 `DRIVE_MODE_DISABLED`(계약 사본 §2에 반영; 비밀 파일·기준 폴더 오류는 기동 거부). relay를 끈 뒤 남은 relay 대기열 항목은 `FAILED`(`RELAY_DISABLED`). 경로 구간 앞뒤 공백·끝 `.`·보이지 않는 문자(zero-width 등)는 400 `INVALID_PATH`. (b) 잡 API 확인은 어댑터 모듈의 `RadiossJobRequest`·`JobResources`와 게이트웨이의 `submit_radioss`·`get_job`·`terminate_job` 존재로 판단. (c) 어댑터 `NOT_FOUND`는 `get_job`·`terminate_job`·resolve attach에서 404 `SCX_JOB_NOT_FOUND`, 그 밖 경로 작업에서 404 `NOT_FOUND`. (d) `/files` 업로드의 `X-Request-Id`·`X-Content-SHA256` 형식 오류와 본문 JSON 오류는 422 `INVALID_REQUEST`. (e) 관리 › 업로드 대기열의 출처 표시 이름("PhysicsAI")은 화면 작업(후속 프런트)에 남김 — 지금은 `physicsai_relay`로 보인다.
