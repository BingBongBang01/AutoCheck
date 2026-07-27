"""show snmp 출력을 파싱한다."""
import re

COMMUNITY_RE = re.compile(r"^\s*(\S+)\s+(RO|RW)\s*$", re.IGNORECASE)
LOCATION_RE = re.compile(r"^\s*Location\s*:\s*(.*)$", re.IGNORECASE)
CONTACT_RE = re.compile(r"^\s*Contact\s*:\s*(.*)$", re.IGNORECASE)


def parse(raw_output):
    """반환: {"enabled": bool, "location": str, "contact": str, "communities": [str]}"""
    location, contact = "", ""
    communities = []
    for line in raw_output.splitlines():
        loc_m = LOCATION_RE.match(line)
        if loc_m:
            location = loc_m.group(1).strip()
            continue
        con_m = CONTACT_RE.match(line)
        if con_m:
            contact = con_m.group(1).strip()
            continue
        comm_m = COMMUNITY_RE.match(line)
        if comm_m:
            communities.append(comm_m.group(1))
    text = raw_output.lower()
    enabled = "snmp agent enabled" in text or bool(communities)
    return {"enabled": enabled, "location": location, "contact": contact, "communities": communities}


if __name__ == "__main__":
    sample = """
SNMP agent enabled: Yes
Location: DC1-Rack3
Contact: noc@example.com
public       RO
private      RW
"""
    print("파싱 결과:", parse(sample))
