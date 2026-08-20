"""중복 억제가 '재발'과 '에코'를 구분하지 못하면 감시가 틀린 상태를 보여준다.

세 가지를 본다:

1. **방향 판정** — `%LINEPROTO-5-UPDOWN` 은 up 이든 down 이든 mnemonic 자체에 'down' 이라는
   글자를 품고 있다. 줄 전체로 방향을 재던 예전 로직에서는 링크 복구 syslog 가 DOWN 으로
   읽혔다. 즉 LINK_UP 이 한 번도 발행되지 않았고, 작업자가 링크를 되살려도 CRITICAL 이
   화면에 그대로 남았다.

2. **플랩** — down -> up -> down 이 dedupe 창(10초) 안에서 벌어지면 두 번째 down 이 '중복'으로
   버려져 조건이 다시 열리지 않았다. 링크가 내려가 있는데 화면은 '복구됨'이 된다.
   플랩은 억제할 대상이 아니라 세어야 할 대상이다.

3. **재실행 중복** — 감시를 시작할 때마다 세션 로그의 마지막 구간을 다시 판정하는데(seed),
   alert_id 는 프로세스마다 새로 발급되므로 저장본의 id 대조로 걸러지지 않는다. 자동시작을
   켜 두면 앱을 켤 때마다 어제 친 명령이 한 건씩 쌓인다.
"""
import pytest

from engine.baseline_diff_engine import BaselineDiffEngine
from engine.realtime_monitor import RealtimeMonitor

LINK = "%LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, changed state to "


class FakeStore:
    def get_device_baseline(self, device):
        return {"vlans": set(), "interfaces": {"Ethernet1"},
                "routes": set(), "bgp_neighbors": {"10.1.1.2"}}


@pytest.fixture
def engine():
    return BaselineDiffEngine(FakeStore())


# ---------- 1. 방향 판정 ----------

def test_link_up_syslog_is_not_read_as_down(engine):
    """mnemonic 의 'UPDOWN' 이 방향 판정을 오염시키면 안 된다."""
    assert [a["type"] for a in engine.analyze_stream("Core1", LINK + "down\n")] == ["LINK_DOWN"]

    # 복구는 alert 가 아니라 resolution 으로 나간다(앞선 경고를 취소한다).
    assert engine.analyze_stream("Core1", LINK + "up\n") == []
    assert len(engine.drain_resolutions()) == 1
    assert engine.open_conditions("Core1") == []


def test_administratively_down_is_still_down(engine):
    """'changed state to administratively down' — 상태 단어 하나로는 방향을 못 정한다."""
    alerts = engine.analyze_stream("Core1", LINK + "administratively down\n")
    assert [a["type"] for a in alerts] == ["LINK_DOWN"]


def test_bgp_adjacency_up_resolves(engine):
    engine.analyze_stream("Core1", "%BGP-5-ADJCHANGE: peer 10.1.1.2 Down - hold timer expired\n")
    engine.analyze_stream("Core1", "%BGP-5-ADJCHANGE: peer 10.1.1.2 Up\n")
    assert len(engine.drain_resolutions()) == 1


# ---------- 2. 플랩 ----------

def test_flap_reopens_the_condition(engine):
    """down -> up -> down 이 한 덩어리로 들어와도 마지막 상태가 남아야 한다."""
    alerts = engine.analyze_stream("Core1", LINK + "down\n" + LINK + "up\n" + LINK + "down\n")

    assert [a["type"] for a in alerts] == ["LINK_DOWN", "LINK_DOWN"]
    assert len(engine.drain_resolutions()) == 1, "중간의 up 은 첫 경고를 취소한다"
    # 마지막 down 이 조건을 다시 열어야 한다 — 예전에는 '중복'으로 버려져 '복구됨'으로 남았다.
    assert [(c["component_id"], c["condition"]) for c in engine.open_conditions("Core1")] == [
        ("Ethernet1", "interface_down")]


def test_echo_duplicates_are_still_folded_but_counted(engine):
    """터미널 에코로 같은 줄이 네 번 와도 경고는 하나 — 다만 몇 번이었는지는 남는다."""
    alerts = engine.analyze_stream("Core1", (LINK + "down\n") * 4)
    assert len(alerts) == 1
    assert engine.drain_repeats() == {alerts[0]["alert_id"]: 3}


def test_repeat_count_reaches_the_monitor(engine):
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])
    alerts = engine.analyze_stream("Core1", (LINK + "down\n") * 4)
    monitor.apply_alerts(alerts)
    monitor.bump_repeats(engine.drain_repeats())

    stored = monitor.alerts("Core1")[0]
    assert stored["repeat"] == 3


# ---------- 3. 재실행 중복 ----------

HISTORY_ALERT = {
    "device": "Core1", "type": "CONFIG_REMOVED", "severity": "CRITICAL",
    "target": "vlan:100", "message": "Baseline 등록 VLAN 100 삭제 명령 감지!",
    "raw_line": "Core1(config)#no vlan 100", "ts": "--:--:--", "history": True,
}


def test_reseeded_history_alert_is_not_duplicated():
    """감시를 다시 시작해 같은 구간을 seed 로 재판정해도 이력이 늘지 않는다."""
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])

    monitor.apply_alerts([dict(HISTORY_ALERT, alert_id="Core1#1")])
    monitor.apply_alerts([dict(HISTORY_ALERT, alert_id="Core1#7")])   # 다음 실행의 새 id

    assert len(monitor.alerts("Core1")) == 1


def test_history_dedupe_survives_a_restart():
    """저장본에서 되살린 사건도 서명으로 기억해야 한다 — 그것이 실제 재실행 경로다."""
    first = RealtimeMonitor()
    first.reset(["Core1"], ["Core1"])
    first.apply_alerts([dict(HISTORY_ALERT, alert_id="Core1#1")])
    snapshot = first.snapshot()

    revived = RealtimeMonitor()
    revived.adopt_devices(["Core1"], ["Core1"])
    revived.restore(snapshot)
    revived.apply_alerts([dict(HISTORY_ALERT, alert_id="Core1#1-new")])

    assert len(revived.alerts("Core1")) == 1


def test_live_alerts_are_never_deduped_by_content():
    """오늘 실제로 다시 친 명령은 반드시 보여야 한다 — 서명 중복 제거는 history 에만 적용된다."""
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])
    live = {k: v for k, v in HISTORY_ALERT.items() if k != "history"}

    monitor.apply_alerts([dict(live, alert_id="Core1#1", ts="10:00:00")])
    monitor.apply_alerts([dict(live, alert_id="Core1#2", ts="14:30:00")])

    assert len(monitor.alerts("Core1")) == 2


def test_clear_alerts_forgets_history_signatures():
    """'초기화' 후에는 다음 seed 가 화면을 다시 채워야 한다 — 영구히 비어 있으면 안 된다."""
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])
    monitor.apply_alerts([dict(HISTORY_ALERT, alert_id="Core1#1")])
    monitor.clear_alerts()

    monitor.apply_alerts([dict(HISTORY_ALERT, alert_id="Core1#2")])
    assert len(monitor.alerts("Core1")) == 1
