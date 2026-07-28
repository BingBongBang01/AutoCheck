"""
RootCauseFinding — "이 사건이 저 사건의 원인일 가능성이 높다"는 상관관계 추론 결과의
표준 스키마. Finding(core/finding.py, PASS/FAIL 판정)과도, DeviceEvent(core/event.py,
관측된 사건 하나)와도 다른 별개 모델이다: 이건 여러 DeviceEvent 사이의 인과 가설이다.

rule_engine/correlation_rules.py가 이 구조체를 만든다 — 규칙 매칭이면 source="rule"
(confidence는 규칙에 명시된 고정값), AI 폴백이 잡으면 source="ai_local"/"ai_cloud"
(confidence는 AI가 스스로 추정하지 않는 한 None으로 둬 "확신도 불명"을 그대로 드러냄).
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List, Any

SOURCE_RULE = "rule"
SOURCE_AI_LOCAL = "ai_local"
SOURCE_AI_CLOUD = "ai_cloud"


@dataclass
class RootCauseFinding:
    project_id: str
    session_id: str
    cause_device: str
    cause_event: Any                  # DeviceEvent.to_dict()
    effect_events: List[Any] = field(default_factory=list)   # [DeviceEvent.to_dict(), ...]
    confidence: Optional[float] = None
    rule_id: str = ""
    explanation: str = ""
    source: str = SOURCE_RULE
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self):
        return asdict(self)
