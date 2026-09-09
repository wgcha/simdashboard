# 내 PC 설정 검증 기록

- 날짜: 2026-09-09
- 구현 기준: [내 PC 설정 계획](personal-pc-settings-plan.md)
- 결과: 로컬 구현 및 검증 완료. 검증 당시에는 커밋·push 전이었으며, 후속 Git 게시 요청에 따라 이 기록을 구현과 함께 반영한다. 사내 배포 및 사내 PostgreSQL 검증은 별도다.

## 자동 검증

- TypeScript 빌드, 프런트엔드 구조 검사, 라우팅·기본 메뉴 자체 검사 통과.
- Vite 운영 빌드 통과. 기존 대형 청크 경고는 남아 있다.
- 시작 파일 자체 검사 6건 통과: 주소 검증, 비밀값 없는 설정, PowerShell 5.1 구문, 한글·공백 경로에서 BAT 실행, 자동 시작 인수, 실패 종료 코드 전달. 가짜 실행기를 사용하여 실제 Windows 자동 시작 설정은 변경하지 않았다.
- 기존 `local-programs.spec.ts` 통과: 실제 로컬 도우미와 중앙 테스트 API를 이용한 실행·이력 회귀 검증.
- Git 게시 전 PostgreSQL 시작·이관·중앙 PC 연결 테스트 100건 통과(55.41초): `test_postgres_startup.py`, `test_duckdb_postgres_migration.py`, `test_managed_local_execution.py`, `test_managed_local_execution_security.py`. PostgreSQL 시작 계약은 모의 연결로 검증하며 실제 운영 DB migration 성공을 뜻하지 않는다. 백엔드 구조 검사도 통과했다.
- 신규 `personal-pc.spec.ts` 통과: 프로젝트 없는 일반 사용자 기본 메뉴, 오프라인 안내·다운로드, 재확인·연결, 프로그램 등록·수정·키워드 검색·삭제, 새로고침 후 자동 연결, 다른 계정의 별도 연결 및 카탈로그 분리, 연결 해제.
- 검수에서 발견한 취소된 조회의 로딩 상태 고착, 프로그램 조회와 수정의 경합, 실행 파일 입력의 접근성 이름을 수정했다.

## 브라우저·화면

- Browser plugin not available: 해당 Browser 플러그인/스킬이 없어 일반 Playwright Chromium으로 검증했다.
- 주소: `http://127.0.0.1:15173/workspace/settings/local-pc` (중앙 테스트 API 포트 18000, 임시 로컬 도우미 포트 8766).
- 데스크톱 1440×960 및 모바일 390×844에서 확인했다. 모바일 가로 넘침 검사 통과. 연결 상태, 기본 메뉴, 프로그램 카드와 설정 버튼 배치를 스크린샷으로 확인했다.
- 신규 시나리오에서 수집한 페이지 JavaScript 예외는 없었다. 초기 로그인 전 인증 실패와 도우미 미실행 요청 실패는 상태 확인 과정이며, 모든 콘솔 메시지가 없다는 검증은 하지 않았다.
- 화면 증거는 작업의 시각화 폴더 `personal-pc` 아래 `personal-pc-first-setup.png`, `personal-pc-connected-desktop.png`, `personal-pc-mobile.png`에 저장했다. 프로그램 이름의 HyperMesh는 Python 실행 파일을 사용한 테스트 데이터이며 실제 HyperMesh 실행 검증을 뜻하지 않는다.

## 검증 범위

중앙 DB는 격리된 E2E DuckDB를 사용했다. 실제 사내 PostgreSQL, 사내 보안 정책, 실물 HyperMesh 설치 탐지, 새 PC 전체 설치 및 Windows 로그인 시 자동 실행은 이번 검증에 포함하지 않았다. 도우미의 네이티브 승인은 테스트 fixture를 사용했다. 사용자가 받는 시작 파일은 기존 Workbench 배포 폴더와 Python 환경이 필요하다.

테스트에서 시작한 프런트엔드·백엔드와 임시 도우미는 종료했다.
