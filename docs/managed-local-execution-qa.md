# 계정별 PC 운영 검증

기준일: 2026-09-09. 운영 계약은 [개발 계획](managed-local-execution-plan.md)을 따른다.

## 검증 환경과 경계

- Windows 로컬 개발 환경, 기존 password 인증과 별도 DuckDB 테스트 데이터 사용.
- 브라우저에서는 실제 중앙 FastAPI 서버와 실제 loopback 도우미를 연결한다. HyperMesh 버전 후보는 무해한 Python 실행 파일로 구성한 fixture다. 회사 HyperMesh 설치나 라이선스를 사용하지 않는다.
- 최초 네이티브 승인창은 브라우저 fixture의 주입 콜백으로 승인한다. 실제 운영 코드에는 승인 생략 CLI를 두지 않는다. 파일 선택창만 fixture 응답으로 대체하며 인증·프로그램 등록·실행·배치·상태·중앙 동기화는 실제 API를 사용한다.
- Browser plugin이 제공되지 않아 저장소의 Playwright를 사용한다. 브라우저 데이터와 도우미 DB는 `.local-runner/browser-test-*`, 중앙 DB는 `backend/data/e2e-playwright.duckdb`, 화면 증거는 `backups/local-program-qa`에 격리한다.
- 실제 회사 OIDC 공급자 연결, 사내 HTTPS 프록시, 각 PC의 브라우저 로컬 네트워크 접근 정책, PostgreSQL 운영 계정은 이 로컬 검증 환경과 구분한다.

## 구현 검수에서 확인한 항목

- 사용자-PC 연결용 2분 일회성 요청과 10분 세션을 분리하고 날짜를 UTC로 직렬화한다.
- 연결별 카탈로그와 배치·이력을 분리한다. 기존 공통 코드 DB를 자동 반입하지 않는다.
- 중앙 실행 권한 확인과 디스크에 기록한 실행 승인을 연결한다. 재실행은 원본 snapshot을 유지하며 새 승인을 사용한다.
- 도우미 재시작 복구, 실행 상태/완료 메모와 재전송 이벤트의 저장 경계, 실행별 순서번호를 검토한다.
- 중앙 이력과 로컬 최신 상태를 합칠 때 오래된 중앙 상태가 최신 로컬 상태를 덮어쓰지 않도록 한다.
- Windows 네이티브 승인창의 subprocess 생성과 자동 시작 설정은 사용자 프로필별로 동작하도록 한다.

## 결과

| 검증 | 결과 |
|---|---|
| 로컬 도우미 pytest 전체 | **22 passed**, 9.24초 |
| 중앙 관리 연결·권한 및 스키마 전송·OpenAPI 회귀 | **32 passed**, 43.15초 |
| 실제 브라우저 관리 연결 + 기존 DOE/Viewer | **3 passed**, 약 1.4분 |
| TypeScript·frontend/backend architecture·shared API | 통과 |
| production build | 통과, 22.44초 |
| Windows 런처 AST 구문 검사 | 통과, 실제 시작 프로그램 변경 없음 |
| Sol 독립 최종 검수 | 범위 내 잔여 차단 결함 없음 |

브라우저에서는 두 계정의 같은 PC 연결, 첫 연결 후 새로고침 자동 복원(재승인 요청 없음), 서로 다른 카탈로그, 타인 binding의 세션 발급 거부, 권한이 있는 다른 계정의 중앙 이력 조회, 연결 해제 후 즉시 접근 차단, 도우미 종료 후 이력/완료 메모 조회까지 통과했다. 프로그램 이름/버전 선택·직접 실행/완료·두 항목 순차 배치·일부 실패·불변 snapshot 재실행·모바일 폭 검증도 포함한다.

로컬 테스트에는 최초 승인/outbox 저장 실패 시 원자적 rollback, 중앙 중단 후 재시도, 재시작 복구, 영구 거부 연결의 재전송 중지, 큰 이벤트 묶음의 바이트 단위 분할과 413 적응, PC 이름 변경 후 카탈로그 유지/재실행을 포함한다. 회사 계정이 중지되거나 실행 담당자/프로젝트 실행 권한이 맞지 않으면 중앙이 거부한다. 조회 정책은 현재 제품의 `PROJECT_DATA_VIEW`(활성 회사 계정 기본 권한)를 그대로 따른다.

처음 브라우저 검증에서 도우미가 꺼지면 이력 UI가 숨겨지는 결함을 발견했다. 이력을 연결 UI 밖으로 분리하고, 작업 문맥별 조회를 하나의 합쳐진 응답으로 처리한 뒤 전체 흐름을 재검증했다. Sol 검수에서 찾은 grant/outbox 원자성·이벤트 크기·영구 거부 반복·PC 이름 변경·시작 바로가기 검증 순서도 수정했다.

재현 명령:

```powershell
.\.venv-runtime\Scripts\python.exe -m pytest local_runner/tests -q
# backend 디렉터리에서
..\.venv-runtime\Scripts\python.exe -m pytest tests/test_managed_local_execution.py tests/test_managed_local_execution_security.py tests/test_duckdb_postgres_migration.py tests/test_openapi_contract_check.py -q
# frontend 디렉터리에서 (저장소 Node/Playwright 환경 사용)
node scripts/run-e2e.mjs local-programs.spec.ts workbench-demo.spec.ts --grep '메시 키워드|DOE 의뢰를 접수|Viewer는 배정'
```

무시된 증거 디렉터리 `backups/local-program-qa`에 `managed-runner-tests.log`, `managed-backend-tests.log`, `managed-browser-final.log`, `managed-build.log`, `local-program-selection-desktop.png`, `local-program-batch-mobile.png`, `managed-local-central-history-offline.png`를 저장했다.

실제 운영 PostgreSQL에 접속해 migration을 적용한 검증이나 실제 회사 OIDC·HyperMesh 라이선스 검증은 수행하지 않았다. 기존 대형 frontend chunk 경고와 도우미 FastAPI `on_event`의 deprecation 경고는 남아 있으며 현재 검증의 실패는 아니다. 클라우드 프로그램 실행은 구현하지 않았다.
