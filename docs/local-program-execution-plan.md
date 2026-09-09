# 로컬 프로그램 검색·선택 및 작업 실행

- 기준일: 2026-09-08
- 상태: 로컬 구현 및 검증 완료. 2026-09-09부터 연결·인증·이력 공유는 [계정별 PC 운영 계획](managed-local-execution-plan.md)이 이 문서의 공통 연결 코드 방식을 대체한다. 클라우드는 계획만 보유.
- 사용자 요청: 작업 키워드(예: 메시 수정)로 HyperMesh 2024.1/2025.1 등 등록·설치된 프로그램 버전을 검색하고 사용자가 선택하면 해당 PC의 경로를 적용한다.
- 우선순위: 이번 사용자 요청은 기존 배치 프로필의 작업 유형 버전별 1:1 연결을 로컬 프로그램 선택의 제약으로 사용하지 않는다. 기존 DEMO_ONLY 실행과 중앙 업무 상태 계약은 보존한다.

## 구현 범위

1. 독립 로컬 실행 도우미: 내 PC의 프로그램 등록, 제한된 설치 탐색, 파일/폴더 선택, 실행과 로컬 이력 저장.
2. 프로그램은 이름+버전+PC별 항목이며 여러 키워드를 가진다. 발견 후보는 명시적으로 등록한 후 실행한다. 미설치 버전을 예제 데이터로 실제 후보처럼 표시하지 않는다.
3. 작업 실행 화면에서 직접 작업/일괄 실행, 키워드 검색, 버전 선택, 기본값 기억, 입력 경로와 작업 폴더 적용을 제공한다.
4. 직접 실행은 프로세스 종료와 사용자 작업 완료를 구분한다. 일괄 실행은 항목별 입력 파일/작업 폴더와 상태를 가지며 순차 실행, 목록 저장/불러오기, 실패 항목 재실행을 우선 제공한다.
5. 이력은 실행 당시 프로그램/인자/경로 snapshot, 시각, 상태, 오류, 메모, 의뢰/작업 문맥을 보존한다. 재실행은 새 기록이다. 중앙 서버의 의뢰 완료/결과 import를 자동 호출하지 않는다.

## 최초 구현의 실행 경계 (연결·권한은 계정별 PC 운영 계획으로 대체)

- 중앙 웹 서버는 사용자 PC의 경로를 탐색하거나 프로세스를 시작하지 않는다. 별도 Python/FastAPI 도우미가 `127.0.0.1:8766`에서 동작한다.
- 도우미는 bearer 연결 코드와 정확한 허용 웹 origin으로 접근을 제한한다. 서버의 로그인 토큰을 도우미에 전달하지 않는다. 연결 코드는 URL에 넣지 않고 브라우저 세션에만 보관한다.
- 로컬 개인 프로그램과 실행 이력은 도우미의 SQLite DB에 저장한다. 웹 계정/의뢰 문맥은 사용자가 전달한 참고 정보이며 중앙 서버 감사 기록 또는 권한 검증으로 간주하지 않는다.
- 명시적 사용자 실행으로 등록된 실행 파일만 `shell=False`와 인자 배열로 실행한다. 입력 경로/작업 폴더 존재 여부를 실행 직전에 검사한다. 자동 검색·선택은 실행을 발생시키지 않는다.
- 최초 실행 지원은 Windows `.exe` 및 비 Windows 실행 파일이다. 임의 shell 문자열 및 `.bat/.cmd/.ps1` 실행은 이번 범위에 포함하지 않는다. 프로그램별 파일 열기 인자는 등록의 고급 설정에서 token 배열로 정의한다.
- 설치 탐색은 Windows 등록 정보와 알려진 설치 위치에 한정한다. 디스크 전체 재귀 탐색은 하지 않으며 찾지 못한 프로그램은 실행 파일 선택으로 등록한다.
- 도우미 재시작 시 실행 중 기록을 성공으로 간주하지 않고 연결 종료/상태 확인 필요로 복구한다. 기존 프로세스 강제 종료는 하지 않는다.

## 공통 API 계약 (`/v1`, 도우미 전용)

중앙 서버 OpenAPI와 분리된 typed client를 `frontend/src/shared/api/localRunner.ts`에 둔다. 도우미의 FastAPI `/openapi.json`이 독립 계약이다.

- `GET /health` → `{host_id, host_name, platform}`
- `GET /programs?q=` → Program[]; `POST /programs` → Program; `PUT /programs/{id}` → Program; `DELETE /programs/{id}` → `{ok:true}`. 수정은 같은 등록 입력을 받으며 과거 실행 snapshot을 바꾸지 않는다.
- Program: `{id,name,version,keywords:string[],executable_path,arguments:string[],host_id,available:boolean}`. 등록 입력은 id/host_id/available 제외. arguments는 `{input}`만 치환하며 기본 `[]`는 파일 인자를 자동 추가하지 않는다.
- `POST /discover` → ProgramCandidate[] (`name,version,keywords,executable_path,arguments`)
- `POST /pick` body `{kind:'program'|'files'|'directory'}` → `{paths:string[]}`
- `POST /runs` body `{program_id,mode:'DIRECT'|'BATCH',items:[{input_path,working_directory}],context:{request_id,work_item_id,task_name,actor},idempotency_key}` → Run[]
- `GET /runs?request_id=&work_item_id=` → Run[]
- Run: `{id,source_run_id?,batch_id,mode,status,program_name,program_version,input_path,working_directory,created_at,started_at,completed_at,exit_code,note,error,context,program_snapshot}`; status `QUEUED|RUNNING|AWAITING_COMPLETION|SUCCEEDED|FAILED|COMPLETED|INTERRUPTED`. source_run_id는 재실행 원본 기록을 가리킨다.
- `POST /runs/{id}/complete` body `{note}` → Run. DIRECT의 AWAITING_COMPLETION만 사용자 완료로 전환한다. BATCH의 SUCCEEDED는 exit code 0을 뜻하며 결과 내용의 정합성을 보장하지 않는다.
- `POST /runs/{id}/retry` body `{idempotency_key}` → Run[]. FAILED/INTERRUPTED 기록의 당시 프로그램 snapshot·입력·문맥으로 새 실행을 만든다. 현재 선택 프로그램이나 나중에 수정된 등록 설정을 대신 사용하지 않으며 실행 전 경로를 다시 검사한다.
- `GET /batches` → `{id,name,program_id,items}[]`; `POST /batches` body `{name,program_id,items}` → saved batch.

동일 idempotency key는 같은 요청만 재사용하고 다른 payload는 거부한다. 배치 최대 100개, 입력/인자/메모 길이를 제한한다. 경로는 내 PC native 경로이며 서버 업로드 파일명으로 대체하지 않는다.

직접 작업은 입력 없이 프로그램만 열 수 있다. 빈 작업 폴더는 실행 파일의 부모 폴더를 사용하고, `{input}` 인자가 있으면 입력을 필수로 검증한다. 일괄 실행은 각 항목에 입력 파일을 요구한다. 버전을 읽지 못한 설치 후보는 `버전 확인 필요`로 표시하고 사용자가 등록 시 수정한다.

파일별 일괄 실행은 프로그램 인자에 `{input}` 매핑이 있어야 한다. 입력 파일만 바꿔 동일한 무인자 명령을 반복 실행하지 않도록 UI/API/실행기에서 검사한다. 직접 작업에서 파일 전달 설정이 없으면 프로그램만 열리는 점과 설정 수정 경로를 안내한다.

도우미는 시작한 프로세스의 종료를 관찰한다. 실행 파일이 별도 GUI 프로세스를 만든 뒤 먼저 종료하는 프로그램은 모든 창의 종료를 판별할 수 없으므로 사용자 완료 확인을 유지한다. 연결 코드가 있는 native CLI는 Origin 없는 loopback 요청을 사용할 수 있으며 브라우저가 보낸 Origin은 정확한 허용 목록으로 검증한다.

## 화면 및 기본값

- 기존 작업 문맥과 실행 권한 안내를 유지한다. 로컬 도우미 영역은 기존 데모 기능과 명확히 구분한다.
- 프로그램 목록에 이름, 버전, PC, 사용 가능 여부를 표시한다. 후보가 복수이면 사용자가 선택한다. 키워드 검색은 공백/대소문자를 정규화하며 한국어 키워드와 버전도 검색한다.
- 이름/키워드 매칭은 후보 추천이다. 이전 명시 선택을 기억한 경우만 자동 선택한다. 기본값 key에는 사용자+host+작업 유형을 포함한다.
- 상세 경로/인자와 프로그램 관리는 접힌 영역에서 제공한다. 연결되지 않음, 후보 없음, 경로 없음, 실행 실패를 각각 조치 가능한 문구로 안내한다.
- 브라우저/작업 화면 종료는 프로세스 종료 또는 업무 완료가 아니다.

## 클라우드 후속 계획 (미구현)

같은 프로그램 식별/버전/키워드 개념을 재사용한다. 실제 클라우드 실행에는 별도 실행기와 파일 전달/결과 회수, 원격 GUI 또는 스케줄러, 취소·리소스 제한이 필요하다. 클라우드 실행 연결 코드는 구현하지 않는다. 로컬 PC의 중앙 권한 검증과 이력 동기화는 계정별 PC 운영 변경에 포함하며, 병렬 배치·의존성·결과 자동 수집은 후속 단계다.

## 수용 검증

- HyperMesh 두 버전을 테스트 fixture로 등록하고 메시/HyperMesh/버전 검색 및 선택 경로 적용 확인.
- 잘못된 토큰/origin/host, shell 파일, 없는 경로, 잘못된 인자 거부.
- 무해한 테스트 프로세스로 직접 실행 종료/사용자 완료 분리, 순차 배치 부분 실패, 재실행/중복 제출과 재시작 복구 확인.
- 목록·이력 영속성, 실행 snapshot 보존, 문맥 전환 stale response 차단 확인.
- frontend build/architecture/API checks 및 실제 브라우저 데스크톱/좁은 화면 확인. Browser plugin 미제공이므로 저장소 Playwright 사용.
- 실제 HyperMesh 라이선스·설치 버전별 인자와 원격/클라우드 환경은 별도 현장 검증이며 테스트 fixture와 구분한다.

## 담당 경계

- Astra: 계약, 개발문서, 조립·최종 검증.
- Terra: `local_runner/`, 로컬 도우미 테스트와 실행 script.
- Luna: `frontend/src/features/workbench/local-programs/`, shared local API adapter, 기존 작업 화면 최소 연결.
- Sol: 독립 검수, 권한/실행/상태/문맥 오류 검토.

## 최초 구현 파일과 사용 순서 (관리 모드는 계정별 PC 운영 계획 참조)

| 책임 | 구현 |
|---|---|
| 로컬 도우미 API·schema | `local_runner/server.py`, `local_runner/models.py` |
| 프로그램·배치·이력 저장 | `local_runner/storage.py` |
| 실행·경로 검증 | `local_runner/executor.py` |
| 설치 탐색·파일 선택 | `local_runner/discovery.py`, `local_runner/picker.py`, `local_runner/picker_helper.py` |
| 기동·중복 기동 방지 | `local_runner/__main__.py`, `local_runner/instance_lock.py`, `start-local-runner.ps1`, `start-local-runner.bat` |
| 작업 화면 | `frontend/src/features/workbench/local-programs/LocalProgramPanel.tsx`, `local-programs.css` |
| 도우미 API client | `frontend/src/shared/api/localRunner.ts` |
| 자동 검증 | `local_runner/tests/`, `frontend/e2e/local-programs.spec.ts` |

1. 저장소의 기존 Windows 설치를 마친 PC에서 `start-local-runner.bat`을 실행한다. `.venv-runtime`의 기존 FastAPI/uvicorn과 Python 표준 라이브러리를 사용하며 추가 패키지는 없다.
2. 작업 실행 화면의 `내 PC 프로그램으로 작업 실행`에 도우미 콘솔의 연결 코드를 입력한다. 연결 실패 시 도우미 실행 여부와 허용 웹 주소를 확인한다.
3. `설치 후보 발견`에서 실제 발견된 버전을 등록하거나 `프로그램 등록`에서 실행 파일·이름·버전·키워드를 등록한다. 현재 자동 탐색의 기본 제공 대상은 HyperMesh이고 다른 프로그램은 수동 등록한다.
4. `메시 수정`, `HyperMesh`, `2024.1` 등으로 검색해 원하는 버전을 선택한다. 필요한 경우 `이 작업 유형의 기본 프로그램으로 기억`을 체크한다. 해제하면 저장한 기본값도 제거한다.
5. 직접 작업은 프로그램만 열거나 프로그램별 파일 전달 설정과 함께 입력 파일을 연다. 일괄 실행은 파일 선택창에서 여러 파일을 고르면 각 파일이 행이 되며 각 행의 작업 폴더를 변경할 수 있다. 목록 저장/불러오기를 지원한다.
6. 직접 실행 종료 후 로컬 `업무 완료 표시`와 메모를 남긴다. 일괄 실행은 프로세스 종료 상태를 항목별로 표시하며 실패/중단 항목은 원본 설정으로 새로 실행한다.

기본 도우미 주소는 `http://127.0.0.1:8766`이며 UI도 이 주소를 사용한다. 런처의 Port 변경은 개발/진단용이고 UI의 별도 포트 설정은 이번 범위에 없다. 허용 웹 주소의 기본값은 `http://127.0.0.1:5173`, `http://localhost:5173`이다. 다른 웹 주소는 PowerShell의 `-Origin`에 정확한 origin을 지정한다. 웹을 원격 서버에서 제공할 때의 브라우저 로컬 네트워크 권한·HTTPS 연결 정책은 별도 환경 검증 대상이다.

Windows 런처의 개인 저장 위치는 `%LOCALAPPDATA%/SimulationWorkbench/local-runner`이며 이력 DB, 연결 코드 설정과 기동 로그가 들어간다. 도우미를 다시 실행하면 같은 코드의 정상 실행 상태를 확인하고 재사용한다. 실행 중 상태를 복구하기 전에 데이터 폴더의 OS lock을 획득한다. 테스트 데이터는 저장소의 git 제외 `.local-runner/browser-test-*`와 `backups/local-program-qa/`에 격리한다.

## 검수에서 반영한 변경

- 실행 파일이 이동/삭제되면 목록의 가용성을 다시 계산하고 시작 직전에 검증한다.
- 응답이 유실된 제출은 같은 idempotency key로 확인하며 명시적인 다음 실행은 새 기록을 만든다.
- 프로그램 등록을 변경/삭제해도 재실행은 원본 snapshot을 사용하며 다른 PC snapshot은 거부한다.
- 파일 전달 인자가 없는 일괄 실행은 차단하고 직접 실행에는 프로그램만 열린다는 안내를 제공한다.
- 작업/계정 전환, 기본값 해제, 복수 파일 선택, 파일 선택 취소, 좁은 화면의 내부 잘림을 보완한다.
- 페이지 전체의 DEMO 표시를 기존 데모 예제 버튼으로 한정해 실제 로컬 실행과 구분한다.

검증 결과: 로컬 도우미 테스트 16개, 신규 실행 흐름과 기존 DOE/Viewer 브라우저 회귀 3개 통과. 최종 버전·탭 표시 조정 후 신규 브라우저 흐름 1개도 재통과했다. TypeScript·production build·architecture·API 검사는 통과했으며 자세한 환경과 실제 HyperMesh 미검증 범위는 [검증 기록](local-program-execution-qa.md)에 남겼다.
