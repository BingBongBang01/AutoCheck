"""
show vlan brief 출력을 파싱한다.
VLAN 번호가 몇 번이든 상관없이, 화면에 있는 걸 그대로 구조화한다 (하드코딩 없음).
"""
import re

VLAN_LINE_RE = re.compile(r"^\s*(\d+)\s+(\S+)\s+(\w+)")


def parse(raw_output):
    """반환: {vlan_id(int): {"name": str, "status": str}}"""
    vlans = {}
    for line in raw_output.splitlines():
        m = VLAN_LINE_RE.match(line)
        if m:
            vlan_id, name, status = m.groups()
            vlans[int(vlan_id)] = {"name": name, "status": status}
    return vlans


if __name__ == "__main__":
    sample = """
VLAN  Name                             Status    Ports
----- -------------------------------- --------- -------------------------------
1     default                          active    Et3, Et4
100   USER                             active    Et1
200   SERVER                           active
999   NATIVE_UNUSED                    active    Et1, Et2, Et3, Et4
"""
    result = parse(sample)
    print("파싱 결과:", result)
