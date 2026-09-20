# UI 개선 P3 글자 역할 시범 적용

- 일자: 2026-09-20
- 상태: 구현·관련 자동 검증·Astra 최종 코드 검수 완료. 2026-09-20 후속 작업에서 Sol 독립 검수를 재시도하여 승인받았다. 사용자 다음 단계 승인 후 P4-1 폴더 연결 화면에 착수했고 P5는 미착수다.

## 적용 범위

결과 개요와 SimulationDashboard에 `data-ui-density="v1"`을 지정하고 기존 전역 글자 강제 규칙에서 제외했다. 제목·본문·보조 문구의 역할을 토큰으로 구분하며 나머지 화면은 기존 pt 동작을 유지한다. 두 shell은 공통 `workspaceFontSizeStyle` 어댑터를 사용한다. 기본 14pt, 11~18 범위, 기존 소수값 허용, 저장 키와 이전 방식은 보존한다.

| 설정 | 본문 | 보조 문구·차트 | 큰 제목 |
|---|---|---|---|
| 11pt | 14.67px | 12.83px | 22px |
| 14pt | 18.67px | 16.33px | 28px |
| 18pt | 24px | 21px | 36px |

컨트롤은 최소 높이·모서리 토큰을 사용하며 긴 문구에 맞게 높이가 늘어난다. 확대 그래프와 미디어 dialog는 시범 영역 하위 native DOM이므로 변수를 상속한다. 실제 외부 portal로 바뀌면 별도 변수 전달 검증이 필요하다.

html 글자 크기, grid rowHeight 84/70/74/20px, 보고서 maxRows 18, 저장 배치, 업무 선택·API·DB·배포 계약은 변경하지 않았다. 새 의존성은 없다. 기존 important 상한 61을 유지하고 변경된 selector만 기준선에 반영했다.

## 검증

- 고유 Playwright 6개 통과: PC/모바일에서 11·14·18pt 역할, light/dark, 경계 버튼, 재접속 설정 유지, 미이전 도움말, 확대 그래프와 Escape, 단일 Run Option URL, 의뢰 전환 capture 문맥, 모바일 컨트롤 가독성.
- 초기 검사에서 결과 행의 남은 전역 강제 규칙과 적용 중인 차트 scope 누락을 확인해 수정 후 재통과했다. 독립 Astra 검수의 미디어 버튼·행렬 헤더·legend 글자와 고정 컨트롤 높이 지적도 반영했다.
- TypeScript, architecture 225 sources/5 imports, CSS checker self-test, preferences self-test 통과. `/home/` production build 통과(기존 큰 chunk 경고 유지). 기본 Vite config 임시 파일 EPERM은 runner 방식과 TEMP 출력으로 우회했다.
- IAB에서 desktop 14pt와 mobile 18pt 육안 확인, 페이지 가로 넘침 없음. 자동 검증 스크린샷은 `%TEMP%/sim-workbench-ui-p3-20260920/screenshots`에 보관한다.
- 최종 높이 수정 후 확대 그래프 테스트도 재통과했다. IAB 콘솔에는 `A router only supports one blocker at a time` 경고 2회가 관찰되었다. 라우터 코드는 이번 변경에 포함하지 않았으며 이 경고 해결을 검증했다고 주장하지 않는다. 임시 검수 탭과 서버를 종료했다.
- 합성 TEMP DB만 사용했다. 전체 앱 회귀, 실제 Server 2022 폐쇄망 실기, commit/push/배포는 하지 않았다. 최초 Sol 실행은 용량 오류로 미완료였으나 후속 독립 검수에서 설정 보존·scope 제외·역할 매핑·dialog/chart 상속을 확인하고 차단 사항 없이 승인했다.
