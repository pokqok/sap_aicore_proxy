# SAP AI Core → OpenAI Compatible Proxy

SAP AI Core deployment를 **OpenAI 호환 API 엔드포인트**로 변환하는 경량 프록시 서버.  
Cursor, Windsurf, Continue 등 OpenAI API를 지원하는 모든 도구에서 SAP AI Core 모델을 바로 사용할 수 있습니다.

## 주요 기능

| 기능 | 설명 |
|---|---|
| **OAuth2 자동 관리** | 토큰 자동 발급 및 만료 시간 기반 선제 갱신 |
| **JSON 키 지원** | BTP 서비스 키 (`sap_key.json`) 파일 자동 인식 |
| **다중 사용자 보호** | 자체 API Key 인증(`allowed_keys.json`)으로 다수 사용자 접근 제어 |
| **동적 오버라이딩** | HTTP 커스텀 헤더를 통한 배포 모델 및 리소스 그룹 동적 변경 |
| **플랫폼 호환성** | Docker 기반 로컬 실행 및 SAP BAS/Cloud Foundry(`manifest.yml`) 완벽 지원 |
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

### 2. (선택) 다중 사용자 API Key 발급 (`allowed_keys.json`)
아무나 이 프록시를 사용하는 것을 막으려면 접근을 허락할 "프록시 API Key" 목록을 만듭니다.

```json
[
  "alice-secret",
  "bob-token",
  "dev-team-xyz"
]
```
*(파일이 없으면 모든 요청을 허용합니다.)*

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

### Cursor
`Settings` → `Models` → OpenAI API 설정:
- **Base URL**: `http://localhost:8000/v1` (또는 배포된 서버 URL)
- **API Key**: `allowed_keys.json`에 등록한 본인의 키 (예: `alice-secret`)
- **Model**: 배포 ID 지정 (예: `d1234abcd` 등)

### Windsurf
`Settings` → `AI Providers` → Custom Provider:
- **Base URL**: `http://localhost:8000/v1`
- **API Key**: 발급받은 프록시 API Key

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
