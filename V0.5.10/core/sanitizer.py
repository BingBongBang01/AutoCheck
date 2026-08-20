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

def mask_findings_with_mapping(findings, hostnames=None):
    mapping = {}
    values = set(hostnames or [])
    for finding in findings:
        item = finding.to_dict() if hasattr(finding, "to_dict") else finding
        values.update(str(item.get(field)) for field in ("device", "expected", "actual") if item.get(field))
    tokens = {value: f"ENTITY_{index:03d}" for index, value in enumerate(sorted(values), 1)}
    pattern = re.compile("|".join(re.escape(value) for value in sorted(values, key=len, reverse=True))) if values else None

    def replace(value):
        if not isinstance(value, str):
            return value
        value = IP_RE.sub(lambda match: tokens.setdefault(match.group(), f"ENTITY_{len(tokens) + 1:03d}"), value)
        value = MAC_RE.sub(lambda match: tokens.setdefault(match.group(), f"ENTITY_{len(tokens) + 1:03d}"), value)
        value = MAC_COLON_RE.sub(lambda match: tokens.setdefault(match.group(), f"ENTITY_{len(tokens) + 1:03d}"), value)
        return pattern.sub(lambda match: tokens[match.group()], value) if pattern else value

    result = []
    for finding in findings:
        item = copy.deepcopy(finding.to_dict() if hasattr(finding, "to_dict") else finding)
        for field, value in list(item.items()):
            item[field] = replace(value)
        result.append(item)
    mapping = {token: value for value, token in tokens.items()}
    return result, mapping


_hostname_pattern_cache = {}


def _hostname_pattern(hostnames):
    """hostnames(장비명 목록)를 하나의 alternation 정규식으로 합쳐서 캐싱.
    finding/field마다 매번 이름 개수만큼 정규식을 새로 컴파일하지 않도록(호출 빈도가
    findings 수 x sensitive 필드 수만큼 반복되므로) 같은 목록이면 컴파일 결과를 재사용한다."""
    key = tuple(sorted(name for name in hostnames if name))
    if not key:
        return None
    pattern = _hostname_pattern_cache.get(key)
    if pattern is None:
        pattern = re.compile("|".join(re.escape(name) for name in key), re.IGNORECASE)
        _hostname_pattern_cache[key] = pattern
    return pattern


def _mask_text(text, hostnames=None):
    if not isinstance(text, str):
        return text
    text = IP_RE.sub(MASK, text)
    text = MAC_RE.sub(MASK, text)
    text = MAC_COLON_RE.sub(MASK, text)
    if hostnames:
        pattern = _hostname_pattern(hostnames)
        if pattern:
            text = pattern.sub(MASK, text)
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
        check_id="actual_root_bridge_vlan1", result="FAIL", severity="Critical",
        evidence="실제 root: Core2 (172.30.1.102), MAC 5001.0002.0000",
        expected="Core1", actual="Core2",
    )
    masked = mask_finding(f.to_dict(), hostnames=["Core1", "Core2"])
    print("원본 evidence:", f.evidence)
    print("마스킹 evidence:", masked["evidence"])
    print("원본 device:", f.device, "-> 마스킹 device:", masked["device"])
    print("원본 Finding 불변 확인(evidence 그대로):", f.evidence)
