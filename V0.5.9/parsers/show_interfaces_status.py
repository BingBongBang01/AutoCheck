"""
show interface status (Status 컬럼: connected/notconnect/errdisabled 등)
show interfaces counters errors (FCS/CRC 등)
show interfaces counters discards (Discard 카운트)
"""
import re

STATUS_LINE_RE = re.compile(r"^(\S+)\s+.*?\s+(connected|notconnect|disabled|errdisabled|notconnected)\b", re.IGNORECASE)
ERRORS_LINE_RE = re.compile(r"^(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$")
DISCARDS_LINE_RE = re.compile(r"^(\S+)\s+(\d+)\s+(\d+)\s*$")


def parse_status(raw_output):
    """반환: {interface: status}"""
    result = {}
    for line in raw_output.splitlines():
        m = STATUS_LINE_RE.match(line.strip())
        if m:
            result[m.group(1)] = m.group(2).lower()
    return result


def parse_counters_errors(raw_output):
    """반환: {interface: {"fcs": int, "align": int, "symbol": int, "rx": int, "runts": int, "giants": int}}"""
    result = {}
    for line in raw_output.splitlines():
        m = ERRORS_LINE_RE.match(line.strip())
        if m:
            iface = m.group(1)
            result[iface] = {
                "fcs": int(m.group(2)), "align": int(m.group(3)), "symbol": int(m.group(4)),
                "rx": int(m.group(5)), "runts": int(m.group(6)), "giants": int(m.group(7)),
                "tx": int(m.group(8)),
            }
    return result


# show interfaces status 의 Vlan 열에 'in Po2048' 로 Port-Channel 소속이 적힌다:
#     Et3        core_agg_mlag connected    in Po2048 full   1G     EbraTestPhyPort
# 구성도(engine/topology_builder.py)가 병렬 링크를 하나의 묶음으로 접는 근거가 이것이다 —
# 안 묶으면 Core↔Agg 사이에 선 4개가 겹쳐 그려져 읽을 수 없다.
_PO_MEMBER_RE = re.compile(r"^(\S+)\s+.*?\bin\s+Po(\d+)\b", re.IGNORECASE)
# show interfaces description 의 4열 형태:
#     Interface   Status   Protocol   Description
#     Et3         up       up         core_agg_mlag
_DESCRIPTION_RE = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)(?:\s+(.*))?$")
_DESCRIPTION_HEADER_RE = re.compile(r"^\s*Interface\b", re.IGNORECASE)


def parse_port_channel_membership(raw_output):
    """반환: {interface: "Port-ChannelNNNN"} — 소속이 없는 포트는 담지 않는다."""
    result = {}
    for line in raw_output.splitlines():
        m = _PO_MEMBER_RE.match(line.strip())
        if m:
            result[m.group(1)] = f"Port-Channel{m.group(2)}"
    return result


def parse_descriptions(raw_output):
    """`show interfaces description` -> {interface: description}. 설명이 빈 포트는 담지 않는다.

    설명은 사람이 붙인 것이라 구성도 링크 라벨로 가장 유용하다('core_agg_mlag' 처럼 그 링크가
    무엇인지 작업자가 이미 적어 둔 경우가 많다).
    """
    result = {}
    for line in raw_output.splitlines():
        stripped = line.rstrip()
        if not stripped.strip() or _DESCRIPTION_HEADER_RE.match(stripped):
            continue
        m = _DESCRIPTION_RE.match(stripped)
        if not m:
            continue
        description = (m.group(4) or "").strip()
        if description:
            result[m.group(1)] = description
    return result


def parse_counters_discards(raw_output):
    result = {}
    for line in raw_output.splitlines():
        m = DISCARDS_LINE_RE.match(line.strip())
        if m:
            result[m.group(1)] = {"in_discards": int(m.group(2)), "out_discards": int(m.group(3))}
    return result


def find_errdisabled(status_dict):
    return [iface for iface, status in status_dict.items() if status == "errdisabled"]


if __name__ == "__main__":
    status_sample = """
Port      Name               Status       Vlan       Duplex Speed  Type
Et1       Core-Core          connected    trunk      full   40G    QSFP
Et2       Uplink             errdisabled  100        full   1G     RJ45
Et3       Access             notconnect   100        full   1G     RJ45
"""
    errors_sample = """
Port               FCS    Align   Symbol       Rx    Runts   Giants    Tx
Et3/4               10802       0      795       10828     26      0        0
"""
    print("Status:", parse_status(status_sample))
    print("Errdisabled:", find_errdisabled(parse_status(status_sample)))
    print("Errors:", parse_counters_errors(errors_sample))
