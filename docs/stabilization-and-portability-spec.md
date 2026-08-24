# 안정화·이식성 사양

## 1. 목적

기존 기능을 보존하면서 편집 기능의 회귀를 자동 검증하고, 운영·워크플로·해석·PPT 편집 상태를 분리한다. 이후 동일한 API 계약으로 DuckDB와 PostgreSQL을 선택할 수 있고, PostgreSQL이 설치된 다른 PC에도 재현 가능한 방식으로 이관할 수 있어야 한다.

## 2. 우선순위와 완료 기준

1. 핵심 편집 E2E
   - 운영 대시보드 취소·저장·새로고침 복원
   - 의뢰 진행 상태의 레이아웃 편집과 단계 편집 격리
   - 해석 대시보드 편집 취소
   - PPT 레이아웃 편집기의 독립 동작
   - 전용 포트와 전용 DuckDB를 사용해 개발 데이터와 격리
2. 편집 상태 분리
   - `portfolio-layout`, `analysis-dashboard`, `workflow-stages`, `workflow-layout`을 구분한다.
   - PPT 레이아웃 편집 상태는 워크스페이스 편집 상태와 별도로 관리한다.
   - 한 워크스페이스에서 두 편집 모드가 동시에 활성화되지 않는다.
3. 점진적 모듈 분리
   - 프런트 레이아웃 기본값·보고서 유틸리티·편집 상태를 기능 모듈로 둔다.
   - FastAPI 요청 모델을 `schemas/`로 분리한다.
   - DB 연결 계층을 초기화·샘플 데이터와 분리한다.
4. 공용 레이아웃 저장
   - 브라우저 `localStorage`를 공용 저장소로 사용하지 않는다.
   - 운영·워크플로 레이아웃 저장마다 새 버전을 만든다.
5. OpenAPI 클라이언트
   - FastAPI OpenAPI JSON을 단일 원본으로 TypeScript 타입과 API 클라이언트를 생성한다.
   - `pnpm run generate:api`가 `frontend/openapi.json`과 `src/shared/api/generated/openapi.ts`를 갱신한다.
   - `src/shared/api/client.ts`의 타입 안전 클라이언트를 신규·변경 API부터 적용한다.
   - CI는 재생성 결과에 Git 차이가 생기면 API 계약 누락으로 실패한다.
6. PostgreSQL
   - 마이그레이션, 연결 풀, DuckDB 복사·검증 CLI, Windows/Linux 실행 스크립트를 제공한다.
7. 외부 배포
   - 인증, 역할 기반 권한, 감사 로그, 백업·복구를 배포 선행 조건으로 둔다.

## 3. 편집 상태 모델

| 상태 | 대상 | 저장 단위 |
|---|---|---|
| `portfolio-layout` | 운영 대시보드 | `workspace_layouts.layout_kind=portfolio` |
| `analysis-dashboard` | 해석 위젯 | 기존 `dashboards` |
| `workflow-layout` | 의뢰 카드 위치·크기·색·글자 | `workspace_layouts.layout_kind=workflow` |
| `workflow-stages` | 단계 내용·순서·추가·삭제 | `request_steps` |
| PPT 레이아웃 편집 | 보고서 슬라이드 | 기존 `report_layouts` |

## 4. 워크스페이스 레이아웃 데이터 모델

### `workspace_layouts`

- `layout_kind`: `portfolio` 또는 `workflow`, 기본 키
- `version`: 현재 버전
- `definition_json`: 현재 레이아웃 정의
- `updated_by`, `updated_at`: 최근 변경자와 시각

### `workspace_layout_versions`

- 기본 키: `(layout_kind, version)`
- `definition_json`: 해당 버전의 불변 스냅샷
- `created_by`, `created_at`, `is_valid`: 변경자·생성 시각·유효 여부

저장은 현재 행 갱신과 버전 스냅샷 추가를 하나의 트랜잭션으로 처리한다. PostgreSQL 전환 시 `definition_json`은 `JSONB`로 매핑한다.

## 5. API 계약

- `GET /api/workspace-layouts/{layout_kind}`: 현재 레이아웃
- `PUT /api/workspace-layouts/{layout_kind}`: 검증 후 새 버전 저장
- `GET /api/workspace-layouts/{layout_kind}/versions`: 최신순 버전 목록

프런트엔드는 DB 종류를 알지 못하며 이 API만 사용한다.

## 6. PostgreSQL 타 PC 이관 원칙

- 저장소에 비밀번호·호스트별 절대 경로를 넣지 않는다.
- `.env` 또는 운영 비밀 저장소에서 `DATABASE_URL`을 주입한다.
- Alembic으로 빈 DB를 동일 스키마까지 올린다.
- DuckDB 원본은 읽기 전용으로 열어 PostgreSQL에 복사한다.
- 복사 전 dry-run, 복사 후 테이블별 행 수·핵심 FK·Run별 결과 수·체크섬을 비교한다.
- 이관 스크립트는 재실행으로 중복 데이터를 숨기지 않고 실패 DB를 명확하게 복구하도록 한다.
- Windows PowerShell과 Linux shell에서 같은 절차를 수행할 수 있어야 한다.

상세 PostgreSQL 구현·검증 계약은 `backend-sql-integration-guide.md`를 따른다.

## 7. 현재 자동 검증

- DuckDB 백엔드: `pytest` 26개, 테스트별 임시 DB 격리
- PostgreSQL 18 최소 권한 앱 계정: 동일 `pytest` 26개
- 프런트 타입·번들: `pnpm run build`
- 실제 브라우저 로그인·Viewer 제한·핵심 편집 흐름: `pnpm run test:e2e` 5개
- CI: 위 세 검증을 각각 독립 job으로 실행

## 8. 외부 배포 안전장치

- `AUTH_MODE=disabled|password`로 로컬 호환성과 외부 인증 모드를 분리한다.
- password 모드는 scrypt 비밀번호 해시, HMAC 만료 토큰, HttpOnly SameSite 쿠키를 사용한다.
- 역할은 `viewer`, `editor`, `admin`이며 조회·일반 편집·관리 변경을 구분한다.
- 인증 실패, 권한 거절, 로그인, 모든 변경 API를 `audit_events`에 기록한다.
- PostgreSQL 앱 역할은 감사 테이블을 조회·추가할 수 있지만 수정·삭제할 수 없다.
- `pg_dump` custom-format 백업은 archive 검사와 SHA-256 manifest 검증 후에만 완료로 처리한다.
- 복구는 기본적으로 빈 DB만 허용하고 대상 DB명을 명시적으로 확인한다.

운영 절차는 `deployment-security-backup-guide.md`를 따른다.
