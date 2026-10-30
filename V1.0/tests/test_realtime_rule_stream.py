"""규칙 엔진(config/log_rules.json)이 실시간 스트림에도 붙어야 한다.

engine/realtime_monitor.py 는 처음부터 규칙 경고를 전제로 만들어져 있었다 — 경고를 rule_id 로
묶고, 우클릭 '이 규칙 숨기기'가 rule_id 를 보고, 고정 항목 check_id 로 서명 id 를 받는다.
그런데 실시간 경로에는 rule_id 를 만들어 주는 곳이 없었다(유일한 공급원이 점검 직후 1회 배치).

실측으로 정해진 노이즈 문턱도 함께 본다. 실제 CRT 세션 로그(2,977줄)에서 규칙 판정 70건 중
66건이 running-config 문구와 `?` 도움말 사전이었다 — 그 둘을 끊고 major 이상만 올려야
'지금 조치할 것'을 고르는 화면이 된다.
"""
import pytest

from engine.log_rule_engine import ContextTracker, get_engine
from engine.realtime_rule_stream import RealtimeRuleStream


@pytest.fixture
def stream():
    return RealtimeRuleStream()


def rule_ids(alerts):
    return [a["rule_id"] for a in alerts]


# ---------- 판정이 실제로 흘러나온다 ----------

def test_device_output_produces_rule_alerts(stream):
    """`show mlag` 의 'state : Inactive' — 명령 한 줄로는 알 수 없고 출력을 읽어야 아는 것."""
    alerts = stream.analyze("Core1", "Core1#show mlag\n"
                                     "state                              :   Inactive\n")
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["severity"] in ("MAJOR", "CRITICAL")
    assert alert["rule_id"] == alert["type"], "숨김/묶음이 rule_id 와 type 둘 다를 보므로 같아야 한다"
    assert alert["from_rules"] is True
    assert alert["component_id"] is None, "복구 이벤트가 정의되지 않으므로 상태추적 대상이 아니다"


def test_alerts_are_shaped_for_realtime_monitor(stream):
    """RealtimeMonitor.apply_alerts() 가 그대로 받을 수 있는 모양이어야 한다."""
    from engine.realtime_monitor import RealtimeMonitor

    alerts = stream.analyze("Core1", "Core1#show mlag\nstate  :  Inactive\n")
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])
    monitor.apply_alerts(alerts)

    analysis = monitor.state()["analysis"]
    assert analysis["verdict"] in ("warn", "fail")
    # 규칙 경고는 규칙 단위로 한 줄이 된다(category 로 묶으면 성질이 다른 것이 뭉친다).
    assert any(f["group_key"].endswith(alerts[0]["rule_id"]) for f in analysis["findings"])


# ---------- 노이즈를 끊는다 ----------

def test_help_output_is_not_judged(stream):
    """`?` 출력은 가능한 키워드 목록이다 — 온갖 위험 단어가 설명문으로 들어 있다."""
    alerts = stream.analyze("Core1", "Core1(config-mlag)#no ?\n"
                                     "  inactive   Configure actions taken when MLAG state is inactive\n"
                                     "  reload-delay   Delay (seconds) after reboot\n"
                                     "  errdisable  Configure error disable function\n")
    assert alerts == []


def test_running_config_text_is_not_judged(stream):
    """`show run` 축약형에서도 설정 원문 구간을 알아봐야 한다 — 작업자는 끝까지 치지 않는다."""
    alerts = stream.analyze("Core1", "Core1#show run\n"
                                     "no service interface inactive port-id allocation disabled\n"
                                     "!\n"
                                     "end\n")
    assert alerts == []


def test_minor_and_info_are_not_raised(stream):
    """오타(`% Invalid input`)와 정상 운영 로그는 이력에만 남는다 — 실측 세션에서 62건이었다."""
    alerts = stream.analyze("Core1", "Core1(config)#clea\n"
                                     "% Invalid input\n"
                                     "Core1(config)#foo\n"
                                     "% Incomplete command\n")
    assert alerts == []


def test_repeated_query_is_folded(stream):
    """같은 조회를 네 번 치면 같은 판정이 네 번 들어온다 — 한 줄로 접는다."""
    text = "Core1#show mlag\nstate  :  Inactive\n"
    first = stream.analyze("Core1", text)
    again = stream.analyze("Core1", text)
    assert len(first) == 1
    assert again == []


def test_reset_forgets_output_context(stream):
    """세션이 끊기면 문맥을 버린다 — 새 세션 첫 줄이 지난 명령의 출력으로 읽히면 안 된다."""
    stream.analyze("Core1", "Core1#show run\n")
    stream.reset()
    alerts = stream.analyze("Core1", "state  :  Inactive\n")
    assert rule_ids(alerts) == ["keyword_inactive"]


# ---------- 규칙 엔진 쪽 게이트 자체 ----------

def test_context_tracker_recognizes_abbreviated_show_run():
    tracker = ContextTracker()
    tracker.feed("Core1#show run")
    assert tracker.is_config is True
    assert tracker.is_help is False


@pytest.mark.parametrize("command", ["show running-config", "sh run", "show run", "show startup-config",
                                     "show start"])
def test_config_command_abbreviations(command):
    tracker = ContextTracker()
    tracker.feed(f"Core1#{command}")
    assert tracker.is_config is True, command


@pytest.mark.parametrize("command", ["?", "no ?", "default ?", "show mlag ?"])
def test_help_commands_are_recognized(command):
    tracker = ContextTracker()
    tracker.feed(f"Core1(config)#{command}")
    assert tracker.is_help is True, command


def test_help_gate_short_circuits_evaluate():
    tracker = ContextTracker()
    tracker.feed("Core1(config)#no ?")
    verdict = get_engine().evaluate(
        "  inactive   Configure actions taken when MLAG state is inactive", tracker)
    assert verdict is None
