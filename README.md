# Analysis Canvas

## 다른 Windows PC에서 최초 설치

GitHub에서 전체 저장소를 받은 뒤 프로젝트 최상위 폴더의
`setup-windows.bat`을 실행한다. 배치파일은 다음 작업을 순서대로 수행한다.

1. Python 3.12 확인
2. `.venv` 가상환경 생성
3. 백엔드 요구 패키지 설치
4. Node.js 20 이상 확인
5. `pnpm-lock.yaml` 기준 프런트엔드 패키지 설치
6. 프런트엔드 프로덕션 빌드 검증

설치 완료 후 다음 명령으로 서비스를 시작한다.

```powershell
.\start.bat
```

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

요구 사항은 Python 3.11~3.13, Node.js 20 이상, pnpm입니다.

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
```

## 주요 경로

- `backend/app/database.py`: DuckDB 스키마와 재현 가능한 샘플 데이터
- `backend/app/main.py`: 프로젝트, 의뢰, 하중 경우, 결과, 변수, 템플릿, 대시보드 API
- `backend/app/repositories/`: 데이터 접근 계층
- `backend/app/media_policy.py`: 이미지·영상·경량 3D 정책
- `backend/data/analysis_dashboard.duckdb`: 자동 생성 파일 DB
- `frontend/src/App.tsx`: 계층 탐색, 분석 화면, 편집기, 카탈로그, 템플릿
- `frontend/src/PortfolioDashboard.tsx`: 운영 KPI와 필터 연동 화면
- `frontend/src/styles.css`: 반응형 UI

## PostgreSQL 전환 계획

현재 기본값은 `ANALYSIS_DB_BACKEND=duckdb`이며 `ANALYSIS_DUCKDB_PATH`로 파일 위치를 지정합니다. 이후 `ANALYSIS_DB_BACKEND=postgresql`과 `DATABASE_URL`을 사용하도록 저장소 구현을 교체합니다.

1. 현재 테이블을 마이그레이션 도구가 관리하는 PostgreSQL 스키마로 옮깁니다.
2. `repositories/`의 DuckDB SQL 실행부를 PostgreSQL 어댑터로 교체합니다.
3. 대시보드 설정과 결과 메타데이터는 JSONB로 저장합니다.
4. 파일 바이너리는 객체 저장소로 옮기고 DB에는 경로와 검증 메타데이터만 저장합니다.
5. API 계약과 프런트엔드는 그대로 유지합니다.

샘플 데이터는 합성이며 실제 제품 판정 근거로 사용하면 안 됩니다.
