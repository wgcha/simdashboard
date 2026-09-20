# UI 밀도 개선 P0 시각 기준선

- 측정일: 2026-09-19
- 상태: P0 기준선 수집 완료
- 목적: 앱 수정 전 대표 화면의 실제 렌더 크기, 글자 설정, 문서 가로 넘침, 내부 overflow를 고정한다.
- Browser 플러그인: 사용 불가. `frontend-testing-debugging` 지침에 따라 일반 Playwright fallback을 사용했다.
- 앱 코드 변경: 없음. 기존 E2E fixture와 임시 Playwright spec만 사용했으며 임시 spec은 실행 후 제거했다.

## 검증 범위

| 화면 | viewport / 글자 | 상태 |
|---|---|---|
| 결과 개요 | 1366×768 / 14pt | 결과 대시보드 표시, 결과 필터 화면 캡처 |
| 의뢰 작업공간 | 1366×768 / 14pt | 선택 의뢰 개요 표시 |
| Case 결과 | 1366×768 / 14pt | 합성 Usage fixture 5개 평가 행 표시 |
| Case 결과 | 390×844 / 18pt | 같은 합성 결과 표시, 내부 표 가로 스크롤 확인 |
| 기존 Run 대시보드 | 1366×768 / 14pt | `상세 분석 구성 확인 중`이 사라지고 `상세 분석` heading이 보인 뒤 캡처. 결과 구성 미지정(Unconfigured) 상태 |
| 도움말 | 1366×768 / 14pt | 도움말 전체 첫 viewport 캡처 |
| 의뢰 접수 폼 | 1366×768 / 14pt | 새 의뢰 폼 캡처 |
| 폴더 연결 | 1366×768 / 14pt | 합성 폴더 구조 확인 단계 캡처 |
| 폴더 연결 | 390×844 / 18pt | 구조 확인 단계 캡처 |
| 의뢰 작업공간 | 2560×1440 / 14pt | 가용 본문 폭과 root overflow 측정 |
| 폴더 연결 | 2560×1440 / 14pt | 구조 확인 단계와 wrapper 폭 측정 |

## 측정 결과

`scrollWidth/clientWidth`는 문서 전체 가로 넘침 기준이다. 모든 수집 화면의 root와 01~09 화면의 body 값이 viewport와 같았다. Wide 10~11 JSON에는 body를 별도 측정하지 않았다.

| 증거 | 주요 computed rect | root 가로 | shell 변수 / 대표 computed font |
|---|---|---:|---|
| `01-results-overview-desktop-14pt` | dashboard 1158×1035 | 1366/1366 | `--ui-font-size=14pt`, shell 18.6667px, body 16px |
| `02-request-workspace-desktop-14pt` | focused request 1158×604 | 1366/1366 | 14pt, shell 18.6667px |
| `03-case-results-desktop-14pt` | simulation dashboard 1078×1414 | 1366/1366 | 14pt, shell 18.6667px |
| `04-case-results-mobile-18pt` | simulation dashboard 358×2257 | 390/390 | `--ui-font-size=18pt`, shell 24px, body 16px |
| `05-existing-run-desktop-14pt` | main 1158×1045, header 1158×361 | 1366/1366 | 14pt, shell 18.6667px |
| `06-help-desktop-14pt` | help center 1158×1462 | 1366/1366 | 14pt |
| `07-request-form-desktop-14pt` | form 1102×1276 | 1366/1366 | 14pt |
| `08-folder-connection-desktop-14pt` | workspace 1158×805, tree 669×339 | 1366/1366 | 14pt |
| `09-folder-connection-mobile-18pt` | workspace 390×853, tree 340×240 | 390/390 | 18pt, shell 24px |
| `10-request-workspace-wide-2560-14pt` | main 2352 wide, focused request 2352×579 | 2560/2560 | 14pt, `max-width:none` |
| `11-folder-connection-wide-2560-14pt` | workspace 2352 wide, tree 1415×294 | 2560/2560 | 14pt, `max-width:none` |

### 내부 overflow 관찰

- 결과 개요 desktop: 카드 안 `strong/small` 9개가 `scrollWidth > clientWidth`였다. 문서 전체 넘침은 없지만 긴 텍스트가 카드 내부에서 잘리거나 스크롤되는지 P1에서 확인할 후보이다.
- Case 결과 desktop: Usage selector label 1개가 내부 폭을 초과했다.
- Case 결과 mobile: request journey nav가 881px, 결과 표가 549px로 내부 가로 스크롤을 사용한다. viewport는 390px을 넘지 않는다. 이는 표·여정의 내부 스크롤 정책과 연결해 검토할 항목이다.
- 기존 Run desktop: breadcrumb 및 숨겨진 strong 2개에서 내부 overflow가 관찰됐다.
- 폴더 연결 desktop/mobile: 수집된 대표 요소에서 내부 overflow가 없었다.

## 증거 경로

PNG와 동일 이름의 JSON은 다음 TEMP 디렉터리에 있다.

`C:\Users\coolc\AppData\Local\Temp\simulation-workbench-ui-density-baseline-20260919`

파일 대응은 다음과 같다.

- `01` 결과 개요 desktop
- `02` 의뢰 작업공간 desktop
- `03` 합성 Usage Case 결과 desktop
- `04` 합성 Usage Case 결과 mobile 18pt
- `05` 기존 Run 대시보드 안정화 후 desktop
- `06` 도움말 desktop
- `07` 의뢰 접수 폼 desktop
- `08` 폴더 연결 구조 확인 desktop
- `09` 폴더 연결 구조 확인 mobile 18pt
- `10` 의뢰 작업공간 2560 wide
- `11` 폴더 연결 2560 wide

JSON의 마지막 `run-summary.json`은 `pageErrors=[]`, `consoleErrors=[]`, `consoleWarnings=[]`, `unexpected401s=[]`, `expectedAuthProbe401s` 1건을 기록한다. 401은 로그인 전 `/api/auth/me` probe이며 인증 성공 후 동일 요청은 200이었다.

## 실행 명령 기록과 실행 소유권

첫 기준선 실행은 아래 명령으로 수행했다. 두 임시 spec은 실행 후 삭제되어 현재 저장소에 없다.

따라서 아래는 실제 실행 기록이며 그대로 재실행 가능한 영구 테스트 명령은 아니다. P1에서는 적용 대상의 전후 검사를 유지 가능한 fixture로 편입한다. TEMP 캡처도 영구 보관을 보장하지 않는다.

```powershell
Set-Location E:\simulation_workbench\frontend
$env:E2E_PASSWORD_RELEASE='true'
node scripts/run-e2e.mjs e2e/ui-density-baseline-temp.spec.ts
node scripts/run-e2e.mjs e2e/ui-density-wide-temp.spec.ts
```

`frontend/scripts/run-e2e.mjs`가 각 실행에서 `backend/data/e2e-playwright.duckdb`를 삭제·재생성하고, `ANALYSIS_DB_BACKEND=duckdb`, `ANALYSIS_DUCKDB_PATH`를 설정한 backend를 `127.0.0.1:18000`에, `VITE_API_TARGET=http://127.0.0.1:18000`인 Vite를 `127.0.0.1:15173`에 자식 프로세스로 시작했다. 인증은 `AUTH_MODE=password`와 runner 내부 합성 secret을 사용했다. 실제 `.env`, 실제 사용자 DB, 운영 서비스는 테스트 대상이 아니었다.

두 실행 모두 Playwright 테스트 자체는 통과했다. Windows runner 종료 단계에서는 자식 taskkill이 exit 1을 반환해 `AggregateError: E2E child did not exit`가 보고되는 기존 runner 종료 결함이 있었다. 실행 후 `Get-NetTCPConnection`으로 15173/18000 LISTEN 소유 프로세스를 확인했으며 잔존하지 않았다. 따라서 테스트 시나리오 통과와 runner process cleanup 오류를 분리해 기록한다.

## 미검증 범위

- 라이트/다크 양 테마 전체 조합, 11pt 경계, 18pt 모든 화면 조합은 이 기준선에 포함하지 않았다.
- 1920×1080, 2048×1152, 1707×960의 전체 대표 화면과 실제 Windows Server 2022 폐쇄망 배포는 검증하지 않았다.
- Case 결과의 유통환경 20 scene, contour/영상 확대, Run 비교 상세 동작은 별도 기존 E2E 검증 범위이며 이 baseline 캡처에서는 수행하지 않았다.
- 폴더 연결은 합성 route fixture로 구조 확인 화면을 측정했다. 실제 저장소 파일, 실제 사용자 DB, 운영 folder scan은 사용하지 않았다.
- 기존 Run은 `request-showcase-compare`의 결과 구성 미지정 화면을 안정화해 캡처했다. 실제 configured dashboard의 위젯 밀도 비교는 P2 대상이다.

## P0 계약 메모

- 비교 기준 글자 설정은 저장값 14pt와 mobile 18pt이며 computed shell 값은 각각 약 18.67px/24px이다.
- 문서 전체 horizontal overflow는 금지하고, 결과 표·Case 여정처럼 내용상 필요한 영역은 내부 scroll container로 한정한다.
- 2560 wide에서 작업 본문과 폴더 workspace는 `max-width:none`으로 가용 폭을 사용했다. 읽기 본문·dialog·canvas의 별도 cap 여부는 후속 화면별 검토 대상으로 남긴다.
- 이 문서는 구현 완료나 P0 승인 기록이 아니라, 앱 코드 변경 전 측정 증거와 다음 단계의 비교 계약이다.
- Astra 시각 검수에서 제목·본문 크기, header/본문 시작선, 결과 첫 화면 노출, 내부 표 스크롤, 폴더 트리/상세 패널 배치, 2560 본문 폭을 확인했다. 원시 overflow 후보는 sr-only·의도적 말줄임도 포함하므로 전부 UI 결함이라고 판정하지 않는다.
