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
