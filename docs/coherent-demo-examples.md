# 완결형 예제 프로젝트 안내

- 상태: 현재 구현 기준
- 기준일: 2026-09-01
- 구현 위치: `backend/app/database.py`의 `seed_database()`, `ensure_feature_examples()`

## 목적

예제 프로젝트는 프로젝트를 선택한 직후 최소 하나 이상의 해석 의뢰가 보여야 한다. 각 의뢰는 하중 경우와 작업 단계까지 연결한다. 결과 파일·Run은 해석을 수행한 하중 경우에만 존재하며, 아직 실행하지 않은 의뢰나 하중 경우에 결과가 없는 것은 정상 상태다.

### 기준 업무 흐름

예제와 이후 개발은 다음 흐름을 기준으로 한다. **SPDM의 접수 원천**과
**Altair One의 작업 공유폴더**는 같은 장소나 같은 개념이 아니다.

```text
SPDM에서 의뢰 접수 (의뢰 번호·요청 내용의 원천)
  → Altair One 공유폴더의 의뢰별 작업폴더를 생성 또는 검증 후 연결
  → 담당 실무자가 작업 수행
  → 결과를 v1, v2, ... 불변 Run으로 정리·적재
  → 사용자가 대시보드에서 표시할 결과 버전을 선택
```

현재 예제에는 프로젝트→의뢰→하중 경우→작업 단계→완료 Run/결과의 연결과 실행 전
의뢰의 결과 없음 상태가 구현되어 있다. `analysis_runs.run_no`는 결과 버전 역할을
하며, 일반 상세 대시보드에서 Run을 선택하면 해당 `run_id`의 overview가 표시된다.
결과 Run이 없으면 선택기는 비활성 상태로 남아 결과 대기 상태를 유지한다. 반면
**SPDM 실연계**와 **Altair One 의뢰 작업폴더를 DB에 생성·추적하는 binding**은 아직
구현 범위가 아니다. 따라서 예제의 출처 문자열이나 결과 파일명을 실제 SPDM/Altair
One 연동 완료로 해석하면 안 된다.

임의로 생성한 일반 프로젝트는 자동으로 의뢰를 넣지 않는다. 실제 업무에서는 프로젝트 등록 후 의뢰를 접수하는 시점이 다를 수 있으므로, 빈 프로젝트 자체는 유효하다. 반면 아래 두 프로젝트는 항상 완결된 예제 카탈로그로 유지한다.

## Canonical 예제

| 프로젝트 ID / 화면 이름 | 의뢰와 하중 경우 | Run·결과 상태 | 확인 목적 |
|---|---|---|---|
| `project-tv-001` / **Orion 65 TV 포장 신뢰성** | 낙하 응력 평가(`request-drop-001`)와 Side Clamp 평가(`request-clamp-001`)가 각각 하중 경우·작업 단계와 연결됨 | 낙하 하중 경우는 완료 Run(버전)과 정량·시계열·컨투어·대시보드 결과가 있음. Side Clamp는 `READY`라 결과가 없음 | 실제 업무에 가까운 접수→작업→버전별 결과와 아직 실행 전인 의뢰를 함께 확인 |
| `project-feature-showcase` / **Analysis Canvas 기능 예제 모음** | 비교, 신뢰도, 검토, 다중 결과형, 데이터 대기, 워크플로 등 7개 의뢰가 각각 하중 경우·10단계 작업과 연결됨 | 비교·신뢰도·검토·다중 결과형은 완료 Run과 결과가 있음. `waiting`과 `workflow`는 결과 생성 전 상태를 의도적으로 보존 | 화면별 정상·경고·대기·차단 상태를 안전하게 재현 |

`loadcase-showcase-waiting`의 변수 선언만 있고 실제 결과가 없는 상태, `request-showcase-workflow`의 진행·차단·대기 상태는 누락이 아니라 의도된 예제다. 결과를 임의로 채우지 않아야 “해석 수행 전에는 결과가 없다”는 실제 흐름도 검증할 수 있다.

`Vertical Slice Project`는 화면에 보여 주는 canonical 예제가 아니라 프로젝트 생성 API의 격리 테스트에서 쓰는 fixture 이름이다. 테스트 DB 밖의 예제 카탈로그로 취급하거나 운영 데이터에 의뢰를 자동 추가하지 않는다.

## 로컬 DuckDB 확인

기본 로컬 실행은 `ANALYSIS_DB_BACKEND=duckdb`와 `ANALYSIS_DUCKDB_PATH` 설정을 사용한다. 새 로컬 DB는 아래 실행으로 초기화되며 canonical 예제 두 프로젝트가 함께 준비된다.

```bash
./start.sh
```

기동 뒤에는 API에서 각 프로젝트의 의뢰가 존재하는지 확인한다.

```bash
curl -s http://127.0.0.1:8000/api/projects
curl -s http://127.0.0.1:8000/api/projects/project-tv-001/requests
curl -s http://127.0.0.1:8000/api/projects/project-feature-showcase/requests
```

기존 개인 DuckDB에 사용자가 만든 빈 프로젝트가 남아 있어도 자동 변경하지 않는다. 예제 상태를 처음부터 확인하려면 별도 개발용 DuckDB 경로를 설정해 새 파일로 실행한다. 기존 파일을 삭제하거나 덮어쓰는 방식은 사용하지 않는다.

## PostgreSQL에 예제 적재

PostgreSQL에서는 앱 시작이 예제를 자동 적재하지 않는다. migration을 적용한 뒤, `ANALYSIS_DB_BACKEND=postgresql` 및 필요한 연결 설정이 준비된 환경에서만 아래 명령을 명시적으로 실행한다.

```bash
PYTHONPATH=backend ./.venv-wsl/bin/python backend/scripts/upgrade_postgres_schema.py
PYTHONPATH=backend ./.venv-wsl/bin/python backend/scripts/seed_database.py --mode demo
```

이 명령은 idempotent reference/demo fixture를 적재한다. 연결 문자열·비밀번호·owner 자격 증명은 문서나 셸 이력에 남기지 말고 배포 환경의 보호된 설정을 사용한다. 이 저장소에서 PostgreSQL 명령의 실제 사내 실행은 검증하지 않았으므로, 사내에서는 별도 전용 DB에서 실행 결과와 row count를 기록한 뒤 적용한다. 자세한 운영·migration 절차는 [`backend-sql-integration-guide.md`](backend-sql-integration-guide.md)와 [`personal-laptop-to-corporate-release-handoff.md`](personal-laptop-to-corporate-release-handoff.md)를 따른다.
