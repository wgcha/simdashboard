# UI 개선 P2 — 공통 컴포넌트와 결과 화면

- 날짜: 2026-09-20
- 상태: **P2 구현·Sol 독립 검수·Astra 최종 검수 완료, 사용자 확인 대기**. P3는 사용자 확인 전 진행하지 않는다.
- 기준: [실행 계획](ui-density-improvement-plan.md), [P0 API 계약](ui-density-p0-contract.md), [P1 기록](ui-density-p1-implementation.md).
- 역할: Astra 설계·통합·브라우저 검수, Terra 공통 컴포넌트, Luna 결과 화면, Sol 독립 검수.

## 변경 범위

`shared/components`에 Button/Input/Select/Table과 각 CSS를 추가했다. React 18 forwardRef로 native 속성·이벤트·ref를 전달한다. Button 기본 type은 button이고 Input/Select는 native size와 별도 controlSize를 제공한다. Table 계열은 의미론적 HTML을 유지하며 TableScroll에 role/tabIndex를 강제하지 않는다. 컨트롤·행 크기는 P1 토큰을 소비하며 글자 확대에 따라 성장한다.

결과 개요는 기존 목록 구조를 유지하고 기본 페이지 크기를 5에서 25로 변경했다. 10/25/50을 선택할 수 있고 검색·프로젝트·요약 필터·페이지 크기·데이터 변경 시 첫 페이지로 돌아간다. 표시 페이지와 slice/disabled가 같은 유효 페이지를 사용한다. 새 저장 키 없이 컴포넌트 세션 상태만 사용한다.

Case 결과의 환경 버튼·선택 필드와 사용환경 평가 표에 공통 컴포넌트를 적용했다. Case/Run Option/capture, 단일 후보 축약, 수집 이력, URL 문맥과 요청 순서 제어는 유지한다. 헤더·컨트롤·패널·목록의 중복 여백을 정리했다. 기존 전역 font-size important와 P3 이전 표시, DB/API·grid 저장 배치·배포 경로는 변경하지 않는다. 새 의존성은 없다.

## 실제 화면 비교

동일 합성 DB, 1366×768, light, 14pt(18.6667px) 조건이다. 수정 전 TSX/CSS를 TEMP에 보관하고 별도 Vite load overlay로 비교했으며 작업 소스를 되돌리지 않았다.

| 항목 | P2 전 | P2 후 |
|---|---:|---:|
| 결과 패널 시작 y | 476.8px | 439.8px |
| 첫 결과 행 시작 y | 627.1px | 578.1px |
| 두 줄 결과 행 실제 높이 | 78px | 74px |
| 첫 화면에 완전히 보이는 행 | 1 | 2 |

페이지 크기 증가와 별개로 여백 감소를 확인했다. 글자 크기를 줄이지 않았고 행은 내용에 따라 최소 토큰 높이보다 커질 수 있다. 모바일에서 발견한 목록 헤더와 페이지 크기 선택값 잘림을 보완했고, 18pt 날짜와 버튼은 별도 행으로 배치해 겹침을 해소했다.

## 검증

- 브라우저: IAB, `http://127.0.0.1:15182`, 1366×768/390×844, light/dark. 페이지 식별·본문 렌더·오류 overlay 없음·console 오류 없음·페이지 크기 선택과 모바일 스크롤 확인.
- 기존 result-overview 4개: 최신 Run 집계, 정확한 Run 연결, 비동기 의뢰 전환 안전성, 운영 차트 왕복 통과.
- 기존 simulation-dashboard 4개: 단일 Run Option 안정 ID URL, 느린 사용환경 응답 경합, Case 범례 선택 보존, 사용환경 평가 표5행·의뢰 전환 시 이전 capture 해제 통과. 마지막 테스트 스크린샷 저장 위치도 GUI_QA_OUTPUT_DIR/TEMP로 정리했다.
- 신규 `ui-density-results.spec.ts` 2개: 페이지 경계 0/1/5/6/25/26 및 10/11, 10/25/50 선택, 검색·프로젝트·요약 필터 재설정, 새로고침 기본값, 모바일18pt·focus·빈 검색 통과. 모바일 선택값의 실제 글자 폭과 날짜·동작 버튼의 비겹침도 검사한다. 최종 페이지 검사1 passed(1.4m), 최종 모바일 검사1 passed(8.8s), 각각 exit0. **고유 시나리오 총10개 통과**이며 한 번의 전체 suite 실행 결과와 구분한다.
- TypeScript, architecture(224 sources/5 reviewed imports), CSS checker self-test 및 `/home/` production build 통과. CSS 기준 상한을 늘리지 않았다. 기존 큰 chunk 경고는 남는다.

초기 새 테스트는 구현 중 확정된 label과 불일치해 실패하여 최종 접근성 이름으로 정정했다. 여러 경계에서 실제 인증 shell을 반복 로드하는 검사는 Windows의 90초 전체 제한에 걸려 해당 테스트만 180초로 늘렸다. 개별 assertion 제한은 완화하지 않았다. Terra의 pnpm build 시도는 비대화형 node_modules 정리 요구로 중단됐고, 이후 기존 설치 의존성으로 직접 TypeScript/Vite 빌드를 검증했다.

18pt 선택값 너비의 초기 3em 추정 검사는 실제 글자 폭보다 과도해서 canvas의 동일 font 텍스트 측정으로 바꿨다. 사용환경 추가 검사는 합성 viewer 계정이 없어 첫 인증에 실패했고, 임시 QA DB에 해당 테스트 계정을 준비한 뒤 통과했다. 제품 인증 로직은 변경하지 않았다. 최종 `/home/` 빌드와 CSS 구조 검사를 모바일 수정 후 다시 통과했고 문서 링크·diff 공백 검사도 완료했다.

## 환경·한계

QA는 이전 P1 합성 DB를 별도 TEMP 복사본으로 사용했다. 실제 사용자 DB·설정·서비스는 테스트 대상으로 쓰지 않았다. 산출물은 `%TEMP%/sim-workbench-ui-p2-20260920/`의 screenshots/build/test-results에 있다. 이전 비교 서버는 15183, 임시 API는18102였다.

실제 Server 2022 폐쇄망 배포·LAN 재검사·전체 애플리케이션 회귀는 이번 범위에 포함하지 않았다. P1의 오프라인 설치 메타데이터 경고를 해결했다고 주장하지 않는다. commit/push/배포는 수행하지 않았다.

Sol은 primitive/native API, 페이지 경계·재설정, Case 문맥, 모바일 보완을 독립 승인했다. Astra는 실제 화면·테스트 결과·범위와 증거를 최종 확인했다. 모든 단계 완료 승인이 아니라 P2 범위의 완료이며 다음 단계는 사용자 확인 후 진행한다.
