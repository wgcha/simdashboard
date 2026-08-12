# WSL 개발환경 설치 및 운영

이 문서는 Analysis Canvas를 WSL2 Ubuntu에서 재현 가능하게 실행하기 위한 실제 설치 절차다. 운영 전환과 장기 아키텍처 원칙은 `CAE_Dashboard_WSL_Ubuntu_to_Rocky8_Development_Guide.md`를 함께 참고한다.

## 고정 도구와 격리 원칙

- Node.js는 `.node-version`의 버전을 사용자 홈(`~/.local/share/analysis-canvas`)에 설치한다.
- Python은 `.python-version`을 `uv`가 관리하며, 저장소에는 Linux 전용 `.venv-wsl`을 만든다.
- pnpm은 `frontend/package.json#packageManager` 버전을 Corepack으로 활성화한다.
- Windows의 `.venv`와 WSL의 `.venv-wsl`을 섞지 않는다.
- 설치 스크립트는 시스템 Python/Node를 교체하지 않으며 `sudo`를 사용하지 않는다.

## 권장 저장소 위치

파일 감시, `node_modules`, Python 패키지 설치 성능을 위해 저장소는 `/mnt/c`, `/mnt/e`가 아니라 WSL의 Linux 파일시스템에 둔다.

```bash
mkdir -p ~/src
cd ~/src
git clone <repository-url> simulation_dashboard
cd simulation_dashboard
```

Windows에서 이 폴더는 `\\wsl$\Ubuntu\home\<user>\src\simulation_dashboard`로 접근할 수 있다. `/mnt/e/simulation_dashboard`에서도 설치 스크립트는 동작하지만 경고를 출력한다.

## 최초 설치

Ubuntu에 `curl`, `git`, `tar`, `xz`, `sha256sum`이 있어야 한다. 그 다음 저장소 루트에서 실행한다.

```bash
chmod +x setup-wsl.sh start.sh stop.sh scripts/wsl/*.sh
./setup-wsl.sh
```

스크립트는 Node.js 배포 파일의 공식 SHA-256을 검증하고, Python 및 프런트엔드 의존성을 잠금 파일 기준으로 설치한다.

## 실행과 점검

```bash
./scripts/wsl/doctor.sh
./start.sh
# http://127.0.0.1:5173
# http://127.0.0.1:8000/docs

./stop.sh
```

개별 검증은 다음처럼 실행한다.

```bash
(cd backend && ../.venv-wsl/bin/python -m pytest -q)
pnpm --dir frontend run build
```

## 브라우저 E2E 환경

Playwright E2E를 처음 실행할 때는 WSL 사용자용 Chromium과 Ubuntu 공유 라이브러리를 설치한다. 시스템 패키지 설치 때문에 이 단계에만 `sudo`가 필요하다.

```bash
./scripts/wsl/setup-e2e.sh --install-system-deps
```

이후 테스트 실행기는 `.venv-wsl/bin/python`을 자동으로 사용한다.

```bash
pnpm --dir frontend run test:e2e
# 특정 시나리오만 실행
node frontend/scripts/run-e2e.mjs bootstrap-empty-state.spec.ts
```

브라우저 바이너리만 갱신하거나 설치 상태를 재검사할 때는 `sudo` 없이 실행한다.

```bash
./scripts/wsl/setup-e2e.sh
```

## 장애 처리

- `pnpm was not found`: 새 WSL 셸을 열거나 `export PATH="$HOME/.local/bin:$PATH"` 후 doctor를 다시 실행한다.
- Windows용 venv 실행 오류: `.venv`가 아니라 `.venv-wsl/bin/python`을 사용한다.
- Vite 파일 변경 감지가 느림: 저장소를 `~/src` 아래로 옮긴다.
- 포트가 이미 사용 중: `./stop.sh`를 먼저 실행하고 `.server-pids.env`의 소유 PID를 확인한다.
- 설치 중 네트워크가 끊김: `./setup-wsl.sh`를 다시 실행한다. 이미 검증된 도구와 가상환경은 재사용된다.
- Chromium 공유 라이브러리 오류: `./scripts/wsl/setup-e2e.sh --install-system-deps`를 다시 실행한다.
