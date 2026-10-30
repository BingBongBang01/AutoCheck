"""API 예외 기록과 화면 오류 처리 — IMPROVEMENT_PLAN 2-17.

  1. 서버 기록이 없었다: 화면은 call() 이 토스트만 띄웠고, 어떤 예외였는지 로그로 되짚을 수 없었다.
     → 화면에 노출되는 Api 메서드를 감싸 traceback 과 함께 앱 로그에 남기고, 예외는 그대로 다시 던진다.
  2. 화면(JS)에서 난 예외도 콘솔에만 남았다 → log_client_error 로 앱 로그에.
  3. 화면 쪽 정적 점검: null 역참조로 버튼이 고착되던 자리, 확인 없이 지우던 자리, 결과와 상관없이
     '저장됨'을 띄우던 자리, 다른 탭에서 매초 예외로 멈추던 점검 진행 폴러.
"""
import inspect
from pathlib import Path

import pytest

from api import api_logging

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "web_ui" / "js"


@pytest.fixture
def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(api_logging, "log_event", lambda message, source="app", level="INFO": lines.append(
        (message, source, level)))
    return lines


class Sample:
    def ok(self, a, b=2):
        return a + b

    def boom(self, value):
        raise ValueError(f"잘못된 값 {value}")

    def _private(self):
        raise ValueError("감싸지 않는다")

    @staticmethod
    def helper(x):
        return x * 2


def test_public_methods_log_and_reraise(logged):
    cls = api_logging.log_api_exceptions(type("Api", (Sample,), {}))
    api = cls()
    assert api.ok(1) == 3
    with pytest.raises(ValueError):
        api.boom(7)
    (message, source, level), = logged
    assert "API 'boom'" in message and "잘못된 값 7" in message and "Traceback" in message
    assert (source, level) == ("api", "ERROR")


def test_signatures_private_and_static_methods_are_kept(logged):
    cls = api_logging.log_api_exceptions(type("Api", (Sample,), {}))
    assert list(inspect.signature(cls.ok).parameters) == ["self", "a", "b"]
    # pywebview 는 getfullargspec(바운드 메서드).args[1:] 로 JS 쪽 함수 서명을 만든다 — 예전과 같아야 한다
    assert inspect.getfullargspec(cls().ok).args[1:] == ["a", "b"]
    assert cls.helper(3) == 6
    with pytest.raises(ValueError):
        cls()._private()
    assert logged == []


def test_main_api_is_wrapped():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "@log_api_exceptions\nclass Api(" in source


def test_client_errors_reach_the_app_log(monkeypatch):
    import core.app_logger as app_logger
    from api.base import BaseApiMixin
    lines = []
    monkeypatch.setattr(app_logger, "log_event", lambda message, source="app", level="INFO": lines.append(
        (message, source, level)))
    assert BaseApiMixin().log_client_error("TypeError: x is null", "realtime-monitor-panel.js", 261) is True
    assert lines == [("[화면 오류] realtime-monitor-panel.js:261 TypeError: x is null", "ui", "ERROR")]


def read(name):
    return (JS / name).read_text(encoding="utf-8")


def test_core_reports_uncaught_errors():
    core = read("core.js")
    assert "addEventListener('error'" in core and "addEventListener('unhandledrejection'" in core
    assert "log_client_error" in core


@pytest.mark.parametrize("name, needle", [
    ("realtime-baseline-alerts.js", "if (!result) return false;"),
    ("realtime-monitor-panel.js", "if (!result || result.error) {"),
    ("settings-cloud-api.js", "if (!r) { resultEl.textContent = '확인하지 못했습니다'"),
    ("settings-local-ai.js", "if (!result) { resultEl.textContent = '확인하지 못했습니다'"),
])
def test_null_results_do_not_freeze_the_screen(name, needle):
    assert needle in read(name)


@pytest.mark.parametrize("name, before_call", [
    ("catalog.js", "call('reset_catalog_defaults'"),
    ("settings-cloud-api.js", "call('remove_cloud_api'"),
    ("knowledge.js", "call('delete_knowledge_doc'"),
])
def test_deletes_ask_first(name, before_call):
    text = read(name)
    position = text.index(before_call)
    assert "confirm(" in text[max(0, position - 300):position]


def test_saved_badges_follow_the_result():
    assert "saved === true ? '자동저장됨'" in read("inventory.js")
    catalog = read("catalog.js")
    assert "flashSaved(true)" not in catalog


def test_inspection_pollers_survive_other_tabs():
    # 점검 폴러는 connection-inspection.js 의 pollTerminalInspection() 하나다(3-4 — 예전엔 두 파일에 복사돼 있었다).
    text = read("connection-inspection.js")
    poller = text[text.index("termInspectPollTimer = setInterval"):]
    poller = poller[:poller.index("}, ")]
    assert "document.getElementById('term-status-msg').textContent" not in poller
    assert "setTermStatusMsg(" in poller
    connection = read("connection.js")
    assert "termInspectPollTimer = setInterval" not in connection and "pollTerminalInspection();" in connection


def test_navigation_renders_one_page_at_a_time():
    nav = read("core-navigation.js")
    assert "navigationChain = navigationChain.then(" in nav and "if (navigationTarget !== page)" in nav


def test_realtime_polling_skips_while_a_request_is_in_flight():
    panel = read("realtime-monitor-panel.js")
    assert "if (rtmPollInFlight) return;" in panel
