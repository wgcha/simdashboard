# PhysicsAI 플랫폼 연결 기술 계획

- 기준일: 2026-10-09
- 상태: **계획(구현 전)**. 사용자 결정(§6) 후 P0부터 착수한다.
- 요청: "E:\physicsai_platform 여기 프로젝트 문서 확인하고 연결에 필요한 기술 계획"
- 근거 저장소
  - A(연결 대상): `wgcha/physicsai-platform` HEAD `dec451e` — `README.md`, `AGENTS.md`, `docs/contracts/platform.md`(이하 **PA §n**), `docs/decisions.md`, `docs/e2e-checklist.md`, `config/platform.example.yaml`, `backend/openapi.json`(경로 35개), `backend/physicsai_api/auth.py`
  - B(이 저장소): 브랜치 `claude/scx-drive` HEAD `fd42618` — [`../../AGENTS.md`](../../AGENTS.md), [Windows 배포 정책](../windows-deployment-policy.md), [깊이 스키마](../contracts/depth-schema.md), [최종확정](../features/case-finalization.md), [SCX 드라이브](../features/scx-drive.md), [알림](../features/notifications.md)
- 표기: **[확인]** = 문서·코드로 확인, **[가정]** = 추정(§6에서 확인 필요)

## 1. 요약

| 구분 | 내용 |
|---|---|
| 대시보드(B) | 해석자가 SPDM `Project/Request(환경)/Working/Case/…/Scene` 결과를 등록·비교하고, Case를 Final로 지정하면 `Final/CAE`·`Final/Report`에 복사·보고서를 남긴다(depth-schema D10–D12, case-finalization). 로그인·프로젝트·멤버십의 원천 |
| PhysicsAI(A) | Altair PhysicsAI(edspy) 대리모델 플랫폼. 1차 = ③ 데이터셋(h3d → train/eval `.psdata`, 10% 홀드아웃) → 학습 패키지 → HPC 학습(플랫폼 밖) → 모델 등록·평가·Final 모델, ④ 단일 예측(파라미터 세트 → SimLab → Radioss 입력 → `edspy --predict-write` → 컨투어·커브·응답 표). 2차 = ① 학습데이터 생성 ② 정리 ⑤ 최적화, PBS, 오프라인 배포 (PA §3) [확인] |
| 이미 정해진 연결 | A는 **대시보드와 같은 Windows Server, 같은 주소의 `/physicsai/`** 에서 동작하고, 대시보드 쿠키 `analysis_canvas_session`을 받아 대시보드 `GET /api/auth/me`로 매 요청 신원을 확인한다(30초 캐시). Study는 대시보드 `project_id`에 속한다. **1차 A 구현에는 대시보드 코드 변경이 필요 없다**(PA §1, §5, §20) [확인] |
| 연결 목표 | ① 해석 결과(h3d 등)를 PhysicsAI 학습 입력으로 넘기는 **데이터 인계 계약**, ② 대시보드 화면에서 PhysicsAI로의 **이동·상태 표시**, ③ Case 결과 옆 **AI 예측 참고값 표시**, ④ 운영 배포(같은 서버의 Caddy·PostgreSQL·서비스) **공존 규칙** |
| 사용자 시나리오 가치 | 해석자 결과 → 비교 → Final → SPDM 흐름에서 검증된 Final 결과가 AI 학습 데이터로 재사용되고(출처 해시 유지), 예측값을 실제 해석 결과와 같은 화면에서 비교한다 [가정: §6 Q1] |

핵심 판단: **공유 DB·공유 프로세스 없이 느슨하게 잇는다.** 신원은 이미 쿠키 introspection으로 해결되어 있으므로, 새로 필요한 것은 (1) 버전 있는 **데이터셋 내보내기 manifest**와 파일 인계 폴더, (2) PhysicsAI 작업에 대시보드 출처 참조(`dashboard_ref`)를 싣는 작은 계약 확장, (3) 운영 배포 공존 ADR이다.

## 2. 현재 상태 비교

| 항목 | 대시보드(B) | PhysicsAI(A) | 연결 시 의미 |
|---|---|---|---|
| 스택 | React+Vite+TS / FastAPI / Alembic, PostgreSQL(운영)·DuckDB(개발) | React+Vite+TS(base `/physicsai/`) / FastAPI(`/physicsai/api`) / 별도 워커 / PostgreSQL 전용 (PA §4) | 기술 스택 동일, 저장소·프로세스 분리 |
| 인증 | 서명 토큰 쿠키 `analysis_canvas_session`(`backend/app/security.py:45`), `AUTH_MODE=disabled|password|oidc`(`config.py:281`), `GET /api/auth/me`(`routers/security.py:311`) | 쿠키 → 대시보드 `/api/auth/me` Bearer introspection, 30초 캐시, 비GET은 `X-PhysicsAI-Request: 1` (PA §5.2, §5.4, `auth.py`) | SSO 이미 설계됨. 대시보드는 쿠키 이름·`/api/auth/me`·`/api/projects` 필드를 **고정 계약**으로 지켜야 함(PA §20 D2–D4) |
| 권한 | 프로젝트 역할 general/power/admin, 회사 권한(`access_policy.py:100`), 전역 관리자 | 조회 = ACTIVE 전원, 실행 = power 이상, 대기열 관리 = 전역 관리자 (PA §5.3) | 역할 체계 재사용, DB 복제 없음 |
| DB | 앱 DB(`dashboard_cases`·`dashboard_captures`·`finalization_operations`·`notifications` 등, migration 0041까지) | 별도 DB `physicsai`(`studies`·`datasets`·`models`·`param_sets`·`jobs`·`job_steps`·`artifacts`·`notifications`·`worker_slot`·`hpc_jobs`·`audit_events`) (PA §6) | **공유 DB 금지 유지**. id는 문자열 참조만(FK 없음) |
| 배포 | `deploy.bat`/`update.bat`, 폐쇄망 설치 패키지(Python·PG·Caddy·서비스 래퍼 포함), 서비스는 LocalService, PG `127.0.0.1:55432`, API `apiPort`(예 18080) (`deploy/windows/offline/install.example.json`, `install.ps1:82`) | 1차 = 개발 실행 스크립트만, 백엔드 `127.0.0.1:8100`, 오프라인 wheel·deploy.bat·서비스 등록·Caddy 반영은 2차 (PA §4.2, §4.6) | 운영 공존 규칙이 아직 없다(§4.4) |
| 파일 저장 | SPDM 루트(local `SIMDASH_SPDM_ROOT` 또는 scx 드라이브 `SIMDASH_SCX_DRIVE_ROOT`), 앱 쓰기는 `Final/` 아래뿐(D11, D14) | AI 루트(`storage.ai_root`, 예 `E:/shared/AI_WORK`)에만 쓰기, **SPDM 읽기·쓰기 모두 없음**, AI 루트·허용 루트는 SPDM 루트와 겹치면 설정 오류 (PA §15, §17.7) | 결과 파일은 **대시보드가 AI 루트 하위 인계 폴더로 내보내야** 한다(PhysicsAI가 SPDM을 직접 읽지 않음). scx 모드에서는 SPDM이 로컬 경로가 아니므로 더더욱 대시보드만 읽을 수 있다 |
| 작업 실행 | Final 복사 데몬 스레드(`case_finalization_jobs`), 드라이브 업로드 대기열·SCX 워커 프로세스, 관리형 로컬 실행(`managed_local_execution.py`) | 전역 슬롯 1개 FIFO 대기열, lease 워커, Windows Job Object(32코어/64GB 기본), HPC 게이트웨이(기본 none) (PA §7, §11, §12) | 같은 서버 CPU·메모리 경합(§7 R5) |
| 알림 | `notifications` 표, 60초 폴링, 90일 보관 (features/notifications.md) | 자체 `notifications`, 10초 폴링, 30일 보관 (PA §13) | 벨 2개 → 읽기 전용 합산 표시 권장(§3e) |
| 단위 | 결과 JSON 키에 단위 문자열(예 `Wobble Disp. (mm)`), 변환 없음 | mm-ton-s 고정, 변환 없음 (PA §2) | manifest에 단위 명시, 변환 금지 |

## 3. 연결 지점 후보

| # | 연결 | 데이터 흐름 | 필요한 계약 | 바뀌는 쪽 | 위험 | 판단 |
|---|---|---|---|---|---|---|
| a | **데이터셋 내보내기** | 대시보드 Final(또는 등록 Case) → 저장소 공급자로 읽기 → `<ai_root>/_exchange/dashboard/<export_id>/`에 h3d·manifest 쓰기 → PhysicsAI ③-1 `DATASET_CREATE.input_path`로 지정 | manifest v1(§4.2), 인계 폴더 경로·이름 규칙 | B: 내보내기 서비스·API·화면, 새 쓰기 구역 `EXPORT`. A: 1차는 **변경 없음**(`_exchange`는 `_`로 시작해 Study 폴더와 겹치지 않고, AI 루트 하위 폴더라 B23을 통과) | 대용량 복사, 이름의 공백·한글·`[]`(A는 공백·cmd 메타문자 거부, PA §9.1·§17.3), 같은 run 여러 h3d의 홀드아웃 누설(U12) | **P1 채택** |
| b | 작업 실행·감시 | 대시보드 화면 → `/physicsai/p/:projectId/s/:studyId/stage/3?input=<export>` 이동, 또는 대시보드 프런트가 `/physicsai/api/studies/{id}/jobs` 직접 호출 | 딥링크 쿼리 규칙(A 프런트), 내보내기 상태 표시 | A: 쿼리로 입력 경로 채우기(소규모). B: 버튼·링크 | 실행 UI 중복 시 두 화면 계약이 갈라짐 | **P2: 딥링크만**, 실행은 A 화면에서 |
| c | 예측값 표시 | 대시보드 Case 결과·비교 화면 → 같은 origin `GET /physicsai/api/jobs?dashboard_case_id=…` → `result.response_table` | A: PREDICT `params.dashboard_ref{project_id, request_id, case_id, scene?}`(선택) + 목록 필터. B: 읽기 전용 패널 | A·B 모두 소규모 | 응답 이름 ↔ 결과 키 대응 없음(§6 Q5), A 장애 시 패널 오류 | **P3** |
| d | SSO·계정 | 이미 동작(쿠키 introspection) | 대시보드 측 계약 고정 시험, 운영 포트 설정 | B: 계약 시험만. A: 운영 설정 `dashboard_internal_url` | 운영 API 포트 불일치(A 기본 `:8000`, B 설치 예 `apiPort 18080`), 로그아웃 후 최대 30초 캐시·서명 토큰 만료 전 재사용 | **P0(시험)·P4(운영값)** |
| e | 알림 통합 | 대시보드 벨이 `GET /physicsai/api/notifications/unread-count`를 함께 읽어 "AI n건" 표시·링크 | 없음(기존 API) | B: 프런트만 | A 미설치·장애 시 조용히 숨김 필요 | **P4**. 서비스 간 쓰기(대시보드 알림 표에 A가 기록)는 서비스 토큰·DB 쓰기 경계가 생겨 **채택 안 함** |
| f | SCX 드라이브 공유 | A가 드라이브에서 직접 읽기/쓰기 | 어댑터 계약 공유 | A: 어댑터 도입 | 어댑터는 대시보드 단일 프로세스·외부 wheel(ADR 0006), 토큰 공유 위험 | **채택 안 함**. 드라이브 결과는 (a)가 대시보드 공급자로 읽어 인계 |

서비스 토큰·OIDC 클라이언트 신설은 (a)–(e) 어디에도 필요하지 않다. 비동기 서버 간 호출이 생길 때(예: A 워커가 대시보드에서 파일을 끌어오기)만 필요하며 이 계획은 그런 호출을 만들지 않는다.

## 4. 권장 아키텍처

### 4.1 구성

```text
Browser ── https://<서버>/ ─┬─ /api/*, 나머지 ─▶ 대시보드(FastAPI :apiPort, 정적 dist)
                            ├─ /physicsai/api/* ─▶ PhysicsAI API 127.0.0.1:8100
                            └─ /physicsai/*     ─▶ PhysicsAI 정적 dist
PhysicsAI API ── GET /api/auth/me, /api/projects (루프백) ─▶ 대시보드        (기존 설계)
대시보드 내보내기 작업 ── StorageProvider(local|scx) 읽기 ─▶ SPDM Final/CAE
                       └─ 쓰기 ─▶ <ai_root>/_exchange/dashboard/<export_id>/   (새 EXPORT 구역)
PhysicsAI 워커 ── DATASET_CREATE input_path = 위 폴더/h3d ─▶ <ai_root>/<study>/03_dataset/…
```

- 공유 DB 없음, 공유 프로세스 없음, 서버 간 쓰기 API 없음. 두 앱이 공유하는 것은 **쿠키(같은 origin)**, **루프백 읽기 API 2개**, **인계 폴더 1개**뿐이다.
- 대시보드는 인계 폴더에 **쓰기만** 하고 PhysicsAI 산출물(`<study>/…`)을 읽거나 지우지 않는다. PhysicsAI는 인계 폴더를 **읽기만** 한다.

### 4.2 데이터셋 내보내기 계약 `simdashboard.physicsai.dataset-export` v1

인계 폴더:

```text
<ai_root>/_exchange/dashboard/<export_id>/        # export_id = 32자리 hex
  export.json                                      # manifest (마지막에 공개)
  h3d/<run_key>/<scene_key>__<file_key>.h3d        # ASCII 이름만: ^[A-Za-z0-9_\-]{1,64}
  samples.csv                                      # 선택: run_key,<파라미터…>,resp:<응답…> (PA §15.4 형식)
  COMPLETE                                         # 내용 = export.json sha256
```

- `h3d/<run_key>/` 단위가 PhysicsAI `split_group=parent_dir`의 그룹이 되어 같은 run의 h3d가 학습·평가에 나뉘지 않는다(U12 대응).
- 이름은 대시보드가 만든 ASCII 키만 쓴다(Case·Scene 원래 이름은 manifest에만). 공백·한글·`[]`·cmd 메타문자 경로 문제를 피한다(PA §9.1, §17.3, B24).
- 공개 방식은 Final과 같다: `<export_id>.staging/`에 쓰고 해시 확인 후 `rename_no_replace`로 `<export_id>/` 공개, `COMPLETE`는 마지막. 기존 폴더는 덮어쓰지 않는다.

`export.json` 필드(요약):

| 필드 | 내용 |
|---|---|
| `schema`, `schema_version` | `"simdashboard.physicsai.dataset-export"`, `1`. 알 수 없는 메이저 버전은 소비 측 거부 |
| `export_id`, `created_at`(UTC), `created_by{user_id, username}` | 감사·표시 |
| `source` | `{app_version, storage_mode: "local"|"scx", project_id, project_name, request_id, request_folder, environment: "USAGE"|"DISTRIBUTION", basis: "FINAL"|"WORKING_LATEST", finalization_id?, complete_json_sha256?}` |
| `unit_system` | `"mm-ton-s"`(변환 없음, 결과 키의 단위 문자열은 `responses[].unit`에 그대로) |
| `runs[]` | `{run_key, case_id, case_name, load_case?, run?, run_option?, source_rel(의뢰 기준 상대경로), scenes[{scene_key, scene_name, files[{rel, size, sha256, source_hash{alg:"sha256"|"sha1"|null, value}, source_version_token?}]}], parameters{name: number}|null, responses{name: number}|null}` |
| `parameters[]`, `responses[]` | `{name(^[A-Za-z_][A-Za-z0-9_]{0,63}$), unit, source}` — `source`는 `run_conditions:<key>`·`capture:<key>`처럼 값의 출처 |
| `totals` | `{runs, files, bytes}` |
| `excluded[]` | 제외된 run·파일과 사유(h3d 없음, 원본 변경, 이름 충돌 등) |

출처 해시: local Final은 `complete.json`의 sha256을 그대로 쓰고, 복사 중 다시 계산해 일치를 확인한다. scx Final은 완료 기록에 수집 결과 외 파일의 sha256이 없고 드라이브 `sha1`만 있으므로(case-finalization §scx) `source_hash.alg="sha1"`로 적고 복사하며 sha256을 새로 계산한다.

### 4.3 보안

| 항목 | 규칙 |
|---|---|
| 권한 | 내보내기 실행 = 해당 프로젝트 `result.import`(power 이상) 또는 전역 관리자. 조회 = `project.data.view`. PhysicsAI 실행 권한(power)과 같은 수준 |
| 경로 | 인계 루트는 설정 `SIMDASH_PHYSICSAI_EXCHANGE_ROOT`(절대경로, 공백·메타문자 금지, SPDM 루트와 비중첩, 릴리스 폴더 밖). 저장소 공급자에 새 쓰기 구역 `EXPORT`를 두고 기존 FINAL·LEGACY와 같은 고정·reparse 거부·no-replace 규칙 적용. 사용자 입력 경로는 받지 않는다(Case id만) |
| 비밀 | 새 비밀 없음. 대시보드는 PhysicsAI를 호출하지 않으므로 토큰 저장이 필요 없다. 나중에 서비스 토큰이 필요해지면 드라이브 자격 증명과 같은 `SIMDASH_SECRET_ENC_KEY` Fernet 저장(`services/drive/token_store.py`) 방식을 따른다 |
| 감사 | 대시보드 `audit_events`에 `physicsai.export.create|complete|fail` 기록(export_id, case 수, bytes). PhysicsAI는 자체 `audit_events`에 작업 생성 기록(PA §17.6) |
| 프런트 직접 호출 | 대시보드 프런트가 `/physicsai/api` GET만 호출(같은 origin, 쿠키). 비GET 호출은 하지 않는다(실행은 A 화면) |
| 보안 검수 | P1은 파일 쓰기 경계 변경 → `security-diff-scan` 대상(AGENTS.md Codex Security) |

### 4.4 폐쇄망 Windows 배포 영향

| 항목 | 현재 | 필요한 결정·변경 |
|---|---|---|
| Caddy | 대시보드 패키지가 `Caddyfile.intranet.template`을 소유, `/physicsai/` 처리 없음 | 대시보드 템플릿에 **선택 include**(`import <state>/caddy.d/*.caddy`) 추가 → PhysicsAI 설치기가 자기 조각만 둔다. 대시보드 업데이트가 조각을 지우지 않게 `state` 아래 둔다. 배포 계약 변경이므로 **ADR 0007 + 배포 정책 + 계약 CI** 동시 갱신(AGENTS.md 배포 계약 7) |
| PostgreSQL | 대시보드 번들 PG(`127.0.0.1:55432`), 정책상 "다른 DB를 변경하지 않는다" | 같은 클러스터에 별도 DB·역할을 둘지(Q7), 별도 클러스터일지. 같은 클러스터면 대시보드 설치·업데이트·백업이 `physicsai` DB를 건드리지 않음을 계약 시험에 추가하고, PhysicsAI 백업은 A 쪽 책임 |
| 포트 | 대시보드 API `apiPort`(예 18080), 웹 8080 | PhysicsAI `auth.dashboard_internal_url`을 운영 `apiPort`로 설정(기본 `:8000`은 개발값). 8100 충돌 검사는 A 설치기 |
| 서비스 계정 | 대시보드 서비스는 LocalService만 허용(`install.ps1:82`) | PhysicsAI 워커는 Altair 라이선스·GPU·HyperWorks 배치 때문에 1차에 로그온 사용자 콘솔 실행(U11). 인계 루트에 LocalService 쓰기 권한, PhysicsAI 워커 계정 읽기 권한 필요 |
| 기본 동작 | — | `SIMDASH_PHYSICSAI_MODE=none`(기본)이면 메뉴·패널·내보내기 API가 숨김/409. PhysicsAI가 없는 설치는 지금과 같다 |
| 의존성 | — | 대시보드 신규 의존성 없음(표준 라이브러리·기존 공급자). lock 변경 없음 |

### 4.5 실패 모드

| 상황 | 동작 |
|---|---|
| PhysicsAI 미설치·중지 | 대시보드 패널·벨 합산은 숨김 또는 "AI 플랫폼 연결 안 됨" 회색 문구. 대시보드 기능 영향 없음 |
| 대시보드 중지 | PhysicsAI 전체 503 `DASHBOARD_UNREACHABLE`(A 설계). 운영 안내에 명시 |
| 내보내기 중 원본 변경 | 파일 단위 `SOURCE_STALE` → 그 run 제외 또는 전체 실패(옵션), `COMPLETE` 없음 |
| 디스크 부족 | Final과 같은 사전 공간 검사(507), 아무것도 쓰지 않음 |
| 부분 폴더 잔존 | `.staging`만 남고 공개 폴더 없음. 관리자 정리(자동 삭제 없음, D14와 같은 원칙) |
| manifest 버전 불일치 | A 쪽 확인 단계(`paths/inspect` 확장 또는 수동)에서 거부 |
| 쿠키·`/api/auth/me` 형식 변경 | P0 계약 시험이 대시보드 CI에서 실패하게 해 사전 차단 |

## 5. 단계별 계획

| 단계 | 산출물 | 바뀌는 쪽 | 규모(추정) | 시험 | 완료 기준 |
|---|---|---|---|---|---|
| **P0 계약·확인** | (1) 이 문서 결정 반영 (2) 대시보드 계약 시험 `test_physicsai_auth_contract.py`: 쿠키 이름·속성, `/api/auth/me` 필드·Bearer 허용, `/api/projects` 필드·비멤버 조회 (3) manifest v1 JSON Schema 파일·예시 (4) 실제 결과 표본 확인: 유통/사용 Scene 폴더에 h3d가 있는지, 파라미터 출처(Q2–Q4) | B 시험·문서, A 문서(계약 §20에 manifest 참조) | 2–3일 | pytest(합성 사용자·프로젝트), 스키마 검증 | 계약 시험 통과, Q1–Q7 답 기록 |
| **P1 데이터셋 내보내기** | 서비스 `physicsai_export.py`(계획 미리보기 → 확정 → 작업 스레드 복사·해시·공개, Final 복사 구조 재사용), API `POST /api/physicsai/exports/preview`, `POST …/exports`, `GET …/exports/{id}`, 공급자 `EXPORT` 구역, 설정 `SIMDASH_PHYSICSAI_MODE`·`SIMDASH_PHYSICSAI_EXCHANGE_ROOT`, Case 결과 헤더 "AI 학습 데이터로 내보내기" 창(데스크톱), 감사·알림(`PHYSICSAI_EXPORT` 종류) | B | 6–9일 | 격리 임시 SPDM·인계 루트, 합성 h3d(local), 가짜 드라이브 어댑터(scx), 이름 정규화·충돌, 원본 변경, 공간 부족, reparse 거부, no-replace, 재시작 이어하기; Codex `security-diff-scan` | 합성 Final 1건 → `export.json`·`COMPLETE` 생성, 해시 일치, PhysicsAI `paths/inspect DATASET_INPUT`가 h3d 수를 맞게 보고(가짜 도구), 정적 시험으로 EXPORT 외 쓰기 없음 |
| **P2 실행 이동** | 내보내기 완료 창·Case 헤더에 "PhysicsAI에서 열기" 딥링크(`/physicsai/p/<project>/…?import=<export_id>`), A 프런트가 쿼리로 ③-1 입력 경로를 채우고 `split_group=parent_dir` 기본 선택 | A 프런트 소규모, B 링크 | 2–3일 | A vitest, B e2e(링크·모드 none 숨김) | 클릭 한 번으로 A ③-1 카드에 경로가 채워짐 |
| **P3 예측 표시** | A: PREDICT `params.dashboard_ref`(선택) + `GET /jobs?dashboard_case_id=`(+openapi), A ④ 화면에서 "대시보드 Case 연결" 선택. B: Case 결과·Case 비교에 "AI 예측(참고)" 읽기 전용 열/패널(최근 SUCCEEDED PREDICT의 `response_table`, 모델 이름·버전·학습 범위 밖 여부 표시) | A·B | 4–6일 | A API 시험, B 컴포넌트·e2e(가짜 `/physicsai/api` 응답, 401/503/빈 목록) | 예측 응답이 결과 키와 대응 규칙(Q5)대로 한 화면에 표시, A 중지 시 대시보드 정상 |
| **P4 운영 공존** | ADR 0007(Caddy include·PG 공존·포트·`SIMDASH_PHYSICSAI_MODE`), 배포 정책·설치 안내·계약 CI 갱신, 대시보드 벨의 AI 미읽음 합산, 사이드바 외부 링크 "AI 예측"(PA §20 D5), 로그인 후 복귀 `?next=`(D6, 같은 origin 상대경로만) | B 배포·프런트, A 설치기(2차) | 3–5일(+A 오프라인 설치기는 A 2차 범위) | 배포 계약 CI, 업데이트가 `state/caddy.d`·`physicsai` DB를 보존하는지 시험 | **실제 Server 2022 폐쇄망 신규 설치·업데이트·재부팅 검증은 별도 기록 전까지 미수행으로 남긴다** |

범위 밖: PhysicsAI가 SPDM을 직접 읽기/쓰기, 공유 DB·스키마, 대시보드 프로세스 안에서 edspy/Altair 실행, PhysicsAI 결과를 SPDM `Final/`에 쓰기, 서버 간 알림 쓰기·서비스 토큰, SCX 어댑터 공유, ①②⑤·PBS 연동(A 2차), 모바일 화면.

## 6. 결정 필요 사항

1. **학습 데이터 원천**: PhysicsAI 학습 h3d가 대시보드 해석자 결과(Final/CAE)에서 오는가, 아니면 A의 ① HyperStudy DOE(2차)가 따로 만드는가? 전자가 아니라면 P1의 가치가 낮아 P3(예측 표시)를 먼저 한다.
2. **내보내기 기준**: Final만(검증·해시 있음, 권장) / Working 최신 결과도 허용?
3. **대상 파일**: Scene 폴더의 `*.h3d`만인가, 특정 Scene(예 `2_Face`)·특정 결과 파일만인가? 운영 결과 폴더에 h3d가 실제로 있는지 표본 확인이 필요하다.
4. **설계 파라미터 출처**: PhysicsAI ④ `samples.csv`의 파라미터(예 `THK_1`) 값을 대시보드가 알 수 있는가(`run_conditions` 메타데이터, Case 이름 규칙, 별도 표)? 없으면 manifest는 h3d·응답값만 싣는다.
5. **응답 이름 대응**: PhysicsAI 응답 이름(`responses.json`)과 대시보드 결과 키(예 `Wobble Disp. (mm)`)를 어떻게 맞출지 — 대시보드 변수 카탈로그에 대응표를 둘지, PhysicsAI 파라미터 세트에 둘지.
6. **인계 루트 위치**: `<ai_root>/_exchange/dashboard/`로 둘지(A 변경 없음), A 설정 `allowed_import_roots`에 별도 폴더를 추가할지(A 계약 변경 필요: 현재 ③-1 입력은 AI 루트 하위만).
7. **운영 배포 공존**: 같은 PG 클러스터에 `physicsai` DB를 둘지 별도 클러스터일지, Caddy는 include 조각 방식(ADR 0007)으로 할지, 대시보드 운영 API 포트를 PhysicsAI 설정에 어떻게 전달할지.
8. **알림**: 대시보드 벨에 AI 미읽음 수 합산(읽기 전용)으로 충분한가, 아니면 별도 벨 유지인가.
9. **U10 확인(A 쪽)**: PhysicsAI 대기열 관리 권한을 전역 관리자로 해석한 것이 맞는가 — 대시보드 역할 표기와 맞추기 위해.

## 7. 위험

| # | 위험 | 영향 | 완화 |
|---|---|---|---|
| R1 | 대시보드 인증 계약(쿠키 이름·`/api/auth/me`·`/api/projects`) 무심코 변경 | PhysicsAI 전체 로그인 실패 | P0 계약 시험, PA §20 D2–D4를 이 저장소 문서에 고정 |
| R2 | 해석자 결과가 PhysicsAI 학습 형식(DOE 다수 run)과 맞지 않음 | P1 무용 | Q1·Q3 먼저 확인, 표본 1건으로 A 가짜 도구가 아닌 실제 edspy 데이터셋 생성 확인(A E3-2) |
| R3 | 대용량 h3d 복사 시간·디스크 | Final 복사와 같은 서버 I/O 경합 | 사전 공간 검사, 작업 스레드 동시성 제한(Final과 공유), 같은 볼륨이면 하드링크 검토(Q6) |
| R4 | 경로 규칙 불일치(공백·한글·`[]`·260자) | A 단계에서 `INPUT_INVALID` | ASCII 키 이름, 짧은 경로, manifest에 원래 이름 |
| R5 | 같은 서버 자원 경합(A 워커 기본 32코어/64GB, GPU) | 대시보드 응답 저하 | A 워커 Job Object 한도 조정, 운영 측정 후 결정 |
| R6 | 로그아웃 후 30초 캐시·서명 토큰 만료 전 재사용 | 짧은 권한 잔존 | 현행 대시보드도 서명 토큰이라 동일 수준. 필요 시 D7 introspect 엔드포인트(`exp` 포함) |
| R7 | 배포 계약 확대(Caddy·PG 공존) | 업데이트가 A 상태를 지우거나 반대 | ADR 0007, state 경로 보존 시험, 실기 검증 전 "미수행" 기록 |
| R8 | scx 모드 Final은 sha256이 없음 | 출처 해시 약화 | `source_hash.alg="sha1"` + 복사 시 sha256 계산, manifest에 `storage_mode` |
| R9 | A 1차 구현 진행 중(계약 변경 메모 B1–B26 미반영분) | 연결 계약 흔들림 | P3 착수 전 A `openapi.json` 버전 고정, 대시보드는 GET 필드만 의존 |

## 8. 관련 문서

- PhysicsAI: `docs/contracts/platform.md` §1·§4·§5·§15·§17·§19·§20, `docs/decisions.md`(2026-10-08 별도 플랫폼 확정)
- 대시보드: [최종확정](../features/case-finalization.md), [깊이 스키마](../contracts/depth-schema.md) D10–D14, [SCX 드라이브](../features/scx-drive.md), [알림](../features/notifications.md), [저장소 공급자 계약](../contracts/storage-provider.md), [Windows 배포 정책](../windows-deployment-policy.md), [ADR 0006](../adr/0006-scx-drive-adapter-external-wheel.md)
- 과거 구상(현재 기준 아님): [통합 해석 워크벤치 확장 계획](../integrated-simulation-workbench-plan.md) §4.3·§5 — PhysicsAI를 대시보드 안에 통합하는 안은 PhysicsAI 결정(2026-10-08 "별도 플랫폼")으로 대체됨
