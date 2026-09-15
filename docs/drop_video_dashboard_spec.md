# 해석 결과 영상 비교 기능 간이 사양서

> 상태: 2026-08-01 synthetic Scene 평가 통합 구현 기준
>
> 이 문서의 **현재 프로그램 적용 사양**이 아래의 운영 목표 사양과 충돌하면 현재 프로그램 적용 사양을 우선한다. 운영 DB 연계 항목은 후속 전환 목표다.

## 0. 현재 프로그램 적용 검토 및 변경 결정

### 2026-09-11 가로·세로 설정

- 대시보드 편집 → 영상 위젯 설정에서 가로 열 수와 세로 행 수를 개별 선택한다. 기본값 2×2, 곱은 최대 20개다. 5×4는 20개를 한 표시 묶음으로 제공한다.
- `settings.videoColumns`, `settings.videoRows`를 기존 레이아웃 저장과 함께 보존한다. API 조회용 `pageSize`와 표시 개수는 구분하고 기존 설정·위젯 위치는 유지한다.
- 한 축을 변경할 때 다른 축을 자동 변경하지 않는다. 현재 반대 축과 곱이 20을 넘는 선택지는 비활성화한다.
- 작은 화면은 실제 표시 열 수를 줄이되 저장된 열·행 수와 표시 묶음 개수는 유지한다. 많은 행은 영상이 잘리지 않도록 스크롤한다.
- 이미 생성된 snapshot의 설정은 그대로 읽고, 읽기 전용 snapshot을 이 기능으로 수정하지 않는다.

### 2026-09-10 사용성 변경

- 기존 위젯 위치·크기와 API 페이지 계약은 유지한다. 아래 초기 사양의 ‘20개 영상 동시 표시’는 현재 목록을 4개씩 탐색하는 방식으로 대체한다.
- Scene 요약에서 선택하면 해당 영상이 있는 표시 묶음으로 이동한다. 묶음 변경 시 기존 영상을 정지하고 새 묶음 첫 영상을 선택한다.
- 일괄 제어는 현재 표시된 영상에만 적용하며 버튼에 그 범위를 표시한다. 전체 목록 판정과 표시 중인 영상 개수를 구분한다.
- 위젯 확대·복귀는 동일한 미디어 DOM을 유지한다. 예제/합성 평가 경고와 기준은 계속 표시한다.
- 자세한 목표·범위는 [결과 분석 사용성 실행 계획](dashboard-usability-and-recommended-layout-plan.md)을 따른다.

### 0.1 적용 원칙

| 검토 항목 | 1차 구현 결정 |
|---|---|
| 화면 진입점 | 새 페이지를 만들지 않고 기존 상세 분석 대시보드에 `video_grid` 복합 위젯으로 추가 |
| 레이아웃 | 기존 `DashboardWidget`·`react-grid-layout`·편집·저장·버전 체계를 그대로 재사용 |
| 기본 배치 | `dashboard-drop-default`의 기존 위젯 좌표는 유지하고 맨 아래 `x=0, y=16, w=12, h=10`으로 추가. 위젯 내부는 좌측 평가 요약·Scene 그래프, 우측 20개 영상의 2열 복합 배치 |
| 조회 컨텍스트 | 기존 프로젝트·의뢰·하중 경우 선택기를 재사용하며 영상 위젯 안에 중복 선택기를 만들지 않음 |
| 조회 키 | 현재 선택된 `load_case_id` |
| 데이터 원천 | 영상은 루트 `video_example`의 읽기 전용 `EXAMPLE_ADAPTER`; 평가는 실제 Solver/overview와 분리된 고정 `SYNTHETIC_DEMO` fixture. DB 스키마 변경 없음 |
| 예제 연결 범위 | `loadcase-drop-bottom-001` 한 건만 명시적으로 허용. 다른 하중 경우는 `200`과 빈 목록 반환 |
| 파일 제공 | `/api` 인증을 거치는 정확한 20개 파일 allowlist와 안전한 `FileResponse`; 별도 정적 mount 금지 |
| 카드 메타데이터 | main은 `기준 낙하 해석`, variant는 `낙하 비교 Scene 01..19`; 파일명에서 방향·Face·Edge·Corner·조건을 추정하지 않음 |
| 페이지 | API는 `page >= 1`, `1 <= page_size <= 20`; 현재 예제는 정확히 20개라 기본 설정에서 1페이지 |
| 재생 | 개별 controls, 전체 재생·정지·처음부터·반복. 일괄 재생은 브라우저 정책에 맞게 muted 적용 |
| 로딩 | `preload="metadata"`, `playsInline`; 페이지 변경·하중 경우 변경·unmount 때 기존 영상을 정지하고 요청 취소 |
| 판정 표시 | overall PASS는 green 2px border, FAIL은 red 2px border와 텍스트 badge. 재생 실패는 평가 판정을 덮지 않고 영상 frame 내부 오류로만 표시 |
| 실패 격리 | 한 카드의 파일·코덱 재생 실패를 카드 내부 오류로 표시하고 다른 영상과 전체 위젯은 계속 동작 |

### 0.2 현재 예제 파일 검토 결과

- `video_example`의 현재 20개 MP4를 바이너리 구조와 SPS로 재검증한 결과 모두 `avc1` H.264 High Profile 3.1, 8-bit yuv420이다.
- 20개 모두 top-level `moov` box가 `mdat`보다 앞선 fast-start이며 MPEG-4 Part 2 sample entry는 0개다.
- Chromium 실제 검증에서 20개 모두 디코딩되고 재생 시간이 증가했으며, 인증·MIME·Range 요청도 정상 동작했다.
- 서버는 외부 `ffprobe`에 의존하지 않는 bounded stdlib MP4 box probe로 `codec=h264`, `fast_start=true`를 응답한다. 파일 교체 시 size·mtime cache key가 probe 결과를 무효화한다.
- 파일이 손상되거나 지원되지 않는 형식으로 교체될 가능성은 남으므로 카드별 playback 오류 격리는 계속 유지한다.

### 0.3 운영 전환 시 후속 범위

- `media_assets` 또는 별도 영상 레코드와 하중 경우의 영속 관계 정의
- 실제 파일 probe 결과(`duration`, `codec`, fast-start, 해상도) 영속 저장 및 입수 단계 검증
- DB 기반 전체 개수·정렬·페이지 조회로 `EXAMPLE_ADAPTER` 교체
- 썸네일 생성, H.264 규격 검증·필요 시 변환 파이프라인, 업로드/보존/권한 정책
- 운영 데이터로 최대 20개 동시 재생 성능과 브라우저 호환성 검증

### 0.4 `20260730_223421` 참조 레이아웃 매핑

`docs/layouts/20260730_223421.jpg`는 기능·배치 관계만 참고하고 외형을 그대로 복제하지 않는다.

| 참조 화면 관찰 | 현재 프로그램 매핑 |
|---|---|
| 좌측의 결과 표·기준·재생 제어 | `video_grid` 내부 synthetic 판정 summary, subsystem 기준, 20 Scene compact graph, 선택 Scene 상세와 기존 일괄 재생 제어 |
| 우측의 20 Scene 영상 4×5 배열 | 기존 `DashboardWidget` 안의 우측 20개 video card grid |
| Scene별 색상 구분 | overall PASS/FAIL의 green/red 2px border와 텍스트 badge. 색상만으로 의미를 전달하지 않음 |
| 개별 Scene 선택 | 카드 focus + Enter/Space 또는 좌측 Scene button으로 선택 상세 갱신 |

기존 상세 분석 화면의 선택기, 위젯 shell, drag/resize, 저장·버전 뼈대는 변경하지 않는다.

## 1. 문서 목적

본 문서는 기존 해석 결과 대시보드에 **선택된 하중 경우의 낙하 해석 영상을 조회·비교·재생하는 기능**을 추가하기 위한 간이 개발 사양이다.

1차 PoC는 현재 프로그램의 위젯·레이아웃 체계를 유지한 채 저장소의 예제 영상을 읽기 전용 어댑터로 제공한다. 운영 단계에서는 하중 경우와 영상 링크를 DB에 영속화하고 같은 API 계약으로 교체한다.

---

## 2. 기능 개요

| 항목 | 사양 |
|---|---|
| 기능명 | 낙하 해석 결과 영상 다중 비교 |
| 주요 목적 | 제품과제 또는 의뢰에 포함된 낙하 씬별 해석 영상을 한 화면에서 비교 |
| 조회 기준 | 현재 선택된 하중 경우 ID |
| 데이터 원천 | 1차: `video_example` 예제 어댑터 / 운영: 해석 결과 데이터베이스 |
| 영상 분류 기준 | 낙하 씬별 영상 |
| 페이지당 최대 영상 수 | 20개 |
| 전체 영상 수 | DB 등록 수에 따라 가변 |
| 페이지네이션 | 영상이 20개를 초과하면 다음·이전 페이지 제공 |
| 그리드 구성 | 현재 페이지 영상 개수 및 화면 폭에 따라 자동 조정 |
| 지원 브라우저 | 최신 Microsoft Edge, Google Chrome |
| 사용자 PC 추가 설치 | 불필요 |
| 기본 영상 형식 | MP4(H.264) |
| 선택 영상 형식 | WebM(VP9) |
| 예상 구현 난이도 | 낮음~중간 |
| 예상 개발 기간 | PoC 2~4시간, 통합 및 안정화 1~2일 |

---

## 3. 사용자 시나리오

1. 사용자가 기존 상단 선택기에서 프로젝트·의뢰·하중 경우를 선택한다.
2. `video_grid` 위젯은 현재 `load_case_id`의 영상 목록을 API에서 조회한다.
3. 영상 개수에 따라 그리드 열 수와 카드 크기를 자동 결정한다.
4. 현재 페이지에는 최대 20개 영상만 표시한다.
5. 영상이 20개를 초과하면 페이지네이션을 표시한다.
6. 사용자는 현재 페이지의 영상을 전체 재생·정지하거나 각 영상을 개별 재생·정지한다.
7. 다음 페이지로 이동하면 기존 페이지 영상은 정지되고, 새 페이지의 영상 목록이 표시된다.
8. 각 영상 카드에는 낙하 씬명, 해석 조건, 영상 상태 등 필요한 메타데이터를 표시한다.

---

## 4. 화면 구성

### 4.1 상단 조회 영역

| 구성 요소 | 세부 사양 | 우선순위 |
|---|---|---:|
| 기존 컨텍스트 선택기 | 프로젝트·의뢰·하중 경우 선택기를 그대로 사용하며 위젯 내부에는 중복 생성하지 않음 | 필수 |
| 영상 개수 표시 | 선택 항목에 등록된 전체 영상 수 표시 | 필수 |
| 현재 페이지 표시 | 현재 페이지 / 전체 페이지 표시 | 필수 |
| 전체 재생 | 현재 페이지에 표시된 영상 전체 재생 | 필수 |
| 전체 정지 | 현재 페이지에 표시된 영상 전체 일시정지 | 필수 |
| 전체 처음부터 재생 | 현재 페이지 영상 시간을 0초로 맞춘 후 재생 | 권장 |
| 반복 재생 | 현재 페이지 영상의 반복 여부 제어 | 권장 |

### 4.2 영상 그리드 영역

| 구성 요소 | 세부 사양 | 우선순위 |
|---|---|---:|
| 좌측 평가 패널 | 전체 PASS/FAIL 수, subsystem 기준, 20 Scene compact 판정 그래프, 선택 Scene 상세 | 필수 |
| 우측 영상 패널 | 데스크톱 4×5 영상 카드 그리드. 1200px 이하 3열, 900px 이하 2열, 620px 이하 1열 | 필수 |
| 영상 카드 | 낙하 씬별 영상 1개당 카드 1개 | 필수 |
| 카드 제목 | 낙하 씬명 또는 Scene ID | 필수 |
| 영상 플레이어 | HTML5 `<video>` 사용 | 필수 |
| 개별 재생 | 해당 영상만 재생 | 필수 |
| 개별 정지 | 해당 영상만 일시정지 | 필수 |
| 개별 초기화 | 해당 영상 시간을 0초로 이동 | 선택 |
| 영상 상태 | 로딩, 재생 중, 정지, 오류 표시 | 권장 |
| 평가 상태 | 카드별 Open Cell/Chassis mini bar, subsystem 판정, overall 판정 badge와 색상 border | 필수 |
| 키보드 선택 | 카드에 focus 후 Enter 또는 Space로 선택 Scene 상세 변경 | 필수 |
| 부가정보 | 방향, 조건, 높이, 해석 버전 등 표시 | 권장 |
| 확대 보기 | 선택 영상을 모달 또는 상세 화면으로 확대 | 선택 |

### 4.3 페이지네이션 영역

| 항목 | 세부 사양 |
|---|---|
| 표시 조건 | 전체 영상 수가 페이지당 표시 수를 초과할 때 표시 |
| 기본 페이지 크기 | 20개 |
| 이전 페이지 | 현재 페이지가 2 이상일 때 활성화 |
| 다음 페이지 | 다음 페이지 데이터가 있을 때 활성화 |
| 페이지 번호 | 현재 페이지 주변 번호 또는 전체 번호 표시 |
| 페이지 이동 시 | 기존 페이지의 모든 영상 정지 |
| 페이지 이동 후 | 새 페이지 영상은 기본 정지 상태 |
| 선택적 확장 | 페이지당 8, 12, 20개 선택 기능 추가 가능 |

---

## 5. 동적 그리드 사양

영상 개수와 브라우저 화면 폭에 따라 그리드 열 수를 자동 조정한다.

### 5.1 영상 개수 기준 권장 배치

| 현재 페이지 영상 개수 | 권장 그리드 |
|---:|---|
| 1개 | 1열 |
| 2개 | 2열 |
| 3~4개 | 2열 |
| 5~6개 | 3열 |
| 7~12개 | 최대 4열 |
| 13~20개 | 4열 |

현재 페이지 수가 4개 미만이면 불필요한 빈 열을 만들지 않고 1~3열을 사용한다. 4개 이상은 데스크톱에서 4열을 유지하고 화면 폭에 따라 3/2/1열로 줄인다.

```css
.video-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
```

### 5.2 반응형 기준 예시

| 화면 폭 | 권장 열 수 |
|---:|---:|
| 1201px 이상 | 4열(좌측 평가 패널과 함께 표시) |
| 901~1200px | 3열(평가 패널은 위로 배치) |
| 621~900px | 2열 |
| 620px 이하 | 1열 |

### 5.3 카드 표시 기준

- 카드 영상 비율은 기본 16:9로 통일한다.
- 영상이 적을 때 카드를 과도하게 확대하지 않도록 최대 폭을 설정할 수 있다.
- 영상이 많을 때 최소 카드 폭 이하로 축소하지 않고 열 수를 줄인다.
- 현재 페이지 영상 수가 20개 미만이어도 빈 카드 영역을 생성하지 않는다.

---

## 6. 영상 파일 기준

| 항목 | 권장 사양 | 허용 기준 |
|---|---:|---:|
| 영상 길이 | 약 10초 | 5~15초 |
| 파일 크기 | 2MB 이하 | 최대 3MB 권장 |
| 해상도 | 640×360 | 최소 480×270 |
| 프레임 속도 | 15fps | 10~20fps |
| MP4 코덱 | H.264 | 필수 |
| WebM 코덱 | VP9 | 선택 |
| 오디오 | 없음 | 필수 |
| 화면 비율 | 16:9 | 가급적 통일 |
| 웹 최적화 | MP4 `faststart` 적용 | 권장 |
| 반복 재생 | 지원 | 사용자 선택 |

페이지당 20개, 영상당 2MB 기준 최대 파일 총량은 약 40MB이다.

페이지 진입 시 모든 파일을 즉시 전체 다운로드하지 않도록 `preload="metadata"` 또는 포스터 이미지 기반 로딩을 사용한다.

---

## 7. 데이터 구조

### 7.1 현재 조회 컨텍스트

| 필드 | 형식 | 설명 |
|---|---|---|
| `load_case_id` | string | 현재 하중 경우 고유 ID |
| `load_case_name` | string | 화면 표시용 하중 경우 명칭 |
| `analysis_type` | string | `DROP`, `SIDE_CLAMP` 등 해석 유형 |
| `request_id` | string | 상위 해석 의뢰 ID |
| `request_name` | string | 상위 해석 의뢰 표시명 |

### 7.2 낙하 씬별 영상 정보

| 필드 | 형식 | 설명 |
|---|---|---|
| `video_id` | string 또는 integer | 영상 고유 ID |
| `load_case_id` | string | 목록 조회 컨텍스트. 1차 응답에서는 상위 `load_case`에 제공 |
| `scene_id` | string 또는 integer | 낙하 씬 고유 ID |
| `scene_name` | string | 낙하 씬 표시명 |
| `video_url` | string | MP4 또는 WebM 영상 링크 |
| `thumbnail_url` | string 또는 null | 포스터 또는 썸네일 링크 |
| `duration` | number 또는 null | 영상 길이, 초. probe 전에는 null |
| `file_size` | integer | 파일 크기, byte |
| `format` | string | `mp4` 또는 `webm` |
| `codec` | string 또는 null | stdlib MP4 probe 결과. 현재 fixture는 `h264` |
| `fast_start` | boolean 또는 null | `moov`가 `mdat` 앞이면 true. box를 확인할 수 없으면 null |
| `sort_order` | integer | 기본 표시 순서 |
| `drop_direction` | string 또는 null | 낙하 방향. 근거 데이터가 있을 때만 표시 |
| `drop_condition` | string 또는 null | 낙하 높이·속도·조건. 근거 데이터가 있을 때만 표시 |
| `analysis_version` | string 또는 null | 해석 모델 또는 결과 버전 |
| `evaluation.overall_verdict` | `PASS` 또는 `FAIL` | 두 subsystem이 모두 PASS일 때만 PASS |
| `evaluation.open_cell` | object | `critical_value`, `threshold`, `unit`, `verdict`, `metrics` |
| `evaluation.chassis_rear` | object | `critical_value`, `threshold`, `unit`, `verdict`, `metrics` |

### 7.3 Synthetic demo 평가 계약

- 응답 최상위에 `evaluation_source: "SYNTHETIC_DEMO"`, `contract_version: 1`, 전체 `summary`를 제공한다.
- 이 값은 UI와 API 계약을 검증하기 위한 고정 fixture이며 실제 Solver 결과나 `/overview` 결과를 복제하지 않는다.
- Open Cell은 `critical_value > 75.0 MPa`일 때 FAIL이다. 정확히 `75.0 MPa`는 PASS다.
- Chassis Rear는 `critical_value >= 5.0 mm`일 때 FAIL이다. 정확히 `5.0 mm`는 FAIL이다.
- 어느 subsystem이든 FAIL이면 overall FAIL이고, 두 subsystem이 모두 PASS일 때만 overall PASS다.

| Scene | Open Cell (MPa) | OC | Chassis Rear (mm) | Chassis | Overall |
|---:|---:|---|---:|---|---|
| 01 | 68.2 | PASS | 4.2 | PASS | PASS |
| 02 | 76.8 | FAIL | 4.4 | PASS | FAIL |
| 03 | 70.1 | PASS | 5.3 | FAIL | FAIL |
| 04 | 82.5 | FAIL | 5.7 | FAIL | FAIL |
| 05 | 62.4 | PASS | 3.8 | PASS | PASS |
| 06 | 73.9 | PASS | 4.9 | PASS | PASS |
| 07 | 75.0 | PASS | 5.0 | FAIL | FAIL |
| 08 | 79.1 | FAIL | 4.1 | PASS | FAIL |
| 09 | 66.7 | PASS | 4.6 | PASS | PASS |
| 10 | 71.8 | PASS | 5.2 | FAIL | FAIL |
| 11 | 84.3 | FAIL | 6.1 | FAIL | FAIL |
| 12 | 69.5 | PASS | 4.3 | PASS | PASS |
| 13 | 74.6 | PASS | 4.8 | PASS | PASS |
| 14 | 77.2 | FAIL | 4.7 | PASS | FAIL |
| 15 | 64.9 | PASS | 5.5 | FAIL | FAIL |
| 16 | 72.3 | PASS | 3.9 | PASS | PASS |
| 17 | 80.6 | FAIL | 5.1 | FAIL | FAIL |
| 18 | 67.4 | PASS | 4.0 | PASS | PASS |
| 19 | 73.1 | PASS | 4.5 | PASS | PASS |
| 20 | 78.0 | FAIL | 5.4 | FAIL | FAIL |

합계는 overall PASS 9 / FAIL 11, Open Cell FAIL 7, Chassis Rear FAIL 8이다.

---

## 8. API 요구사항

### 8.1 영상 목록 조회

```http
GET /api/load-cases/{load_case_id}/drop-videos?page=1&page_size=20
GET /api/drop-videos/{video_id}/content
```

- `page`는 1 이상, `page_size`는 1~20이다.
- 두 경로 모두 기존 `/api` 인증·권한 체계를 적용한다.
- 콘텐츠 경로는 서버의 정확한 ID/파일명 allowlist와 resolved path containment 검사를 통과한 파일만 제공한다.

### 8.2 응답 예시

```json
{
  "load_case": {
    "load_case_id": "loadcase-drop-bottom-001",
    "load_case_name": "Bottom Face 800 mm Drop",
    "analysis_type": "DROP",
    "request_id": "request-drop-001",
    "request_name": "TV 포장 낙하 신뢰성 검증"
  },
  "source": "EXAMPLE_ADAPTER",
  "demo_only": true,
  "evaluation_source": "SYNTHETIC_DEMO",
  "contract_version": 1,
  "summary": {
    "total_scenes": 20,
    "pass_count": 9,
    "fail_count": 11,
    "open_cell": {"pass_count": 13, "fail_count": 7, "threshold": 75.0, "unit": "MPa"},
    "chassis_rear": {"pass_count": 12, "fail_count": 8, "threshold": 5.0, "unit": "mm"}
  },
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total_items": 20,
    "total_pages": 1,
    "has_previous": false,
    "has_next": false
  },
  "videos": [
    {
      "video_id": "drop-analysis",
      "scene_id": "drop-analysis",
      "scene_name": "기준 낙하 해석",
      "video_url": "/api/drop-videos/drop-analysis/content",
      "thumbnail_url": null,
      "duration": null,
      "file_size": 280714,
      "format": "mp4",
      "codec": "h264",
      "fast_start": true,
      "sort_order": 1,
      "drop_direction": null,
      "drop_condition": null,
      "analysis_version": null,
      "evaluation": {
        "overall_verdict": "PASS",
        "open_cell": {
          "critical_value": 68.2,
          "threshold": 75.0,
          "unit": "MPa",
          "verdict": "PASS",
          "metrics": {"top_edge": 68.2, "bottom_edge": 57.29, "left_edge": 60.7, "right_edge": 63.43}
        },
        "chassis_rear": {
          "critical_value": 4.2,
          "threshold": 5.0,
          "unit": "mm",
          "verdict": "PASS",
          "metrics": {"top_gap": 4.2, "bottom_gap": 3.53, "top_left_corner": 3.74, "top_right_corner": 3.91, "bottom_left_corner": 3.4, "bottom_right_corner": 3.65}
        }
      }
    }
  ]
}
```

### 8.3 API 처리 기준

- 서버는 `load_case_id`, `page`, `page_size`를 기준으로 영상 목록을 반환한다.
- Pydantic response model과 생성 OpenAPI가 `evaluation_source`, `contract_version`, `summary`, Scene 평가 계약을 명시한다.
- 기본 `page_size`는 20이다.
- 1차 어댑터는 allowlist에 있으면서 실제 존재하는 파일만 목록에 포함한다. 폴더 또는 일부 파일이 없어도 앱 시작은 실패하지 않는다.
- 예제 허용 하중 경우 외의 정상 하중 경우는 `200`과 빈 `videos` 목록을 반환한다. 존재하지 않는 하중 경우만 `404`다.
- 기본 정렬은 `sort_order`, 이후 `scene_id` 순으로 한다.
- 파일명에서 방향·조건·분류·해석 버전을 추정하지 않는다.
- 코덱과 fast-start는 파일명이나 확장자가 아니라 MP4 box probe로 판정한다.
- `summary`는 페이지 slice가 아닌 해당 하중 경우 전체 영상 평가를 집계한다.
- 페이지 단위 조회를 사용하여 전체 영상 링크를 한 번에 모두 내려보내지 않는다.
- 운영 DB 전환 후에도 이 응답 구조를 유지하고 `source`, `demo_only`로 출처를 구분한다.

---

## 9. 재생 동작 사양

| 동작 | 요구사항 | 난이도 |
|---|---|---:|
| 개별 재생 | 선택 영상의 `play()` 호출 | 낮음 |
| 개별 정지 | 선택 영상의 `pause()` 호출 | 낮음 |
| 전체 재생 | 현재 페이지 영상만 재생 | 낮음 |
| 전체 정지 | 현재 페이지 영상만 정지 | 낮음 |
| 전체 초기화 | 현재 페이지 영상의 `currentTime = 0` | 낮음 |
| 전체 처음부터 재생 | 초기화 후 현재 페이지 영상 재생 | 낮음 |
| 반복 재생 | 현재 페이지 영상의 `loop` 속성 제어 | 낮음 |
| 페이지 이동 | 이동 전 현재 페이지 영상 전체 정지 | 낮음 |
| 느슨한 동기 재생 | 동일 시작점에서 거의 동시에 재생 | 낮음~중간 |
| 정밀 동기화 | 재생 중 시간 편차 주기적 보정 | 선택·중간 |

### 9.1 전체 재생 기준

- 전체 재생은 현재 페이지에 표시된 영상에만 적용한다.
- 전체 재생 전 모든 영상의 준비 상태를 확인할 수 있다.
- 일부 영상이 로딩되지 않아도 나머지 영상은 재생 가능하도록 한다.
- 프레임 단위 정밀 동기화는 1차 범위에서 제외한다.
- 전체 처음부터 재생 시 모든 영상 시간을 0초로 맞춘 후 재생한다.

---

## 10. 로딩 및 성능 사양

| 항목 | 사양 |
|---|---|
| 페이지 진입 상태 | 모든 영상 기본 정지 |
| 초기 표시 | 첫 프레임 또는 포스터 이미지 |
| 기본 preload | `metadata` |
| 페이지 데이터 조회 | 최대 20개 영상 링크만 조회 |
| 전체 재생 시 | 현재 페이지 영상만 재생 |
| 페이지 이동 시 | 이전 페이지 영상 정지 및 참조 해제 |
| 동시 재생 수 | 최대 20개 |
| 성능 목표 | 20개 동시 재생 중 UI 버튼 반응 유지 |
| 실패 처리 | 일부 영상 실패 시 나머지는 정상 동작 |
| 캐시 | 동일 영상 반복 조회 시 브라우저 캐시 활용 |
| 정적 파일 전송 | HTTP Range 요청 지원 권장 |

---

## 11. 서버 요구사항

| 항목 | 요구사항 | 우선순위 |
|---|---|---:|
| 정적 영상 제공 | MP4/WebM URL 제공 | 필수 |
| MP4 MIME 타입 | `video/mp4` | 필수 |
| WebM MIME 타입 | `video/webm` | WebM 사용 시 필수 |
| HTTP Range 요청 | 구간 재생 및 탐색 지원 | 권장 |
| 영상 저장 위치 | 파일 서버 또는 정적 미디어 디렉터리 | 필수 |
| DB 저장 방식 | 영상 바이너리가 아닌 링크·메타데이터 저장 | 권장 |
| 인증 및 권한 | 기존 대시보드 권한 체계 재사용 | 필요 시 |
| 캐시 정책 | 반복 요청 최소화 | 권장 |

---

## 12. 오류 및 예외 처리

| 상황 | 처리 방식 |
|---|---|
| 등록 영상 0개 | “등록된 낙하 영상이 없습니다” 표시 |
| 영상 개수 1~20개 | 페이지네이션 없이 단일 페이지 표시 |
| 영상 개수 21개 이상 | 페이지네이션 표시 |
| 영상 링크 누락 | 해당 카드에 “영상 링크 없음” 표시 |
| 영상 로딩 실패 | 해당 카드만 오류 상태 표시 |
| 일부 영상 재생 실패 | 나머지 영상은 계속 재생 |
| DB의 영상 개수와 실제 레코드 수 불일치 | 실제 조회 수를 우선하고 서버 로그 기록 |
| 페이지 이동 중 재생 중 | 기존 페이지 영상 전체 정지 후 이동 |
| 사용자가 빠르게 페이지 이동 | 이전 API 요청 취소 또는 최신 요청 결과만 반영 |
| 영상 길이가 서로 다름 | 기본은 각각 종료, 반복 설정 시 개별 반복 |
| 형식 미지원 | 대체 MP4 링크 또는 오류 표시 |
| 요청 ID 없음 | 조회 영역 유지 및 안내 메시지 표시 |

---

## 13. 구현 범위

### 13.1 1차 필수 범위

- 기존 상세 분석 화면과 `DashboardWidget` 레이아웃 편집 체계 재사용
- `video_grid` 복합 위젯의 추가·이동·리사이즈·저장 지원
- 현재 하중 경우별 영상 목록 조회
- `video_example` 20개 allowlist와 인증 적용 콘텐츠 API
- 지정된 데모 하중 경우만 예제 영상 연결
- 페이지당 최대 20개 표시
- 영상 수 초과 시 페이지네이션
- 현재 페이지 영상 개수에 따른 동적 그리드
- 좌측 전체 판정 summary·20 Scene compact graph·선택 Scene 상세와 우측 4×5 영상 그리드
- 고정 `SYNTHETIC_DEMO` 평가 fixture와 출처·계약 버전 고지
- 카드별 Open Cell/Chassis Rear mini bar, overall PASS/FAIL badge와 green/red border
- 키보드 Scene 선택
- 개별 재생·정지
- 전체 재생·정지
- 전체 처음부터 재생
- 반복 재생
- 카드별 로딩·재생·정지·완료·오류 상태 표시
- 페이지·하중 경우 변경 및 unmount 시 영상 정지, ref 정리, 목록 요청 취소
- 실제 H.264/fast-start 20개 Chromium 재생과 Range 검증
- 재생 오류를 평가 판정 border와 분리하여 카드별로 격리

### 13.2 2차 개선 범위

- 운영 DB 영상 개수·링크·메타데이터 연동
- MP4(H.264/yuv420p/faststart) 입수 검증 및 필요 시 변환 파이프라인
- WebM(VP9) 지원
- WebP 포스터 이미지
- 페이지당 영상 수 선택
- 선택 영상만 일괄 재생
- 영상 확대 보기
- 재생 위치 슬라이더
- 배속 조절
- 낙하 방향·조건 필터
- 영상 정렬 기능
- 정밀 동기화
- MP4→WebM 자동 변환
- 화면 밖 영상 자동 정지
- 즐겨찾기 또는 비교 대상 선택

---

## 14. 기능별 예상 작업량

> 아래 작업량은 운영 DB·배포 안정화까지 포함한 최초 추정 참고값이다. 1차 PoC는 기존 위젯 체계를 재사용해 별도 화면 골격과 DB 스키마 작업을 제외했다.

| 작업 | 난이도 | 예상 시간 |
|---|---:|---:|
| 제품과제·의뢰별 영상 API 연결 | 낮음~중간 | 1~3시간 |
| DB 영상 개수 및 페이지 정보 연동 | 낮음~중간 | 1~2시간 |
| 낙하 씬별 영상 카드 구성 | 낮음 | 1~2시간 |
| 동적 그리드 구현 | 낮음 | 1~2시간 |
| 페이지네이션 구현 | 낮음~중간 | 1~2시간 |
| 개별 재생·정지 | 낮음 | 0.5~1시간 |
| 전체 재생·정지·초기화 | 낮음 | 1~2시간 |
| 반복 재생 및 상태 표시 | 낮음 | 0.5~1시간 |
| 페이지 이동 시 영상 정리 | 낮음~중간 | 0.5~1시간 |
| 로딩·오류 처리 | 중간 | 1~2시간 |
| 20개 동시 재생 성능 테스트 | 중간 | 1~3시간 |
| Edge·Chrome 검증 | 낮음 | 1시간 |
| 배포 설정 점검 | 낮음~중간 | 1~2시간 |

### 예상 총 작업량

- 최소 PoC: 약 4~6시간
- 기존 대시보드 정상 통합: 약 8~14시간
- 배포 및 성능 안정화 포함: 약 1~2일
- 정밀 동기화 및 자동 영상 변환 포함: 약 2~4일

---

## 15. 완료 기준

다음 조건을 모두 만족하면 1차 개발 완료로 본다.

1. 기존 프로젝트·의뢰·하중 경우 선택을 바꾸면 위젯이 현재 `load_case_id`로 다시 조회한다.
2. 지정된 데모 하중 경우에서 존재하는 allowlist 예제 20개가 순서대로 조회된다.
3. 한 페이지에 최대 20개 영상만 표시된다.
4. 영상이 20개를 초과하면 다음·이전 페이지 이동이 가능하다.
5. 현재 페이지 영상 개수와 화면 폭에 따라 그리드가 자동 조정된다.
6. 현재 페이지 전체 재생·정지·처음부터·반복 제어가 각 video element에 전달된다.
7. 각 영상은 native controls로 개별 제어할 수 있고 상태를 카드에 표시한다.
8. 페이지·하중 경우 변경 및 unmount 시 기존 영상이 정지되고 요청/ref가 정리된다.
9. 영상 링크·코덱 오류가 발생해도 해당 카드만 오류가 되고 다른 카드와 위젯은 계속 동작한다.
10. `video_grid`를 기존 카탈로그에서 변수 바인딩 없이 추가하고 기존 레이아웃 버전으로 저장할 수 있다.
11. 다른 정상 하중 경우는 예제 영상을 재사용하지 않고 `200`과 빈 목록을 표시한다.
12. 콘텐츠 API는 allowlist 밖 ID와 누락 파일에 `404`를 반환하고 경로 이탈을 허용하지 않는다.
13. 응답은 `SYNTHETIC_DEMO`와 contract v1을 고지하며 고정 20 Scene 결과를 overall PASS 9 / FAIL 11로 반환한다.
14. Open Cell 75.0 MPa는 PASS이고 그 초과는 FAIL, Chassis Rear 5.0 mm는 FAIL이고 그 미만만 PASS다.
15. 좌측 요약·Scene 판정 그래프·선택 상세와 우측 4×5 영상이 한 복합 위젯에 표시되고 3/2/1열로 반응한다.
16. 각 카드의 두 mini bar와 텍스트 badge가 평가 근거를 표시하고 overall PASS/FAIL이 green/red 2px border를 결정한다.
17. PASS 카드 영상 재생이 실패해도 green 평가 border를 유지하며 playback 오류는 frame 내부에만 표시된다.
18. 실제 20개가 Chromium에서 decode·play되어 재생 시간이 증가하고 전체 정지·처음부터·반복 제어가 전달된다.

---

## 16. Codex 전달용 구현 요청문

```text
기존 해석 결과 대시보드의 DashboardWidget/react-grid-layout 구조를 유지하고 full-width video_grid 복합 위젯을 추가한다. 기존 프로젝트·의뢰·하중 경우 선택기를 재사용하고 위젯 안에 중복 선택기를 만들지 않는다.

1차는 video_example 폴더의 정확한 20개 allowlist를 인증 적용 API로 제공하며 loadcase-drop-bottom-001에만 연결한다. 다른 하중 경우에는 200과 빈 목록을 반환한다. DB 스키마와 원본 파일은 변경하지 않는다.

한 페이지에 최대 20개를 표시한다. video_grid 내부는 좌측 synthetic 평가 summary/20 Scene graph/선택 상세와 우측 4×5 영상 그리드로 나누고 1200px 이하에서 3/2/1열로 반응한다. 각 카드에는 Open Cell/Chassis mini bar, overall badge, native controls, 상태, 독립 오류 UI를 둔다. 전체 재생·정지·처음부터·반복을 제공한다.

평가는 `SYNTHETIC_DEMO`, contract v1 고정 fixture이며 실제 overview 결과와 분리한다. Open Cell은 75 MPa 초과, Chassis Rear는 5 mm 이상을 FAIL로 판정하고 subsystem 하나라도 FAIL이면 overall FAIL이다. 평가 border는 green/red로 유지하며 playback 오류가 판정 색상을 덮지 않게 한다.

preload="metadata", playsInline, muted를 적용한다. 페이지·하중 경우 변경 또는 unmount 때 기존 영상을 정지하고 AbortController와 video ref를 정리한다. 현재 예제 20개는 H.264/yuv420p/faststart이며 stdlib MP4 probe와 Chromium 실재생으로 검증한다.
```
