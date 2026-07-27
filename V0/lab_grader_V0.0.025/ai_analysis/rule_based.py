"""
규칙기반 분석 — 네트워크·API 키 없이도 항상 동작하는 최종 폴백.
채점 결과(scored)를 받아 요약 문장·이상장비 목록·우선순위·조치권고를 생성.
"""

# 흔한 실패 패턴 -> 조치 권고 매핑 (키워드 기반, 실제 CRC/root선출 사례 반영)
ACTION_HINTS = {
    "root_priority": "STP priority 설정이 아직 반영 안 됐을 가능성 — 설정 재확인 및 재수렴 대기",
    "actual_root_bridge": "설계와 다른 장비가 root로 선출됨 — priority 설정 확인, 재수렴 시간(수십 초) 경과 후 재확인",
    "vlan": "VLAN 설정 누락 — 해당 장비에 VLAN 생성 여부 확인",
    "mlag": "MLAG 세션 다운 — peer-link 물리연결·설정 확인",
    "vrrp": "VRRP/VARP 이중화 이상 — Master 중복 또는 부재 확인",
    "bgp": "BGP 세션 다운 — 피어 설정·경로 확인",
    "ospf": "OSPF 인접성 다운 — MTU/네트워크 타입/영역 설정 확인",
    "crc": "케이블/트랜시버 불량 의심 — 광파워 및 케이블 점검",
    "power": "전원 이중화 상실 — PSU 상태 확인",
    "fan": "팬 고장 — 냉각 이중화 상실, 하드웨어 점검 필요",
}


def summarize(scored):
    """전체 요약 문장 하나."""
    total_pass = sum(s["pass"] for s in scored if s["status"] not in ("SKIPPED", "NOT_STARTED"))
    total_all = sum(s["total"] for s in scored if s["status"] not in ("SKIPPED", "NOT_STARTED"))
    fail_stages = [s["label"] for s in scored if s["status"] == "IN_PROGRESS"]
    complete_stages = [s["label"] for s in scored if s["status"] == "COMPLETE"]

    pct = round(100 * total_pass / total_all) if total_all else 0
    summary = f"전체 {total_pass}/{total_all}건 PASS({pct}%)."
    if complete_stages:
        summary += f" 완료 단계: {', '.join(complete_stages)}."
    if fail_stages:
        summary += f" 미해결 단계: {', '.join(fail_stages)}."
    return summary


def detect_anomalies(scored):
    """FAIL/UNKNOWN 항목만 모아서 device별로 그룹핑."""
    anomalies = []
    for stage in scored:
        for r in stage.get("results", []):
            if r["result"] in ("FAIL", "UNKNOWN"):
                anomalies.append({
                    "stage": stage["label"], "device": r.get("device", "-"),
                    "check": r["check"], "result": r["result"],
                    "expected": r.get("expected"), "actual": r.get("actual"),
                })
    return anomalies


def suggest_action(check_name):
    """체크 이름의 키워드로 조치권고 문장 찾기 — 못 찾으면 일반 안내."""
    check_lower = check_name.lower()
    for keyword, hint in ACTION_HINTS.items():
        if keyword in check_lower:
            return hint
    return "패턴 사전에 없는 새로운 이상 — 수동 확인 필요 (사전 확장 대상)"


def prioritize(anomalies):
    """
    우선순위 규칙: network-wide(교차검증) 항목 > 특정 장비 다건 실패 > 단건 실패 순.
    반환: anomalies를 우선순위 내림차순으로 정렬한 리스트 + priority 필드 추가.
    """
    device_fail_count = {}
    for a in anomalies:
        device_fail_count[a["device"]] = device_fail_count.get(a["device"], 0) + 1

    def score(a):
        s = 0
        if a["device"] == "(network-wide)":
            s += 100
        s += device_fail_count.get(a["device"], 0) * 10
        if a["result"] == "FAIL":
            s += 5
        return s

    ranked = sorted(anomalies, key=score, reverse=True)
    for i, a in enumerate(ranked):
        a["priority"] = i + 1
        a["suggested_action"] = suggest_action(a["check"])
    return ranked


def analyze(scored):
    """전체 파이프라인 — summarize + anomalies + priority + action, 전부 규칙기반(항상 동작)."""
    anomalies = detect_anomalies(scored)
    ranked = prioritize(anomalies)
    return {
        "source": "rule_based",
        "summary": summarize(scored),
        "anomaly_count": len(ranked),
        "top_priority": ranked[:5],
        "all_anomalies": ranked,
    }


if __name__ == "__main__":
    sample_scored = [
        {"label": "VLAN", "status": "COMPLETE", "pass": 18, "total": 18, "results": []},
        {"label": "STP", "status": "IN_PROGRESS", "pass": 3, "total": 14, "results": [
            {"stage": "STP", "device": "Core1", "check": "root_priority_vlan1_core1", "result": "FAIL", "expected": 4096, "actual": 32768},
            {"stage": "STP", "device": "(network-wide)", "check": "actual_root_bridge_vlan1", "result": "FAIL", "expected": "Core1", "actual": "Core2"},
            {"stage": "STP", "device": "Agg1", "check": "root_priority_vlan100_agg1", "result": "FAIL", "expected": 4096, "actual": 32768},
        ]},
    ]
    result = analyze(sample_scored)
    print("요약:", result["summary"])
    print("이상 건수:", result["anomaly_count"])
    print("우선순위 top:")
    for a in result["top_priority"]:
        print(f"  [{a['priority']}] {a['device']} / {a['check']} -> {a['suggested_action']}")
