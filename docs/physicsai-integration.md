# PhysicsAI 연동 안내

PhysicsAI 플랫폼(별도 저장소, 같은 Windows Server에서 동작)을 대시보드와 같은 주소 아래 `/physicsai/`로 붙이는 방법과 대시보드 쪽 계약을 정리한다. 근거는 PhysicsAI 계약 `platform.md` §20(D1~D7)과 `phase2.md` §18(D8·D9)이다. **기본값은 꺼짐**이며, PhysicsAI를 설치하지 않은 서버는 아무 설정도 바꿀 필요가 없다.

## 1. 켜고 끄는 설정

| 위치 | 이름 | 기본값 | 의미 |
|---|---|---|---|
| 백엔드 `.env` | `SIMDASH_PHYSICSAI_ENABLED` | `false` | `true`이면 사이드바 "AI 예측"과 Case 결과의 "AI 학습 데이터로 보내기"를 표시한다 |
| 오프라인 설치 설정 JSON | `physicsaiEnabled` | `false` | `true`이면 설치기가 Caddyfile에 PhysicsAI 라우트를 넣고 `.env`의 `SIMDASH_PHYSICSAI_ENABLED=true`를 쓴다(끄면 `false`) |
| 오프라인 설치 설정 JSON | `physicsaiPort` | `8100` | PhysicsAI 백엔드 루프백 포트(API·웹·PostgreSQL 포트와 달라야 함) |
| 오프라인 설치 설정 JSON | `physicsaiFrontendRoot` | `""` | PhysicsAI `frontend\dist` 절대경로(켤 때 필수, PhysicsAI `deploy.json`의 `caddy_frontend_root`) |
| 수동 인트라넷 템플릿 | `physicsai.caddy` 파일 | 없음 | `deploy/windows/Caddyfile.intranet.template`의 `import physicsai*.caddy`가 같은 폴더에 이 파일이 있을 때만 라우트를 넣는다 |

오프라인 설치기는 설정을 `state\install-settings.json`에 저장하므로 다음 업데이트에서도 같은 값이 유지된다. 설치기를 쓰는 서버에서 Caddyfile을 손으로 고치면 업데이트 때 다시 생성되어 사라지므로, 반드시 설정 JSON으로 켠다.

## 2. Caddy 라우트(D1·D9)

켜면 사이트 블록에 다음이 들어간다. 대시보드의 `/api/*`·`/assets/*`·SPA 경로는 그대로이며, `/physicsai/api/*`는 대시보드 API 매처와 겹치지 않는다.

```caddy
redir /physicsai /physicsai/ 308
handle /physicsai/api/* {
    reverse_proxy 127.0.0.1:8100        # 접두를 벗기지 않는다
}
handle /physicsai/* {
    root * "<PhysicsAI>/frontend/dist"
    uri strip_prefix /physicsai
    try_files {path} /index.html
    file_server
}
```

수동 템플릿을 쓰는 서버는 PhysicsAI 저장소의 `deploy/caddy/physicsai.caddy`를 대시보드 Caddyfile과 같은 폴더에 복사하고 Caddy 서비스 환경에 `PHYSICSAI_PORT`(생략 시 8100)와 `PHYSICSAI_FRONTEND_ROOT`를 준다. 파일을 지우면 다시 꺼진다. 렌더링한 Caddyfile은 `caddy validate`로 확인한다.

## 3. 화면(D5·D8)

- **사이드바 "AI 예측"**: `/physicsai/`를 새 창으로 여는 외부 링크. 메뉴 정책 표(`MENU_DEFINITIONS`)에는 넣지 않았다. 표시 조건은 로그인 + 서버 플래그뿐이고, 실행 권한은 PhysicsAI가 대시보드 역할로 다시 확인한다. 메뉴 정책 DB·migration 변경은 없다.
- **Case 결과 "AI 학습 데이터로 보내기"**: Case 결과 머리줄 오른쪽 버튼. 선택한 가장 깊은 SPDM 폴더(유통: Run Option → Run Case → 하중경우 → Case, 사용: Case)를 SPDM 루트와 합쳐 절대경로로 만들고 `/physicsai/import?spdm_path=<URL 인코딩 절대경로>&project_id=<프로젝트 id>`를 새 창으로 연다. 드라이브 문자·UNC 루트는 `\`로 잇는다. 링크를 여는 것만으로 PhysicsAI 작업은 생기지 않고, 대시보드는 파일을 옮기거나 쓰지 않는다.
- 두 화면은 `GET /api/integrations/physicsai`(`{enabled, base_path, spdm_root}`)를 읽는다. 플래그가 꺼져 있으면 `spdm_root`는 항상 `null`이다. SPDM 루트가 없거나 확인할 수 없으면 버튼만 숨긴다.
- 경로는 PhysicsAI 서버가 읽을 수 있고 PhysicsAI `storage.spdm_roots` 아래여야 한다. 같은 PC에서 대시보드 SPDM 루트(`SIMDASH_SPDM_ROOT` 또는 저장된 설정)를 그대로 쓰므로, PhysicsAI `spdm_roots`에 같은 루트 표기(드라이브 문자 또는 UNC)를 넣는다.

## 4. 바꾸면 안 되는 대시보드 계약(D2~D4)

PhysicsAI는 대시보드 로그인을 그대로 쓴다. 아래를 바꾸면 PhysicsAI 설정(`auth.cookie_name` 등)도 같이 바꿔야 한다. `backend/tests/test_physicsai_link_contract.py`가 고정한다.

| # | 계약 |
|---|---|
| D2 | 세션 쿠키 `analysis_canvas_session`, `Path=/`, `SameSite=Strict`, `HttpOnly` |
| D3 | `GET /api/auth/me`가 쿠키와 `Authorization: Bearer` 모두 허용, 응답 `id, username, display_name, account_status, is_global_admin, memberships[{project_id, role}]` |
| D4 | `GET /api/projects`가 `id, name, product_name`을 주고 모든 ACTIVE 사용자(프로젝트 비멤버 포함)가 조회 가능 |

D6(로그인 후 `?next=` 복귀), D7(`/api/auth/introspect`)은 구현하지 않았다.

## 5. 검증

- 백엔드: `pytest tests/test_physicsai_link_contract.py tests/test_physicsai_caddy_routes.py`(Caddy 실행 확인은 `CADDY_BIN`을 줄 때만 수행).
- 프런트: `pnpm run test:api`(경로 결합·URL 인코딩·플래그 off), `tsc -b`.
- 실제 Windows Server 2022 설치·업데이트에서 PhysicsAI 라우트를 켠 검증은 아직 수행하지 않았다.
