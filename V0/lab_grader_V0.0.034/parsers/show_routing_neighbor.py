"""
OSPF/BGP/EVPN 네이버 상태 파싱 — 프로토콜별 출력 포맷은 다르지만
"정상 상태 키워드가 하나 있고 나머지는 전부 비정상"이라는 공통 구조를 이용한 범용 파서.
"""
import re

OSPF_NEIGHBOR_RE = re.compile(r"^\s*(\d+\.\d+\.\d+\.\d+)\s+\d+\s+(\S+)\s+", re.MULTILINE)
BGP_NEIGHBOR_RE = re.compile(r"^\s*(\d+\.\d+\.\d+\.\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\S+\s+(\S+)\s*$", re.MULTILINE)

OSPF_NORMAL_STATES = {"full"}
BGP_ABNORMAL_KEYWORDS = {"idle", "active", "connect", "opensent", "openconfirm"}  # 이 단어면 비정상, 숫자(prefix 개수)면 정상


def parse_ospf_neighbor(raw_output):
    """반환: [{"neighbor": ip, "state": str, "is_full": bool}]"""
    result = []
    for m in OSPF_NEIGHBOR_RE.finditer(raw_output):
        neighbor_ip, state = m.groups()
        state_clean = state.split("/")[0].lower()
        result.append({"neighbor": neighbor_ip, "state": state_clean, "is_full": state_clean in OSPF_NORMAL_STATES})
    return result


def parse_bgp_summary(raw_output):
    """
    show ip bgp summary 마지막 컬럼(State/PfxRcd)이 숫자면 Established(정상),
    Idle/Active 등 문자열이면 비정상.
    """
    result = []
    for m in BGP_NEIGHBOR_RE.finditer(raw_output):
        neighbor_ip, state_or_count = m.groups()
        is_established = state_or_count.isdigit()
        result.append({
            "neighbor": neighbor_ip,
            "state": "Established" if is_established else state_or_count,
            "is_established": is_established,
        })
    return result


def parse_evpn_summary(raw_output):
    """EVPN은 BGP 세션 위에서 동작 — BGP summary와 동일 포맷으로 파싱 가능."""
    return parse_bgp_summary(raw_output)


VTEP_RE = re.compile(r"Vxlan1.*?Source Interface:\s*(\S+).*?VTEP:\s*([\d.]+)", re.DOTALL)


def parse_vxlan_vtep(raw_output):
    """show interfaces vxlan 1 에서 VTEP(로컬 터널엔드포인트) IP 확인."""
    m = VTEP_RE.search(raw_output)
    if m:
        return {"source_interface": m.group(1), "vtep_ip": m.group(2), "configured": True}
    return {"configured": False}


if __name__ == "__main__":
    ospf_sample = """
Neighbor ID     Pri   State           Dead Time   Address         Interface
10.0.0.1        1     FULL/BDR        00:00:38    10.0.0.1        Ethernet1
10.0.0.2        1     2-WAY/DR        00:00:32    10.0.0.2        Ethernet2
"""
    bgp_sample = """
Neighbor        V  AS      MsgRcvd  MsgSent  TblVer  InQ  OutQ  Up/Down  State/PfxRcd
10.1.1.1        4  65001   1200     1180     50      0    0     3d02h    120
10.1.1.2        4  65002   0        0        0       0    0     never    Idle
"""
    print("OSPF:", parse_ospf_neighbor(ospf_sample))
    print("BGP:", parse_bgp_summary(bgp_sample))
