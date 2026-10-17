# 서버 시각·시간대 정책

대시보드의 모든 시각은 **서버 컴퓨터의 시계**를 기준으로 표시한다(사용자 결정 2026-10-17). PC 시계나 PC 시간대가 틀려도 "n분 전"과 절대 시각이 서버와 같아야 한다.

## 1. 저장과 API

| 단계 | 규칙 |
| --- | --- |
| DB 저장 | `TIMESTAMP`(시간대 없음) 열은 **UTC**. 파이썬은 `datetime.now(timezone.utc)`(필요하면 `.replace(tzinfo=None)`)로 쓴다. `datetime.now()`·`fromtimestamp(x)`(지역 시각)는 쓰지 않는다. |
| PostgreSQL 세션 | 앱 연결은 항상 `timezone=UTC`(`database_connection.POSTGRES_CONNECT_ARGS`, import gate 세션 포함). Windows 설치판처럼 서버 `TimeZone`이 `Asia/Seoul`이어도 naive UTC 값을 `TIMESTAMPTZ`에 넣을 때 9시간 밀리지 않고, `CURRENT_TIMESTAMP`/`now()` 기본값이 `TIMESTAMP`에 KST 벽시계를 쓰지 않는다. |
| API 문자열 | ISO 8601 + 명시적 오프셋(`Z` 또는 `+00:00`). naive DB 값은 UTC로 보고 `Z`를 붙인다(`server_clock.iso_utc`, `notifications._iso`, `folder_discovery_environment.iso_utc`). |
| 화면 해석 | `parseServerTime`: 오프셋 없는 옛 문자열도 UTC로 읽는다(JS 기본은 지역 시각). |

## 2. 서버 시계 기준(응답 헤더)

`ServerClockMiddleware`(가장 바깥 미들웨어)가 모든 HTTP 응답(401·403·503 포함)에 붙인다.

| 헤더 | 예 | 뜻 |
| --- | --- | --- |
| `X-Server-Time` | `2026-10-17T05:02:03.456Z` | 응답 시작 시점의 서버 UTC 시각(ms) |
| `X-Server-UTC-Offset-Minutes` | `540` | 서버 현재 UTC 오프셋(분) |
| `X-Server-Timezone` | `Asia/Seoul` | 서버 IANA 시간대(알 수 있을 때만) |

서버 시간대 결정 순서: 환경 변수 `SIMDASH_TIMEZONE`(유효한 IANA 이름) → `TZ` → `/etc/localtime` → Windows 레지스트리 `TimeZoneKeyName`(예 `Korea Standard Time` → `Asia/Seoul`). 모르면 오프셋만 보낸다.

## 3. 화면(`frontend/src/shared/api/serverTime.ts`)

- `apiFetch`가 모든 응답의 헤더를 `noteServerClock`에 넘긴다. 표본 오프셋 = `X-Server-Time − 헤더 도착 시각`(요청 처리 시간과 무관, 단방향 지연만큼만 작다). 최근 10분 표본 중 가장 큰 값을 쓰고, 오래된 표본은 버려 서버 시계 보정을 따라간다. 알림 벨이 60초마다 호출하므로 따로 동기화 호출은 없다.
- `serverNow()` = `Date.now()` + 오프셋. 첫 응답 전에는 PC 시계.
- `relativeServerTime`(알림 `notificationTime`이 사용): "방금 / n분 전 / n시간 전 / n일 전", 7일 이상은 서버 시간대 날짜.
- `formatServerTime`·`formatServerDate`: 서버 시간대(`X-Server-Timezone`, 없으면 정시 오프셋 → `Etc/GMT-9`)로 표시, 첫 응답 전에는 브라우저 시간대.
- Case 결과 폴더 동기화 상태(`최신 · n분 전`)는 PC에서 잰 경과 시간(확인 시각과 현재 모두 PC 시계)이라 시계 차이와 무관하다.

## 4. 운영 확인

- 서버 PC 시계가 틀리면 화면도 그 시계를 따른다(의도). 서버 시계는 Windows 시간 동기화로 맞춘다.
- 이전 버전이 KST 세션으로 쓴 행은 그대로다(migration 없음): `TIMESTAMPTZ` 표(관리형 로컬 실행 세션·페어링·권한, 모델링 템플릿 등)의 naive 쓰기 값은 9시간 이른 시각으로 남아 남은 단기 세션·토큰이 최대 9시간 일찍 만료될 수 있고, `asset_blobs.created_at` 기본값 행은 KST 벽시계다. 알림·등록 이력 등 naive UTC로 쓰던 `TIMESTAMP` 표는 영향이 없다.
- PostgreSQL 서버 설정은 바꿀 필요가 없다(앱 세션이 UTC). `DATABASE_URL`에 `options=`를 넣어 쓰는 경우 앱의 `-c timezone=UTC`가 그 값을 대신한다.
- 검증: `backend/tests/test_server_time_convention.py`(TZ=Asia/Seoul, `SIMDASH_TEST_PG_TZ_URL`을 주면 서버 TimeZone이 Asia/Seoul인 실제 PostgreSQL로도 확인), `frontend` `npm run test:api`(PC 시계 18시간 앞섬, naive/Z/+09:00 문자열, 서버 시간대 표시).
