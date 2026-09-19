# 해석 결과 대시보드 구현·사용 안내

기준일: 2026-09-19. 이번 사용자 요청에 따라 [구현 인수인계](구현.md)와 [상세 설계](상세.md)를 프로그램에 반영한다. 두 원본 문서의 과거 ‘문서 작성만’ 제한은 원본 작성 작업의 범위이며, 원본 문서는 보존했다.

## 사용 흐름

1. 기존 의뢰의 **결과 검토** 화면에서 **해석 결과 대시보드**를 연다. 사용환경/유통환경을 선택한다.
2. 관리자는 기존 저장소 설정에 자료 루트를 지정한다. 대시보드의 **원본 폴더 조사**는 설정된 루트 아래만 읽는다. 빈 상대 경로는 `WR_*_SimType1/2` 표준 경로를 조사한다. SPDM 컨테이너처럼 별도 매핑이 필요한 구조는 해당 Case 상대 경로를 명시한다.
3. 조사 후보에서 **Simulation Case**를 선택해 수집 버전을 게시한다. 조사만으로 업무나 결과를 등록하지 않는다. 최초 미연결 폴더 조사·연결에는 기존 저장소 관리 권한이 필요하다. 연결된 Case 재수집은 해당 프로젝트의 결과 가져오기 권한을 검사한다.
4. Case와 **수집 버전(Capture)**을 명시적으로 선택한다. 유통환경은 하중경우·사용자 Run·Mode·Component·집계 기준을 추가 선택한다. `analysis_run` 선택과 별개이다.
5. 요약 엣지 맵, Scene 그래프, 네 엣지 패널, 컨투어 전치 비교표, 거동 슬롯과 Scene 상세에서 결과를 확인한다. 비교 Case마다 선택 문맥을 별도로 고정한다.

## 데이터 계약

- 사용환경은 Settle·Wobble·Horizontal Force Angle·Slope Angle·Slope Angle 360을 한 Case로 조회한다. Settle 공통값, 나머지 전·후방을 구분하고 숫자와 원본 OK/NG를 분리한다.
- 동일 원본의 JSON을 우선하며 JSON이 없을 때만 키–값 CSV를 사용한다. JSON 오류는 CSV로 숨기지 않는다. result2/bushing은 기본 결과와 병합하지 않는다. 음수·0·결측을 구분한다.
- 유통환경은 Case→Drop/Clamping→사용자 Run→Mode→Scene을 보존한다. Mode가 없으면 UNKNOWN이다. `20_...Scene12...`는 순번 20, 시나리오 12이며 DAMP 접두사는 순번 미확인이다.
- 원본 요약과 상세 추출값의 집계를 분리한다. 선택 엣지 envelope에 코너를 넣지 않는다. 부분 자료·동률·원본 근거를 보존한다. 정렬 행의 Node ID를 L1~L4 실제 노드로 간주하지 않는다.
- 수집 버전마다 원본 목록·해시·읽기 규칙 버전·정규화 결과와 CSV/JSON/미디어 바이트를 DB에 보관한다. 반복 게시의 동일 내용은 같은 버전을 반환한다. 파일 추가/변경이 사용자 Run이나 Scene ID를 바꾸지 않는다.
- 자산은 프로젝트 권한을 확인하는 API로 제공하며 영상 Range 요청을 지원한다. 과거 버전 조회에서 현재 원본 파일을 다시 읽지 않는다.
- 사용환경 원본 키의 deg/mm는 보존한다. 유통환경 CSV 단위, Component 물리 역할, 최종 프레임, Scale bar 정합성은 확인 근거가 없어 미확인으로 표시한다. 규격 기준선·합격 판정을 생성하지 않는다.

## 미확정 사항의 표시

운송 프로파일·자세·회차의 Case 간 대응이 확정되지 않았으므로 비교표는 Case별 Scene을 분리하고 다른 Case 셀에 미대응 사유를 표시한다. 순번만으로 조용히 합치지 않는다. 사용환경 Reference는 원본 단위·조건이 일치하는 항목만 비교한다.

C23/C24를 Cell/Cushion/Box 또는 Upper/Lower로 추정하지 않는다. 거동 슬롯은 대상 역할 자료가 없으면 미제공이다. JPG 파일명에 Max_Stress가 있어도 최종 프레임으로 지정하지 않는다. 실제 Clamping 파일 읽기, 실제 CAE 이미지 범례·시간 정합성, Cushion/Box 영상 및 동기 재생은 원본 자료와 명시 매핑 확보 후 검증 대상이다.

현재 수집 제한은 파일당 32 MiB, 한 버전 합계 256 MiB, 10,000개 파일이다. CSV는 파일당 100,000행, Scene당 정규화 관측값 400,000개까지 읽는다. 500 MiB 영상 수집을 지원한다고 간주하지 않는다. 원본을 이동·재작성하지 않으며 진행 중이거나 안전하게 읽을 수 없는 파일은 게시를 중단한다. 한도를 늘릴 때는 DB·메모리·백업 영향을 함께 검토한다.

## 구조와 배포

| 책임 | 구현 |
|---|---|
| 원본 파싱·관측 차원 | `backend/app/domains/dashboard/parser.py` |
| 폴더 조사·불변 수집 | `backend/app/services/dashboard_capture.py` |
| 집계·사용환경·Case 비교 조회 | `backend/app/services/dashboard_queries.py` |
| 권한·게시 트랜잭션·자산 요청 | `backend/app/routers/dashboard.py` |
| 저장 구조 | `0029_dashboard_captures` migration; `dashboard_cases`, `dashboard_captures`, `dashboard_assets` |
| 화면·API 연결 | `frontend/src/features/results/SimulationDashboard.tsx`, `frontend/src/shared/api/simulationDashboard.ts` |

신규 외부 의존성은 없다. 최초 설치는 `deploy.bat`, 기존 설치 업데이트는 `update.bat`을 유지한다. 기존 DB는 검증된 백업 뒤 migration 0029를 적용하며 앱 시작은 스키마를 검사만 한다. DB 원본 자산도 전체 DB 백업 범위에 포함된다. 이번 작업에서 실제 사용자 DB와 설정을 변경하거나 운영 배포하지 않았다.

## 검증 기록

격리 PostgreSQL 17.11에서 빈 DB→전체 migration, 0028 DB의 기존 프로젝트·의뢰 데이터→0029 보존 업데이트, 읽기 전용 시작 검사를 통과했다. 같은 임시 서버에서 수집 중복 방지, 변경 전·후 수치 40/44의 불변 조회, 원본 바이트 저장, Run/Scene ID 유지도 확인했다.

자동 테스트는 별도 임시 DuckDB·합성 자료로 JSON 우선·오류·방향, Component·집계 기준·Mode 분리, root 탈출·자산 권한, 영상 Range, 과거 수집 버전, 선택 엣지·결측·동률을 검증한다. 실행 결과의 최종 집계는 `log/work-log.md`에 기록한다.

Windows Server 2022 폐쇄망 신규 설치·기존 DB 업데이트·재부팅 실기, 실제 20 Scene 및 실제 CAE 이미지 검증은 수행하지 않았다. 합성 자료 검증과 실제 해석 결과 검증을 구분한다.

합성 자료 브라우저 E2E 2개가 통과했다. 20 Scene/결측 그래프 공백, Scene 상세 연결, 컨투어 전치 셀 유지, 확대·ESC, 탭 전환 지연 응답 차단을 확인했다. 별도 하네스에서 데스크톱·모바일 화면을 직접 검수했으며 console/pageerror는 없었다. 전체 E2E 러너의 자식 종료 오류는 테스트 성공과 별도로 작업 기록에 남겼다.
