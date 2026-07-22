"""
show environment power / show environment cooling / show environment all 파싱.
NC서비스망 실제 보고서 포맷(Status 컬럼이 Ok/Failed로 명시) 기준.
"""
import re

STATUS_LINE_RE = re.compile(r"^\s*(\d+)\s+(\S+)\s+(\S+)\s+([\d.]+A)\s+([\d.]+A)\s+([\d.]+W)\s+(\w+)\s*$")
FAN_LINE_RE = re.compile(r"^\s*(\d+)\s+(Ok|Failed|Not Inserted)\s+(\d+%)\s*$")
TEMP_SUMMARY_RE = re.compile(r"System temperature status is:\s*(\w+)", re.IGNORECASE)
COOLING_SUMMARY_RE = re.compile(r"System cooling status is:\s*(\w+)", re.IGNORECASE)
SENSOR_LINE_RE = re.compile(r"^\s*(\d+)\s+(.+?)\s+([\d.]+)C\s+(\d+)C\s+(\d+)C\s*$")


def parse_power(raw_output):
    """반환: [{"supply": int, "status": str}]"""
    supplies = []
    for line in raw_output.splitlines():
        m = STATUS_LINE_RE.match(line)
        if m:
            supplies.append({"supply": int(m.group(1)), "status": m.group(7)})
    return supplies


def parse_cooling(raw_output):
    fans = []
    for line in raw_output.splitlines():
        m = FAN_LINE_RE.match(line)
        if m:
            fans.append({"fan": int(m.group(1)), "status": m.group(2), "speed": m.group(3)})
    summary = COOLING_SUMMARY_RE.search(raw_output)
    return {"fans": fans, "summary_status": summary.group(1) if summary else None}


def parse_temperature(raw_output):
    sensors = []
    for line in raw_output.splitlines():
        m = SENSOR_LINE_RE.match(line)
        if m:
            sensors.append({
                "sensor_id": int(m.group(1)), "description": m.group(2).strip(),
                "temp_c": float(m.group(3)), "alert_threshold_c": int(m.group(4)),
                "critical_threshold_c": int(m.group(5)),
            })
    summary = TEMP_SUMMARY_RE.search(raw_output)
    return {"sensors": sensors, "summary_status": summary.group(1) if summary else None}


if __name__ == "__main__":
    power_sample = """
Power Input     Output   Output  Supply
Model    Capacity Current  Current Power    Status
------- ---------- --------- -------- -------- ------- -------
1 PWR-760AC 760W     0.81A    11.00A   132.8W   Ok
2 PWR-760AC 760W     0.00A    0.00A    0.0W     Failed
"""
    cooling_sample = """
System cooling status is: Ok
Fan Tray Status Speed
--------- --------------- ------
1 Ok 35%
2 Failed 0%
"""
    temp_sample = """
System temperature status is: Ok
Sensor Description Temperature Threshold Threshold
------- ------------------------ ------------- ---------- ----------
1 Front-panel temp sensor 22.750C 65C 75C
"""
    print("Power:", parse_power(power_sample))
    print("Cooling:", parse_cooling(cooling_sample))
    print("Temp:", parse_temperature(temp_sample))
