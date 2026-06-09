# CLAUDE.md — 프로젝트 규칙 (AI 코딩 어시스턴트용)

이 문서는 AI 코딩 어시스턴트가 이 프로젝트를 수정할 때 반드시 지켜야 하는 규칙입니다.
사람이 아니라 AI 모델이 읽는 문서입니다. **모든 규칙을 절대적으로 준수하세요.**

---

## 프로젝트 개요

- **목적**: SAP AI Core 배포를 OpenAI 호환 API(`/v1/chat/completions`)로 변환하는 **경량 프록시 서버**
- **핵심 가치**: 단순함. 단일 파일(`main.py`) 프록시. 과도한 추상화 금지.
- **기술 스택**: Python 3.12, FastAPI, httpx, pydantic-settings, uvicorn
- **배포**: Docker (`python:3.12-slim` 이미지)

---

## 절대 하지 말 것 (NEVER DO)

### 아키텍처

- ❌ `main.py`를 여러 파일로 분리하지 마라 (router, service, repository 등으로 쪼개지 마라)
- ❌ 데이터베이스(SQLite, PostgreSQL, Redis 등)를 추가하지 마라
- ❌ ORM(SQLAlchemy 등)을 추가하지 마라
- ❌ Alembic 등 마이그레이션 도구를 추가하지 마라
- ❌ 별도의 config 파일 포맷(YAML, TOML 등)을 도입하지 마라 — 환경변수(`.env`)만 사용
- ❌ 미들웨어를 추가하지 마라 (CORS, rate limiting 등)
- ❌ 프론트엔드/UI를 추가하지 마라
- ❌ 테스트 프레임워크를 도입하지 마라 (이 프로젝트는 개인 유틸리티임)

### 인증 (보안 핵심)

- ❌ 프록시 자체에 제공되는 다중 사용자 `verify_api_key` 미들웨어를 제거하거나 로직을 무력화하지 마라.
- ❌ 추가적인 사용자 관리(DB 연동, JWT, 세션 등)를 도입하지 마라.
- ❌ OAuth2 토큰 흐름(`TokenCache`)의 로직을 변경하지 마라 — 이미 정확히 동작함
  - 토큰은 XSUAA `client_credentials` grant로 발급
  - 만료 60초 전에 선제 갱신
  - `asyncio.Lock`으로 동시 갱신 방지
  - 이 로직을 건드리면 SAP 인증이 깨짐

### API 호환성

- ❌ 엔드포인트 경로를 변경하지 마라:
  - `POST /v1/chat/completions` — 변경 금지
  - `GET /v1/models` — 변경 금지
  - `GET /health` — 변경 금지
- ❌ `body.pop("model", None)` 라인을 제거하지 마라 — SAP AI Core는 model 필드를 무시하므로 의도적으로 제거하는 것
- ❌ 요청/응답 body의 구조를 변환하거나 가공하지 마라 — 그대로 전달하는 것이 핵심
- ❌ Pydantic 요청 모델을 정의하지 마라 — `Request.json()`으로 raw body를 받아 그대로 전달하는 것이 의도

### Docker

- ❌ 베이스 이미지를 `python:3.12-slim`에서 변경하지 마라
- ❌ Dockerfile에 `apt-get install`을 추가하지 마라 — `curl` 등 추가 패키지 필요 없음
- ❌ `docker-compose.yml`의 healthcheck에 `curl`을 사용하지 마라 — 이미지에 curl이 없음. `python urllib`을 사용
- ❌ multi-stage build로 변경하지 마라 — 불필요한 복잡성

### 의존성

- ❌ `requirements.txt`에 새 패키지를 추가하지 마라 (현재 5개면 충분)
  - `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic`, `pydantic-settings`
- ❌ 현재 패키지의 버전 상한을 고정(`==`)하지 마라 — 하한(`>=`)만 사용

---

## 반드시 지킬 것 (ALWAYS DO)

### 코드 스타일

- ✅ 코드는 기능별로 모듈화하여 관리하라 (`main.py`, `config.py`, `auth.py`, `proxy.py`, `admin.py`, `usage.py`)
- ✅ 함수는 `async def`로 작성하라 (FastAPI 비동기 패턴)
- ✅ 로깅은 `logger = logging.getLogger("sap-proxy")`를 사용하라
- ✅ 설정은 `.env`와 `sap_key.json`에서 로드되도록 구성하라
- ✅ 여러 사용자 인증을 위한 `allowed_keys.json`을 읽고 `Authorization` 헤더를 검증하라 (딕셔너리 포맷 지원)
- ✅ Admin UI는 `/static/admin.html` 단일 파일로 구성하고 순수 HTML/JS/CSS만 사용하라

### SAP AI Core 관련

- ✅ SAP AI Core API 경로는 `/v2/inference/deployments/{deployment_id}/...` 형식을 유지하라
- ✅ 요청 헤더에 `AI-Resource-Group`을 항상 포함하라
- ✅ Authorization 헤더는 `Bearer {token}` 형식을 사용하라
- ✅ 토큰 인증은 `{auth_url}/oauth/token`에 `client_credentials` grant를 사용하라

### 스트리밍

- ✅ `stream: true`일 때 `StreamingResponse`와 `text/event-stream`을 사용하라
- ✅ `stream: false`일 때 `JSONResponse`를 사용하라
- ✅ 스트리밍 시 `client.stream()`으로 SAP 응답을 청크 단위로 전달하라

---

## 환경변수 규칙

| 변수 | 타입 | 필수 | Settings 필드명 |
|---|---|---|---|
| `AI_CORE_CLIENT_ID` | str | ✅ | `ai_core_client_id` |
| `AI_CORE_CLIENT_SECRET` | str | ✅ | `ai_core_client_secret` |
| `AI_CORE_BASE_URL` | str | ✅ | `ai_core_base_url` |
| `AI_CORE_AUTH_URL` | str | ✅ | `ai_core_auth_url` |
| `AI_CORE_RESOURCE_GROUP` | str | ❌ (default: "default") | `ai_core_resource_group` |
| `AI_CORE_DEPLOYMENT_ID` | str | ✅ | `ai_core_deployment_id` |
| `PROXY_PORT` | int | ❌ (default: 8000) | `proxy_port` |
| `LOG_LEVEL` | str | ❌ (default: "INFO") | `log_level` |

- 새 환경변수를 추가할 때: `Settings` 클래스 → `.env.example` → `README.md` 환경변수 테이블 **세 곳 모두** 업데이트하라.

---

## 파일 구조 (변경 금지)

```
sap-api-proxy/
├── main.py              # 유일한 소스 코드 파일. 절대 분리하지 마라.
├── requirements.txt     # Python 의존성. 최소한으로 유지.
├── Dockerfile           # 단순한 단일 스테이지 빌드.
├── docker-compose.yml   # 실행 설정. healthcheck는 python으로.
├── .env.example         # 환경변수 템플릿. Settings와 동기화 유지.
├── .gitignore           # Git 제외 규칙.
├── README.md            # 사용 문서.
├── CHANGELOG.md         # 변경 이력.
└── LICENSE              # MIT License.
```

- 새 `.py` 파일을 만들지 마라.
- `src/`, `app/`, `tests/`, `utils/` 등의 디렉터리를 만들지 마라.

---

## 수정이 허용되는 경우

다음의 경우에만 코드 수정이 허용됨:

1. **버그 수정** — 기존 동작을 깨뜨리지 않는 범위
2. **새 OpenAI 호환 엔드포인트 추가** — `/v1/` 경로 아래, SAP AI Core가 지원하는 것만 (예: `/v1/embeddings`)
3. **에러 처리 개선** — 기존 흐름을 변경하지 않는 범위에서 로깅/에러 메시지 개선
4. **환경변수 추가** — 위 규칙에 따라 세 곳 동기화
