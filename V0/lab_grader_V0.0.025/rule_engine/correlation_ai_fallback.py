"""규칙으로 못 잡는 애매한 상관관계 케이스의 AI 폴백 — ai_analysis/router.py의
_try_api/_try_gemini/_try_local_npu 폴백 체인 구조를 그대로 재사용(신규 provider 없음).
"""
import json
import os
import datetime
import urllib.request

try:
    from core.root_cause import RootCauseFinding, SOURCE_AI_CLOUD, SOURCE_AI_LOCAL
    from engine.session_timeline import parse_event_ts
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.root_cause import RootCauseFinding, SOURCE_AI_CLOUD, SOURCE_AI_LOCAL
    from engine.session_timeline import parse_event_ts


def _causal_prompt(events):
    lines = [f"- device={e.device} time={e.timestamp} type={e.event_type} "
             f"interfaces={e.interfaces} mac={e.mac}" for e in events]
    return (
        "다음은 시간상 근접해 있지만 규칙으로는 인과관계를 확정하지 못한 네트워크 이벤트 목록이다"
        "(장비명/MAC은 마스킹됨). 이 중 원인-결과 관계가 있다고 보이는 이벤트 쌍이 있다면 "
        "'원인: ..., 결과: ..., 근거: ...' 형식으로 짧게 답하고, 없다면 '인과관계 불명확'이라고 답해줘:\n"
        + "\n".join(lines)
    )


def _mask_events(events, hostnames=None):
    from core.sanitizer import mask_findings_with_mapping
    return mask_findings_with_mapping(events, hostnames=hostnames)


def _try_api(events, api_cfg, project_id, session_id, user_approved_cloud=False, hostnames=None):
    if not user_approved_cloud:
        raise PermissionError("Cloud AI는 사용자 승인 전엔 호출하지 않음(설계 원칙)")
    api_key = os.environ.get(api_cfg.get("api_key_env", ""))
    if not api_key:
        raise RuntimeError("API 키 환경변수가 설정되지 않음")

    masked, mapping = _mask_events(events, hostnames=hostnames)
    os.makedirs("ai_mappings", exist_ok=True)
    with open(os.path.join("ai_mappings", f"corr_{datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"), "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

    prompt = ("다음은 시간상 근접해 있지만 규칙으로는 인과관계를 확정하지 못한 네트워크 이벤트 목록이다"
               "(민감정보 마스킹됨). 인과관계가 있다고 보이는 쌍이 있다면 '원인: ..., 결과: ..., 근거: ...' "
               "형식으로 짧게 답하고, 없다면 '인과관계 불명확'이라고 답해줘:\n"
               + json.dumps(masked, ensure_ascii=False))

    endpoint = api_cfg.get("endpoint", "https://api.anthropic.com/v1/messages")
    body = json.dumps({
        "model": api_cfg.get("model", "claude-sonnet-4-6"),
        "max_tokens": 500,
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
    return _build_ai_finding(events, project_id, session_id, text, SOURCE_AI_CLOUD)


def _try_gemini(events, api_cfg, project_id, session_id, user_approved_cloud=False, hostnames=None):
    if not user_approved_cloud:
        raise PermissionError("Cloud AI는 사용자 승인 전엔 호출하지 않음(설계 원칙)")
    api_key = os.environ.get(api_cfg.get("api_key_env", "GEMINI_API_KEY"))
    if not api_key:
        raise RuntimeError("Gemini API 키 환경변수가 설정되지 않음")

    masked, mapping = _mask_events(events, hostnames=hostnames)
    os.makedirs("ai_mappings", exist_ok=True)
    with open(os.path.join("ai_mappings", f"corr_{datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"), "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

    prompt = ("다음은 시간상 근접해 있지만 규칙으로는 인과관계를 확정하지 못한 네트워크 이벤트 목록이다"
               "(민감정보 마스킹됨). 인과관계가 있다고 보이는 쌍이 있다면 '원인: ..., 결과: ..., 근거: ...' "
               "형식으로 짧게 답하고, 없다면 '인과관계 불명확'이라고 답해줘:\n"
               + json.dumps(masked, ensure_ascii=False))

    model = api_cfg.get("model", "gemini-1.5-flash")
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return _build_ai_finding(events, project_id, session_id, text, SOURCE_AI_CLOUD)


def _try_local_npu(events, local_cfg, project_id, session_id):
    endpoint = local_cfg.get("endpoint", "http://localhost:13305")
    payload = {"prompt": _causal_prompt(events)}
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
    return _build_ai_finding(events, project_id, session_id, data.get("text", ""), SOURCE_AI_LOCAL)


def _build_ai_finding(events, project_id, session_id, explanation_text, source):
    """AI는 이 사건 묶음 안에서 인과관계 유무를 텍스트로만 판단할 뿐, 규칙처럼 cause/effect를
    구조적으로 분리해 확정하지 않는다 — 그래서 cause_event는 시간상 가장 이른 이벤트로 두고,
    나머지를 effect_events에 담되 confidence는 None(AI가 스스로 수치를 매기지 않는 한 불명)."""
    ordered = sorted(events, key=lambda e: (e.timestamp is None, e.timestamp or ""))
    cause_event, effects = ordered[0], ordered[1:]
    return RootCauseFinding(
        project_id=project_id, session_id=session_id,
        cause_device=cause_event.device, cause_event=cause_event.to_dict(),
        effect_events=[e.to_dict() for e in effects],
        confidence=None, rule_id="", explanation=explanation_text.strip(), source=source,
    )


def infer_ambiguous_root_causes(unmatched_events, project_id, session_id, ai_config=None,
                                 user_approved_cloud=False, hostnames=None, window_sec=180):
    """
    규칙에 안 걸린 이벤트들을 시간순으로 슬라이딩 윈도우 묶어(서로 다른 장비가 섞인 묶음만)
    AI에 "이 목록에서 인과관계를 추정하라" 프롬프트로 넘긴다. 설정 없거나 전부 실패하면
    빈 리스트 반환(=무리하게 추측하지 않음, rule_based.py와 달리 이 단계엔 규칙 폴백이 없음 —
    이미 규칙 매칭을 시도한 나머지이기 때문).
    """
    ai_config = ai_config or {}
    providers = ai_config.get("providers", [])
    if not providers or not unmatched_events:
        return []

    dated = sorted(
        ((e, parse_event_ts(e, datetime.date.today().year)) for e in unmatched_events),
        key=lambda pair: (pair[1] is None, pair[1] or datetime.datetime.min),
    )
    window = datetime.timedelta(seconds=window_sec)
    groups, current = [], []
    for e, ts in dated:
        if current and (ts is None or current[-1][1] is None or ts - current[-1][1] > window):
            groups.append([g[0] for g in current])
            current = []
        current.append((e, ts))
    if current:
        groups.append([g[0] for g in current])

    ambiguous_groups = [g for g in groups if len({e.device for e in g}) >= 2]

    findings = []
    for group in ambiguous_groups:
        for provider in providers:
            try:
                if provider.get("type") == "api":
                    findings.append(_try_api(group, provider, project_id, session_id,
                                              user_approved_cloud=user_approved_cloud, hostnames=hostnames))
                elif provider.get("type") == "gemini":
                    findings.append(_try_gemini(group, provider, project_id, session_id,
                                                 user_approved_cloud=user_approved_cloud, hostnames=hostnames))
                elif provider.get("type") == "local":
                    findings.append(_try_local_npu(group, provider, project_id, session_id))
                else:
                    continue
                break
            except Exception as e:
                print(f"[상관관계 AI] {provider.get('type')} 실패({e}) — 다음 provider로 폴백")
                continue
    return findings
