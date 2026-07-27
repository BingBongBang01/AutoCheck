import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed


def expand_targets(value):
    value = value.strip()
    if "/" in value:
        return [str(item) for item in ipaddress.ip_network(value, strict=False).hosts()]
    if "-" in value.rsplit(".", 1)[-1]:
        prefix, bounds = value.rsplit(".", 1)
        start, end = (int(part) for part in bounds.split("-", 1))
        return [f"{prefix}.{number}" for number in range(start, end + 1)]
    return [value]


def scan(value, port=22, timeout=1, workers=64):
    targets = expand_targets(value)
    def check(ip):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return {"ip": ip, "alive": True, "ssh": True}
        except OSError as error:
            return {"ip": ip, "alive": False, "ssh": False, "error": str(error)}
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(targets)))) as executor:
        return [future.result() for future in as_completed([executor.submit(check, ip) for ip in targets])]
