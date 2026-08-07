"""
Rule Engine — 기존 comparator.py(exact_match/absence/in_set 디스패치)의 판정 로직은
그대로 재사용하고, 그 결과(Verdict dict)를 Finding 객체로 승격시키는 어댑터.
comparator.py 내부는 전혀 안 건드림(이미 실전 검증된 로직 — wrap, don't rewrite).

핵심 원칙: 여기서 만든 Finding.result는 이후 AI 단계에서 절대 바뀌지 않는다
(Finding.with_recommendation()이 result를 못 건드리게 설계되어 있음 — core/finding.py 참고).
"""
try:
    from core.finding import Finding
    from engine.comparator import compare_vlan_stage, compare_stp_stage, build_vlan_index
    from rule_engine import registry as rule_registry
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.finding import Finding
    from engine.comparator import compare_vlan_stage, compare_stp_stage, build_vlan_index
    from rule_engine import registry as rule_registry


def run_vlan_stp_rules(project_id, session_id, target_state, collected_vlan, collected_stp):
    """
    기존 grade()가 하던 걸 그대로 수행하되, 결과를 Finding 리스트로 반환.
    반환: {"stage_vlan": [Finding, ...], "stage_stp": [Finding, ...]}
    """
    vlan_index = build_vlan_index(collected_stp)

    vlan_verdicts = compare_vlan_stage(target_state["stage_vlan"]["checks"], collected_vlan)
    stp_verdicts = compare_stp_stage(target_state["stage_stp"]["checks"], collected_stp, vlan_index)

    vlan_findings = [Finding.from_verdict(project_id, session_id, "VLAN", v) for v in vlan_verdicts]
    stp_findings = [Finding.from_verdict(project_id, session_id, "STP", v) for v in stp_verdicts]

    return {"stage_vlan": vlan_findings, "stage_stp": stp_findings}


def run_extended_stage_rules(project_id, session_id, target_state, collected):
    """
    LACP/MLAG/OSPF/VRRP/ACL/NTP/SNMP 등 rule_engine/registry.py에 등록된 모든 stage를 판정.
    VLAN/STP(run_vlan_stp_rules)와 분리한 이유: 이 stage들은 target_state.yaml에 checks가
    비어있으면(운영자가 아직 값을 안 채운 lab) 자동으로 빈 리스트를 반환해야 하기 때문.

    새 rule을 추가하려면 이 함수를 건드릴 필요 없이 rule_engine/registry.py에
    register(stage_id, category, compare_fn, collected_key) 한 줄만 추가하면 된다(Plugin 원칙).

    collected: {"lacp": {...}, "mlag": {...}, "ospf": {...}, ...} — 각 값은 해당
               comparator가 기대하는 파서 결과 형태(ParserStep이 registry 기반으로 채움).
    반환: {"stage_lacp": [Finding,...], "stage_mlag": [...], ...} (등록된 stage마다 하나씩)
    """
    result = {}
    for stage_id in rule_registry.list_registered():
        plugin = rule_registry.get(stage_id)
        checks = target_state.get(stage_id, {}).get("checks", [])
        collected_data = collected.get(plugin["collected_key"], {})
        verdicts = plugin["compare_fn"](checks, collected_data) if checks else []
        result[stage_id] = [Finding.from_verdict(project_id, session_id, plugin["category"], v) for v in verdicts]
    return result


def findings_to_verdicts(findings):
    """반대 방향 어댑터 — 기존 scorer.score_all()이 여전히 Verdict dict 리스트를 기대하므로
    과도기 동안 Finding -> Verdict 형태로 되돌려줌(scorer.py는 아직 안 건드림)."""
    return [
        {"check": f.check_id, "device": f.device, "result": f.result,
         "expected": f.expected, "actual": f.actual}
        for f in findings
    ]


def run_inspection_rules(project_id, session_id, inspection_rules, collected):
    findings = []
    for rule in inspection_rules or []:
        check_id = rule.get("id", rule.get("check_id", "inspection_rule"))
        for device, device_data in collected.items():
            actual = device_data.get(rule.get("field")) if isinstance(device_data, dict) else None
            findings.append(Finding(
                project_id=project_id, session_id=session_id, device=device,
                category=rule.get("category", "Inspection"), check_id=check_id,
                result=rule.get("result", "UNKNOWN"), severity=rule.get("severity", "Medium"),
                expected=rule.get("expected"), actual=actual, evidence=str(actual),
            ))
    return findings


if __name__ == "__main__":
    target_state = {
        "stage_vlan": {"checks": [
            {"id": "vlan_100_exists", "type": "in_set", "applies_to": ["Core1"], "expected_vlan_id": 100},
        ]},
        "stage_stp": {"checks": [
            {"id": "root_priority_vlan1_core1", "type": "exact_match", "device": "Core1",
             "field": "priority_vlan1", "expected": 4096},
        ]},
    }
    collected_vlan = {"Core1": {100: {}, 999: {}}}
    collected_stp = {"Core1": {1: {"configured_priority": 32768, "is_root": False}}}

    result = run_vlan_stp_rules("lab1_campus", "session_test", target_state, collected_vlan, collected_stp)
    for stage, findings in result.items():
        print(f"[{stage}]")
        for f in findings:
            print(f"  {f.check_id}: {f.result} (severity={f.severity}, source={f.source})")

    print("\n역변환(Verdict) 확인:")
    print(findings_to_verdicts(result["stage_stp"]))
