"""원시 로그(raw .txt) 텍스트 분석 경로 — 마스킹 없이 청크 단위로 API/로컬 NPU에 직접 전달.
구조화된 findings(scored) 분석 경로는 findings_analyzer.py 참고.
"""
import json
import os
import time
import urllib.request
import urllib.error

from core import cloud_providers

_RAW_LOG_PROMPT_PREFIX = (
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


def _urlopen_with_context(req, timeout, label):
    """urlopen 실패 시(특히 404) 어떤 endpoint/모델을 호출하다 실패했는지, 서버가 어떤 응답
    본문을 돌려줬는지까지 포함해서 예외를 다시 던진다 — '연결 테스트는 되는데 분석은 404'처럼
    원인을 알기 어려운 상황에서 로그만 보고도 원인을 바로 알 수 있게 하기 위함."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        print(f"[AI 라우터] {label} 호출 실패: {req.full_url} -> HTTP {exc.code} {exc.reason} | 응답: {body}")
        raise


def _call_api_raw_text(prompt, api_cfg):
    api_key = api_cfg.get("api_key") or os.environ.get(api_cfg.get("api_key_env", "ANTHROPIC_API_KEY"))
    if not api_key:
        raise RuntimeError("API 키가 설정되지 않음")
    endpoint = api_cfg.get("endpoint") or "https://api.anthropic.com/v1/messages"
    model = api_cfg.get("model", "claude-sonnet-4-6")
    body = json.dumps({
        "model": model,
        "max_tokens": api_cfg.get("max_tokens", 1500),
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })
    with _urlopen_with_context(req, 30, f"anthropic(model={model})") as resp:
        data = json.loads(resp.read())
    return "".join(block.get("text", "") for block in data.get("content", []))


def _call_gemini_raw_text(prompt, api_cfg):
    api_key = api_cfg.get("api_key") or os.environ.get(api_cfg.get("api_key_env", "GEMINI_API_KEY"))
    if not api_key:
        raise RuntimeError("Gemini API 키가 설정되지 않음")
    model = api_cfg.get("model") or cloud_providers.default_model("gemini")
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
    with _urlopen_with_context(req, 30, f"gemini(model={model})") as resp:
        data = json.loads(resp.read())
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_openai_raw_text(prompt, api_cfg):
    api_key = api_cfg.get("api_key") or os.environ.get(api_cfg.get("api_key_env", "OPENAI_API_KEY"))
    if not api_key:
        raise RuntimeError("OpenAI API 키가 설정되지 않음")
    # OpenAI 호환 제공자 전부가 이 함수를 공유한다 — 주소만 제공자 표에서 가져온다.
    endpoint = (api_cfg.get("endpoint")
                or cloud_providers.chat_endpoint_of(api_cfg.get("provider"))
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
    with _urlopen_with_context(req, 30, f"openai(model={model})") as resp:
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


def analyze_raw_log_text(text, mode, api_cfg):
    """원시 로그(raw .txt) 텍스트를 분석하여 텍스트 결과 반환.
    클라우드와 로컬의 분할(Chunk) 크기 및 max_tokens를 동적으로 결정하며,
    Rate Limit(429) 발생 시 자동 재시도 로직을 포함함.
    """
    api_cfg = api_cfg or {}
    model_name = api_cfg.get("model", "").lower()
    sleep_time = 0

    # 1. 모드별 오버플로우(Chunking) 설정 분리
    if mode == "local":
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
    else:
        provider_type = api_cfg.get("provider") or api_cfg.get("type", "anthropic")

        # 클라우드 제공자별 적정 기본 수치 및 지연 시간 — 수치는 제공자 표에서 읽으므로
        # 제공자를 추가할 때 이 분기를 늘리지 않는다.
        spec = cloud_providers.get(provider_type) or {}
        default_chars = spec.get("batch_chars", 20000)
        default_tokens = spec.get("max_tokens", 1500)
        # RPM 방어 지연: Gemini가 분당 요청 제한이 가장 빡빡해서 더 길게 쉰다.
        sleep_time = 4 if provider_type == "gemini" else 2

        # settings_api.py에 저장된 개별 클라우드 설정값 우선 반영
        batch_cfg = {
            "batch_chars": api_cfg.get("batch_chars", default_chars),
            "max_tokens": api_cfg.get("max_tokens", default_tokens)
        }

    api_cfg["max_tokens"] = batch_cfg["max_tokens"]
    chunk_size = batch_cfg["batch_chars"]

    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    summaries = []

    for i, chunk in enumerate(chunks):
        prefix = f"(전체 {len(chunks)} 파트 중 {i + 1}번째 파트)\n" if len(chunks) > 1 else ""
        prompt = _RAW_LOG_PROMPT_PREFIX + prefix + chunk

        max_retries = 3
        for attempt in range(max_retries):
            try:
                if mode == "cloud":
                    provider_type = api_cfg.get("provider") or api_cfg.get("type", "anthropic")
                    # 제공자 id가 아니라 호출 형식(wire)으로 고른다 — OpenAI 호환 제공자
                    # (NVIDIA NIM/xAI/Mistral/DeepSeek/Groq/Perplexity/Upstage)가 늘어도 그대로 동작.
                    wire = cloud_providers.wire_of(provider_type)
                    if wire == "gemini":
                        res = _call_gemini_raw_text(prompt, api_cfg)
                    elif wire == "openai_compat":
                        res = _call_openai_raw_text(prompt, api_cfg)
                    else:
                        res = _call_api_raw_text(prompt, api_cfg)
                elif mode == "local":
                    res = _call_local_npu_raw_text(prompt, api_cfg)
                else:
                    res = f"[AI 분석 오류] 알 수 없는 모드: {mode}"

                summaries.append(res)

                # 2. 클라우드 API 호출 성공 시 RPM 방어 지연
                if mode == "cloud" and len(chunks) > 1:
                    time.sleep(sleep_time)
                break

            except urllib.error.HTTPError as e:
                # 3. HTTP 429 Too Many Requests 대응
                if e.code == 429:
                    wait_time = 22
                    print(f"[AI 분석] 429 Too Many Requests. {wait_time}초 대기 후 재시도 (시도 {attempt+1}/{max_retries})...")
                    time.sleep(wait_time)
                    continue
                else:
                    summaries.append(f"[AI 분석 오류 - 파트 {i+1}] HTTP 오류: {e.code}")
                    break
            except Exception as e:
                summaries.append(f"[AI 분석 오류 - 파트 {i+1}] {e}")
                break

    return "\n\n".join(summaries)
