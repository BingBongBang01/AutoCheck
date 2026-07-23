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
    def build(self, project_name, scored, ai_result, output_path) -> str:
        """output_path에 파일을 쓰고, 실제로 쓰여진 경로(또는 None, 실패/미지원 시)를 반환."""
        raise NotImplementedError


def register(reporter: BaseReporter):
    _REGISTRY[reporter.format_id] = reporter


def get_reporter(format_id):
    return _REGISTRY.get(format_id)


def list_formats():
    return list(_REGISTRY.keys())
