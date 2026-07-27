"""규칙 기반 상관관계/근본원인 매칭 — 장비 A의 user_reload/interface_down류 이벤트 직후
(수 초~수 분 내) "인접" 장비들에서 LLDP timeout/link down이 동일 MAC 또는 동일 인터페이스로
발생하면 그 A 이벤트를 원인(cause)으로 지목한 RootCauseFinding(core/root_cause.py)을 만든다.

규칙으로 못 잡는 애매한 케이스는 correlation_ai_fallback.py로 넘어간다.
"""
import os
import datetime

try:
    from core.root_cause import RootCauseFinding
    from engine.session_timeline import parse_event_ts
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.root_cause import RootCauseFinding
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
