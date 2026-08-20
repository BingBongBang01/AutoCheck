"""
상관관계/근본원인 추론 — 규칙 기반 우선(ai_analysis/rule_based.py의 "network-wide"
우선순위 사상을 확장): 장비 A의 user_reload/interface_down류 이벤트 직후(수 초~수 분 내)
"인접" 장비들에서 LLDP timeout/link down이 동일 MAC 또는 동일 인터페이스로 발생하면
그 A 이벤트를 원인(cause)으로 지목한 RootCauseFinding(core/root_cause.py)을 만든다.

규칙으로 못 잡는 애매한 케이스(다른 장비 이벤트가 시간상으로는 근접해 있지만 MAC도
인터페이스도 안 겹치는 경우)만 AI로 넘긴다.

이 모듈은 engine/session_timeline.py가 만든 이벤트 목록/그룹을 입력으로 받을 뿐,
comparator.py -> Finding 기반 grading 흐름과는 무관하다.

구현은 두 경로로 분리되어 있다:
  - correlation_rule_matcher.py: 규칙 기반 매칭(CORRELATION_RULES, find_root_causes)
  - correlation_ai_fallback.py: 규칙으로 못 잡는 애매한 케이스의 AI 폴백(infer_ambiguous_root_causes)
"""
from rule_engine.correlation_rule_matcher import CORRELATION_RULES, find_root_causes
from rule_engine.correlation_ai_fallback import infer_ambiguous_root_causes

__all__ = ["CORRELATION_RULES", "find_root_causes", "infer_ambiguous_root_causes", "analyze"]


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
