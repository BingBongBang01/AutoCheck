"""
ParsedDevice — 파서 단계의 산출물이자 Rule 단계의 유일한 입력 형태.
Rule(comparator)은 이 객체(또는 이 객체들의 dict)만 받고, raw CLI 텍스트는 절대 안 본다.
"""
from dataclasses import dataclass, field


@dataclass
class ParsedDevice:
    name: str
    vendor: str
    checks: dict = field(default_factory=dict)   # check_id -> 파서 결과(구조화된 dict/list)

    def get(self, check_id, default=None):
        return self.checks.get(check_id, default)

    def set(self, check_id, value):
        self.checks[check_id] = value
        return self
