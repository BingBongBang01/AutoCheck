"""
DeviceEvent — 장비 raw 로그(syslog 등)에서 뽑아낸 "사건 하나"를 나타내는 표준 스키마.

Finding(core/finding.py)과는 별개 모델이다: Finding은 Rule Engine이 PASS/FAIL을
판정한 "점검 결과"이고, DeviceEvent는 그 판정 이전 단계에서 로그 원문을 그대로
구조화한 "관측된 사건"(LLDP timeout, 인터페이스 up/down, reload 등)이다.
event_extractor.py가 이 모델을 채워서 만들고, 이후 단계(예: 타임라인 뷰,
Rule Engine 입력)에서 소비한다 — 이 파일 자체는 파싱 로직을 갖지 않는다.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Any


@dataclass
class DeviceEvent:
    device: str
    timestamp: Optional[str]      # 원본 로그의 타임스탬프 문자열(예: "Jul 23 14:22:36"), 없으면 None
    event_type: str               # 예: "lldp_timeout", "interface_down", "interface_up", "reload", "console_idle_timeout"
    interfaces: List[str] = field(default_factory=list)
    mac: Optional[str] = None
    raw_line: str = ""
    source_file: str = ""
    line_no: int = 0

    def to_dict(self):
        return asdict(self)
