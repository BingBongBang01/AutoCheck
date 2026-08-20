"""MLAG 단계 비교 — check type: mlag_active."""


def compare_mlag_stage(checks, collected_mlag):
    """
    collected_mlag: {device: {"state": str, "negotiation_status": str, "is_active_full": bool, ...}}
                    (parsers/show_inventory_mlag_vrrp.parse_mlag 결과)
    check type: "mlag_active" — MLAG 페어가 Active/Connected 상태인지 확인.
    """
    results = []
    for check in checks:
        check_id = check["id"]
        for device in check.get("applies_to", []):
            mlag = collected_mlag.get(device)
            if not mlag:
                result, actual_desc = "UNKNOWN", "수집된 MLAG 데이터 없음"
            elif mlag.get("is_active_full"):
                result, actual_desc = "PASS", f"state={mlag.get('state')}, negotiation={mlag.get('negotiation_status')}"
            else:
                result, actual_desc = "FAIL", f"state={mlag.get('state')}, negotiation={mlag.get('negotiation_status')}"
            results.append({
                "check": f"{check_id}__{device}", "device": device, "result": result,
                "expected": "state=Active, negotiation=Connected", "actual": actual_desc,
            })
    return results
