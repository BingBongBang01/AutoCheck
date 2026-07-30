"""show ntp status 출력을 파싱한다."""
import re

STRATUM_RE = re.compile(r"stratum\s+(\d+)", re.IGNORECASE)
SERVER_RE = re.compile(r"synchronized to.*?\(([^)]+)\)|synchronized to\s+(\S+)", re.IGNORECASE)


def parse(raw_output):
    """반환: {"synchronized": bool, "stratum": int|None, "server": str|None}"""
    text = raw_output.strip()
    synchronized = "synchronized" in text.lower() and "unsynchronized" not in text.lower()
    stratum_m = STRATUM_RE.search(text)
    server_m = SERVER_RE.search(text)
    return {
        "synchronized": synchronized,
        "stratum": int(stratum_m.group(1)) if stratum_m else None,
        "server": (server_m.group(1) or server_m.group(2)) if server_m else None,
    }


if __name__ == "__main__":
    sample = "synchronized to NTP server (192.168.205.1) at stratum 3\n"
    print("파싱 결과:", parse(sample))
    print("미동기화:", parse("unsynchronized\n"))
