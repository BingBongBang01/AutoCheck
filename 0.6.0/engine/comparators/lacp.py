"""LACP/Port-Channel 단계 비교 — check type: lacp_no_degraded."""


def compare_lacp_stage(checks, collected_po):
    """
    collected_po: {device: {"Po1": {"status": "U"/"D", "members": {iface: "P"/"D"/"s"}}, ...}}
                  (parsers/show_port_channel.parse 결과)
    check type: "lacp_no_degraded" — 지정된 Port-Channel의 모든 멤버가 Bundled(P)인지 확인.
    """
    from parsers.show_port_channel import has_degraded_member
    results = []
    for check in checks:
        check_id = check["id"]
        for device in check.get("applies_to", []):
            po_info = collected_po.get(device, {})
            if not po_info:
                results.append({
                    "check": f"{check_id}__{device}", "device": device, "result": "UNKNOWN",
                    "expected": "모든 Port-Channel 멤버 Bundled(P)", "actual": "수집된 LACP 데이터 없음",
                })
                continue
            degraded = has_degraded_member(po_info)
            po_filter = check.get("port_channel")
            if po_filter:
                degraded = [d for d in degraded if d["portchannel"] == po_filter]
            passed = not degraded
            results.append({
                "check": f"{check_id}__{device}", "device": device,
                "result": "PASS" if passed else "FAIL",
                "expected": "모든 Port-Channel 멤버 Bundled(P)",
                "actual": "정상" if passed else f"저하됨: {degraded}",
            })
    return results
