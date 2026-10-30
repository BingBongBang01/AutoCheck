"""클라우드 AI 제공자 표 — 제공자에 관한 유일한 정본.

여기 한 곳을 읽는 쪽이 셋 있다:
  - api/cloud_ai_api.py  : 환경 설정 화면의 제공자 드롭다운/모델 목록/연결 테스트
  - ai_analysis/raw_log_analyzer.py  : 원본 로그 분석 호출
  - ai_analysis/transmission.py      : 전송 미리보기·승인 이력의 받는 곳(호스트·모델)

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
  min_max_tokens: (선택) 보낼 max_tokens 의 하한 — 저장된 값이 이보다 작아도 이만큼은 보낸다
  timeout_sec  : (선택) 분석 요청 한 건의 제한 시간(초). 없으면 30
"""
from urllib.parse import urlparse

CLOUD_PROVIDER_TYPES = [
    {
        "id": "anthropic",
        "label": "Anthropic (Claude)",
        # 첫 항목이 기본값이다. 현행 세대(Opus 5.5·Sonnet 5.5·Haiku 5.5·Fable 5.1) 뒤에 예전 세대를 남겨
        # 둔다 — 이미 저장된 선택이 목록에서 사라져 '직접입력'으로 보이지 않게.
        "models": [
            "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1",
            "claude-opus-5", "claude-sonnet-5", "claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5",
        ],
        "wire": "anthropic",
        "chat_endpoint": "https://api.anthropic.com/v1/messages",
        "test": {"url": "https://api.anthropic.com/v1/models", "auth": "anthropic"},
        # 현행 모델은 thinking 이 항상 켜져 있고(끌 수 없다) 생각에 쓴 토큰도 max_tokens 에 들어간다. 예전처럼
        # 답 길이만 보고 1500 으로 잡으면 생각하다 한도에 닿아 답이 잘리거나 비어서 온다 — 그래서 기본값을
        # 크게 잡고, 예전에 저장된 작은 값도 하한까지 올려서 보낸다. 한도일 뿐 그만큼 쓰는 것은 아니다.
        # 생각하는 만큼 응답이 늦어질 수 있어 요청 제한 시간도 길게 둔다(스트리밍 없이 응답 전체를 기다린다).
        "batch_chars": 20000, "max_tokens": 16000, "min_max_tokens": 16000, "timeout_sec": 600,
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
        # 키는 URL 쿼리(?key=)가 아니라 헤더로 보낸다 — URL 은 오류 메시지·로그에 그대로 찍힌다.
        "test": {"url": "https://generativelanguage.googleapis.com/v1beta/models",
                  "auth": "google_api_key_header"},
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

# Anthropic Messages API 버전(anthropic-version 헤더) — 분석 호출과 연결 테스트가 같은 값을 쓴다.
# 2026-10-21 판에서 바뀐 것(temperature/top_p/top_k 를 보내면 400, assistant 메시지의 document 블록·user
# 메시지의 citations 금지, Admin API 응답 필드)은 이 앱의 요청(user 텍스트 한 개)에 해당하지 않는다.
ANTHROPIC_VERSION = "2026-10-21"

# 안전 분류기가 요청을 거절하면 그 거절 종류에 맞게 Anthropic 이 권장하는 다른 Claude 모델이 서버에서 같은
# 요청을 다시 처리하게 한다(fallbacks: "default"). 같은 Anthropic API 안의 일이라 데이터가 가는 곳은 같다.
# 이 기능을 받는 모델에만 붙인다 — Haiku 5.5 는 대체 모델이 없고, 그 밖의 모델에는 보내지 않는다.
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"
ANTHROPIC_FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})

# Gemini 는 주소를 모델명으로 조립하므로 표에 chat_endpoint 가 없다 — 받는 곳(호스트)을 보여 줄 때 쓴다.
GEMINI_HOST = "generativelanguage.googleapis.com"

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


def max_tokens_for(provider_id, value=None):
    """저장된 max_tokens -> 실제로 보낼 값. 비었거나 숫자가 아니면 제공자 기본값, 제공자 하한
    (min_max_tokens)보다 작으면 하한."""
    p = _BY_ID.get(provider_id) or {}
    try:
        tokens = int(value)
    except (TypeError, ValueError):
        tokens = 0
    if tokens <= 0:
        tokens = p.get("max_tokens", 1500)
    return max(tokens, p.get("min_max_tokens", 0))


def timeout_of(provider_id):
    """provider id -> 분석 요청 한 건의 제한 시간(초)."""
    p = _BY_ID.get(provider_id) or {}
    return p.get("timeout_sec", 30)


def host_of(entry):
    """저장된 클라우드 항목의 분석 요청이 가는 호스트 — 전송 미리보기와 승인 이력에 적는다."""
    provider = entry.get("provider") or entry.get("type") or "anthropic"
    url = entry.get("endpoint") or chat_endpoint_of(provider)
    if not url:
        return GEMINI_HOST if wire_of(provider) == "gemini" else ""
    return urlparse(url).hostname or ""


def is_loopback_host(host):
    """이 PC 안을 가리키는 호스트인지(localhost / 127.x / ::1)."""
    host = (host or "").lower()
    return host in ("localhost", "::1") or host.startswith("127.")


def endpoint_problem(endpoint):
    """직접입력 엔드포인트 검사 -> 문제 문구(괜찮으면 None).

    점검 로그와 API 키가 오가는 주소라 https 만 허용한다. 같은 PC 안의 프록시(http://localhost)만
    예외다 — 그 경우 데이터가 이 PC 를 떠나지 않는다."""
    parsed = urlparse((endpoint or "").strip())
    if parsed.scheme == "https" and parsed.hostname:
        return None
    if parsed.scheme == "http" and is_loopback_host(parsed.hostname):
        return None
    return ("엔드포인트는 https:// 로 시작해야 합니다(같은 PC 의 http://localhost 만 예외) — "
            "API 키와 점검 로그가 암호화되지 않은 채 오가면 안 됩니다.")
