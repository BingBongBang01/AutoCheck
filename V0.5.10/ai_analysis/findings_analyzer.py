"""구조화된 findings(scored 결과) 분석 경로 — 마스킹 후 배치 분할해 API(설정된 경우) ->
로컬 NPU(Lemonade) -> 규칙기반 순서로 시도. 원시 로그 텍스트 분석은 raw_log_analyzer.py 참고.
"""
import json
import os
import datetime
import urllib.request

from core import cloud_providers

try:
    from ai_analysis import rule_based
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ai_analysis import rule_based


def _looks_like_findings(obj):
    """scored(list of stage dict) vs findings(list of Finding) 구분용 — 과도기 호환."""
    return bool(obj) and hasattr(obj[0], "to_dict")


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
    model = api_cfg.get("model") or cloud_providers.default_model("gemini")
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
    # OpenAI 호환 제공자(NVIDIA NIM/xAI/Mistral/DeepSeek/Groq/Perplexity/Upstage)는 모두 이
    # 핸들러를 그대로 쓴다 — 주소만 제공자 표에서 가져오면 요청 형식이 동일하다.
    endpoint = (api_cfg.get("endpoint")
                or cloud_providers.chat_endpoint_of(api_cfg.get("provider"))
                or "https://api.openai.com/v1/chat/completions")
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

    return {"source": api_cfg.get("provider") or "openai", "summary": "\n\n".join(summaries),
            "anomaly_count": len(rule_based.detect_anomalies(scored_or_findings)),
            "top_priority": rule_based.prioritize(rule_based.detect_anomalies(scored_or_findings))[:5]}


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


# 제공자 id가 아니라 '호출 형식(wire)'으로 핸들러를 고른다 — OpenAI 호환 제공자가 늘어날
# 때마다 여기 항목을 추가할 필요가 없다. wire는 core/cloud_providers.py가 정한다.
_WIRE_HANDLERS = {"anthropic": _try_api, "gemini": _try_gemini, "openai_compat": _try_openai}


def _try_cloud_apis(scored, provider, user_approved_cloud, hostnames, batching_cfg=None):
    """등록된 클라우드 API 키 목록(entries)을 이름 순서대로 시도하고, 실패한 키는 건너뛰어
    다음 키로 넘어간다(PDF번역 프로그램의 key pool 로테이션과 동일한 원리). enabled=False인
    항목은 건너뜀."""
    entries = [e for e in provider.get("entries", []) if e.get("enabled")]
    last_error = None
    for entry in entries:
        handler = _WIRE_HANDLERS.get(cloud_providers.wire_of(entry.get("provider")))
        if handler is None:
            print(f"[AI 라우터] cloud_apis:{entry.get('name')}({entry.get('provider')}) "
                  f"호출 형식을 몰라 건너뜀 — core/cloud_providers.py의 wire 확인")
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
