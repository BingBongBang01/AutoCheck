"""OSPF 단계 비교 — check type: ospf_all_full / ospf_neighbor_full."""


def compare_ospf_stage(checks, collected_ospf):
    """
    collected_ospf: {device: [{"neighbor": ip, "state": str, "is_full": bool}, ...]}
                    (parsers/show_routing_neighbor.parse_ospf_neighbor 결과)
    check type: "ospf_all_full" — 해당 장비의 모든 OSPF 네이버가 FULL인지 확인.
              "ospf_neighbor_full" — 특정 네이버 IP 하나가 FULL인지 확인(neighbor_ip 지정 시).
    """
    results = []
    for check in checks:
        check_id = check["id"]
        neighbor_ip = check.get("neighbor_ip")
        for device in check.get("applies_to", []):
            neighbors = collected_ospf.get(device, [])
            if not neighbors:
                results.append({
                    "check": f"{check_id}__{device}", "device": device, "result": "UNKNOWN",
                    "expected": "OSPF 네이버 FULL", "actual": "수집된 OSPF 네이버 데이터 없음",
                })
                continue
            if neighbor_ip:
                target = next((n for n in neighbors if n["neighbor"] == neighbor_ip), None)
                if target is None:
                    result, actual_desc = "FAIL", f"네이버 {neighbor_ip} 없음"
                else:
                    result, actual_desc = ("PASS" if target["is_full"] else "FAIL"), f"{neighbor_ip}={target['state']}"
            else:
                not_full = [n for n in neighbors if not n["is_full"]]
                result = "PASS" if not not_full else "FAIL"
                actual_desc = "전체 FULL" if not not_full else f"미수렴: {not_full}"
            results.append({
                "check": f"{check_id}__{device}", "device": device, "result": result,
                "expected": "OSPF 네이버 FULL", "actual": actual_desc,
            })
    return results
