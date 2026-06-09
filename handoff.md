# Project Handoff: SAP AI Core Proxy

## 1. 프로젝트 개요 (Project Overview)
이 프로젝트는 **SAP AI Core**를 **OpenAI 호환 API(OpenAI-compatible API)**로 변환해주는 경량 프록시 서버입니다. 
Cursor, Windsurf 등 OpenAI 규격을 요구하는 서드파티 AI 코딩 도구에서 SAP AI Core 모델(GPT-4, Claude 등)을 직접 연동해 사용할 수 있도록 돕습니다.

### 핵심 기능
- **규격 변환 (오케스트레이션 호환)**: Cursor 등에서 보내는 OpenAI 규격의 요청을 SAP AI Core **Orchestration API** 전용 JSON 규격(`orchestration_config` 등)으로 변환하여 전송하고, 반환되는 오케스트레이션 응답을 다시 OpenAI 규격으로 역변환합니다. (스트리밍 SSE 포함)
- **자동 인증 관리**: SAP XSUAA OAuth2 토큰 만료를 추적하고, 만료 전 자동으로 갱신(Token Cache).
- **Admin 대시보드 내장**: 웹 UI를 통해 다중 사용자(API Key)를 발급하고, 모델 매핑 및 사용량 로그 조회 가능.
- **다중 사용자 라우팅**: 사용자별로 서로 다른 배포 모델(`deployment_id`)과 리소스 그룹(`resource_group`) 지정이 가능하며, 클라이언트에서 전송한 `model` 파라미터를 동적으로 오케스트레이션에 매핑합니다.

---

## 2. 아키텍처 및 모듈 구조 (Architecture)
코드는 유지보수를 위해 기능별로 완벽히 분리되어 있습니다. 모든 의존성은 `requirements.txt`에 명시되어 있으며 데이터베이스 없이 파일 시스템(`*.json`, `*.log`)을 사용합니다.

- **`main.py`**: 애플리케이션 진입점(FastAPI app 생성), 전역 미들웨어(API Key 검증) 및 라우터 등록.
- **`config.py`**: 환경변수(`.env`) 및 `sap_key.json`, `allowed_keys.json`을 읽고 설정 객체를 초기화.
- **`auth.py`**: SAP AI Core와의 백그라운드 서버 대 서버 인증 처리 및 토큰 캐싱(`TokenCache`).
- **`proxy.py`**: 이 프로젝트의 핵심입니다. OpenAI 규격 API 요청을 받아 SAP AI Core Orchestration 엔드포인트(`/completion`)가 요구하는 `module_configurations` 기반의 복잡한 JSON으로 변환하여 요청을 보내고, SSE 스트리밍 응답을 OpenAI 청크(chunk) 형태로 쪼개어 역변환합니다.
- **`admin.py`**: `/admin` 경로로 제공되는 관리자 대시보드 API.
- **`usage.py`**: 사용자별 토큰 소비량을 `usage.log`에 JSONL 형태로 Append 및 조회 파싱.
- **`static/admin.html`**: 외부 라이브러리 없이 순수 HTML/CSS/JS로 짜여진 관리자 웹 대시보드.

---

## 3. 핵심 설정 파일 (Configurations)

- **`sap_key.json`**: SAP BTP Cockpit에서 다운로드한 AI Core 서비스 키. `clientid`, `clientsecret`, 인증 URL 및 API URL이 들어있습니다. (보안상 `.gitignore` 적용됨)
- **`allowed_keys.json`**: Admin UI에서 생성된 사용자 API Key 정보가 딕셔너리 형태로 저장됩니다. (보안상 `.gitignore` 적용됨)
- **`usage.log`**: 토큰 사용량이 실시간으로 기록되는 파일입니다. (보안상 `.gitignore` 적용됨)
- **`.env`**: 서버 포트나 `ADMIN_PASSWORD` 등을 로컬에서 테스트할 때 설정합니다.

---

## 4. 배포 및 구동 환경 (Deployment)
현재 **SAP Cloud Foundry (CF)** 환경에 최적화되어 배포(`sap-aicore-proxy.cfapps.jp10.hana.ondemand.com`)되어 있습니다.

- **`manifest.yml`**: SAP BAS/CF 환경에서 `cf push` 만으로 즉시 배포 가능하도록 구성됨. 
  - `command: uvicorn main:app --host 0.0.0.0 --port $PORT` 로 동적 포트 바인딩.
  - `ADMIN_PASSWORD` 등을 `env` 블록에서 안전하게 설정 관리.
- **Docker**: `Dockerfile`과 `docker-compose.yml`이 준비되어 있어 필요 시 AWS, GCP 등 타 클라우드나 온프레미스 서버 배포도 언제든 가능합니다.

---

## 5. 알려진 제약 및 유지보수 규칙 (Cursor Rules)
- **데이터베이스 도입 금지**: 극단적인 가벼움을 지향합니다. SQLite, PostgreSQL 등은 절대 붙이지 말고 현재의 파일 기반(`allowed_keys.json`, `usage.log`) 구조를 유지해야 합니다.
- **인증 무력화 금지**: `main.py`의 `verify_api_key` 미들웨어는 다중 사용자 보호와 중앙 집중식 라우팅의 핵심입니다. 이를 삭제하거나 건너뛰게 만들지 마세요.
- **단일 UI 유지**: `admin.html`을 Vue나 React 등 거대한 프론트엔드 프레임워크 구조로 교체하지 마세요. 현재의 독립된(Self-contained) 모던 HTML 아키텍처로 충분히 관리 가능합니다.

## 6. 개발 인수인계 시 주의사항
- 다른 AI 에이전트(Cursor, Windsurf 등)에게 코드를 수정하게 하기 전, `CLAUDE.md`, `.cursorrules`, `.windsurfrules` 등의 규칙 파일을 지우지 마세요. 에이전트가 코드를 훼손하지 못하게 막는 안전장치입니다.
- **오케스트레이션 페이로드 구조**: 현재 `proxy.py`는 SAP AI Core의 **오케스트레이션(Orchestration)** 배포 방식에 맞춰져 있습니다. `llm_module_config`와 필수 항목인 빈 `templating_module_config` 구조를 유지해야 400 Bad Request 에러가 나지 않습니다.
- **SQLite 마이그레이션(예정)**: 현재는 단일 파일 정책을 준수하기 위해 `allowed_keys.json`과 `usage.log` 등 파일 기반 저장을 사용 중이나, CF 컨테이너 재시작 시 휘발되는 문제가 있습니다. 향후 Persistent Volume 마운트나 DB 마이그레이션을 고려해야 합니다.
