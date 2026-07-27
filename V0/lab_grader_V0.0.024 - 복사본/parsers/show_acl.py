"""show ip access-lists 파싱 — ACL명·ACE(순번/permit·deny/프로토콜/매치카운트) 추출."""
import re

ACL_HEADER_RE = re.compile(r"^\s*IP Access List\s+(\S+)", re.IGNORECASE)
ENTRY_RE = re.compile(r"^\s*(\d+)\s+(permit|deny)\s+(.+?)(?:\s+\[(\d+)\s+match(?:es)?\])?\s*$", re.IGNORECASE)


def parse(raw_output):
    """반환: {acl_name: [{"seq": int, "action": "permit"/"deny", "rule": str, "hit_count": int|None}, ...]}"""
    result = {}
    current = None
    for line in raw_output.splitlines():
        header = ACL_HEADER_RE.match(line)
        if header:
            current = header.group(1)
            result[current] = []
            continue
        if current is None:
            continue
        m = ENTRY_RE.match(line)
        if m:
            seq, action, rule, hits = m.groups()
            result[current].append({
                "seq": int(seq), "action": action.lower(), "rule": rule.strip(),
                "hit_count": int(hits) if hits is not None else None,
            })
    return result


def acl_exists(parsed, acl_name):
    return acl_name in parsed


def has_explicit_deny_any(acl_entries):
    """마지막 방어선으로 'deny ip any any' 류 명시적 규칙이 있는지 (없으면 EOS 암묵적 permit 위험 가능성 검토용)."""
    return any(e["action"] == "deny" and re.match(r"ip\s+any\s+any", e["rule"], re.IGNORECASE) for e in acl_entries)


def unused_deny_rules(acl_entries):
    """hit_count가 0인 deny 규칙 — 카운터가 수집된 경우에만 의미 있음(hit_count 전부 None이면 빈 리스트)."""
    return [e for e in acl_entries if e["action"] == "deny" and e["hit_count"] == 0]


if __name__ == "__main__":
    sample = """
IP Access List MGMT-ACL
        10 permit tcp any any eq ssh [120 matches]
        20 permit tcp any any eq 443 [0 matches]
        30 deny ip any any log [5 matches]
IP Access List OPEN-ACL
        10 permit ip any any
"""
    parsed = parse(sample)
    print("파싱:", parsed)
    print("MGMT-ACL 존재:", acl_exists(parsed, "MGMT-ACL"))
    print("명시적 deny any any:", has_explicit_deny_any(parsed["MGMT-ACL"]))
    print("사용 안 된 deny:", unused_deny_rules(parsed["MGMT-ACL"]))
    print("OPEN-ACL 명시적 deny any any:", has_explicit_deny_any(parsed["OPEN-ACL"]))
