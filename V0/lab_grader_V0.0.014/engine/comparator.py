"""
target_state.yaml의 체크 항목을 실제 파싱 결과(collected)와 대조한다.
check_type: exact_match / in_set / absence 3종만 우선 구현 (numeric_threshold는 로드맵).

주의: "absence"는 Arista EOS 기본 브리지 우선순위(32768)를 기준값으로 간주한다.
      즉 "설정을 안 건드렸으면 기본값 그대로일 것"이라는 전제. 이 기본값은 설정으로 분리 가능.
"""

EOS_DEFAULT_BRIDGE_PRIORITY = 32768


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


def compare_stp_stage(checks, collected_stp, all_devices_stp_by_vlan):
    """
    collected_stp: {device: {vlan_id: {"configured_priority":.., "is_root": bool}}}
    all_devices_stp_by_vlan: {vlan_id: {device: {...}}}  (root 선출 교차검증용, 미리 재구성해서 전달)
    """
    results = []
    for check in checks:
        check_id = check["id"]
        check_type = check["type"]

        # --- 교차검증형 체크 (root_bridge_*) ---
        if check_id.startswith("actual_root_bridge_vlan"):
            vlan_id = int(check_id.replace("actual_root_bridge_vlan", ""))
            devices_for_vlan = all_devices_stp_by_vlan.get(vlan_id, {})
            root_candidates = [d for d, v in devices_for_vlan.items() if v.get("is_root")]

            if len(root_candidates) == 0:
                result, actual_desc = "UNKNOWN", "root로 표시된 장비 없음(STP 미수렴 중이거나 데이터 누락 가능성)"
            elif len(root_candidates) > 1:
                result, actual_desc = "FAIL", f"복수 장비가 동시에 root 주장(실제 이상 상황): {root_candidates}"
            else:
                actual_root = root_candidates[0]
                result = "PASS" if actual_root == check["expected"] else "FAIL"
                actual_desc = actual_root

            results.append({
                "check": check_id,
                "device": "(network-wide)",
                "result": result,
                "expected": check["expected"],
                "actual": actual_desc,
            })
            continue

        # --- 단일 장비 체크 ---
        device = check["device"]
        device_stp = collected_stp.get(device, {})

        if check_type == "exact_match":
            # field 예: "priority_vlan100" -> vlan_id=100
            field = check["field"]
            vlan_id = int(field.replace("priority_vlan", ""))
            actual = device_stp.get(vlan_id, {}).get("configured_priority")
            passed = actual == check["expected"]
            results.append({
                "check": check_id, "device": device,
                "result": "PASS" if passed else "FAIL",
                "expected": check["expected"], "actual": actual,
            })

        elif check_type == "absence":
            # 이 장비엔 명시적 priority 설정이 없어야 함 = 기본값(32768) 그대로여야 함
            # 주의: None(파싱 실패로 값을 못 찾음)과 32768(진짜 기본값)은 반드시 구분해야 함.
            #       구분 안 하면 파싱 실패를 "정상"으로 오판하는 사고가 남.
            unparsed_vlans = [vlan_id for vlan_id, v in device_stp.items() if v.get("configured_priority") is None]
            non_default = [
                vlan_id for vlan_id, v in device_stp.items()
                if v.get("configured_priority") is not None and v.get("configured_priority") != EOS_DEFAULT_BRIDGE_PRIORITY
            ]

            if not device_stp:
                result, actual_desc = "UNKNOWN", "수집된 STP 데이터 없음(장비 접속 실패 또는 커맨드 누락 가능성)"
            elif unparsed_vlans:
                result, actual_desc = "UNKNOWN", f"우선순위 파싱 실패 VLAN: {unparsed_vlans} (원본 출력 형식 확인 필요)"
            elif non_default:
                result, actual_desc = "FAIL", f"기본값 아닌 VLAN: {non_default}"
            else:
                result, actual_desc = "PASS", "기본값 유지"

            results.append({
                "check": check_id, "device": device,
                "result": result,
                "expected": f"모든 VLAN priority = 기본값({EOS_DEFAULT_BRIDGE_PRIORITY}) 또는 미설정",
                "actual": actual_desc,
            })

    return results


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


def compare_acl_stage(checks, collected_acl):
    """
    collected_acl: {device: {acl_name: [{"seq","action","rule","hit_count"}, ...]}}
                  (parsers/show_acl.parse 결과)
    check type: "acl_exists" — 지정된 ACL이 장비에 설정돼 있는지 확인.
              "acl_has_explicit_deny_any" — 마지막 방어선(deny ip any any류)이 명시돼 있는지 확인.
    """
    from parsers.show_acl import acl_exists, has_explicit_deny_any
    results = []
    for check in checks:
        check_id = check["id"]
        check_type = check["type"]
        acl_name = check["acl_name"]
        for device in check.get("applies_to", []):
            device_acls = collected_acl.get(device, {})
            if not device_acls:
                results.append({
                    "check": f"{check_id}__{device}", "device": device, "result": "UNKNOWN",
                    "expected": f"ACL {acl_name} 설정 확인", "actual": "수집된 ACL 데이터 없음",
                })
                continue
            exists = acl_exists(device_acls, acl_name)
            if check_type == "acl_exists":
                results.append({
                    "check": f"{check_id}__{device}", "device": device,
                    "result": "PASS" if exists else "FAIL",
                    "expected": f"ACL {acl_name} 존재", "actual": "존재함" if exists else "존재하지 않음",
                })
            elif check_type == "acl_has_explicit_deny_any":
                if not exists:
                    results.append({
                        "check": f"{check_id}__{device}", "device": device, "result": "FAIL",
                        "expected": f"ACL {acl_name}에 명시적 deny any any", "actual": f"ACL {acl_name} 자체가 없음",
                    })
                    continue
                has_deny = has_explicit_deny_any(device_acls[acl_name])
                results.append({
                    "check": f"{check_id}__{device}", "device": device,
                    "result": "PASS" if has_deny else "FAIL",
                    "expected": f"ACL {acl_name}에 명시적 deny any any",
                    "actual": "있음" if has_deny else "없음(암묵적 permit 위험)",
                })
    return results


def build_vlan_index(collected_stp):
    """{device: {vlan_id: {...}}} -> {vlan_id: {device: {...}}} 재구성 (root 교차검증용)"""
    index = {}
    for device, vlans in collected_stp.items():
        for vlan_id, data in vlans.items():
            index.setdefault(vlan_id, {})[device] = data
    return index
