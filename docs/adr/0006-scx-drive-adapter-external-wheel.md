# ADR 0006: SCX 드라이브 어댑터는 선택 런타임 의존성으로 따로 공급한다

- 상태: 승인(사용자 결정 D6·D7, 2026-10-07 15:40), D0 구현. 설치기 외부 wheel 경로 반영(2026-10-07)
- 날짜: 2026-10-07
- 근거: [SCX 드라이브 연동 계획 §9](../plans/scx-drive-integration.md), 이슈 #45 어댑터 계약 v0.2 §10.4, [기능 안내](../features/scx-drive.md)
- 관련: [Windows 배포 정책](../windows-deployment-policy.md), [ADR 0005](0005-windows-server-offline-deployment.md)

## 결정

1. 대시보드는 `SIMDASH_DRIVE_GATEWAY=scx`일 때만 사내 어댑터 패키지 `scx_drive_adapter`(wheel `vd_scx_drive_adapter`, 표준 라이브러리만 사용)를 **지연 import**한다. 기본값 `none`에서는 import하지 않으며 지금과 동작이 같다.
2. 어댑터 wheel과 SDK(`siemens_scx`) wheel은 **이 저장소와 대시보드 배포 패키지에 넣지 않는다.** 사용자가 어댑터 릴리스 zip을 서버·PC에 따로 반입해 ① 워커 런타임(별도 venv, Python 3.11 x64)을 어댑터의 `install_runtime.ps1`로 설치하고 ② 어댑터 wheel을 보존 폴더 `external-wheels`(아래)에 한 번 복사하면 설치 스크립트가 대시보드 venv에 `--no-deps --no-index`로 설치한다. `siemens_scx`는 대시보드 venv에 설치하지 않는다.
3. scx 모드에서 어댑터가 없으면 대시보드는 **기동을 거부**하고 설치 명령을 안내한다(한국어·영어). 필수 설정 누락, 다른 프로세스의 `dashboard.lock` 보유(단일 프로세스 위반)도 기동 거부다.
4. 가짜 어댑터(메모리 드라이브)는 `backend/tests/` 전용이며 `app/`에서 import하지 않는다.
5. 토큰 암호화는 기존 잠금 의존성 `cryptography`(PyJWT[crypto]가 이미 설치)를 직접 의존성으로 명시해 쓴다. 버전·wheel은 바뀌지 않는다.

## 배포 계약에 대한 영향

- 기본 `none` 모드의 최초 설치·업데이트·서비스 시작 전제조건은 바뀌지 않는다(배포 계약 1·2항).
- scx 모드는 **선택 기능**이며, 그 런타임(어댑터 wheel·워커 venv)은 대시보드 패키지 밖에서 공급된다. 폐쇄망 서버 반입물은 어댑터 릴리스 zip이 추가된다(인터넷·Git·Node 불필요, 어댑터 `install_runtime.ps1`은 오프라인 설치).
- 설치기와의 충돌과 해결: 소스 설치·업데이트는 `uv pip sync`로 venv를 lock과 정확히 맞추고, 폐쇄망 설치기는 릴리스마다 새 venv를 만든다. 두 경우 모두 따로 설치한 어댑터 wheel이 사라진다. 그래서 **외부 wheel 보존 폴더**를 둔다.
  - 위치: 소스 배포는 저장소 루트 `external-wheels\`(`.gitignore`, Git 업데이트 보호 경로 — 원격이 이 경로를 추적하면 업데이트 거부), 폐쇄망 서버는 `<설치 루트>\state\external-wheels\`(릴리스 교체 경로 밖, 링크·정션 거부). wheel은 저장소·배포 패키지에 넣지 않는다.
  - 동작: `setup.ps1`과 `scripts/windows/prepare-source-environment.ps1`(`deploy.bat`·`update.bat` 경로)은 `uv pip sync` 직후, 폐쇄망 `install.ps1`은 새 venv에 lock 설치·`pip check` 직후 폴더의 `*.whl`을 `--no-deps --no-index`로 설치하고(색인·네트워크 미사용) 어댑터 wheel이 있거나 scx 모드면 `import scx_drive_adapter`를 확인해 출력한다. 공용 로직은 `scripts/windows/ExternalWheels.psm1`, 설치기는 단일 파일 원칙에 따라 같은 규칙의 ASCII 함수를 내장한다.
  - 모드: `SIMDASH_DRIVE_GATEWAY`를 앱과 같은 우선순위(프로세스 환경 → 루트 `.env` → `backend\.env`; 서버는 `state\.env`)로 읽는다. 폴더 없음·빈 폴더 + `none`은 아무것도 하지 않는다(기본 동작 불변). `none`에서 설치·import 실패는 경고 후 계속, `scx`에서 wheel 없음·설치·import 실패는 명확한 오류로 중단(서버 설치기는 서비스 정지 전 준비 단계에서 중단).
  - 검증: `scripts/windows/external-wheels-self-test.ps1`(있음/없음/실패/모드 우선순위/보호 경로), `source-frontend-build-self-test.ps1`의 sync 뒤 설치 순서 fixture, `offline-installer-self-test.ps1` 계약 문자열, `backend/tests/test_windows_external_wheels.py` 정적 계약 — Windows 배포 계약 CI에 포함.
  - 남은 범위(D5): 워커 런타임 설치 단계(선택)와 서비스 환경변수 안내.
- DB: migration `0036_drive_credentials`(추가 전용). 빈 설치·기존 DB 업데이트를 검증했다(작업 기록 2026-10-07 D0).

## 거부한 대안

- 어댑터 wheel을 대시보드 wheelhouse에 넣기: 사용자 결정 D6(저장소에 넣지 않음)에 어긋나고, 어댑터 릴리스 주기를 대시보드 릴리스에 묶는다.
- SDK를 대시보드 venv에 함께 설치: 의존성 고정 충돌(pydantic·PyJWT)과 adcopy 명령줄 토큰 출력(contract §10.1) 때문에 불가.
