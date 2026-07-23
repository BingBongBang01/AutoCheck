"""
PipelineStep — 모든 파이프라인 단계(Discovery/Collector/Parser/RuleEngine/Scorer/AI/Report)가
구현해야 하는 최소 계약. main.py의 grade()가 Stage 이름을 직접 호출하던 문제(OCP 위반)를
"Step 리스트에 추가"로 대체하기 위한 것.
"""
from abc import ABC, abstractmethod


class PipelineStep(ABC):
    name: str = "unnamed_step"

    @abstractmethod
    def run(self, ctx):
        """ctx(SessionContext)를 받아 갱신하고 그대로 반환. 실패해도 예외 대신
        ctx에 상태를 기록하고 반환하는 걸 권장(Pipeline이 계속 진행할지 결정)."""
        raise NotImplementedError
