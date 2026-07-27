"""
Requirement 6 (Parsing) — Arista CLI raw 출력(show version, show processes top 등)을
정규식만으로 구조화된 dict로 변환한다(textfsm/ntc-templates 미사용 — vEOS-lab 출력이
ntc-templates 매칭에 실패하는 경우가 많아 자체 정규식 파서로 대체).

raw_logs/{lab}/{session}/{device}.txt 는 "--- {cmd} ---\\n{output}\\n\\n" 포맷으로
engine/collector.py가 저장한 것을 그대로 읽어 커맨드별로 잘라 사용한다.

커맨드별 정규식 파서는 report/textfsm_parsers/(parsers/ 폴더와 동일한 구조)에 분리되어
있다: version.py / processes.py / environment.py / interfaces.py.
"""
import re

from report.textfsm_parsers.version import parse_show_version
from report.textfsm_parsers.processes import parse_processes_top
from report.textfsm_parsers.environment import parse_environment_status
from report.textfsm_parsers.interfaces import parse_interface_status, parse_interface_errors

__all__ = [
    "split_raw_log", "parse_environment_status", "parse_processes_top", "parse_show_version",
    "parse_interface_status", "parse_interface_errors", "parse_command_output",
    "extract_device_metrics", "build_report_dataset",
]

_SECTION_RE = re.compile(r"^--- (.+?) ---\n", re.MULTILINE)


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
