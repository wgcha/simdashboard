# Analysis Canvas

해석 데이터베이스를 기반으로 사용자가 시각화 위젯과 레이아웃을 구성할 수 있는 대시보드 MVP입니다. 현재 실행 데이터베이스는 파일 기반 DuckDB이며, 저장소 계층을 분리해 PostgreSQL 전환을 준비합니다.

## 현재 구현 범위

- TV 포장 낙하 시 Open Cell 상·하·좌·우 엣지 최대 응력
- 엣지별 응력-시간 그래프와 75 MPa 기준선
- 엣지별 및 전체 PASS/FAIL 판정
- 수행자 의견과 상세 결과표
- 컨투어 이미지용 미디어 메타데이터와 위젯
- 해석 의뢰 작업 순서와 단계별 상태
- 웹에서 프로젝트 → 의뢰 → 하중 경우를 순서대로 등록하는 운영 화면
- 의뢰 등록 시 10단계 기본 작업 순서 자동 생성(Validation 선택 단계)
- DROP 및 SIDE_CLAMP 하중 경우 등록 API와 파일 DB 영구 저장
- Open Cell 파손 분석과 Chassis Rear 영구변형 평가 계층 탭
- 드래그·리사이즈 대시보드 편집
- DuckDB에 저장되는 버전형 레이아웃
- 자연어 요청을 검증된 위젯 변경안으로 변환하는 MVP
- 데스크톱 및 모바일 조회 화면

## 데이터 소유 관계

```text
Project
├─ ProductInformation
│  ├─ CAD
│  ├─ Material
│  └─ Reliability
├─ AnalysisRequest
│  ├─ RequestStep
│  └─ LoadCase
│     ├─ DROP
│     │  ├─ TemplateExecution
│     │  └─ AnalysisRun → AnalysisResult / MediaAsset / Note
│     └─ SIDE_CLAMP
│        ├─ TemplateExecution
│        └─ AnalysisRun → AnalysisResult / MediaAsset / Note
└─ Validation
   ├─ Reliability verdict
   ├─ Sensor data
   └─ AI analysis
```

모델링 자동화 템플릿 실행과 해석 결과는 프로젝트나 의뢰에 직접 귀속되지 않고 반드시 `LoadCase` 아래에 위치합니다. 실제 결과는 `AnalysisRun`과 연결되어 템플릿 버전, 입력값 및 재실행 이력을 추적할 수 있습니다.

## 빠른 실행

요구 사항:

- Python 3.11 이상
- Node.js 20 이상
- pnpm

PowerShell에서 다음 명령을 실행합니다.

```powershell
cd E:\simulation_dashboard
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
cd .\frontend
pnpm install
cd ..
.\start.ps1
```

브라우저에서 <http://127.0.0.1:5173>을 엽니다. API 문서는 <http://127.0.0.1:8000/docs>에서 확인할 수 있습니다.

이미 설치가 완료된 현재 환경에서는 다음 명령만 실행하면 됩니다.

```powershell
cd E:\simulation_dashboard
.\start.ps1
```

종료:

```powershell
.\stop.ps1
```

## 수동 실행

백엔드:

```powershell
cd E:\simulation_dashboard\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

프런트엔드:

```powershell
cd E:\simulation_dashboard\frontend
pnpm run dev
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
- `backend/app/main.py`: 프로젝트, 의뢰, 하중 경우, 결과, 워크플로 및 레이아웃 API
- `backend/data/analysis_dashboard.duckdb`: 자동 생성되는 파일 DB
- `frontend/src/App.tsx`: 대시보드, 편집기, 자연어 변경 미리보기
- `frontend/src/styles.css`: 반응형 엔지니어링 UI

## 자연어 편집 MVP

현재 다음 문장을 인식합니다.

- `응력-시간 그래프를 추가해`
- `상하좌우 최대 응력 막대그래프를 추가해`
- `패스/실패 판정 카드를 추가해`

명령은 SQL이나 코드를 실행하지 않습니다. 백엔드가 허용된 위젯 명세로 변환하고, 프런트엔드에서 미리보기를 확인한 뒤에만 레이아웃에 적용합니다. 외부 AI 모델 연동 시에도 같은 명세 검증 경계를 유지해야 합니다.

## 파일 및 3차원 결과 정책

이미지, 영상, CAD 및 3차원 데이터는 DuckDB에 바이너리로 저장하지 않습니다. DB에는 경로, MIME 형식, 크기, 체크섬과 결과 메타데이터만 저장합니다.

- 컨투어 이미지: PNG, JPEG, WebP
- 영상: MP4, WebM
- 브라우저 3D 가시화: GLB 또는 glTF만 허용
- 원본 CAD/CAE 파일: 참조 및 변환 이력만 관리

## PostgreSQL 전환

현재 MVP는 DuckDB 연결을 사용합니다. PostgreSQL 단계에서는 다음 순서로 전환합니다.

1. 현재 테이블을 마이그레이션 도구가 관리하는 스키마로 옮깁니다.
2. 쿼리를 저장소 인터페이스 뒤로 이동시킵니다.
3. 파일 경로는 로컬 경로 대신 객체 스토리지 키로 교체합니다.
4. 대시보드 JSON 명세와 결과 메타데이터는 PostgreSQL JSONB를 사용합니다.
5. 실행 이력과 결과 대량 적재에 트랜잭션 및 배치 적재를 적용합니다.
6. DuckDB를 로컬 개발 및 읽기 전용 분석 모드로 유지합니다.

자격증명은 코드에 저장하지 않고 환경변수 또는 시크릿 저장소에서 주입합니다.
