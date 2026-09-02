# 권한·메뉴 정책 기능 사양서

## 1. 목적과 범위

이 사양서는 Analysis Canvas의 인증, 계정 승인, 프로젝트 역할, permission, 좌측 메뉴 정책, 사내 임직원 검색, 담당자 배정과 감사 기능의 외부 동작 계약을 정의한다. 구현 파일 구조보다 사용자가 관찰할 수 있는 동작과 운영자가 지켜야 할 불변 조건을 우선한다.

포함 범위는 다음과 같다.

- 사내 OIDC 로그인과 계정 생명주기
- 프로젝트별 3단계 역할과 별도 전역 관리자
- API permission 및 프로젝트·owner 범위 검사
- 역할별 좌측 메뉴 표시 정책, 버전과 복원
- 사내 임직원 검색, 초대, 멤버십과 담당자 배정
- 감사 이벤트와 운영 fail-closed 규칙
- Windows VM 배포·사내 이전 계약

조직도 동기화, IdP 자체 계정 관리, 사용자 정의 역할 생성, 세분화된 데이터 행별 보안 정책 편집기는 v1 범위가 아니다.

## 2. 용어와 행위자

| 용어 | 정의 |
|---|---|
| principal | 현재 요청에서 인증되고 DB 상태가 재검증된 사용자 |
| account status | `PENDING`, `ACTIVE`, `SUSPENDED` 중 하나인 전역 계정 상태 |
| project role | 한 프로젝트에서의 `general`, `power`, `admin` 역할 |
| global admin | `is_global_admin=true`인 시스템 운영자. 프로젝트 역할과 별도다. |
| permission | 서버가 API 동작을 허용할 때 검사하는 불변 기능 키 |
| menu policy | 역할별 좌측 메뉴 표시 여부. permission을 추가하거나 제거하지 않는다. |
| policy role | 선택 프로젝트의 membership role. 비멤버 읽기 사용자는 `general` 표시 정책을 적용한다. |
| owner | 의뢰 또는 업무에 배정된 canonical `owner_user_id` 사용자 |
| context | 회사, 선택 프로젝트 또는 시스템 관리 화면이라는 메뉴·API 범위 |

## 3. 계정과 인증 사양

### 3.1 계정 상태

| 상태 | 허용 동작 |
|---|---|
| `PENDING` | 본인 인증 상태 확인, 승인 대기 화면, 로그아웃만 허용 |
| `ACTIVE` | permission과 범위에 따라 일반 기능 사용 |
| `SUSPENDED` | 다음 요청부터 일반 API 차단, 기존 세션 만료 처리 |

모든 보호 API는 요청 시점의 DB 계정 상태를 확인한다. 세션 발급 시점의 상태만 신뢰해서는 안 된다.

### 3.2 OIDC 운영 인증

- `GET /api/auth/oidc/start`는 Authorization Code + S256 PKCE 흐름을 시작한다.
- callback은 state, nonce, issuer, JWKS 서명, audience, 만료와 발급 시각을 각각 검증한다.
- `(oidc_issuer, oidc_subject)`가 외부 사용자 유일 키다.
- 이메일, 사번 또는 표시 이름이 같다는 이유로 기존 계정에 자동 연결하지 않는다.
- 최초 유효 로그인은 `PENDING` 사용자를 만들고 데이터 화면 대신 승인 대기로 이동한다.
- identity 충돌은 409로 실패하며 기존 계정을 변경하지 않는다.
- OIDC 토큰은 권한 토큰으로 브라우저나 DB에 보관하지 않는다.
- OIDC·디렉터리 장애 시 로컬 관리자나 합성 permission으로 우회하지 않는다.

### 3.3 세션

- 운영 애플리케이션 세션 쿠키는 HttpOnly, Secure, SameSite=Strict다.
- IdP 왕복용 임시 쿠키는 HttpOnly, Secure, SameSite=Lax이며 짧은 수명과 서명을 갖는다.
- 프런트엔드는 `/api/auth/me`를 계정·permission·멤버십의 권위 소스로 사용한다.
- sessionStorage의 사용자 표시 캐시는 권한 판정에 사용하지 않는다.

### 3.4 개발·테스트 인증

- `AUTH_MODE=password`는 로컬 사용자 로그인 회귀 테스트용이다.
- `AUTH_MODE=disabled`는 명시적 로컬 개발 모드다.
- `DEPLOYMENT_PROFILE=windows-vm-intranet`에서는 두 모드를 허용하지 않고 `oidc`만 허용한다.

## 4. 역할과 permission 사양

### 4.1 기본 역할

| 역할 | 제품 의미 | 주요 동작 |
|---|---|---|
| 일반 사용자 (`general`) | 조회와 본인 업무 실행 | 운영·프로젝트 조회, 보고서 내보내기, 본인 배정 업무 실행 |
| 파워 사용자 (`power`) | 의뢰·업무 중심 편집 | general 기능 + 의뢰 생성·편집, 워크플로 편집, 결과 등록·검토 |
| 프로젝트 관리자 (`admin`) | 해당 프로젝트 구성 관리 | power 기능 + 대시보드·레이아웃·기준·변수·멤버·초대 관리 |
| 전역 관리자 | 시스템 운영·복구 | 모든 프로젝트 기능 + 사용자 승인, 시스템 카탈로그, 메뉴 정책, 전체 감사로그, 명시적 업무 override |

프로젝트 관리자는 다른 프로젝트의 변경 권한을 갖지 않는다. 전역 관리자는 별도의 네 번째 프로젝트 역할이 아니라 전역 플래그와 permission 합성으로 구현한다.

### 4.2 permission 매트릭스

| Permission | 일반 | 파워 | 프로젝트 관리자 | 전역 관리자 |
|---|:---:|:---:|:---:|:---:|
| `company.dashboard.view` | O | O | O | O |
| `project.data.view` | O | O | O | O |
| `report.export` | O | O | O | O |
| `work.execute_assigned` | O | O | O | O |
| `request.create` |  | O | O | O |
| `request.edit` |  | O | O | O |
| `workflow.edit` |  | O | O | O |
| `result.import` |  | O | O | O |
| `result.review` |  | O | O | O |
| `dashboard.edit` |  |  | O | O |
| `project.layout.edit` |  |  | O | O |
| `project.threshold.manage` |  |  | O | O |
| `project.variable.manage` |  |  | O | O |
| `project.member.manage` |  |  | O | O |
| `project.invitation.create` |  |  | O | O |
| `work.execute_any` |  |  |  | O |
| `system.catalog.manage` |  |  |  | O |
| `system.user.approve` |  |  |  | O |
| `system.menu_policy.manage` |  |  |  | O |
| `audit.view` |  |  |  | O |

### 4.3 권한 계산

1. account status가 `ACTIVE`인지 확인한다.
2. 전역 관리자면 global permission 집합을 사용한다.
3. 프로젝트 변경이면 DB에서 해당 프로젝트 membership을 찾는다.
4. role permission 집합에 요구 permission이 있는지 확인한다.
5. 경로 또는 리소스 관계로 계산한 프로젝트가 검사 프로젝트와 같은지 확인한다.
6. 업무 실행이면 owner 범위를 추가로 확인한다.

승인된 비멤버 사용자는 읽기 전용 company permission을 사용할 수 있다. `policy_role=general`은 메뉴 표시 기준일 뿐, 프로젝트 변경 permission을 부여하지 않는다.

### 4.4 리소스 범위

- request는 `analysis_requests.project_id`로 범위를 결정한다.
- load case는 request를 거쳐 project를 결정한다.
- run, result, review는 load case와 request를 거쳐 project를 결정한다.
- workflow step, request step과 work item은 request를 거쳐 project를 결정한다.
- dashboard와 프로젝트 workspace layout은 저장된 project 관계로 범위를 결정한다.
- body에 전달된 project ID와 actor 표시 문자열은 권한 근거로 사용하지 않는다.

## 5. 좌측 메뉴 사양

### 5.1 최종 표시식

일반 사용자의 메뉴는 다음 조건이 모두 참일 때 표시한다.

```text
visible = account_status == ACTIVE
          AND has(required_permission)
          AND context_matches
          AND role_menu_policy == true
```

전역 관리자는 `role_menu_policy`만 우회한다. `ACTIVE`, permission과 context 조건은 그대로 검사한다. 메뉴가 숨겨졌더라도 사용자가 해당 permission을 보유하면 API는 허용될 수 있다. 반대로 정책을 켜도 permission이 없으면 메뉴와 API가 생기지 않는다.

### 5.2 메뉴 registry와 기본 표시

| ID | 레이블 | 요구 permission | 컨텍스트 | 일반 | 파워 | 프로젝트 관리자 | 전역 관리자 |
|---|---|---|---|:---:|:---:|:---:|:---:|
| `portfolio` | 운영 대시보드 | `company.dashboard.view` | company | O | O | O | O |
| `dashboard` | 해석 의뢰 현황 | `project.data.view` | project | O | O | O | O |
| `intake` | 의뢰 접수 | `request.create` | project |  | O | O | O |
| `workbench` | 해석 작업 실행 | `project.data.view` | project | O | O | O | O |
| `data` | 해석 데이터 등록 | `result.import` | project |  | O | O | O |
| `workbench_admin` | 작업 유형 관리 | `system.catalog.manage` | system |  |  |  | O |
| `variables` | 변수 카탈로그 | `project.variable.manage` | project |  |  | O | O |
| `templates` | 자동화 템플릿 | `system.catalog.manage` | system |  |  |  | O |
| `schemas` | 폴더 스키마 | `system.catalog.manage` | system |  |  |  | O |
| `examples` | 예제 갤러리 | `project.data.view` | company | O | O | O | O |
| `help` | 도움말 | `company.dashboard.view` | company | O | O | O | O |
| `access_admin` | 사용자·프로젝트 권한 | `project.member.manage` | project |  |  | O | O |
| `menu_policy_admin` | 권한 및 메뉴 정책 | `system.menu_policy.manage` | system |  |  |  | 항상 O |
| `audit_admin` | 감사로그 | `audit.view` | system |  |  |  | 항상 O |

`menu_policy_admin`과 `audit_admin`은 전역 관리자 복구 경로이므로 정책 편집 대상이 아니다.

### 5.3 정책 변경과 복원

- 정책은 모든 역할·메뉴 셀의 전체 snapshot으로 저장한다.
- 저장 요청은 현재 `expected_version`과 2자 이상의 `change_note`를 포함한다.
- 버전이 다르면 409를 반환하고 아무 값도 변경하지 않는다.
- 저장 성공 시 현재 정책, snapshot, version과 감사 이벤트를 한 트랜잭션에서 기록한다.
- 과거 버전 복원은 기존 버전을 덮어쓰지 않고 새 버전을 생성한다.
- permission이 없는 역할에 메뉴를 켜려는 저장은 422로 거부한다.

### 5.4 화면 진입과 갱신

- 화면 표시와 직접 URL·내부 page state 진입에 같은 guard를 적용한다.
- 허용되지 않는 현재 화면은 사용 가능한 첫 안전 메뉴로 이동한다.
- 프로젝트 전환, 창 포커스 복귀, 정책 저장·복원과 403 응답 후 `/me`와 메뉴 정책을 갱신한다.
- 정책 API 실패 시 일반 메뉴는 fail-closed로 숨긴다. 전역 관리자의 복구 메뉴는 유지한다.

## 6. 디렉터리, 초대와 멤버십 사양

### 6.1 임직원 검색

- 프로젝트 관리 permission을 가진 사용자가 2~80자의 정규화된 검색어로 요청한다.
- 기본 결과 제한은 운영 설정을 따르며 서버 최대값을 넘을 수 없다.
- 응답은 employee ID, 표시 이름, 이메일, 부서, 직급, 계정·멤버십에 필요한 최소 상태만 포함한다.
- 원시 검색어, 전체 결과와 디렉터리 token은 감사로그에 남기지 않는다. 검색 결과 건수만 기록한다.
- 운영 adapter의 timeout 또는 오류는 503이며 빈 권한 사용자나 로컬 권한을 만들지 않는다.

### 6.2 초대 상태 전이

```text
PENDING_ACCOUNT -> PENDING_APPROVAL -> READY -> COMPLETED
       |                  |              |
       +------------------+--------------+-> CANCELLED
```

- 디렉터리에 있지만 로컬 계정이 없으면 `PENDING_ACCOUNT`다.
- 로컬 계정이 `PENDING`이면 `PENDING_APPROVAL`이다.
- 계정이 `ACTIVE`이면 `READY`다.
- `READY` 초대만 완료해 멤버십을 만들 수 있다.
- `COMPLETED` 또는 `CANCELLED` 초대는 재실행할 수 없다.
- 동일 프로젝트·사용자 중복 멤버십과 유효 중복 초대를 차단한다.

### 6.3 멤버십 보호

- 한 사용자는 한 프로젝트에서 하나의 role만 가진다.
- 멤버 생성·역할 변경·삭제는 해당 프로젝트의 `project.member.manage`를 요구한다.
- 프로젝트의 마지막 admin 강등·삭제는 전역 관리자가 아닌 경우 409다.
- 열린 업무가 있는 담당 멤버를 삭제할 때는 재배정 또는 명시적 보호 규칙을 적용한다.
- 다른 프로젝트 admin이 project ID나 user ID를 변조해 접근하면 403 또는 404로 실패한다.

## 7. 담당자와 업무 실행 사양

- 의뢰 생성 body는 canonical `owner_user_id`를 사용한다.
- 서버는 해당 사용자가 `ACTIVE`이며 현재 프로젝트 멤버인지 검증하고 표시 이름을 계산한다.
- 생성된 request, step과 work item에 동일한 owner ID를 전파한다.
- 의뢰와 work item은 관리 permission이 있는 사용자가 재배정할 수 있다.
- `owner_user_id`가 없는 legacy 업무 실행은 409 `OWNER_REASSIGNMENT_REQUIRED`다.
- owner가 아닌 사용자의 시작·진행·완료·batch dispatch는 403이다.
- 전역 관리자는 owner가 존재하는 업무에 한해 `work.execute_any`로 override할 수 있다.
- override는 item, project와 기존 owner를 포함하는 별도 감사 이벤트를 남긴다.
- 전역 관리자라도 owner가 NULL인 업무는 override하지 못한다.

## 8. 주요 API 계약

### 8.1 인증과 사용자

| Method | 경로 | 목적 |
|---|---|---|
| GET | `/api/auth/status` | 인증 모드와 상태 확인 |
| GET | `/api/auth/oidc/start` | OIDC 로그인 시작 |
| GET | `/api/auth/oidc/callback` | OIDC callback 검증·세션 생성 |
| POST | `/api/auth/login` | password 모드 로그인 |
| POST | `/api/auth/logout` | 세션 종료 |
| GET | `/api/auth/me` | 권위 있는 현재 사용자·permission·membership 조회 |
| GET | `/api/admin/users` | 전역 사용자 검색·상태 필터·cursor 페이지 조회 |
| PATCH | `/api/admin/users/{user_id}/status` | 계정 승인·정지·복구 |
| PATCH | `/api/admin/users/{user_id}/global-admin` | 전역 관리자 지정·해제 |
| GET | `/api/audit-events` | 감사로그 조회 |

상태 및 전역 관리자 변경 body는 `expected_updated_at`과 공백 제거 후 2자 이상인 `reason`을 요구한다.

### 8.2 프로젝트 접근 관리

| Method | 경로 | 목적 |
|---|---|---|
| GET/POST | `/api/projects/{project_id}/members` | 멤버 조회·추가 |
| PATCH/DELETE | `/api/projects/{project_id}/members/{user_id}` | 역할 변경·멤버 제거 |
| GET | `/api/projects/{project_id}/directory/employees` | 사내 임직원 검색 |
| GET/POST | `/api/projects/{project_id}/invitations` | 초대 조회·생성 |
| POST | `/api/projects/{project_id}/invitations/{invitation_id}/complete` | READY 초대 완료 |
| DELETE | `/api/projects/{project_id}/invitations/{invitation_id}` | 초대 취소 |
| GET | `/api/projects/{project_id}/assignee-candidates` | ACTIVE 프로젝트 멤버 후보 조회 |
| PATCH | `/api/requests/{request_id}/assignee` | 의뢰 담당자 변경 |
| PATCH | `/api/workbench/work-items/{item_id}/assignee` | 작업 항목 담당자 변경 |

### 8.3 메뉴 정책

| Method | 경로 | 목적 |
|---|---|---|
| GET | `/api/navigation/menu-policy` | 현재 사용자에게 적용할 registry·정책 조회 |
| PUT | `/api/admin/menu-policy` | 전체 snapshot 저장과 새 버전 생성 |
| GET | `/api/admin/menu-policy/versions` | 버전 목록 조회 |
| GET | `/api/admin/menu-policy/versions/{version}` | 버전 snapshot 조회 |
| POST | `/api/admin/menu-policy/versions/{version}/restore` | 과거 snapshot을 새 버전으로 복원 |

### 8.4 업무 실행

| Method | 경로 | 범위 |
|---|---|---|
| POST | `/api/workbench/work-items/{item_id}/start` | assigned owner 또는 감사되는 global override |
| PATCH | `/api/workbench/work-items/{item_id}/progress` | assigned owner 또는 감사되는 global override |
| POST | `/api/workbench/work-items/{item_id}/complete` | assigned owner 또는 감사되는 global override |
| POST | `/api/workbench/work-items/{item_id}/batch-dispatch` | assigned owner 또는 감사되는 global override |

그 밖의 의뢰·워크플로·결과·대시보드·레이아웃·기준·변수·카탈로그 변경 API도 4장의 permission과 리소스 범위 계약을 따른다.

## 9. 오류 계약

| HTTP | 대표 코드/상황 | 의미 |
|---|---|---|
| 401 | `AUTHENTICATION_REQUIRED`, 세션 무효 | 로그인 필요 또는 세션 만료 |
| 403 | `ACCOUNT_PENDING`, `ACCOUNT_SUSPENDED`, `PERMISSION_DENIED`, owner 불일치 | 인증은 확인됐으나 현재 상태·권한·범위로 금지 |
| 404 | 리소스 없음 | 존재하지 않거나 노출하지 않는 리소스 |
| 409 | `IDENTITY_CONFLICT`, `LAST_GLOBAL_ADMIN_PROTECTED`, `LAST_PROJECT_ADMIN_PROTECTED`, `OWNER_REASSIGNMENT_REQUIRED`, version 충돌, 완료된 초대 재실행 | 현재 상태와 충돌해 변경 불가 |
| 422 | 잘못된 role·정책, 비활성/비멤버 담당자, 빈 reason | 요청 계약 또는 도메인 검증 실패 |
| 503 | `DIRECTORY_UNAVAILABLE`, OIDC/JWKS 장애 | 외부 권위 시스템 장애로 fail-closed |

오류 응답은 안정된 code와 사용자 표시용 message를 제공하며 token, 내부 SQL과 과도한 개인정보를 포함하지 않는다.

## 10. 감사 사양

감사 대상은 다음을 포함한다.

- OIDC·password 로그인 성공과 실패
- PENDING 승인 대기 차단과 SUSPENDED 계정 차단
- 계정 상태 및 전역 관리자 변경
- 프로젝트 멤버 추가·역할 변경·삭제
- 초대 생성·완료·취소
- 의뢰·작업 담당자 변경
- 메뉴 정책 저장·복원
- 전역 관리자의 타인 업무 실행 override
- 권한이 거부된 보호 API 접근

각 이벤트는 action, actor, target, project, request ID, result, 시각과 허용된 old/new 값을 갖는다. 권한 변경과 감사 저장은 동일 트랜잭션이다. 비밀번호, 쿠키, OIDC code/token, 디렉터리 token, 원시 검색어·결과는 기록 금지다.

## 11. 데이터 모델 계약

```text
users
  ├─< project_memberships >─ projects
  ├─< project_invitations >─ projects
  └─< owner_user_id: analysis_requests / request_steps / request_work_items

menu_definitions
  └─< role_menu_policies
menu_policy_state
  └─< menu_policy_versions (full snapshot)

projects
  └─< project_workspace_layouts
       └─< project_workspace_layout_versions
```

핵심 제약은 다음과 같다.

- account status는 `PENDING|ACTIVE|SUSPENDED`만 허용하고 NULL이 아니다.
- project role은 `general|power|admin`만 허용한다.
- `(project_id, user_id)` 멤버십은 유일하다.
- OIDC issuer·subject 조합은 유일하다.
- 메뉴 ID와 정책 role·menu 조합은 유일하다.
- 현재 메뉴 정책은 하나이며 version이 단조 증가한다.
- owner ID backfill은 유일하게 식별 가능한 기존 데이터에만 적용한다.

## 12. 모듈 독립성과 사내 이전

권한 기능은 모듈형 모놀리스의 access-control 모듈로 배포한다. 별도 프로세스 격리가 아니라 코드·데이터 계약의 격리다.

- 도메인 기능은 공개 facade의 permission·scope guard만 호출한다.
- OIDC와 디렉터리는 adapter 뒤에 있어 사내 provider 변경을 국소화한다.
- UI는 menu registry와 `/me` 계약을 사용하며 backend 내부 테이블을 알지 않는다.
- permission 이름과 공개 API는 안정된 통합 계약으로 취급한다.
- 데이터 이전은 Alembic revision, canonical schema, transfer manifest와 preflight로 검증한다.
- 권한 모듈 장애 시 보안을 끄는 fallback은 제공하지 않는다.

이 구조는 “격리 실행”처럼 별도 서비스 프로세스를 운영하는 방식은 아니다. 현재 규모에서는 트랜잭션 일관성과 배포 단순성을 유지하면서 향후 서비스 분리가 가능하도록 경계를 고정한 모듈형 모놀리스다.

## 13. Windows VM 운영 계약

운영 환경은 다음 조건을 만족해야 한다.

```dotenv
DEPLOYMENT_PROFILE=windows-vm-intranet
ANALYSIS_DB_BACKEND=postgresql
AUTH_MODE=oidc
AUTH_COOKIE_SECURE=true
DIRECTORY_MODE=http
OIDC_ISSUER_URL=https://...
OIDC_REDIRECT_URI=https://.../api/auth/oidc/callback
DIRECTORY_API_BASE_URL=https://...
```

- Python 3.12, Node.js 20 이상, PostgreSQL과 HTTPS reverse proxy를 사용한다.
- 애플리케이션 시작 전에 배포 프로필, PostgreSQL 연결, Alembic head와 필수 테이블을 검사한다.
- 실제 secret은 `.env.example`, Git, 로그와 명령행 인자에 저장하지 않는다.
- 최초 전역 관리자는 `backend\scripts\approve_oidc_global_admin.py`로 명시적으로 승인한다.
- 이전 전 `backend\scripts\access_migration_preflight.py` 결과가 `ready` 또는 승인된 `ready_with_warnings`여야 한다.
- 백업, 이전, readiness, OIDC와 디렉터리 smoke test가 완료되기 전 운영 트래픽을 전환하지 않는다.

## 14. 수용 기준

기능 완료는 다음 기준을 모두 만족할 때 인정한다.

1. OIDC의 정상·개별 부정 검증과 PENDING·SUSPENDED 동작이 테스트된다.
2. 3개 프로젝트 역할과 전역 관리자 permission 집합이 정확히 일치한다.
3. 모든 변경 API가 명시적 permission과 서버 해석 프로젝트 범위를 사용한다.
4. 프로젝트 간 ID·body 변조가 권한을 우회하지 못한다.
5. 일반 사용자는 본인 owner 업무만 실행하며 NULL owner는 실행되지 않는다.
6. 전역 업무 override에는 별도 permission과 감사 이벤트가 있다.
7. 14개 메뉴의 registry와 프런트 registry가 일치하고 strict AND truth table을 만족한다.
8. 전역 관리자가 역할별 메뉴 정책을 저장·버전 조회·복원할 수 있다.
9. 숨겨진 직접 화면은 차단되지만 메뉴 정책이 API permission을 변경하지 않는다.
10. 디렉터리 장애가 권한 합성 없이 503으로 실패한다.
11. 초대 상태 전이, 중복 방지와 마지막 관리자 보호가 동작한다.
12. 관리 변경과 감사 이벤트가 원자적으로 저장된다.
13. DuckDB fresh·legacy와 PostgreSQL empty·0006→0007 이전 의미가 일치한다.
14. OpenAPI와 생성 클라이언트가 일치하고 빌드가 성공한다.
15. general, power, project admin, global admin 4개 persona E2E가 통과한다.
16. 실제 Windows VM에서 PostgreSQL, HTTPS OIDC, 디렉터리와 재시작 smoke test가 통과한다.

1~15는 구현·자동화 검증 기준이고, 16은 사내 인프라에서 수행하는 최종 운영 release gate다.

## 15. 요구사항 추적

| 요구사항 | 사양 절 | 주요 검증 영역 |
|---|---|---|
| AUTH-01~06 | 3장 | OIDC, 세션, 계정 상태, password/disabled 회귀 |
| RBAC-01~10 | 4장, 7장 | permission exact set, 프로젝트 변조, owner·override |
| MENU-01~08 | 5장 | truth table, registry, version·복원, 직접 진입 |
| DIR-01~06 | 6장 | adapter, 초대 상태, 멤버십·담당자 후보 |
| AUDIT-01~04 | 10장 | 이벤트 내용, secret denylist, 트랜잭션 rollback |
| OPS-01~04 | 11~14장 | fail-closed, DB 이전, Windows VM release gate |
| MOD-01~06 | 12장 | facade 의존, provider 교체, 데이터·배포 독립성 |
