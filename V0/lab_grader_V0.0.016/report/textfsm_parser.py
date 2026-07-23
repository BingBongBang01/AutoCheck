"""
Requirement 6 (Parsing) — Arista CLI raw 출력(show version, show processes top 등)을
textfsm + ntc-templates로 구조화된 dict로 변환.

raw_logs/{lab}/{session}/{device}.txt 는 "--- {cmd} ---\\n{output}\\n\\n" 포맷으로
engine/collector.py가 저장한 것을 그대로 읽어 커맨드별로 잘라 사용한다.
"""
import re
import textfsm
from ntc_templates.parse import parse_output

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
    """ntc-templates 매칭 템플릿으로 raw output -> list[dict]. 매칭 템플릿이 없으면 빈 리스트."""
    if not output:
        return []
    try:
        return parse_output(platform=platform, command=command, data=output)
    except textfsm.TextFSMError:
        return []
    except Exception:
        return []


def extract_device_metrics(raw_outputs, platform="arista_eos"):
    """
    raw_outputs: {command: output_text} (engine/collector.py의 raw_outputs 또는
                 split_raw_log()로 나눈 결과)
    반환: 보고서에 바로 쓸 수 있는 평탄화된 metrics dict.
    """
    metrics = {}

    version_rows = parse_command_output("show version", raw_outputs.get("show version", ""), platform)
    if version_rows:
        v = version_rows[0]
        metrics["model"] = v.get("model", "")
        metrics["serial_number"] = v.get("serial_number", "")
        metrics["image"] = v.get("image", "")
        metrics["uptime"] = v.get("uptime", "")
        total_mem = v.get("total_memory")
        free_mem = v.get("free_memory")
        if total_mem and free_mem:
            try:
                total_mem, free_mem = int(total_mem), int(free_mem)
                used_pct = round((total_mem - free_mem) / total_mem * 100, 1) if total_mem else None
                metrics["total_memory_kb"] = total_mem
                metrics["free_memory_kb"] = free_mem
                metrics["memory_used_percent"] = used_pct
            except (TypeError, ValueError):
                pass

    proc_cmd = next((c for c in raw_outputs if "processes top" in c), None)
    if proc_cmd:
        proc_rows = parse_command_output(proc_cmd, raw_outputs.get(proc_cmd, ""), platform)
        if proc_rows:
            p = proc_rows[0]
            metrics["cpu_util_percent"] = _cpu_util_from_top(p)
            metrics["load_average_1m"] = p.get("global_load_average_1_minutes", "")
            metrics["tasks_running"] = p.get("global_tasks_running", "")

    return metrics


def _cpu_util_from_top(top_row):
    """'top'의 idle%로부터 사용률(%) 역산 — CPU_UTIL = 100 - idle."""
    idle = top_row.get("global_cpu_percent_idle")
    try:
        return round(100 - float(idle), 1)
    except (TypeError, ValueError):
        return None


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
