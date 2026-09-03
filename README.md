# Analysis Canvas

해석 의뢰의 접수·수행·결과 수집·판정·대시보드·PPTX 보고서를 하나의 흐름으로 관리하는 한국어 웹 애플리케이션입니다. 프런트엔드는 React/TypeScript/Vite, 백엔드는 FastAPI/Python으로 구성되며 로컬 개발은 DuckDB, 동시 사용자 운영은 PostgreSQL을 사용합니다.

이 README는 실행과 진입점만 설명합니다. 실제 코드 기준 구조는 [`docs/current-architecture.md`](docs/current-architecture.md), 개발 절차는 [`docs/development-workflow.md`](docs/development-workflow.md), 정리·개발 우선순위는 [`docs/program-consolidation-and-development-plan.md`](docs/program-consolidation-and-development-plan.md), 전체 문서 분류는 [`docs/README.md`](docs/README.md)를 먼저 참고하세요.

## 현재 제공 범위

- 프로젝트 → 해석 의뢰 → 하중 경우 → 실행 Run → 유형별 결과 탐색
- 업무 유형, 작업계획, 담당자, 배치 실행, 진행률과 상태 관리
- 업무 유형별 요청 결과 위젯 정의와 의뢰 생성 시 결과 레이아웃 snapshot 고정
- 수치·시계열·곡선·위치·이미지·영상·경량 3D 결과 등록 및 폴더 Import
- 신뢰된 마스터 결과 폴더 Refresh와 중복 방지 적재
- Open Cell 응력, Chassis Rear 영구변형, Run 비교, 데이터 신뢰도와 검토 항목
- 12열 대시보드 편집, 버전·복제·복구, 규칙 기반 자연어 변경 미리보기
- PPTX 보고서 레이아웃 편집, 템플릿 검사·치환·내보내기
- 프로젝트 역할 기반 권한, 메뉴 정책, 비밀번호/OIDC 인증, 감사 로그
- WSL 개발, Windows 호환 실행, Rocky Linux 8 + PostgreSQL 운영 배포

합성 예제 데이터와 기본 판정값은 기능 검증용이며 실제 제품 승인 근거로 사용할 수 없습니다.

## 빠른 시작

고정 런타임은 [`.node-version`](.node-version)의 Node.js, [`.python-version`](.python-version)의 Python, `pnpm@11.15.1`입니다.

### WSL2 Ubuntu

저장소 루트에서 다음을 실행합니다.

```bash
./setup-wsl.sh
./start.sh
```

- 웹: <http://127.0.0.1:5173>
- API 문서: <http://127.0.0.1:8000/docs>
- 상태 확인: <http://127.0.0.1:8000/api/health>

종료는 `./stop.sh`입니다. 설치 조건과 장애 대응은 [`docs/wsl-development-setup.md`](docs/wsl-development-setup.md)를 따릅니다.

### Windows

PowerShell에서 다음을 실행합니다.

```powershell
.\setup.ps1
.\start.ps1
```

Windows 실행은 개발·호환성 검증과 DuckDB→PostgreSQL DB 이관을 위한 compatibility profile입니다. PostgreSQL 프로필을 사용할 때는 `start-postgresql.ps1`을 사용합니다. 새 DB 구축, 기존 DB 유지, 전송 번들 이관은 [`docs/backend-sql-integration-guide.md`](docs/backend-sql-integration-guide.md)와 [`docs/postgresql-pc-transfer-guide.md`](docs/postgresql-pc-transfer-guide.md)를 따릅니다. Windows one-command 운영 배포는 지원하지 않습니다. 사내 운영 배포는 [`deploy/rocky8/README.md`](deploy/rocky8/README.md)의 Rocky 8.6+ 단일 명령 배포 절차를 사용합니다. Master Refresh endpoint는 POSIX snapshot traversal을 사용하는 canonical WSL/Rocky 경로에서만 지원하며, native Windows에서는 Windows handle 기반 adapter가 준비될 때까지 fail-closed합니다. 수동 upload와 다른 compatibility 기능은 계속 사용할 수 있습니다.

## 실행 프로필

| 용도 | 설정 | 계약 |
|---|---|---|
| 로컬 개발·데모 | `ANALYSIS_DB_BACKEND=duckdb` | 단일 프로세스용이며 `backend/data/*.duckdb`를 자동 초기화합니다. |
| 동시 사용자 운영 | `ANALYSIS_DB_BACKEND=postgresql` + `DATABASE_URL` | PostgreSQL 18, Alembic migration, 최소 권한 app 역할을 사용합니다. |
| 로컬 인증 없음 | `AUTH_MODE=disabled` | 개발 전용 로컬 관리자 principal을 사용합니다. |
| 비밀번호 인증 | `AUTH_MODE=password` | 32자 이상 `AUTH_SECRET_KEY`가 필요합니다. |
| 사내 SSO | `AUTH_MODE=oidc` | HTTPS OIDC와 directory 설정이 필요합니다. |

환경변수 예시는 [`.env.example`](.env.example)에 있습니다. PostgreSQL에서는 앱 시작이 스키마를 변경하지 않습니다. owner 역할로 Alembic을 적용한 뒤 app 역할로 실행해야 합니다.

## 구현 구조 요약

```text
Browser
  → frontend/src/main.tsx                 React Router 진입
  → frontend/src/App.tsx                  인증·bootstrap·공용 조정자(점진 분리 중)
  → frontend/src/app/                     shell, URL routing, workspace controller
  → frontend/src/features/                기능 화면과 기능별 상태
  → frontend/src/shared/api/              OpenAPI client, 인증, 오류, 응답 adapter

HTTP /api
  → backend/app/main.py                   FastAPI 조립 + 아직 남은 legacy endpoint
  → backend/app/routers/                  분리된 기능 router
  → backend/app/adapters/http/routers/    vertical-slice HTTP adapter
  → backend/app/services|application/     orchestration/use case
  → backend/app/domains/                  프레임워크 독립 모델·port·policy
  → backend/app/repositories|adapters/persistence/
  → DuckDB(local) 또는 PostgreSQL(operation)
```

현재 구조는 완전한 단일 아키텍처가 아니라 점진 분리 중인 하이브리드입니다. 새 기능은 `main.py`나 `App.tsx`에 직접 누적하지 않고 위 경계를 사용해야 합니다. 정확한 책임과 주요 데이터 흐름은 [`docs/current-architecture.md`](docs/current-architecture.md)에 정리되어 있습니다.

## 주요 개발 명령

```bash
# 환경 진단
./scripts/wsl/doctor.sh

# 백엔드 전체 테스트
cd backend
../.venv-wsl/bin/python -m pytest -q

# 프런트엔드 정적 검증과 빌드
cd ../frontend
pnpm run check:architecture
pnpm run test:architecture
pnpm run test:preferences
pnpm run test:routing
pnpm run test:api
pnpm run build

# 브라우저 테스트(최초 1회 Chromium 준비 필요)
pnpm run test:e2e
```

FastAPI 경로나 요청·응답 스키마를 바꾸면 다음 명령으로 두 생성물을 함께 갱신합니다.

```bash
cd frontend
pnpm run generate:api
```

- OpenAPI snapshot: `frontend/openapi.json`
- 생성 TypeScript 계약: `frontend/src/shared/api/generated/openapi.ts`

생성 파일은 직접 편집하지 않습니다. 세부 변경 순서와 선택 테스트는 [`docs/development-workflow.md`](docs/development-workflow.md)를 참고하세요.

## 핵심 데이터 흐름

```text
업무 유형 버전
  → 작업계획 + 요청 결과 정의
  → 의뢰 생성 시 불변 snapshot
  → 수동/CSV/Radioss/폴더/마스터 Refresh 결과 적재
  → Run·변수·미디어·판정 연결
  → 상세 분석 대시보드
  → 검토·버전 편집·PPTX 보고서
```

마스터 폴더 Refresh는 서버가 설정한 `SIMDASH_IMPORT_ROOT`만 탐색하며 클라이언트가 임의 경로를 전달할 수 없습니다. canonical `mappings` manifest와 normalized payload는 공통 `ResultIngestionUnitOfWork`의 single-connection transaction으로 Run·결과·media blob을 함께 저장합니다. 일반 수동 `SUMMARY_RESULT` JSON/CSV와 `Radioss` mesh CSV도 target-qualified source와 content checksum을 사용해 같은 UoW로 저장하고, 동일 재시도는 `SKIPPED`되며 write transaction 안에서 권한 재확인과 audit를 수행합니다. Radioss의 `result_locations`도 같은 canonical contract에 포함된다. schema와 맞지 않던 구형 `result_files` persistence service/repository는 제거했으며 legacy manifest schema, `ManifestParser` alias와 normalized parser adapter만 compatibility 전용으로 남는다. 폴더·확장자·DB 저장 계약은 [`docs/storage-folder-and-file-contract.md`](docs/storage-folder-and-file-contract.md), 기능 흐름은 [`docs/work-type-request-results-and-master-refresh.md`](docs/work-type-request-results-and-master-refresh.md)를 따릅니다. 실제 import 예제는 [`examples/master-results/`](examples/master-results/)에 있습니다.

## 배포

Rocky Linux 8.6 이상(8.x) + nginx + systemd + PostgreSQL 18이 canonical 운영 target입니다. 구조는 `nginx → loopback FastAPI systemd service → PostgreSQL 18`이며 운영 Node.js 서버나 Docker를 사용하지 않습니다. 결정과 profile 범위는 [`docs/adr/0004-canonical-production-deployment-target.md`](docs/adr/0004-canonical-production-deployment-target.md)를 따릅니다.

사내 서버에서 저장소를 pull한 뒤 설정 파일을 한 번 작성하고 다음 명령 하나를
실행합니다. 비밀번호가 들어가는 `*.local.env`는 Git에서 제외되고 `0600` 권한을
강제합니다. PostgreSQL 18과 TLS 인증서는 실행 전에 준비되어 있어야 합니다.

```bash
cp deploy/rocky8/install.env.example deploy/rocky8/install.local.env
chmod 0600 deploy/rocky8/install.local.env
# install.local.env의 CHANGE_ME 값 수정 후
./deploy/rocky8/deploy-from-source.sh --config deploy/rocky8/install.local.env
```

배포기는 Rocky 8.6에서 부족할 수 있는 고정 Node/Python runtime 준비, frontend와
wheel bundle 빌드, checksum 검증, 설치 preflight, migration, 서비스 시작과 health
check를 연속 수행합니다. artifact를 별도로 전달하는 수동 경로도 유지됩니다.

- 설치와 번들: [`deploy/rocky8/README.md`](deploy/rocky8/README.md)
- 운영 순서: [`docs/rocky8-deployment-runbook.md`](docs/rocky8-deployment-runbook.md)
- 보안·백업: [`docs/deployment-security-backup-guide.md`](docs/deployment-security-backup-guide.md)

## 문서 유지 원칙

- 현재 구현 설명은 `docs/current-architecture.md`와 `docs/development-workflow.md`를 우선합니다.
- 제품 결정은 `docs/adr/`와 상태가 `구현 기준`인 기능 문서를 따릅니다.
- `plan`, `roadmap`, `spec` 문서는 배경과 수용 조건을 보존하는 기록일 수 있으므로 현재 구현 여부를 자동으로 뜻하지 않습니다.
- 코드 구조, 명령, 환경변수, API 계약을 바꿀 때 관련 기준 문서와 OpenAPI snapshot을 같은 변경에서 갱신합니다.
