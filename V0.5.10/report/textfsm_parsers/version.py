"""show version 출력 파싱."""
import re

_VERSION_MODEL_RE = re.compile(r"^Arista\s+(\S+)", re.MULTILINE)
_VERSION_SERIAL_RE = re.compile(r"Serial number:\s*(\S+)")
_VERSION_IMAGE_RE = re.compile(r"Software image version:\s*(\S+)")
_VERSION_UPTIME_RE = re.compile(r"^Uptime:\s*(.+)$", re.MULTILINE)
_VERSION_TOTAL_MEM_RE = re.compile(r"Total memory:\s*(\d+)\s*kB")
_VERSION_FREE_MEM_RE = re.compile(r"Free memory:\s*(\d+)\s*kB")


def parse_show_version(output):
    """show version 출력 -> {"model", "serial_number", "image", "uptime",
    "total_memory_kb", "free_memory_kb", "memory_used_percent"}."""
    result = {"model": "", "serial_number": "", "image": "", "uptime": ""}
    if not output:
        return result

    m = _VERSION_MODEL_RE.search(output)
    if m:
        result["model"] = m.group(1)
    m = _VERSION_SERIAL_RE.search(output)
    if m:
        result["serial_number"] = m.group(1)
    m = _VERSION_IMAGE_RE.search(output)
    if m:
        result["image"] = m.group(1)
    m = _VERSION_UPTIME_RE.search(output)
    if m:
        result["uptime"] = m.group(1).strip()

    total_m = _VERSION_TOTAL_MEM_RE.search(output)
    free_m = _VERSION_FREE_MEM_RE.search(output)
    if total_m and free_m:
        try:
            total_mem, free_mem = int(total_m.group(1)), int(free_m.group(1))
            result["total_memory_kb"] = total_mem
            result["free_memory_kb"] = free_mem
            result["memory_used_percent"] = round((total_mem - free_mem) / total_mem * 100, 1) if total_mem else None
        except ValueError:
            pass

    return result
