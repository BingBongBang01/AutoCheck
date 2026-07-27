"""show processes top 출력 파싱."""
import re

_TOP_UPTIME_RE = re.compile(r"up\s+((?:\d+\s+days?,\s+)?[\d:]+|\d+\s+min),\s+\d+\s+users?,")
_TOP_IDLE_RE = re.compile(r"%Cpu\(s\):.*?([\d.]+)\s*id")
_TOP_LOAD_RE = re.compile(r"load average:\s*([\d.]+)")
_TOP_TASKS_RE = re.compile(r"Tasks:\s*(\d+)\s*total,\s*(\d+)\s*running")
_TOP_MEM_RE = re.compile(r"(?:MiB|KiB|GiB)\s*Mem\s*:\s*([\d.]+)\s+total,\s*([\d.]+)\s+free,\s*([\d.]+)\s+used")


def parse_processes_top(output):
    """show processes top 출력 -> {"uptime", "cpu_util_percent", "load_average_1m",
    "tasks_running", "memory_total_mib", "memory_free_mib", "memory_used_percent"}."""
    result = {
        "uptime": "", "cpu_util_percent": None, "load_average_1m": "",
        "tasks_running": "", "memory_total_mib": None, "memory_free_mib": None,
        "memory_used_percent": None,
    }
    if not output:
        return result

    m = _TOP_UPTIME_RE.search(output)
    if m:
        result["uptime"] = re.sub(r"\s+", " ", m.group(1)).strip()

    m = _TOP_IDLE_RE.search(output)
    if m:
        try:
            result["cpu_util_percent"] = round(100 - float(m.group(1)), 1)
        except ValueError:
            pass

    m = _TOP_LOAD_RE.search(output)
    if m:
        result["load_average_1m"] = m.group(1)

    m = _TOP_TASKS_RE.search(output)
    if m:
        result["tasks_running"] = m.group(2)

    m = _TOP_MEM_RE.search(output)
    if m:
        total, free = float(m.group(1)), float(m.group(2))
        result["memory_total_mib"] = total
        result["memory_free_mib"] = free
        result["memory_used_percent"] = round((total - free) / total * 100, 1) if total else None

    return result
