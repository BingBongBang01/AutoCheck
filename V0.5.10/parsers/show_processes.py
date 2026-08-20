"""show processes top once 파싱 — CPU 사용률, Free Memory."""
import re

CPU_RE = re.compile(r"%Cpu\(s\):\s*([\d.]+)\s*us")
MEM_LINE_RE = re.compile(r"(?:MiB|KiB)?\s*Mem\s*:\s*([\d.]+)\s+total,\s*([\d.]+)\s+free")


def parse(raw_output):
    cpu_match = CPU_RE.search(raw_output)
    mem_match = MEM_LINE_RE.search(raw_output)
    cpu_percent = float(cpu_match.group(1)) if cpu_match else None

    result = {"cpu_percent": cpu_percent, "memory_total": None, "memory_free": None, "memory_free_ratio": None}
    if mem_match:
        total, free = float(mem_match.group(1)), float(mem_match.group(2))
        result["memory_total"] = total
        result["memory_free"] = free
        result["memory_free_ratio"] = round(free / total, 4) if total else None
    return result


if __name__ == "__main__":
    sample = """
top - 10:32:11 up 193 weeks
Tasks: 123 total
%Cpu(s):  3.0 us,  1.2 sy,  0.0 ni, 95.0 id
MiB Mem :   8098.9 total,   5275.5 free,   1200.0 used
"""
    print(parse(sample))
