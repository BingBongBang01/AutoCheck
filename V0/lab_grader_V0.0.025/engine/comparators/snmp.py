"""SNMP 단계 비교 — check type: snmp_enabled."""


def compare_snmp_stage(checks, collected_snmp):
    """
    collected_snmp: {device: {"enabled": bool, "location": str, "contact": str, "communities": [str]}}
                    (parsers/show_snmp.parse 결과)
    check type: "snmp_enabled" — SNMP 에이전트가 활성화돼있는지 확인.
    """
    results = []
    for check in checks:
        check_id = check["id"]
        for device in check.get("applies_to", []):
            snmp = collected_snmp.get(device)
            if not snmp:
                result, actual_desc = "UNKNOWN", "수집된 SNMP 데이터 없음"
            elif snmp.get("enabled"):
                result, actual_desc = "PASS", f"communities={snmp.get('communities')}"
            else:
                result, actual_desc = "FAIL", "SNMP 비활성화"
            results.append({
                "check": f"{check_id}__{device}", "device": device, "result": result,
                "expected": "SNMP 활성화됨", "actual": actual_desc,
            })
    return results
