# PostgreSQL 바이너리 미디어 저장 전환 구현 계획

## 1. 문서 목적

현재 애플리케이션은 이미지(JPEG, PNG 등)와 영상(MP4 등)을 파일 시스템에 저장하고 `media_assets.file_path`만 SQL에 기록한다. 이 문서는 비정형 파일의 원본 바이트를 PostgreSQL에 저장하고, 웹에서 안전한 스트리밍·다운로드로 제공하기 위한 구현 사양과 단계별 실행 계획이다.

이 계획은 다음 요구를 모두 포함한다.

- 파일 경로 의존성을 제거하고 PostgreSQL을 미디어의 정본(source of truth)으로 사용한다.
- 원본 파일의 바이트, MIME, 확장자, 파일명을 보존한다. 트랜스코딩이나 재인코딩을 하지 않는다.
- 다수 동시 접속을 위해 전체 파일을 메모리에 올리지 않고 청크 단위로 스트리밍한다.
- 사용자가 요청하면 DB에 저장된 데이터를 원래 형식 그대로 다운로드한다.
- 기존 파일 기반 자료와 `video_example`의 데모 MP4 20개를 무중단 방식으로 이전한다.
- 프로젝트 권한, IDOR 방지, 백업·복원, GC, 운영 검증을 포함한다.
- Sol이 지휘하고, 실제 Luna 모델이 구현하며, 별도의 Sol이 검증·피드백하고 Luna가 반영하는 협업 구조를 따른다.

## 2. 현재 상태와 범위

### 2.1 현재 구현 기준선

- 백엔드: FastAPI, SQLAlchemy/psycopg, PostgreSQL 18.x 및 DuckDB 호환 계층
- 프런트엔드: React/Vite
- `media_assets`는 기존 `file_path`를 통해 파일 시스템의 `FileResponse`를 반환한다.
- 드롭 비디오 20개는 현재 `video_example`에서 파일 응답으로 제공된다.
- `/assets` 정적 마운트와 DB 경로 기반 URL이 존재한다.
- 기존 작업 트리에 사용자 변경사항이 있으므로 구현자는 관련 없는 diff를 되돌리거나 덮어쓰지 않는다.

### 2.2 포함 범위

- 등록 이미지·영상 및 데모 MP4 20개
- PostgreSQL 스키마, Alembic 마이그레이션, 저장소/서비스/HTTP 계층
- 기존 데이터·파일 마이그레이션과 롤백/재실행
- Range 요청, 캐시 검증, 다운로드 파일명 처리
- 프로젝트 권한 및 감사 로그
- 프런트엔드 미디어 URL·다운로드 버튼·리포트 내보내기
- 백업/복원 검증과 운영 게이트
- DuckDB 개발·테스트 경로

### 2.3 기본 운영 목표

| 항목 | 목표 |
|---|---|
| 동시 영상 스트림 | 50개 |
| 최대 영상 크기 | 500 MiB |
| 읽기 배치 | 기본 1 MiB |
| 파일 원본 보존 | 이전 검증 후 최소 7일 |
| 스트림 메모리 | 파일 전체 크기와 무관하게 일정 상한 |
| 실행기 | v1 Uvicorn worker 1개 |
| 오류 목표 | 정상 부하에서 5xx 및 pool timeout 0건 |

## 3. 목표 아키텍처

### 3.1 정본과 호환성

새로운 데이터의 정본은 PostgreSQL의 `asset_blobs`와 `asset_blob_chunks`다. `media_assets.file_path`는 전환 기간의 legacy 참조로만 남기며, database-only 운영 게이트를 통과한 뒤에는 새 행에 사용하지 않는다.

원본 파일은 다음 메타데이터와 함께 저장한다.

- 원본 파일명 및 확장자
- 검증된 MIME 타입
- 전체 바이트 수
- 전체 SHA-256(소문자 64자리 hex)
- 청크 크기와 개수
- 생성 시각 및 수명주기 상태

### 3.2 PostgreSQL 테이블

```sql
CREATE TABLE asset_blobs (
    id UUID PRIMARY KEY,
    sha256 CHAR(64) NOT NULL,
    file_size BIGINT NOT NULL CHECK (file_size >= 0),
    chunk_size INTEGER NOT NULL DEFAULT 1048576
        CHECK (chunk_size > 0 AND chunk_size <= 1048576),
    chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    orphaned_at TIMESTAMPTZ,
    CONSTRAINT asset_blobs_sha256_format
        CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT asset_blobs_identity UNIQUE (sha256, file_size)
);

CREATE TABLE asset_blob_chunks (
    blob_id UUID NOT NULL REFERENCES asset_blobs(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content BYTEA NOT NULL,
    content_length INTEGER NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    PRIMARY KEY (blob_id, chunk_index),
    CONSTRAINT asset_blob_chunks_size
        CHECK (content_length = octet_length(content)),
    CONSTRAINT asset_blob_chunks_max_size
        CHECK (content_length > 0 AND content_length <= 1048576),
    CONSTRAINT asset_blob_chunks_sha256_format
        CHECK (content_sha256 ~ '^[0-9a-f]{64}$')
);

ALTER TABLE media_assets
    ADD COLUMN blob_id UUID REFERENCES asset_blobs(id),
    ADD COLUMN original_filename TEXT,
    ADD COLUMN mime_type TEXT;

CREATE INDEX ix_media_assets_blob_id ON media_assets(blob_id);
```

`asset_blobs`의 unique identity는 동일한 바이트의 중복 저장을 막는다. blob을 새 참조에 연결하거나 GC하는 트랜잭션은 `asset_blobs ... FOR UPDATE`로 잠근 뒤 같은 트랜잭션에서 참조 유무를 다시 확인한다.

### 3.3 드롭 비디오

드롭 비디오도 파일 경로가 아닌 blob을 참조한다.

```sql
ALTER TABLE drop_video_assets
    ADD COLUMN blob_id UUID REFERENCES asset_blobs(id) ON DELETE RESTRICT,
    ADD COLUMN original_filename TEXT,
    ADD COLUMN mime_type TEXT;

CREATE INDEX ix_drop_video_assets_blob_id ON drop_video_assets(blob_id);
CREATE UNIQUE INDEX ux_drop_video_assets_case_order
    ON drop_video_assets(load_case_id, sort_order);
```

기존 `video_id`의 의미와 외래키는 보존한다. orphan blob 삭제가 비디오 참조를 조용히 제거하지 않도록 드롭 비디오 FK는 `RESTRICT`를 사용한다.

### 3.4 모듈 배치

구현 시 다음 책임을 분리한다.

```text
backend/app/repositories/media_repository.py
backend/app/services/media_storage_service.py
backend/app/services/media_http.py
backend/app/routers/media.py
scripts/migrate_media_to_database.py
scripts/gc_media_blobs.py
scripts/cleanup_migrated_media_files.py
tests/test_media_storage.py
tests/test_media_http.py
tests/test_media_migration.py
```

저장소(repository)는 DB 조회·청크 iterator만 담당하고, storage service는 MIME/해시/청크 생성과 attach를 담당한다. HTTP 계층은 Range·ETag·다운로드 헤더와 감사 이벤트를 담당한다.

## 4. 파일 저장 및 마이그레이션 규칙

### 4.1 원본 바이트 검증

허용 MIME은 확장자만 믿지 않고 magic bytes와 파서 가능한 최소 구조를 함께 검사한다.

- JPEG: `FF D8 FF`
- PNG: PNG signature
- WebP: RIFF/WEBP
- SVG: XML 텍스트이되 active content를 차단
- MP4: ISO Base Media File 구조 및 `ftyp` box 확인
- WebM: EBML signature

SVG는 script, event handler, 외부 리소스, active URL을 거부하고 응답 시 CSP·`nosniff`·sandbox 정책을 적용한다. 저장 시 원본 바이트를 바꾸지 않으며 검증은 저장 전 별도 단계에서 수행한다.

### 4.2 청크 저장

1. 입력 스트림을 1 MiB 이하로 읽는다.
2. 청크별 SHA-256과 전체 SHA-256을 동시에 계산한다.
3. blob identity `(sha256, file_size)`를 조회한다.
4. 기존 blob이면 새 참조만 attach하고, 없으면 metadata 행과 청크 행을 삽입한다.
5. 삽입 후 청크 개수·길이·순서·해시를 검증한다.

대용량 파일을 위해 전체 파일을 `bytes` 하나로 만들지 않는다. DuckDB에서 PostgreSQL로 옮길 때도 일반적인 1,000행 base64 배치 경로를 사용하지 말고 `fetchmany(1)`(최대 4로 제한)와 psycopg `COPY`/raw bytes 경로를 사용한다.

### 4.3 레거시 파일 이전

`migrate_media_to_database.py`는 기본적으로 dry-run으로 실행한다.

- 대상 파일, 크기, MIME, 전체 SHA-256을 사전 계산한다.
- 모든 대상의 preflight가 통과한 뒤에만 DB 쓰기를 시작한다.
- 기존 `media_assets` 및 드롭 비디오 참조를 blob에 연결한다.
- 실패 시 현재 파일과 DB 참조를 보존하고 재실행 가능하도록 한다.
- 데모 MP4는 명시적 allowlist만 사용하고 정확히 20/20개인지 확인한다.

이전 원본은 검증된 백업이 존재하고 database-only 게이트가 통과한 뒤 7일 동안 보존한다. `cleanup_migrated_media_files.py`는 자동 삭제하지 않고 명시적 실행 및 사전 확인을 요구한다.

### 4.4 안전한 public/static 분리

`/assets` 정적 마운트는 `backend/public_assets`로 교체하고 신뢰된 checked-in fixture만 둔다. `sample-contour.svg`, imports 및 모든 DB 참조 legacy 미디어는 `backend/media_legacy` 등 private 경로로 이동한다.

preflight와 startup gate에서 다음을 거부한다.

- public root와 legacy root의 경로 교집합
- symlink 또는 동일 realpath
- DB가 가리키는 legacy 파일이 public mount 아래에 존재하는 경우

## 5. HTTP API 계약

### 5.1 엔드포인트

| 메서드 | 경로 | 동작 |
|---|---|---|
| GET, HEAD | `/api/assets/{asset_id}` | 인라인 미디어 응답 |
| GET, HEAD | `/api/assets/{asset_id}/download` | 원본 파일 다운로드 |
| GET | `/api/load-cases/{load_case_id}/drop-videos` | 권한이 있는 드롭 비디오 목록 |
| GET, HEAD | `/api/drop-videos/{video_id}/content` | 드롭 비디오 인라인 응답 |
| GET, HEAD | `/api/drop-videos/{video_id}/download` | 드롭 비디오 원본 다운로드 |

`HEAD`는 body 없이 GET과 같은 상태 코드·핵심 헤더를 반환한다. legacy fallback은 전환 기간에만 API 내부에서 허용하며 public 정적 URL을 새 API 응답에 노출하지 않는다.

### 5.2 Range 및 캐시

- Range가 없으면 `200`과 전체 길이를 반환한다.
- 단일 유효 Range만 `206`으로 지원한다.
- malformed, 다중, unsatisfiable Range는 `416`과 `Content-Range: bytes */{size}`를 반환한다.
- `ETag`는 전체 SHA-256을 사용한다.
- 일치하는 `If-None-Match`는 `304`다.
- `If-Range`가 불일치하면 Range를 무시하고 전체 응답을 반환한다.
- `Content-Length`, `Content-Range`, `Accept-Ranges: bytes`, `Content-Type`, `Content-Disposition`을 정확히 설정한다.
- `Cache-Control: private, max-age=0, must-revalidate, no-transform` 및 `X-Content-Type-Options: nosniff`를 적용한다.

응답 generator는 한 번에 1 MiB만 읽고, 배치 사이에 DB connection을 반납하거나 별도 짧은 read transaction을 사용한다. 파일 전체를 메모리에 조립하지 않는다.

### 5.3 다운로드 헤더

다운로드 응답은 `Content-Disposition: attachment`를 사용한다. 원본 파일명에 대해 ASCII fallback과 RFC 5987 UTF-8 `filename*`를 모두 제공한다.

다운로드한 파일은 다음을 보장해야 한다.

- 원본 확장자 보존
- 원본 MIME 보존
- 원본 바이트와 SHA-256 일치
- 브라우저가 직접 저장할 수 있고 프런트엔드가 JS blob으로 재조립하지 않음

감사 이벤트는 `STARTED`, `COMPLETED`, `ABORTED`로 기록한다. 전송량 필드는 실제 클라이언트 수신량으로 오인하지 않도록 `bytes_yielded_to_asgi`라고 명명한다.

## 6. 권한과 보안

모든 list, head, range, content, download 요청에 `PROJECT_DATA_VIEW`를 적용한다.

리소스 해석 경로는 다음과 같다.

```text
media_asset
  -> analysis_runs
  -> load_cases
  -> analysis_requests.project_id

drop_video
  -> drop_video_assets
  -> load_cases
  -> analysis_requests.project_id
```

기존 인증 미들웨어의 `/api`·`/assets` 경로 보호만으로 충분하다고 가정하지 않는다. DB resource resolver가 최종 프로젝트 권한을 확인하고, 다른 프로젝트의 ID를 추측한 요청은 존재 여부가 드러나지 않도록 일관된 404/403 정책을 적용한다.

추가 보안 요구:

- 파일 경로를 SQL에 그대로 넣어 임의 경로를 읽지 않는다.
- `../`, 절대 경로, symlink를 API 입력으로 허용하지 않는다.
- 로그·감사 데이터에 전체 바이너리를 남기지 않는다.
- MIME은 DB 메타데이터가 아니라 저장 시 검증된 값을 사용한다.
- 다운로드 파일명은 header injection을 막도록 sanitize한다.

## 7. 연결 풀과 성능

미디어 스트리밍용 풀을 일반 SQL 풀과 분리한다.

| 풀 | 기본 크기 | overflow | timeout |
|---|---:|---:|---:|
| 일반 SQL | 5 | 10 | 기존 설정 유지 |
| 미디어 읽기 | 10 | 5 | 10초 |

v1은 worker 1개로 실행한다. worker를 늘릴 경우 전체 상한을 `worker 수 × (일반 풀 상한 + 미디어 풀 상한)`으로 계산하고 PostgreSQL `max_connections` 및 관리/마이그레이션 연결을 포함해 사전 검증한다.

성능 검증 기준:

- 500 MiB MP4에 대해 50개 동시 스트림
- 10분 부하 동안 p95 응답 지연 2초 이하(환경 기준을 기록)
- RSS 256 MiB 이하(파일 전체 크기와 무관)
- 5xx 0건, pool timeout 0건
- 브라우저 seek가 필요한 Range 요청을 반복해도 청크 순서와 checksum 일치

## 8. 프런트엔드 변경

미디어 타입에 다음 필드를 추가한다.

```ts
type MediaAsset = {
  id: string;
  original_filename?: string;
  mime_type?: string;
  file_size?: number;
  checksum?: string;
  asset_url?: string;
  download_url?: string;
  /** @deprecated migration compatibility only */
  file_path?: string;
};
```

변경 사항:

- `/assets/${file_path}` 조합 fallback을 제거한다.
- 이미지·영상·드롭 비디오 카드에 same-origin `download_url` 링크를 제공한다.
- 다운로드는 `fetch` 후 blob 조립하지 않고 `<a download>` 또는 직접 링크로 시작한다.
- 리포트 export는 `original_filename`을 표시하고 legacy `file_path`에 의존하지 않는다.
- API/OpenAPI 타입과 생성 산출물을 구현 마지막에 갱신한다.

## 9. 백업·복원 및 운영 게이트

백업 transfer v2는 다음을 포함한다.

- `pg_dump`에 `asset_blobs`, `asset_blob_chunks`, 참조 테이블 포함
- manifest에 blob 수, 청크 수, 총 바이트, 참조 수, 전체 checksum 기록
- `assets.zip`에는 DB에 없는 정적/legacy 파일만 포함
- 복원 후 모든 blob/chunk checksum·길이·참조 무결성 재검증

`database_only` startup gate는 다음을 모두 확인한다.

1. unbound `media_assets`가 0개
2. 모든 참조 blob이 존재하고 크기·checksum·chunk_count가 일치
3. 데모 MP4가 정확히 20/20개 연결
4. 애플리케이션 role에 필요한 CRUD 권한 존재
5. connection budget 검증 통과
6. Alembic head가 기대 revision(계획상 `0008_media_blob_storage`)과 일치
7. public/static과 legacy 경로 교집합·symlink 없음
8. 복원 rehearsal 및 checksum 검증 완료

## 10. Alembic 및 스키마 호환성

새 마이그레이션 revision은 `0008_media_blob_storage`로 추가하고 현재 head인 `0007_access_control_menu_policy`를 `down_revision`으로 사용한다.

- 이미 존재하는 테이블·컬럼·인덱스에 대해 존재성 검사를 고려한다.
- 현재 `0001`이 canonical `schema.sql`을 읽는 구조이므로 schema.sql, DuckDB DDL, export/import 스크립트를 함께 갱신한다.
- positional `media_assets` INSERT를 모두 명시적 컬럼 INSERT로 바꾼다.
- up/down 마이그레이션을 빈 DB와 기존 데이터 DB 각각에서 검증한다.

## 11. 단계별 실행 순서

### Phase 0 — 지휘 기준선

- dirty worktree 상태와 현재 migration head 기록
- PostgreSQL/DuckDB 스키마·권한·connection budget 확인
- public/static 및 legacy 경로 inventory 작성
- 이 문서를 구현 spec으로 고정

### Phase 1 — 스키마·저장 계층

- `0008_media_blob_storage` 작성
- `asset_blobs`, `asset_blob_chunks`, 참조 컬럼·인덱스 추가
- repository와 storage service 구현
- MIME·checksum·chunk invariant 단위 테스트 작성

### Phase 2 — HTTP·권한·감사

- media router와 streaming generator 구현
- GET/HEAD, Range, ETag, If-Range, 304/416 처리
- download filename 및 attachment 응답
- 프로젝트 resource resolver와 IDOR 테스트
- media pool 및 connection lifecycle 적용

### Phase 3 — 마이그레이션·GC·백업

- legacy 및 demo dry-run/execute 도구 구현
- preflight, rollback, 재실행, 20/20 gate 구현
- two-sweep GC 및 lock/recheck 구현
- transfer v2 manifest·복원 checksum 검증 구현

### Phase 4 — 프런트엔드

- API 타입·OpenAPI 갱신
- inline 미디어 URL과 다운로드 링크 연결
- file_path fallback 제거
- 브라우저 이미지 표시, 영상 seek, 원본 다운로드 테스트

### Phase 5 — 전환

- dual-read 기간에 DB blob 우선, 검증된 legacy fallback만 허용
- 모든 기존 참조 migrate
- database-only startup gate 통과
- 최소 7일 원본 보존 후 운영자가 별도 cleanup 실행

## 12. 검증 계획

### 12.1 자동 테스트

- PostgreSQL과 DuckDB 스키마/삽입/조회
- 동일 파일 동시 업로드 dedup
- 청크 누락·순서 변경·길이 mismatch·checksum mismatch 거부
- JPEG/PNG/MP4 원본 byte-for-byte round trip
- 단일 Range의 200/206/304/416 케이스
- malformed/multi-range/unsatisfiable Range
- UTF-8 파일명 다운로드 헤더
- 다른 프로젝트 asset/drop-video IDOR
- GC orphan 2단계 및 attach race
- migration dry-run, execute, rollback, 재실행
- pg_dump 복원 후 manifest 일치

### 12.2 브라우저 및 부하 검증

- 이미지 inline 표시
- MP4 seek가 Range로 동작
- 다운로드 파일명·확장자·MIME 확인
- 500 MiB/50 clients/10분 시나리오
- RSS, p95, 5xx, pool timeout 측정값을 CI 또는 운영 보고서에 남김

### 12.3 승인 기준

다음이 모두 참이어야 database-only 전환을 승인한다.

- 전체 자동 테스트 통과
- checksum 및 원본 byte round trip 통과
- 권한/IDOR 테스트 통과
- 복원 rehearsal 통과
- 20개 데모 영상 gate 통과
- 부하 기준 통과
- 미디어 파일 전체 메모리 적재가 없음을 코드 리뷰와 프로파일링으로 확인
- 별도 Sol 검증자가 P0/P1 결함 없음 판정

## 13. Sol/Luna 협업 프로토콜

### 지휘 Sol

- 이 문서를 기준 사양으로 유지한다.
- dirty worktree와 변경 범위를 통제한다.
- Luna에 단계별 구현 작업과 완료 조건을 전달한다.
- 사용자 승인 없이 범위를 확장하지 않는다.

### 구현 Luna

- 실제 `gpt-5.6-luna` 모델로 구현한다.
- 각 Phase 시작 전에 영향을 받는 파일과 기존 변경사항을 확인한다.
- 큰 파일 전체 메모리 적재, 경로 기반 보안 우회, 무검증 destructive migration을 금지한다.
- 구현 후 재현 가능한 테스트 명령과 잔여 위험을 보고한다.

### 검증 Sol

- Luna 변경분을 독립적으로 읽고 보안·정합성·성능·복원 관점에서 검증한다.
- P0/P1/P2로 결함을 분류하고 재현 절차를 남긴다.
- 승인 조건을 충족하지 못하면 Luna가 피드백을 반영한 뒤 재검증한다.

현재 협업 런타임에서 `gpt-5.6-luna`가 하위 에이전트 허용목록에 없을 경우, 이 문서 작성과 사양 검토까지만 진행하고 구현은 Luna가 활성화된 세션에서 재개한다. Sol/Terra를 Luna로 표시해 대체하지 않는다.

## 14. 완료 산출물

- 이 구현 계획 문서
- Alembic `0008_media_blob_storage`
- media repository/service/router 및 테스트
- migration/GC/cleanup 스크립트
- backup/restore transfer v2 검증 산출물
- 프런트엔드 타입·URL·다운로드 변경
- 부하·복원·권한 검증 결과와 Luna/Sol 피드백 기록
- database-only 전환 승인 기록
