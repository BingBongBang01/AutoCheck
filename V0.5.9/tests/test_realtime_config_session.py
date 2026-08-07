"""config session 안의 변경은 commit 전까지 일어나지 않은 일이다.

실제 세션 로그(2026-08-07)에서 작업자가 이렇게 작업했다:

    Core1#conf session reset
    Core1(config-s-reset)#no vlan 4093
    Core1(config-s-reset)#no mlag

Arista 의 config session 은 `commit` 할 때까지 장비에 적용되지 않는다. 그런데 예전에는 이 줄이
곧바로 CRITICAL '삭제 명령 감지'로 올라갔다 — 아직 아무것도 지워지지 않았는데 즉시 조치 대상이
됐고, 반대로 나중에 실제로 commit 되는 순간에는 아무 경고도 나오지 않았다(그 줄은 `commit`
한 단어라 어떤 패턴에도 안 걸린다).

세 가지를 본다:
  1. 세션 안의 변경은 '예정'으로 한 단계 낮추고 상태추적을 열지 않는다.
  2. commit 하면 원래 심각도로 승격되고 그때 상태추적이 열린다.
  3. abort 하면 화면에 올린 '예정' 경고를 해제한다(지우지 않는다 — 준비했다가 되돌린 것도 이력이다).
"""
import pytest

from engine.baseline_diff_engine import BaselineDiffEngine


class FakeStore:
    def get_device_baseline(self, device):
        return {"vlans": {"100", "4093"}, "interfaces": {"Ethernet1"},
                "routes": set(), "bgp_neighbors": set()}


@pytest.fixture
def engine():
    return BaselineDiffEngine(FakeStore())


def opened(engine, device="Core1"):
    return {(c["component_id"], c["condition"]) for c in engine.open_conditions(device)}


# ---------- 1. 예정으로 낮춘다 ----------

def test_session_change_is_staged_not_applied(engine):
    alerts = engine.analyze_stream("Core1", "Core1#conf session reset\n"
                                            "Core1(config-s-reset)#no vlan 4093\n")
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["staged"] is True
    assert alert["staged_session"] == "reset"
    assert alert["severity"] == "MAJOR", "확정 변경이면 CRITICAL 이지만 아직 예정이다"
    assert "예정" in alert["message"]
    assert opened(engine) == set(), "일어나지 않은 장애를 상태표에 세우면 되돌릴 방법이 없다"


def test_same_change_outside_a_session_is_unchanged(engine):
    alerts = engine.analyze_stream("Core1", "Core1(config)#no vlan 100\n")
    assert alerts[0]["severity"] == "CRITICAL"
    assert alerts[0].get("staged") is not True
    assert opened(engine) == {("vlan:100", "config_removed")}


def test_interface_context_works_inside_a_session(engine):
    """'config-s-w1-if-Et1' — 세션 안에서 인터페이스에 들어간 프롬프트."""
    alerts = engine.analyze_stream("Core1", "Core1(config-s-w1-if-Et1)#shutdown\n")
    assert alerts[0]["target"] == "shutdown:Ethernet1"
    assert alerts[0]["staged"] is True


def test_leaving_config_mode_clears_the_session(engine):
    engine.analyze_stream("Core1", "Core1(config-s-w1)#no vlan 100\n")
    alerts = engine.analyze_stream("Core1", "Core1#show version\n"
                                            "Core1(config)#no vlan 4093\n")
    assert alerts[0].get("staged") is not True, "특권 모드를 거쳤으면 세션 안이 아니다"
    assert alerts[0]["severity"] == "CRITICAL"


# ---------- 2. commit 하면 승격된다 ----------

def test_commit_promotes_staged_changes(engine):
    engine.analyze_stream("Core1", "Core1(config-s-reset)#no vlan 4093\n")
    promoted = engine.analyze_stream("Core1", "Core1(config-s-reset)#commit\n")

    assert len(promoted) == 1
    assert promoted[0]["severity"] == "CRITICAL"
    assert promoted[0]["staged"] is False
    assert promoted[0]["committed_session"] == "reset"
    assert "확정" in promoted[0]["message"]
    assert "예정" not in promoted[0]["message"]
    # 승격된 시점에야 '지금 문제'가 된다.
    assert opened(engine) == {("vlan:4093", "config_removed")}


def test_commit_from_outside_the_session(engine):
    """`configure session <name> commit` — 세션 밖에서 이름을 지정해 확정하는 형태."""
    engine.analyze_stream("Core1", "Core1(config-s-w1-if-Et1)#shutdown\n")
    promoted = engine.analyze_stream("Core1", "Core1#configure session w1 commit\n")

    assert [a["severity"] for a in promoted] == ["CRITICAL"]
    assert opened(engine) == {("Ethernet1", "interface_down")}


def test_staged_alert_does_not_suppress_the_real_one(engine):
    """세션을 버리고 밖에서 같은 명령을 다시 쳐도 확정 변경은 반드시 보여야 한다."""
    engine.analyze_stream("Core1", "Core1(config-s-w1)#no vlan 100\n")
    alerts = engine.analyze_stream("Core1", "Core1#show version\n"
                                            "Core1(config)#no vlan 100\n")
    assert [a["severity"] for a in alerts] == ["CRITICAL"]
    assert alerts[0].get("staged") is not True, "특권 모드를 거쳤으면 세션 안이 아니다"


def test_promotion_survives_the_dedupe_window(engine):
    """예정 경고와 (장비, type, target)이 같다 — staged 를 키에 넣지 않으면 승격이 삼켜진다."""
    ticks = iter([100.0] * 20)
    frozen = BaselineDiffEngine(FakeStore(), clock=lambda: next(ticks))
    frozen.analyze_stream("Core1", "Core1(config-s-w1)#no vlan 100\n")
    promoted = frozen.analyze_stream("Core1", "Core1(config-s-w1)#commit\n")
    assert len(promoted) == 1, "같은 시각에 commit 해도 승격은 반드시 통과해야 한다"


def test_commit_of_an_empty_session_is_quiet(engine):
    assert engine.analyze_stream("Core1", "Core1(config-s-w1)#commit\n") == []


def test_commit_promotes_only_that_session(engine):
    engine.analyze_stream("Core1", "Core1(config-s-a)#no vlan 100\n")
    engine.analyze_stream("Core1", "Core1(config-s-b)#no vlan 4093\n")
    promoted = engine.analyze_stream("Core1", "Core1(config-s-a)#commit\n")

    assert [a["target"] for a in promoted] == ["vlan:100"]
    assert opened(engine) == {("vlan:100", "config_removed")}


# ---------- 3. abort 하면 해제된다 ----------

def test_abort_resolves_the_staged_alert(engine):
    staged = engine.analyze_stream("Core1", "Core1(config-s-fix)#no vlan 100\n")
    engine.analyze_stream("Core1", "Core1(config-s-fix)#abort\n")

    resolutions = engine.drain_resolutions()
    assert [r["alert_id"] for r in resolutions] == [staged[0]["alert_id"]]
    assert "폐기" in resolutions[0]["resolved_by"]
    assert opened(engine) == set()


def test_abort_then_commit_promotes_nothing(engine):
    engine.analyze_stream("Core1", "Core1(config-s-fix)#no vlan 100\n")
    engine.analyze_stream("Core1", "Core1(config-s-fix)#abort\n")
    engine.drain_resolutions()

    assert engine.analyze_stream("Core1", "Core1#configure session fix commit\n") == []
    assert opened(engine) == set()


def test_reset_context_drops_staged_changes(engine):
    """세션이 끊기면 예정 변경도 잊는다 — 재접속 후의 commit 이 옛 세션을 확정하면 안 된다."""
    engine.analyze_stream("Core1", "Core1(config-s-fix)#no vlan 100\n")
    engine.reset_context("Core1")
    assert engine.analyze_stream("Core1", "Core1#configure session fix commit\n") == []
