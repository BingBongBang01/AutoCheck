"""
Finding — 점검 결과 하나를 나타내는 표준 스키마.
comparator.py가 만들던 dict(check/result/expected/actual)를 감싸서
severity/status/owner/source(감사용) 등 필드를 추가한 것.

중요: source 필드는 "이 판정이 어디서 나왔는지"의 감사 근거.
Rule Engine이 만든 건 항상 source="rule" — AI는 절대 이 필드를
"ai_cloud"/"ai_local"로 바꾸면서 동시에 PASS/FAIL을 못 바꾼다(설계 원칙).
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Any


SEVERITY_CRITICAL = "Critical"
SEVERITY_HIGH = "High"
SEVERITY_MEDIUM = "Medium"
SEVERITY_LOW = "Low"
SEVERITY_INFO = "Info"

STATUS_OPEN = "Open"
STATUS_INVESTIGATING = "Investigating"
STATUS_FIXED = "Fixed"
STATUS_IGNORED = "Ignored"
STATUS_CLOSED = "Closed"

SOURCE_RULE = "rule"
SOURCE_AI_LOCAL = "ai_local"
SOURCE_AI_CLOUD = "ai_cloud"


# Rule Engine의 result(PASS/FAIL/UNKNOWN/SKIPPED) -> severity 매핑 기본값.
# 5단계 세분화는 룰별로 severity를 직접 지정하는 게 정확하지만(예: Power Failed=Critical,
# CRC 소폭 증가=Low), 아직 룰별 severity 매핑 테이블이 없는 지금 단계에서는
# result 기준 안전한 기본값만 제공 — 필요하면 Finding 생성 시 severity를 직접 넘기면 override됨.
_DEFAULT_SEVERITY_MAP = {
    "FAIL": SEVERITY_HIGH,
    "UNKNOWN": SEVERITY_MEDIUM,
    "PASS": SEVERITY_INFO,
}


@dataclass
class Finding:
    project_id: str
    session_id: str
    device: str
    category: str            # Stage 이름 (VLAN/STP/MLAG 등)
    check_id: str            # 벤더 무관 추상 체크 ID
    result: str               # PASS/FAIL/UNKNOWN/SKIPPED (Rule Engine이 확정, 이후 불변)
    severity: str
    status: str = STATUS_OPEN
    owner: str = ""
    interface: str = ""       # 인터페이스 단위 Finding(CRC/포트다운 등)을 위한 필드
    evidence: str = ""
    expected: Any = None
    actual: Any = None
    recommendation: str = ""
    memo: str = ""            # 담당자가 남기는 자유메모(Jira 코멘트 성격, AI/Rule은 안 씀)
    source: str = SOURCE_RULE
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    resolved_at: Optional[str] = None

    def to_dict(self):
        return asdict(self)

    def mark_status(self, status, memo=None):
        """
        사람이 Findings 화면에서 상태를 바꾸는 경로 — result/severity/source는 안 건드림.
        Fixed/Closed로 바뀌면 resolved_at을 기록(문서의 Resolved 개념).
        """
        self.status = status
        if memo is not None:
            self.memo = memo
        if status in (STATUS_FIXED, STATUS_CLOSED):
            self.resolved_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        return self

    def with_recommendation(self, text, source=SOURCE_RULE):
        """
        AI가 조치권고만 채우는 경로 — result/severity는 여기서 절대 안 건드림(설계 원칙 강제).
        source는 recommendation의 출처만 기록(누가 이 설명을 붙였는지), result의 출처는
        생성 시점의 source 필드(항상 "rule")로 고정된 채 유지됨.
        """
        self.recommendation = text
        self.updated_at = datetime.now().isoformat()
        return self

    @classmethod
    def from_verdict(cls, project_id, session_id, category, verdict, severity=None):
        """
        기존 comparator.py가 만들던 Verdict dict
        ({"check","device","result","expected","actual"})를 그대로 감싸서 Finding으로 변환.
        기존 코드(comparator.py)는 안 건드림 — 이 함수가 어댑터 역할만 함.
        """
        result = verdict["result"]
        sev = severity or _DEFAULT_SEVERITY_MAP.get(result, SEVERITY_INFO)
        return cls(
            project_id=project_id, session_id=session_id,
            device=verdict.get("device", "-"), category=category,
            check_id=verdict["check"], result=result, severity=sev,
            evidence=str(verdict.get("actual", "")),
            expected=verdict.get("expected"), actual=verdict.get("actual"),
            source=SOURCE_RULE,
        )
