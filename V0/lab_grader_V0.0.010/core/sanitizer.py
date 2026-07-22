"""
Sanitizer — Cloud AI로 나가는 데이터에만 적용되는 마스킹 계층.
원본(Finding, raw_by_device, history)은 절대 수정 안 함 — 이 함수가 만든 '사본'만
Cloud Provider로 전달된다. Local AI/규칙기반/Report/History는 원본 그대로 사용.

마스킹 대상(문서 10번): Hostname, IP, MAC, Customer Name, Username, Password,
SNMP Community, Serial, License, Contract 등.
"""
import re
import copy

IP_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
MAC_RE = re.compile(r"\b[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\b")  # Arista 표기(xxxx.xxxx.xxxx)
MAC_COLON_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")
SERIAL_RE = re.compile(r"\b[A-Z0-9]{8,}\b")   # 대문자+숫자 8자 이상(S/N 패턴 근사치)

MASK = "****"


def _mask_text(text, hostnames=None):
    if not isinstance(text, str):
        return text
    text = IP_RE.sub(MASK, text)
    text = MAC_RE.sub(MASK, text)
    text = MAC_COLON_RE.sub(MASK, text)
    if hostnames:
        for name in hostnames:
            if name:
                text = re.sub(re.escape(name), MASK, text, flags=re.IGNORECASE)
    return text


def mask_finding(finding_dict, hostnames=None):
    """Finding.to_dict() 결과(또는 유사 dict)를 받아 마스킹된 사본을 반환. 원본 불변."""
    masked = copy.deepcopy(finding_dict)
    sensitive_text_fields = ["device", "evidence", "recommendation"]
    for field_name in sensitive_text_fields:
        if field_name in masked:
            masked[field_name] = _mask_text(masked[field_name], hostnames)

    for value_field in ["expected", "actual"]:
        if isinstance(masked.get(value_field), str):
            masked[value_field] = _mask_text(masked[value_field], hostnames)

    return masked


def mask_findings(findings, hostnames=None):
    """
    findings: Finding 객체 리스트 또는 dict 리스트 둘 다 허용.
    hostnames: 마스킹해야 할 실제 장비명 목록(Device Inventory에서 가져와 넘김) —
               정규식만으론 "Core1" 같은 이름이 안 잡히므로 명시적으로 넘겨받아야 함.
    """
    result = []
    for f in findings:
        d = f.to_dict() if hasattr(f, "to_dict") else f
        result.append(mask_finding(d, hostnames))
    return result


def mask_summary_text(text, hostnames=None):
    """자유 텍스트(요약문 등)에 대한 마스킹 — Finding 아닌 문자열 전체를 넘길 때 사용."""
    return _mask_text(text, hostnames)


if __name__ == "__main__":
    from core.finding import Finding

    f = Finding(
        project_id="lab1_campus", session_id="s1", device="Core1", category="STP",
        check_id="actual_root_bridge_vlan1", result="FAIL", severity="CRITICAL",
        evidence="실제 root: Core2 (172.30.1.102), MAC 5001.0002.0000",
        expected="Core1", actual="Core2",
    )
    masked = mask_finding(f.to_dict(), hostnames=["Core1", "Core2"])
    print("원본 evidence:", f.evidence)
    print("마스킹 evidence:", masked["evidence"])
    print("원본 device:", f.device, "-> 마스킹 device:", masked["device"])
    print("원본 Finding 불변 확인(evidence 그대로):", f.evidence)
