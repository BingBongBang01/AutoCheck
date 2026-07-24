"""
AI 분석 라우터. 우선순위: API(설정된 경우) -> 로컬 NPU(Lemonade) -> 규칙기반(항상 성공).
실제 API 키/네트워크가 없어도 프로그램이 절대 멈추지 않도록 규칙기반을 최종 안전망으로 둔다.
"""
import json
import os
import datetime
import urllib.request
import urllib.error

try:
    from ai_analysis import rule_based
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ai_analysis import rule_based


def _build_masked_batches(scored_or_findings, hostnames, batching_cfg):
    """마스킹 후 batch_chars/batch_segs 한도로 분할된 배치 리스트를 반환(컨텍스트 오버플로우 방지)."""
    from core.sanitizer import mask_findings_with_mapping
    from core.ai_context_builder import build_context, make_batches

    findings = scored_or_findings if _looks_like_findings(scored_or_findings) else rule_based.detect_anomalies(scored_or_findings)
    masked, mapping = mask_findings_with_mapping(findings, hostnames=hostnames)
    os.makedirs("ai_mappings", exist_ok=True)
    with open(os.path.join("ai_mappings", f"{datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"), "w", encoding="utf-8") as file:
        json.dump(mapping, file, ensure_ascii=False, indent=2)
    prompt_source = build_context(masked, max_items=200)
    batch_chars = batching_cfg.get("batch_chars", 1500)
    batch_segs = batching_cfg.get("batch_segs", 10)
    return make_batches(prompt_source["items"], max_chars=batch_chars, max_segs=batch_segs), prompt_source


def _prompt_for_batch(items, idx, total):
    prefix = f"(배치 {idx + 1}/{total}) " if total > 1 else ""
    return (f"다음 네트워크 점검 결과(민감정보 마스킹됨)를 요약하고 조치를 제안해줘:\n"
            f"{prefix}{json.dumps(items, ensure_ascii=False)}")


def _try_api(scored_or_findings, api_cfg, user_approved_cloud=False, hostnames=None, batching_cfg=None):
    """
    실제 LLM API 호출 시도. Cloud API는 반드시 user_approved_cloud=True 여야 시도함
    (문서 원칙: "Cloud AI는 사용자가 승인해야만 사용"). 승인 없으면 즉시 예외 —
    라우터가 다음 단계(규칙기반)로 폴백하게 함.
    """
    if not user_approved_cloud:
        raise PermissionError("Cloud AI는 사용자 승인 전엔 호출하지 않음(설계 원칙)")

    api_key = api_cfg.get("api_key") or os.environ.get(api_cfg.get("api_key_env", ""))
    if not api_key:
        raise RuntimeError("API 키가 설정되지 않음")

    batching_cfg = batching_cfg or {}
    batches, prompt_source = _build_masked_batches(scored_or_findings, hostnames, batching_cfg)
    max_tokens = batching_cfg.get("max_tokens", 1000)
    endpoint = api_cfg.get("endpoint", "https://api.anthropic.com/v1/messages")

    summaries = []
    for idx, batch in enumerate(batches):
        body = json.dumps({
            "model": api_cfg.get("model", "claude-sonnet-4-6"),
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": _prompt_for_batch(batch, idx, len(batches))}],
        }).encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        summaries.append("".join(block.get("text", "") for block in data.get("content", [])))

    return {"source": "api", "summary": "\n\n".join(summaries), "anomaly_count": len(rule_based.detect_anomalies(scored_or_findings)),
            "top_priority": rule_based.prioritize(rule_based.detect_anomalies(scored_or_findings))[:5]}


def _try_gemini(scored_or_findings, api_cfg, user_approved_cloud=False, hostnames=None, batching_cfg=None):
    """
    Gemini API 호출 — PDF번역 프로그램에서 이미 검증된 'Gemini API + 로컬 NPU 폴백' 패턴을
    이 프로젝트의 AI 분석에도 동일하게 적용. Cloud AI 승인 게이트는 Anthropic과 동일하게 강제.
    """
    if not user_approved_cloud:
        raise PermissionError("Cloud AI는 사용자 승인 전엔 호출하지 않음(설계 원칙)")

    api_key = api_cfg.get("api_key") or os.environ.get(api_cfg.get("api_key_env", "GEMINI_API_KEY"))
    if not api_key:
        raise RuntimeError("Gemini API 키가 설정되지 않음")

    batching_cfg = batching_cfg or {}
    batches, prompt_source = _build_masked_batches(scored_or_findings, hostnames, batching_cfg)
    max_tokens = batching_cfg.get("max_tokens", 1000)
    model = api_cfg.get("model", "gemini-3.5-flash-lite")
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    summaries = []
    for idx, batch in enumerate(batches):
        body = json.dumps({
            "contents": [{"parts": [{"text": _prompt_for_batch(batch, idx, len(batches))}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }).encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        summaries.append(data["candidates"][0]["content"]["parts"][0]["text"])

    return {"source": "gemini", "summary": "\n\n".join(summaries), "anomaly_count": len(rule_based.detect_anomalies(scored_or_findings)),
            "top_priority": rule_based.prioritize(rule_based.detect_anomalies(scored_or_findings))[:5]}


def _try_openai(scored_or_findings, api_cfg, user_approved_cloud=False, hostnames=None, batching_cfg=None):
    """OpenAI Chat Completions 호출 — Anthropic/Gemini와 동일한 배치/승인 규칙 적용."""
    if not user_approved_cloud:
        raise PermissionError("Cloud AI는 사용자 승인 전엔 호출하지 않음(설계 원칙)")

    api_key = api_cfg.get("api_key") or os.environ.get(api_cfg.get("api_key_env", "OPENAI_API_KEY"))
    if not api_key:
        raise RuntimeError("OpenAI API 키가 설정되지 않음")

    batching_cfg = batching_cfg or {}
    batches, prompt_source = _build_masked_batches(scored_or_findings, hostnames, batching_cfg)
    max_tokens = batching_cfg.get("max_tokens", 1000)
    endpoint = api_cfg.get("endpoint", "https://api.openai.com/v1/chat/completions")
    model = api_cfg.get("model", "gpt-4o-mini")

    summaries = []
    for idx, batch in enumerate(batches):
        body = json.dumps({
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": _prompt_for_batch(batch, idx, len(batches))}],
        }).encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        summaries.append(data["choices"][0]["message"]["content"])

    return {"source": "openai", "summary": "\n\n".join(summaries), "anomaly_count": len(rule_based.detect_anomalies(scored_or_findings)),
            "top_priority": rule_based.prioritize(rule_based.detect_anomalies(scored_or_findings))[:5]}


_RAW_LOG_PROMPT_PREFIX = (
    "You are a network expert. Analyze the following raw network switch log. "
    "Identify any ERRORs, TIMEOUTs, interface DOWN states, or anomalies. "
    "Output ONLY the extracted problem lines and a brief summary. "
    "Do not use markdown blocks, output plain text so it can be saved directly as a log file.\n\n"
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
    model = api_cfg.get("model") or "gemini-3.5-flash-lite"
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
    endpoint = api_cfg.get("endpoint") or "https://api.openai.com/v1/chat/completions"
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
    import time
    import urllib.error

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
        
        # 클라우드 제공자별 적정 기본 수치 및 지연 시간
        if provider_type == "gemini":
            default_chars, default_tokens = 40000, 1500
            sleep_time = 4
        elif provider_type == "openai":
            default_chars, default_tokens = 30000, 1500
            sleep_time = 1
        else:
            default_chars, default_tokens = 20000, 1500
            sleep_time = 2
        
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
                    if provider_type == "gemini":
                        res = _call_gemini_raw_text(prompt, api_cfg)
                    elif provider_type == "openai":
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

def _looks_like_findings(obj):
    """scored(list of stage dict) vs findings(list of Finding) 구분용 — 과도기 호환."""
    return bool(obj) and hasattr(obj[0], "to_dict")


def _try_local_npu(scored, local_cfg):
    """로컬 Lemonade 서버(Gemma) 호출 시도. 응답 없으면 예외."""
    endpoint = (local_cfg.get("endpoint") or "http://localhost:13305").rstrip("/")
    prompt = f"다음 네트워크 점검 결과를 요약해줘:\n{json.dumps(rule_based.detect_anomalies(scored), ensure_ascii=False)}"
    payload = {
        "model": local_cfg.get("model", ""),
        "messages": [{"role": "user", "content": prompt}],
    }
    if "temperature" in local_cfg:
        payload["temperature"] = local_cfg["temperature"]
    if "max_tokens" in local_cfg:
        payload["max_tokens"] = local_cfg["max_tokens"]
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{endpoint}/api/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    summary = data["choices"][0]["message"]["content"]
    return {"source": "local_npu", "summary": summary,
            "anomaly_count": len(rule_based.detect_anomalies(scored)),
            "top_priority": rule_based.prioritize(rule_based.detect_anomalies(scored))[:5]}


_CLOUD_HANDLERS = {"anthropic": _try_api, "gemini": _try_gemini, "openai": _try_openai}


def _try_cloud_apis(scored, provider, user_approved_cloud, hostnames, batching_cfg=None):
    """등록된 클라우드 API 키 목록(entries)을 이름 순서대로 시도하고, 실패한 키는 건너뛰어
    다음 키로 넘어간다(PDF번역 프로그램의 key pool 로테이션과 동일한 원리). enabled=False인
    항목은 건너뜀."""
    entries = [e for e in provider.get("entries", []) if e.get("enabled")]
    last_error = None
    for entry in entries:
        handler = _CLOUD_HANDLERS.get(entry.get("provider"))
        if handler is None:
            continue
        try:
            # 클라우드는 공통 설정 대신 각 entry 내부의 넓은 batch_chars/max_tokens 설정을 우선 사용
            return handler(scored, entry, user_approved_cloud=user_approved_cloud, hostnames=hostnames, batching_cfg=entry)
        except Exception as e:
            last_error = e
            print(f"[AI 라우터] cloud_apis:{entry.get('name')}({entry.get('provider')}) 실패({e}) — 다음 키로 폴백")
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("사용 가능한(체크된) 클라우드 API 키가 없음")

def analyze(scored, ai_config=None, user_approved_cloud=False, hostnames=None):
    ai_config = ai_config or {}
    providers = ai_config.get("providers", [])
    
    # 로컬 AI 전용 배칭 설정 (레거시 batching 키 호환 포함)
    local_batching_cfg = ai_config.get("local_batching") or ai_config.get("batching", {})

    for provider in providers:
        try:
            ptype = provider.get("type")
            if ptype == "cloud_apis":
                # 클라우드는 각 entry 자체의 수치를 사용하므로 여기서는 공통 설정 전달 생략
                return _try_cloud_apis(scored, provider, user_approved_cloud, hostnames, batching_cfg=None)
            elif ptype == "api":
                return _try_api(scored, provider, user_approved_cloud=user_approved_cloud, hostnames=hostnames, batching_cfg=provider)
            elif ptype == "gemini":
                return _try_gemini(scored, provider, user_approved_cloud=user_approved_cloud, hostnames=hostnames, batching_cfg=provider)
            elif ptype == "local":
                return _try_local_npu(scored, provider)
        except Exception as e:
            print(f"[AI 라우터] {provider.get('type')} 실패({e}) — 다음 단계로 폴백")
            continue

    result = rule_based.analyze(scored)
    result["source"] = "rule_based"
    return result

if __name__ == "__main__":
    sample_scored = [
        {"label": "STP", "status": "IN_PROGRESS", "pass": 3, "total": 14, "results": [
            {"stage": "STP", "device": "Core1", "check": "root_priority_vlan1_core1", "result": "FAIL", "expected": 4096, "actual": 32768},
        ]},
    ]
    # 설정 없음 -> 바로 규칙기반으로 떨어지는지 확인
    result = analyze(sample_scored, ai_config=None)
    print("source:", result["source"])
    print("summary:", result["summary"])

    # 존재하지 않는 API/로컬 설정 -> 둘 다 실패하고 규칙기반으로 폴백되는지 확인
    result2 = analyze(sample_scored, ai_config={"providers": [
        {"type": "api", "api_key_env": "NONEXISTENT_KEY_XYZ"},
        {"type": "local", "endpoint": "http://localhost:19999"},
    ]})
    print("\nsource(폴백 후):", result2["source"])
    print("summary:", result2["summary"])
