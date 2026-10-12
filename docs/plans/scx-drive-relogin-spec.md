# SCX 드라이브 재연결 사양: 저장 토큰 재연결 · 계정 로그인 (대시보드 + 어댑터)

- 상태: 사양 초안(2026-10-12). 구현 전. 브랜치 `claude/scx-drive`.
- 배경: HPC 드라이브는 사내 SSO가 아닌 **HPC 내부의 독립 SSO**를 쓴다. 지금은 관리자 PC 브라우저에서 `scx_drive_adapter login`으로 토큰을 받아 붙여 넣는데, 브라우저에 다른 계정 로그인이 남아 있으면 **다른 계정 토큰이 발급**되고, 사내 정책상 시크릿 창·브라우저 프로필을 쓸 수 없다.
- 목표: 연결 실패(`AUTH_REQUIRED` 등) 때 대시보드 화면에서 바로 복구한다.
  1. **저장 토큰으로 다시 연결**: 저장된 refresh 토큰으로 강제 갱신해 다시 붙는다(입력 없음).
  2. **계정으로 로그인**: 아이디·비밀번호를 입력하면 어댑터가 HPC SSO에서 토큰을 받아 자동 등록한다(브라우저 불필요 → 계정 섞임 없음).
- 관련: [SCX 드라이브 기능](../features/scx-drive.md) §4·§6·§7, 어댑터 계약(이슈 #45) §4 토큰·§10 워커 프로토콜.

## 1. 전체 흐름

```
관리자 화면 ── POST /api/admin/drive/reconnect ──▶ 대시보드 ── refresh_now ──▶ 워커 ──▶ HPC SSO
           └─ POST /api/admin/drive/login {id, pw} ─▶ 대시보드 ── login_password ─▶ 워커 ──▶ HPC SSO
                                                      ◀── tokens 알림(새 TokenBundle) ──┘
                                     계정 확인 → 암호화 저장(새 세대) → 대기열 재개 → 알림 해제
```

- 비밀번호는 **브라우저 → 대시보드 → 워커(stdin)** 로만 지나가고, 어디에도 저장·기록하지 않는다.
- 토큰 저장·세대 번호·게이트웨이 초기화는 지금의 `PUT /credentials`와 같은 경로를 탄다(§4 토큰 저장 규칙 유지).

## 2. 어댑터 사양 (scx_drive_adapter 쪽 추가)

### 2.1 `DriveGateway` 메서드 추가

| 메서드 | 입력 | 출력 | 동작 |
|---|---|---|---|
| `auth_capabilities()` | — | `{"refresh": bool, "password": bool, "mfa": bool}` | 이 서버·클라이언트가 지원하는 방식. SDK 호출 없이 설정·사전 확인 결과로 답한다. 대시보드는 `password=false`면 로그인 양식을 숨긴다 |
| `refresh_now()` | — | `TokenBundle` | 현재 refresh 토큰으로 **즉시** 갱신. 성공하면 `tokens` 알림과 같은 값 반환 |
| `login_password(username, password, *, timeout_s=30)` | 아이디, 비밀번호(str) | `TokenBundle` | HPC SSO에서 **비밀번호 방식으로 새 토큰** 발급. 성공 시 워커 세션을 새 토큰으로 교체 |

- `TokenBundle.account_hint`에 **토큰의 실제 계정 식별자**(SSO 토큰의 사용자 이름 클레임, 예: `preferred_username`)를 반드시 채운다. 대시보드가 이 값으로 공용 계정인지 확인한다.
- `refresh_now`·`login_password`는 `INTERACTIVE` 우선순위로, 진행 중인 다른 호출과 겹치지 않게 워커의 토큰 잠금 안에서 실행한다.

### 2.2 워커 프로토콜(§10.2) op 추가

| op | args | result |
|---|---|---|
| `auth_capabilities` | `{}` | `{refresh, password, mfa}` |
| `refresh_now` | `{}` | `{"account_hint", "obtained_at", "expires_at"}` + `tokens` 알림 |
| `login_password` | `{"username", "password"}` | `{"account_hint", "obtained_at", "expires_at"}` + `tokens` 알림 |

- 비밀번호는 **stdin JSON으로만** 받는다. 명령줄 인자·환경변수·임시 파일·`adcopy.exe` 인자에 넣지 않는다.
- 워커는 처리 직후 비밀번호 변수를 지우고(`del`, 참조 해제), **로그·오류 메시지·예외 문자열에 아이디 외 값을 넣지 않는다**. 요청 로그가 있다면 `login_password`의 `args`는 `{"username": "<값>", "password": "***"}`로만 남긴다.
- 응답 `result`에 토큰 원문을 넣지 않는다(토큰은 기존처럼 `tokens` 알림으로만 전달).

### 2.3 인증 방식 (HPC SSO 확인 필요)

| 우선 | 방식 | 조건 |
|---|---|---|
| 1 | SDK가 제공하는 아이디·비밀번호 로그인 함수(있다면) | HPC_SDK에 해당 API가 있는지 확인 |
| 2 | OIDC 토큰 엔드포인트 `grant_type=password`(Resource Owner Password) | HPC SSO 관리자가 이 클라이언트(`client_name`)에 허용해야 함 |
| 3 | SSO 로그인 HTML 양식 자동 제출 | 1·2가 불가할 때만. 화면 변경에 취약하므로 비권장 |

- 어떤 방식이든 **TLS 검증**(`ca_bundle`)을 끄지 않는다.
- MFA(OTP 등)가 필요하면 `login_password`는 `AUTH_MFA_REQUIRED`로 끝낸다(1차 범위에서 OTP 입력은 지원하지 않음, §6 결정 2).

### 2.4 오류 코드 추가 (contract §6 `ErrorCode`)

| 코드 | 의미 | 재시도 |
|---|---|---|
| `AUTH_INVALID_CREDENTIALS` | 아이디·비밀번호 불일치 | **하지 않음**(계정 잠금 방지) |
| `AUTH_ACCOUNT_LOCKED` | 계정 잠김·비활성 | 하지 않음 |
| `AUTH_MFA_REQUIRED` | 추가 인증 필요 | 하지 않음 |
| `AUTH_METHOD_UNSUPPORTED` | 이 SSO·클라이언트가 비밀번호 방식을 허용하지 않음 | 하지 않음 |
| `AUTH_REFRESH_EXPIRED` | refresh 토큰 만료·폐기(저장 토큰 재연결 불가) | 하지 않음 → 계정 로그인 필요 |
| `RATE_LIMITED` | SSO가 시도 횟수 제한 | 대시보드가 안내 후 대기 |

- 어댑터는 `login_password`를 **자체적으로 재시도하지 않는다**(한 번 요청 = SSO에 한 번 시도).

### 2.5 CLI (선택)

- `login --username <id>`: 비밀번호는 `getpass`로 입력받아(화면 비표시) 같은 경로로 토큰 발급 → 지금처럼 TokenBundle JSON 출력. 브라우저 없이 관리자 PC·서버에서 쓸 수 있다.
- 기존 브라우저 `login`에는 `--prompt-login`(OIDC `prompt=login`) 옵션을 추가해 남아 있는 브라우저 로그인 대신 계정 입력 화면을 띄운다.

### 2.6 어댑터 시험 (가짜 SDK)

- 성공 시 `tokens` 알림·`account_hint` 채움, 비밀번호가 로그·예외·`repr`에 나타나지 않음(캡처 검사), 잘못된 비밀번호 1회 = SSO 호출 1회, MFA·잠금·미지원 코드 매핑, `refresh_now` 회전 토큰 저장 알림, 진행 중 호출과의 잠금 순서.

## 3. 대시보드 사양

### 3.1 설정

| 변수 | 기본 | 내용 |
|---|---|---|
| `SIMDASH_SCX_EXPECTED_ACCOUNT` | (없음) | 공용 계정 식별자. 설정되어 있으면 **다른 계정 토큰은 저장하지 않고 거부**(로그인·붙여넣기·갱신 모두) |
| `SIMDASH_DRIVE_PASSWORD_LOGIN` | `off` | `off` / `https_only` / `on`. `https_only`면 요청이 HTTPS(또는 같은 PC `127.0.0.1`)일 때만 허용. 사내망이 HTTP라면 비밀번호가 평문으로 지나가므로 운영 기본은 `https_only` 권장 |
| `SIMDASH_DRIVE_LOGIN_MAX_ATTEMPTS` | `3` | 15분당 시도 한도(대시보드 전체). 넘으면 15분 잠금 |

### 3.2 API (전역 관리자, scx 모드)

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| GET | `/api/admin/drive/auth` | — | `{capabilities, account_hint, expected_account, obtained_at, expires_at?, last_refresh_at, last_error_code, password_login: "off"|"https_only"|"on", attempts_left, locked_until?}` |
| POST | `/api/admin/drive/reconnect` | `{}` | `{ok, account_hint, obtained_at}` 또는 오류 코드(§2.4). 성공 시 대기열 자동 재개 |
| POST | `/api/admin/drive/login` | `{"username": str(≤128), "password": str(≤256)}` | 위와 같음. **본문은 저장·로그·감사에 남기지 않음** |

- 요청 처리: 관리자 확인 → 설정·HTTPS·시도 한도 확인 → **DB 연결 없이** 워커 호출(`login_password`/`refresh_now`) → `tokens` 알림으로 받은 번들을 계정 확인(`expected_account`) → 기존 `write()` 경로로 암호화 저장(새 `generation`) → 게이트웨이 초기화 없이 워커 세션 유지(이미 새 토큰) → `queue/resume` → `DRIVE_QUEUE_PAUSED` 알림 해제.
- 계정이 다르면: 저장하지 않고 워커에 기존 토큰을 다시 `set_tokens`로 돌려준 뒤 409 `DRIVE_ACCOUNT_MISMATCH`(`{received: "<계정>", expected: "<계정>"}`).
- 오류 매핑: `AUTH_INVALID_CREDENTIALS` 401, `AUTH_ACCOUNT_LOCKED`·`AUTH_MFA_REQUIRED`·`AUTH_METHOD_UNSUPPORTED`·`DRIVE_ACCOUNT_MISMATCH` 409, `AUTH_REFRESH_EXPIRED` 409, `RATE_LIMITED`·한도 초과 429, 비활성 설정·HTTP 거부 403 `DRIVE_PASSWORD_LOGIN_DISABLED`.
- 기존 `PUT /credentials`(토큰 붙여넣기)도 `expected_account` 확인을 같이 적용한다.

### 3.3 보안 규칙

1. 비밀번호는 요청 처리 함수 안의 지역 변수로만 존재하고, 워커 전송 직후 참조를 버린다. DB·파일·캐시·감사·알림·오류 응답에 넣지 않는다.
2. 요청 본문 로깅·예외 추적에서 `/api/admin/drive/login` 본문을 제외한다(미들웨어·예외 처리기에서 마스킹 시험 포함).
3. 실패 응답은 원인 구분 코드만 주고 SSO 원문 메시지를 그대로 노출하지 않는다.
4. 시도 한도(§3.1)로 HPC 계정 잠금을 막는다. 잘못된 비밀번호 후 자동 재시도 없음.
5. 감사: `DRIVE_RECONNECT_SUCCEEDED/FAILED`, `DRIVE_LOGIN_SUCCEEDED/FAILED`(관리자, 시각, 입력 아이디, 받은 계정, 결과 코드). 비밀번호·토큰 없음.
6. 화면: 비밀번호 칸은 `type=password`, `autocomplete="off"`, 전송 후 즉시 비움. 브라우저 저장 금지.

### 3.4 화면 (데스크톱)

- 상단 배너 "드라이브 인증이 필요합니다"(관리자에게는) → **[다시 연결]** 버튼 → 대화상자.
- 대화상자:
  - 현재 연결 계정, 기대 계정, 마지막 갱신 시각, 마지막 오류.
  - **[저장된 토큰으로 다시 연결]**: 입력 없음. `AUTH_REFRESH_EXPIRED`면 "저장된 토큰이 만료되었습니다. 계정으로 로그인하세요."로 아래 양식을 연다.
  - **계정으로 로그인**: 아이디(기대 계정으로 미리 채움), 비밀번호, [로그인]. `password=false` 또는 설정 `off`면 숨기고 "토큰 붙여넣기"만 보인다. HTTP 접속이라 거부되면 이유를 표시한다.
  - 결과: 성공 "○○ 계정으로 연결됨 · 대기열을 재개했습니다", 계정 불일치 "공용 계정이 아닙니다(받은 계정: ○○)".
- 일반 사용자 배너는 지금처럼 "관리자에게 문의하세요".

### 3.5 자동 갱신 보강

- 워커가 `AUTH_REQUIRED`를 내기 전에 **한 번 `refresh_now`** 를 시도한다(이미 401 후 1회 갱신은 계약에 있음). 실패 코드가 `AUTH_REFRESH_EXPIRED`이면 그때 관리자 알림.
- 관리자 상태에 refresh 토큰 만료 예정 시각을 알 수 있으면(`expires_at`) 3일 전 알림.

### 3.6 대시보드 시험

- 가짜 게이트웨이에 `auth_capabilities`·`refresh_now`·`login_password` 구현(성공/각 오류/다른 계정).
- 비밀번호 비노출: 응답·로그(caplog)·감사·DB 전체 덤프에서 비밀번호 문자열 검색 0건.
- 계정 불일치 시 저장 안 됨·기존 토큰 유지, 시도 한도·잠금, HTTP 거부(`https_only`), 대기열 재개·알림 해제, DB 연결 없이 드라이브 호출(기존 훅).
- 화면 e2e: 다시 연결 성공, 만료 → 로그인 양식 전환, 계정 불일치 메시지, 비밀번호 칸 비움.

## 4. 단계

| 단계 | 내용 | 담당 |
|---|---|---|
| R0 | HPC SSO 방식 확인(§2.3), MFA·잠금 정책·대시보드 HTTPS 여부 확인 | 사용자·어댑터 팀 |
| R1 | 대시보드: `expected_account` 거부, 상태 API, 화면 틀(붙여넣기·다시 연결 버튼) — 어댑터 변경 없이 가능한 부분 | 대시보드 |
| R2 | 어댑터: `auth_capabilities`·`refresh_now`·`login_password`·오류 코드·CLI | 어댑터 팀 |
| R3 | 대시보드: reconnect·login API, 보안 규칙, 화면 완성, 시험 | 대시보드 |
| R4 | 회사 PC에서 실제 SSO로 확인(성공·오입력·다른 계정·만료) | 사용자 |

## 5. 위험

- HTTP로 서비스하면 비밀번호가 사내망에 평문으로 흐른다 → `https_only` 기본, HTTPS 구성 필요(배포 정책 영향, ADR 필요).
- 비밀번호 방식이 SSO에서 금지되어 있으면 계정 로그인은 불가 → 브라우저 `--prompt-login`과 계정 확인(R1)만으로 운영.
- 반복 실패로 공용 계정이 잠기면 드라이브 전체가 멈춘다 → 시도 한도·자동 재시도 금지.

## 6. 결정 필요

1. HPC SSO가 비밀번호 방식을 허용하는가(SDK 함수 또는 OIDC password grant)? 어떤 방식인가?
2. MFA(OTP)가 있는가? 있다면 OTP 입력 칸을 2차로 넣을지.
3. 대시보드를 HTTPS로 서비스하는가? 아니면 `on`(HTTP 허용)을 감수할지.
4. 공용 계정 식별자(`SIMDASH_SCX_EXPECTED_ACCOUNT`) 값.
5. 계정 로그인을 전역 관리자만 할지, 지정한 담당자에게도 허용할지.
