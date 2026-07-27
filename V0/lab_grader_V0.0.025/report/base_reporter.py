"""
Report Plugin 구조. BaseReporter 구현체를 등록만 하면 Reports 탭 export 옵션에 자동으로 나타남.
기존 report/markdown_report.py의 로직(build_markdown_report 등)은 안 건드리고 감싸기만 함.
"""
from abc import ABC, abstractmethod

_REGISTRY = {}


class BaseReporter(ABC):
    format_id: str = "generic"
    file_extension: str = ".txt"

    @abstractmethod
    def build(self, project_name, scored, ai_result, output_path, root_causes=None) -> str:
        """output_path에 파일을 쓰고, 실제로 쓰여진 경로(또는 None, 실패/미지원 시)를 반환.

        root_causes: List[RootCauseFinding](core/root_cause.py), 선택 인자.
        rule_engine/correlation_rules.py의 상관관계 분석 결과를 받아 "근본 원인 분석"
        섹션을 별도로 추가하기 위한 것 — scored(장비별 점수)/ai_result와는 무관한
        독립된 입력이라 기본값 None으로 두면 기존 호출부(넘기지 않는 곳)는 그대로 동작."""
        raise NotImplementedError


def register(reporter: BaseReporter):
    _REGISTRY[reporter.format_id] = reporter


def get_reporter(format_id):
    return _REGISTRY.get(format_id)


def list_formats():
    return list(_REGISTRY.keys())
