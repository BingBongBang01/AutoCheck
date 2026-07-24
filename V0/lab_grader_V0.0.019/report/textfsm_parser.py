"""
Requirement 6 (Parsing) — Arista CLI raw 출력(show version, show processes top 등)을
정규식만으로 구조화된 dict로 변환한다(textfsm/ntc-templates 미사용 — vEOS-lab 출력이
ntc-templates 매칭에 실패하는 경우가 많아 자체 정규식 파서로 대체).

raw_logs/{lab}/{session}/{device}.txt 는 "--- {cmd} ---\\n{output}\\n\\n" 포맷으로
engine/collector.py가 저장한 것을 그대로 읽어 커맨드별로 잘라 사용한다.
"""
import re

_SECTION_RE = re.compile(r"^--- (.+?) ---\n", re.MULTILINE)

# --- show environment power / cooling ---------------------------------------
_ENV_STATUS_RE = re.compile(r"\b(Ok|Failed|Failure|Not Inserted)\b", re.IGNORECASE)
_ENV_NOT_SUPPORTED_RE = re.compile(r"^\s*%|no power supplies|invalid input", re.IGNORECASE)

# --- show processes top ------------------------------------------------------
_TOP_UPTIME_RE = re.compile(r"up\s+((?:\d+\s+days?,\s+)?[\d:]+|\d+\s+min),\s+\d+\s+users?,")
_TOP_IDLE_RE = re.compile(r"%Cpu\(s\):.*?([\d.]+)\s*id")
_TOP_LOAD_RE = re.compile(r"load average:\s*([\d.]+)")
_TOP_TASKS_RE = re.compile(r"Tasks:\s*(\d+)\s*total,\s*(\d+)\s*running")
_TOP_MEM_RE = re.compile(r"(?:MiB|KiB|GiB)\s*Mem\s*:\s*([\d.]+)\s+total,\s*([\d.]+)\s+free,\s*([\d.]+)\s+used")

# --- show version -------------------------------------------------------------
_VERSION_MODEL_RE = re.compile(r"^Arista\s+(\S+)", re.MULTILINE)
_VERSION_SERIAL_RE = re.compile(r"Serial number:\s*(\S+)")
_VERSION_IMAGE_RE = re.compile(r"Software image version:\s*(\S+)")
_VERSION_UPTIME_RE = re.compile(r"^Uptime:\s*(.+)$", re.MULTILINE)
_VERSION_TOTAL_MEM_RE = re.compile(r"Total memory:\s*(\d+)\s*kB")
_VERSION_FREE_MEM_RE = re.compile(r"Free memory:\s*(\d+)\s*kB")

# --- show interface status ----------------------------------------------------
_IFACE_STATUS_RE = re.compile(
    r"^(\S+)\s+.*?\b(connected|notconnect|disabled|errdisabled|notconnected)\b", re.IGNORECASE
)

# --- show interfaces counters errors -------------------------------------------
_IFACE_ERRORS_RE = re.compile(
    r"^(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$"
)


def split_raw_log(raw_text):
    """collector.py가 쓴 '--- cmd ---\\noutput\\n\\n' 형식을 {command: output} dict로 분리."""
    if not raw_text:
        return {}
    parts = _SECTION_RE.split(raw_text)
    # split 결과: ['', cmd1, output1, cmd2, output2, ...]
    sections = {}
    for i in range(1, len(parts) - 1, 2):
        sections[parts[i].strip()] = parts[i + 1].strip("\n")
    return sections


def parse_environment_status(output):
    """show environment power/cooling 출력 -> "Ok"/"Failed"/"N/A"(미지원)/"Unknown"."""
    if not output or not output.strip():
        return "N/A"
    text = output.strip()
    if _ENV_NOT_SUPPORTED_RE.match(text):
        return "N/A"
    statuses = _ENV_STATUS_RE.findall(text)
    if not statuses:
        return "Unknown"
    if any(s.lower() in ("failed", "failure") for s in statuses):
        return "Failed"
    return "Ok"


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


def parse_command_output(command, output, platform="arista_eos"):
    """레거시 호환용 — 커맨드 이름으로 알맞은 정규식 파서에 위임. 알 수 없는 커맨드는 빈 리스트."""
    if not output:
        return []
    cmd = command.lower()
    if "version" in cmd:
        return [parse_show_version(output)]
    if "processes top" in cmd:
        return [parse_processes_top(output)]
    if "interface status" in cmd:
        return [{"interface": k, "status": v} for k, v in parse_interface_status(output).items()]
    if "counters errors" in cmd:
        return [{"interface": k, **v} for k, v in parse_interface_errors(output).items()]
    return []


def extract_device_metrics(raw_outputs, platform="arista_eos"):
    """
    raw_outputs: {command: output_text} (engine/collector.py의 raw_outputs 또는
                 split_raw_log()로 나눈 결과)
    반환: 보고서에 바로 쓸 수 있는 평탄화된 metrics dict.
    """
    metrics = {}

    version_info = parse_show_version(raw_outputs.get("show version", ""))
    metrics["model"] = version_info.get("model", "")
    metrics["serial_number"] = version_info.get("serial_number", "")
    metrics["image"] = version_info.get("image", "")
    metrics["uptime"] = version_info.get("uptime", "")
    if "total_memory_kb" in version_info:
        metrics["total_memory_kb"] = version_info["total_memory_kb"]
        metrics["free_memory_kb"] = version_info["free_memory_kb"]
        metrics["memory_used_percent"] = version_info["memory_used_percent"]

    power_cmd = next((c for c in raw_outputs if "environment power" in c), None)
    if power_cmd is not None:
        metrics["power_status"] = parse_environment_status(raw_outputs.get(power_cmd, ""))

    cooling_cmd = next((c for c in raw_outputs if "environment cooling" in c), None)
    if cooling_cmd is not None:
        metrics["cooling_status"] = parse_environment_status(raw_outputs.get(cooling_cmd, ""))

    proc_cmd = next((c for c in raw_outputs if "processes top" in c), None)
    if proc_cmd:
        top = parse_processes_top(raw_outputs.get(proc_cmd, ""))
        # show processes top의 uptime/CPU/RAM을 우선 사용(요구사항: top 기준).
        if top.get("uptime"):
            metrics["uptime"] = top["uptime"]
        metrics["cpu_util_percent"] = top.get("cpu_util_percent")
        metrics["load_average_1m"] = top.get("load_average_1m", "")
        metrics["tasks_running"] = top.get("tasks_running", "")
        if top.get("memory_used_percent") is not None:
            metrics["memory_used_percent"] = top["memory_used_percent"]
            metrics["memory_total_mib"] = top.get("memory_total_mib")
            metrics["memory_free_mib"] = top.get("memory_free_mib")

    status_cmd = next((c for c in raw_outputs if "interface status" in c), None)
    if status_cmd:
        metrics["interface_status"] = parse_interface_status(raw_outputs.get(status_cmd, ""))

    errors_cmd = next((c for c in raw_outputs if "counters errors" in c), None)
    if errors_cmd:
        errors = parse_interface_errors(raw_outputs.get(errors_cmd, ""))
        metrics["interface_errors"] = errors
        metrics["interfaces_with_errors"] = [
            iface for iface, e in errors.items() if any(v for k, v in e.items())
        ]

    return metrics


def build_report_dataset(devices_raw):
    """
    devices_raw: {device_name: raw_log_text_or_dict}
        - raw_log_text_or_dict가 str이면 split_raw_log()로 먼저 분해.
        - dict({cmd: output})이면 그대로 사용(engine.collector.collect_all의 반환 형태).
    반환: {device_name: metrics_dict} — 접속 실패(디바이스가 None)면 metrics = {"unreachable": True}.
    """
    dataset = {}
    for name, raw in devices_raw.items():
        if raw is None:
            dataset[name] = {"unreachable": True}
            continue
        sections = split_raw_log(raw) if isinstance(raw, str) else raw
        dataset[name] = extract_device_metrics(sections)
    return dataset
