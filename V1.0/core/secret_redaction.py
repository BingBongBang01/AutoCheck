"""로그·화면으로 나가는 문자열에서 API 키·토큰을 지운다.

AI 제공자 호출이 실패하면 오류 출력에 요청 URL 이 섞인다. Gemini 는 예전에 키를 URL 쿼리
(?key=...)로 받았고, core/app_logger 가 print 를 로그 파일·UI 로그로 캡처하므로 429 한 번에 키가
'전체 로그 내보내기'로 나갔다. app_logger 는 기록하는 모든 줄을 이 함수로 거른다 — 어느 호출부가
실수로 키를 찍어도 파일에는 남지 않게 하는 마지막 방어선이다.
"""
import re

_REDACTED = "[REDACTED]"

# 문맥 없이 단독으로 찍혀도 알아볼 수 있는 제공자별 키 모양
# (Anthropic/OpenAI/DeepSeek sk-, Google AIza, NVIDIA nvapi-, xAI xai-, Groq gsk_, Perplexity pplx-).
# tools/bundle_secret_check 도 EXE 에 담길 파일을 이 모양으로 검사한다.
PROVIDER_KEY_RE = re.compile(r"\b(?:sk-(?:ant-)?[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{30,}|nvapi-[A-Za-z0-9_-]{20,}"
                             r"|xai-[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{20,}|pplx-[A-Za-z0-9]{20,})")

_PATTERNS = [
    # URL 쿼리의 키/토큰 파라미터: ...?key=AIza...&alt=json, Teams 워크플로 웹훅의 ...&sig=...
    (re.compile(r"([?&](?:key|api_key|apikey|access_token|token|sig)=)[^&\s\"'#]+", re.IGNORECASE),
     r"\1" + _REDACTED),
    # 경로가 곧 토큰인 웹훅 주소: Slack hooks.slack.com/services/T…/B…/…, 예전 Teams 커넥터 …/webhookb2/…
    (re.compile(r"(hooks\.slack\.com/(?:services|workflows|triggers)/)[^\s\"'<>]+", re.IGNORECASE), r"\1" + _REDACTED),
    (re.compile(r"(\.webhook\.office\.com/webhookb2/)[^\s\"'<>]+", re.IGNORECASE), r"\1" + _REDACTED),
    # Authorization: Bearer <token>
    (re.compile(r"(\bBearer[ \t]+)[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE), r"\1" + _REDACTED),
    # 헤더/설정 형태: x-api-key: ..., 'x-goog-api-key': '...', "api_key": "..."
    (re.compile(r"((?:x-api-key|x-goog-api-key|api[_-]?key)[\"']?[ \t]*[:=][ \t]*[\"']?)[^\s\"',}]+",
                re.IGNORECASE), r"\1" + _REDACTED),
    (PROVIDER_KEY_RE, _REDACTED),
]


def redact_secrets(text):
    """문자열 안의 API 키·토큰을 [REDACTED] 로 바꾼다. 키가 없으면 그대로 돌려준다."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# 장비 목록·프로젝트 YAML 에서 계정 정보가 들어가는 키 — engine/device_inventory_core 의 DEVICE_FIELDS·
# DEFAULT_PROJECT_DEFAULTS, ip_allocation.yaml 의 default_credentials/allocations.
SECRET_FIELD_NAMES = frozenset({"password", "default_password", "key_content", "key_passphrase",
                                "enable_secret", "default_enable_secret"})


def scrub_secret_fields(data):
    """dict/list 를 따라 내려가며 SECRET_FIELD_NAMES 키의 값을 비운 사본과, 비운 값의 개수를 돌려준다.
    원본은 바꾸지 않는다. 이미 비어 있는 값은 세지 않는다."""
    if isinstance(data, dict):
        out, count = {}, 0
        for key, value in data.items():
            if key in SECRET_FIELD_NAMES and value not in (None, ""):
                out[key] = ""
                count += 1
            else:
                out[key], inner = scrub_secret_fields(value)
                count += inner
        return out, count
    if isinstance(data, list):
        out, count = [], 0
        for item in data:
            scrubbed, inner = scrub_secret_fields(item)
            out.append(scrubbed)
            count += inner
        return out, count
    return data, 0
