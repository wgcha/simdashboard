# Windows 새 PC: PostgreSQL 최초 설정 가이드

- 기준일: 2026-10-13
- 대상: 대시보드를 **처음 설치하는 Windows PC**(기존 데이터 없음). 기존 DuckDB 이관은 [Windows PostgreSQL 빠른 시작](windows-postgresql-quickstart.md), 다른 PC의 데이터를 옮기려면 [PostgreSQL PC 간 이관 가이드](postgresql-pc-transfer-guide.md)를 따른다.
- 요약: PostgreSQL만 설치하고 관리자(`postgres`) 비밀번호를 알고 있으면 된다. DB·계정·`.env`는 `deploy.bat`이 만든다.

## 1. PostgreSQL 설치 (한 번만)

1. PostgreSQL 18 Windows x64 설치 파일을 준비한다(사내 소프트웨어 센터 또는 인터넷 가능한 PC에서 받아 복사).
2. 설치 화면 선택값:

| 화면 | 값 |
|---|---|
| 구성 요소 | PostgreSQL Server, Command Line Tools 필수. pgAdmin은 선택. Stack Builder는 체크 해제 |
| 데이터 폴더 | 기본값 또는 넉넉한 드라이브(예: `E:\PostgreSQL\18\data`) |
| 관리자(`postgres`) 비밀번호 | 새로 정하고 **안전하게 보관**. 대시보드 최초 설정과 백업·복구에 필요 |
| 포트 | `5432`(다른 PostgreSQL이 이미 쓰면 다른 번호, 아래 3단계에서 그 번호 입력) |
| 로캘 | 기본값 |

3. 설치가 끝나면 `services.msc`에서 `postgresql-x64-18` 서비스가 **실행 중**이고 시작 유형이 **자동**인지 확인한다.
4. 명령 프롬프트에서 접속 확인(비밀번호 입력):
   ```
   "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h 127.0.0.1 -p 5432 -U postgres -c "select version();"
   ```
   버전 문자열이 나오면 정상이다.

## 2. 대시보드 받기

- Git이 있으면: `git clone -b <배포 브랜치> https://github.com/wgcha/simdashboard.git E:\simulation_workbench`
- Git이 없으면: GitHub에서 배포 브랜치를 선택해 **Code → Download ZIP** → 압축 해제. 나중에 `update.bat` 전에 `set SIMDASH_UPDATE_BRANCH=<배포 브랜치>`를 지정한다([Windows Git 업데이트 안내](windows-git-update.md)).

## 3. `deploy.bat` 실행 (DB 자동 생성)

`E:\simulation_workbench\deploy.bat`을 실행하면 런타임을 준비한 뒤 PostgreSQL 관리자 연결을 묻는다.

| 질문 | 입력 |
|---|---|
| `PostgreSQL administrator host [127.0.0.1]` | Enter (`127.0.0.1`) — **비밀번호를 여기에 넣지 않는다** |
| `PostgreSQL administrator port [5432]` | Enter (또는 1단계에서 정한 포트) |
| `PostgreSQL administrator user [postgres]` | Enter |
| `PostgreSQL administrator password` | `postgres` 비밀번호(화면에 표시되지 않음) |

`deploy.bat`이 하는 일:
- DB `simulation_dashboard`와 전용 역할 `simdashboard_owner`(migration용)·`simdashboard_app`(실행용)을 만들고 무작위 비밀번호를 정한다.
- 실행용 연결을 `.env`(`DATABASE_URL`)에, migration용 연결을 `.postgres-owner.env`에 저장한다. 관리자(`postgres`) 비밀번호는 저장하지 않는다.
- 스키마를 최신으로 만든 뒤 **최초 관리자 계정**(아이디·비밀번호)을 묻는다.

끝나면 `start.bat`으로 실행하고 브라우저에서 로그인한다. 확인: `Invoke-RestMethod http://127.0.0.1:8000/api/health` → `database_backend: postgresql`.

## 4. 꼭 보관할 것

| 파일·값 | 이유 |
|---|---|
| `postgres` 관리자 비밀번호 | 재설치·복구·백업 도구 |
| `E:\simulation_workbench\.env`, `.postgres-owner.env` | DB 연결 정보. 지우면 앱이 DB에 접속하지 못한다. Git·메일·채팅에 올리지 않는다 |
| `backend\backups\` | 업데이트 전 자동 DB 백업 |

## 5. 자주 나는 문제

| 증상 | 원인 | 해결 |
|---|---|---|
| `OperationalError … password authentication failed` | 관리자 비밀번호 오입력 | 창을 닫고 `deploy.bat` 다시 실행 |
| `OperationalError … Connection refused` | 서비스 꺼짐·포트 다름 | `services.msc`에서 서비스 시작, 포트 확인 |
| `could not translate host name` | host 칸에 다른 값 입력 | host는 Enter(`127.0.0.1`) |
| `Target database simulation_dashboard already contains … Automatic setup stopped` | 같은 PostgreSQL에 이미 대시보드 DB가 있음 | 정상 보호 동작. 같은 서버에는 대시보드 DB를 하나만 둔다(DB 이름 고정). 기존 설치를 쓰거나 다른 PostgreSQL(다른 포트)을 쓴다 |
| `Dedicated application roles exist without the target database` | 이전 시도에서 역할만 남음 | pgAdmin에서 `simdashboard_owner`·`simdashboard_app` 역할 삭제 후 재실행(데이터 없는 경우만) |
| `pg_dump … 실행 파일을 찾지 못했습니다` | PostgreSQL `bin`이 PATH에 없음 | `.env`에 `POSTGRES_BIN=C:\Program Files\PostgreSQL\18\bin` 추가 |
| `.setup-recovery-required.json` 생김 | 설정 중단 | 파일 안내를 먼저 확인. 임의로 지우지 않는다 |

## 6. 다음 단계

- SCX 드라이브를 쓰는 PC: [SCX 드라이브 기능 안내](features/scx-drive.md) §2(어댑터 휠·워커 런타임·`.env` 설정·토큰 등록).
- 업데이트: `update.bat`(DB 백업 → migration → 재시작 자동).
