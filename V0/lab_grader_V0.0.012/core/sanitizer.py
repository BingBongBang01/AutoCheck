"""
Sanitizer

Cloud AI로 전달되는 데이터만 마스킹한다.

원본 Finding은 절대 수정하지 않는다.

History
Rule
Report
Health Score

모두 원본을 사용한다.
"""

import copy
import re

IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

MAC_RE = re.compile(
    r"\b[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\b"
)

MAC_COLON_RE = re.compile(
    r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"
)

MASK = "****"


def _mask_text(text, hostnames=None):

    if not isinstance(text, str):
        return text

    text = IP_RE.sub(MASK, text)
    text = MAC_RE.sub(MASK, text)
    text = MAC_COLON_RE.sub(MASK, text)

    if hostnames:

        for hostname in hostnames:

            text = re.sub(
                re.escape(hostname),
                MASK,
                text,
                flags=re.IGNORECASE,
            )

    return text


def mask_finding(finding_dict, hostnames=None):

    finding = copy.deepcopy(finding_dict)

    for field in (
        "device",
        "interface",
        "evidence",
        "recommendation",
        "memo",
    ):

        if field in finding:

            finding[field] = _mask_text(
                finding[field],
                hostnames,
            )

    for field in (
        "expected",
        "actual",
    ):

        if isinstance(finding.get(field), str):

            finding[field] = _mask_text(
                finding[field],
                hostnames,
            )

    return finding


def mask_findings(findings, hostnames=None):

    result = []

    for f in findings:

        if hasattr(f, "to_dict"):
            d = f.to_dict()
        else:
            d = f

        result.append(
            mask_finding(
                d,
                hostnames,
            )
        )

    return result


def mask_summary_text(text, hostnames=None):

    return _mask_text(
        text,
        hostnames,
    )


if __name__ == "__main__":

    from core.finding import Finding

    f = Finding(
        project_id="lab1",
        session_id="s1",
        device="Core1",
        category="STP",
        check_id="actual_root_bridge_vlan1",
        result="FAIL",
        severity="Critical",
        evidence="Root : Core2 172.30.1.102 MAC 5001.0002.0000",
        expected="Core1",
        actual="Core2",
    )

    masked = mask_finding(
        f.to_dict(),
        hostnames=[
            "Core1",
            "Core2",
        ],
    )

    print(masked)