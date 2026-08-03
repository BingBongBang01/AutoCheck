"""클라우드 AI 제공자 표 — 제공자에 관한 유일한 정본.

여기 한 곳을 읽는 쪽이 셋 있다:
  - api/cloud_ai_api.py  : 환경 설정 화면의 제공자 드롭다운/모델 목록/연결 테스트
  - ai_analysis/findings_analyzer.py : 채점 결과 요약 호출
  - ai_analysis/raw_log_analyzer.py  : 원본 로그 분석 호출

제공자를 추가할 때 손댈 곳은 이 파일 하나다. 표에 없는 제공자를 UI에만 추가하면
분석 쪽 핸들러가 없어서 그 키는 조용히 건너뛰어진다 — 그래서 목록을 공유한다.

필드
  id / label   : 내부 식별자 / 화면 표시 이름
  models       : 모델 선택 목록(첫 항목이 기본값). 제공자들이 수시로 갱신하므로
                 새 모델을 쓰려면 여기 한 줄 추가하면 된다.
  wire         : 실제 HTTP 호출 형식. 분석 쪽이 이걸로 핸들러를 고른다.
                   "anthropic"     — Anthropic Messages API
                   "gemini"        — Google generativeLanguage
                   "openai_compat" — OpenAI Chat Completions 호환(대부분이 여기 해당)
  chat_endpoint: wire="openai_compat"일 때 호출할 주소. OpenAI 핸들러가 그대로 쓴다.
  test         : 연결 테스트용 {url, auth}. auth는 cloud_ai_api._build_test_request가 해석.
  batch_chars / max_tokens : 그 제공자 모델의 기본 배치 크기
                 (모델별로 다르게 두고 싶으면 LocalAiApiMixin.MODEL_PARAM_DEFAULTS가 우선)
"""

CLOUD_PROVIDER_TYPES = [
    {
        "id": "anthropic",
        "label": "Anthropic (Claude)",
        "models": [
            "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5",
            "claude-opus-4-8", "claude-sonnet-4-6",
        ],
        "wire": "anthropic",
        "chat_endpoint": "https://api.anthropic.com/v1/messages",
        "test": {"url": "https://api.anthropic.com/v1/models", "auth": "anthropic"},
        "batch_chars": 20000, "max_tokens": 1500,
    },
    {
        "id": "openai",
        "label": "OpenAI (GPT)",
        "models": [
            "gpt-5.1", "gpt-5.1-mini", "gpt-5", "gpt-5-mini",
            "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o4-mini",
        ],
        "wire": "openai_compat",
        "chat_endpoint": "https://api.openai.com/v1/chat/completions",
        "test": {"url": "https://api.openai.com/v1/models", "auth": "bearer"},
        "batch_chars": 30000, "max_tokens": 1500,
    },
    {
        "id": "gemini",
        "label": "Google Gemini",
        "models": [
            "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
            "gemini-2.0-flash", "gemini-2.0-flash-lite",
        ],
        "wire": "gemini",
        "chat_endpoint": "",   # gemini 핸들러가 모델명으로 주소를 조립한다
        "test": {"url": "https://generativelanguage.googleapis.com/v1beta/models",
                  "auth": "google_key_param"},
        "batch_chars": 40000, "max_tokens": 1800,
    },
    {
        "id": "nvidia_nim",
        "label": "NVIDIA NIM",
        "models": [
            "meta/llama-3.3-70b-instruct",
            "nvidia/llama-3.3-nemotron-super-49b-v1",
            "meta/llama-3.1-405b-instruct",
            "deepseek-ai/deepseek-r1",
            "qwen/qwen2.5-coder-32b-instruct",
            "mistralai/mistral-large-2-instruct",
        ],
        "wire": "openai_compat",
        "chat_endpoint": "https://integrate.api.nvidia.com/v1/chat/completions",
        "test": {"url": "https://integrate.api.nvidia.com/v1/models", "auth": "bearer"},
        "batch_chars": 25000, "max_tokens": 1500,
    },
    {
        "id": "xai",
        "label": "xAI (Grok)",
        "models": ["grok-4", "grok-3", "grok-3-mini"],
        "wire": "openai_compat",
        "chat_endpoint": "https://api.x.ai/v1/chat/completions",
        "test": {"url": "https://api.x.ai/v1/models", "auth": "bearer"},
        "batch_chars": 30000, "max_tokens": 1500,
    },
    {
        "id": "mistral",
        "label": "Mistral AI",
        "models": [
            "mistral-large-latest", "mistral-medium-latest",
            "mistral-small-latest", "magistral-medium-latest",
        ],
        "wire": "openai_compat",
        "chat_endpoint": "https://api.mistral.ai/v1/chat/completions",
        "test": {"url": "https://api.mistral.ai/v1/models", "auth": "bearer"},
        "batch_chars": 25000, "max_tokens": 1500,
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "wire": "openai_compat",
        "chat_endpoint": "https://api.deepseek.com/chat/completions",
        "test": {"url": "https://api.deepseek.com/models", "auth": "bearer"},
        "batch_chars": 25000, "max_tokens": 1500,
    },
    {
        "id": "groq",
        "label": "Groq",
        "models": [
            "llama-3.3-70b-versatile", "llama-3.1-8b-instant",
            "qwen/qwen3-32b", "moonshotai/kimi-k2-instruct",
        ],
        "wire": "openai_compat",
        "chat_endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "test": {"url": "https://api.groq.com/openai/v1/models", "auth": "bearer"},
        "batch_chars": 20000, "max_tokens": 1200,
    },
    {
        "id": "perplexity",
        "label": "Perplexity",
        "models": ["sonar-pro", "sonar", "sonar-reasoning-pro"],
        "wire": "openai_compat",
        "chat_endpoint": "https://api.perplexity.ai/chat/completions",
        "test": {"url": "https://api.perplexity.ai/chat/completions", "auth": "bearer"},
        "batch_chars": 20000, "max_tokens": 1200,
    },
    {
        "id": "upstage",
        "label": "Upstage (Solar, 한국어)",
        "models": ["solar-pro2", "solar-pro", "solar-mini"],
        "wire": "openai_compat",
        "chat_endpoint": "https://api.upstage.ai/v1/chat/completions",
        "test": {"url": "https://api.upstage.ai/v1/models", "auth": "bearer"},
        "batch_chars": 20000, "max_tokens": 1200,
    },
    # 목록에 없는 서비스를 직접 등록하는 칸 — 항상 목록의 맨 마지막에 둔다.
    # OpenAI 호환 엔드포인트면 대부분 여기로 붙는다(OpenRouter, Together, Fireworks,
    # 사내 게이트웨이, vLLM/LM Studio 등). 주소와 모델명은 사용자가 직접 입력하므로
    # chat_endpoint/models를 비워 두고, 저장된 항목의 endpoint 값을 그대로 쓴다.
    {
        "id": "custom",
        "label": "직접입력 (OpenAI 호환)",
        "models": [],
        "wire": "openai_compat",
        "chat_endpoint": "",
        "test": {"url": "", "auth": "bearer"},
        "batch_chars": 20000, "max_tokens": 1500,
        "custom": True,
        "needs_endpoint": True,
    },
]

# 모델 목록 드롭다운 맨 끝의 '직접입력' 항목이 쓰는 표식 — JS와 값이 같아야 한다.
CUSTOM_MODEL_SENTINEL = "__custom__"

_BY_ID = {p["id"]: p for p in CLOUD_PROVIDER_TYPES}


def get(provider_id):
    """provider id -> 표 항목(없으면 None)."""
    return _BY_ID.get(provider_id)


def wire_of(provider_id):
    """provider id -> HTTP 호출 형식('anthropic' / 'gemini' / 'openai_compat').
    표에 없는(=예전 버전에서 저장된) provider는 anthropic으로 본다 — 예전 기본값이었다."""
    p = _BY_ID.get(provider_id)
    return p["wire"] if p else "anthropic"


def chat_endpoint_of(provider_id):
    """provider id -> 채팅 호출 주소(모르면 빈 문자열 — 핸들러가 자기 기본값을 쓴다)."""
    p = _BY_ID.get(provider_id)
    return p.get("chat_endpoint", "") if p else ""


def default_model(provider_id):
    p = _BY_ID.get(provider_id)
    return (p.get("models") or [""])[0] if p else ""


def models_of(provider_id):
    p = _BY_ID.get(provider_id)
    return list(p.get("models") or []) if p else []


def needs_endpoint(provider_id):
    """주소를 사용자가 직접 넣어야 하는 제공자인지(=직접입력 항목)."""
    p = _BY_ID.get(provider_id)
    return bool(p.get("needs_endpoint")) if p else False
