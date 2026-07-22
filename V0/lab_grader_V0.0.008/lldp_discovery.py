"""
.unl(설계도) 대신 실제 장비의 show lldp neighbors 출력을 모아 as-built 토폴로지를 재구성.
unl_parser의 discovery와 다른 지점: 여기는 "지금 실제로 뭐가 연결돼 있는지"를 살아있는 장비에서 직접 확인.
"""
import re

LLDP_LINE_RE = re.compile(r"^(\S+)\s+(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s*$")


def parse_lldp_neighbors(raw_output):
    """반환: [{"local_port": str, "neighbor_device": str, "neighbor_port": str}]"""
    result = []
    for line in raw_output.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("port", "device", "----", "last")):
            continue
        m = LLDP_LINE_RE.match(line)
        if m:
            local_port, neighbor_device = m.group(1), m.group(2)
            neighbor_port = m.group(5)
            result.append({"local_port": local_port, "neighbor_device": neighbor_device, "neighbor_port": neighbor_port})
    return result


def build_topology_from_lldp(lldp_by_device):
    """
    {device_name: raw_lldp_output} 전체를 모아 중복 제거된 링크 목록으로 재구성.
    (A-B, B-A로 양쪽에서 잡히는 걸 링크 1개로 합침)
    """
    edges = set()
    for device, raw in lldp_by_device.items():
        for entry in parse_lldp_neighbors(raw):
            pair = tuple(sorted([
                f"{device}:{entry['local_port']}",
                f"{entry['neighbor_device']}:{entry['neighbor_port']}",
            ]))
            edges.add(pair)
    return [{"a": a, "b": b} for a, b in sorted(edges)]


def diff_lldp_vs_designed(lldp_links, designed_links):
    """
    lldp_links: build_topology_from_lldp() 결과
    designed_links: unl_parser의 physical_links(설계도 기준)를 "device:port" 형태로 정규화한 것
    반환: {"matched": [...], "missing": [...], "unexpected": [...]}
    """
    def normalize(link_dict, a_key="a", b_key="b"):
        return tuple(sorted([link_dict[a_key], link_dict[b_key]]))

    lldp_set = {normalize(l) for l in lldp_links}
    designed_set = {normalize(l) for l in designed_links}

    return {
        "matched": sorted(lldp_set & designed_set),
        "missing": sorted(designed_set - lldp_set),     # 설계했는데 실제 배선엔 없음
        "unexpected": sorted(lldp_set - designed_set),   # 설계에 없는데 실제로 연결된 것(오배선 의심)
    }


if __name__ == "__main__":
    core1_lldp = """
Port    Device ID    Hold-time  Capability  Port ID
Et1     Core2         120        B           Et1
Et3     Agg1          120        B           Et3
"""
    core2_lldp = """
Port    Device ID    Hold-time  Capability  Port ID
Et1     Core1         120        B           Et1
"""
    topo = build_topology_from_lldp({"Core1": core1_lldp, "Core2": core2_lldp})
    print("LLDP 기반 실토폴로지:", topo)

    designed = [{"a": "Core1:Et1", "b": "Core2:Et1"}, {"a": "Core1:Et3", "b": "Agg1:Et3"},
                {"a": "Core1:Et99", "b": "Agg2:Et99"}]  # 설계엔 있지만 실제 안 잡힐 가짜 링크
    diff = diff_lldp_vs_designed(topo, designed)
    print("일치:", diff["matched"])
    print("설계했으나 실배선 없음(missing):", diff["missing"])
    print("설계에 없는 예상외 연결(unexpected):", diff["unexpected"])
