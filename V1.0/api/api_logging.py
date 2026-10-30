"""화면에 노출되는 API 메서드의 예외를 앱 로그에 남긴다.

pywebview 는 js_api 메서드에서 난 예외를 화면 쪽 프라미스 reject 로 넘긴다. 화면(web_ui/js/core.js 의
call())은 그것을 토스트로 띄울 뿐이라, 예전엔 서버 쪽에 아무 기록이 없었다 — 사용자가 '버튼을 눌렀더니
오류가 떴다'고 해도 어떤 예외였는지 로그로 되짚을 수 없었다. 여기서 traceback 과 함께 남기고, 예외는
그대로 다시 던진다(화면 동작은 바뀌지 않는다).
"""
import functools
import inspect
import traceback

from core.app_logger import log_event


def _logged(name, func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            log_event(f"API '{name}' 처리 중 예외: {exc}\n{traceback.format_exc()}", source="api", level="ERROR")
            raise
    # pywebview 는 inspect.getfullargspec 으로 인자 이름을 읽어 JS 쪽 함수를 만든다 — getfullargspec 은
    # __wrapped__ 를 따라가지 않으므로 원래 시그니처를 직접 붙여 JS 쪽 서명이 예전과 같게 한다.
    wrapper.__signature__ = inspect.signature(func)
    return wrapper


def log_api_exceptions(cls):
    """cls 의 공개 메서드(밑줄로 시작하지 않는 일반 함수)를 감싼다. staticmethod·classmethod·속성은 그대로 둔다.
    functools.wraps 로 시그니처를 보존한다 — pywebview 가 인자 이름을 읽는다."""
    for name in dir(cls):
        if name.startswith("_"):
            continue
        raw = inspect.getattr_static(cls, name)
        if inspect.isfunction(raw):
            setattr(cls, name, _logged(name, raw))
    return cls
