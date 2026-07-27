"""NTP 단계 비교 — check type: ntp_synchronized."""


def compare_ntp_stage(checks, collected_ntp):
    """
    collected_ntp: {device: {"synchronized": bool, "stratum": int|None, "server": str|None}}
                    (parsers/show_ntp.parse 결과)
    check type: "ntp_synchronized" — 장비가 NTP 서버와 동기화됐는지 확인.
    """
    results = []
    for check in checks:
        check_id = check["id"]
        for device in check.get("applies_to", []):
            ntp = collected_ntp.get(device)
            if not ntp:
                result, actual_desc = "UNKNOWN", "수집된 NTP 데이터 없음"
            elif ntp.get("synchronized"):
                result, actual_desc = "PASS", f"server={ntp.get('server')}, stratum={ntp.get('stratum')}"
            else:
                result, actual_desc = "FAIL", "동기화 안 됨(unsynchronized)"
            results.append({
                "check": f"{check_id}__{device}", "device": device, "result": result,
                "expected": "NTP 동기화됨", "actual": actual_desc,
            })
    return results
