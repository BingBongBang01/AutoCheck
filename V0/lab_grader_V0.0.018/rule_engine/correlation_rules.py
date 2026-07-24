"""
상관관계/근본원인 추론 — 규칙 기반 우선(ai_analysis/rule_based.py의 "network-wide"
우선순위 사상을 확장): 장비 A의 user_reload/interface_down류 이벤트 직후(수 초~수 분 내)
"인접" 장비들에서 LLDP timeout/link down이 동일 MAC 또는 동일 인터페이스로 발생하면
그 A 이벤트를 원인(cause)으로 지목한 RootCauseFinding(core/root_cause.py)을 만든다.

규칙으로 못 잡는 애매한 케이스(다른 장비 이벤트가 시간상으로는 근접해 있지만 MAC도
인터페이스도 안 겹치는 경우)만 AI로 넘긴다 — ai_analysis/router.py의
_try_api/_try_gemini/_try_local_npu 폴백 체인 구조를 그대로 재사용(신규 provider 없음).

이 모듈은 engine/session_timeline.py가 만든 이벤트 목록/그룹을 입력으로 받을 뿐,
comparator.py -> Finding 기반 grading 흐름과는 무관하다.
"""
import json
import os
import datetime
import urllib.request
import urllib.error

try:
    from core.root_cause import RootCauseFinding, SOURCE_AI_CLOUD, SOURCE_AI_LOCAL
    from engine.session_timeline import parse_event_ts
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.root_cause import RootCauseFinding, SOURCE_AI_CLOUD, SOURCE_AI_LOCAL
    from engine.session_timeline import parse_event_ts


# 우선순위 규칙 — 리스트 순서가 곧 우선순위(먼저 매칭되는 규칙을 채택).
# window_sec: cause 발생 시각으로부터 이 시간 안에 발생한 effect만 인정("직후" 관계).
CORRELATION_RULES = [
    {
        "rule_id": "reload_then_lldp_timeout",
        "cause_types": {"user_reload"},
        "effect_types": {"lldp_timeout"},
        "window_sec": 180,
        "confidence": 0.9,
        "explanation": "장비 reload 직후 인접 장비에서 LLDP neighbor timeout 발생 — reload로 인한 일시적 링크 단절로 추정",
    },
    {
        "rule_id": "interface_down_then_lldp_timeout",
        "cause_types": {"interface_down", "ethernet_down", "port_channel_down", "mgmt_down"},
        "effect_types": {"lldp_timeout"},
        "window_sec": 120,
        "confidence": 0.85,
        "explanation": "인터페이스 down 직후 인접 장비에서 같은 링크의 LLDP neighbor timeout 발생 — 물리 링크 단절의 연쇄 반응으로 추정",
    },
    {
        "rule_id": "interface_down_then_interface_down",
        "cause_types": {"interface_down", "ethernet_down", "port_channel_down", "mgmt_down"},
        "effect_types": {"interface_down", "ethernet_down", "port_channel_down", "mgmt_down"},
        "window_sec": 60,
        "confidence": 0.75,
        "explanation": "인터페이스 down 직후 인접 장비에서도 연결 인터페이스 down — 같은 케이블/링크 단절의 양쪽 관측으로 추정",
    },
]


def _shares_signal(cause_event, effect_event):
    """cause/effect가 "동일 MAC 또는 동일 인터페이스 방향"으로 연관돼 있는지 확인.
    인터페이스는 이름 문자열이 정확히 같을 필요는 없고(장비마다 로컬 넘버링이 다를 수 있음),
    최소 한쪽의 인터페이스 번호 부분이 겹치면 "같은 방향"으로 간주 — 과도한 정밀 매칭 대신
    규칙 오탐(미스매치로 인한 취소)보다 회수율을 우선."""
    if cause_event.mac and effect_event.mac and cause_event.mac == effect_event.mac:
        return True
    cause_ifaces = set(cause_event.interfaces)
    effect_ifaces = set(effect_event.interfaces)
    if cause_ifaces and effect_ifaces and cause_ifaces & effect_ifaces:
        return True
    return False


def find_root_causes(events, project_id, session_id, session_year=None, rules=None):
    """
    events: [DeviceEvent, ...] (engine/session_timeline.collect_session_events 결과 등)
    반환: [RootCauseFinding, ...] (source="rule"), matched_ids(효과로 소비된 이벤트 id 집합)와
          함께 (matched_ids, unmatched_cause_events)도 반환해 AI 폴백이 나머지를 이어받게 함.
    """
    rules = rules or CORRELATION_RULES
    session_year = session_year or datetime.date.today().year

    dated = [(e, parse_event_ts(e, session_year)) for e in events]
    dated = [(e, ts) for e, ts in dated if ts is not None]
    dated.sort(key=lambda pair: pair[1])

    findings = []
    matched_effect_ids = set()
    matched_cause_ids = set()

    for i, (cause_event, cause_ts) in enumerate(dated):
        rule = next((r for r in rules if cause_event.event_type in r["cause_types"]), None)
        if rule is None:
            continue

        window = datetime.timedelta(seconds=rule["window_sec"])
        effects = []
        for effect_event, effect_ts in dated[i + 1:]:
            if effect_ts - cause_ts > window:
                break
            if effect_event.device == cause_event.device:
                continue
            if effect_event.event_type not in rule["effect_types"]:
                continue
            if not _shares_signal(cause_event, effect_event):
                continue
            effects.append(effect_event)

        if not effects:
            continue

        findings.append(RootCauseFinding(
            project_id=project_id, session_id=session_id,
            cause_device=cause_event.device, cause_event=cause_event.to_dict(),
            effect_events=[e.to_dict() for e in effects],
            confidence=rule["confidence"], rule_id=rule["rule_id"],
            explanation=rule["explanation"], source="rule",
        ))
        matched_cause_ids.add(id(cause_event))
        matched_effect_ids.update(id(e) for e in effects)

    consumed_ids = matched_cause_ids | matched_effect_ids
    unmatched = [e for e, _ in dated if id(e) not in consumed_ids]
    return findings, unmatched


# --- AI 폴백: ai_analysis/router.py의 _try_api/_try_gemini/_try_local_npu 구조 재사용 ---

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


def analyze(events, project_id, session_id, session_year=None, ai_config=None,
            user_approved_cloud=False, hostnames=None):
    """전체 파이프라인 — 규칙 매칭 우선, 남은 애매한 케이스만 AI 폴백.
    반환: [RootCauseFinding, ...] (규칙 매칭분 먼저, AI 보강분 다음)"""
    rule_findings, unmatched = find_root_causes(events, project_id, session_id, session_year=session_year)
    ai_findings = infer_ambiguous_root_causes(
        unmatched, project_id, session_id, ai_config=ai_config,
        user_approved_cloud=user_approved_cloud, hostnames=hostnames,
    )
    return rule_findings + ai_findings
