"""show interface status / show interfaces counters errors 출력 파싱."""
import re

_IFACE_STATUS_RE = re.compile(
    r"^(\S+)\s+.*?\b(connected|notconnect|disabled|errdisabled|notconnected)\b", re.IGNORECASE
)

_IFACE_ERRORS_RE = re.compile(
    r"^(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$"
)


def parse_interface_status(output):
    """show interface status 출력 -> {interface: status(lower)}."""
    result = {}
    if not output:
        return result
    for line in output.splitlines():
        m = _IFACE_STATUS_RE.match(line.strip())
        if m:
            result[m.group(1)] = m.group(2).lower()
    return result


def parse_interface_errors(output):
    """show interfaces counters errors 출력 -> {interface: {fcs, align, symbol, rx, runts, giants, tx}}."""
    result = {}
    if not output:
        return result
    for line in output.splitlines():
        m = _IFACE_ERRORS_RE.match(line.strip())
        if m:
            iface = m.group(1)
            result[iface] = {
                "fcs": int(m.group(2)), "align": int(m.group(3)), "symbol": int(m.group(4)),
                "rx": int(m.group(5)), "runts": int(m.group(6)), "giants": int(m.group(7)),
                "tx": int(m.group(8)),
            }
    return result
