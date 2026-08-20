"""
AlarmHandler — Critical Finding 발생 시 즉시 통보하는 책임.
report/reporters.py, plugins/vendors/base.py와 동일한 Registry 패턴을 따른다.
"""
from abc import ABC, abstractmethod


class AlarmHandler(ABC):
    handler_name: str = "generic"

    @abstractmethod
    def notify(self, project_id, findings):
        """findings: Finding.to_dict() 형태 dict 리스트 (이미 Critical로 필터된 것들).
        여기서 다시 severity를 걸러내거나 result를 바꾸면 안 됨 — 통보만 담당."""
        raise NotImplementedError


_REGISTRY = {}


def register(handler: AlarmHandler):
    _REGISTRY[handler.handler_name] = handler


def get_handler(handler_name):
    handler = _REGISTRY.get(handler_name)
    if not handler:
        raise ValueError(f"등록된 AlarmHandler 없음: {handler_name} (지원: {list(_REGISTRY.keys())})")
    return handler


def list_handlers():
    return list(_REGISTRY.keys())


def notify_all(project_id, findings, handler_names=None):
    """handler_names를 안 주면 등록된 모든 핸들러로 통보."""
    names = handler_names or list(_REGISTRY.keys())
    for name in names:
        handler = _REGISTRY.get(name)
        if handler:
            handler.notify(project_id, findings)
