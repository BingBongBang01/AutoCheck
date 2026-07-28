"""ACL 단계 비교 — check type: acl_exists / acl_has_explicit_deny_any."""


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
