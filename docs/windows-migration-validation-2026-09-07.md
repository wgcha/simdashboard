# WSL → Windows 개발환경 검증 기록

검증일: 2026-09-07. 작업 트리: `E:\simulation_workbench`, 시작 HEAD: `a7a0a78`.

## 확인 결과

Windows 개발 실행은 기존 DuckDB의 별도 복제본으로 검증했다. 기존 `.env`는 Windows PostgreSQL을 가리키며, 그 DB는 현재 코드보다 오래된 스키마여서 그대로 실행할 수 없다. WSL의 작업 데이터를 계속 사용하려면 별도 PostgreSQL 데이터 이전이 필요하다. 두 원본 DB는 데이터 구성이 달라 어느 쪽도 덮어쓰지 않았다.

## 보존과 정리

- 최초 미커밋·미추적 파일 13개를 `output/windows-migration-backup-20260907-223533/`에 복사했다. `preserved-files.json`의 SHA-256과 `tracked-changes.patch`로 원본 변경을 확인할 수 있다.
- `.env`, `.postgres-owner.env`, 기존 DuckDB 파일 3개도 같은 백업에 보존했다. 비밀값은 검증 문서나 로그에 기록하지 않았다.
- 최초 환경 이전 검증에서는 기존 프런트엔드 작업 파일 10개를 내용 변경 없이 보존했다. 이후 사용자가 보고한 메뉴 역순 문제를 수정하면서 `AppSidebar.tsx`와 `access-policy.spec.ts`의 순서만 바로잡았다. 나머지 8개는 최초 백업과 SHA-256이 같고, 두 수정 파일의 원래 내용도 백업에 남아 있다. 환경 이전 과정의 별도 브라우저 테스트 수정은 `analysis-pages.spec.ts`와 `playwright.config.ts`에 있다.
- 참조되지 않는 초기 패치 `e_frontend.patch`, `.e-patches/frontend.patch`는 `retired-artifacts/`에 보존한 뒤 작업 트리에서 제거했다. 복사 과정의 `Zone.Identifier` 메타데이터도 백업 후 제거했다.
- Git에 남아 있던 생성 캐시 `frontend/tsconfig.app.tsbuildinfo`, `frontend/tsconfig.node.tsbuildinfo`도 보존 후 제거했다. 해당 캐시는 빌드 시 재생성되며 이미 `.gitignore` 대상이다.
- 런타임·출력물·테스트 임시 폴더는 Git에서 제외했다. 과거 설계 문서는 결정 근거가 있으므로 문서 지도에서 현재 기준과 구분했다.
- 중복 Windows 설치기를 PowerShell 진입점으로 통합하고, 시작 시 PostgreSQL 자동 migration을 제거했다. 종료는 PID뿐 아니라 프로세스 시작 시각과 프로젝트 정체성을 확인한다.

## Windows 런타임

| 구성 요소 | 검증 버전 / 경로 |
|---|---|
| Node.js | `v22.23.2`, `.tools/node-v22.23.2-win-x64/` |
| Python | `3.12.13`, `.tools/python/` 및 `.venv-runtime/` |
| uv | `0.11.32`, `.tools/uv/` |
| pnpm | `11.15.1`, `.tools/pnpm.cmd`, 프로젝트 Corepack 캐시 |
| backend 의존성 | `requirements.lock`의 Windows용 39개, 호환성 검사 통과 |
| frontend 의존성 | 기존 `frontend/pnpm-lock.yaml`로 171개 설치 |
| 브라우저 | Playwright Chromium, `.tools/playwright/` |

Node와 uv 배포 압축파일의 SHA-256을 검증했다. 시스템 Node 26.5.0이나 WindowsApps Python 별칭에 의존하지 않는다. Python은 기존 WSL 설치와 동일하게 [uv의 관리형 Python 설치](https://docs.astral.sh/uv/guides/install-python/)를 사용했다. Windows 설치·실행 명령은 [Windows 개발환경](windows-development-setup.md)을 따른다.

## DB 비교 — 읽기 전용 조회

아래 두 PostgreSQL은 이름이 `simulation_dashboard`로 같지만 별개 클러스터다. Windows에서 `127.0.0.1:5432`로 연결하면 Windows 서비스가 응답한다.

| 항목 | Windows PostgreSQL | WSL Ubuntu PostgreSQL |
|---|---|---|
| 서버 버전 | 18.4 | 18.6 |
| migration revision | `0007_access_control_menu_policy` | `0015_legacy_drop_layout` |
| 프로젝트 | 4 | 5 |
| 해석 의뢰 | 10 | 16 |
| 해석 Run | 19 | 14 |
| 사용자 | 0 | 14 |
| 일반 미디어 | 7 | 3 |
| blob / chunk | 테이블 없음 | 21 / 21 |
| 영상 | 해당 테이블 없음 | 20 |
| 감사 이벤트 | 502 | 418 |

현재 코드 head는 `0019_batch_recovery_lease`다. Windows DB는 앱 역할로 연결되지만 스키마 조건을 충족하지 않는다. WSL DB도 현재 head까지 별도의 업그레이드가 필요하다. 실행 스크립트는 이를 자동 변경하지 않는다.

WSL의 일반 미디어 3개와 영상 20개는 모두 `blob_id`가 있다. 이들 콘텐츠는 PostgreSQL dump에 포함되는 DB blob으로 이전할 수 있다. 결과 원본 폴더, 이후 추가되는 파일 전용 미디어, 외부 경로 설정은 별도로 확인해야 한다. Windows DB의 일반 미디어 7개는 상대 파일 경로를 사용하므로 복원 시 파일 연결도 검증해야 한다.

`transfer-bundles/analysis-canvas-transfer-20260728T123701Z/`는 7월 28일의 오래된 v1 번들이다. 현재 원본 데이터의 백업으로 간주하거나 새 Windows DB에 덮어씌우면 안 된다.

권장 이전 순서는 두 원본의 최신 백업 → 별도 이름의 Windows DB에 WSL dump 복원 → owner 역할로 `0019`까지 migration → app 역할 권한·미디어·행 수 검증 → Windows에만 존재하는 기록 비교 → 사용할 연결을 선택하는 순서다. 이번 요청에서는 필요 여부만 확인했으며 원본 DB 복원·교체·스키마 변경은 실행하지 않았다.

## 실행 및 검증 범위

개발 DB는 원본 `backend/data/analysis_dashboard.duckdb`를 복사한 `backend/data/windows-verification.duckdb`다. 첫 시작의 DuckDB 호환 bootstrap은 이 복제본에만 적용된다.

```powershell
.\setup.ps1
.\start.ps1 -DatabaseBackend duckdb -DuckdbPath 'E:\simulation_workbench\backend\data\windows-verification.duckdb'
Invoke-RestMethod http://127.0.0.1:8000/api/health
# 작업을 마치면
.\stop.ps1
```

- 설치 스크립트 재실행, bootstrap 재실행, 프로세스 종료·재시작을 확인했다.
- 프런트엔드 architecture·preferences·routing·API 자체 검사와 production build를 통과했다. 기존 500 kB 초과 번들 경고는 남아 있다.
- `http://127.0.0.1:5173/workspace/overview`가 실제 집계 화면을 렌더링하고 도움말 메뉴가 `/workspace/help`로 이동함을 확인했다. 페이지 제목, 비어 있지 않은 화면, framework 오류 화면 없음, 콘솔 오류 0건, 실패 요청 0건을 확인했다.
- 데스크톱 1440×1000과 모바일 390×844의 화면을 확인했다. Browser plugin이 없어 저장소 Playwright를 사용했다. 처음 sandbox 브라우저의 네트워크 거부는 정상 권한으로 재검증하여 해소했다.
- 초기 E2E 전체 실행은 7개 통과·26개 실패였다. 대부분 Windows 초기 데이터 준비 중 5초 제한을 넘었고, 버전 이력 테스트는 동적인 enabled 버튼 선택이 다른 버전으로 바뀌는 문제가 있었다. Windows 전용 대기시간과 안정적인 버전 선택으로 수정한 뒤, DB 부하 테스트와 겹치지 않게 전체 33개를 재실행해 모두 통과했다.
- 백엔드 전체 실행은 Windows DB 초기화 비용으로 68개 진행 후 중단했다. 전체 테스트 통과를 의미하지 않는다. 환경·DB 보호·상태 API·지원되는 수동 업로드에 직접 관련된 48개 검사는 46개 통과·2개 skip으로 완료했다. 두 skip은 실제 PostgreSQL 테스트용 DB를 명시하지 않아 실행하지 않은 통합 검사다.
- Rocky 배포 테스트 38개는 POSIX 전용이므로 Windows에서 명시적으로 skip한다. Master Result Refresh도 기존 보안 계약에 따라 Windows에서는 fail-closed한다. Linux 전용 snapshot·배포·실제 PostgreSQL 통합 release gate는 이번 검증에 포함되지 않는다.

## 최종 검증 기록

| 검사 | 결과 | 근거 |
|---|---|---|
| Windows bootstrap / 전체 setup | 통과 | `output/windows-setup-final.log` |
| backend 패키지 호환성 | 39개 호환 | `uv pip check --python .venv-runtime/Scripts/python.exe` |
| frontend 정적 검사 5종 / build | 통과 | architecture, architecture 자체 검사, preferences, routing, API 및 Vite build |
| Chromium E2E 전체 | 33 통과, 0 실패 (6분 38초) | `output/windows-e2e-results.xml`, `output/windows-e2e-tests.log` |
| backend 핵심 검사 | 46 통과, 2 PostgreSQL skip (69초) | `output/windows-backend-targeted.xml`, `output/windows-backend-targeted.log` |
| Rocky 전용 테스트 | Windows에서 38 skip | 명시적인 POSIX 플랫폼 조건 |
| native 재시작 / health | 통과 | API 8000, Vite 5173 모두 `127.0.0.1`; `status=ok`, `database_backend=duckdb` |
| 최초 환경 이전 보존 검증 | 원본 frontend 10개·환경설정 2개·DuckDB 3개 동일 | 후속 메뉴 수정 전 SHA-256 재비교; E2E 종료 후 기존 E2E DB도 바이트 단위 복원 |

백엔드 핵심 검사의 모듈은 `test_database_bootstrap_boundary.py`, `test_postgres_portability.py`, `test_deployment_profile.py`, `test_pytest_runtime_guards.py`, `test_system_health_slice.py`, `test_manual_result_ingestion.py`다. 여기에 PostgreSQL 초기화의 비변경 경계, owner 비사용, 알 수 없는 revision 거부, 단일 migration head, Windows 시작 스크립트의 비변경 계약 및 명시 PostgreSQL profile 검사를 선택했다. 원본 DB를 대상으로 통합 write 테스트를 수행하지 않았다.

## 후속 확인 — 메뉴 역순과 라벨 데이터

사용자가 Windows 화면의 사이드바가 다시 역순이고 라벨 기능이 보이지 않는다고 보고해 Git, 실제 개발 서버, WSL 원본 데이터를 비교했다.

- Windows와 WSL 주 작업 트리의 HEAD는 모두 `a7a0a78`이다. 원격 브랜치를 `git ls-remote --heads origin`으로 확인했고, 현재 HEAD는 `origin/main`보다 13커밋 앞서며 뒤처진 커밋은 없다. 라벨 기능 도입 커밋 `1dccb0a`도 현재 HEAD에 포함된다.
- 역순은 복사된 미커밋 `AppSidebar.tsx`의 `.reverse()` 때문이었다. 최초 작업에서 이 변경을 그대로 보존한 탓에 화면에 남았다. 사용자 요청에 따라 Git 커밋본의 메뉴 순서로 복구했고, E2E 기대 순서도 맞췄다. 사용자가 추가한 글자 크기 검사는 유지했다.
- 실제 `5173` 서버는 작업 트리를 직접 제공하는 Vite이며 `Cache-Control: no-cache`다. 현재 화면은 `해석 의뢰 현황 → 의뢰 접수 → 해석 작업 실행 → 해석 데이터 등록` 및 번호 `1 → 2 → 3 → 4`를 표시한다.
- 라벨 입력·편집은 `/workspace/admin/work-types`, 라벨 필터는 `/workspace/requests/new`에 존재한다. 실제 브라우저에서 확인했고, 라벨 생성·버전 수정·접수 필터·삭제를 포함한 기존 E2E와 사이드바 E2E를 함께 실행해 **2개 모두 통과**했다. 근거는 `output/sidebar-labels-e2e.xml`과 `output/sidebar-labels-e2e.log`다.
- 저장 데이터는 같지 않다. 현재 Windows 검증용 DuckDB의 API에는 활성 작업 유형 2개와 `SPDM`, `부서` 라벨이 있다. WSL PostgreSQL에는 활성 작업 유형 7개와 `SPDM`, `부서`, `기구파트`, `포장파트` 라벨이 있다. 코드가 있어도 WSL에 저장된 유형과 라벨이 현재 검증용 DB 화면에 모두 나타나지는 않는다. 이 데이터를 이어 쓰려면 앞서 설명한 PostgreSQL 이전이 필요하다.
- 후속 E2E 종료 후 `backend/data/e2e-playwright.duckdb`는 테스트 전 백업으로 복원했고 SHA-256 일치를 확인했다. WSL/Windows 원본 PostgreSQL과 개발 서버의 검증용 DB에는 이번 확인을 위한 쓰기 작업을 하지 않았다.
- 검수 중 발견한 `stop.ps1`의 HTTP 보조 식별 제목도 실제 `frontend/index.html`의 `VD simulation workbench`에 맞췄다. PID·경로 기반 식별은 유지했으며 PowerShell 문법과 기존 시작 스크립트 계약 검사가 통과했다.
