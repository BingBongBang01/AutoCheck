"""STP 단계 비교 — root bridge 교차검증(network-wide) + 단일 장비 exact_match/absence.

주의: "absence"는 Arista EOS 기본 브리지 우선순위(32768)를 기준값으로 간주한다.
      즉 "설정을 안 건드렸으면 기본값 그대로일 것"이라는 전제. 이 기본값은 설정으로 분리 가능.
"""

EOS_DEFAULT_BRIDGE_PRIORITY = 32768


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


def build_vlan_index(collected_stp):
    """{device: {vlan_id: {...}}} -> {vlan_id: {device: {...}}} 재구성 (root 교차검증용)"""
    index = {}
    for device, vlans in collected_stp.items():
        for vlan_id, data in vlans.items():
            index.setdefault(vlan_id, {})[device] = data
    return index
