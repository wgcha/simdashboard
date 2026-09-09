# 프론트 URL 라우팅·모듈화 완료 기록

> 부분 대체(2026-09-07): 1·2·5장의 화면별 진입/선택 문맥 deep-link 후속 범위는 [의뢰 중심 작업공간 GUI](request-centric-workspace-ux.md)의 기준으로 확장한다. 기존 canonical path·단일 registry·권한 fallback·편집 blocker는 유지한다. 아래 내용은 당시 완료 기록이다. 실제 App 연결 여부는 현재 코드를 확인한다.

- 원래 계획 문서명 복원: `frontend-url-routing-and-modularization-plan.md`
- 상태: 구현·회귀 검증 완료
- 기준일: 2026-08-16
- 범위: Simulation Dashboard 프론트엔드의 메뉴 URL, 브라우저 이력, 권한 전환, 기능 모듈 경계

## 1. 완료 결과

메뉴 선택이 단순한 화면 상태 변경에 머물지 않고, 공유·새로고침 가능한 canonical URL로 전환된다. URL은 화면의 진입점이며, 프로젝트·의뢰·하중 경우의 런타임 선택은 해당 화면의 데이터 로더와 현재 선택 상태로 결정한다. 이 식별자를 URL에 보존하는 deep-link는 후속 범위다.

- `React Router`의 location/navigate를 사용한다.
- 메뉴와 URL은 단일 `WORKSPACE_ROUTES` 레지스트리가 소유한다.
- 사이드바, 도움말 링크, 앱 내부 이동은 레지스트리에서 경로를 얻는다.
- 대시보드는 메뉴 진입과 데이터 가져오기 진입을 구분한다. 메뉴 진입은 monitoring 화면으로 reset하고, 가져온 결과에서의 진입은 선택한 분석 화면을 preserve한다.

## 2. canonical URL

| 영역 | canonical URL |
|---|---|
| 운영 대시보드 | `/workspace/overview` |
| 해석 의뢰 현황 | `/workspace/requests` |
| 의뢰 접수 | `/workspace/requests/new` |
| 해석 작업 실행 | `/workspace/execution` |
| 해석 데이터 등록 | `/workspace/data` |
| 작업 유형 관리 | `/workspace/admin/work-types` |
| 프로젝트 결과 구성 | `/workspace/project/result-layouts` |
| 폴더/변수/자동화 카탈로그 | `/workspace/catalog/schemas`, `/workspace/catalog/variables`, `/workspace/catalog/templates` |
| 접근·메뉴 정책·감사 | `/workspace/admin/access`, `/workspace/admin/menu-policy`, `/workspace/admin/audit` |
| 예제·도움말 | `/workspace/examples`, `/workspace/help` |

후행 `/`은 같은 route로 정규화한다. 알려지지 않은 `/workspace/*` 경로는 명시적인 404 화면을 표시한다.

## 3. 이력·새로고침·권한 규칙

- 내부 화면 이동은 browser history에 기록되므로 뒤로/앞으로가 URL과 함께 동작한다.
- 직접 URL 입력과 새로고침은 route registry로 같은 화면을 해석한다. 배포 환경은 SPA entry(`index.html`)로의 history fallback을 제공해야 한다.
- 인증/메뉴 정책이 준비된 뒤 현재 URL이 허용되지 않으면, 허용된 첫 메뉴로 `replace` 이동하고 안내를 표시한다.
- `/`, `/workspace`, `/workspace/`는 허용된 첫 화면으로 정규화한다.
- 편집 중 이동은 `useBlocker`로 보류한다. 사용자가 취소하면 pending destination을 버리고, 확인하면 편집을 취소한 뒤 목적지로 이동한다.

## 4. 모듈화 완료 범위

| 경계 | 책임 |
|---|---|
| `features/navigation/workspaceRouteRegistry.ts` | canonical path, 메뉴 메타데이터, 권한·진입 동작 |
| `app/routing/useWorkspaceNavigation.ts` | URL 해석, 권한 fallback, history 이동, 편집 차단 |
| `app/routing/workspaceRouteModules.tsx` | lazy import와 hover/preload 대상 |
| `app/workspace/WorkspaceRouteRenderer.tsx` | route-to-feature adapter |
| feature 폴더 | 접수, 결과, 데이터, workbench 등 기능별 화면과 API adapter |
| `shared/api` | 생성 OpenAPI client와 공통 adapter |

분석 결과·프로젝트 결과 구성처럼 후속 기능도 위 경계를 사용한다. URL/메뉴 정의를 개별 화면에 중복하지 않는다.

## 5. 현재 제약과 후속 리팩터링

- `App.tsx`는 현재 1076줄 architecture ceiling을 유지한다. 따라서 이를 400줄 이하라고 주장하지 않는다.
- 추가 화면은 App에 로직을 누적하기보다 route module, workspace controller 또는 기능 폴더로 먼저 분리한다.
- URL에 프로젝트·의뢰·하중 경우 식별자를 모두 포함하는 deep-link 확장은 별도 UX/권한 설계 후 진행한다. 현재 canonical URL은 workspace 진입점을 안정화하는 범위다.

## 6. 검증 명령

```bash
cd frontend
pnpm run test:routing
pnpm run test:preferences
pnpm check:architecture
pnpm run build
pnpm run test:e2e -- e2e/workspace-routing.spec.ts e2e/analysis-pages.spec.ts
```

검증은 canonical path 왕복, trailing slash 정규화, unknown path, 대시보드 reset/preserve, 권한 fallback, 편집 중 navigation blocker, direct route 렌더링을 포함한다.
