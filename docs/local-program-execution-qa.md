# 로컬 프로그램 실행 검증 기록

- 기준일: 2026-09-08
- 대상: [개발 계획과 실행 계약](local-program-execution-plan.md)
- 상태: 로컬 기능·회귀 및 최종 표시 변경 재확인 통과

## 결과

로컬 도우미의 등록·검색·실행·이력 및 기존 의뢰 작업 흐름을 함께 검증했다. 중앙 서버 DB와 실제 설치 프로그램을 테스트 데이터로 변경하지 않았다. HyperMesh 이름을 붙인 테스트 등록 항목은 두 개의 Python 실행 파일을 사용하며, 실제 HyperMesh 설치·라이선스 검증을 의미하지 않는다.

## 환경

- Windows, 저장소의 Python 3.12 runtime 및 Node 22 runtime.
- Chromium/Playwright. Browser plugin not available: 해당 plugin/skill이 제공되지 않아 저장소 `scripts/run-e2e.mjs`를 사용했다.
- 웹 `http://127.0.0.1:15173`, 격리된 중앙 API `http://127.0.0.1:18000`, 테스트 도우미 `http://127.0.0.1:8766`.
- 화면: 1280×720 dark, 390×844 dark, 1440×960 light.
- 테스트용 중앙 DuckDB와 개인 도우미 SQLite 사용. 테스트 서버는 종료 시 정리한다.

## 확인한 흐름

`작업 실행 → 도우미 연결 → 메시 수정/버전 검색 → HyperMesh 2024.1 선택 → 파일/폴더 적용 → 실제 무해한 프로세스 실행 → 사용자 완료 메모 → 복수 파일의 순차 실행 → 부분 실패 → 원본 설정 재실행`.

네이티브 파일 선택창 자체는 브라우저 테스트에서 응답만 대체한다. 선택된 모든 경로가 배치 행으로 추가되는 UI는 검증하며, 프로그램 등록·프로세스 실행·이력 API는 실제 도우미를 사용한다.

| 항목 | 결과 | 근거 |
|---|---|---|
| 페이지 식별 | 통과 | 실행 URL, 비어 있지 않은 title |
| 빈 화면/프레임워크 오류 | 통과 | 프로그램 패널·후보·입력 표시, Vite overlay 없음 |
| 콘솔 오류 | 통과 | 페이지/실행 오류 없음; 로그인 전 `/auth/me`의 예상 401만 제외 |
| 버전 선택 | 통과 | 메시 수정 검색 결과 2개, 2025.1 검색 결과 1개, 실행 기록의 실제 경로는 선택한 2024.1 항목 |
| 직접 실행 완료 분리 | 통과 | 프로세스 exit 0 후 AWAITING_COMPLETION, 사용자 확인 후 COMPLETED |
| 파일 다중 선택·목록 저장 | 통과 | 선택한 2개 경로가 각각 행으로 추가, 저장 목록 표시 |
| 부분 실패 | 통과 | 동일 batch ID에서 SUCCEEDED와 FAILED 각각 1건 |
| 과거 설정 재실행 | 통과 | 원본 등록을 수정하고 다른 버전을 선택한 뒤에도 원본 버전·인자와 exit 3 유지, source_run_id 연결 |
| 좁은 화면 | 통과 | document와 패널 내부 너비 및 모든 입력/버튼의 수평 경계를 확인 |
| 기존 작업 회귀 | 통과 | DOE 의뢰 접수·명시적 시작/완료·데모 배치, Viewer 실행 제한 |

## 자동 검증

- `python -m pytest local_runner/tests -q`: **16 passed**. 실행 순서/부분 실패, 직접 무입력 실행, 등록 키워드/버전, 후보 중복 제거, 미확인 버전, bearer/Origin/Host/query/body, snapshot 보존, 재시작, 중복 기동 lock, 중복 제출/재시도/PC 경계, 파일 매핑 없는 배치 차단 포함.
- TypeScript `tsc -b`: 통과.
- Vite production build `--configLoader runner`: 통과. 기본 config bundling은 sandbox에서 node_modules 임시 파일 EPERM이 발생하여 Vite의 runner 설정 로더로 빌드했다. 기존 큰 bundle 안내는 남아 있으며 빌드 실패가 아니다.
- frontend architecture 및 shared API self-test: 통과.
- Playwright `local-programs.spec.ts` 및 기존 `workbench-demo.spec.ts`의 DOE/Viewer 회귀: **3 passed (47.6s)**.
- 최종 버전 표시/선택 탭 강조 후 `local-programs.spec.ts` 재검증: **1 passed (17.1s)**.
- PowerShell 기동 script: 구문 및 공백/끝 backslash 인자 quoting 확인. 실제 사용자용 런처를 실행하거나 연결 코드를 출력하지 않았다.

## 수정한 검증 발견 사항

- 등록 수정 후 다른 프로그램으로 재실행되는 문제를 원본 snapshot 기반 retry API로 해결했다.
- 직접 실행에 입력을 강제하던 제한을 제거하고, 파일별 배치에는 파일 전달 인자를 요구했다.
- 복수 파일 선택 시 첫 파일만 남는 동작과 파일 선택 취소 시 값이 비워지는 동작을 수정했다.
- 화면 전체 DEMO 표기를 기존 데모 예제에 한정했다.
- 다크/라이트 theme 변수를 적용하고, 작은 화면에서 긴 PC 정보와 입력 영역이 내부에서 잘리는 문제를 해결했다.

## 증거와 남은 환경 검증

스크린샷은 git 제외 `backups/local-program-qa/`에 저장한다. 이름·버전은 테스트 등록이며 이미지의 Python 경로가 실제 실행 대상이다.

- [Dark 버전 선택](../backups/local-program-qa/local-program-selection-desktop.png)
- [모바일 선택 화면](../backups/local-program-qa/local-program-batch-mobile.png)
- [Light 일괄 실행 화면](../backups/local-program-qa/local-program-light-desktop.png)
- [프로덕션 빌드 로그](../backups/local-program-qa/build.log)
- [최종 표시 확인 로그](../backups/local-program-qa/browser-final.log)

실제 HyperMesh의 설치별 실행 인자, 라이선스, Tk 파일 선택창의 사용자 조작, 외부 웹 origin에서 브라우저의 로컬 네트워크 접근 허용은 현장 검증 대상이다. 클라우드 권한/실행, 병렬 배치, 중앙 서버 이력 동기화와 결과 자동 수집은 구현 범위에 포함하지 않았다.
