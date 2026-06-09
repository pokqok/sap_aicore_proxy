# SAP AI Core → OpenAI Compatible Proxy

SAP AI Core deployment를 **OpenAI 호환 API 엔드포인트**로 변환하는 경량 프록시 서버.  
Cursor, Windsurf, Continue 등 OpenAI API를 지원하는 모든 도구에서 SAP AI Core 모델을 바로 사용할 수 있습니다.

## 주요 기능

| 기능 | 설명 |
|---|---|
| **OAuth2 자동 관리** | 토큰 자동 발급 및 만료 시간 기반 선제 갱신 |
| **JSON 키 지원** | BTP 서비스 키 (`sap_key.json`) 파일 자동 인식 |
| **중앙 집중식 라우팅** | 사용자별 API Key에 `deployment_id`, `resource_group` 매핑 |
| **사용량 추적 로깅** | 스트리밍 포함, 유저별 토큰 소모량을 `usage.log`에 자동 기록 |
| **다중 사용자 보호** | 자체 API Key 인증(`allowed_keys.json`)으로 접근 제어 |
| **동적 오버라이딩** | HTTP 커스텀 헤더를 통한 배포 모델 동적 변경 |
| **플랫폼 호환성** | Docker 기반 로컬 실행 및 SAP BAS/Cloud Foundry 완벽 지원 |
| **Streaming 지원** | SSE 기반 스트리밍 / 일반 응답 모두 지원 |

---

## 아키텍처 및 다중 사용자 구조

```
┌──────────────────┐
│  Alice (Cursor)  │ ─── API Key: "alice-secret" ───┐
└──────────────────┘                                ▼
                                         ┌─────────────────────┐       ┌─────────────────┐
┌──────────────────┐                     │  이 프록시 서버       │       │  SAP AI Core    │
│  Bob (Windsurf)  │ ─── API Key: "bob-token" ───▶│  (인증 검증 통과)     │ ────▶ │  (배포된 모델)    │
└──────────────────┘                     │                     │       │                 │
                                         └──────────┬──────────┘       └─────────────────┘
                                                    │
                                   (1개의 SAP Service Key 사용)
                                                    ▼
                                         ┌─────────────────────┐
                                         │  XSUAA (OAuth2)     │
                                         │  토큰 자동 갱신       │
                                         └─────────────────────┘
```

> **💡 안전성**: 비동기(Async) 기반으로 동작하므로 여러 사용자가 동시에 스트리밍 응답을 요청하더라도 **절대 데이터가 섞이지 않습니다.** 

---

## 빠른 시작

### 1. SAP AI Core 연동 설정 (`sap_key.json`)
SAP BTP Cockpit에서 다운로드 받은 서비스 키 전체 내용을 프로젝트 폴더의 `sap_key.json` 이름으로 저장하세요.  
(직접 `.env`에 입력하는 방식도 계속 지원됩니다.)

```json
{
  "clientid": "...",
  "clientsecret": "...",
  "url": "https://...authentication...",
  "serviceurls": {
    "AI_API_URL": "https://api.ai..."
  }
}
```

### 2단계: 다중 사용자 키 세팅 및 라우팅 설정 (권장)
사용자별로 API Key를 발급하고, 해당 사용자가 어떤 모델(Deployment ID)을 쓸지 서버에서 중앙 통제할 수 있습니다.

1. 프로젝트 폴더에 **`allowed_keys.json`** 파일을 만듭니다.
2. 각 키에 매핑할 메타데이터(`user_id`, `deployment_id` 등)를 아래처럼 적고 저장합니다.

```json
{
  "alice-secret": {
    "user_id": "Alice",
    "deployment_id": "d1234abcd",
    "resource_group": "default"
  },
  "team-b-token": {
    "user_id": "Team B",
    "deployment_id": "d9876efgh",
    "resource_group": "dev-team-rg"
  }
}
```
*(과거의 단순 배열 `["key1", "key2"]` 포맷도 계속 지원됩니다.)*

> **💡 사용량 추적 기능**: 응답이 완료될 때마다 사용자별 토큰 소비량(Prompt / Completion)이 프로젝트 폴더의 `usage.log` 파일에 JSONL 형식으로 자동 기록됩니다.

### 3. 배포 (Deployment)

**A. SAP BAS / Cloud Foundry 환경**
```bash
# manifest.yml이 준비되어 있으므로 한 줄로 푸시 가능합니다.
cf push
```

**B. Docker (로컬/서버)**
```bash
docker compose up -d
```

**C. 로컬 Python 직접 실행**
```bash
pip install -r requirements.txt
python main.py
```

---

## 도구별 연결 방법

### Cursor / Windsurf 등 AI 코딩 툴
`Settings` → `Models` / `AI Providers` 설정 창에서:
- **Base URL**: `http://localhost:8000/v1` (또는 배포된 서버 URL)
- **API Key**: 관리자에게 발급받은 본인의 프록시 키 (예: `alice-secret`)
- **Model**: **아무 값이나 입력해도 됩니다.** 프록시 서버의 `allowed_keys.json`이 자동으로 알맞은 배포 모델로 요청을 라우팅해 줍니다!

---

## 고급: HTTP 커스텀 헤더 오버라이딩

한 프록시 서버에서 여러 개의 SAP 배포 모델(Deployment)을 동적으로 선택해 사용하려면, 클라이언트에서 아래 헤더를 추가해 요청할 수 있습니다.

- `x-sap-deployment-id`: 요청의 모델 배포 ID를 덮어씁니다.
- `x-sap-resource-group`: 요청의 리소스 그룹을 덮어씁니다. (기본값: `default`)

---

## 트러블슈팅

### 401 Unauthorized 오류
- **`Missing or invalid Authorization header`**: Cursor 등에서 API Key를 입력하지 않았을 때 발생합니다.
- **`Unauthorized API Key`**: 입력한 API Key가 `allowed_keys.json`에 없거나 오타가 있을 때 발생합니다.
- **`SAP error 401 (httpx.HTTPStatusError)`**: 프록시가 SAP에 접근하지 못하는 상태입니다. `sap_key.json` 정보가 올바른지 확인하세요.

---

## 라이선스
MIT License
