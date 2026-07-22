"""
Finding — 점검 결과 하나를 나타내는 표준 스키마.

Enterprise Network Inspection Platform v0.0.12

Rule Engine이 Finding을 생성하며,
AI는 Finding의 result를 변경하지 않고
Recommendation만 생성한다.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


# --------------------------------------------------
# Severity
# --------------------------------------------------

SEVERITY_CRITICAL = "Critical"
SEVERITY_HIGH = "High"
SEVERITY_MEDIUM = "Medium"
SEVERITY_LOW = "Low"
SEVERITY_INFO = "Info"


# --------------------------------------------------
# Status
# --------------------------------------------------

STATUS_OPEN = "Open"
STATUS_INVESTIGATING = "Investigating"
STATUS_FIXED = "Fixed"
STATUS_IGNORED = "Ignored"
STATUS_CLOSED = "Closed"


# --------------------------------------------------
# Source
# --------------------------------------------------

SOURCE_RULE = "rule"
SOURCE_AI_LOCAL = "ai_local"
SOURCE_AI_CLOUD = "ai_cloud"


# Rule Engine 기본 Severity
_DEFAULT_SEVERITY_MAP = {
    "FAIL": SEVERITY_HIGH,
    "UNKNOWN": SEVERITY_MEDIUM,
    "PASS": SEVERITY_INFO,
    "SKIPPED": SEVERITY_LOW,
}


@dataclass
class Finding:

    project_id: str
    session_id: str

    device: str
    interface: str = ""

    category: str = ""
    check_id: str = ""

    result: str = ""

    severity: str = SEVERITY_INFO
    status: str = STATUS_OPEN

    owner: str = ""

    evidence: str = ""
    expected: Any = None
    actual: Any = None

    summary: str = ""
    recommendation: str = ""

    memo: str = ""

    source: str = SOURCE_RULE

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    resolved_at: str = ""

    def to_dict(self):
        return asdict(self)

    def with_recommendation(self, text):
        self.recommendation = text
        self.updated_at = datetime.now().isoformat()
        return self

    def mark_status(self, status):

        self.status = status
        self.updated_at = datetime.now().isoformat()

        if status in (
            STATUS_FIXED,
            STATUS_CLOSED,
        ):
            self.resolved_at = datetime.now().isoformat()

        return self

    @classmethod
    def from_verdict(
        cls,
        project_id,
        session_id,
        category,
        verdict,
        severity=None,
    ):

        result = verdict["result"]

        sev = severity or _DEFAULT_SEVERITY_MAP.get(
            result,
            SEVERITY_INFO,
        )

        return cls(
            project_id=project_id,
            session_id=session_id,

            device=verdict.get("device", "-"),

            interface=verdict.get("interface", ""),

            category=category,

            check_id=verdict["check"],

            result=result,

            severity=sev,

            evidence=str(verdict.get("actual", "")),

            expected=verdict.get("expected"),

            actual=verdict.get("actual"),

            source=SOURCE_RULE,
        )