"""VRRP 단계 비교 — check type: vrrp_single_master."""


def compare_vrrp_stage(checks, collected_vrrp):
    """
    collected_vrrp: {device: {interface: "Master"/"Backup"/"Initialize"}}
                    (parsers/show_inventory_mlag_vrrp.parse_vrrp 결과)
    check type: "vrrp_single_master" — group(장비 목록) 안에서 지정 interface의 Master가 정확히 1대인지 확인.
    """
    from parsers.show_inventory_mlag_vrrp import check_vrrp_split_brain
    results = []
    for check in checks:
        check_id = check["id"]
        interface = check["interface"]
        group = check.get("group", [])
        states_by_device = {}
        missing = []
        for device in group:
            state = collected_vrrp.get(device, {}).get(interface)
            if state is None:
                missing.append(device)
            else:
                states_by_device[device] = state
        if not states_by_device:
            result, actual_desc = "UNKNOWN", f"수집된 VRRP 데이터 없음(대상: {group})"
        else:
            verdict = check_vrrp_split_brain(states_by_device)
            result, actual_desc = verdict["result"], verdict["reason"]
            if missing:
                actual_desc += f" (데이터 누락: {missing})"
        results.append({
            "check": f"{check_id}__{interface}", "device": "(group)", "result": result,
            "expected": f"{interface}의 Master는 정확히 1대", "actual": actual_desc,
        })
    return results
