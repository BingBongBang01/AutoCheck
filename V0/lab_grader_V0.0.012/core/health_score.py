"""
Health Score Engine

Enterprise Network Inspection Platform

모든 장비는 100점에서 시작한다.

Rule 위반 시 감점한다.

Device

↓

Rack

↓

Site

↓

Project

순으로 집계한다.
"""

DEFAULT_SCORE = 100


RULE_PENALTY = {

    "power_status": 30,

    "fan_status": 20,

    "crc_error": 10,

    "cpu_usage": 5,

    "license": 5,
}


SEVERITY_PENALTY = {

    "Critical": 30,

    "High": 15,

    "Medium": 5,

    "Low": 2,

    "Info": 0,
}


def _get(item, key, default=None):

    if isinstance(item, dict):
        return item.get(key, default)

    return getattr(item, key, default)


def score_device(findings):

    score = DEFAULT_SCORE

    for f in findings:

        if _get(f, "result") != "FAIL":
            continue

        check = _get(f, "check_id")

        severity = _get(
            f,
            "severity",
            "Info",
        )

        penalty = RULE_PENALTY.get(
            check,
            SEVERITY_PENALTY.get(
                severity,
                0,
            ),
        )

        score -= penalty

    return max(score, 0)


def score_project(findings):

    devices = {}

    for f in findings:

        device = _get(
            f,
            "device",
            "(unknown)",
        )

        devices.setdefault(
            device,
            [],
        ).append(f)

    device_scores = {}

    for device, device_findings in devices.items():

        device_scores[device] = score_device(
            device_findings
        )

    if device_scores:

        project_score = round(
            sum(device_scores.values())
            / len(device_scores),
            1,
        )

    else:

        project_score = DEFAULT_SCORE

    return {

        "project_score": project_score,

        "device_scores": device_scores,
    }


if __name__ == "__main__":

    from core.finding import Finding

    findings = [

        Finding(
            project_id="p",
            session_id="s",
            device="Core1",
            category="Power",
            check_id="power_status",
            result="FAIL",
            severity="Critical",
        ),

        Finding(
            project_id="p",
            session_id="s",
            device="Core1",
            category="CPU",
            check_id="cpu_usage",
            result="FAIL",
            severity="Medium",
        ),

        Finding(
            project_id="p",
            session_id="s",
            device="Access1",
            category="VLAN",
            check_id="vlan100",
            result="PASS",
            severity="Info",
        ),
    ]

    print(score_project(findings))