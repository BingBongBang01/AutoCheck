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


def _try_api(scored_or_findings, api_cfg, user_approved_cloud=False, hostnames=None):
    """
    실제 LLM API 호출 시도. Cloud API는 반드시 user_approved_cloud=True 여야 시도함
    (문서 원칙: "Cloud AI는 사용자가 승인해야만 사용"). 승인 없으면 즉시 예외 —
    라우터가 다음 단계(규칙기반)로 폴백하게 함.
    """
    if not user_approved_cloud:
        raise PermissionError("Cloud AI는 사용자 승인 전엔 호출하지 않음(설계 원칙)")

    import os
    api_key = os.environ.get(api_cfg.get("api_key_env", ""))
    if not api_key:
        raise RuntimeError("API 키 환경변수가 설정되지 않음")

    # --- Sanitizer 통과: Cloud로 나가는 이 지점에서만 마스킹, 원본(scored_or_findings)은 안 건드림 ---
    from core.sanitizer import mask_findings_with_mapping
    from core.ai_context_builder import build_context, to_prompt_text

    findings = scored_or_findings if _looks_like_findings(scored_or_findings) else rule_based.detect_anomalies(scored_or_findings)
    masked, mapping = mask_findings_with_mapping(findings, hostnames=hostnames)
    os.makedirs("ai_mappings", exist_ok=True)
    with open(os.path.join("ai_mappings", f"{datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"), "w", encoding="utf-8") as file:
        json.dump(mapping, file, ensure_ascii=False, indent=2)
    prompt_source = build_context(masked, max_items=20)
    prompt = f"다음 네트워크 점검 결과(민감정보 마스킹됨)를 요약하고 조치를 제안해줘:\n{json.dumps(prompt_source, ensure_ascii=False)}"

    endpoint = api_cfg.get("endpoint", "https://api.anthropic.com/v1/messages")
    body = json.dumps({
        "model": api_cfg.get("model", "claude-sonnet-4-6"),
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    text = "".join(block.get("text", "") for block in data.get("content", []))
    return {"source": "api", "summary": text, "anomaly_count": len(rule_based.detect_anomalies(scored_or_findings)),
            "top_priority": rule_based.prioritize(rule_based.detect_anomalies(scored_or_findings))[:5]}


def _try_gemini(scored_or_findings, api_cfg, user_approved_cloud=False, hostnames=None):
    """
    Gemini API 호출 — PDF번역 프로그램에서 이미 검증된 'Gemini API + 로컬 NPU 폴백' 패턴을
    이 프로젝트의 AI 분석에도 동일하게 적용. Cloud AI 승인 게이트는 Anthropic과 동일하게 강제.
    """
    if not user_approved_cloud:
        raise PermissionError("Cloud AI는 사용자 승인 전엔 호출하지 않음(설계 원칙)")

    import os
    api_key = os.environ.get(api_cfg.get("api_key_env", "GEMINI_API_KEY"))
    if not api_key:
        raise RuntimeError("Gemini API 키 환경변수가 설정되지 않음")

    from core.sanitizer import mask_findings_with_mapping
    from core.ai_context_builder import build_context

    findings = scored_or_findings if _looks_like_findings(scored_or_findings) else rule_based.detect_anomalies(scored_or_findings)
    masked, mapping = mask_findings_with_mapping(findings, hostnames=hostnames)
    os.makedirs("ai_mappings", exist_ok=True)
    with open(os.path.join("ai_mappings", f"{datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"), "w", encoding="utf-8") as file:
        json.dump(mapping, file, ensure_ascii=False, indent=2)
    prompt_source = build_context(masked, max_items=20)
    prompt = f"다음 네트워크 점검 결과(민감정보 마스킹됨)를 요약하고 조치를 제안해줘:\n{json.dumps(prompt_source, ensure_ascii=False)}"

    model = api_cfg.get("model", "gemini-1.5-flash")
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return {"source": "gemini", "summary": text, "anomaly_count": len(rule_based.detect_anomalies(scored_or_findings)),
            "top_priority": rule_based.prioritize(rule_based.detect_anomalies(scored_or_findings))[:5]}


def _looks_like_findings(obj):
    """scored(list of stage dict) vs findings(list of Finding) 구분용 — 과도기 호환."""
    return bool(obj) and hasattr(obj[0], "to_dict")


def _try_local_npu(scored, local_cfg):
    """로컬 Lemonade 서버(Gemma) 호출 시도. 응답 없으면 예외."""
    endpoint = local_cfg.get("endpoint", "http://localhost:13305")
    prompt = f"다음 네트워크 점검 결과를 요약해줘:\n{json.dumps(rule_based.detect_anomalies(scored), ensure_ascii=False)}"
    payload = {"prompt": prompt}
    if local_cfg.get("model"):
        payload["model"] = local_cfg["model"]
    if "temperature" in local_cfg:
        payload["temperature"] = local_cfg["temperature"]
    if "max_tokens" in local_cfg:
        payload["max_tokens"] = local_cfg["max_tokens"]
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{endpoint}/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    return {"source": "local_npu", "summary": data.get("text", ""),
            "anomaly_count": len(rule_based.detect_anomalies(scored)),
            "top_priority": rule_based.prioritize(rule_based.detect_anomalies(scored))[:5]}


def analyze(scored, ai_config=None, user_approved_cloud=False, hostnames=None):
    """
    ai_config 예:
      {"providers": [{"type": "api", ...}, {"type": "local", ...}]}
    설정이 없거나 전부 실패하면 규칙기반으로 항상 결과를 반환 (절대 예외를 던지지 않음).
    user_approved_cloud=False(기본값)면 Cloud API는 아예 시도하지 않고 바로 실패 처리되어
    다음 단계(로컬/규칙기반)로 폴백함 — "Cloud AI는 사용자 승인 필요" 원칙을 라우터 레벨에서 강제.
    """
    ai_config = ai_config or {}
    providers = ai_config.get("providers", [])

    for provider in providers:
        try:
            if provider.get("type") == "api":
                return _try_api(scored, provider, user_approved_cloud=user_approved_cloud, hostnames=hostnames)
            elif provider.get("type") == "gemini":
                return _try_gemini(scored, provider, user_approved_cloud=user_approved_cloud, hostnames=hostnames)
            elif provider.get("type") == "local":
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
