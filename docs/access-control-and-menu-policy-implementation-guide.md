# 권한 및 메뉴 정책 구현 수행서

## 1. 수행서의 목적과 사용법

이 문서는 Luna 또는 다른 구현 에이전트가 [사내 SSO·프로젝트 권한·좌측 메뉴 정책 구축 계획서](./access-control-and-menu-policy-plan.md)를 추가 설계 판단 없이 구현하기 위한 실행 명세다.

플랫폼 우선순위는 `Windows VM 운영 배포 > 가정용 Windows 개발·테스트 > Linux 교차 플랫폼 CI`다. 운영 VM의 서비스 수명주기와 PostgreSQL·OIDC·백업/복구가 release gate이며, 현재 PC와 Linux CI는 동일 기능 계약을 빠르게 검증하는 환경이다.

계획서의 요구사항 ID가 최종 기준이다. 이 수행서와 코드 현황이 충돌하면 다음 순서로 처리한다.

1. 사용자 요구와 계획서의 확정 결정을 우선한다.
2. 기존 공개 API 호환성을 가능한 한 유지한다.
3. 보안을 약화시키는 호환성 처리는 허용하지 않는다.
4. 불가피하게 명세를 변경해야 하면 구현을 임의로 진행하지 말고 문서에 변경 이유와 대안을 먼저 기록한다.

## 2. Luna 수행 규칙

1. 작업 시작 전에 이 수행서와 계획서를 끝까지 읽는다.
2. 현재 worktree의 사용자 변경을 덮어쓰거나 되돌리지 않는다.
3. 아래 구현 단계 순서를 지키고 각 단계의 종료 조건을 통과한 뒤 다음 단계로 간다.
4. 프런트의 메뉴 숨김만으로 권한 구현을 끝내지 않는다. 모든 변경 API에 서버 권한 검사가 있어야 한다.
5. 클라이언트가 보내는 사용자 이름을 감사 작성자나 실행자로 신뢰하지 않는다.
6. 표시 이름으로 담당자 소유권을 판정하지 않는다.
7. 새 API 또는 스키마 변경 후 OpenAPI와 생성 클라이언트를 갱신한다.
8. 테스트를 삭제·skip·완화해 통과시키지 않는다.
9. 기존 `AUTH_MODE=password`와 `AUTH_MODE=disabled` 테스트를 유지한다.
10. 사내 실제 OIDC·디렉터리 값이 없을 때는 명시적 mock adapter로 테스트하며 운영 우회 계정을 자동 생성하지 않는다.
11. 구현 완료 시 변경 파일, 마이그레이션, 테스트 결과, 남은 외부 설정을 작업 로그에 남긴다.

## 3. 완료 정의

구현은 다음이 모두 충족될 때만 완료다.

- 계획서 `AUTH-*`, `RBAC-*`, `MENU-*`, `DIR-*`, `AUDIT-*`, `OPS-*` 요구사항이 코드와 테스트로 추적된다.
- PostgreSQL Alembic upgrade가 성공한다.
- 신규/기존 DuckDB 데이터베이스 초기화와 호환 업그레이드가 성공한다.
- 백엔드 전체 pytest가 통과한다.
- 프런트 TypeScript 빌드가 통과한다.
- 네 역할 시나리오와 메뉴 정책 변경 Playwright E2E가 통과한다.
- `frontend/openapi.json`, `frontend/src/generated/openapi.ts`가 최신 백엔드 계약과 일치한다.
- 운영 설정 문서에 OIDC·디렉터리 환경변수와 첫 전역 관리자 복구 방법이 추가된다.
- Windows와 Ubuntu Linux에서 동일한 권한·메뉴 동작을 검증하는 실행 경로가 존재한다.

## 4. 구현 아키텍처

```text
사내 OIDC IdP
    │ Authorization Code + PKCE
    ▼
FastAPI 인증 라우터 ── users(account_status, oidc subject)
    │
    ├─ Principal + PermissionService
    │       ├─ 전사 읽기 permission
    │       ├─ project_memberships의 프로젝트 permission
    │       └─ is_global_admin의 시스템 permission
    │
    ├─ 보호된 도메인 API
    │       └─ permission + project_id + owner_user_id 검사
    │
    ├─ 디렉터리 프록시 ── 사내 임직원 검색 API
    │
    └─ 메뉴 정책 API ── role_menu_policies + versions
                            │
                            ▼
React AuthProvider + AccessPolicyProvider
    ├─ 선택 프로젝트의 effective role/permissions
    ├─ 서버 메뉴 정책
    ├─ 좌측 메뉴 필터
    ├─ 페이지 진입 가드
    └─ 버튼/편집/실행 가드
```

인증 미들웨어는 주체 확인과 계정 상태 차단을 담당한다. 권한은 각 라우트 또는 서비스가 명시적으로 요청한다. 기존 `minimum_role(method, path)`는 단계적으로 제거하고 최종적으로 보안 결정에 사용하지 않는다.

### 4.1 독립 모듈 경계

- 분석·의뢰·실행 코드는 `access_policy` facade의 `require_permission`, `require_resource_permission`, `resolve_project_assignee`, `require_assigned_work_item`만 호출한다.
- OIDC SDK, issuer/JWKS, 사내 claim mapping은 인증 provider 모듈 밖으로 노출하지 않는다.
- 임직원 디렉터리의 URL·토큰·vendor JSON은 `EmployeeDirectory` adapter 밖으로 노출하지 않는다.
- React 화면은 provider 이름이나 사내 응답 필드가 아니라 `AuthUser`, `ProjectMembership`, `DirectoryEmployee`, `Permission`, `MenuPolicy` DTO만 사용한다.
- `AUTH_MODE=disabled|password|oidc`와 `DIRECTORY_MODE=local|http`를 조합하되, `oidc/http` 운영 profile에서 설정 누락 시 시작을 실패시킨다. 운영에서 local adapter로 자동 fallback하지 않는다.
- 독립성은 보안을 끄는 기능 flag로 구현하지 않는다. `disabled` 모드의 명시적 local-admin과 provider adapter가 로컬 개발 호환 경계다.

## 5. 공통 용어와 상수

백엔드와 프런트에서 다음 문자열을 동일하게 사용한다.

```text
AccountStatus = PENDING | ACTIVE | SUSPENDED
ProjectRole   = general | power | admin
InvitationStatus = PENDING_ACCOUNT | PENDING_APPROVAL | READY | COMPLETED | CANCELLED
MenuPolicyRole = general | power | admin
```

Permission 문자열은 계획서 5.1의 값을 그대로 사용한다. 문자열을 라우트마다 직접 반복하지 말고 한 모듈에서 상수로 정의한다.

권장 파일 책임은 다음과 같다.

- `backend/app/security.py`: Principal, 인증 미들웨어, 세션 처리
- `backend/app/access_policy.py`: permission 상수, 역할 매핑, 프로젝트 범위 판정
- `backend/app/services/directory_service.py`: 디렉터리 adapter와 검색 정규화
- `backend/app/routers/security.py`: 로그인, 내 정보, 사용자 승인
- `backend/app/routers/access_control.py`: 멤버십, 초대, 메뉴 정책, 감사 조회
- `frontend/src/auth.ts`: 인증 DTO와 세션 캐시
- `frontend/src/features/auth/AuthProvider.tsx`: 사용자·정책 로딩과 갱신
- `frontend/src/features/auth/accessPolicy.ts`: 순수 permission/메뉴 계산 함수
- `frontend/src/features/navigation/menuRegistry.tsx`: 메뉴 ID와 렌더링 컴포넌트 매핑
- `frontend/src/features/admin/AccessManagementPage.tsx`: 승인·멤버·초대 관리
- `frontend/src/features/admin/MenuPolicyPage.tsx`: 역할별 메뉴 정책과 버전 복원

실제 구조가 더 자연스러우면 파일을 나눌 수 있지만 책임과 테스트 가능성은 유지한다. `App.tsx` 안에 permission 테이블이나 관리자 화면 전체를 추가하지 않는다.

## 6. 단계 1 — 데이터베이스와 마이그레이션

### 6.1 PostgreSQL Alembic 마이그레이션

`0006_batch_attempts` 다음 revision으로 `0007_access_control_menu_policy.py`를 추가한다.

#### 6.1.1 users 확장

다음 컬럼을 추가한다.

| 컬럼 | 타입 | 규칙 |
|---|---|---|
| `employee_id` | VARCHAR | nullable, 값이 있으면 unique |
| `email` | VARCHAR | nullable |
| `department` | VARCHAR | nullable |
| `job_title` | VARCHAR | nullable |
| `oidc_issuer` | VARCHAR | nullable |
| `oidc_subject` | VARCHAR | nullable |
| `account_status` | VARCHAR | NOT NULL, 기본 `ACTIVE`, check 3개 상태 |
| `is_global_admin` | BOOLEAN | NOT NULL, 기본 false |
| `approved_by` | VARCHAR | nullable, users.id 논리 참조 |
| `approved_at` | TIMESTAMP | nullable |
| `last_login_at` | TIMESTAMP | nullable |

- OIDC 사용자 지원을 위해 `password_hash`의 NOT NULL 제약을 제거한다.
- `(oidc_issuer, oidc_subject)` unique 제약 또는 unique index를 만든다.
- 기존 `role` 컬럼은 한 호환 릴리스 동안 `legacy_role`로 이름을 변경해 보존하고 런타임 권한 판정에서는 사용하지 않는다.
- 신규 사용자 생성 코드가 `legacy_role`을 요구하지 않도록 nullable로 바꾼다.
- `idx_users_account_status`, `idx_users_employee_id`, `idx_users_oidc_identity`를 만든다.

#### 6.1.2 project_memberships

```sql
CREATE TABLE project_memberships (
    id VARCHAR PRIMARY KEY,
    project_id VARCHAR NOT NULL,
    user_id VARCHAR NOT NULL,
    role VARCHAR NOT NULL,
    created_by VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_by VARCHAR NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_project_membership UNIQUE (project_id, user_id),
    CONSTRAINT ck_project_membership_role CHECK (role IN ('general', 'power', 'admin'))
);
```

인덱스:

- `idx_project_memberships_user_project (user_id, project_id)`
- `idx_project_memberships_project_role (project_id, role, user_id)`

DB foreign key를 기존 스키마 관례 때문에 적용하지 않는다면 삭제·존재 검사를 repository에서 트랜잭션으로 강제하고 그 결정을 주석에 남긴다.

#### 6.1.3 project_invitations

```sql
CREATE TABLE project_invitations (
    id VARCHAR PRIMARY KEY,
    project_id VARCHAR NOT NULL,
    employee_id VARCHAR NOT NULL,
    display_name_snapshot VARCHAR NOT NULL,
    department_snapshot VARCHAR,
    desired_role VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    resolved_user_id VARCHAR,
    invited_by VARCHAR NOT NULL,
    invited_at TIMESTAMP NOT NULL,
    resolved_by VARCHAR,
    resolved_at TIMESTAMP,
    cancelled_by VARCHAR,
    cancelled_at TIMESTAMP,
    CONSTRAINT ck_project_invitation_role CHECK (desired_role IN ('general', 'power', 'admin')),
    CONSTRAINT ck_project_invitation_status CHECK (
        status IN ('PENDING_ACCOUNT', 'PENDING_APPROVAL', 'READY', 'COMPLETED', 'CANCELLED')
    )
);
```

- 동일 프로젝트·임직원에 완료되지 않은 초대가 하나만 존재하도록 application transaction과 조회 인덱스로 보장한다.
- PostgreSQL partial unique index를 사용할 경우 DuckDB에서는 동일 검사를 repository에 구현한다.
- `idx_project_invitations_project_status (project_id, status, invited_at DESC)`와 `idx_project_invitations_employee (employee_id, status)`를 만든다.

#### 6.1.4 메뉴 정책 테이블

```sql
CREATE TABLE menu_definitions (
    id VARCHAR PRIMARY KEY,
    label VARCHAR NOT NULL,
    required_permission VARCHAR NOT NULL,
    context_kind VARCHAR NOT NULL,
    sequence_no INTEGER NOT NULL,
    is_policy_editable BOOLEAN NOT NULL DEFAULT true,
    is_active BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE menu_policy_state (
    id VARCHAR PRIMARY KEY,
    version INTEGER NOT NULL,
    updated_by VARCHAR NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE role_menu_policies (
    role VARCHAR NOT NULL,
    menu_id VARCHAR NOT NULL,
    is_visible BOOLEAN NOT NULL,
    policy_version INTEGER NOT NULL,
    updated_by VARCHAR NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    PRIMARY KEY (role, menu_id),
    CONSTRAINT ck_role_menu_policy_role CHECK (role IN ('general', 'power', 'admin'))
);

CREATE TABLE menu_policy_versions (
    version INTEGER PRIMARY KEY,
    definition_json JSONB NOT NULL,
    created_by VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    source_version INTEGER,
    change_note VARCHAR
);
```

- `menu_policy_state` singleton ID는 `global`로 고정한다.
- 계획서 6.2 메뉴를 `menu_definitions`에 seed한다.
- `menu_policy_admin`, `audit_admin`은 `is_policy_editable=false`로 seed한다.
- 계획서 6.2 기본 노출을 `role_menu_policies` version 1로 seed한다.
- version 1 전체 스냅샷을 `menu_policy_versions`에 저장한다.
- 메뉴 경로나 React 컴포넌트 이름은 DB에서 실행하지 않는다. DB는 알려진 메뉴 ID 정책만 저장하고 프런트 registry가 ID를 실제 화면에 매핑한다.

#### 6.1.5 담당자 ID 컬럼

다음 테이블에 nullable `owner_user_id VARCHAR`와 조회 인덱스를 추가한다.

- `analysis_requests`
- `request_steps`
- `request_work_items`

기존 owner/display name 컬럼은 화면 스냅샷과 마이그레이션 호환을 위해 유지한다.

### 6.2 기존 데이터 backfill

마이그레이션은 한 트랜잭션 안에서 다음을 수행한다.

1. 모든 기존 사용자를 `ACTIVE`로 설정한다.
2. `legacy_role='admin'` 사용자는 `is_global_admin=true`로 설정한다.
3. 기존 모든 프로젝트와 editor 사용자의 조합에 `power` 멤버십을 생성한다.
4. 기존 모든 프로젝트와 viewer 사용자의 조합에 `general` 멤버십을 생성한다.
5. admin은 전역 관리자이므로 프로젝트별 멤버십 생성이 필수는 아니지만, 표시 일관성이 필요하면 생성하지 않고 effective role에서 합성한다.
6. owner 문자열을 `trim + casefold`한 값이 활성 사용자 display_name 또는 username과 정확히 하나만 일치할 때만 owner_user_id를 채운다.
7. 0개 또는 2개 이상과 일치하면 owner_user_id를 NULL로 유지한다.

마이그레이션은 반복 실행에 안전해야 하며 `ON CONFLICT DO NOTHING` 또는 존재 검사를 사용한다.

### 6.3 DuckDB 호환

- `backend/app/database.py`의 신규 DB 생성 SQL을 새 스키마와 일치시킨다.
- 기존 DuckDB 파일에 컬럼·테이블이 없으면 추가하는 idempotent 호환 업그레이드 함수를 둔다.
- PostgreSQL 전용 partial index, JSONB, `ON CONFLICT` 차이를 `database_connection`의 placeholder/JSON 처리 관례에 맞춘다.
- `backend/migrations/schema.sql`, PostgreSQL export/transfer 검증 목록, portability 테스트를 갱신한다.
- 파일·인증서·임시 DB 경로는 `pathlib.Path`와 설정값으로 조합하고 드라이브 문자, 역슬래시, 고정 `E:\\` 경로를 런타임 코드에 넣지 않는다.

### 6.4 단계 1 종료 조건

- 빈 PostgreSQL에서 `alembic upgrade head` 성공
- 기존 revision 0006 데이터에서 0007 upgrade 성공
- 신규/기존 DuckDB `initialize_database()` 성공
- 기존 계정과 owner 문자열 보존 확인
- 기본 메뉴 정책 version 1 조회 가능

## 7. 단계 2 — Permission 엔진과 서버 권한 검사

### 7.1 Principal 변경

`Principal`은 다음 값을 가진다.

```python
@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    display_name: str
    account_status: AccountStatus
    is_global_admin: bool
    employee_id: str | None
```

- 세션 토큰에는 최소 `sub`, `iat`, `exp`만 둔다.
- 역할·상태는 토큰 클레임을 신뢰하지 않고 매 요청 DB에서 읽는다.
- `PENDING`은 `/api/auth/me`, `/api/auth/logout`, 승인 상태 확인 외 일반 API를 `403 AUTH_ACCOUNT_PENDING`으로 거부한다.
- `SUSPENDED`는 세션 쿠키를 만료시키고 `403 AUTH_ACCOUNT_SUSPENDED`를 반환한다.
- `disabled` 모드 local principal은 `ACTIVE`, `is_global_admin=true`로 합성한다.

### 7.2 역할 permission 매핑

`access_policy.py`에 불변 집합을 정의한다.

```python
GENERAL_PERMISSIONS = frozenset({...})
POWER_PERMISSIONS = GENERAL_PERMISSIONS | frozenset({...})
PROJECT_ADMIN_PERMISSIONS = POWER_PERMISSIONS | frozenset({...})
GLOBAL_ADMIN_PERMISSIONS = PROJECT_ADMIN_PERMISSIONS | frozenset({...system permissions...})
```

필수 함수:

```python
def project_role(conn, principal, project_id) -> ProjectRole | None: ...
def permissions_for(principal, role) -> frozenset[str]: ...
def require_permission(request, permission, project_id=None) -> AccessContext: ...
def require_assigned_work_item(request, work_item_id) -> AccessContext: ...
```

동작 규칙:

- 전역 관리자는 모든 permission을 통과한다.
- `company.dashboard.view`, `project.data.view`, `report.export`는 모든 ACTIVE 사용자에게 허용한다.
- 프로젝트 변경 permission은 해당 프로젝트 멤버십을 조회한다.
- `work.execute_assigned`는 general 이상 멤버십과 owner_user_id 일치를 모두 요구한다.
- 전역 관리자는 `work.execute_assigned`만으로 owner 검사를 우회하지 않는다. 별도 시스템 permission `work.execute_any`를 가진 경우에만 담당자가 지정된 타인 업무를 실행하고 관리자 override 감사 이벤트를 남긴다.
- owner_user_id가 NULL이면 전역 관리자도 먼저 명시적으로 재배정하도록 하고 실행을 허용하지 않는다. 데이터 책임 추적을 우선한다.

### 7.3 프로젝트 ID 해석

각 변경 API는 리소스에서 프로젝트 ID를 서버가 계산한다.

- project 경로 API: path `project_id`
- request API: `analysis_requests.project_id`
- load case API: `load_cases → analysis_requests.project_id`
- workflow step/work item API: step/item → request → project
- dashboard API: dashboard/page → load case → request → project
- report/result/review API: run/load case → request → project

클라이언트가 body에 보낸 project_id로 기존 리소스의 범위를 판정하지 않는다.

### 7.4 기존 API permission 매핑

| API 그룹 | 변경 권한 |
|---|---|
| 프로젝트·의뢰·대시보드·결과 GET | `project.data.view` |
| 포트폴리오 GET/CSV | `company.dashboard.view` |
| 보고서 렌더·내보내기 | `report.export` |
| `POST /api/projects/{id}/requests` | `request.create` |
| 의뢰 유형·담당자·일반 정보 변경 | `request.edit` |
| workflow steps 생성·수정 | `workflow.edit` |
| result/folder import | `result.import` |
| review item 생성·수정 | `result.review` |
| dashboard definition·version·command 변경 | `dashboard.edit` |
| 프로젝트 workspace layout | `project.layout.edit` |
| 프로젝트 quality threshold | `project.threshold.manage` |
| load case variable 변경 | `project.variable.manage` |
| import schema, task/request type, batch profile, 전역 template | `system.catalog.manage` |
| work item start/progress/complete/batch dispatch | `work.execute_assigned` + owner 검사 |
| 사용자·메뉴 정책·전체 audit | 해당 `system.*` 또는 `audit.view` |

DELETE가 무조건 전역 관리자라는 기존 규칙은 제거한다. 삭제 대상의 도메인 permission과 기존 보호 규칙을 함께 사용한다. 시스템 페이지·현재 live 버전·최소 버전 보호 같은 기존 `409` 규칙은 유지한다.

### 7.5 오류 계약

권한 오류는 가능한 한 다음 구조를 사용한다.

```json
{
  "detail": {
    "code": "PERMISSION_DENIED",
    "message": "이 작업을 수행할 권한이 없습니다.",
    "required_permission": "dashboard.edit",
    "project_id": "project-tv-001"
  }
}
```

고정 오류 코드:

- `AUTHENTICATION_REQUIRED` — 401
- `AUTH_ACCOUNT_PENDING` — 403
- `AUTH_ACCOUNT_SUSPENDED` — 403
- `PERMISSION_DENIED` — 403
- `PROJECT_MEMBERSHIP_REQUIRED` — 403
- `WORK_ITEM_NOT_ASSIGNED` — 403
- `OWNER_REASSIGNMENT_REQUIRED` — 409
- `STALE_POLICY_VERSION` — 409
- `LAST_GLOBAL_ADMIN_PROTECTED` — 409
- `MENU_POLICY_LOCKED` — 409

### 7.6 단계 2 종료 조건

- 모든 변경 라우트에 명시적 permission 또는 owner 검사가 존재
- `minimum_role()`가 런타임 권한 결정에 사용되지 않음
- 전역 읽기, 프로젝트별 편집, 본인 업무 실행 단위 테스트 통과
- 다른 프로젝트의 ID를 body/path로 조작한 요청 거부

## 8. 단계 3 — OIDC SSO와 계정 상태

### 8.1 설정

`SecuritySettings`에 다음 값을 추가한다.

```text
AUTH_MODE=oidc
OIDC_ISSUER_URL=
OIDC_CLIENT_ID=
OIDC_CLIENT_SECRET=
OIDC_REDIRECT_URI=https://host/api/auth/oidc/callback
OIDC_SCOPES=openid profile email
OIDC_EMPLOYEE_ID_CLAIM=employee_id
OIDC_USERNAME_CLAIM=preferred_username
OIDC_DISPLAY_NAME_CLAIM=name
OIDC_DEPARTMENT_CLAIM=department
OIDC_JOB_TITLE_CLAIM=job_title
OIDC_LOGIN_SUCCESS_URL=https://host/
OIDC_LOGIN_FAILURE_URL=https://host/login
```

- secret은 로그와 오류 응답에 출력하지 않는다.
- issuer discovery 문서와 JWKS는 timeout을 적용하고 짧은 프로세스 캐시를 사용한다.
- issuer, audience, signature, exp, nonce를 모두 검증한다.

### 8.2 API

```text
GET /api/auth/oidc/start
GET /api/auth/oidc/callback
GET /api/auth/status
GET /api/auth/me
POST /api/auth/logout
```

- `/start`는 state/nonce/PKCE verifier를 서버가 서명한 단기 HttpOnly 쿠키 또는 서버 세션에 저장한다.
- `/callback`은 code를 교환하고 검증한 뒤 user를 issuer+subject로 찾는다.
- 신규 사용자는 claim의 최소 프로필로 `PENDING` 생성한다.
- 기존 사용자는 employee_id와 프로필 스냅샷을 안전하게 갱신하고 `last_login_at`을 기록한다.
- 동일 employee_id가 다른 subject와 이미 연결된 경우 자동 병합하지 않고 `409 OIDC_IDENTITY_CONFLICT`로 거부한다.
- PENDING 사용자는 세션을 만들되 프런트가 승인 대기 화면만 표시할 수 있는 `/me` 응답을 받는다.

### 8.3 `/api/auth/me` 응답

```json
{
  "id": "user-...",
  "username": "e12345",
  "display_name": "홍길동",
  "employee_id": "e12345",
  "account_status": "ACTIVE",
  "is_global_admin": false,
  "memberships": [
    {"project_id": "project-tv-001", "role": "power"}
  ],
  "company_permissions": ["company.dashboard.view", "project.data.view", "report.export"]
}
```

프로젝트 permission 전체 배열을 중복 전송하지 않는다. 프런트는 membership role과 공통 역할 매핑으로 파생하되, 최종 서버 판정은 독립적으로 수행한다.

### 8.4 계정 관리 API

```text
GET   /api/admin/users?q=&status=&limit=&cursor=
PATCH /api/admin/users/{user_id}/status
PATCH /api/admin/users/{user_id}/global-admin
```

- 전역 관리자 전용이다.
- status 변경 body는 `{account_status, expected_updated_at, reason}`다.
- global admin 변경 body는 `{is_global_admin, expected_updated_at, reason}`다.
- 마지막 ACTIVE 전역 관리자의 중지·권한 해제는 `409`로 거부한다.
- 자신의 계정을 중지하는 요청도 마지막 관리자 여부를 검사한다.
- 승인 시 `approved_by`, `approved_at`을 서버 principal로 기록한다.

### 8.5 단계 3 종료 조건

- mock IdP로 state/nonce/PKCE/signature/audience 테스트 통과
- 신규 PENDING, 승인 후 ACTIVE, SUSPENDED 기존 세션 차단 확인
- password/disabled 회귀 테스트 통과
- 토큰·secret이 응답, DB, 감사로그에 없음

## 9. 단계 4 — 디렉터리·초대·멤버십·담당자 API

### 9.1 Directory adapter

공통 인터페이스:

```python
class EmployeeDirectory(Protocol):
    def search(self, query: str, limit: int) -> list[DirectoryEmployee]: ...
    def get_by_employee_id(self, employee_id: str) -> DirectoryEmployee | None: ...
```

정규화 결과:

```python
class DirectoryEmployee(BaseModel):
    employee_id: str
    display_name: str
    department: str | None
    job_title: str | None
    email: str | None
    employment_status: Literal["ACTIVE", "INACTIVE"]
```

운영 adapter 설정:

```text
DIRECTORY_API_BASE_URL=
DIRECTORY_API_SEARCH_PATH=/employees/search
DIRECTORY_API_TOKEN=
DIRECTORY_API_TIMEOUT_SECONDS=3
DIRECTORY_API_RESULT_LIMIT=20
```

- query는 trim 후 2~80자만 허용한다.
- 결과는 기본 20개, 최대 50개로 제한한다.
- `employment_status=ACTIVE`만 초대 가능하다.
- 검색 응답을 영구 DB에 저장하지 않는다. 초대 시 최소 스냅샷만 저장한다.
- timeout/5xx는 `503 DIRECTORY_UNAVAILABLE`로 반환한다.

### 9.2 디렉터리와 멤버십 API

```text
GET    /api/projects/{project_id}/directory/employees?q=&limit=
GET    /api/projects/{project_id}/members
POST   /api/projects/{project_id}/members
PATCH  /api/projects/{project_id}/members/{user_id}
DELETE /api/projects/{project_id}/members/{user_id}
GET    /api/projects/{project_id}/invitations
POST   /api/projects/{project_id}/invitations
POST   /api/projects/{project_id}/invitations/{invitation_id}/complete
DELETE /api/projects/{project_id}/invitations/{invitation_id}
GET    /api/projects/{project_id}/assignee-candidates?q=
```

권한:

- 디렉터리 전체 검색과 invitation API: `project.invitation.create`
- member CRUD: `project.member.manage`
- assignee candidates: `request.edit` 또는 `workflow.edit`
- 파워 사용자의 assignee 검색은 현재 프로젝트 ACTIVE 멤버만 반환하고 디렉터리 전체를 검색하지 않는다.

### 9.3 초대 상태 전이

```text
디렉터리 검색 사용자 미존재       -> PENDING_ACCOUNT
사용자가 첫 SSO 로그인(PENDING)  -> PENDING_APPROVAL
전역 관리자가 ACTIVE 승인         -> READY
프로젝트 관리자가 멤버십 확정      -> COMPLETED
관리자가 취소                      -> CANCELLED
```

- 이미 ACTIVE 사용자인 경우 초대를 만들지 않고 member 추가 화면으로 유도하거나 invitation을 즉시 READY로 만든다. API는 일관성을 위해 READY invitation 생성 후 complete를 허용한다.
- 동일 프로젝트에 이미 멤버면 `409 ALREADY_PROJECT_MEMBER`다.
- 미완료 초대가 있으면 새 초대 대신 기존 초대를 반환하거나 `409 INVITATION_ALREADY_EXISTS`를 반환한다. 한 방식을 선택해 모든 클라이언트와 테스트에서 동일하게 사용한다. 기본은 409다.

### 9.4 프로젝트 관리자 보호

- 프로젝트의 마지막 admin 멤버를 power/general로 낮추거나 삭제하려는 요청은 전역 관리자가 아닌 경우 `409 LAST_PROJECT_ADMIN_PROTECTED`로 거부한다.
- 전역 관리자는 복구 목적상 마지막 project admin을 변경할 수 있다.
- 사용자를 프로젝트에서 제거하기 전에 미완료 담당 업무 수를 계산한다.
- 미완료 담당 업무가 있으면 `409 USER_HAS_OPEN_WORK_ITEMS`와 개수를 반환하고 먼저 재배정하도록 한다.

### 9.5 담당자 저장 API 변경

- 의뢰 생성·수정, workflow step 수정, work item 배정 payload는 `owner_user_id`를 받는다.
- 서버는 ACTIVE + 현재 프로젝트 membership을 검증한다.
- 저장 시 display name snapshot은 서버가 users에서 가져온다.
- 클라이언트가 owner 문자열도 보내더라도 무시하거나 계약에서 제거한다.
- work start/progress/complete의 수행자 이름은 principal에서 생성한다.

### 9.6 단계 4 종료 조건

- 검색 권한, 최소 글자, 결과 제한, 비재직자 제외 테스트 통과
- 초대 전체 상태 전이와 중복 방지 테스트 통과
- 프로젝트 간 멤버 관리 우회 거부
- 미승인·비멤버 담당자 배정 거부
- 표시 이름이 같은 두 사용자가 있어도 user_id로 정확히 실행 권한 판정

## 10. 단계 5 — 메뉴 정책 API

### 10.1 조회 API

```text
GET /api/navigation/menu-policy
```

모든 ACTIVE 사용자가 호출할 수 있다. 응답은 활성 version, 메뉴 metadata, 역할별 visible 값을 포함한다. 클라이언트가 임의 permission을 만들 수 없도록 required_permission은 서버 seed 정의에서 반환한다.

```json
{
  "version": 3,
  "menus": [
    {
      "id": "intake",
      "label": "의뢰 접수",
      "required_permission": "request.create",
      "context_kind": "project",
      "sequence_no": 30,
      "is_policy_editable": true,
      "visibility": {"general": false, "power": true, "admin": true}
    }
  ]
}
```

### 10.2 관리 API

```text
PUT  /api/admin/menu-policy
GET  /api/admin/menu-policy/versions
GET  /api/admin/menu-policy/versions/{version}
POST /api/admin/menu-policy/versions/{version}/restore
```

저장 body:

```json
{
  "expected_version": 3,
  "change_note": "일반 사용자 데이터 등록 메뉴 비노출",
  "visibility": {
    "general": {"portfolio": true, "dashboard": true, "data": false},
    "power": {"portfolio": true, "dashboard": true, "data": true},
    "admin": {"portfolio": true, "dashboard": true, "data": true}
  }
}
```

검증 순서:

1. `system.menu_policy.manage` 확인
2. expected_version과 현재 version 비교
3. 등록되지 않은 role/menu ID 거부
4. `is_policy_editable=false` 메뉴 변경 거부
5. role permission이 없는 메뉴를 true로 저장하려 하면 `422 MENU_PERMISSION_MISMATCH`
6. 모든 현재 정책 행을 포함하는 완전한 snapshot으로 정규화
7. transaction에서 version 증가, current upsert, version snapshot, audit 기록

복원은 선택 버전의 snapshot으로 `현재 version + 1`을 생성하고 `source_version`을 기록한다.

### 10.3 캐시와 즉시 반영

- 서버 메뉴 정책은 크기가 작으므로 DB를 기준으로 하고 version 기반 짧은 메모리 캐시를 허용한다.
- 저장 성공 시 해당 프로세스 캐시를 무효화한다.
- 다중 서버 배포에서 캐시 TTL은 최대 30초로 제한하거나 매 `/menu-policy` 호출에서 state version만 확인한다.
- 프런트는 로그인, 프로젝트 변경, 창 focus, 정책 저장 성공 때 정책을 재조회한다.
- 정책 조회 실패 시 마지막 검증 정책을 사용할 수 있으나 변경 메뉴를 새로 허용하지 않는다. 캐시가 없으면 읽기 기본 메뉴만 표시한다.

### 10.4 단계 5 종료 조건

- 전역 관리자 외 저장·복원 403
- stale version 409
- permission 없는 셀 true 저장 422
- 잠긴 메뉴 변경 409
- 저장과 복원 시 version·audit 생성
- 조회 실패 시 프런트 fail-closed 동작 검증

## 11. 단계 6 — React 인증·권한·좌측 메뉴

### 11.1 타입 변경

기존 `UserRole = 'viewer' | 'editor' | 'admin'`을 런타임 권한 모델에서 제거하고 다음 타입을 사용한다.

```ts
export type AccountStatus = 'PENDING' | 'ACTIVE' | 'SUSPENDED'
export type ProjectRole = 'general' | 'power' | 'admin'
export type Permission = /* 계획서의 문자열 union */
export type ProjectMembership = { project_id: string; role: ProjectRole }
export type AuthUser = {
  id: string
  username: string
  display_name: string
  employee_id: string | null
  account_status: AccountStatus
  is_global_admin: boolean
  memberships: ProjectMembership[]
  company_permissions: Permission[]
}
```

sessionStorage에는 최소 사용자 표시 캐시만 저장한다. 권한의 신뢰 원본은 앱 시작 시 `/api/auth/me` 응답이다.

### 11.2 AuthProvider

- 앱 시작 시 `/auth/status`와 필요한 경우 `/auth/me`를 병렬화 가능한 범위에서 호출한다.
- ACTIVE 확인 후 health, projects, workflows, menu policy를 독립 promise로 시작해 불필요한 waterfall을 줄인다.
- PENDING이면 기존 데이터 bootstrap을 시작하지 않고 `ApprovalPendingScreen`을 렌더링한다.
- SUSPENDED/401/403 인증 이벤트는 사용자·정책 캐시를 지우고 로그인 또는 차단 화면으로 이동한다.
- `refreshAccess()`는 `/me`와 `/navigation/menu-policy`를 갱신한다.
- project memberships Map과 파생 permission Set은 `useMemo`로 만들고 primitive version/user id에 의존한다.

### 11.3 순수 권한 함수

```ts
effectiveRole(user, projectId): ProjectRole | null
policyRole(user, projectId): ProjectRole
permissionsFor(user, projectId): ReadonlySet<Permission>
hasPermission(user, permission, projectId?): boolean
isMenuVisible(menu, user, policy, projectId?): boolean
firstAllowedWorkspacePage(...): WorkspacePage
```

- 함수는 React state를 직접 읽지 않는 순수 함수로 작성하고 단위 테스트한다.
- 전역 관리자는 모든 permission을 가진다.
- 프로젝트 role이 null이어도 전사 읽기 permission은 유지한다.
- `policyRole`은 멤버십이 없으면 `general`을 반환하지만, 이것이 프로젝트 변경 permission을 부여하지는 않는다.
- 메뉴 policy true만으로 permission false를 뒤집을 수 없다.

### 11.4 메뉴 registry와 렌더링

현재 App.tsx의 nav button을 registry 기반으로 옮긴다.

```ts
type MenuRegistryItem = {
  id: MenuId
  page: WorkspacePage
  label: string
  icon: ComponentType
  requiredPermission: Permission
  contextKind: 'company' | 'project' | 'system'
}
```

- registry의 `id`, permission, context는 서버 `menu_definitions`과 테스트로 일치시킨다.
- 서버가 알려지지 않은 menu ID를 반환하면 무시하고 오류 telemetry를 남긴다.
- DB에서 React component나 임의 URL을 받아 렌더링하지 않는다.
- 표시 메뉴는 `filter` 후 `sequence_no`로 정렬한다.
- 현재 페이지가 정책/역할 변경으로 사라지면 편집 중 변경을 취소하고 `firstAllowedWorkspacePage()`로 이동한다.
- 접근 불가능한 page를 state에 직접 주입해도 해당 component를 렌더링하지 않는 page guard를 둔다.

### 11.5 기존 편집·실행 가드 교체

- `canEdit = role === editor || admin` 같은 조건을 모두 검색한다.
- 기능별로 `can('dashboard.edit')`, `can('workflow.edit')`, `can('work.execute_assigned')`를 사용한다.
- 업무 실행 버튼은 permission과 `owner_user_id === authUser.id`를 함께 사용한다.
- 서버 403을 버튼 비활성화만으로 삼키지 말고 사용자에게 권한이 변경되었음을 알리고 access state를 갱신한다.
- 프로젝트 전환 시 현재 edit draft를 취소하고 선택 프로젝트 permission을 다시 계산한다.

### 11.6 관리자 화면

#### 사용자·프로젝트 권한

전역 관리자 탭:

- 승인 대기
- 전체 사용자
- 계정 상태
- 전역 관리자 지정
- 프로젝트 멤버십 조회

프로젝트 관리자 탭:

- 현재 프로젝트 멤버
- 역할 변경·제거
- 임직원 검색·초대
- 초대 상태

Power 사용자의 담당자 선택:

- 현재 프로젝트의 ACTIVE member만 검색
- 결과에 이름, 임직원 ID, 부서, 프로젝트 역할 표시
- 자유 텍스트 owner 입력 제거

#### 권한 및 메뉴 정책

- 행=메뉴, 열=일반/파워/프로젝트 관리자
- permission이 없는 셀과 `is_policy_editable=false` 셀은 disabled + 사유 tooltip
- dirty 상태, 저장 취소, 변경 요약, expected version을 제공
- 409 stale 응답이면 서버 최신 정책을 새로 받고 사용자 변경을 자동 덮어쓰지 않는다.
- 버전 목록과 선택 버전 비교, 복원 확인 대화상자를 제공한다.
- 역할 권한표는 읽기 전용으로 같은 화면에 표시해 메뉴 설정과 실제 permission 차이를 설명한다.

### 11.7 접근성·사용성

- 숨김 메뉴는 DOM에 렌더링하지 않는다.
- disabled 설정 셀은 이유를 텍스트 또는 `aria-describedby`로 제공한다.
- 메뉴 정책 표는 키보드로 이동·토글할 수 있다.
- 승인·역할·복원과 같은 중요 변경에는 대상과 결과를 명시한 확인창을 사용한다.
- 역할은 `GENERAL/POWER/ADMIN` 영문 대신 `일반 사용자/파워 사용자/관리자/전역 관리자`로 표시한다.

### 11.8 단계 6 종료 조건

- App.tsx의 하드코딩 역할 조건 제거
- 선택 프로젝트별 메뉴 즉시 변경
- 메뉴 정책 저장 후 nav 즉시 반영
- 직접 page state/URL 우회 렌더링 방지
- PENDING/차단 화면과 오류 메시지 동작
- TypeScript build 성공

## 12. 단계 7 — 감사로그

### 12.1 action 이름

기존 이벤트에 다음 action을 추가한다.

```text
OIDC_LOGIN_SUCCEEDED
OIDC_LOGIN_FAILED
ACCOUNT_CREATED_PENDING
ACCOUNT_STATUS_CHANGED
GLOBAL_ADMIN_CHANGED
DIRECTORY_SEARCHED
PROJECT_INVITATION_CREATED
PROJECT_INVITATION_STATUS_CHANGED
PROJECT_MEMBERSHIP_CREATED
PROJECT_MEMBERSHIP_ROLE_CHANGED
PROJECT_MEMBERSHIP_REMOVED
WORK_ITEM_ASSIGNEE_CHANGED
MENU_POLICY_UPDATED
MENU_POLICY_RESTORED
AUTHORIZATION_DENIED
```

### 12.2 detail_json 규칙

허용 필드:

- `target_user_id`
- `target_employee_id`의 마스킹 값
- `project_id`
- `old_status`, `new_status`
- `old_role`, `new_role`
- `policy_version`, `source_version`
- 변경된 menu ID 목록
- `required_permission`
- 디렉터리 검색 결과 수

금지 필드:

- 비밀번호·password hash
- authorization code
- ID/access/refresh token
- client secret·directory token
- 전체 이메일 목록이나 검색 결과 원문
- 업무상 필요하지 않은 개인정보

메뉴 정책/역할 변경 API는 도메인 변경과 audit insert를 같은 DB 트랜잭션에 넣는다. 일반 API middleware의 포괄적 `API_MUTATION` 이벤트는 유지할 수 있지만 구체 action을 대체하지 않는다.

## 13. 단계 8 — 테스트 수행서

### 13.1 백엔드 단위 테스트

새 파일 권장:

- `backend/tests/test_access_policy.py`
- `backend/tests/test_oidc_auth.py`
- `backend/tests/test_directory_memberships.py`
- `backend/tests/test_menu_policy.py`

필수 테스트:

1. 각 ProjectRole의 permission 집합이 계획서 5.2와 정확히 일치한다.
2. ACTIVE 비멤버도 읽기 permission은 갖지만 변경 permission은 없다.
3. global admin은 모든 permission을 갖는다.
4. general은 본인 owner_user_id 작업만 실행한다.
5. 같은 표시 이름 사용자가 있어도 다른 user_id 작업을 실행할 수 없다.
6. PENDING/SUSPENDED account 차단과 ACTIVE 허용.
7. OIDC state/nonce/PKCE/audience/signature/issuer 실패.
8. employee identity 충돌 자동 병합 금지.
9. 디렉터리 최소 2자, limit, timeout, 비재직자 초대 금지.
10. 초대 상태 전이와 중복 초대 방지.
11. 다른 프로젝트 admin의 멤버 변경 거부.
12. 마지막 project/global admin 보호.
13. open work item 보유 사용자 제거 거부.
14. 메뉴 정책 permission mismatch, locked cell, stale version 거부.
15. 메뉴 정책 저장·복원 version과 감사 이벤트.

### 13.2 기존 API 권한 회귀 테스트

기존 `test_security.py`를 새 모델로 변경하되 다음 동작을 유지·확장한다.

- anonymous 401
- 읽기 API ACTIVE 사용자 200
- 파워의 의뢰·workflow·결과 변경 200
- 파워의 dashboard/threshold/variable/member 변경 403
- 프로젝트 admin의 해당 프로젝트 변경 200
- 프로젝트 admin의 다른 프로젝트 변경 403
- global admin의 system API 200
- 일반 사용자의 dashboard command·의뢰 생성 403
- 일반 사용자의 본인 work execution 200, 타인 작업 403

각 주요 기존 mutation endpoint가 permission mapping 표대로 동작하는 parametrized 테스트를 만든다. 하나의 대표 endpoint만 테스트하고 나머지를 추정하지 않는다.

### 13.3 마이그레이션 테스트

- 0006 상태 fixture에서 0007 upgrade
- admin/editor/viewer backfill 결과
- 중복 display name owner가 NULL 유지
- 유일 display name owner가 user_id로 backfill
- 메뉴 seed와 version 1
- downgrade가 필요한 경우 신규 테이블/컬럼만 안전하게 제거하고 기존 사용자·owner 문자열을 보존
- PostgreSQL schema export/restore와 portability 검증

### 13.4 프런트 순수 함수 테스트

프로젝트에 기존 unit test runner가 없다면 Vitest를 추가하거나 Playwright component-independent test 방식을 사용한다. 최소한 다음 순수 함수는 자동 검증한다.

- effective role
- permission derivation
- `permission false + policy true => hidden`
- `permission true + policy false => hidden`
- 선택 프로젝트 변경에 따른 menu 목록
- 허용된 첫 workspace page 계산

### 13.5 Playwright E2E

네 persona를 seed한다.

```text
general-a: project A general, project B 미가입
power-a: project A power, project B general
project-admin-a: project A admin, project B general
global-admin: is_global_admin=true
```

시나리오:

1. 각 persona의 기본 좌측 메뉴 screenshot/role assertion.
2. power-a가 project A와 B를 전환할 때 메뉴가 바뀐다.
3. global-admin이 power의 `data` 메뉴를 숨기면 power-a에서 사라진다.
4. 숨겨진 data page 직접 진입은 렌더링되지 않고 기본 화면으로 이동한다.
5. power가 숨겨진 메뉴의 API를 호출해도 permission이 있으면 API는 허용된다. 메뉴 정책은 권한을 회수하지 않기 때문이다.
6. general에게 `data`를 표시하려고 해도 관리 API가 저장을 거부한다.
7. general-a는 본인 배정 업무를 실행하고 타인 업무 버튼은 비활성화된다.
8. raw API로 타인 업무 실행 요청 시 403.
9. project-admin-a가 project B 멤버 관리 직접 URL/API에 접근하면 403.
10. PENDING 사용자는 승인 대기 화면만 본다.
11. ACTIVE 세션 사용자를 SUSPENDED로 변경한 후 다음 요청에서 차단된다.
12. 정책 이전 버전 복원 후 메뉴가 원복된다.

### 13.6 검증 명령

```powershell
cd E:\simulation_dashboard\backend
..\.venv\Scripts\python.exe -m pytest -q

cd E:\simulation_dashboard\frontend
pnpm run generate:api
pnpm run build
pnpm run test:e2e
```

PostgreSQL 전용 테스트 환경이 구성되어 있으면 migration upgrade와 transfer/restore 테스트를 별도로 실행한다.

### 13.7 교차 플랫폼 CI와 Windows VM 운영 검증

- `.github/workflows`의 기존 검증 흐름을 확장하거나 별도 Ubuntu job을 추가한다.
- Ubuntu job은 Python 3.12, Node.js 20+, pnpm, PostgreSQL service를 사용한다.
- 백엔드 전체 pytest, 빈 DB `alembic upgrade head`, 기존 revision fixture의 0007 upgrade, 프런트 API 생성 결정성 검사와 build를 실행한다.
- shell 명령은 저장소의 `setup.sh`, `start.sh`, PostgreSQL shell script가 POSIX 경로와 LF 줄바꿈에서 동작하는지 확인한다.
- Windows 전용 PowerShell/batch 파일은 유지하되 공통 로직을 Python 모듈 또는 플랫폼 중립 설정에 두어 Linux 구현과 의미가 갈라지지 않게 한다.
- Ubuntu CI는 플랫폼 중립 코드와 PostgreSQL migration 회귀를 검증하는 보조 gate로 유지한다.
- `windows-latest` CI 또는 동등한 Windows 자동 검증에서 백엔드 pytest, OpenAPI 생성, 프런트 build, PowerShell script 구문 검사를 수행한다.
- 실제 운영 Windows VM에서 PostgreSQL 빈 DB와 기존 DB upgrade, PowerShell 시작·readiness·중지·실패 cleanup, 백업·복원, HTTPS OIDC redirect/cookie를 smoke test한다.
- 현재 가정용 Windows PC의 성공만으로 운영 VM 배포 완료를 선언하지 않는다.

## 14. 단계 9 — OpenAPI·문서·운영 배포

### 14.1 OpenAPI

- 신규 schema를 Pydantic model로 선언하고 임의 dict 반환을 최소화한다.
- API 변경 후 `backend/scripts/export_openapi.py` 또는 `pnpm run generate:api`의 기존 흐름을 따른다.
- `frontend/openapi.json`, `frontend/src/generated/openapi.ts`, client wrapper를 갱신한다.
- 수동 타입과 generated 타입이 중복되면 generated 타입을 기준으로 정리한다.

### 14.2 운영 문서

`docs/deployment-security-backup-guide.md`와 `.env.example`에 다음을 추가한다.

- OIDC 환경변수 목록과 redirect URI
- 디렉터리 API 환경변수와 네트워크 허용 조건
- 운영에서 cookie secure 필수
- 첫 전역 관리자 확보 절차
- 마지막 전역 관리자 보호와 비상 복구 CLI
- 메뉴 정책 기본값과 버전 복원
- SSO/디렉터리 장애 시 사용자 메시지와 운영 확인 순서

### 14.3 첫 전역 관리자와 비상 복구

- 기존 admin migration으로 최소 한 명의 global admin을 확보한다.
- 기존 admin이 없는 신규 OIDC 설치를 위해 CLI를 추가한다.

```powershell
python backend\scripts\grant_global_admin.py --employee-id E12345
```

- CLI는 DB에 이미 존재하는 ACTIVE 사용자만 승격한다.
- 사용자가 없으면 먼저 한 번 SSO 로그인해 PENDING 계정을 만든 뒤 운영자가 상태를 ACTIVE로 바꾸는 별도 승인 옵션을 명시적으로 사용한다.
- CLI는 실행 전 대상 정보를 보여주고 확인을 요구하며 감사 이벤트를 생성한다.
- employee ID나 secret을 명령행 history에 불필요하게 노출하지 않도록 사내 기준이 요구되면 환경변수 입력도 지원한다.

### 14.4 배포 순서

1. DB 백업과 복원 가능성 확인
2. 기존 admin 계정 수와 중복 display name owner 목록 사전 점검
3. 애플리케이션 중지 또는 mutation 차단
4. Alembic 0007 적용
5. backfill 검증 보고서 확인
6. 새 백엔드 배포, password 모드 smoke test
7. 새 프런트 배포, 네 persona 메뉴 smoke test
8. OIDC·directory 환경변수 주입
9. OIDC 모드 전환과 PENDING→ACTIVE 승인 smoke test
10. 최소 한 명 global admin과 메뉴 정책 복원 경로 확인
11. 감사로그, 401/403/409 비율 모니터링

롤백 시 신규 코드만 되돌리고 DB를 즉시 downgrade하지 않는다. 사용자 승인·멤버십 데이터가 생긴 뒤 downgrade하면 정보 손실 위험이 있으므로 백업 복원 또는 별도 검토를 거친다.

### 14.5 사내 이전 preflight

플랫폼 중립 CLI를 추가한다.

```text
python backend/scripts/access_migration_preflight.py --profile intranet --format json
```

CLI는 기본적으로 read-only이며 다음을 검사한다.

- 현재 Alembic revision과 요구 head
- OIDC issuer/redirect/cookie 설정의 운영 적합성(비밀값 출력 금지)
- directory adapter 연결 가능 여부와 정규화 DTO 계약
- 중복·누락 employee_id와 issuer/subject 충돌
- 문자열 owner가 남은 request/step/work item과 NULL owner_user_id
- 프로젝트별 ACTIVE admin 존재 여부와 마지막 전역 관리자
- 초대 상태 불일치와 열린 업무가 있는 제거 대상
- menu definition/current policy/version snapshot의 수와 참조 무결성
- PostgreSQL 필수 테이블·컬럼·인덱스·제약과 transfer manifest 포함 여부

출력은 `status`, `code`, `severity`, `entity_type`, `entity_id`, `message`를 가진 JSON 항목 배열과 총계를 포함한다. 오류가 하나라도 있으면 non-zero exit code를 반환한다. 실제 사용자·멤버십·담당자·정책 데이터는 변경하지 않는다.

## 15. 요구사항 추적표

| 계획서 요구사항 | 구현 단계 | 주요 테스트 |
|---|---|---|
| AUTH-01~06 | 단계 2, 3 | OIDC 검증, 상태 차단, password 회귀 |
| RBAC-01~02 | 단계 1, 2 | role permission exact match |
| RBAC-03 | 단계 1, 2, 4, 6 | 본인/타인 업무 실행 API·E2E |
| RBAC-04~07 | 단계 2, 4, 6 | 프로젝트 A/B 변경 권한, system API |
| RBAC-08~10 | 단계 2 | 전체 mutation mapping, 전사 GET, 관리자 실행 override 감사 |
| MENU-01~03 | 단계 5, 6 | visibility 진리표, permission mismatch |
| MENU-04~06 | 단계 5, 7 | version/audit/locked menu/last admin |
| MENU-07~08 | 단계 6 | 정책 변경 redirect, 직접 URL 차단, API permission 독립 판정 |
| DIR-01~02 | 단계 4 | proxy, 최소 글자, limit, timeout |
| DIR-03~05 | 단계 4 | invitation state, member/assignee validation |
| DIR-06 | 단계 7 | directory/invitation audit |
| AUDIT-01~04 | 단계 3, 4, 5, 7 | action/detail/secret exclusion |
| OPS-01 | 단계 3, 4 | IdP/directory 장애 fail-closed |
| OPS-02~03 | 단계 1, 8 | PostgreSQL/DuckDB migration/backfill |
| OPS-04 | 단계 1, 8, 9 | Windows VM 운영 smoke, Windows CI, Ubuntu 교차 플랫폼 CI |
| MOD-01~03 | 단계 2, 3, 4, 6 | provider/facade import 경계, local/intranet profile 계약 테스트 |
| MOD-04~06 | 단계 1, 4, 9 | additive migration, read-only preflight, provider 중립 DTO |

## 16. 최종 인수 체크리스트

Luna는 완료 보고 전에 아래 항목을 하나씩 확인한다.

- [ ] 계획서와 수행서의 역할·permission 표가 코드 상수와 일치한다.
- [ ] 전역 메뉴 정책으로 permission을 확대할 수 없다.
- [ ] 모든 좌측 메뉴가 registry와 서버 정책을 통해 렌더링된다.
- [ ] 전역 관리자가 역할별 메뉴 표시를 변경·복원할 수 있다.
- [ ] 필수 관리자 메뉴는 숨길 수 없다.
- [ ] 모든 변경 API가 프로젝트 범위를 서버에서 해석한다.
- [ ] 일반 사용자의 실행 권한은 owner_user_id로 검사한다.
- [ ] 미승인·중지·비멤버 사용자의 변경 요청이 거부된다.
- [ ] 마지막 전역/프로젝트 관리자 보호가 동작한다.
- [ ] OIDC token과 directory secret이 저장·로그되지 않는다.
- [ ] 역할·승인·초대·메뉴 정책 변경 감사 이벤트가 남는다.
- [ ] 기존 admin/editor/viewer와 owner 데이터 migration 검증이 완료됐다.
- [ ] OpenAPI와 생성 클라이언트가 갱신됐다.
- [ ] 백엔드 전체 테스트가 통과했다.
- [ ] 프런트 빌드가 통과했다.
- [ ] Playwright 네 persona와 정책 변경 시나리오가 통과했다.
- [ ] 운영 환경변수와 첫 전역 관리자 복구 절차가 문서화됐다.
- [ ] 가정용 Windows 개발 테스트와 Ubuntu CI가 통과하고, 운영 Windows VM smoke test가 별도 증거로 통과했다.
- [ ] local profile이 사내 연결 없이 실행되고 intranet profile은 provider 설정 누락/장애 시 fail-closed다.
- [ ] 사내 이전 preflight가 read-only JSON 보고서를 만들고 충돌 시 non-zero로 종료한다.
