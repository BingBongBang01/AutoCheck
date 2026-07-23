"""TextFSM-based CLI parsing and the shared Finding data structure."""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

import textfsm

# TextFSM template for Arista EOS "show interfaces status".
# Sample line matched:
# Et1        name1              connected    1        full     1000   1000BASE-T
ARISTA_EOS_INTERFACES_STATUS_TEMPLATE = """Value INTERFACE (\\S+)
Value NAME (.*?)
Value LINK_STATUS (connected|notconnect|disabled|errdisabled|notconnected)
Value VLAN_ID (\\S+)
Value DUPLEX (full|half|auto|unknown)
Value SPEED (\\S+)
Value TYPE (.*?)

Start
  ^Port\\s+Name\\s+Status\\s+Vlan\\s+Duplex\\s+Speed\\s+Type\\s*$$
  ^-+\\s*$$
  ^${INTERFACE}\\s+${NAME}\\s+${LINK_STATUS}\\s+${VLAN_ID}\\s+${DUPLEX}\\s+${SPEED}\\s+${TYPE}\\s*$$ -> Record
  ^\\s*$$
"""

VALID_SEVERITIES = {"CRITICAL", "WARNING", "INFO"}


@dataclass
class Finding:
    """A single audit observation surfaced against a network node."""

    node_id: str
    category: str
    severity: str
    message: str
    measured_value: Any

    def __post_init__(self) -> None:
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(f"Invalid severity '{self.severity}', must be one of {VALID_SEVERITIES}")

    def serialize(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Finding":
        return cls(
            node_id=data["node_id"],
            category=data["category"],
            severity=data["severity"],
            message=data["message"],
            measured_value=data.get("measured_value"),
        )


def _run_textfsm(template_text: str, raw_cli: str) -> List[Dict[str, str]]:
    template_buffer = io.StringIO(template_text)
    fsm = textfsm.TextFSM(template_buffer)
    parsed_rows = fsm.ParseText(raw_cli)
    return [dict(zip(fsm.header, row)) for row in parsed_rows]


def parse_interface_status(raw_cli: str, hostname: str) -> List[Finding]:
    """Parse 'show interfaces status' output and flag notconnect/half-duplex interfaces."""
    findings: List[Finding] = []
    rows = _run_textfsm(ARISTA_EOS_INTERFACES_STATUS_TEMPLATE, raw_cli)

    for row in rows:
        interface = row.get("INTERFACE", "")
        status = row.get("LINK_STATUS", "")
        duplex = row.get("DUPLEX", "")

        if status in ("notconnect", "notconnected"):
            findings.append(
                Finding(
                    node_id=hostname,
                    category="Physical",
                    severity="WARNING",
                    message=f"Interface {interface} is not connected",
                    measured_value=status,
                )
            )

        if duplex == "half":
            findings.append(
                Finding(
                    node_id=hostname,
                    category="Physical",
                    severity="CRITICAL",
                    message=f"Interface {interface} is running half duplex",
                    measured_value=duplex,
                )
            )

    return findings


import pytest

SAMPLE_OUTPUT = """Port        Name               Status       Vlan     Duplex   Speed  Type
Et1         uplink             connected    1        full     1000   1000BASE-T
Et2         to-leaf1           notconnect   1        auto     auto   1000BASE-T
Et3         to-leaf2           connected    100      half     100    1000BASE-T
Et4         mgmt               disabled     1        auto     auto   1000BASE-T
"""

def test_finding_serialize_round_trip():
    finding = Finding(
        node_id="core-sw1",
        category="Physical",
        severity="CRITICAL",
        message="Interface Et3 is running half duplex",
        measured_value="half",
    )
    serialized = finding.serialize()
    restored = Finding.from_dict(serialized)

    assert restored == finding
    assert serialized["severity"] == "CRITICAL"

def test_finding_rejects_invalid_severity():
    with pytest.raises(ValueError):
        Finding(node_id="x", category="Physical", severity="FATAL", message="bad", measured_value=None)

def test_parse_interface_status_flags_notconnect():
    findings = parse_interface_status(SAMPLE_OUTPUT, "core-sw1")
    notconnect_findings = [f for f in findings if "not connected" in f.message]

    assert len(notconnect_findings) == 1
    assert notconnect_findings[0].node_id == "core-sw1"
    assert notconnect_findings[0].category == "Physical"
    assert notconnect_findings[0].severity == "WARNING"
    assert notconnect_findings[0].measured_value == "notconnect"

def test_parse_interface_status_flags_half_duplex():
    findings = parse_interface_status(SAMPLE_OUTPUT, "core-sw1")
    half_duplex_findings = [f for f in findings if "half duplex" in f.message]

    assert len(half_duplex_findings) == 1
    assert half_duplex_findings[0].severity == "CRITICAL"
    assert half_duplex_findings[0].measured_value == "half"

def test_parse_interface_status_ignores_healthy_interfaces():
    findings = parse_interface_status(SAMPLE_OUTPUT, "core-sw1")
    messages = [f.message for f in findings]

    assert not any("Et1" in msg for msg in messages)

def test_parse_interface_status_empty_input_returns_no_findings():
    assert parse_interface_status("", "core-sw1") == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
