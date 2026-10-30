"""원시 로그(raw .txt) 텍스트 분석 경로 — 청크 단위로 API/로컬 NPU에 전달.

이 PC 밖으로 나가는 요청(클라우드 제공자, 또는 다른 호스트를 가리키는 '로컬' 엔드포인트)은 보내기
직전에 engine.log_masking.mask_for_external_ai 로 비밀값·주소·장비명을 강제로 가린다. 예전에는
마스킹 없이 보내서 running-config 구간의 커뮤니티·키·IP·호스트명이 외부 제공자로 그대로 나갔다.
마스킹을 호출부가 아니라 build_prompts 안에서 하는 이유: 호출부마다 기억해야 하면 언젠가 빠진다.

보낼 내용을 만드는 일(build_prompts)과 보내는 일(send_prompts)을 나눠 둔다 — 이 PC 밖으로 나가는 분석은
사용자가 미리보기에서 보낼 내용을 확인·승인한 뒤 바로 그 내용만 보낸다(ai_analysis/transmission.py,
IMPROVEMENT_PLAN 4-2). send_prompts 는 build_prompts 가 만든 PreparedRequests 만 받고, 가리지 않고 만든
내용을 이 PC 밖으로 보내려 하면 거부한다. analyze_raw_log_text 는 둘을 이어 부른다.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from core import cloud_providers
from engine.log_masking import mask_for_external_ai

# 프롬프트는 두 벌이다 — 프로파일의 '가상환경' 옵션이 어느 쪽을 쓸지 고른다.
# 예전에는 가상(vEOS-lab) 프롬프트만 있어서, 실기 장비를 점검하는 프로파일에서도 AI가
# "하드웨어/전원/온도 이상은 가상 플랫폼 제약이니 무시하라"는 지시를 받았다 — 실제 PSU 고장이
# 보고되지 않을 수 있는 지시라, 물리 장비용 프롬프트를 따로 둔다.
_VIRTUAL_LOG_PROMPT_PREFIX = (
    "You are a Senior Network Engineer analyzing raw CLI logs from an Arista vEOS-lab virtual platform.\n"
    "CRITICAL CONTEXT & VIRTUAL PLATFORM CONSTRAINTS:\n"
    "- Target Device: Arista vEOS-lab (Virtual EOS lab environment).\n"
    "- IGNORE EXPECTED VIRTUAL LIMITATIONS: Do NOT report hardware errors or unavailable commands caused by running in a virtual environment. Specifically ignore:\n"
    "  * Commands returning 'Unavailable command' or 'Not supported on this platform' (e.g., 'show module', 'show environment power/cooling/temperature').\n"
    "  * Environmental/Hardware status output showing 'No power supplies connected', missing fans, or zero temperature sensors.\n"
    "  * Lack of transceiver/DOM diagnostic monitoring.\n"
    "- FOCUS STRICTLY ON OPERATIONAL FAILURES: Concentrate exclusively on active control-plane, data-plane, and protocol errors:\n"
    "  * System/Reload causes (e.g., crash dumps, kernel panic, unexpected reboot).\n"
    "  * Interface and link states (e.g., interface DOWN, errdisabled, link flap).\n"
    "  * MLAG operational failures (e.g., MLAG state inactive/disabled, peer-link down).\n"
    "  * Routing and Overlay failures (e.g., BGP/EVPN neighbor Idle/Active, OSPF adjacency failure, VXLAN tunnel down).\n"
    "  * Spanning Tree Protocol (STP) topology changes, root bridge shifts, or port blocking anomalies.\n"
    "Output ONLY the extracted operational problem lines and a concise engineering summary. "
    "Do not use markdown code block wrappers (```), output plain text so it can be saved directly as a log file.\n\n"
)

_PHYSICAL_LOG_PROMPT_PREFIX = (
    "You are a Senior Network Engineer analyzing raw CLI logs from production network switches/routers.\n"
    "CONTEXT:\n"
    "- Target Device: physical hardware in a live/production network.\n"
    "- REPORT HARDWARE FAULTS: power supply failures, fan failures/speed anomalies, temperature alarms,"
    " module/linecard failures, and transceiver(DOM) problems are real findings — never dismiss them.\n"
    "- Also report operational failures:\n"
    "  * System/Reload causes (e.g., crash dumps, kernel panic, unexpected reboot).\n"
    "  * Interface and link states (e.g., interface DOWN, errdisabled, link flap).\n"
    "  * MLAG/port-channel operational failures (e.g., MLAG state inactive/disabled, peer-link down).\n"
    "  * Routing and Overlay failures (e.g., BGP/EVPN neighbor Idle/Active, OSPF adjacency failure, VXLAN tunnel down).\n"
    "  * Spanning Tree Protocol (STP) topology changes, root bridge shifts, or port blocking anomalies.\n"
    "- Do NOT invent problems: if a command output is simply missing, say it was not collected.\n"
    "Output ONLY the extracted operational problem lines and a concise engineering summary. "
    "Do not use markdown code block wrappers (```), output plain text so it can be saved directly as a log file.\n\n"
)

# 요청 한도(429)·서버 문제에 걸리면 키를 바꿔 가며 다시 보내고, 쓸 수 있는 키가 모두 막혔으면 쉬었다가 다시 돈다.
_RETRY_ROUNDS = 3
_RETRY_WAIT_SEC = 22
# 키를 바꿔 다시 보낼 만한 응답 — 요청 한도(429)와 제공자 서버 문제(5xx, Anthropic 과부하 529).
_RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504, 529})
# 이 키로는 안 되는 응답 — 이번 분석의 남은 요청에서 그 키를 뺀다.
_KEY_REJECTED_CODES = frozenset({401, 403})


class AIRefusal(Exception):
    """제공자의 안전 분류기가 요청을 거절했다(HTTP 200 에 stop_reason="refusal")."""

    def __init__(self, category=None):
        super().__init__(category or "")
        self.category = category


@dataclass(frozen=True)
class PreparedRequests:
    """build_prompts 가 만든 '보낼 내용'. masked: 이 PC 밖으로 보내도 되게 가린 것인지."""
    prompts: tuple
    masked: bool


@dataclass
class SendResult:
    """send_prompts 결과 — 파트별 결과 문자열과 실제로 보낸 양(승인 이력에 남긴다)."""
    texts: list = field(default_factory=list)
    sent: int = 0          # 응답을 받은 요청 수
    failed: int = 0        # 오류·거절로 결과를 받지 못한 파트 수
    keys_used: list = field(default_factory=list)   # 응답을 받은 항목 이름(처음 쓴 순서)
    stopped: bool = False  # 취소로 남은 파트를 보내지 않았다(작업 센터 — IMPROVEMENT_PLAN 4-9)

    @property
    def text(self):
        return "\n\n".join(self.texts)


def prompt_prefix(is_virtual: bool) -> str:
    """프로파일의 '가상환경' 옵션에 맞는 프롬프트 머리말."""
    return _VIRTUAL_LOG_PROMPT_PREFIX if is_virtual else _PHYSICAL_LOG_PROMPT_PREFIX


def _urlopen_with_context(req, timeout, label):
    """urlopen 실패 시(특히 404) 어떤 endpoint/모델을 호출하다 실패했는지, 서버가 어떤 응답
    본문을 돌려줬는지까지 포함해서 예외를 다시 던진다 — '연결 테스트는 되는데 분석은 404'처럼
    원인을 알기 어려운 상황에서 로그만 보고도 원인을 바로 알 수 있게 하기 위함.
    응답 본문은 예외의 body_text 에도 남긴다 — 호출부가 오류 내용을 보고 다르게 처리할 수 있게."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        exc.body_text = body
        # 쿼리스트링은 찍지 않는다 — 키가 들어 있을 수 있다(로그 쪽 필터는 마지막 방어선일 뿐).
        endpoint = req.full_url.split("?", 1)[0]
        print(f"[AI 라우터] {label} 호출 실패: {endpoint} -> HTTP {exc.code} {exc.reason} | 응답: {body}")
        raise


def _post_json(endpoint, payload, headers, timeout, label):
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers)
    with _urlopen_with_context(req, timeout, label) as resp:
        return json.loads(resp.read())


def _call_api_raw_text(prompt, api_cfg):
    """Anthropic Messages API. 현행 모델은 thinking 이 항상 켜져 있어 응답 앞에 thinking 블록이 올 수 있다 —
    순서가 아니라 type 이 text 인 블록만 모은다. 안전 분류기가 거절하면(stop_reason="refusal") AIRefusal,
    출력 한도에 닿았으면 결과 끝에 그렇다고 적는다(생각하다 한도에 닿으면 답이 비어서 온다)."""
    api_key = api_cfg.get("api_key") or os.environ.get(api_cfg.get("api_key_env", "ANTHROPIC_API_KEY"))
    if not api_key:
        raise RuntimeError("API 키가 설정되지 않음")
    endpoint = api_cfg.get("endpoint") or cloud_providers.chat_endpoint_of("anthropic")
    model = api_cfg.get("model") or cloud_providers.default_model("anthropic")
    max_tokens = cloud_providers.max_tokens_for("anthropic", api_cfg.get("max_tokens"))
    payload = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": cloud_providers.ANTHROPIC_VERSION,
    }
    fallback = model in cloud_providers.ANTHROPIC_FALLBACK_MODELS
    if fallback:
        payload["fallbacks"] = "default"
        headers["anthropic-beta"] = cloud_providers.ANTHROPIC_FALLBACK_BETA
    timeout = cloud_providers.timeout_of("anthropic")
    label = f"anthropic(model={model})"
    try:
        data = _post_json(endpoint, payload, headers, timeout, label)
    except urllib.error.HTTPError as exc:
        # 대체 모델 기능은 베타라 계정에 따라 400 으로 거부될 수 있다 — 그러면 그 기능 없이 한 번 더 보낸다.
        body = getattr(exc, "body_text", "")
        if not (fallback and exc.code == 400 and ("anthropic-beta" in body or "fallback" in body)):
            raise
        payload.pop("fallbacks")
        headers.pop("anthropic-beta")
        data = _post_json(endpoint, payload, headers, timeout, label)
    if data.get("stop_reason") == "refusal":
        raise AIRefusal((data.get("stop_details") or {}).get("category"))
    text = "".join(block.get("text", "") for block in data.get("content") or [] if block.get("type") == "text")
    if data.get("stop_reason") == "max_tokens":
        text += (f"\n[주의] 출력 한도(max_tokens={max_tokens})에 닿아 이 파트의 결과가 잘렸을 수 있습니다 — "
                 "설정 탭에서 max_tokens 를 늘리세요.")
    return text


def _call_gemini_raw_text(prompt, api_cfg):
    api_key = api_cfg.get("api_key") or os.environ.get(api_cfg.get("api_key_env", "GEMINI_API_KEY"))
    if not api_key:
        raise RuntimeError("Gemini API 키가 설정되지 않음")
    model = api_cfg.get("model") or cloud_providers.default_model("gemini")
    # 키는 URL 쿼리(?key=)가 아니라 헤더로 — URL 은 오류 메시지와 로그에 그대로 찍힌다.
    endpoint = f"https://{cloud_providers.GEMINI_HOST}/v1beta/models/{model}:generateContent"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, headers={
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    })
    with _urlopen_with_context(req, cloud_providers.timeout_of("gemini"), f"gemini(model={model})") as resp:
        data = json.loads(resp.read())
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_openai_raw_text(prompt, api_cfg):
    api_key = api_cfg.get("api_key") or os.environ.get(api_cfg.get("api_key_env", "OPENAI_API_KEY"))
    if not api_key:
        raise RuntimeError("OpenAI API 키가 설정되지 않음")
    # OpenAI 호환 제공자 전부가 이 함수를 공유한다 — 주소만 제공자 표에서 가져온다.
    provider = api_cfg.get("provider")
    endpoint = (api_cfg.get("endpoint")
                or cloud_providers.chat_endpoint_of(provider)
                or "https://api.openai.com/v1/chat/completions")
    model = api_cfg.get("model") or "gpt-4o-mini"
    body = json.dumps({
        "model": model,
        "max_tokens": api_cfg.get("max_tokens", 1500),
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    with _urlopen_with_context(req, cloud_providers.timeout_of(provider), f"openai(model={model})") as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def _call_local_npu_raw_text(prompt, api_cfg):
    """lemonade-server는 OpenAI 호환 서버로 /generate가 아니라 /api/v1/chat/completions를
    사용한다(과거 코드가 존재하지 않는 /generate를 호출해 404가 발생했었음)."""
    endpoint = (api_cfg.get("endpoint") or "http://localhost:13305").rstrip("/")
    model = api_cfg.get("model", "")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if "temperature" in api_cfg:
        payload["temperature"] = api_cfg["temperature"]
    if "max_tokens" in api_cfg:
        payload["max_tokens"] = api_cfg["max_tokens"]
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{endpoint}/api/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"},
    )
    with _urlopen_with_context(req, 60, f"local_npu(model={model})") as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def leaves_this_pc(mode, api_cfg):
    """요청이 이 PC 밖으로 나가는가 — 클라우드는 항상, 로컬은 엔드포인트가 이 PC(loopback)가 아닐 때."""
    if mode != "local":
        return True
    endpoint = (api_cfg or {}).get("endpoint") or "http://localhost:13305"
    return not cloud_providers.is_loopback_host(urllib.parse.urlparse(endpoint).hostname)


def _provider_of(api_cfg):
    return api_cfg.get("provider") or api_cfg.get("type", "anthropic")


def request_settings(mode, api_cfg):
    """(batch_chars, max_tokens, 파트 사이 쉬는 초) — 로컬은 모델명으로, 클라우드는 제공자 표와 저장된 항목 값으로."""
    if mode == "local":
        model_name = (api_cfg.get("model") or "").lower()
        if "gemma" in model_name:
            batch_cfg = {"batch_chars": 2500, "max_tokens": 800}
        elif "deepseek" in model_name or "r1" in model_name:
            batch_cfg = {"batch_chars": 5000, "max_tokens": 3000}
        elif "qwen" in model_name:
            batch_cfg = {"batch_chars": 7000, "max_tokens": 1500}
        elif "llama" in model_name:
            batch_cfg = {"batch_chars": 8000, "max_tokens": 1500}
        elif "phi" in model_name:
            batch_cfg = {"batch_chars": 4500, "max_tokens": 1200}
        elif "mistral" in model_name:
            batch_cfg = {"batch_chars": 6000, "max_tokens": 1200}
        else:
            batch_cfg = {"batch_chars": 3000, "max_tokens": 1000}
        return batch_cfg["batch_chars"], batch_cfg["max_tokens"], 0

    # 클라우드 제공자별 기본 수치는 제공자 표에서 읽으므로 제공자를 추가할 때 이 분기를 늘리지 않는다.
    # 설정 화면에 저장된 항목 값(batch_chars/max_tokens)이 먼저다.
    provider_type = _provider_of(api_cfg)
    spec = cloud_providers.get(provider_type) or {}
    batch_chars = api_cfg.get("batch_chars", spec.get("batch_chars", 20000))
    max_tokens = cloud_providers.max_tokens_for(provider_type, api_cfg.get("max_tokens"))
    # RPM 방어 지연: Gemini가 분당 요청 제한이 가장 빡빡해서 더 길게 쉰다.
    return batch_chars, max_tokens, (4 if provider_type == "gemini" else 2)


def build_prompts(text, mode, api_cfg, *, is_virtual=False, device_names=()):
    """보낼 프롬프트들을 만든다 — 이 PC 밖으로 나가는 설정이면 먼저 가리고, 배치 크기로 나눠 머리말을 붙인다.

    is_virtual: 활성 프로파일이 '가상환경'이면 True — 가상 플랫폼 제약(PSU/FAN/센서/모듈
    미지원)을 무시하라는 프롬프트를 쓴다. 기본값은 물리 장비(하드웨어 고장을 그대로 보고).
    device_names: 이 로그의 장비명(파일명에서 얻은 것). 외부로 나가는 요청이면 함께 가린다.
    """
    api_cfg = api_cfg or {}
    masked = leaves_this_pc(mode, api_cfg)
    if masked:
        text = mask_for_external_ai(text, device_names)
    try:
        chunk_size = max(1, int(request_settings(mode, api_cfg)[0]))
    except (TypeError, ValueError):
        chunk_size = 20000
    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
    prompts = []
    for i, chunk in enumerate(chunks):
        prefix = f"(전체 {len(chunks)} 파트 중 {i + 1}번째 파트)\n" if len(chunks) > 1 else ""
        prompts.append(prompt_prefix(is_virtual) + prefix + chunk)
    return PreparedRequests(tuple(prompts), masked)


class KeyRing:
    """분석 한 번 동안 돌려 쓰는 설정(키)들. 앞의 것부터 쓰고 —
      - 키가 거부되면(401/403) 이번 분석에서 그 키를 뺀다,
      - 요청 한도·서버 문제(429/5xx)면 그 키를 맨 뒤로 보내고 다음 키로 다시 보낸다.
    성공한 키는 앞에 그대로 두어 다음 요청도 그 키로 보낸다. 파일이 바뀌어도 같은 KeyRing 을 이어 쓴다 —
    앞 파일에서 거부된 키로 다시 보내지 않는다. 어떤 설정을 넣을지(같은 곳으로 가는 키만)는 부르는 쪽이 정한다."""

    def __init__(self, configs, mode, should_stop=None):
        self.mode = mode
        # should_stop() 이 참이면 남은 파트를 보내지 않는다 — 작업 센터의 취소. 보내는 중인 요청 하나는 끝까지
        # 기다린다(HTTP 요청을 중간에 끊을 수 없다). 기다리는 시간(재시도 대기 22초·요청 사이 쉼)은 바로 끊는다.
        self.should_stop = should_stop
        self.live = []
        for cfg in configs:
            if cfg is None:
                continue
            cfg = dict(cfg)
            cfg["max_tokens"] = request_settings(mode, cfg)[1]
            self.live.append(cfg)
        self.pause_sec = request_settings(mode, self.live[0])[2] if self.live else 0

    def leaves_this_pc(self):
        return any(leaves_this_pc(self.mode, cfg) for cfg in self.live)


def _stopping(ring):
    return bool(ring.should_stop is not None and ring.should_stop())


def _pause(ring, seconds):
    """seconds 동안 쉬되 취소되면 바로 깬다 -> 취소됐으면 True."""
    deadline = time.monotonic() + seconds
    while True:
        if _stopping(ring):
            return True
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(0.25, left))


def _entry_name(cfg):
    return cfg.get("name") or cfg.get("provider") or cfg.get("model") or "설정"


def _dispatch(prompt, mode, api_cfg):
    if mode == "local":
        return _call_local_npu_raw_text(prompt, api_cfg)
    if mode != "cloud":
        raise ValueError(f"알 수 없는 모드: {mode}")
    # 제공자 id가 아니라 호출 형식(wire)으로 고른다 — OpenAI 호환 제공자
    # (NVIDIA NIM/xAI/Mistral/DeepSeek/Groq/Perplexity/Upstage)가 늘어도 그대로 동작.
    wire = cloud_providers.wire_of(_provider_of(api_cfg))
    if wire == "gemini":
        return _call_gemini_raw_text(prompt, api_cfg)
    if wire == "openai_compat":
        return _call_openai_raw_text(prompt, api_cfg)
    return _call_api_raw_text(prompt, api_cfg)


def _send_part(prompt, mode, ring, part, result):
    """파트 하나를 보낸다 -> 결과 문자열. 결과를 못 받으면 '[AI 분석 오류 - 파트 N] …' 를 돌려준다 —
    예전에는 재시도를 다 쓴 파트가 아무 표시 없이 빠져서, 결과만 보면 그 구간에 이상이 없었던 것처럼 읽혔다."""
    live = ring.live
    if not live:
        result.failed += 1
        return f"[AI 분석 오류 - 파트 {part}] 쓸 수 있는 API 키가 없어 보내지 않았습니다(모두 거부됨)."
    last_code = None
    for round_no in range(_RETRY_ROUNDS):
        for _ in range(len(live)):
            cfg = live[0]
            try:
                text = _dispatch(prompt, mode, cfg)
            except urllib.error.HTTPError as exc:
                last_code = exc.code
                if exc.code in _KEY_REJECTED_CODES:
                    live.pop(0)
                    print(f"[AI 분석] '{_entry_name(cfg)}' 키가 거부됨(HTTP {exc.code}) — 이번 분석에서는 이 키를 쓰지 않습니다.")
                    if not live:
                        result.failed += 1
                        return (f"[AI 분석 오류 - 파트 {part}] 인증 실패(HTTP {exc.code}) — 설정 탭에서 API 키를 "
                                "확인하세요.")
                    continue
                if exc.code in _RETRYABLE_CODES:
                    live.append(live.pop(0))
                    continue
                result.failed += 1
                return f"[AI 분석 오류 - 파트 {part}] HTTP 오류: {exc.code}"
            except AIRefusal as exc:
                # 같은 내용은 키를 바꿔도 같은 답이 온다 — 다시 보내지 않는다.
                result.failed += 1
                kind = f"(분류: {exc.category})" if exc.category else ""
                return (f"[AI 분석 거절 - 파트 {part}] 제공자의 안전 분류기가 이 파트를 거절했습니다{kind} — "
                        "이 파트는 분석하지 못했습니다.")
            except Exception as exc:
                result.failed += 1
                return f"[AI 분석 오류 - 파트 {part}] {exc}"
            result.sent += 1
            name = _entry_name(cfg)
            if name not in result.keys_used:
                result.keys_used.append(name)
            return text
        if round_no + 1 < _RETRY_ROUNDS:
            print(f"[AI 분석] HTTP {last_code} — 쓸 수 있는 키가 모두 막혀 {_RETRY_WAIT_SEC}초 대기 후 재시도 "
                  f"(시도 {round_no + 1}/{_RETRY_ROUNDS})...")
            if _pause(ring, _RETRY_WAIT_SEC):
                result.stopped = True
                return f"[AI 분석 중단 - 파트 {part}] 재시도를 기다리는 중에 취소되어 이 파트는 분석하지 못했습니다."
    result.failed += 1
    if last_code == 429:
        return (f"[AI 분석 오류 - 파트 {part}] 요청 한도(HTTP 429)를 {_RETRY_ROUNDS}회 연속 초과해 이 파트는 "
                "분석하지 못했습니다. 잠시 후 다시 실행하세요.")
    return (f"[AI 분석 오류 - 파트 {part}] 제공자 서버 오류(HTTP {last_code})가 {_RETRY_ROUNDS}회 이어져 이 파트는 "
            "분석하지 못했습니다. 잠시 후 다시 실행하세요.")


def send_prompts(requests, mode, ring):
    """build_prompts 가 만든 내용을 차례로 보낸다 -> SendResult. ring: KeyRing(설정 목록을 주면 새로 만든다).
    파트 하나가 실패해도 나머지는 보낸다."""
    if not isinstance(requests, PreparedRequests):
        raise TypeError("send_prompts 는 build_prompts 가 만든 내용만 보낸다")
    if not isinstance(ring, KeyRing):
        ring = KeyRing(ring, mode)
    if not requests.masked and ring.leaves_this_pc():
        raise ValueError("가리지 않고 만든 내용을 이 PC 밖으로 보내려 했다 — 받는 곳에 맞게 build_prompts 를 다시 부르세요")
    result = SendResult()
    total = len(requests.prompts)
    for part, prompt in enumerate(requests.prompts, start=1):
        if result.stopped or _stopping(ring):
            result.stopped = True
            result.texts.append(f"[AI 분석 중단 - 파트 {part}~{total}] 취소되어 보내지 않았습니다.")
            break
        before = result.sent
        result.texts.append(_send_part(prompt, mode, ring, part, result))
        # RPM 방어 지연 — 클라우드에 여러 파트를 보낼 때 성공한 요청 사이에 쉰다(취소되면 바로 깬다).
        if mode == "cloud" and result.sent > before and part < total:
            _pause(ring, ring.pause_sec)
    return result


def analyze_raw_log_text(text, mode, api_cfg, *, is_virtual=False, device_names=()):
    """원시 로그(raw .txt) 텍스트를 분석하여 텍스트 결과 반환 — build_prompts 로 만들어 send_prompts 로 보낸다.
    클라우드와 로컬의 분할(Chunk) 크기 및 max_tokens 는 request_settings 가 정하고, 요청 한도(429)·서버
    오류에는 자동으로 다시 보낸다. 미리보기 승인을 거치는 분석은 transmission.py 가 둘을 따로 부른다."""
    requests = build_prompts(text, mode, api_cfg, is_virtual=is_virtual, device_names=device_names)
    return send_prompts(requests, mode, [api_cfg or {}]).text
