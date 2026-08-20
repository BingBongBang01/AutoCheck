"""VLAN 단계 비교 — check_type: exact_match(VLAN 존재 여부)."""


def compare_vlan_stage(checks, collected):
    """collected: {device: {vlan_id(int): {...}}}  (show_vlan.parse 결과)"""
    results = []
    for check in checks:
        vlan_id = check["expected_vlan_id"]
        for device in check["applies_to"]:
            device_vlans = collected.get(device, {})
            passed = vlan_id in device_vlans
            results.append({
                "check": f"{check['id']}__{device}",
                "device": device,
                "result": "PASS" if passed else "FAIL",
                "expected": f"VLAN {vlan_id} 존재",
                "actual": "존재함" if passed else "존재하지 않음",
            })
    return results
