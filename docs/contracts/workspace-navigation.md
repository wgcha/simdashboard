# 계약: 사이드바 이동 시 의뢰 문맥 처리

- 상태: 확정 (2026-10-04)
- 배경: 폴더 탐색으로 등록된 의뢰는 구 방식 개요(`overview`)가 없다. `App.tsx`의 준비 단계 대체 화면(`BootstrapWorkspaceShell`)이 의뢰와 무관한 페이지(변수 카탈로그 등)까지 가로채고, 사이드바 이동이 이전 화면의 쿼리를 그대로 가져가 문제가 가끔 재현됐다.

## 결정

| # | 결정 |
|---|---|
| N1 | 준비 단계 대체 화면은 의뢰 작업 페이지(`dashboard`, `workbench`, `data`)에서만 쓴다. 그 밖의 페이지는 `overview`가 없어도 자기 화면을 그린다 |
| N2 | `overview`가 필요한 페이지(현재 변수 카탈로그)는 없을 때 빈 상태 안내를 보여준다. 문구: "선택한 의뢰에는 변수 카탈로그 데이터가 없습니다." |
| N3 | 사이드바(및 `navigateWorkspace` 페이지 전환)로 다른 페이지에 가면 의뢰 화면 전용 쿼리를 지운다: `view`, `capture`, `result_environment`, Case·Scene·Run·Option·part 선택 계열. `project`, `request`는 유지한다 |
| N4 | 같은 페이지 안의 이동(탭, 의뢰 선택 등)은 기존 쿼리 동작을 바꾸지 않는다 |

## Verifier 기준

1. Case 결과(폴더 등록 의뢰) → 사이드바 변수 카탈로그: 준비 단계 화면이 아니라 카탈로그 페이지(또는 빈 상태)가 보인다
2. 이동 후 URL에 `view`, `capture`, `result_environment`가 없다. `project`, `request`는 남는다
3. templates, examples, help, 관리자 페이지들도 1과 같다
4. dashboard/workbench/data에서 개요가 없는 의뢰는 기존대로 준비 단계 화면
5. 구 방식 의뢰(개요 있음)의 변수 카탈로그는 기존과 동일
