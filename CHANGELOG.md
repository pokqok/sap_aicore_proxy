# CHANGELOG

이 프로젝트의 주요 변경사항을 기록합니다.

## [1.0.0] - 초기 릴리스

### 추가
- SAP AI Core → OpenAI 호환 프록시 서버
- OAuth2 토큰 자동 발급 및 캐싱 (만료 60초 전 선제 갱신)
- `POST /v1/chat/completions` — 스트리밍/비스트리밍 지원
- `GET /v1/models` — 도구 호환용 모델 목록 엔드포인트
- `GET /health` — 헬스체크 엔드포인트
- Docker 및 Docker Compose 지원
- 환경변수 기반 설정 (`.env` 파일)
