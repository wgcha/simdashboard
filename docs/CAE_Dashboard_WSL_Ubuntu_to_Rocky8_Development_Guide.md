# CAE 해석 대시보드 개발·운영 가이드
## WSL Ubuntu 개발 → Rocky Linux 8 운영 / Docker 미사용 환경

> 목적: 현재 구축 중인 CAE 해석 대시보드를 단순 조회용 웹앱이 아니라, 향후 **해석 DB · 물성 DB · DOE · HPC 해석 실행 · 결과 후처리 · 영상/결과 가시화 · MCP · Embedding · Graph DB · Local LLM**까지 확장 가능한 엔지니어링 플랫폼으로 발전시키기 위한 개발 및 운영 기준을 정의한다.

---

## 1. 기본 전제

### 개발 환경
- Host OS: Windows 11
- Linux 개발환경: WSL2 Ubuntu
- IDE: Windows의 VS Code + WSL Remote
- Browser: Windows Edge / Chrome
- Source Repository: WSL Linux filesystem 내부에 위치
- Git 기반 버전 관리
- Docker 사용하지 않음

### 운영 환경
- OS: Rocky Linux 8 계열
- Docker/Container 사용 불가
- Python 서비스: `venv`
- 서비스 관리: `systemd`
- Web reverse proxy / static serving: `nginx`
- DB: PostgreSQL
- CAE/HPC 연동: SSH/API 기반
- 사내망, 방화벽, 보안 정책, SELinux 고려

---

# 2. 최종 목표 아키텍처

```text
사용자 PC
  │
  │ HTTPS
  ▼
┌────────────────────────────┐
│       Rocky Linux 8        │
│                            │
│  ┌──────────────────────┐  │
│  │        nginx         │  │
│  └──────────┬───────────┘  │
│             │              │
│    ┌────────┴─────────┐    │
│    │                  │    │
│    ▼                  ▼    │
│ Frontend Static     FastAPI│
│ HTML/JS/CSS          API   │
│                       │    │
│           ┌───────────┼────────────┐
│           │           │            │
│           ▼           ▼            ▼
│      PostgreSQL     Worker      AI/MCP API
│                       │            │
└───────────────────────┼────────────┼─────┘
                        │            │
                       SSH          향후
                        │
                        ▼
                       HPC
                        │
               Radioss / LS-DYNA /
               기타 CAE Solver
                        │
                        ▼
                   Result Storage
                        │
                        ▼
                    PostgreSQL
                        │
                        ▼
                    Dashboard
```

핵심 원칙은 **웹 UI, API, DB, CAE Worker, AI/MCP를 논리적으로 분리**하는 것이다.

---

# 3. 개발환경 구성 원칙

## 3.1 Repository는 WSL Linux filesystem에 둔다

권장:

```bash
~/projects/cae-dashboard
```

예:

```text
/home/<user>/projects/cae-dashboard
```

비권장:

```text
/mnt/c/Users/...
/mnt/d/...
/mnt/e/...
```

Node, npm, Python package, Git, file watcher처럼 작은 파일을 자주 읽는 개발 작업은 WSL Linux filesystem 안에서 수행한다.

Windows 드라이브는 다음 용도로 제한한다.

- 대형 CAE 결과 원본
- Windows 전용 CAE 프로그램 입력/출력
- 사용자 파일 교환
- 필요 시 임시 export

---

# 4. 권장 Repository 구조

```text
cae-dashboard/
│
├── frontend/
│   ├── src/
│   ├── public/
│   ├── package.json
│   └── ...
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── repositories/
│   │   ├── core/
│   │   └── main.py
│   │
│   ├── requirements.txt
│   └── requirements.lock
│
├── worker/
│   ├── app/
│   │   ├── hpc/
│   │   ├── preprocessing/
│   │   ├── postprocessing/
│   │   ├── jobs/
│   │   └── main.py
│   │
│   ├── requirements.txt
│   └── requirements.lock
│
├── database/
│   ├── migrations/
│   ├── schema/
│   └── seeds/
│
├── deploy/
│   └── rocky8/
│       ├── nginx/
│       │   └── cae-dashboard.conf
│       ├── systemd/
│       │   ├── cae-api.service
│       │   └── cae-worker.service
│       ├── scripts/
│       │   ├── install.sh
│       │   ├── deploy.sh
│       │   ├── rollback.sh
│       │   └── healthcheck.sh
│       └── README.md
│
├── tests/
│   ├── backend/
│   ├── worker/
│   └── integration/
│
├── docs/
│   ├── architecture.md
│   ├── database.md
│   ├── hpc-interface.md
│   ├── deployment.md
│   └── operations.md
│
├── .env.example
├── .gitignore
└── README.md
```

---

# 5. Ubuntu 개발 / Rocky 8 운영 호환 원칙

## 5.1 Ubuntu 전용 기능에 의존하지 않는다

개발자가 Ubuntu에서 작업하더라도 Production target은 Rocky Linux 8이다.

따라서 Application code에서 다음을 직접 실행하지 않는다.

```python
os.system("apt install ...")
```

또는 OS별 package manager에 직접 의존하는 코드를 작성하지 않는다.

OS dependency는 `deploy/rocky8`에 별도 정의한다.

---

## 5.2 Python 버전은 Rocky 8 기준으로 고정한다

먼저 Rocky 서버에서 실제 사용 가능한 Python 버전을 확인한다.

```bash
python3 --version
dnf module list python
```

그 버전을 기준으로 WSL Ubuntu 개발환경도 동일한 major/minor 버전을 사용한다.

예:

```text
Development: Python 3.X
Production : Python 3.X
```

개발 편의를 위해 Ubuntu에서 무조건 최신 Python을 사용하는 방식은 피한다.

---

# 6. Python 환경관리

각 서비스는 독립된 `venv`를 사용한다.

예:

```text
/opt/cae-dashboard/.venv
/opt/cae-worker/.venv
/opt/cae-ai/.venv
```

개발환경:

```bash
cd backend

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

운영환경에서도 별도로 생성한다.

```bash
python3 -m venv /opt/cae-dashboard/.venv
source /opt/cae-dashboard/.venv/bin/activate

pip install -r requirements.lock
```

## 중요

Ubuntu에서 만든 `.venv`를 Rocky로 복사하지 않는다.

```text
Ubuntu .venv
     X
     │
     ▼
Rocky
```

대신:

```text
requirements.lock
      │
      ▼
Rocky에서 새 venv 생성
      │
      ▼
pip install
```

특히 아래 계열은 native binary dependency가 있으므로 주의한다.

- NumPy
- SciPy
- pandas
- psycopg
- cryptography
- OpenCV
- PyTorch
- ONNX Runtime
- 기타 C/C++ extension package

---

# 7. Dependency 관리 원칙

최소 두 파일을 유지한다.

```text
requirements.txt
requirements.lock
```

역할:

### requirements.txt
사람이 관리하는 주요 dependency.

예:

```text
fastapi
uvicorn
sqlalchemy
psycopg
pydantic
paramiko
```

### requirements.lock
실제 배포에 사용하는 정확한 version set.

Production은 가능한 한 lock된 버전을 사용한다.

목표:

```text
같은 Git Commit
+
같은 Python Version
+
같은 Dependency Version
=
동일한 동작
```

---

# 8. Frontend 운영 전략

React/Vue/Vite 등의 SPA를 사용한다면 Production에서 Node 개발 서버를 계속 실행하지 않는 것을 기본으로 한다.

개발:

```bash
npm run dev
```

배포:

```bash
npm ci
npm run build
```

결과:

```text
dist/
```

또는 framework의 production build artifact를 nginx가 직접 제공한다.

구조:

```text
Browser
   │
   ▼
 nginx
   │
   ├── /        → Frontend static files
   │
   └── /api     → FastAPI
```

Production 의존성을 줄이고 운영 안정성을 높이는 방향을 우선한다.

---

# 9. Backend API 설계

Backend는 가능하면 FastAPI와 같은 API 계층으로 분리한다.

권장 Layer:

```text
API Route
   ↓
Service
   ↓
Repository
   ↓
Database
```

예:

```text
backend/app/

api/
services/
repositories/
models/
schemas/
core/
```

API route에서 직접 SQL, SSH, filesystem operation을 수행하지 않는다.

---

# 10. PostgreSQL 역할

PostgreSQL을 단순 결과 저장 DB가 아니라 플랫폼의 **System of Record / Source of Truth**로 사용한다.

저장 대상 예:

- 과제/프로젝트
- 해석 모델
- DOE case
- 해석 조건
- 재료 참조 정보
- solver
- HPC job
- 해석 status
- 결과 metadata
- 결과 file path
- 영상 path
- 후처리 결과
- validation 정보
- 사용자/이력
- 변경 history

---

# 11. CAE Job 상태관리

초기에는 Redis/Celery를 반드시 도입하지 않아도 된다.

PostgreSQL 기반 Job Queue를 먼저 구축한다.

예:

```text
simulation_job
```

필드 예시:

```text
job_id
project_id
analysis_id
status
priority
created_at
started_at
submitted_at
finished_at
hpc_job_id
solver
input_path
result_path
error_message
retry_count
```

상태 예:

```text
CREATED
  ↓
QUEUED
  ↓
PREPROCESSING
  ↓
SUBMITTED
  ↓
RUNNING
  ↓
POSTPROCESSING
  ↓
COMPLETED
```

오류 시:

```text
FAILED
CANCELLED
TIMEOUT
```

---

# 12. Worker 설계

Web API process에서 장시간 해석 작업을 직접 수행하지 않는다.

```text
FastAPI
   │
   ▼
PostgreSQL Job
   │
   ▼
Worker
   │
   ├─ preprocessing
   ├─ input generation
   ├─ SSH submission
   ├─ HPC status polling
   ├─ result retrieval
   ├─ postprocessing
   └─ DB update
```

Worker를 별도 process로 분리한다.

이 구조는 향후:

- DOE 수십~수백 case
- 대량 해석
- 비동기 후처리
- 영상 생성
- AI inference

로 확장할 때 중요하다.

---

# 13. HPC Interface 설계

HPC connection 정보는 코드에 hard coding하지 않는다.

비권장:

```python
HPC_HOST = "192.168.x.x"
SOLVER_PATH = "/opt/solver/..."
QUEUE = "..."
```

권장:

```env
HPC_HOST=
HPC_PORT=
HPC_USER=
HPC_QUEUE=
SOLVER_PATH=
RESULT_ROOT=
LICENSE_SERVER=
```

HPC Adapter를 별도 abstraction으로 둔다.

예:

```text
worker/app/hpc/

base.py
ssh_client.py
scheduler.py
radioss.py
lsdyna.py
```

목표:

```text
Dashboard Logic
      │
      ▼
HPC Interface
      │
      ├─ Radioss
      ├─ LS-DYNA
      └─ 향후 다른 Solver
```

Solver별 구현이 Dashboard 핵심 로직에 침투하지 않도록 한다.

---

# 14. 파일 및 결과 Storage 원칙

대형 CAE 결과를 PostgreSQL binary column에 직접 저장하지 않는다.

DB에는 metadata와 path를 저장한다.

예:

```text
Database
 ├ result_id
 ├ case_id
 ├ file_type
 ├ file_path
 ├ file_size
 ├ checksum
 ├ created_at
 └ version
```

실제 데이터:

```text
Shared Storage / NAS / HPC Storage
```

예:

```text
/project/
  /analysis/
    /case/
      input/
      raw/
      processed/
      media/
      report/
```

---

# 15. 영상 및 결과 가시화

대시보드의 해석 영상 기능은 별도의 Media metadata로 관리한다.

예:

```text
analysis_media

media_id
analysis_id
scene_id
media_type
file_path
thumbnail_path
duration
order_index
created_at
```

현재 요구사항 기준으로 UI는 다음을 지원할 수 있도록 설계한다.

- 한 화면 최대 약 20개 영상
- 데이터 개수 기반 responsive grid
- 다음 페이지 / pagination
- 전체 재생 / 전체 정지
- 개별 재생 / 개별 정지
- DB의 scene별 media link와 연결
- WebM 등 Browser 친화적 format 활용
- Edge / Chrome 우선 검증

---

# 16. nginx 역할

Rocky Production에서 nginx는 다음을 담당한다.

```text
HTTPS
Static frontend
Reverse proxy
Optional file serving
Request size control
Timeout
Security header
```

예시 구조:

```text
Client
   │
 HTTPS :443
   │
   ▼
 nginx
   │
   ├── /       → frontend
   ├── /api/   → FastAPI
   └── /media/ → 필요한 경우 result media
```

DB port를 사용자 network에 직접 공개하지 않는다.

---

# 17. systemd 서비스 관리

Docker가 없으므로 `systemd`가 application lifecycle을 관리한다.

서비스 예:

```text
cae-api.service
cae-worker.service
```

향후:

```text
cae-mcp.service
cae-ai.service
```

기본 요구사항:

- Boot 시 자동 시작
- Process 비정상 종료 시 restart
- Service별 log 확인 가능
- 실행 user 분리
- Environment file 사용
- WorkingDirectory 명확화

개념 예:

```ini
[Service]
User=cae
WorkingDirectory=/opt/cae-dashboard/current
EnvironmentFile=/etc/cae-dashboard/backend.env
ExecStart=/opt/cae-dashboard/.venv/bin/uvicorn app.main:app
Restart=on-failure
```

실제 설정은 사내 서버 정책에 맞게 조정한다.

---

# 18. 환경변수 / Secret 관리

다음을 Source Code에 넣지 않는다.

- DB password
- HPC password
- API key
- Secret key
- 사내 server IP
- 인증서 private key

Repository에는:

```text
.env.example
```

만 저장한다.

Production:

```text
/etc/cae-dashboard/backend.env
/etc/cae-dashboard/worker.env
```

등 별도 파일을 사용한다.

권한 예:

```text
600
```

실제 인증 방식은 사내 정책을 따른다.

---

# 19. Rocky Linux 8 보안 고려

## SELinux

Ubuntu에서 정상 동작하더라도 Rocky에서 file access가 거부될 수 있다.

특히 확인 대상:

- Upload directory
- Result directory
- nginx static directory
- 공유 storage
- log directory
- PostgreSQL
- network connection
- SSH key

SELinux를 단순히 끄는 방식으로 해결하지 않는다.

Production deployment test에서 SELinux enforcing 상태 기준으로 검증한다.

---

## firewalld

원칙적으로 필요한 port만 노출한다.

예:

```text
443     HTTPS
```

필요 시 내부 관리 port만 제한적으로 허용한다.

다음은 사용자 network에 직접 공개하지 않는 것을 기본으로 한다.

```text
PostgreSQL 5432
Worker internal interface
AI internal service
```

---

# 20. 로그 설계

`print()` 중심 운영을 피한다.

Service별 structured logging을 사용한다.

예:

```text
API log
Worker log
HPC submission log
Postprocessing log
Error log
Audit log
```

각 Job에는 가능하면:

```text
job_id
analysis_id
project_id
```

를 log context로 포함한다.

목표:

```text
사용자 오류 보고
   ↓
analysis_id 또는 job_id 확인
   ↓
API → Worker → HPC → Postprocess 전체 추적
```

---

# 21. Error Handling

CAE workflow는 실패를 정상적인 상태로 취급해야 한다.

예:

```text
SSH 연결 실패
HPC queue 제출 실패
License 부족
Solver fail
결과 파일 누락
Postprocess fail
DB update fail
Network timeout
```

각 오류는 가능한 한 다음 형태로 저장한다.

```text
error_code
error_stage
error_message
retryable
timestamp
```

UI에서는 단순 `FAILED` 대신 어느 단계에서 실패했는지 확인 가능하도록 한다.

---

# 22. Database Migration

운영 DB schema를 수동 SQL 수정으로 관리하지 않는다.

Migration tool을 사용한다.

Python/SQLAlchemy 기반이라면 예:

```text
Alembic
```

원칙:

```text
Git Commit
  │
  ├ Application Code
  └ Database Migration
```

DB 변경도 version control에 포함한다.

---

# 23. 개발 → 운영 배포 흐름

권장:

```text
WSL Ubuntu 개발
      │
      ▼
Unit Test
      │
      ▼
Integration Test
      │
      ▼
Git Commit
      │
      ▼
Production Build
      │
      ├ Frontend build
      └ Python dependency lock
      │
      ▼
Rocky Test
      │
      ▼
Deploy
      │
      ▼
DB Migration
      │
      ▼
systemd Restart
      │
      ▼
Health Check
```

---

# 24. 배포 Directory 구조

예:

```text
/opt/cae-dashboard/
│
├── releases/
│   ├── 2026xxxx_001/
│   ├── 2026xxxx_002/
│   └── ...
│
├── current -> releases/2026xxxx_002
│
├── .venv/
└── shared/
    ├── logs/
    ├── uploads/
    └── data/
```

이 방식은 rollback을 쉽게 만든다.

```text
current
   ↓
release 002

문제 발생

current
   ↓
release 001
```

---

# 25. Health Check

Backend에 최소 endpoint를 둔다.

```text
GET /health
```

예:

```json
{
  "status": "ok"
}
```

향후:

```text
API
DB
Storage
HPC connectivity
Worker heartbeat
```

등을 별도 readiness check로 확장할 수 있다.

---

# 26. 테스트 전략

최소 네 계층으로 분리한다.

## Unit Test

```text
Parsing
Validation
Calculation
DB service
Result transformation
```

## API Test

```text
request
response
authentication
error response
```

## Integration Test

```text
API ↔ PostgreSQL
Worker ↔ PostgreSQL
Worker ↔ SSH test target
```

## End-to-End Test

```text
DOE / Case 생성
  ↓
해석 Job 생성
  ↓
HPC 제출
  ↓
결과 확보
  ↓
후처리
  ↓
DB
  ↓
Dashboard 표시
```

---

# 27. 현재 대시보드 WSL 이전 절차

기존 프로그램을 새로 작성하지 않는다.

권장 순서:

### Phase 1. 현재 코드 그대로 WSL에서 실행

```text
Windows 기존 Repository
       ↓ Git
WSL Repository Clone
       ↓
현재 기능 실행
```

먼저 기존 기능의 parity를 확보한다.

### Phase 2. Dependency 정리

```text
Python version
requirements
Node version
environment variable
DB connection
```

을 명시한다.

### Phase 3. Backend / Worker 분리

장시간 CAE 작업을 API process에서 제거한다.

### Phase 4. Rocky-compatible deploy 구성

```text
deploy/rocky8
systemd
nginx
install/deploy script
```

작성.

### Phase 5. Rocky Test Server 검증

Ubuntu에서 동작한다는 이유만으로 완료 처리하지 않는다.

### Phase 6. 운영 전환

Rocky 기준 acceptance test 후 Production 배포.

---

# 28. 현재 단계에서 Redis/Celery를 서두르지 않는 이유

초기 플랫폼에서는 PostgreSQL Job Table + Worker 구조로도 충분하다.

장점:

- 인프라 dependency 감소
- 사내 설치 승인 부담 감소
- Job history가 DB에 직접 남음
- CAE 업무 추적성 확보
- 운영 구조 단순

다음 조건에서 Redis/RabbitMQ/Celery 등을 다시 검토한다.

- 동시 Job 급증
- 다수 Worker 확장
- 초당 많은 queue event
- 복잡한 scheduling
- distributed task orchestration 필요

---

# 29. 향후 MCP / Embedding / Graph DB / Local LLM 확장

현재 Dashboard core와 AI dependency를 섞지 않는다.

권장:

```text
Dashboard API
   │
   ├── CAE DB
   ├── Material DB
   ├── Worker
   │
   └── AI Gateway
           │
           ├── MCP
           ├── Embedding
           ├── Vector DB
           ├── Graph DB
           └── Local LLM
```

AI 기능을 추가하더라도 기존 해석 workflow는 AI 없이 정상 동작해야 한다.

즉:

```text
CAE Core ≠ AI Core
```

로 설계한다.

---

# 30. Graph DB / Embedding의 역할 구분

## PostgreSQL

정형 데이터의 공식 기록.

예:

```text
Project
Model
Material
Simulation
DOE
Test
Result
Validation
```

## Embedding / Vector Search

비정형 정보 검색.

예:

- 보고서
- 해석 comment
- 기술 문서
- 시험 문서
- 유사 과제 검색
- 유사 failure mode 검색

## Graph DB

관계 탐색.

예:

```text
제품
 ↓
부품
 ↓
재료
 ↓
해석모델
 ↓
시험
 ↓
Failure Mode
 ↓
설계변경
```

Graph DB나 Vector DB를 PostgreSQL을 대체하는 목적으로 사용하지 않는다.

---

# 31. AI Agent / MCP 활용 방향

MCP는 Dashboard DB를 무제한 직접 수정하는 통로가 아니라, **명확하게 정의된 Engineering Tool Interface**로 설계한다.

예:

```text
search_analysis()
get_analysis_result()
find_similar_cases()
get_material_property()
submit_simulation()
get_job_status()
compare_results()
```

Write operation은 read operation보다 더 강한 권한 검증과 audit을 적용한다.

특히 해석 실행 Agent는:

```text
Input 생성
→ 사용자/Rule 검증
→ Job 등록
→ Worker 제출
```

절차를 유지한다.

LLM이 직접 shell command를 자유롭게 실행하는 구조는 피한다.

---

# 32. 데이터 ID 전략

PLM, 해석 DB, 물성 DB 등 여러 시스템을 연결할 때 시스템별 고유 ID를 유지하고 mapping layer를 둔다.

예:

```text
project_id
part_id
material_id
analysis_id
test_id
result_id
```

외부 시스템:

```text
plm_object_id
material_master_id
spdm_id
```

필요하면 mapping table 사용:

```text
external_object_mapping

system_name
external_id
internal_object_type
internal_id
```

ID를 파일명 parsing에만 의존하지 않는다.

---

# 33. CAE 데이터 추적성

한 해석 결과에서 다음을 추적할 수 있어야 한다.

```text
Result
 ↓
Analysis Case
 ↓
Input Model Version
 ↓
Geometry / Design Version
 ↓
Material Version
 ↓
Boundary Condition
 ↓
Solver Version
 ↓
Postprocess Version
 ↓
Validation/Test
```

향후 AI 학습 데이터의 신뢰도를 위해서도 필수다.

---

# 34. 배포 전에 반드시 확인할 Rocky 8 정보

사내 운영 담당자에게 확인:

- 정확한 Rocky Linux minor version
- 설치 가능한 Python version
- `sudo` 권한 여부
- `dnf` 설치 권한
- nginx 설치 가능 여부
- PostgreSQL 설치 위치/버전
- SELinux 상태
- firewalld 정책
- 인터넷 연결 여부
- 사내 Proxy
- 사내 PyPI/NPM mirror 존재 여부
- Git 접근 여부
- SSH key 사용 정책
- 서비스 계정 생성 가능 여부
- systemd unit 생성 권한
- shared storage/NAS mount
- HPC 접근 network
- TLS 인증서 발급 절차
- Backup 정책
- 로그 보존 정책

---

# 35. 인터넷 차단망 대응

사내 Rocky가 인터넷 차단이면 사전에 dependency 설치 방식을 정의한다.

가능한 방식:

```text
사내 PyPI Mirror
사내 NPM Registry
사내 RPM Repository
Offline wheel package
Offline frontend build artifact
```

Production 서버에서 인터넷 pip install을 전제로 개발하지 않는다.

---

# 36. 운영 Backup

최소 다음을 분리한다.

```text
Database backup
Application release
Configuration backup
User uploaded files
Result metadata
```

대형 CAE raw result는 별도의 storage backup 정책을 따른다.

DB backup과 CAE storage backup의 lifecycle은 동일하지 않을 수 있다.

---

# 37. Monitoring 최소 기준

초기에는 복잡한 monitoring system이 없어도 다음은 있어야 한다.

- API process alive
- Worker alive
- DB connectivity
- Disk usage
- Error log
- Job failure count
- 오래 RUNNING 중인 Job
- storage path accessibility

향후 중앙 monitoring 환경이 있다면 연계한다.

---

# 38. 성능 원칙

대형 CAE 데이터를 API가 한 번에 읽어 Browser로 전달하지 않는다.

권장:

```text
Raw Result
   ↓
Postprocessing
   ↓
Lightweight Data
   ↓
Dashboard
```

예:

- Raw H3D / solver output
- 필요한 scalar/curve 추출
- thumbnail
- WebM
- summary JSON
- downsample된 chart data

Dashboard는 가시화에 필요한 경량 데이터 중심으로 동작한다.

---

# 39. 보안 원칙

최소 기준:

- DB password Git 저장 금지
- HPC credential Git 저장 금지
- 최소 권한 service account
- HTTPS
- DB external exposure 금지
- Input validation
- Path traversal 방지
- Shell command parameter validation
- User action audit
- 해석 제출 권한 분리 고려
- LLM/Agent write 권한 제한

---

# 40. 금지하거나 피해야 할 구조

## 1. 모든 기능을 FastAPI 한 process에 넣기

```text
API
+ SSH
+ HPC
+ Postprocess
+ AI
+ 영상 변환
```

→ 장애 전파와 dependency 충돌 위험.

## 2. Ubuntu `.venv`를 Rocky로 복사

→ OS/native library incompatibility 가능.

## 3. Rocky 시스템 Python에 package 직접 설치

```bash
sudo pip install ...
```

→ 시스템 환경 오염.

## 4. Credential hard coding

→ 운영/보안 문제.

## 5. Raw CAE 결과를 DB BLOB로 대량 저장

→ DB 비대화.

## 6. DB schema를 운영 서버에서 수동 수정

→ 변경 추적 불가.

## 7. AI 기능을 Core workflow의 필수 dependency로 만들기

→ AI 장애가 해석 업무 전체 장애로 전파.

## 8. LLM에 자유 shell/HPC 권한 부여

→ 보안 및 재현성 문제.

---

# 41. 권장 기술 경계

```text
Frontend
  │
  │ REST/API
  ▼
Backend API
  │
  ├─ PostgreSQL
  └─ Job Registration
        │
        ▼
      Worker
        │
        │ SSH
        ▼
       HPC
        │
        ▼
    Postprocess
        │
        ▼
      Storage
        │
        ▼
    PostgreSQL
```

향후:

```text
Backend
   │
   ▼
AI Gateway
   │
   ├ MCP
   ├ Embedding
   ├ Graph
   └ Local LLM
```

---

# 42. 단계별 개발 Roadmap

## Stage 1 — 개발환경 이전

목표:

```text
현재 Dashboard를 WSL Ubuntu에서 완전히 실행
```

완료 조건:

- Frontend 실행
- Backend 실행
- PostgreSQL 연결
- 기존 기능 동일 동작

---

## Stage 2 — 환경 재현성

목표:

```text
다른 PC에서도 동일한 방법으로 개발환경 생성
```

완료 조건:

- README
- Python version
- requirements lock
- Node version
- `.env.example`
- DB migration

---

## Stage 3 — Rocky 8 Production 구조

목표:

```text
Native Linux service 구조 확립
```

완료 조건:

- nginx
- systemd API
- systemd Worker
- deployment script
- health check
- logging

---

## Stage 4 — CAE Workflow Platform

목표:

```text
단일 검증 → DOE / 설계 탐색 workflow
```

기능:

- Case 생성
- 입력 생성
- HPC 제출
- status 추적
- 결과 수집
- 후처리
- DB 저장
- Dashboard 가시화

---

## Stage 5 — Data Platform

목표:

```text
해석·물성·시험·설계 이력 연결
```

기능:

- 공통 ID
- Version
- Validation link
- Result history
- Search

---

## Stage 6 — AI Engineering Platform

목표:

```text
과거 Engineering Data 기반 검색·분석·지원
```

기능 후보:

- Embedding
- 유사 해석 검색
- Graph relationship
- MCP
- Local LLM
- Engineering Agent
- DOE/최적화 지원

---

# 43. 현재 시스템의 핵심 설계 철학

현재 플랫폼은 단순히 "해석 결과를 보여주는 Dashboard"가 아니다.

목표 흐름:

```text
Single Design Verification
          ↓
Automated CAE Workflow
          ↓
DOE / Design Exploration
          ↓
CAE + Material + Test Data Accumulation
          ↓
Traceable Engineering Database
          ↓
AI-assisted Engineering
          ↓
Data-driven Design Decision
```

따라서 현재의 개발 결정은 향후 다음 요소와 충돌하지 않아야 한다.

- 대량 DOE
- HPC
- 해석 DB
- 물성 DB
- 시험 Validation
- 설계 이력
- 자동 후처리
- 영상
- AI
- Graph
- Embedding
- MCP

---

# 44. 개발 에이전트 / Codex에게 줄 기본 지침

아래 원칙을 모든 개발 변경에 적용한다.

```text
1. Production target은 Rocky Linux 8이며 Docker는 사용할 수 없다.

2. 개발환경은 WSL2 Ubuntu이다.

3. Ubuntu-specific 구현보다 Rocky-compatible Linux 구현을 우선한다.

4. Python dependency는 서비스별 venv와 lock file로 관리한다.

5. Frontend, Backend API, Worker를 논리적으로 분리한다.

6. 장시간 CAE 작업을 Web request process에서 직접 실행하지 않는다.

7. HPC 연동은 별도 Adapter/Service layer로 격리한다.

8. 환경별 값, credential, host/path는 코드에 hard coding하지 않는다.

9. PostgreSQL을 해석 workflow 및 상태 관리의 Source of Truth로 사용한다.

10. 대형 CAE raw file은 filesystem/storage에 저장하고 DB에는 metadata/path를 저장한다.

11. DB schema 변경은 migration으로 관리한다.

12. Production service는 nginx + systemd + venv 구조를 기준으로 한다.

13. Ubuntu에서 만든 venv 또는 native binary를 Rocky에 그대로 복사하지 않는다.

14. SELinux와 firewalld 환경에서 정상 동작하도록 고려한다.

15. 기존 기능을 깨지 않는 점진적 migration을 우선한다.

16. AI/MCP/Embedding/Graph DB는 기존 CAE Core와 느슨하게 결합한다.

17. LLM에게 임의 shell/HPC 실행 권한을 직접 주지 않는다.

18. 모든 주요 Job은 ID 기반으로 API → Worker → HPC → Result까지 추적 가능해야 한다.

19. 코드 변경 시 운영 배포, 장애 복구, rollback 가능성을 함께 검토한다.

20. 새로운 dependency를 추가하기 전에 Rocky 8 및 사내망에서 설치·운영 가능한지 확인한다.
```

---

# 45. 개발 변경 시 Review Checklist

새로운 기능 또는 구조 변경 전 확인한다.

- [ ] Rocky Linux 8에서 동작 가능한가?
- [ ] Docker 없이 설치 가능한가?
- [ ] Python version과 compatible한가?
- [ ] 새로운 native dependency가 있는가?
- [ ] 사내망 offline 설치 가능한가?
- [ ] SELinux에 영향을 받는가?
- [ ] 새로운 port가 필요한가?
- [ ] systemd service가 필요한가?
- [ ] DB migration이 필요한가?
- [ ] HPC interface를 침범하지 않는가?
- [ ] 장시간 작업을 API thread에서 수행하지 않는가?
- [ ] 대형 파일을 DB에 직접 저장하지 않는가?
- [ ] 환경변수로 분리해야 할 값이 코드에 들어가 있지 않은가?
- [ ] Error 상태가 DB에 남는가?
- [ ] Log로 문제를 추적할 수 있는가?
- [ ] Rollback 가능한가?
- [ ] 기존 기능 regression test가 있는가?
- [ ] 향후 AI/MCP extension과 충돌하지 않는가?

---

# 46. Production Acceptance Criteria

Rocky Linux 8 배포 완료의 최소 조건:

- [ ] 서버 재부팅 후 API 자동 기동
- [ ] Worker 자동 기동
- [ ] nginx 자동 기동
- [ ] Frontend 접근 가능
- [ ] API health check 정상
- [ ] PostgreSQL 연결 정상
- [ ] DB migration 정상
- [ ] HPC SSH 연결 정상
- [ ] Test Job 제출 가능
- [ ] HPC Job ID DB 기록
- [ ] Job status 갱신
- [ ] 결과 수집
- [ ] 후처리 정상
- [ ] Dashboard 결과 표시
- [ ] 영상/미디어 표시
- [ ] Error 발생 시 log 확인 가능
- [ ] SELinux enforcing 환경에서 정상
- [ ] 필요한 port만 firewalld open
- [ ] Credential source에 포함되지 않음
- [ ] 서버 재시작 시 데이터 손실 없음
- [ ] 이전 release rollback 가능

---

# 47. 우선순위

현재 단계에서 우선순위는 다음과 같다.

```text
1. WSL Ubuntu로 현재 Dashboard 이전
2. Rocky 8 Python Version 확정
3. 기존 기능 parity 확보
4. Dependency lock
5. Backend 구조 정리
6. Worker 분리
7. PostgreSQL Job 상태 모델 확립
8. HPC Interface 분리
9. nginx + systemd 배포 구조 구축
10. Rocky Test Server 검증
11. 결과/영상 Storage 표준화
12. DB 이력/Validation 강화
13. MCP/Embedding/Graph DB
14. Local LLM / Engineering Agent
```

AI 기능보다 먼저 **신뢰 가능한 CAE workflow와 데이터 추적성**을 확립한다.

---

# 48. 최종 원칙

이 프로젝트의 운영 환경은 Docker가 없는 Rocky Linux 8이므로, 개발 편의성보다 다음 네 가지를 우선한다.

```text
Reproducibility
Traceability
Deployability
Maintainability
```

개발환경은 Ubuntu를 적극 활용하되 운영환경과의 차이를 항상 의식한다.

최종적으로 시스템은 다음 특성을 가져야 한다.

> 사용자는 하나의 Dashboard에서 설계/해석 정보를 조회하고, 해석 Case를 생성하며, 대량 해석을 HPC에 제출하고, 진행 상태와 결과를 추적하고, 시험/물성/설계 이력과 함께 판단할 수 있어야 한다. 이후 AI는 이 검증된 데이터와 workflow 위에서 검색·분석·의사결정을 지원한다.

즉, **AI를 먼저 붙이는 플랫폼이 아니라 AI가 신뢰하고 사용할 수 있는 Engineering Data & Workflow Platform을 먼저 구축한다.**
