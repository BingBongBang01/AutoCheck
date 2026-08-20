"""
show spanning-tree vlan <n> 출력을 파싱한다.
- 이 장비의 Bridge Priority(내가 설정한 값)
- 이 장비가 해당 VLAN의 root인지 여부(문구 "This bridge is the root" 존재 여부)
VLAN 번호는 인자로 받은 걸 그대로 키로 쓸 뿐, 특정 번호를 코드에 하드코딩하지 않는다.
"""
import re

BRIDGE_PRIORITY_RE = re.compile(r"Bridge ID\s+Priority\s+(\d+)")
IS_ROOT_RE = re.compile(r"This bridge is the root", re.IGNORECASE)
VLAN_HEADER_RE = re.compile(r"^\s*VLAN0*(\d+)", re.MULTILINE)


def parse(raw_output_per_vlan):
    """
    raw_output_per_vlan: {vlan_id(int): raw_text(str)}
    각 VLAN 섹션의 출력을 개별로 넘겨받는 구조 (show spanning-tree vlan <n> 을 vlan별로 실행했다고 가정)
    반환: {vlan_id: {"configured_priority": int|None, "is_root": bool}}
    """
    result = {}
    for vlan_id, text in raw_output_per_vlan.items():
        prio_match = BRIDGE_PRIORITY_RE.search(text)
        result[vlan_id] = {
            "configured_priority": int(prio_match.group(1)) if prio_match else None,
            "is_root": bool(IS_ROOT_RE.search(text)),
        }
    return result


def split_combined_vlan_output(raw_text):
    """
    'show spanning-tree vlan 1,100,200,999' 처럼 한 커맨드에 여러 VLAN 섹션이
    한 텍스트에 이어져 나오는 경우, VLAN 헤더 기준으로 잘라서
    {vlan_id: text} 형태로 재구성한다.
    """
    sections = {}
    matches = list(VLAN_HEADER_RE.finditer(raw_text))
    for i, m in enumerate(matches):
        vlan_id = int(m.group(1))
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        sections[vlan_id] = raw_text[start:end]
    return sections


def parse_combined(raw_text):
    """콤보 출력을 받아 바로 최종 결과까지 반환하는 편의 함수."""
    sections = split_combined_vlan_output(raw_text)
    return parse(sections)


if __name__ == "__main__":
    sample_vlan1 = """
VLAN0001
  Spanning tree enabled protocol mstp
  Root ID    Priority    4096
             Address     5001.0001.0000
             This bridge is the root
  Bridge ID  Priority    4096  (priority 4096 sys-id-ext 0)
             Address     5001.0001.0000
"""
    sample_vlan100_access1_wrong_root = """
VLAN0100
  Spanning tree enabled protocol mstp
  Root ID    Priority    32768
             Address     5001.0005.0000
             This bridge is the root
  Bridge ID  Priority    32768  (priority 32768 sys-id-ext 100)
             Address     5001.0005.0000
"""
    out = parse({1: sample_vlan1, 100: sample_vlan100_access1_wrong_root})
    print("파싱 결과:", out)
