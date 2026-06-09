import os
import sys

try:
    from openai import OpenAI
except ImportError:
    print("openai 라이브러리가 설치되어 있지 않습니다.")
    print("터미널에서 다음 명령어를 실행해주세요: pip install openai")
    sys.exit(1)

# ── 설정 부분 ────────────────────────────────────────────────────────
# 로컬에서 띄웠다면 http://localhost:8000/v1
# CF에 배포했다면 https://sap-aicore-proxy.../v1
BASE_URL = "https://sap-aicore-proxy.cfapps.jp10.hana.ondemand.com/v1" 

# Admin UI에서 직접 발급받은 API Key (sk-...)
API_KEY = "sk-a4fe0cf81e55e6bb606a43cf3d52de7e"
# ───────────────────────────────────────────────────────────────────

print(f"🔌 Connecting to SAP AI Core Proxy at: {BASE_URL}")
print("-" * 50)

try:
    # 일반 OpenAI 라이브러리를 그대로 사용합니다!
    client = OpenAI(
        base_url=BASE_URL,
        api_key=API_KEY
    )

    # 1. 일반 응답 (Non-Streaming) 테스트
    print("\n[테스트 1] 일반 응답 (Non-Streaming) 요청 중...")
    response = client.chat.completions.create(
        model="gpt-4o", # SAP Orchestration에 전달할 실제 LLM 모델명
        messages=[
            {"role": "user", "content": "안녕! 넌 누구니? 한 문장으로 짧게 대답해줘."}
        ]
    )
    print("🤖 응답:", response.choices[0].message.content)
    if response.usage:
        print(f"📊 사용량: {response.usage.total_tokens} tokens")

    print("-" * 50)

    # 2. 스트리밍 응답 (Streaming) 테스트
    print("\n[테스트 2] 스트리밍 (Streaming) 요청 중...")
    stream = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "user", "content": "1부터 5까지 숫자를 세어볼래?"}
        ],
        stream=True
    )
    
    print("🤖 응답: ", end="")
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)
    
    print("\n\n✅ 모든 테스트가 성공적으로 완료되었습니다!")

except Exception as e:
    print(f"\n❌ [오류 발생] {e}")
    print("서버가 켜져 있는지, API Key가 올바른지 확인해주세요.")
