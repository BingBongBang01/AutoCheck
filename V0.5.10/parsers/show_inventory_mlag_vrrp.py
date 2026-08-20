"""show inventory / show mlag / show vrrp brief / show ip virtual-router 파싱."""
import re

# --- Inventory ---
SN_LINE_RE = re.compile(r"^\s*(\S+)\s+(\S+)\s+(\S+)\s*$")


def parse_inventory(raw_output):
    """반환: [{"slot": str, "model": str, "serial": str}] (헤더/구분선 제외한 3열 라인만)"""
    items = []
    for line in raw_output.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("slot", "----", "system", "port")):
            continue
        m = SN_LINE_RE.match(line)
        if m:
            items.append({"slot": m.group(1), "model": m.group(2), "serial": m.group(3)})
    return items


# --- MLAG ---
MLAG_FIELD_RE = re.compile(r"^\s*(State|Negotiation status|Peer-Link status|Local-int status)\s*:\s*(\S+)", re.IGNORECASE)
# 'MLAG Configuration:' 절의 값들 — 상태가 아니라 '무엇과 짝인지'를 말해 준다.
# 네트워크 구성도(engine/topology_builder.py)가 MLAG 쌍을 찾는 근거가 peer-link 의 Po 다:
#     peer-link  :  Port-Channel4093
# 이 Po 에 속한 물리 포트의 LLDP 이웃이 곧 peer 장비다.
# 'Peer-Link status' 와 구별해야 하므로 status 로 끝나지 않는 것만 잡는다.
MLAG_CONFIG_FIELD_RE = re.compile(
    r"^\s*(domain-id|local-interface|peer-link|peer-address)\s*:\s*(\S+)", re.IGNORECASE)


def parse_mlag(raw_output):
    """반환 예: {"state": "Active", "negotiation_status": "Connected",
                 "peer_link_status": "Up", "peer_link": "Port-Channel4093",
                 "peer_address": "10.10.10.3", "is_active_full": bool}

    config 절 키(peer_link/peer_address/domain_id/local_interface)는 나중에 더한 것이다 —
    기존 소비자(engine/comparators/mlag.py, engine/state_poller.py)는 상태 키만 읽으므로
    키가 늘어도 영향이 없다.
    """
    result = {}
    for line in raw_output.splitlines():
        m = MLAG_FIELD_RE.match(line)
        if m:
            key = m.group(1).lower().replace(" ", "_").replace("-", "_")
            result[key] = m.group(2)
            continue
        m = MLAG_CONFIG_FIELD_RE.match(line)
        if m:
            result[m.group(1).lower().replace("-", "_")] = m.group(2)
    result["is_active_full"] = (result.get("state", "").lower() == "active" and
                                 result.get("negotiation_status", "").lower() == "connected")
    return result


# --- VRRP / VARP ---
VRRP_STATE_RE = re.compile(r"^\s*(\S+)\s+.*?\b(Master|Backup|Initialize)\b", re.IGNORECASE)
VARP_UP_RE = re.compile(r"^\s*(\S+)\s+.*?\b(up|down)\b", re.IGNORECASE)


def parse_vrrp(raw_output):
    """반환: {interface: "Master"|"Backup"|"Initialize"}"""
    result = {}
    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("interface") or stripped.startswith("---"):
            continue  # 헤더/구분선 제외 (헤더에도 "Master"라는 단어가 포함돼 있어 오매칭 방지)
        m = VRRP_STATE_RE.match(line)
        if m:
            result[m.group(1)] = m.group(2).capitalize()
    return result


def check_vrrp_split_brain(vrrp_states_by_device):
    """여러 장비의 VRRP 상태를 모아, Master가 2대 이상(split-brain) 또는 0대(장애)인지 확인."""
    masters = [dev for dev, state in vrrp_states_by_device.items() if state == "Master"]
    if len(masters) > 1:
        return {"result": "FAIL", "reason": f"복수 Master 감지(split-brain 의심): {masters}"}
    if len(masters) == 0:
        return {"result": "UNKNOWN", "reason": "Master를 찾을 수 없음 — 수렴 중이거나 데이터 누락"}
    return {"result": "PASS", "reason": f"정상 Master: {masters[0]}"}


if __name__ == "__main__":
    inv_sample = """
Slot  Model            Serial
Switch DCS-7050CX3-32S-F JPE21484060
"""
    mlag_sample = """
MLAG Configuration:
domain-id           : mlag01
State                : Active
Negotiation status   : Connected
Peer-Link status     : Up
"""
    vrrp_sample = """
Interface  Group   Pri   Time   Own Pre  State    Master addr/Interface
Vlan100    1       100   3.6    N   Y    Master   10.100.0.1
"""
    print("Inventory:", parse_inventory(inv_sample))
    print("MLAG:", parse_mlag(mlag_sample))
    print("VRRP:", parse_vrrp(vrrp_sample))
    print("Split-brain 체크:", check_vrrp_split_brain({"Agg1": "Master", "Agg2": "Backup"}))
    print("Split-brain 체크(이상):", check_vrrp_split_brain({"Agg1": "Master", "Agg2": "Master"}))
