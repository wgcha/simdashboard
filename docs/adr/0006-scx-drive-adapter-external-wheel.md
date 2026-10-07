# ADR 0006: SCX 드라이브 어댑터는 선택 런타임 의존성으로 따로 공급한다

- 상태: 승인(사용자 결정 D6·D7, 2026-10-07 15:40), D0 구현. 설치기 반영은 D5
- 날짜: 2026-10-07
- 근거: [SCX 드라이브 연동 계획 §9](../plans/scx-drive-integration.md), 이슈 #45 어댑터 계약 v0.2 §10.4, [기능 안내](../features/scx-drive.md)
- 관련: [Windows 배포 정책](../windows-deployment-policy.md), [ADR 0005](0005-windows-server-offline-deployment.md)

## 결정

1. 대시보드는 `SIMDASH_DRIVE_GATEWAY=scx`일 때만 사내 어댑터 패키지 `scx_drive_adapter`(wheel `vd_scx_drive_adapter`, 표준 라이브러리만 사용)를 **지연 import**한다. 기본값 `none`에서는 import하지 않으며 지금과 동작이 같다.
2. 어댑터 wheel과 SDK(`siemens_scx`) wheel은 **이 저장소와 대시보드 배포 패키지에 넣지 않는다.** 사용자가 어댑터 릴리스 zip을 서버·PC에 따로 반입해 ① 워커 런타임(별도 venv, Python 3.11 x64)을 어댑터의 `install_runtime.ps1`로 설치하고 ② 대시보드 venv에 어댑터 wheel만 `pip install --no-deps`로 설치한다. `siemens_scx`는 대시보드 venv에 설치하지 않는다.
3. scx 모드에서 어댑터가 없으면 대시보드는 **기동을 거부**하고 설치 명령을 안내한다(한국어·영어). 필수 설정 누락, 다른 프로세스의 `dashboard.lock` 보유(단일 프로세스 위반)도 기동 거부다.
4. 가짜 어댑터(메모리 드라이브)는 `backend/tests/` 전용이며 `app/`에서 import하지 않는다.
5. 토큰 암호화는 기존 잠금 의존성 `cryptography`(PyJWT[crypto]가 이미 설치)를 직접 의존성으로 명시해 쓴다. 버전·wheel은 바뀌지 않는다.

## 배포 계약에 대한 영향

- 기본 `none` 모드의 최초 설치·업데이트·서비스 시작 전제조건은 바뀌지 않는다(배포 계약 1·2항).
- scx 모드는 **선택 기능**이며, 그 런타임(어댑터 wheel·워커 venv)은 대시보드 패키지 밖에서 공급된다. 폐쇄망 서버 반입물은 어댑터 릴리스 zip이 추가된다(인터넷·Git·Node 불필요, 어댑터 `install_runtime.ps1`은 오프라인 설치).
- 현재 설치기와의 충돌: 소스 설치·업데이트는 `uv pip sync`로 venv를 lock과 정확히 맞추고, 폐쇄망 설치기는 릴리스마다 새 venv를 만든다. 두 경우 모두 따로 설치한 어댑터 wheel이 **사라진다.** D5에서 다음을 같은 변경으로 반영한다(이번 D0에서는 설치 스크립트를 바꾸지 않는다).
  - 설치기·`update.bat`이 **외부 어댑터 wheel 경로**(예: 설정값 또는 `deploy\external-wheels\`)를 받아 lock 설치 직후 `--no-index --no-deps`로 설치하고 import를 확인한다. 경로가 없으면 건너뛴다.
  - 워커 런타임 설치 단계(선택, 패키지가 있을 때만)와 서비스 환경변수 안내.
  - Windows 배포 계약 CI에 "외부 wheel 있음/없음" 두 경로 검사.
- DB: migration `0036_drive_credentials`(추가 전용). 빈 설치·기존 DB 업데이트를 검증했다(작업 기록 2026-10-07 D0).

## 거부한 대안

- 어댑터 wheel을 대시보드 wheelhouse에 넣기: 사용자 결정 D6(저장소에 넣지 않음)에 어긋나고, 어댑터 릴리스 주기를 대시보드 릴리스에 묶는다.
- SDK를 대시보드 venv에 함께 설치: 의존성 고정 충돌(pydantic·PyJWT)과 adcopy 명령줄 토큰 출력(contract §10.1) 때문에 불가.
