"""show port-channel summary 파싱."""
import re

PO_LINE_RE = re.compile(r"^\s*(Po\d+)\s*\((\w+)\)\s*(.*)$")
MEMBER_RE = re.compile(r"(\S+)\((\w)\)")


def parse(raw_output):
    """반환: {"Po1": {"status": "U"(사용중)/"D"(다운) 등, "members": {"Et1": "P"(bundled)/"D"/"s" 등}}}"""
    result = {}
    for line in raw_output.splitlines():
        m = PO_LINE_RE.match(line)
        if m:
            po_name, status, rest = m.groups()
            members = {iface: flag for iface, flag in MEMBER_RE.findall(rest)}
            result[po_name] = {"status": status, "members": members}
    return result


def has_degraded_member(portchannel_info):
    """멤버 중 하나라도 Bundled(P)가 아니면 이중화 저하로 판정."""
    degraded = []
    for po_name, info in portchannel_info.items():
        bad_members = [iface for iface, flag in info["members"].items() if flag != "P"]
        if bad_members:
            degraded.append({"portchannel": po_name, "degraded_members": bad_members})
    return degraded


if __name__ == "__main__":
    sample = """
Flags: U - Up, D - Down, N - Not in use, ...

Port Channel                 Protocol
Po1(U)      LACP    Et1(P) Et2(P)
Po10(U)     LACP    Et3(P) Et4(D)
"""
    parsed = parse(sample)
    print("파싱:", parsed)
    print("이중화 저하:", has_degraded_member(parsed))
