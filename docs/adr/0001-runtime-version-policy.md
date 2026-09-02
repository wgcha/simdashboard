# ADR 0001: WSL/Rocky 런타임 버전 정책

- 상태: 승인
- 기준일: 2026-08-13

## 결정

개발, CI, Rocky Linux 8 운영은 Python 3.12 계열과 PostgreSQL 18 계열을 동일하게 사용한다. Python patch 버전은 `.python-version`과 배포 manifest에서 함께 고정하고, PostgreSQL은 18.x의 최신 보안 patch를 허용한다.

Rocky 8의 시스템 Python/PostgreSQL 패키지에 애플리케이션을 맞추지 않는다. Python은 애플리케이션 전용 가상환경으로 설치하고 PostgreSQL 18은 조직에서 승인한 PGDG 또는 내부 mirror 패키지로 공급한다. 시스템 Python에 package를 직접 설치하지 않는다.

DuckDB는 단일 프로세스 로컬 개발·데이터 이관 adapter로만 사용한다. 동시 사용자 운영 profile은 PostgreSQL만 지원한다.

## 변경 조건

Rocky 보안 정책이나 내부 mirror가 위 버전을 제공하지 못하면 배포 전에 이 ADR을 갱신하고, WSL/CI/운영의 Python minor 및 PostgreSQL major를 다시 동일하게 맞춘다. 서로 다른 major/minor 조합을 묵시적으로 지원하지 않는다.
