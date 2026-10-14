# 비밀번호 계정: 가입 안내·비밀번호 규칙·관리자 초기화

- 기준일: 2026-10-14 (사용자 결정)
- 대상: `AUTH_MODE=password` 배포. SSO(OIDC) 계정에는 비밀번호가 없어 해당하지 않는다.

## 1. 가입

- 아이디 칸은 **아이디 (AD ID)** 다. 회사 AD ID(윈도우 로그인 아이디)를 입력하도록 안내한다. 형식은 기존과 같다(영문 소문자로 저장, 영문·숫자·`.`·`-`·`_` 3~80자). AD ID인지 여부는 검사하지 않고 안내만 한다.
- 가입 후 전역 관리자가 승인해야 로그인할 수 있다(기존과 같음).

## 2. 새 비밀번호 규칙

| 규칙 | 내용 |
|---|---|
| 길이 | 8자 이상 256자 이하 |
| 문자 | 영문자·숫자·특수문자를 모두 포함 |
| 아이디 | 비밀번호에 아이디(3자 이상)를 포함할 수 없음 |
| 변경 시 | 새 비밀번호는 현재 비밀번호와 달라야 함 |

- 가입·비밀번호 변경·강제 변경 화면에 규칙을 먼저 보여 주고, 입력하는 동안 항목별로 맞음/아님을 표시한다.
- 서버도 같은 규칙으로 검사한다(`app/schemas/auth_limits.py`의 `password_policy_problem`, 422 `PASSWORD_POLICY`). 오류에 비밀번호를 담지 않는다.
- 규칙 이전에 만든 비밀번호로 로그인하는 것은 막지 않는다. 다음에 바꿀 때부터 적용된다.

## 3. 관리자 비밀번호 초기화 (비밀번호를 잊은 경우)

폐쇄망이라 메일로 재설정 링크를 보내지 않고, 관리자가 임시 비밀번호를 발급한다.

1. 전역 관리자: **사용자·프로젝트 권한 → 계정 목록 →** 해당 사용자의 **[비밀번호 초기화]**. 변경 사유 칸이 필요하다.
2. 화면에 **임시 비밀번호가 한 번만** 표시된다(복사 버튼). 닫으면 다시 볼 수 없다. 본인에게 직접 전달한다.
3. 사용자는 임시 비밀번호로 로그인한다. 그러면 **새 비밀번호 설정** 화면만 나오고, 새 비밀번호를 정하기 전에는 다른 화면·API를 쓸 수 없다(서버가 403 `PASSWORD_CHANGE_REQUIRED`로 막음. `auth/me`·로그아웃·비밀번호 변경만 허용).
4. 새 비밀번호를 저장하면 바로 대시보드로 들어간다.

- 기존 비밀번호는 초기화 즉시 쓸 수 없다. **초기화 전에 로그인한 세션(유출된 토큰 포함)은 즉시 로그아웃**된다(`users.sessions_revoked_at`보다 이른 토큰 거부. 토큰 발급 시각이 초 단위라 초기화와 같은 초의 토큰도 거부되며, 그 경우 다시 로그인하면 된다).
- 본인 계정(409 `PASSWORD_RESET_SELF` — 본인은 비밀번호 변경 사용)과 승인 대기 계정(409 `PASSWORD_RESET_PENDING`)은 초기화하지 않는다. `AUTH_MODE=password`가 아니면 사용할 수 없다.
- 새 비밀번호를 정하기 전에는 PC 실행 장치 세션도 403 `PASSWORD_CHANGE_REQUIRED`로 막는다. 차단은 감사 `PASSWORD_CHANGE_REQUIRED_BLOCKED`로 남긴다.
- 초기화 응답은 `Cache-Control: no-store`다.
- 임시 비밀번호는 서버에 해시로만 저장하고 로그·감사에 남기지 않는다. 감사: `PASSWORD_RESET_BY_ADMIN`(대상 사용자, 처리한 관리자, 사유).
- 다른 관리자가 그 사이 계정을 바꿨으면 409 `STALE_USER_VERSION`으로 멈춘다(새로고침 후 다시).
- DB: migration `0043_password_change_required`(`users.must_change_password` 기본 false, `users.sessions_revoked_at` nullable — 추가 전용. `update.bat`이 백업 후 적용).

## 4. API

| 메서드 | 경로 | 권한 | 내용 |
|---|---|---|---|
| POST | `/api/admin/users/{user_id}/password-reset` | 사용자 승인 권한(전역 관리자) | `{expected_updated_at, reason}` → `{temporary_password, must_change_password, updated_at}` |
| GET | `/api/auth/me` | 로그인 | `must_change_password` 포함 |
| GET | `/api/admin/users` | 사용자 승인 권한 | `has_password`, `must_change_password` 포함 |
