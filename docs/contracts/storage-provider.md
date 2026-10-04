# 계약: SPDM 저장소 공급자 (StorageProvider) — 1단계 코드 정리

- 상태: 확정 (2026-10-04)
- 목적: SPDM 폴더 접근을 한 인터페이스로 모아, 이후 알테어원 드라이브(사이드카 HTTP 서비스) 공급자를 설정만으로 교체할 수 있게 한다.
- 1단계 범위: **동작 변화 없음.** 현재 로컬 폴더 접근을 `LocalFsProvider` 하나로 옮긴다. 원격 공급자, 설정 스위치, 사이드카 연결은 2단계 이후.

## 1. 결정

| # | 결정 |
|---|---|
| S1 | SPDM 루트 아래의 모든 목록·조회·읽기·쓰기는 `StorageProvider`를 거친다. 서비스 모듈에서 SPDM 경로에 대한 `os.scandir`/`os.walk`/`os.listdir`/`Path.iterdir`/`glob`/`open`/`stat`/쓰기 직접 호출을 없앤다 |
| S2 | 1단계 구현체는 `LocalFsProvider` 하나. 결과(목록 순서, 숨김·링크 처리, 오류, 지문, 한도)는 현재와 동일해야 한다 |
| S3 | 쓰기(`create`, `mkdirs`, 기존 Final 흐름이 쓰는 임시파일·이동 포함)는 공급자에서 루트 기준 `*/Final/CAE/**`, `*/Final/Reports/**`, `*/Final/.finalizations/**`만 허용하고 그 밖은 예외. 현재 앱이 실제로 쓰는 경로를 전수 조사해 허용 목록과 일치시킨다(새 쓰기 경로를 만들지 않는다) |
| S4 | SPDM 루트 해석은 한 곳(`spdm_storage.storage_root`)에서만. 모듈별 `_root()` 사본을 제거하고 공급자를 얻는 함수 하나(`get_storage_provider(conn)`)를 둔다 |
| S5 | SPDM 루트 **밖**의 파일 접근(앱 데이터 폴더, 업로드 임시, 백업, 배포 스크립트, local_runner)은 범위 밖이다 |

## 2. 인터페이스 (`backend/app/services/storage/provider.py`)

```python
class StorageProvider(Protocol):
    def root_identity(self) -> str
    def list(self, rel_path: str) -> list[Entry]          # 1단계는 페이지 없음(전체), 정렬은 현재 코드와 동일
    def stat(self, rel_path: str) -> Entry | None
    def open_read(self, rel_path: str) -> BinaryIO
    def create(self, rel_path: str, data: bytes | BinaryIO, *, if_absent: bool = True) -> Entry
    def mkdirs(self, rel_path: str) -> None
    def replace(self, src_rel: str, dst_rel: str) -> None   # 현재 Final 흐름의 임시→최종 이동이 있으면 그대로 옮김
    def changes(self, rel_path: str, since: str | None) -> None   # 1단계는 항상 None(미지원)

@dataclass(frozen=True)
class Entry:
    name: str
    kind: Literal["file", "dir", "other"]
    size: int | None
    modified_ns: int | None
    item_id: str               # 로컬: st_dev:st_ino
    etag: str | None           # 로컬: None
    is_link: bool              # reparse point/symlink
```

- 경로: 루트 기준 상대경로, `/` 구분. `..`, 절대경로, 루트 밖으로 나가는 경로는 거부(현재 검사 재사용).
- `root_identity()`: 현재 `root_identity`/`root_key` 계산과 동일한 값을 반환해 DB 호환을 유지.
- 오류: `StorageError(code)` — `NOT_FOUND`, `FORBIDDEN`, `NOT_ALLOWED_WRITE`, `UNAVAILABLE`, `LIMIT`. 기존 호출부가 잡던 예외 의미는 유지(필요하면 어댑터에서 기존 예외로 변환).
- 한도(`MAX_ENTRIES`, 시간 예산, `_Lister` 등)는 호출부 로직이므로 그대로 두고, 공급자는 단순 목록만 제공.

## 3. 완료 기준 (Verifier)

1. `backend/app` 안에서 SPDM 루트 경로를 대상으로 하는 직접 파일시스템 호출이 공급자 모듈 밖에 없다(정적 검사 테스트 추가: 허용 모듈 목록 외 `scandir|walk|listdir|iterdir|glob|open(|.stat(|write_|mkdir|rename|replace|unlink` 사용 시 실패. SPDM 무관 사용처는 명시적 예외 목록)
2. 기존 전체 테스트 결과가 이전과 동일(DuckDB 전체, 알려진 9건 외 실패 없음, Postgres 주요 묶음 통과)
3. 쓰기 허용 범위 밖 경로에 대한 `create/mkdirs/replace`는 `NOT_ALLOWED_WRITE`(단위 테스트)
4. 실제형 트리에서 자동 탐색→등록→수집→진척→Final 지정→보고서 업로드 흐름의 산출(DB 행, 파일)이 리팩터 전후 동일(비교 스크립트)
5. 성능: 같은 트리에서 자동 탐색·동기화 1회 소요 시간이 전후 ±20% 이내
6. 배포 계약 영향 없음(새 의존성·migration 없음)
