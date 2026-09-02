# 권한·메뉴 정책 기능 변경 이력

## 1. 문서 정보

| 항목 | 값 |
|---|---|
| 릴리스 식별자 | Access Control & Menu Policy v1 |
| 기준일 | 2026-09-01 |
| 운영 대상 | 사내 Windows VM, PostgreSQL, HTTPS, OIDC, 사내 디렉터리 API |
| 개발·테스트 대상 | Windows PC, DuckDB 또는 PostgreSQL, password/disabled 인증 |
| 관련 계획서 | [사내 SSO·프로젝트 권한·좌측 메뉴 정책 구축 계획서](access-control-and-menu-policy-plan.md) |
| 관련 수행서 | [권한 및 메뉴 정책 구현 수행서](access-control-and-menu-policy-implementation-guide.md) |
| 기능 계약 | [권한·메뉴 정책 기능 사양서](access-control-functional-specification.md) |

이 문서는 권한 기능 도입으로 실제 변경된 범위, 데이터 이전 방식, 검증 결과와 운영 인수 조건을 기록한다. Git 커밋 이력을 대신하는 문서가 아니라, 기능 단위 릴리스와 사내 마이그레이션을 위한 변경 명세다. 2026-08-31 프로젝트 멤버십 CRUD, 프로젝트 초대·외부 directory lifecycle, 독립 assignee 후보 query 구조 전환 내용을 기존 변경 이력에 병합했다. 2026-09-01 account status 및 global-admin command vertical slice 완료 내용을 추가했다.

## 2. 주요 의사결정 변경

| 구분 | 초기 구상 | 최종 반영 |
|---|---|---|
| 역할 | 관리자·파워 사용자·일반 사용자 | 프로젝트 역할은 `admin`·`power`·`general` 3단계로 유지하고, 시스템 전체 복구와 승인은 별도 `is_global_admin`으로 분리 |
| 메뉴 | 역할별 고정 노출 | 전역 관리자가 역할별 표시 정책을 버전 관리하며, 최종 노출은 계정·permission·컨텍스트·정책의 교집합으로 계산 |
| 인증 | 사내 임직원 ID 검색 중심 | OIDC SSO 계정과 디렉터리 검색을 분리. 최초 SSO 사용자는 `PENDING`, 전역 관리자 승인 후 `ACTIVE` |
| 권한 범위 | 전역 역할 위주 | 명시적 permission과 리소스에서 해석한 프로젝트 범위로 모든 변경 API를 검사 |
| 업무 실행 | 표시 이름 담당자 비교 | 불변 사용자 키 `owner_user_id` 비교. 전역 관리자 예외 실행은 별도 permission과 감사 이벤트 필요 |
| 배포 | Linux 운영 검토 | 최종 운영 기준을 사내 Windows VM으로 변경. Linux CI는 이식성 회귀 검증 수단으로 유지 |
| 구조 | 기존 기능에 직접 혼합 가능성 | 모듈형 모놀리스 경계와 안정된 facade를 두어 인증·권한·메뉴 정책을 다른 도메인에서 분리 |

## 3. 변경 내역

### 3.1 인증과 계정 상태

- OIDC Authorization Code 흐름에 S256 PKCE, `state`, `nonce` 검증을 추가했다.
- issuer, JWKS 서명, audience, 만료, 발급 시각, nonce를 검증하고 검증 실패 시 로그인하지 않는다.
- OIDC 교환에 사용한 verifier와 state는 서명된 짧은 수명의 HttpOnly·Secure·SameSite=Lax 임시 쿠키에만 저장한다.
- 로그인 완료 후 애플리케이션 세션은 HttpOnly·Secure·SameSite=Strict 쿠키를 사용한다.
- OIDC 토큰, 디렉터리 토큰과 비밀 값은 데이터베이스·브라우저 저장소·감사로그에 저장하지 않는다.
- 외부 식별자는 `(oidc_issuer, oidc_subject)` 유일 조합으로 관리한다. 기존 계정과 자동 병합하지 않는다.
- 최초 OIDC 로그인 사용자를 `PENDING`으로 생성하고 승인 대기 화면 이외의 데이터 부트스트랩을 차단한다.
- 각 요청에서 계정 상태를 데이터베이스로 재검증해 `SUSPENDED` 변경을 기존 세션에도 즉시 반영한다.
- 로컬 개발과 회귀 테스트를 위해 `password`, `disabled` 인증 모드를 유지했다.
- 마지막 활성 전역 관리자의 정지 또는 전역 관리자 해제를 409로 차단한다.

### 3.2 프로젝트 역할과 permission

- 기존 `viewer/editor/admin` 전역 역할 기반 권한 판정을 제거하고 프로젝트 멤버십 `general/power/admin`을 도입했다.
- 20개의 명시적 permission을 정의하고 역할별 정확한 집합을 고정했다.
- 승인된 사용자는 회사·프로젝트 읽기 기능을 사용할 수 있지만 프로젝트 변경 권한은 멤버십을 통해서만 얻는다.
- 전역 관리자는 모든 프로젝트 permission과 시스템 permission을 합성한다.
- HTTP 메서드·URL 서열에 의존하던 `minimum_role` 런타임 판정을 제거했다.
- 변경 API는 경로 또는 데이터베이스 리소스 관계에서 프로젝트 ID를 해석한다. 요청 body의 프로젝트 ID로 권한 범위를 신뢰하지 않는다.
- 프로젝트의 마지막 admin을 일반 관리자가 강등하거나 제거하지 못하게 했다.

### 3.3 담당자와 업무 실행

- `analysis_requests`, `request_steps`, `request_work_items`에 `owner_user_id`를 추가했다.
- 의뢰 생성과 재배정 시 `ACTIVE`이면서 해당 프로젝트 멤버인 사용자만 담당자로 저장한다.
- 표시 이름은 화면용 스냅샷이며 권한 판단에는 사용하지 않는다.
- 일반 업무 실행은 `work.execute_assigned`와 `owner_user_id == principal.user_id`를 모두 요구한다.
- 담당자가 비어 있는 기존 데이터는 409 `OWNER_REASSIGNMENT_REQUIRED`로 차단하고 관리자 재배정 API로 복구한다.
- 전역 관리자의 타인 업무 실행은 `work.execute_any`가 있을 때만 허용하고 `WORK_EXECUTION_OVERRIDE` 감사 이벤트를 남긴다.
- 의뢰 및 작업 항목 담당자 변경을 트랜잭션 내 감사 이벤트와 함께 저장한다.

### 3.4 사내 디렉터리, 초대와 멤버십

- 백엔드 전용 디렉터리 adapter를 추가했다. 프런트엔드는 사내 디렉터리 토큰이나 원본 응답에 접근하지 않는다.
- 검색어 길이, 결과 수, 타임아웃과 최소 개인정보 응답 필드를 강제한다.
- 운영 HTTP adapter는 장애나 비정상 응답 시 503으로 실패하고 로컬 사용자 권한을 합성하지 않는다.
- 프로젝트 초대 상태를 `PENDING_ACCOUNT`, `PENDING_APPROVAL`, `READY`, `COMPLETED`, `CANCELLED`로 관리한다.
- 완료·취소된 초대의 재실행과 동일 프로젝트 중복 멤버십을 차단한다.
- 담당자 후보 API는 현재 프로젝트의 `ACTIVE` 멤버만 반환한다.

프로젝트 멤버십 CRUD의 첫 구조 전환은 다음 네 endpoint에 적용했다.

- `GET/POST /api/projects/{project_id}/members`와
  `PATCH/DELETE /api/projects/{project_id}/members/{user_id}`를 HTTP router,
  framework-neutral application use case, domain model/port, SQL persistence adapter로
  분리했다.
- 기존 project existence → same-connection 권한 검사와 PostgreSQL table lock을
  유지했다. 마지막 project admin 보호, 전역 관리자 우회, open work 차단과 stale
  timestamp 검사를 동일한 결과 계약으로 보존한다.
- membership mutation과 exact audit detail을 같은 transaction에 기록하며, audit
  실패 시 mutation도 함께 rollback한다.
- 기존 route 등록 위치, operationId와 응답 순서를 유지해 OpenAPI 호환성을 지켰다.
- 초대·directory lifecycle과 assignee 후보 API도 독립 application/domain/adapter
  경계로 분리했으며, 기존 route·OpenAPI 계약을 유지한다.

### 3.5 좌측 메뉴와 정책 관리

- 백엔드와 프런트엔드가 공유하는 14개 메뉴 registry를 도입했다.
- 메뉴 노출을 `ACTIVE ∧ permission ∧ context ∧ policy`로 계산한다.
- 정책 값이 `true`여도 permission이 없으면 메뉴가 보이지 않으며 API 권한도 생기지 않는다.
- 전역 관리자는 복구 가능성을 위해 메뉴 정책을 우회하지만 계정 상태, permission과 컨텍스트 검사는 유지한다.
- 전역 관리자가 역할별 메뉴 표시 여부를 편집하는 관리 화면을 추가했다.
- 변경 시 전체 정책 snapshot과 새 버전, 변경 메모, 수행자, 시각을 저장한다.
- `expected_version` 낙관적 잠금으로 동시 편집 충돌을 409로 반환한다.
- 과거 버전 조회·미리보기·복원을 추가했다. 복원도 새 버전으로 기록한다.
- 숨겨진 화면의 직접 진입을 막고 안전한 기본 화면으로 이동한다.
- 메뉴 정책은 탐색 UI 정책이며, 숨겨진 메뉴의 API 허용 여부는 permission으로 독립 판정한다.
- 정책 조회 실패 시 일반 메뉴는 fail-closed로 숨기고 전역 관리자의 정책·감사 복구 경로는 유지한다.

### 3.6 관리자 UI와 권한 갱신

- 사용자 승인·정지, 전역 관리자 설정, 프로젝트 멤버·초대 관리 화면을 추가했다.
- 메뉴 정책 편집, 버전 내역과 복원 화면을 추가했다.
- `/api/auth/me`를 프런트엔드 권한의 유일한 권위 데이터로 사용한다.
- 브라우저 저장소에는 사용자 ID와 표시 이름의 편의 캐시만 두며 permission이나 역할을 신뢰하지 않는다.
- 프로젝트 전환, 탭 포커스, 정책 저장·복원 후 권한과 메뉴 정책을 다시 가져온다.
- 403 응답 시 권한 정보를 갱신해 회수된 권한을 열린 화면에 남기지 않는다.
- 프로젝트 전환 시 현재 컨텍스트에 유효하지 않은 편집 draft를 취소한다.

### 3.7 데이터베이스와 마이그레이션

- Alembic `0007_access_control_menu_policy` 마이그레이션을 추가했다.
- `users`에 임직원·OIDC·계정 상태·전역 관리자·승인·로그인 필드를 추가했다.
- 다음 테이블을 추가했다.

  - `project_memberships`
  - `project_invitations`
  - `menu_definitions`
  - `menu_policy_state`
  - `role_menu_policies`
  - `menu_policy_versions`
  - `project_workspace_layouts`
  - `project_workspace_layout_versions`

- PostgreSQL과 DuckDB에 동일한 유일성, 상태, 역할 의미를 적용했다.
- 기존 `admin`은 활성 전역 관리자로, `editor`는 기존 프로젝트의 power 멤버로, `viewer`는 general 멤버로 이전한다.
- 기존 owner 표시 이름이 사용자 한 명과 유일하게 일치할 때만 `owner_user_id`를 채운다. 중복·모호한 값은 비워 두고 재배정을 요구한다.
- DuckDB 데이터 backfill은 내구성 marker로 한 번만 수행해 재시작 시 권한 재부여·재승격·담당자 재매칭이 발생하지 않게 했다.
- PostgreSQL canonical schema export와 DuckDB→PostgreSQL 전송 manifest에 신규 테이블과 관계를 반영했다.
- PostgreSQL 빈 DB의 head 마이그레이션과 실제 0006 fixture의 0007 이전 검증 스크립트를 추가했다.
- 애플리케이션 시작 전 필수 0007 테이블과 마이그레이션 상태를 검사한다.

### 3.8 감사와 원자성

- 로그인 성공·실패, 승인 대기 차단, 정지 계정 차단을 감사한다.
- 계정 상태, 전역 관리자, 멤버십, 초대, 담당자, 메뉴 정책 변경과 업무 실행 override를 감사한다.
- 보안·관리 변경은 도메인 변경과 동일 트랜잭션에서 감사 이벤트를 기록한다. 감사 기록 실패 시 변경도 롤백한다.
- 이벤트에는 수행자, 대상, 프로젝트, request ID, 결과와 허용된 변경 전후 값을 포함한다.
- 비밀번호, 토큰, secret, 원시 디렉터리 검색어와 결과는 기록하지 않는다.

### 3.9 모듈 경계와 배포

- `backend/app/modules/access_control`을 안정된 공개 facade로 두고 다른 도메인이 내부 provider 구현을 직접 참조하지 않게 했다.
- OIDC와 디렉터리 연동을 adapter로 격리해 사내 IdP·디렉터리 교체 시 도메인 기능 수정을 최소화했다.
- 권한 기능을 끄는 우회 feature flag는 만들지 않았다. 로컬 개발 호환은 명시적 인증·디렉터리 adapter로 제공한다.
- `DEPLOYMENT_PROFILE=windows-vm-intranet` 운영 프로필과 시작 전 fail-closed 검사기를 추가했다.
- 운영 프로필은 PostgreSQL, OIDC, HTTP 디렉터리, Secure 쿠키, HTTPS issuer·callback을 요구한다.
- Windows PowerShell 시작 절차에서 배포 프로필, 마이그레이션, 데이터베이스 준비 상태를 검사한다.
- 최초 전역 관리자 승인 CLI와 사내 이전 preflight JSON 도구를 추가했다.
- Linux CI는 Python 3.12, PostgreSQL, 프런트 빌드와 E2E의 교차 플랫폼 회귀 검증으로 유지한다.

### 3.10 Access vertical slice 구조 전환

- `backend/app/adapters/http/routers/project_memberships.py`가 네 CRUD endpoint의
  HTTP mapping과 권한 진입점을 담당한다.
- `backend/app/application/project_memberships/memberships.py`가 command/query와
  transaction orchestration을 담당하고, `backend/app/domains/project_memberships/`가
  typed model과 port를 소유한다.
- `backend/app/adapters/persistence/project_memberships.py`가 DuckDB/PostgreSQL
  연결에서 조회·lock·mutation·audit adapter를 제공한다.
- 프로젝트 멤버십 CRUD extraction으로 legacy `access_control` router의 직접 SQL
  실행 ceiling을 46에서 36으로 낮췄다.
- 프로젝트 초대·외부 directory lifecycle은
  `backend/app/adapters/http/routers/project_invitations.py`,
  `backend/app/application/project_invitations/`,
  `backend/app/domains/project_invitations/`, directory/persistence adapter로
  분리했다. directory 검색, 초대 생성·조회·완료·취소, 완료 시 멤버십 생성과
  중복·재실행 차단을 같은 transaction에서 처리하고 audit 원자성을 유지한다.
- 프로젝트 assignee 후보 query는
  `backend/app/adapters/http/routers/project_assignees.py`,
  `backend/app/application/project_assignees/`,
  `backend/app/domains/project_assignees/`, persistence adapter로 독립 분리했다.
  프로젝트 존재·권한 확인 뒤 active project member 후보만 반환하고 검색어 정규화,
  제외 조건, 응답 계약을 유지한다.
- 프로젝트 초대·directory·assignee extraction으로 legacy `access_control` router의
  직접 SQL 실행 ceiling을 36에서 20으로 낮췄으며, 새 HTTP router에는 직접 SQL을
  두지 않았다.
- 멤버십·초대/directory·assignee 후보 두 vertical slice의 focused 검증은
  **75 passed in 64.81s, exit 0**다. 이 변경을 포함한 현재 전체 backend suite는
  **897 passed, 10 skipped in 651.42s (0:10:51), exit 0**이다.
- 개인 노트북 검증 범위는 DuckDB/application/contract 테스트다. PostgreSQL
  multi-connection·app-role과 사내 IdP/directory/proxy/CA/Rocky 배포는
  [개인 노트북→사내 인수인계 게이트](personal-laptop-to-corporate-release-handoff.md)에서
  별도로 확인한다.

### 3.11 계정 상태와 전역 관리자 command

- `PATCH /api/admin/users/{user_id}/status`와
  `PATCH /api/admin/users/{user_id}/global-admin`을
  `HTTP → application → domain port → persistence adapter`로 분리했다.
- HTTP composition은 PostgreSQL 전용 lock provider를 status command에서
  `(users, project_invitations)`, global-admin command에서 `(users,)`로 고정한다.
  provider가 transaction을 시작하고 lock을 획득한 뒤 application은 같은
  connection의 fresh actor에 대해 `SYSTEM_USER_APPROVE` 권한을 재검사하고
  target user를 읽는다. status mutation은 invitation을 먼저 읽지 않고 일치하는
  pending invitation을 원자적으로 갱신한다.
- stale timestamp, 마지막 활성 전역 관리자 보호, 승인 시 `ACTIVE` 전이,
  초대의 `READY` 전이를 기존 결과 계약으로 보존한다.
- mutation과 exact audit detail은 같은 transaction에서 기록하며 audit 실패 시
  mutation도 rollback한다.
- 응답은 민감 정보가 제거된 안전한 14-field projection으로 제한하고 route,
  operationId, 응답 순서와 OpenAPI 계약을 유지한다.

### 3.12 메뉴 정책 vertical slice

- public navigation policy 조회는 기존처럼 모든 `ACTIVE` 인증 사용자에게 열어 두고,
  메뉴 정책 관리 권한으로 잘못 제한하지 않는다.
- admin version list/detail과 update/restore는 요청 시점의 fresh authorization을
  재검사한다. PostgreSQL mutation transaction은
  `(menu_policy_state, role_menu_policies)` fixed table lock을 사용한다.
- update마다 역할별 전체 정책 snapshot과 version history를 기록한다. 과거 부분
  snapshot을 복원할 때는 누락 값을 현재 정책에서 overlay해 legacy history도
  복구할 수 있게 한다.
- historical snapshot visibility는 기존 Pydantic coercion을 거친다. coercion할 수
  없는 role/value 등 malformed history는 mutation을 저장하지 않고 rollback한다.
- live 정책 상태, 새 version snapshot과 exact audit event를 같은 transaction에
  기록하며 audit 실패 시 정책 변경도 원자적으로 rollback한다.
- `backend/app/routers/access_control.py`는 하위 router include만 담당하는
  composition-only 경계가 되었다. legacy 직접 SQL `execute` ceiling은
  **11 → 0**으로 낮아졌고, menu policy slice를 포함한 focused 검증은
  **79 passed**다.

## 4. 주요 파일 변경 지도

| 영역 | 주요 파일 | 책임 |
|---|---|---|
| 권한 엔진 | `backend/app/access_policy.py` | permission 집합, 메뉴 registry, principal 재검증, 프로젝트·owner guard |
| 공개 모듈 경계 | `backend/app/modules/access_control/` | 다른 도메인이 사용하는 안정된 facade |
| 인증·세션 | `backend/app/security.py`, `backend/app/routers/security.py` | 세션, OIDC 진입, `/me`, 사용자·감사 조회 |
| 관리 API | `backend/app/adapters/http/routers/user_administration.py` | 계정 상태·전역 관리자 HTTP adapter |
| 메뉴 정책 API | `backend/app/adapters/http/routers/menu_policy.py` | public policy 조회와 admin version list/detail·update/restore HTTP adapter |
| Access composition | `backend/app/routers/access_control.py` | 분리된 access 하위 router include만 담당하는 composition-only 경계 |
| Access HTTP adapters | `backend/app/adapters/http/routers/project_memberships.py`, `project_invitations.py`, `project_assignees.py` | 프로젝트 멤버십 CRUD, 초대·directory lifecycle, assignee 후보 API와 권한 진입점 |
| provider | `backend/app/services/oidc_service.py`, `backend/app/services/directory_service.py` | 외부 IdP·디렉터리 연동 격리 |
| 업무 배정 | `backend/app/main.py`, `backend/app/routers/workbench.py` | canonical 담당자, 재배정, 실행 guard와 override 감사 |
| 데이터 | `backend/app/database.py`, `backend/migrations/versions/0007_access_control_menu_policy.py` | DuckDB·PostgreSQL 스키마와 backfill |
| 이전·운영 도구 | `backend/scripts/access_migration_preflight.py`, `backend/scripts/approve_oidc_global_admin.py`, `backend/scripts/verify_postgres_0006_access_upgrade.py` | 사전 점검, 초기 관리자, 실제 PG 이전 검증 |
| 프런트 권한 | `frontend/src/features/auth/access.ts`, `frontend/src/features/navigation/` | `/me` 기반 권한·메뉴 계산과 직접 진입 guard |
| 관리 화면 | `frontend/src/features/access/` | 사용자·멤버십·초대·메뉴 정책·감사 UI |
| 계약 | `frontend/openapi.json`, `frontend/src/shared/api/generated/openapi.ts` | 서버 OpenAPI와 생성 클라이언트 |
| 배포 설정 | `.env.example`, `start.ps1`, `.github/workflows/ci.yml` | Windows VM 운영 프로필과 CI 회귀 검증 |

## 5. 검증 기록

2026-08-11 기준 다음 검증을 완료했다.

| 검증 | 결과 |
|---|---|
| 백엔드 전체 pytest | **897 passed, 10 skipped in 651.42s (0:10:51), exit 0** |
| 권한·보안 집중 회귀 | 31 passed |
| `minimum_role` 제거 후 보안·권한 집중 회귀 | 6 passed |
| 배포 프로필·마이그레이션 preflight 집중 검증 | 관련 20 passed, 별도 preflight 2 passed |
| 프런트 TypeScript·프로덕션 빌드 | 성공 |
| OpenAPI 생성 결정성 | 연속 2회 생성 결과 SHA 동일 |
| Playwright E2E | 19 passed, 약 2.3분 |
| 4 persona 메뉴 검증 | general, power, project admin, global admin 통과 |
| 메뉴 정책 관리 화면 브라우저 확인 | 제목·3개 역할 열 표시, console warning/error 없음 |
| PowerShell 스크립트 재귀 구문 검사 | 통과 |

테스트는 권한 truth table, OIDC 부정 검증, PENDING·SUSPENDED, 프로젝트 간 변조, owner 충돌과 NULL, 초대 상태 전이, 메뉴 버전 충돌·복원, 감사 원자성, DuckDB 이전, PostgreSQL 스키마 계약과 4개 persona를 포함한다.

2026-08-31 프로젝트 멤버십 CRUD, 프로젝트 초대·외부 directory lifecycle, assignee
후보 query vertical slice의 focused 검증은 **75 passed in 64.81s, exit 0**다. 멤버십 CRUD만 반영한
이전 기준은 `865 passed, 10 skipped`였으며, 현재 두 slice를 포함한 전체 backend
검증은 **897 passed, 10 skipped in 651.42s (0:10:51), exit 0**이다. PostgreSQL·사내 환경이 필요한 corporate proxy/CA,
Rocky/Postgres/deploy gate는 통과로 간주하지 않고 office-only 인수 단계에 남긴다.

2026-09-01 account status/global-admin command slice의 focused 검증은 **14 passed**,
access focused 검증은 **89 passed in 77.27s, exit 0**이다. architecture/OpenAPI/compile
검증을 통과했으며, 이를 포함한 full backend 검증은 **911 passed, 10 skipped in
694.22s (0:11:34), exit 0**이다.

2026-09-01 menu policy vertical slice 단독 검증은 **19 passed**, 확대 focused
검증은 **79 passed**다.
public `ACTIVE` 사용자 조회, admin fresh authorization, version/snapshot 복원,
legacy partial overlay와 historical coercion, malformed history·audit 실패 rollback,
route/OpenAPI 및 composition-only architecture 경계를 검증했다. 완료된 full backend
회귀는 **929 passed, 10 skipped in 691.50s (0:11:31), exit 0**이다. 전체 실행 뒤에는
production 변경 없이 계약 test 한 개만 보강했고, 그 추가 후 위 단독·확대 focused
검증을 다시 통과했다.

## 6. 운영 인수 전 남은 외부 확인

코드와 focused 로컬 검증은 완료했지만 다음 항목은 사내 인프라 자격 증명과 실제 운영
환경이 있어야 닫을 수 있는 office-only 운영 release gate다.

- 운영 PostgreSQL 백업 후 실제 0006→0007 dry-run과 checksum 확인
- 사내 IdP의 issuer·client·JWKS로 HTTPS OIDC redirect와 쿠키 왕복 확인
- 사내 디렉터리 API의 검색 timeout·최소 PII·장애 시 503 확인
- Windows VM 서비스 계정, 리버스 프록시, 인증서, 방화벽과 재시작 후 readiness 확인
- 초기 전역 관리자 지정 후 사용자 승인·메뉴 정책 복원 경로 확인

개인 노트북의 access vertical slice는 menu policy까지 완료했다. 다음 구현
우선순위는 reports 영역 분리, 이후 `app/main.py` 잔여 endpoint 정리다.
corporate proxy/CA, Rocky/Postgres와 실제 deploy
검증은 개인 노트북 범위가 아니라 사내 인수 단계에서 수행한다.

현재 개발 PC에서 운영 PostgreSQL owner 자격 증명이 없을 때 preflight가 `OWNER_CREDENTIAL_UNAVAILABLE`로 중단되는 것은 의도한 fail-closed 동작이다.

## 7. 롤백과 복구 원칙

- 운영 변경 전 PostgreSQL 논리 백업과 복구 리허설을 완료한다.
- 스키마 이전 후에는 애플리케이션 코드만 이전 버전으로 되돌리지 않는다. 새 owner·멤버십·메뉴 정책을 이해하는 코드와 DB revision을 함께 유지한다.
- 메뉴 오설정은 전역 관리자의 정책 버전 복원 기능으로 새 버전을 생성해 복구한다.
- 계정·권한 오설정은 감사로그로 변경자를 확인하고 명시적 관리 API 또는 초기 관리자 CLI로 복구한다.
- 마지막 전역 관리자·마지막 프로젝트 admin 보호를 우회하는 직접 SQL은 표준 복구 절차로 사용하지 않는다.
- 사내 이전 전후의 blocker와 warning은 `access_migration_preflight.py` JSON을 보관해 인수 증적으로 사용한다.
