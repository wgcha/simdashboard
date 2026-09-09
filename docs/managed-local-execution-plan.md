# 계정별 PC 연결과 중앙 실행 이력

2026-09-09 사용자 승인: 회사 계정 로그인 → 최초 한 번 내 PC 연결 승인 → 이후 자동 연결 → 프로젝트 권한에 따른 중앙 이력 공유. 실제 프로그램과 파일은 해당 사용자 PC에서 실행한다. 클라우드 실행은 계획 범위로 유지한다.

## 운영과 신뢰 경계

- 기존 로그인(password/OIDC)을 그대로 사용한다. 실제 회사 IdP 설정은 운영 환경에서 제공한다.
- 도우미를 신뢰할 중앙 서버 URL과 웹 Origin으로 설치/실행한다. 서버는 HTTPS, 개발용 HTTP는 localhost/loopback만 허용한다. 브라우저가 임의의 서버 URL을 지정할 수 없다.
- PC 식별자는 영속 UUID다. 최초 연결은 로그인 세션으로 2분짜리 일회성 연결 요청을 만들고, 도우미의 네이티브 확인창에서 계정과 서버를 확인한다. 일반 화면에는 연결 코드를 노출하지 않는다.
- 도우미가 생성한 256비트 비밀값으로 중앙의 계정-PC 연결을 인증한다. 서버에는 해시만 저장한다. 연결별 비밀값은 브라우저에 반환하지 않는다.
- 브라우저는 로그인 토큰을 localhost로 전달하지 않는다. 계정-PC에 한정된 10분 세션만 전달한다. 도우미는 모든 보호 요청에서 고정 중앙 서버에 세션/연결 상태를 확인한다. 실행·재실행은 담당자, 프로젝트 권한, 현재 작업 순서 및 진행 상태도 서버가 확인한다. 서버 장애 시 신규 실행은 막고 진행 중인 프로그램은 유지한다.
- 로컬 카탈로그·배치·이력은 연결별 SQLite로 분리한다. 기존 공통 코드 DB는 관리 모드에 자동 반입하지 않는다. 동일 PC의 다른 웹 계정은 다시 최초 승인을 받는다. 같은 Windows 계정의 악성 프로세스에 대한 OS 보안 경계까지 제공하지는 않는다.
- 도우미가 실행 상태를 디스크에 저장하고 백그라운드에서 중앙에 재전송한다. 브라우저 종료·일시적 통신 장애 후에도 재개한다. 중앙은 실행 승인과 연결된 레코드만 받고 순서번호로 중복/역순을 방지한다. 이력은 승인된 PC가 보고한 기록이며 프로그램 내부의 모든 클릭을 자동 추적하는 기능은 아니다.
- 내 연결 해제는 서버에서 즉시 신규 접근/실행을 차단한다. 과거 중앙 이력은 보존한다. 프로젝트 조회 권한이 있는 사용자는 중앙 이력을 읽을 수 있지만 다른 사람 PC의 실행/완료/재실행 버튼은 사용할 수 없다.
- 조회는 기존 `PROJECT_DATA_VIEW` 정책을 재사용한다. 현재 제품에서는 이 권한이 활성 회사 계정의 기본 권한이므로 프로젝트 멤버에게만 제한된 조회로 간주하지 않는다. 실행은 별도로 프로젝트 실행 권한과 담당자를 검사한다.

## 모듈 간 API 계약 (v1)

중앙 `/api/local-execution` (회사 로그인 필요):

- `GET /devices` → `Device[]` (내 연결). Device = `{id,device_id,host_name,user_id,created_at,revoked_at}`.
- `POST /pairing` `{device_id}` → `{pairing_token,expires_at}`. 만료 120초, 활성 계정만.
- `POST /devices/{id}/session` `{}` → `{token,expires_at,binding_id,user_id}`. 토큰 형태 `binding_id.random`, 만료 600초. 자기 활성 연결만.
- `POST /devices/{id}/revoke` `{}` → `{ok:true}`. 자기 연결만.
- `GET /runs?request_id=...&work_item_id=...` → `CentralRun[]`. LocalRun 필드 + `{binding_id,device_id,host_name,actor_user_id,synced_at}`. 작업-의뢰 관계와 프로젝트 조회 권한 검증.

중앙 장치 전용 경로는 일반 사용자 인증 미들웨어 예외로 **정확한 경로만** 허용하며 각 경로가 별도 인증한다:

- `POST /device/pair-preview` Bearer pairing_token, `{device_id}` → `{user_id,display_name}`. 소비하지 않음.
- `POST /device/pair` Bearer pairing_token, `{device_id,host_name,device_secret}` → Device. 토큰 원자적 일회 소비. 같은 계정·장치 재연결은 새 연결 ID 발급 및 이전 연결 폐기.
- `POST /device/authorize` Bearer device_secret, `{binding_id,session_token,action,context?}` → `{user_id,display_name,context?,grant_id?}`. action = `catalog|history|execute|retry|complete`. execute/retry의 context는 `{request_id,work_item_id,task_name,actor}`이며 서버가 actor/task_name을 확정한다. history는 로컬 계정 이력 접근, complete는 원래 실행 context에 대한 프로젝트 권한 및 담당자 검사(작업 종료 후도 완료 메모 허용). execute/retry는 현재 진행 순서까지 검사하며 새 grant_id를 발급한다.
- `POST /device/events` Bearer device_secret, `{binding_id,events:[{sequence,grant_id,run:LocalRun}]}` → `{accepted:[{run_id,sequence}]}`. 최대 100개, grant와 연결/actor/context 일치 검증. 기기 보고 데이터 크기 제한. 완료한 기존 승인 실행은 사용자 권한 변경 후에도 보고 가능하지만 폐기한 연결의 보고는 거절. 같은 실행ID의 불변 실행 설정을 덮어쓸 수 없음.

로컬 `/v1`:

- `GET /identity` 무인증 최소 응답 `{device_id,host_name,managed}`. Origin/Host 검증 유지.
- `POST /pair` `{pairing_token}` → `{binding_id,user_id}`. 고정 서버 preview → 네이티브 승인 → redeem → 연결 비밀 저장. 승인창 생략은 주입한 테스트 콜백으로만 가능.
- 기존 보호 API는 Bearer 중앙 session token 사용. binding prefix로 로컬 연결을 고른 뒤 중앙 authorize 응답으로 사용자 확정. 실행 context.actor/task_name을 덮어쓰고 grant_id를 내부 저장. retry/complete는 원본 DB context를 검증한다.
- `GET /health` 기존 필드 + `{binding_id,user_id,sync_pending?,sync_error?}`.
- 기존 standalone API는 명시적 개발 옵션으로만 유지한다. 기본 실행 스크립트는 관리 모드이며 서버 URL 설정을 요구한다.

## 화면

2026-09-09 후속 요청에 따라 기본 메뉴 **내 PC 설정**(`/workspace/settings/local-pc`)에서 작업 선택 없이 연결·프로그램 카탈로그를 관리한다. 관리자 업무 메뉴 정책과 무관하게 활성 계정에 제공하며 프로젝트가 없어도 접근한다. 최초 시작 파일은 웹 주소와 자동 시작 선택만 담아 다운로드하고, 사용자가 기존 설치 폴더를 선택하여 한 번 실행한다. 상세 기준은 [내 PC 설정 계획](personal-pc-settings-plan.md)을 따른다.

연결 상태(확인 중/설치 필요/이 PC 연결/연결됨/연결 해제)와 내 PC 이름, 계정을 표시한다. 로그인 후 identity와 내 장치 목록으로 자동 복원하고, 최초에만 '이 PC 연결'을 누른다. 짧은 세션은 메모리에만 보관하고 만료 전에 갱신한다. 로그아웃·계정 변경 시 즉시 제거한다. 중앙 이력은 도우미가 꺼져도 조회할 수 있으며 내 PC/다른 PC와 실행자를 구분한다. 상태 동기화 지연은 표시한다.

## 검증

다른 계정의 연결/세션/로컬 데이터 접근 차단, 미승인/만료/재사용 연결 요청 차단, 연결 해제 및 계정 중지 즉시 반영, 프로젝트/담당자/현재 작업 검사, 위조 actor 거절 또는 서버 확정, 중앙 장애 시 실행 차단, 재시작 후 이벤트 재전송과 역순/중복 차단, 중앙 조회 권한, 자동 연결/로그아웃/오프라인 UI, 기존 직접/배치/재실행 회귀를 확인한다. PostgreSQL Alembic 및 DuckDB 개발 스키마를 함께 갱신한다.

## 구현과 운영 적용

| 책임 | 위치 |
|---|---|
| 중앙 API·입력·권한·연결/승인/이력 | `backend/app/routers/managed_local_execution.py`, `schemas/managed_local_execution.py`, `services/managed_local_execution.py` |
| 운영/개발 DB | `backend/migrations/versions/0022_managed_local_execution.py`, `backend/app/adapters/persistence/duckdb/managed_local_execution.py` |
| PC 연결 설정·로컬 outbox | `local_runner/managed.py`, `local_runner/server.py`, `local_runner/storage.py` |
| 계정/PC 연결 생명주기 | `frontend/src/shared/local-execution/useManagedLocalConnection.ts` |
| 실행 UI와 중앙/로컬 이력 합치기 | `frontend/src/features/workbench/local-programs/LocalProgramPanel.tsx` |
| 중앙 및 loopback API 경계 | `frontend/src/shared/api/localExecution.ts`, `localRunner.ts` |

1. 중앙 서버는 기존 password 또는 OIDC 인증을 활성화한다. `AUTH_MODE=disabled` 개발 모드는 관리 연결을 허용하지 않는다. 회사 OIDC 공급자의 실제 주소·클라이언트 정보는 기존 인증 설정에 제공한다. PostgreSQL 운영 배포에서는 Alembic `0022_managed_local_execution`까지 적용한다. DuckDB 개발 DB는 기존 bootstrap에서 새 테이블을 준비한다.
2. 각 Windows PC에 기존 Python 실행 환경을 준비한 뒤 아래 명령으로 도우미를 시작한다. 예시 주소는 실제 사내 Workbench 주소로 바꾼다. `-InstallAutoStart`는 해당 Windows 사용자 로그인 시 도우미를 시작하는 바로가기를 만든다. 개발 과정에서 사용자의 시작 프로그램을 직접 변경하지는 않았다.

   ```powershell
   .\start-local-runner.ps1 -ServerUrl https://workbench.example.com -InstallAutoStart
   ```

3. 웹에서 회사 계정으로 로그인 → **내 PC 설정** → 도우미 시작 파일 받기·최초 실행 → `다시 확인` → `이 PC 연결` → PC 승인창에서 계정·서버 확인. 기존 도우미가 실행 중이면 시작 파일 단계는 생략한다. 이후에는 코드 입력 없이 연결이 복원된다. `연결 해제`는 중앙 연결을 폐기하며 과거 이력과 로컬 기록을 지우지 않는다.
4. 웹 Origin이 API 서버와 다르면 `-Origin`에 실제 웹 주소를 명시한다. 개발 예시는 `-ServerUrl http://127.0.0.1:8000 -Origin http://127.0.0.1:5173`이다. 도우미 기본 포트는 8766이다. 다른 설정의 도우미가 포트를 사용 중이면 명확히 실패하며 임의로 기존 연결을 인수하지 않는다.

로컬 실행 기록·중앙 승인·최초 outbox·중복 제출 키는 하나의 SQLite 트랜잭션으로 저장한 후 프로세스를 시작한다. 상태 변경도 outbox와 함께 저장한다. 중앙 이벤트 요청은 최대 1 MiB, 다른 관리 API 요청은 최대 64 KiB다. 도우미는 실제 전송 바이트 크기로 이벤트를 나누고 413 응답에는 더 작게 나눈다. 일시적 실패는 최대 300초까지 간격을 늘려 재시도한다. 해제/무효화된 연결의 영구 401/403은 해당 연결의 전송을 중지하고 미전송 기록을 보존한다.

프로그램 내부에서 수행한 모든 클릭이나 모델 편집 내용까지 자동 기록하지 않는다. 실행/배치 항목/상태/완료 메모가 이번 이력의 범위다. 실제 HyperMesh 버전별 실행 인자, 회사 라이선스와 사내 브라우저의 로컬 접근 정책은 각 운영 환경에서 확인한다. 검증 결과는 [운영 검증 기록](managed-local-execution-qa.md)에 남긴다.
