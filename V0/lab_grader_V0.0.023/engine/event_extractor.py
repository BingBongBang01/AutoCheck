"""
장비별 raw 로그(00_orignal_log)에서 "사건"(DeviceEvent, core/event.py) 목록을 뽑아내는
정규식 파서.

log_analysis.py(analyze_text/run_analysis, "문제 블록" FSM)와는 완전히 별개 파이프라인이다.
log_analysis.py는 이상 키워드 주변 원문 블록을 통째로 보존해 01_problem_log를 만드는 반면,
이 모듈은 syslog 한 줄 단위로 타임스탬프/장비/이벤트유형/인터페이스/MAC을 구조화해서
DeviceEvent로 뽑아낸다 — 기존 grading/scoring 흐름(comparator.py -> Finding)에는
관여하지 않으며, 이 모듈의 결과를 소비하는 쪽(예: 타임라인 뷰)이 별도로 붙는다.
"""
import os
import re
import glob

from core.event import DeviceEvent

# "Jul 23 14:22:36 Agg1 Lldp: %LLDP-5-NEIGHBOR_TIMEOUT: ..." 형태의 syslog 줄 헤더.
# 장비명(hostname)은 타임스탬프 다음, 프로세스명(:) 이전 토큰.
_SYSLOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(?P<device>\S+)\s+(?P<rest>.*)$"
)

# Cisco/Arista 스타일 dotted MAC(xxxx.xxxx.xxxx) 또는 콜론 MAC(xx:xx:xx:xx:xx:xx).
_MAC_RE = re.compile(
    r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"
    r"|\b[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\b"
)

# (event_type, 컴파일된 정규식, 인터페이스 캡처 그룹 번호 목록) — 위에서부터 순서대로 매칭 시도.
_EVENT_PATTERNS = [
    ("lldp_timeout", re.compile(r"LLDP-5-NEIGHBOR_TIMEOUT.*?\bon interface (\S+)", re.IGNORECASE), [1]),
    ("lldp_neighbor_new", re.compile(r"LLDP-5-NEIGHBOR_NEW.*?\bon interface (\S+)", re.IGNORECASE), [1]),
    ("interface_down", re.compile(r"[Ll]ine protocol on Interface (\S+).*changed state to down"), [1]),
    ("interface_up", re.compile(r"[Ll]ine protocol on Interface (\S+).*changed state to up"), [1]),
    ("mgmt_down", re.compile(r"\bMgmt(?:1|1/1)?\s+down\b", re.IGNORECASE), []),
    ("mgmt_up", re.compile(r"\bMgmt(?:1|1/1)?\s+up\b", re.IGNORECASE), []),
    ("port_channel_down", re.compile(r"\b(Po\d+)\b.*\bdown\b", re.IGNORECASE), [1]),
    ("port_channel_up", re.compile(r"\b(Po\d+)\b.*\bup\b", re.IGNORECASE), [1]),
    ("ethernet_down", re.compile(r"\b(Et\d+(?:,\d+)*)\s+down\b", re.IGNORECASE), [1]),
    ("ethernet_up", re.compile(r"\b(Et\d+(?:,\d+)*)\s+up\b", re.IGNORECASE), [1]),
    ("user_reload", re.compile(r"\buser reload\b", re.IGNORECASE), []),
    ("console_idle_timeout", re.compile(r"\bconsole idle timeout\b|idle timeout", re.IGNORECASE), []),
]


def _split_interfaces(token):
    """"Et1,2,3" 같은 콤마 목록을 ["Et1","Et2","Et3"]로 펼침. 그 외는 그대로 단일 목록."""
    m = re.match(r"^([A-Za-z]+)(\d+(?:,\d+)*)$", token)
    if not m:
        return [token]
    prefix, nums = m.groups()
    return [f"{prefix}{n}" for n in nums.split(",")]


def parse_line(line, source_file="", line_no=0):
    """한 줄에서 DeviceEvent 하나를 뽑는다. 매칭 없으면 None."""
    header_m = _SYSLOG_LINE_RE.match(line)
    timestamp = header_m.group("timestamp") if header_m else None
    device = header_m.group("device") if header_m else None
    body = header_m.group("rest") if header_m else line

    for event_type, rx, iface_groups in _EVENT_PATTERNS:
        m = rx.search(body)
        if not m:
            continue
        interfaces = []
        for g in iface_groups:
            interfaces.extend(_split_interfaces(m.group(g)))
        mac_m = _MAC_RE.search(body)
        return DeviceEvent(
            device=device or "-",
            timestamp=timestamp,
            event_type=event_type,
            interfaces=interfaces,
            mac=mac_m.group(0) if mac_m else None,
            raw_line=line,
            source_file=source_file,
            line_no=line_no,
        )
    return None


def extract_events(raw_text, source_file=""):
    """raw_text 전체를 줄 단위로 훑어 DeviceEvent 목록을 반환."""
    events = []
    for line_no, line in enumerate(raw_text.splitlines(), start=1):
        event = parse_line(line, source_file=source_file, line_no=line_no)
        if event:
            events.append(event)
    return events


def run_extraction(original_dir):
    """00_orignal_log의 모든 .txt에서 이벤트를 뽑아 파일명순으로 반환.
    반환: [{"source": str, "event_count": int, "events": [DeviceEvent, ...]}]"""
    results = []
    for path in sorted(glob.glob(os.path.join(original_dir, "*.txt"))):
        with open(path, encoding="utf-8", errors="replace") as f:
            raw_text = f.read()
        source_name = os.path.basename(path)
        events = extract_events(raw_text, source_file=source_name)
        results.append({"source": source_name, "event_count": len(events), "events": events})
    return results
