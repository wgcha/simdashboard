# Analysis Canvas

## 권한 기능 문서

- [구축 계획서](docs/access-control-and-menu-policy-plan.md): 제품 결정, 요구사항 ID와 완료 기준
- [구현 수행서](docs/access-control-and-menu-policy-implementation-guide.md): Luna가 재현 가능한 단계별 구현·시험 절차
- [기능 사양서](docs/access-control-functional-specification.md): 역할, permission, 메뉴, API, 데이터와 운영 계약
- [변경 이력](docs/access-control-change-log.md): 실제 변경 범위, 마이그레이션, 검증 결과와 인수 조건
- [배포·보안·백업 가이드](docs/deployment-security-backup-guide.md): Windows VM 이관·복구 절차

## 운영 런타임과 Windows 이관

최종 운영 대상은 Rocky Linux 8이다. Node.js는 [`.node-version`](.node-version)의 `v22.23.2`, Python은 [`.python-version`](.python-version)의 `3.12.13`, 데이터베이스는 PostgreSQL 18 계열을 사용한다. 세부 호환성·변경 조건은 [ADR 0001](docs/adr/0001-runtime-version-policy.md)을 따른다. Windows VM은 기존 지원 및 이관 경로로 유지하며, 아래 절차로 설치·복구할 수 있다.

1. GitHub에서 저장소 전체를 다운로드한다.
2. VS Code에서 저장소의 최상위 폴더를 연다.
3. VS Code 터미널에서 `setup.bat`을 실행한다.
4. 회사 HTTP/HTTPS 프록시와 예외 주소를 입력한다. 프록시가 없으면 Enter를 누른다.
5. PostgreSQL 처리 방식으로 새 DB 초기화(`Fresh`), 전송 번들 이관(`Transfer`), 기존 DB 유지(`Keep`) 중 하나를 선택한다.
6. 설치와 연결 검증이 끝나면 `start-postgresql.bat`을 실행한다.
7. 운영 `.env`에 `DEPLOYMENT_PROFILE=windows-vm-intranet`, `AUTH_MODE=oidc`, `DIRECTORY_MODE=http`를 설정한다. 누락되면 시작 전 검사가 서버 기동을 차단한다.
8. 사내 계정 연결 전 `backend\scripts\access_migration_preflight.py`의 JSON 결과가 `ready` 또는 승인된 `ready_with_warnings`인지 확인한다.

```powershell
.\setup.bat
.\start-postgresql.bat
```

`setup.bat`은 Python/프런트엔드 의존성 설치와 프로덕션 빌드 검증도 함께 수행한다. 입력한 프록시는 Git에서 제외된 `.setup-proxy.env`에 저장되며 현재 Windows 사용자만 읽고 쓸 수 있다. 인증정보는 설치 출력에서 마스킹되고 TLS 인증서 검증은 끄지 않는다.

자동 설치 시스템에서는 모드와 입력을 인자로 고정할 수 있다. 관리자 비밀번호가 포함된 URL은 명령행 대신 현재 프로세스의 `POSTGRES_ADMIN_URL` 환경변수로 전달한다.

```powershell
$env:POSTGRES_ADMIN_URL = 'postgresql://postgres:비밀번호@127.0.0.1:5432/postgres'
.\setup.bat -NonInteractive -Mode Fresh -SeedMode Empty -ProxyUrl http://proxy.company.local:8080
Remove-Item Env:POSTGRES_ADMIN_URL
```

기존 `setup-windows.bat`, `setup-postgresql.bat`, 개별 내보내기·가져오기 명령도 하위 호환을 위해 유지한다. PC 간 이관과 복구 절차는 [`docs/postgresql-pc-transfer-guide.md`](docs/postgresql-pc-transfer-guide.md)를 참고한다.

Radioss 해석 결과를 파일 기반 DuckDB에 저장하고, 프로젝트와 의뢰별로 탐색·판정·편집하는 한국어 웹 대시보드 MVP입니다. 데이터 접근 계층은 UI와 분리되어 있으며 PostgreSQL 이전을 고려한 구조입니다.

## 현재 구현 기능

- 프로젝트 → 의뢰작업 → 하중 경우(DROP, SIDE_CLAMP) 계층 탐색
- 의뢰 진행 상태와 여러 의뢰의 동시 진행 현황
- Open Cell 파손 분석: 상·하·좌·우 엣지 최대 응력, 응력-시간 그래프, 기준값 PASS/FAIL
- Chassis Rear 영구변형 평가: 위·아래 엣지 직선 이격 및 모서리 영구변형, 최대 위치, 기준값 PASS/FAIL
- DROP과 SIDE_CLAMP 모두 Open Cell 및 Chassis Rear 분석 제공
- Radioss 노드·요소·요소별 응력 CSV 가져오기와 사전 검증
- KPI, 기간 추이, 분포, 비교 차트, 필터, 검색, CSV 내보내기
- 변수 카탈로그와 모델링 자동화 템플릿 실행 이력
- 데스크톱·모바일 반응형 한국어 UI

## 대시보드 편집

Open Cell과 Chassis Rear는 각각 독립된 12열 그리드 레이아웃입니다.

1. 상세 분석 화면에서 `대시보드 편집`을 누릅니다.
2. 위젯 헤더를 끌어 위치를 변경합니다.
3. 위젯 오른쪽 아래 핸들을 끌어 크기를 변경합니다.
4. 톱니바퀴에서 제목, 시각화 유형, 데이터 변수, 집계 방식, 강조색, 기준선 표시를 바꿉니다.
5. `레이아웃 저장`을 눌러 DuckDB에 버전으로 저장합니다.

저장된 레이아웃은 다시 접속해도 복원됩니다. Open Cell과 Chassis Rear의 설정은 서로 영향을 주지 않습니다.

## 변수 카탈로그

좌측 메뉴의 `변수 카탈로그`에서 선택한 하중 경우의 실제 변수 메타데이터를 확인합니다.

- 변수 ID, 이름, 설명, 데이터 형식, 단위
- 분석 유형과 원천 데이터
- 기준값과 판정
- 허용 집계 방식과 허용 위젯 유형
- 검색 및 데이터 형식 필터

위젯 설정의 데이터 변수 목록도 이 카탈로그 API를 사용합니다.

관리자는 카탈로그 화면에서 변수를 생성·수정·비활성화할 수 있습니다. 정의는 DuckDB의 `variable_definitions`에 저장되고 동일한 `variable_key`를 가진 숫자 또는 시간 이력 결과가 들어오면 대시보드 그래프에 자동 연결됩니다. SQL 구조, CRUD API와 PostgreSQL 이전 방법은 [`docs/backend-sql-integration-guide.md`](docs/backend-sql-integration-guide.md)를 참고하세요.

## 자동화 템플릿

좌측 메뉴의 `자동화 템플릿`에서 DROP과 SIDE_CLAMP 모델링 자동화 실행 이력을 조회합니다.

- 템플릿명, 버전, 상태
- 연결 프로젝트·의뢰·하중 경우
- 입력 파라미터
- 생성 모델 정보
- 실행 시간

## 데이터 소유 관계

```text
Project
├─ ProductInformation ─ CAD / Material / Reliability
├─ AnalysisRequest
│  ├─ RequestStep
│  └─ LoadCase (DROP | SIDE_CLAMP)
│     ├─ TemplateExecution
│     └─ AnalysisRun
│        ├─ AnalysisResult
│        ├─ MediaAsset
│        └─ Note
└─ Validation ─ Reliability verdict / Sensor data / AI analysis
```

모델링 자동화 템플릿과 해석 결과는 반드시 하중 경우 아래에 저장됩니다. 경량 3D는 GLB 또는 glTF만 허용하며 원본 CAD/CAE 파일은 참조 경로와 메타데이터만 관리합니다.

## 예제 데이터

- 등록용 통합 예제: [`example/radioss_tv_result_example.csv`](example/radioss_tv_result_example.csv)
- 생성 원본과 형식 설명: [`examples/radioss/README.md`](examples/radioss/README.md)
- 계산 결과 검토: [`examples/radioss/REVIEW.md`](examples/radioss/REVIEW.md)

기본 판정값은 Open Cell 최대 응력 75 MPa, Chassis Rear 영구변형 5 mm이며 관리자 화면에서 변경할 수 있습니다. 값이 기준 이상이면 FAIL입니다.

## 실행

요구 사항은 `.python-version`의 Python, `.node-version`의 Node.js, pnpm 11.15.1입니다.

```powershell
cd E:\simulation_dashboard
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
cd .\frontend
pnpm install
cd ..
.\start.bat
```

- 웹: <http://127.0.0.1:5173>
- API 문서: <http://127.0.0.1:8000/docs>

종료:

```powershell
.\stop.bat
```

## 검증

```powershell
cd E:\simulation_dashboard\backend
..\.venv\Scripts\python.exe -m pytest -q

cd E:\simulation_dashboard\frontend
pnpm run build
pnpm exec playwright install chromium
pnpm run test:e2e
```

E2E 테스트는 실행 중인 개발 서버와 충돌하지 않는 전용 포트와 `backend/data/e2e-playwright.duckdb`를 사용합니다. 운영·워크플로 레이아웃은 브라우저별 저장소가 아니라 백엔드 버전 저장 API를 사용합니다.

FastAPI 계약을 변경한 뒤에는 프런트엔드에서 `pnpm run generate:api`를 실행합니다. 이 명령은 `openapi.json`과 타입 안전 클라이언트용 `src/generated/openapi.ts`를 함께 갱신합니다.

## 주요 경로

- `backend/app/database.py`: DuckDB 스키마와 재현 가능한 샘플 데이터
- `backend/app/main.py`: 프로젝트, 의뢰, 하중 경우, 결과, 변수, 템플릿, 대시보드 API
- `backend/app/repositories/`: 데이터 접근 계층
- `backend/app/media_policy.py`: 이미지·영상·경량 3D 정책
- `backend/data/analysis_dashboard.duckdb`: 자동 생성 파일 DB
- `frontend/src/App.tsx`: 계층 탐색, 분석 화면, 편집기, 카탈로그, 템플릿
- `frontend/src/PortfolioDashboard.tsx`: 운영 KPI와 필터 연동 화면
- `frontend/src/styles.css`: 반응형 UI

## PostgreSQL·외부 배포

기본 로컬 모드는 DuckDB입니다. PostgreSQL 운영 모드는 다음을 제공합니다.

- SQLAlchemy/psycopg 연결 어댑터와 Alembic migration
- Windows/Linux 최초 구축 스크립트
- DuckDB 읽기 전용 이관, 관계 검사, 행 수·체크섬 검증
- DB 소유자와 최소 권한 앱 역할 분리
- 비밀번호 로그인, Viewer/Editor/Admin 권한, HttpOnly 세션 쿠키
- 변경·인증·권한 거절 감사 로그
- 검증된 custom-format 백업과 빈 DB 복구 도구

상세 절차는 [PostgreSQL 연동 가이드](docs/backend-sql-integration-guide.md)와 [배포 보안·백업 가이드](docs/deployment-security-backup-guide.md)를 참고하세요. 외부 공개 전에는 `AUTH_MODE=password`, HTTPS, 별도 백업 저장소를 반드시 사용해야 합니다.

Rocky Linux 8 운영에서는 PostgreSQL만 runtime DB로 사용합니다. Alembic migration은 owner 역할로, 앱 실행과 seed/preflight는 app 역할로 분리합니다. 구체적인 순서와 비권한 템플릿은 [Rocky 8 배포 runbook](docs/rocky8-deployment-runbook.md)을 따르세요. PostgreSQL 시작은 schema 확인만 수행하며, 현재 결정적 fixture 세트는 필요할 때만 `backend/scripts/seed_database.py --mode reference`로 명시 실행합니다. `reference`와 `demo`는 현재 동일 fixture의 별칭이므로 운영 업무 데이터로 간주하면 안 됩니다.

샘플 데이터는 합성이며 실제 제품 판정 근거로 사용하면 안 됩니다.
